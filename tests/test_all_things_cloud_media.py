from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from kira_studio.all_things_cloud_media import (
    DEFAULT_VOICE_NAME,
    CloudMediaValidationError,
    GoogleCloudArtifactStore,
    GoogleCloudNarratedPitchRenderer,
    NarratedPitchRenderError,
)
from kira_studio.all_things_agentic import (
    AllThingsConfig,
    ProductionBrief,
    VisualPanelProviderResult,
    build_visual_storyboard,
)


class PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str) -> None:
        self.bucket = bucket
        self.name = name

    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str,
        if_generation_match: int,
    ) -> None:
        self.bucket.uploads.append(
            {
                "name": self.name,
                "data": data,
                "content_type": content_type,
                "if_generation_match": if_generation_match,
            }
        )
        if if_generation_match == 0 and self.name in self.bucket.objects:
            raise PreconditionFailed("private conflict detail")
        self.bucket.objects[self.name] = data

    def download_as_bytes(self) -> bytes:
        return self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self, name: str = "private-media-bucket") -> None:
        self.name = name
        self.iam_configuration = SimpleNamespace(
            public_access_prevention="enforced",
            uniform_bucket_level_access_enabled=True,
        )
        self.objects: dict[str, bytes] = {}
        self.uploads: list[dict[str, object]] = []

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self.value = bucket
        self.names: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.names.append(name)
        return self.value

    def get_bucket(self, name: str) -> FakeBucket:
        self.names.append(name)
        return self.value


class KeywordOnlyArtifactStore:
    def __init__(self, delegate: GoogleCloudArtifactStore) -> None:
        self.delegate = delegate
        self.prefix = delegate.prefix

    def put_bytes(
        self,
        *,
        job_id: str,
        artifact_id: str,
        data: bytes,
        content_type: str,
    ) -> object:
        return self.delegate.put_bytes(job_id, artifact_id, data, content_type)

    def get_bytes(self, object_name: str) -> bytes:
        return self.delegate.get_bytes(object_name)


def wav_bytes(duration_seconds: float = 1.0) -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * round(16_000 * duration_seconds))
    return stream.getvalue()


def jpeg_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (768, 432), (34, 45, 60)).save(
        stream, format="JPEG", quality=80
    )
    return stream.getvalue()


class FakeTTSClient:
    def __init__(self, *audio: bytes) -> None:
        self.audio = list(audio)
        self.requests: list[dict[str, object]] = []

    def synthesize_speech(self, **kwargs: object) -> object:
        self.requests.append(dict(kwargs))
        return SimpleNamespace(audio_content=self.audio.pop(0))


class FakeVisualProvider:
    def __init__(self, image: bytes) -> None:
        self.image = image

    def create_panel(self, _prompt: str, **_kwargs: object) -> VisualPanelProviderResult:
        return VisualPanelProviderResult(
            image_bytes=self.image,
            mime_type="image/jpeg",
            width=768,
            height=432,
            execution={"evidence_origin": "injected_test_client"},
        )


class FakeMediaRunner:
    def __init__(
        self,
        *,
        video_codec: str = "h264",
        width: int = 1920,
        duration_seconds: float = 2.0,
        segment_durations: tuple[float, ...] | None = None,
        segment_video_codec: str = "h264",
        segment_width: int = 1920,
        segment_probe_stdout: bytes | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.video_codec = video_codec
        self.width = width
        self.duration_seconds = duration_seconds
        self.segment_durations = segment_durations or (1.0,)
        self.segment_video_codec = segment_video_codec
        self.segment_width = segment_width
        self.segment_probe_stdout = segment_probe_stdout
        self.segment_probe_count = 0

    def __call__(self, command: object) -> object:
        call = tuple(command)  # type: ignore[arg-type]
        self.calls.append(call)
        if call[0] == "fake-ffprobe":
            is_segment = Path(call[-1]).name.startswith("segment-")
            if is_segment and self.segment_probe_stdout is not None:
                self.segment_probe_count += 1
                return SimpleNamespace(
                    returncode=0,
                    stdout=self.segment_probe_stdout,
                    stderr=b"private segment probe detail",
                )
            if is_segment:
                duration_index = min(
                    self.segment_probe_count,
                    len(self.segment_durations) - 1,
                )
                probe_duration = self.segment_durations[duration_index]
                probe_video_codec = self.segment_video_codec
                probe_width = self.segment_width
                self.segment_probe_count += 1
            else:
                probe_duration = self.duration_seconds
                probe_video_codec = self.video_codec
                probe_width = self.width
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": probe_video_codec,
                                "width": probe_width,
                                "height": 1080,
                            },
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": f"{probe_duration:.6f}"},
                    }
                ).encode("utf-8"),
                stderr=b"",
            )
        Path(call[-1]).write_bytes(b"fake-private-mp4-bytes")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def pitch_values() -> tuple[dict[str, object], dict[str, object], str, dict[str, object]]:
    brief: dict[str, object] = {
        "title": "The Orbital Threshold",
        "summary": "Two friends choose whether to stay.",
    }
    timeline: dict[str, object] = {
        "shots": [
            {
                "shot_id": "SC01-SH01",
                "scene_number": 1,
                "role": "establishing",
                "planned_in_timecode": "00:00:00:00",
                "planned_out_timecode": "00:00:04:00",
                "storyboard_card": {
                    "action": "Reveal the damaged repair bay.",
                    "dialogue_or_audio": "Low alarm and room tone.",
                },
            },
            {
                "shot_id": "SC01-SH02",
                "scene_number": 1,
                "role": "primary_coverage",
                "planned_in_timecode": "00:00:04:00",
                "planned_out_timecode": "00:00:09:00",
                "storyboard_card": {
                    "action": "Mara confronts Ilan beside Battery C.",
                    "dialogue_or_audio": "Protect the argument.",
                },
            },
        ]
    }
    source = """INT. REPAIR BAY - NIGHT

MARA
Battery C will not survive another cycle.

ILAN
Then we leave before the doors seal.
"""
    image = jpeg_bytes()
    encoded = base64.b64encode(image).decode("ascii")
    panel_base = {
        "status": "available",
        "data_base64": encoded,
        "content_sha256": sha256(image).hexdigest(),
        "byte_length": len(image),
    }
    visuals: dict[str, object] = {
        "panels": [
            {**panel_base, "shot_id": "SC01-SH01"},
            {**panel_base, "shot_id": "SC01-SH02"},
        ]
    }
    return brief, timeline, source, visuals


class GoogleCloudArtifactStoreTests(unittest.TestCase):
    def test_rejects_bucket_without_enforced_pap_and_uniform_access(self) -> None:
        public_bucket = FakeBucket()
        public_bucket.iam_configuration.public_access_prevention = "inherited"
        with self.assertRaisesRegex(CloudMediaValidationError, "bucket_is_not_private"):
            GoogleCloudArtifactStore(public_bucket.name, bucket=public_bucket)

    def test_put_is_content_addressed_private_immutable_and_idempotent(self) -> None:
        bucket = FakeBucket()
        client = FakeStorageClient(bucket)
        store = GoogleCloudArtifactStore(bucket.name, client=client)
        data = b"private video bytes"

        first = store.put_bytes("job-123", "pitch.mp4", data, "video/mp4")
        second = store.put_bytes("job-123", "pitch.mp4", data, "video/mp4")

        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {"artifact_id", "object_name", "sha256", "bytes", "content_type"},
        )
        self.assertEqual(first["sha256"], sha256(data).hexdigest())
        self.assertEqual(first["bytes"], len(data))
        self.assertTrue(
            first["object_name"].startswith("jobs/job-123/artifacts/")
        )
        self.assertNotIn("url", " ".join(first))
        self.assertEqual(bucket.uploads[0]["if_generation_match"], 0)
        self.assertEqual(store.get_bytes(first["object_name"]), data)
        with self.assertRaises(TypeError):
            first["sha256"] = "0" * 64  # type: ignore[index]

    def test_rejects_unsafe_identifiers_and_cross_boundary_reads(self) -> None:
        store = GoogleCloudArtifactStore(
            "private-media-bucket", bucket=FakeBucket()
        )
        for job_id, artifact_id in (
            ("../job", "pitch.mp4"),
            ("job", "../pitch.mp4"),
            ("job", "folder/pitch.mp4"),
        ):
            with self.subTest(job_id=job_id, artifact_id=artifact_id):
                with self.assertRaises(CloudMediaValidationError):
                    store.put_bytes(job_id, artifact_id, b"x", "video/mp4")
        for object_name in (
            "gs://other-bucket/private.mp4",
            "jobs/job/../private.mp4",
            "other-prefix/jobs/job/artifacts/" + "0" * 64 + "/pitch.mp4",
        ):
            with self.subTest(object_name=object_name):
                with self.assertRaises(CloudMediaValidationError):
                    store.get_bytes(object_name)

    def test_download_rechecks_digest_embedded_in_object_name(self) -> None:
        bucket = FakeBucket()
        store = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        manifest = store.put_bytes("job-1", "artifact.bin", b"original", "application/octet-stream")
        bucket.objects[manifest["object_name"]] = b"tampered"
        with self.assertRaisesRegex(Exception, "artifact_integrity_failed"):
            store.get_bytes(manifest["object_name"])


class GoogleCloudNarratedPitchRendererTests(unittest.TestCase):
    def renderer(
        self,
        *,
        runner: FakeMediaRunner | None = None,
    ) -> tuple[GoogleCloudNarratedPitchRenderer, FakeTTSClient, FakeMediaRunner, FakeBucket]:
        bucket = FakeBucket()
        store = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        tts = FakeTTSClient(wav_bytes(), wav_bytes())
        selected_runner = runner or FakeMediaRunner()
        renderer = GoogleCloudNarratedPitchRenderer(
            store,
            tts,
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
            command_runner=selected_runner,
        )
        return renderer, tts, selected_runner, bucket

    def test_renders_every_card_probes_and_stores_private_sidecars(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, tts, runner, bucket = self.renderer()

        manifest = renderer.render(
            brief=brief,
            timeline=timeline,
            source_message=source,
            visual_storyboard=visuals,
            job_id="job-123",
        )

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["schema"], "video-studio.narrated-pitch/v1")
        self.assertEqual((manifest["card_count"], manifest["cue_count"]), (2, 2))
        self.assertEqual(len(tts.requests), 2)
        self.assertEqual(
            tts.requests[0]["request"]["voice"]["name"],  # type: ignore[index]
            DEFAULT_VOICE_NAME,
        )
        self.assertEqual(tts.requests[0]["timeout"], 120)
        self.assertNotIn('Mara says, "Battery C', tts.requests[0]["request"]["input"]["text"])  # type: ignore[index]
        self.assertIn('Mara says, "Battery C', tts.requests[1]["request"]["input"]["text"])  # type: ignore[index]
        ffmpeg_calls = [call for call in runner.calls if call[0] == "fake-ffmpeg"]
        self.assertEqual(len(ffmpeg_calls), 3)
        self.assertEqual(len([call for call in runner.calls if call[0] == "fake-ffprobe"]), 3)
        self.assertEqual(manifest["video"]["content_type"], "video/mp4")
        self.assertEqual(manifest["video"]["video_codec"], "h264")
        self.assertEqual(manifest["video"]["audio_codec"], "aac")
        self.assertEqual(
            (manifest["video"]["width"], manifest["video"]["height"]),
            (1920, 1080),
        )
        self.assertGreater(manifest["video"]["duration_seconds"], 0)
        self.assertEqual(manifest["voice"]["segment_count"], 2)
        self.assertEqual(manifest["verification"]["status"], "passed")
        self.assertEqual(
            {upload["content_type"] for upload in bucket.uploads},
            {"video/mp4", "text/plain; charset=utf-8", "application/x-subrip"},
        )
        body = dict(manifest)
        supplied = body.pop("manifest_sha256")
        self.assertEqual(
            supplied,
            sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )
        serialized = json.dumps(manifest)
        self.assertNotIn("Battery C will not survive", serialized)
        self.assertNotIn(source, serialized)
        self.assertNotIn("public_url", serialized)
        with self.assertRaises(TypeError):
            manifest["video"]["sha256"] = "0" * 64

    def test_bounded_card_dispatches_then_integrity_checked_finalization(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, tts, runner, bucket = self.renderer()
        ownership_checks = 0

        def owned() -> bool:
            nonlocal ownership_checks
            ownership_checks += 1
            return True

        segments: list[dict[str, object]] = []
        for index in range(2):
            rendered = renderer.render_segment_chunk(
                brief=brief,
                timeline=timeline,
                source_message=source,
                visual_storyboard=visuals,
                job_id="job-123",
                start_index=index,
                max_cards=1,
                ownership_check=owned,
            )
            self.assertEqual(len(rendered), 1)
            segments.append(dict(rendered[0]))

        manifest = renderer.finalize_segments(
            brief=brief,
            timeline=timeline,
            source_message=source,
            visual_storyboard=visuals,
            job_id="job-123",
            segments=segments,
            ownership_check=owned,
        )

        self.assertEqual([item["sequence"] for item in segments], [1, 2])
        self.assertEqual([item["shot_id"] for item in segments], ["SC01-SH01", "SC01-SH02"])
        self.assertTrue(all(item["content_type"] == "video/mp4" for item in segments))
        self.assertEqual(len(tts.requests), 2)
        self.assertTrue(all(request["timeout"] == 120 for request in tts.requests))
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["card_count"], 2)
        self.assertGreaterEqual(ownership_checks, 12)
        self.assertEqual(
            len([call for call in runner.calls if call[0] == "fake-ffmpeg"]),
            3,
        )
        self.assertEqual(
            len([call for call in runner.calls if call[0] == "fake-ffprobe"]),
            3,
        )
        self.assertEqual(
            sum(1 for upload in bucket.uploads if upload["content_type"] == "video/mp4"),
            3,
        )

    def test_bounded_card_render_observes_cancellation_before_storage(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, _tts, _runner, bucket = self.renderer()
        checks = iter((True, True, False))
        with self.assertRaisesRegex(NarratedPitchRenderError, "work_stopped"):
            renderer.render_segment_chunk(
                brief=brief,
                timeline=timeline,
                source_message=source,
                visual_storyboard=visuals,
                job_id="job-123",
                start_index=0,
                max_cards=1,
                ownership_check=lambda: next(checks),
            )
        self.assertEqual(bucket.uploads, [])

    def test_finalization_rejects_a_tampered_private_card_segment(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, _tts, _runner, bucket = self.renderer()
        segments: list[dict[str, object]] = []
        for index in range(2):
            segments.extend(
                dict(item)
                for item in renderer.render_segment_chunk(
                    brief=brief,
                    timeline=timeline,
                    source_message=source,
                    visual_storyboard=visuals,
                    job_id="job-123",
                    start_index=index,
                    max_cards=1,
                )
            )
        bucket.objects[str(segments[0]["object_name"])] += b"tampered"

        with self.assertRaisesRegex(
            NarratedPitchRenderError, "visual_asset_load_failed"
        ):
            renderer.finalize_segments(
                brief=brief,
                timeline=timeline,
                source_message=source,
                visual_storyboard=visuals,
                job_id="job-123",
                segments=segments,
            )
        self.assertFalse(
            any(str(upload["name"]).endswith("/narrated-pitch.mp4") for upload in bucket.uploads)
        )

    def test_finalization_observes_cancellation_after_probe_before_publication(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, _tts, _runner, bucket = self.renderer()
        segments: list[dict[str, object]] = []
        for index in range(2):
            segments.extend(
                dict(item)
                for item in renderer.render_segment_chunk(
                    brief=brief,
                    timeline=timeline,
                    source_message=source,
                    visual_storyboard=visuals,
                    job_id="job-123",
                    start_index=index,
                    max_cards=1,
                )
            )
        uploads_before_finalization = len(bucket.uploads)
        checks = iter((True, True, True, True, True, True, False))

        with self.assertRaisesRegex(NarratedPitchRenderError, "work_stopped"):
            renderer.finalize_segments(
                brief=brief,
                timeline=timeline,
                source_message=source,
                visual_storyboard=visuals,
                job_id="job-123",
                segments=segments,
                ownership_check=lambda: next(checks),
            )

        self.assertEqual(len(bucket.uploads), uploads_before_finalization)

    def test_uses_measured_encoded_durations_for_final_probe_and_subtitles(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        runner = FakeMediaRunner(
            duration_seconds=2.8,
            segment_durations=(1.4, 1.4),
        )
        renderer, _tts, _runner, bucket = self.renderer(runner=runner)

        manifest = renderer.render(
            brief,
            timeline,
            source,
            visuals,
            "job-123",
        )

        # The synthesized WAVs are 1.0 seconds each.  Their 0.8-second total
        # drift from the encoded segments exceeds the unchanged 0.5s final
        # tolerance, so this succeeds only when measured MP4 durations are used.
        self.assertEqual(manifest["video"]["duration_seconds"], 2.8)
        subtitle_upload = next(
            upload
            for upload in bucket.uploads
            if upload["content_type"] == "application/x-subrip"
        )
        subtitles = subtitle_upload["data"].decode("utf-8")  # type: ignore[union-attr]
        self.assertIn("00:00:00,000 --> 00:00:01,400", subtitles)
        self.assertIn("00:00:01,400 --> 00:00:02,800", subtitles)
        serialized = json.dumps(manifest)
        self.assertNotIn("segment-", serialized)
        self.assertNotIn("kira-narrated-pitch-", serialized)

    def test_segment_probes_fail_closed_without_exposing_probe_detail(self) -> None:
        private_detail = b"not-json C:\\private\\segment.mp4 PRIVATE SCREENPLAY TEXT"
        cases = (
            (
                FakeMediaRunner(segment_probe_stdout=private_detail),
                "segment_probe_failed",
            ),
            (
                FakeMediaRunner(segment_video_codec="vp9"),
                "segment_probe_mismatch",
            ),
            (
                FakeMediaRunner(segment_durations=(float("nan"),)),
                "segment_probe_mismatch",
            ),
            (
                FakeMediaRunner(segment_durations=(901.0,)),
                "segment_probe_mismatch",
            ),
        )
        for runner, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                brief, timeline, source, visuals = pitch_values()
                renderer, _tts, _runner, bucket = self.renderer(runner=runner)
                with self.assertRaisesRegex(
                    NarratedPitchRenderError,
                    expected_code,
                ) as caught:
                    renderer.render(brief, timeline, source, visuals, "job-123")
                self.assertEqual(str(caught.exception), expected_code)
                self.assertNotIn("private", str(caught.exception).casefold())
                self.assertEqual(bucket.uploads, [])

    def test_resolves_private_panel_artifacts_through_loader(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, _tts, _runner, _bucket = self.renderer()
        panels = visuals["panels"]  # type: ignore[index]
        for index, panel in enumerate(panels, start=1):  # type: ignore[assignment]
            decoded = base64.b64decode(panel.pop("data_base64"))
            artifact = renderer.artifact_store.put_bytes(
                "job-123", f"panel-{index}.jpg", decoded, "image/jpeg"
            )
            panel.update(
                {
                    "artifact_id": artifact["artifact_id"],
                    "object_name": artifact["object_name"],
                    "mime_type": "image/jpeg",
                }
            )

        manifest = renderer.render(
            brief, timeline, source, visuals, "job-123"
        )

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["card_count"], 2)

    def test_accepts_exact_private_storyboard_shape_from_agentic_compiler(self) -> None:
        brief_values, timeline, source, _visuals = pitch_values()
        brief = ProductionBrief.from_mapping(
            {
                **brief_values,
                "format": "Short film",
                "target_audience": "General",
                "duration_seconds": 9,
                "genre": "Science fiction",
                "tone": ["urgent"],
                "visual_direction": "Monochrome storyboard line art.",
                "audio_direction": "Natural narration over room tone.",
                "deliverables": ["Narrated pitch"],
                "scenes": [
                    {
                        "number": 1,
                        "purpose": "Choose whether to leave.",
                        "setting": "Orbital repair bay",
                        "characters": ["Mara", "Ilan"],
                        "dialogue_required": True,
                    }
                ],
                "clarifying_questions": [],
                "ready_for_production": True,
            }
        )
        for shot in timeline["shots"]:  # type: ignore[index]
            shot["storyboard_card"].update(  # type: ignore[index]
                {
                    "framing": "Wide storyboard frame",
                    "camera": "Locked camera",
                    "continuity_requirements": ["Preserve Battery C"],
                }
            )
        bucket = FakeBucket()
        store = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        visual_storyboard = build_visual_storyboard(
            brief,
            timeline,
            provider=FakeVisualProvider(jpeg_bytes()),
            config=AllThingsConfig(project="video-studio-12345"),
            job_id="job-123",
            artifact_store=store,
        )
        self.assertEqual(visual_storyboard["representation"], "private_artifact_route")
        self.assertTrue(
            visual_storyboard["panels"][0]["object_name"].startswith("jobs/job-123/")
        )
        renderer = GoogleCloudNarratedPitchRenderer(
            store,
            FakeTTSClient(wav_bytes(), wav_bytes()),
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
            command_runner=FakeMediaRunner(),
        )

        manifest = renderer.render(
            brief=brief,
            timeline=timeline,
            source_message=source,
            visual_storyboard=visual_storyboard,
            job_id="job-123",
        )

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["card_count"], len(timeline["shots"]))  # type: ignore[arg-type]

    def test_rejects_cross_job_visual_and_truncated_final_duration(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, tts, _runner, _bucket = self.renderer()
        for index, panel in enumerate(visuals["panels"], start=1):  # type: ignore[index]
            decoded = base64.b64decode(panel.pop("data_base64"))
            artifact = renderer.artifact_store.put_bytes(
                "other-job", f"panel-{index}.jpg", decoded, "image/jpeg"
            )
            panel.update(
                {
                    "artifact_id": artifact["artifact_id"],
                    "object_name": artifact["object_name"],
                    "mime_type": "image/jpeg",
                }
            )
        with self.assertRaisesRegex(NarratedPitchRenderError, "visual_asset_job_mismatch"):
            renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertEqual(tts.requests, [])

        brief, timeline, source, visuals = pitch_values()
        renderer, _tts, _runner, bucket = self.renderer(
            runner=FakeMediaRunner(duration_seconds=0.25)
        )
        with self.assertRaisesRegex(NarratedPitchRenderError, "pitch_duration_mismatch"):
            renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertEqual(bucket.uploads, [])

    def test_fails_before_storage_on_missing_visual_or_probe_mismatch(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        renderer, tts, _runner, bucket = self.renderer()
        visuals["panels"][1]["status"] = "missing"  # type: ignore[index]
        visuals["panels"][1]["data_base64"] = None  # type: ignore[index]
        with self.assertRaisesRegex(NarratedPitchRenderError, "incomplete_visual_coverage"):
            renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertEqual(tts.requests, [])
        self.assertEqual(bucket.uploads, [])

        brief, timeline, source, visuals = pitch_values()
        mismatched = FakeMediaRunner(video_codec="vp9")
        renderer, _tts, _runner, bucket = self.renderer(runner=mismatched)
        with self.assertRaisesRegex(NarratedPitchRenderError, "pitch_probe_mismatch"):
            renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertEqual(bucket.uploads, [])

    def test_rejects_invalid_tts_audio_without_leaking_provider_detail(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        bucket = FakeBucket()
        store = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        renderer = GoogleCloudNarratedPitchRenderer(
            store,
            FakeTTSClient(b"provider-private-garbage"),
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
            command_runner=FakeMediaRunner(),
        )
        with self.assertRaisesRegex(NarratedPitchRenderError, "invalid_tts_audio") as caught:
            renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertNotIn("provider-private", str(caught.exception))
        self.assertEqual(bucket.uploads, [])

    def test_default_media_commands_are_bounded_by_a_timeout(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        bucket = FakeBucket()
        store = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        renderer = GoogleCloudNarratedPitchRenderer(
            store,
            FakeTTSClient(wav_bytes(), wav_bytes()),
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
        )
        with patch(
            "kira_studio.all_things_cloud_media.subprocess.run",
            side_effect=subprocess.TimeoutExpired("fake-ffmpeg", 600),
        ) as run:
            with self.assertRaisesRegex(NarratedPitchRenderError, "card_render_failed"):
                renderer.render(brief, timeline, source, visuals, "job-123")
        # A one-card task is bounded by TTS 120s + FFmpeg 600s + FFprobe
        # 600s, and the separate final task by two 600s media commands. Both
        # remain below the reviewed 1,740-second Cloud Tasks/Run envelope.
        self.assertEqual(run.call_args.kwargs["timeout"], 600)
        self.assertEqual(bucket.uploads, [])

    def test_matches_keyword_only_store_protocol_and_voice_language(self) -> None:
        brief, timeline, source, visuals = pitch_values()
        bucket = FakeBucket()
        concrete = GoogleCloudArtifactStore(bucket.name, bucket=bucket)
        renderer = GoogleCloudNarratedPitchRenderer(
            KeywordOnlyArtifactStore(concrete),  # type: ignore[arg-type]
            FakeTTSClient(wav_bytes(), wav_bytes()),
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
            voice_name="en-GB-Chirp3-HD-Aoede",
            command_runner=FakeMediaRunner(),
        )
        manifest = renderer.render(brief, timeline, source, visuals, "job-123")
        self.assertEqual(manifest["voice"]["language_code"], "en-GB")

        with self.assertRaisesRegex(CloudMediaValidationError, "unsafe_language_code"):
            GoogleCloudNarratedPitchRenderer(
                concrete,
                FakeTTSClient(),
                voice_name="en-GB-Chirp3-HD-Aoede",
                language_code="en-US",
            )


if __name__ == "__main__":
    unittest.main()
