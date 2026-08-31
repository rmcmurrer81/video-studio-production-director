"""Private Google Cloud storage and narrated-pitch rendering adapters.

The helpers in :mod:`kira_studio.all_things_media` decide what every card
must say.  This module owns the side effects: immutable private-object writes,
Google Cloud Text-to-Speech calls, FFmpeg rendering, and FFprobe validation.

Google SDK imports are deliberately lazy.  Tests can inject a bucket, TTS
client, image loader, and command runner without credentials, network access,
or local FFmpeg binaries.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import wave

from .all_things_media import (
    build_narrated_pitch_cues,
    pitch_narration_text,
)


NARRATED_PITCH_SCHEMA = "video-studio.narrated-pitch/v1"
NARRATED_PITCH_SEGMENT_SCHEMA = "video-studio.narrated-pitch-segment/v1"
DEFAULT_LANGUAGE_CODE = "en-US"
DEFAULT_VOICE_NAME = "en-US-Chirp3-HD-Aoede"

_SAFE_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SAFE_ARTIFACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_PREFIX_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
_SAFE_CONTENT_TYPE = re.compile(
    r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+"
    r"(?:\s*;\s*[A-Za-z0-9!#$&^_.+-]+=[A-Za-z0-9!#$&^_.+\"-]+)*"
)
_SAFE_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]")
_SHA256 = re.compile(r"[0-9a-f]{64}")

_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_PIXELS = 24_000_000
_MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_MAX_AUDIO_SEGMENT_BYTES = 100 * 1024 * 1024
_MAX_TTS_TEXT_BYTES = 4_800
_MAX_SEGMENT_SECONDS = 15 * 60
_MAX_PITCH_SECONDS = 60 * 60
# A continued pitch task renders one card (FFmpeg + FFprobe), while the final
# task concatenates and probes.  Two worst-case command timeouts therefore stay
# below Cloud Run's reviewed 1,740-second request ceiling with useful headroom.
_DEFAULT_COMMAND_TIMEOUT_SECONDS = 10 * 60
_TTS_REQUEST_TIMEOUT_SECONDS = 2 * 60
_CARD_AUDIO_PAD_SECONDS = 0.25
_FRAME_RATE = 24
_DURATION_TOLERANCE_SECONDS = 0.125


class CloudMediaError(RuntimeError):
    """A fail-closed cloud-media error with an allowlisted public code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CloudMediaValidationError(CloudMediaError, ValueError):
    """The caller supplied an unsafe or incomplete media value."""


class CloudMediaDependencyError(CloudMediaError):
    """A production-only dependency is missing."""


class ArtifactStoreError(CloudMediaError):
    """A private artifact could not be stored or read safely."""


class NarratedPitchRenderError(CloudMediaError):
    """A narrated pitch could not be completely rendered and verified."""


class _ImmutableManifest(dict[str, Any]):
    """A JSON-serializable dict whose published values cannot be changed."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("manifest is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _immutable_manifest(values: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(value: Any) -> Any:
        if isinstance(value, Mapping):
            # dict.__init__ populates without calling the overridden setter.
            return _ImmutableManifest({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        if isinstance(value, tuple):
            return tuple(freeze(item) for item in value)
        return value

    return freeze(values)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_sha256(value: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validated_identifier(value: Any, *, artifact: bool) -> str:
    pattern = _SAFE_ARTIFACT_ID if artifact else _SAFE_JOB_ID
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CloudMediaValidationError(
            "unsafe_artifact_id" if artifact else "unsafe_job_id"
        )
    # Dots are useful for extensions but dot-path semantics are never allowed.
    if artifact and (".." in value or value.endswith(".")):
        raise CloudMediaValidationError("unsafe_artifact_id")
    return value


def _validated_content_type(value: Any) -> str:
    if not isinstance(value, str):
        raise CloudMediaValidationError("unsafe_content_type")
    normalized = value.strip()
    if len(normalized) > 160 or _SAFE_CONTENT_TYPE.fullmatch(normalized) is None:
        raise CloudMediaValidationError("unsafe_content_type")
    return normalized


def _validated_prefix(value: Any) -> str:
    if not isinstance(value, str):
        raise CloudMediaValidationError("unsafe_object_prefix")
    parts = value.replace("\\", "/").split("/")
    if (
        not parts
        or len(parts) > 8
        or any(
            part in {"", ".", ".."} or _SAFE_PREFIX_SEGMENT.fullmatch(part) is None
            for part in parts
        )
    ):
        raise CloudMediaValidationError("unsafe_object_prefix")
    return "/".join(parts)


def _exception_status(exc: Exception) -> int | None:
    for attribute in ("code", "status_code"):
        value = getattr(exc, attribute, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        numeric = getattr(value, "value", None)
        if isinstance(numeric, int) and not isinstance(numeric, bool):
            return numeric
    return None


def _object_already_exists(exc: Exception) -> bool:
    return type(exc).__name__ in {"AlreadyExists", "Conflict", "PreconditionFailed"} or (
        _exception_status(exc) in {409, 412}
    )


class GoogleCloudArtifactStore:
    """Write and read content-addressed objects in one private GCS bucket.

    Object names are derived only from validated identifiers and the complete
    SHA-256 digest.  ``if_generation_match=0`` prevents an existing generation
    from ever being overwritten.  The returned manifest deliberately contains
    neither a bucket URL nor an ACL/public-URL field.
    """

    def __init__(
        self,
        bucket_name: str,
        *,
        client: Any | None = None,
        bucket: Any | None = None,
        prefix: str = "jobs",
    ) -> None:
        if (
            not isinstance(bucket_name, str)
            or _SAFE_BUCKET_NAME.fullmatch(bucket_name) is None
            or ".." in bucket_name
        ):
            raise CloudMediaValidationError("unsafe_bucket_name")
        self.bucket_name = bucket_name
        self.prefix = _validated_prefix(prefix)

        if bucket is None:
            if client is None:
                try:
                    from google.cloud import storage  # type: ignore[import-not-found]
                except ImportError:
                    raise CloudMediaDependencyError(
                        "google_cloud_storage_not_installed"
                    ) from None
                client = storage.Client()
            try:
                get_bucket = getattr(client, "get_bucket", None)
                bucket = (
                    get_bucket(bucket_name)
                    if callable(get_bucket)
                    else client.bucket(bucket_name)
                )
            except Exception:
                raise ArtifactStoreError("bucket_configuration_failed") from None
        reported_name = getattr(bucket, "name", bucket_name)
        if reported_name != bucket_name:
            raise CloudMediaValidationError("bucket_boundary_mismatch")
        self._verify_private_bucket(bucket, reload_metadata=True)
        self._bucket = bucket
        escaped = re.escape(self.prefix)
        self._object_pattern = re.compile(
            rf"{escaped}/(?P<job>{_SAFE_JOB_ID.pattern})/artifacts/"
            rf"(?P<digest>{_SHA256.pattern})/(?P<artifact>{_SAFE_ARTIFACT_ID.pattern})"
        )

    @staticmethod
    def _verify_private_bucket(bucket: Any, *, reload_metadata: bool) -> None:
        if reload_metadata:
            reload_bucket = getattr(bucket, "reload", None)
            if callable(reload_bucket):
                try:
                    reload_bucket()
                except Exception:
                    raise ArtifactStoreError("bucket_privacy_check_failed") from None
        configuration = getattr(bucket, "iam_configuration", None)
        prevention = getattr(configuration, "public_access_prevention", None)
        uniform_access = getattr(
            configuration, "uniform_bucket_level_access_enabled", None
        )
        if prevention != "enforced" or uniform_access is not True:
            raise CloudMediaValidationError("bucket_is_not_private")

    def _object_name(self, job_id: str, artifact_id: str, digest: str) -> str:
        return f"{self.prefix}/{job_id}/artifacts/{digest}/{artifact_id}"

    def _parse_object_name(self, object_name: Any) -> re.Match[str]:
        if not isinstance(object_name, str) or len(object_name) > 1_024:
            raise CloudMediaValidationError("unsafe_object_name")
        if (
            object_name.startswith(("/", "gs://", "http://", "https://"))
            or "\\" in object_name
            or "//" in object_name
        ):
            raise CloudMediaValidationError("unsafe_object_name")
        matched = self._object_pattern.fullmatch(object_name)
        if matched is None or ".." in matched.group("artifact"):
            raise CloudMediaValidationError("object_outside_bucket_boundary")
        return matched

    def put_bytes(
        self,
        job_id: str,
        artifact_id: str,
        data: bytes,
        content_type: str,
    ) -> Mapping[str, Any]:
        """Store a non-empty immutable object and return its safe manifest."""

        safe_job_id = _validated_identifier(job_id, artifact=False)
        safe_artifact_id = _validated_identifier(artifact_id, artifact=True)
        safe_content_type = _validated_content_type(content_type)
        if (
            not isinstance(data, bytes)
            or not data
            or len(data) > _MAX_ARTIFACT_BYTES
        ):
            raise CloudMediaValidationError("invalid_artifact_bytes")
        digest = sha256(data).hexdigest()
        object_name = self._object_name(safe_job_id, safe_artifact_id, digest)
        self._parse_object_name(object_name)
        blob: Any | None = None
        try:
            blob = self._bucket.blob(object_name)
            blob.upload_from_string(
                data,
                content_type=safe_content_type,
                if_generation_match=0,
            )
        except Exception as exc:
            if not _object_already_exists(exc) or blob is None:
                raise ArtifactStoreError("artifact_upload_failed") from None
            # Retrying the exact content-addressed write is idempotent, but only
            # after the existing bytes have been compared in full.
            try:
                existing = blob.download_as_bytes()
            except Exception:
                raise ArtifactStoreError("artifact_upload_conflict") from None
            if not isinstance(existing, bytes) or existing != data:
                raise ArtifactStoreError("artifact_upload_conflict") from None
        return _immutable_manifest(
            {
                "artifact_id": safe_artifact_id,
                "object_name": object_name,
                "sha256": digest,
                "bytes": len(data),
                "content_type": safe_content_type,
            }
        )

    def get_bytes(self, object_name: str) -> bytes:
        """Read one adapter-owned object and verify its content-addressed key."""

        matched = self._parse_object_name(object_name)
        try:
            data = self._bucket.blob(object_name).download_as_bytes()
        except Exception:
            raise ArtifactStoreError("artifact_download_failed") from None
        if (
            not isinstance(data, bytes)
            or not data
            or len(data) > _MAX_ARTIFACT_BYTES
            or sha256(data).hexdigest() != matched.group("digest")
        ):
            raise ArtifactStoreError("artifact_integrity_failed")
        return data


@dataclass(frozen=True)
class _AudioSegment:
    path: Path
    duration_seconds: float


@dataclass(frozen=True)
class _ProbeEvidence:
    duration_seconds: float
    video_duration_seconds: float
    audio_duration_seconds: float
    video_codec: str
    audio_codec: str
    width: int
    height: int
    video_stream_count: int
    audio_stream_count: int


def _default_command_runner(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        timeout=_DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )


class GoogleCloudNarratedPitchRenderer:
    """Synthesize and render one verified narrated storyboard MP4 per job."""

    def __init__(
        self,
        artifact_store: GoogleCloudArtifactStore,
        tts_client: Any | None = None,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        voice_name: str = DEFAULT_VOICE_NAME,
        language_code: str | None = None,
        command_runner: Callable[[Sequence[str]], Any] | None = None,
        image_loader: Callable[[str], bytes] | None = None,
        include_narration_text: bool = True,
        include_subtitles: bool = True,
    ) -> None:
        if not hasattr(artifact_store, "put_bytes") or not hasattr(
            artifact_store, "get_bytes"
        ):
            raise CloudMediaValidationError("invalid_artifact_store")
        self.artifact_store = artifact_store
        self._tts_injected = tts_client is not None
        self._tts_client = tts_client
        self.ffmpeg_path = self._safe_executable(ffmpeg_path)
        self.ffprobe_path = self._safe_executable(ffprobe_path)
        if (
            not isinstance(voice_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", voice_name)
            or "Chirp3-HD" not in voice_name
        ):
            raise CloudMediaValidationError("unsafe_voice_name")
        if language_code is None:
            language_code = "-".join(voice_name.split("-")[:2])
        if (
            not isinstance(language_code, str)
            or re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", language_code) is None
            or not voice_name.startswith(f"{language_code}-")
        ):
            raise CloudMediaValidationError("unsafe_language_code")
        if not isinstance(include_narration_text, bool) or not isinstance(
            include_subtitles, bool
        ):
            raise CloudMediaValidationError("invalid_sidecar_configuration")
        self.voice_name = voice_name
        self.language_code = language_code
        self.command_runner = command_runner or _default_command_runner
        self.image_loader = image_loader or artifact_store.get_bytes
        self.include_narration_text = include_narration_text
        self.include_subtitles = include_subtitles

    @staticmethod
    def _safe_executable(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 1_024
            or "\x00" in value
            or "\r" in value
            or "\n" in value
        ):
            raise CloudMediaValidationError("unsafe_media_executable")
        return value

    def _client_or_create(self) -> Any:
        if self._tts_client is not None:
            return self._tts_client
        try:
            from google.cloud import texttospeech  # type: ignore[import-not-found]
        except ImportError:
            raise CloudMediaDependencyError(
                "google_cloud_texttospeech_not_installed"
            ) from None
        try:
            self._tts_client = texttospeech.TextToSpeechClient()
        except Exception:
            raise CloudMediaError("tts_client_configuration_failed") from None
        return self._tts_client

    @staticmethod
    def _mapping(value: Any, *, code: str) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            try:
                converted = to_dict()
            except Exception:
                converted = None
            if isinstance(converted, Mapping):
                return converted
        raise NarratedPitchRenderError(code)

    @staticmethod
    def _panel_artifact(panel: Mapping[str, Any]) -> Mapping[str, Any] | None:
        for key in ("artifact", "image_artifact", "artifact_manifest"):
            value = panel.get(key)
            if isinstance(value, Mapping):
                return value
        return None

    def _resolve_image(self, panel: Mapping[str, Any], *, job_id: str) -> bytes:
        direct = panel.get("image_bytes")
        expected_digest = panel.get("content_sha256")
        expected_bytes = panel.get("byte_length")
        expected_content_type = panel.get("mime_type")
        panel_artifact_id = panel.get("artifact_id")
        if panel_artifact_id is not None:
            try:
                panel_artifact_id = _validated_identifier(
                    panel_artifact_id, artifact=True
                )
            except CloudMediaValidationError:
                raise NarratedPitchRenderError("invalid_visual_asset") from None
        if isinstance(direct, bytes):
            image = direct
        elif isinstance(panel.get("data_base64"), str):
            try:
                image = base64.b64decode(panel["data_base64"], validate=True)
            except Exception:
                raise NarratedPitchRenderError("invalid_visual_asset") from None
        else:
            artifact = self._panel_artifact(panel)
            object_name = panel.get("object_name")
            if artifact is not None:
                object_name = artifact.get("object_name")
                expected_digest = artifact.get("sha256", expected_digest)
                expected_bytes = artifact.get("bytes", artifact.get("byte_length", expected_bytes))
                expected_content_type = artifact.get(
                    "content_type", expected_content_type
                )
                nested_artifact_id = artifact.get("artifact_id")
                if panel_artifact_id is not None and nested_artifact_id != panel_artifact_id:
                    raise NarratedPitchRenderError("visual_asset_integrity_failed")
            if not isinstance(object_name, str):
                # A bare artifact_id cannot identify a content-addressed object.
                raise NarratedPitchRenderError("unresolved_visual_asset")
            prefix = getattr(self.artifact_store, "prefix", "jobs")
            if (
                not isinstance(prefix, str)
                or not object_name.startswith(f"{prefix}/{job_id}/")
            ):
                raise NarratedPitchRenderError("visual_asset_job_mismatch")
            if (
                panel_artifact_id is not None
                and object_name.rsplit("/", 1)[-1] != panel_artifact_id
            ):
                raise NarratedPitchRenderError("visual_asset_integrity_failed")
            try:
                image = self.image_loader(object_name)
            except Exception:
                raise NarratedPitchRenderError("visual_asset_load_failed") from None
        if not isinstance(image, bytes) or not 100 <= len(image) <= _MAX_IMAGE_BYTES:
            raise NarratedPitchRenderError("invalid_visual_asset")
        digest = sha256(image).hexdigest()
        if expected_digest is not None and (
            not isinstance(expected_digest, str)
            or _SHA256.fullmatch(expected_digest) is None
            or expected_digest != digest
        ):
            raise NarratedPitchRenderError("visual_asset_integrity_failed")
        if expected_bytes is not None and (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes != len(image)
        ):
            raise NarratedPitchRenderError("visual_asset_integrity_failed")
        actual_content_type = self._validate_static_image(image)
        if expected_content_type is not None and expected_content_type != actual_content_type:
            raise NarratedPitchRenderError("visual_asset_integrity_failed")
        return image

    @staticmethod
    def _validate_static_image(image: bytes) -> str:
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            raise CloudMediaDependencyError("pillow_not_installed") from None
        try:
            with Image.open(BytesIO(image)) as opened:
                image_format = opened.format
                width, height = opened.size
                animated = bool(getattr(opened, "is_animated", False))
                frames = int(getattr(opened, "n_frames", 1))
                opened.verify()
        except Exception:
            raise NarratedPitchRenderError("invalid_visual_asset") from None
        if (
            image_format not in {"JPEG", "PNG", "WEBP"}
            or animated
            or frames != 1
            or width < 16
            or height < 16
            or width * height > _MAX_IMAGE_PIXELS
        ):
            raise NarratedPitchRenderError("invalid_visual_asset")
        return {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }[image_format]

    def _synthesize(self, narration: str, destination: Path) -> _AudioSegment:
        if (
            not isinstance(narration, str)
            or not narration.strip()
            or len(narration.encode("utf-8")) > _MAX_TTS_TEXT_BYTES
        ):
            raise NarratedPitchRenderError("invalid_narration_cue")
        request = {
            "input": {"text": narration},
            "voice": {
                "language_code": self.language_code,
                "name": self.voice_name,
            },
            "audio_config": {"audio_encoding": "LINEAR16"},
        }
        try:
            response = self._client_or_create().synthesize_speech(
                request=request,
                timeout=_TTS_REQUEST_TIMEOUT_SECONDS,
            )
        except CloudMediaError:
            raise
        except Exception:
            raise NarratedPitchRenderError("tts_synthesis_failed") from None
        audio = (
            response.get("audio_content")
            if isinstance(response, Mapping)
            else getattr(response, "audio_content", None)
        )
        if (
            not isinstance(audio, bytes)
            or not audio
            or len(audio) > _MAX_AUDIO_SEGMENT_BYTES
        ):
            raise NarratedPitchRenderError("invalid_tts_audio")
        try:
            with wave.open(BytesIO(audio), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                frame_rate = wav.getframerate()
                frame_count = wav.getnframes()
                compression = wav.getcomptype()
        except (EOFError, wave.Error):
            raise NarratedPitchRenderError("invalid_tts_audio") from None
        duration = frame_count / frame_rate if frame_rate else 0.0
        if (
            channels not in {1, 2}
            or sample_width not in {1, 2, 3, 4}
            or frame_rate < 8_000
            or compression != "NONE"
            or not math.isfinite(duration)
            or not 0 < duration <= _MAX_SEGMENT_SECONDS
        ):
            raise NarratedPitchRenderError("invalid_tts_audio")
        destination.write_bytes(audio)
        return _AudioSegment(destination, duration)

    @staticmethod
    def _image_extension(image: bytes) -> str:
        if image.startswith(b"\xff\xd8"):
            return ".jpg"
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        return ".webp"

    @staticmethod
    def _return_code(result: Any) -> int:
        if isinstance(result, int) and not isinstance(result, bool):
            return result
        if isinstance(result, Mapping):
            value = result.get("returncode", 0)
        else:
            value = getattr(result, "returncode", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 1

    @staticmethod
    def _stdout(result: Any) -> str:
        if isinstance(result, Mapping):
            value = result.get("stdout")
            if value is None and "streams" in result:
                return json.dumps(result)
        else:
            value = getattr(result, "stdout", None)
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return ""
        return value if isinstance(value, str) else ""

    def _run(self, command: Sequence[str], *, code: str) -> Any:
        if not command or any(not isinstance(item, str) or "\x00" in item for item in command):
            raise NarratedPitchRenderError("unsafe_media_command")
        try:
            result = self.command_runner(tuple(command))
        except Exception:
            raise NarratedPitchRenderError(code) from None
        if self._return_code(result) != 0:
            # FFmpeg stderr can echo filesystem paths or upstream text.  It is
            # intentionally excluded from public exceptions and manifests.
            raise NarratedPitchRenderError(code)
        return result

    @staticmethod
    def _display_duration_seconds(audio_duration_seconds: float) -> float:
        """Add the card's short tail and align its still image to 24 fps."""

        if (
            not math.isfinite(audio_duration_seconds)
            or not 0 < audio_duration_seconds <= _MAX_SEGMENT_SECONDS
        ):
            raise NarratedPitchRenderError("invalid_tts_audio")
        duration = math.ceil(
            (audio_duration_seconds + _CARD_AUDIO_PAD_SECONDS) * _FRAME_RATE - 1e-9
        ) / _FRAME_RATE
        if not 0 < duration <= _MAX_SEGMENT_SECONDS:
            raise NarratedPitchRenderError("pitch_duration_exceeded")
        return duration

    def _render_segment(
        self,
        image_path: Path,
        audio_path: Path,
        destination: Path,
        *,
        display_duration_seconds: float,
    ) -> None:
        command = (
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            (
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p"
            ),
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-tune",
            "stillimage",
            "-af",
            f"apad=whole_dur={display_duration_seconds:.6f}",
            "-t",
            f"{display_duration_seconds:.6f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(destination),
        )
        self._run(command, code="card_render_failed")
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise NarratedPitchRenderError("card_render_failed")

    def _concat_segments(self, segments: Sequence[Path], destination: Path) -> None:
        if not segments:
            raise NarratedPitchRenderError("incomplete_card_render")
        if any(segment.parent != destination.parent for segment in segments):
            raise NarratedPitchRenderError("unsafe_concat_input")
        concat_file = destination.with_suffix(".ffconcat")
        concat_file.write_text(
            "ffconcat version 1.0\n"
            + "".join(
                # All names are generated by this class.  Relative names avoid
                # drive-letter/protocol ambiguity in Windows FFmpeg builds.
                f"file '{segment.name}'\n" for segment in segments
            ),
            encoding="utf-8",
            newline="\n",
        )
        command = (
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            "scale=1920:1080,setsar=1,format=yuv420p",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(destination),
        )
        self._run(command, code="pitch_render_failed")
        if (
            not destination.is_file()
            or not 0 < destination.stat().st_size <= _MAX_VIDEO_BYTES
        ):
            raise NarratedPitchRenderError("pitch_render_failed")

    @staticmethod
    def _stream_duration(value: Mapping[str, Any], *, failure_code: str) -> float:
        raw_duration = value.get("duration")
        if type(raw_duration) not in {str, int, float}:
            raise NarratedPitchRenderError(failure_code)
        try:
            duration = float(raw_duration)
        except (OverflowError, TypeError, ValueError):
            raise NarratedPitchRenderError(failure_code) from None
        if not math.isfinite(duration) or duration <= 0:
            raise NarratedPitchRenderError(failure_code)
        return duration

    def _probe_media(
        self,
        video_path: Path,
        *,
        failure_code: str,
        mismatch_code: str,
        maximum_duration_seconds: float,
        expected_duration_seconds: float | None = None,
        duration_tolerance_seconds: float = _DURATION_TOLERANCE_SECONDS,
    ) -> _ProbeEvidence:
        command = (
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,duration",
            "-of",
            "json",
            str(video_path),
        )
        result = self._run(command, code=failure_code)
        try:
            payload = json.loads(self._stdout(result))
        except (TypeError, ValueError):
            raise NarratedPitchRenderError(failure_code) from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("streams"), list):
            raise NarratedPitchRenderError(failure_code)
        videos = [
            item
            for item in payload["streams"]
            if isinstance(item, Mapping) and item.get("codec_type") == "video"
        ]
        audios = [
            item
            for item in payload["streams"]
            if isinstance(item, Mapping) and item.get("codec_type") == "audio"
        ]
        format_value = payload.get("format")
        if not isinstance(format_value, Mapping):
            raise NarratedPitchRenderError(failure_code)
        try:
            duration = self._stream_duration(format_value, failure_code=mismatch_code)
            video_duration = self._stream_duration(videos[0], failure_code=mismatch_code)
            audio_duration = self._stream_duration(audios[0], failure_code=mismatch_code)
        except IndexError:
            raise NarratedPitchRenderError(mismatch_code) from None
        if (
            len(payload["streams"]) != 2
            or len(videos) != 1
            or len(audios) != 1
            or videos[0].get("codec_name") != "h264"
            or videos[0].get("width") != 1920
            or videos[0].get("height") != 1080
            or audios[0].get("codec_name") != "aac"
            or duration > maximum_duration_seconds
            or video_duration > maximum_duration_seconds
            or audio_duration > maximum_duration_seconds
            or abs(video_duration - audio_duration) > duration_tolerance_seconds
            or abs(duration - video_duration) > duration_tolerance_seconds
            or abs(duration - audio_duration) > duration_tolerance_seconds
            or (
                expected_duration_seconds is not None
                and (
                    not math.isfinite(expected_duration_seconds)
                    or expected_duration_seconds <= 0
                    or abs(duration - expected_duration_seconds)
                    > duration_tolerance_seconds
                )
            )
        ):
            raise NarratedPitchRenderError(mismatch_code)
        return _ProbeEvidence(
            duration_seconds=round(duration, 6),
            video_duration_seconds=round(video_duration, 6),
            audio_duration_seconds=round(audio_duration, 6),
            video_codec="h264",
            audio_codec="aac",
            width=1920,
            height=1080,
            video_stream_count=1,
            audio_stream_count=1,
        )

    def _probe_segment(
        self,
        video_path: Path,
        *,
        expected_duration_seconds: float,
    ) -> float:
        """Validate one card's container, video, and padded audio duration."""

        return self._probe_media(
            video_path,
            failure_code="segment_probe_failed",
            mismatch_code="segment_probe_mismatch",
            maximum_duration_seconds=_MAX_SEGMENT_SECONDS,
            expected_duration_seconds=expected_duration_seconds,
        ).duration_seconds

    def _probe(
        self,
        video_path: Path,
        *,
        expected_duration_seconds: float,
        card_count: int,
    ) -> _ProbeEvidence:
        duration_tolerance = max(
            _DURATION_TOLERANCE_SECONDS,
            min(0.5, card_count * 0.02),
        )
        evidence = self._probe_media(
            video_path,
            failure_code="pitch_probe_failed",
            mismatch_code="pitch_probe_mismatch",
            maximum_duration_seconds=_MAX_PITCH_SECONDS,
            duration_tolerance_seconds=duration_tolerance,
        )
        if abs(evidence.duration_seconds - expected_duration_seconds) > duration_tolerance:
            raise NarratedPitchRenderError("pitch_duration_mismatch")
        return evidence
    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1_000))
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        whole_seconds, milliseconds = divmod(milliseconds, 1_000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"

    def _subtitles(
        self,
        cues: Sequence[Mapping[str, Any]],
        durations: Sequence[float],
    ) -> str:
        if len(cues) != len(durations):
            raise NarratedPitchRenderError("incomplete_subtitle_coverage")
        blocks: list[str] = []
        position = 0.0
        for index, (cue, duration) in enumerate(zip(cues, durations), start=1):
            narration = str(cue.get("narration") or "").replace("\r", " ").replace("\n", " ")
            if not narration.strip():
                raise NarratedPitchRenderError("incomplete_subtitle_coverage")
            end = position + duration
            blocks.append(
                f"{index}\n{self._srt_timestamp(position)} --> {self._srt_timestamp(end)}\n"
                f"{narration}\n"
            )
            position = end
        return "\n".join(blocks).rstrip() + "\n"

    @staticmethod
    def _artifact_entry(
        manifest: Mapping[str, Any],
        *,
        expected_artifact_id: str,
        expected_data: bytes,
        expected_content_type: str,
    ) -> dict[str, Any]:
        expected = {"artifact_id", "object_name", "sha256", "bytes", "content_type"}
        if not isinstance(manifest, Mapping) or set(manifest) != expected:
            raise NarratedPitchRenderError("invalid_artifact_manifest")
        artifact_id = manifest.get("artifact_id")
        object_name = manifest.get("object_name")
        digest = manifest.get("sha256")
        byte_length = manifest.get("bytes")
        content_type = manifest.get("content_type")
        if (
            artifact_id != expected_artifact_id
            or not isinstance(artifact_id, str)
            or _SAFE_ARTIFACT_ID.fullmatch(artifact_id) is None
            or not isinstance(object_name, str)
            or not object_name
            or len(object_name) > 1_024
            or object_name.startswith(("/", "gs://", "http://", "https://"))
            or "\\" in object_name
            or "//" in object_name
            or any(part in {"", ".", ".."} for part in object_name.split("/"))
            or digest != sha256(expected_data).hexdigest()
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length != len(expected_data)
            or content_type != expected_content_type
        ):
            raise NarratedPitchRenderError("invalid_artifact_manifest")
        return {
            "artifact_id": artifact_id,
            "object_name": object_name,
            "content_type": content_type,
            "sha256": digest,
            "byte_length": byte_length,
        }

    def _validated_pitch_inputs(
        self,
        brief: Mapping[str, Any] | Any,
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
        *,
        resolve_images: bool,
    ) -> tuple[
        str,
        Mapping[str, Any],
        Mapping[str, Any],
        list[dict[str, Any]],
        list[Mapping[str, Any]],
        list[bytes],
    ]:
        """Validate complete card/cue identity before any pitch side effect."""

        safe_job_id = _validated_identifier(job_id, artifact=False)
        safe_brief = self._mapping(brief, code="invalid_pitch_brief")
        safe_timeline = self._mapping(timeline, code="invalid_pitch_timeline")
        safe_visuals = self._mapping(
            visual_storyboard, code="invalid_visual_storyboard"
        )
        if not isinstance(source_message, str):
            raise NarratedPitchRenderError("invalid_source_message")
        try:
            cues = build_narrated_pitch_cues(
                safe_brief,
                safe_timeline,
                source_message=source_message,
            )
        except Exception:
            raise NarratedPitchRenderError("invalid_narration_input") from None
        storyboard_status = safe_visuals.get("status")
        if storyboard_status is not None and storyboard_status != "complete":
            raise NarratedPitchRenderError("incomplete_visual_coverage")
        storyboard_digest = safe_visuals.get("manifest_sha256")
        if storyboard_digest is not None:
            storyboard_body = dict(safe_visuals)
            storyboard_body.pop("manifest_sha256", None)
            if (
                not isinstance(storyboard_digest, str)
                or _SHA256.fullmatch(storyboard_digest) is None
                or storyboard_digest != _manifest_sha256(storyboard_body)
            ):
                raise NarratedPitchRenderError("invalid_visual_storyboard")
        raw_panels = safe_visuals.get("panels")
        if (
            not isinstance(raw_panels, Sequence)
            or isinstance(raw_panels, (str, bytes))
            or not raw_panels
            or len(raw_panels) != len(cues)
        ):
            raise NarratedPitchRenderError("incomplete_visual_coverage")
        for field, expected in (
            ("required_panel_count", len(cues)),
            ("available_panel_count", len(cues)),
            ("missing_panel_count", 0),
        ):
            value = safe_visuals.get(field)
            if value is not None and value != expected:
                raise NarratedPitchRenderError("incomplete_visual_coverage")
        panels: list[Mapping[str, Any]] = []
        images: list[bytes] = []
        for cue, raw_panel in zip(cues, raw_panels):
            if not isinstance(raw_panel, Mapping):
                raise NarratedPitchRenderError("invalid_visual_storyboard")
            if raw_panel.get("shot_id") != cue.get("shot_id"):
                raise NarratedPitchRenderError("visual_cue_identity_mismatch")
            if raw_panel.get("status") not in {None, "available", "stored"}:
                raise NarratedPitchRenderError("incomplete_visual_coverage")
            panels.append(raw_panel)
            if resolve_images:
                images.append(self._resolve_image(raw_panel, job_id=safe_job_id))
        return safe_job_id, safe_brief, safe_timeline, cues, panels, images

    @staticmethod
    def _segment_artifact_id(sequence: int) -> str:
        return f"pitch-card-{sequence:04d}.mp4"

    def render_segment_chunk(
        self,
        *,
        brief: Mapping[str, Any] | Any,
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
        start_index: int,
        max_cards: int = 1,
        ownership_check: Callable[[], bool] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Render a bounded run of independently verified private card MP4s."""

        (
            safe_job_id,
            _safe_brief,
            _safe_timeline,
            cues,
            panels,
            _images,
        ) = self._validated_pitch_inputs(
            brief,
            timeline,
            source_message,
            visual_storyboard,
            job_id,
            resolve_images=False,
        )
        if (
            isinstance(start_index, bool)
            or not isinstance(start_index, int)
            or not 0 <= start_index < len(cues)
            or max_cards != 1
        ):
            raise NarratedPitchRenderError("invalid_narration_input")
        if ownership_check is not None and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")
        cue = cues[start_index]
        image = self._resolve_image(panels[start_index], job_id=safe_job_id)
        if ownership_check is not None and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")
        sequence = start_index + 1
        with tempfile.TemporaryDirectory(prefix="kira-pitch-card-") as temp_name:
            temp = Path(temp_name)
            image_path = temp / f"card-{sequence:04d}{self._image_extension(image)}"
            audio_path = temp / f"cue-{sequence:04d}.wav"
            segment_path = temp / f"segment-{sequence:04d}.mp4"
            image_path.write_bytes(image)
            audio = self._synthesize(str(cue.get("narration") or ""), audio_path)
            display_duration = self._display_duration_seconds(audio.duration_seconds)
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")
            self._render_segment(
                image_path,
                audio.path,
                segment_path,
                display_duration_seconds=display_duration,
            )
            duration = self._probe_segment(
                segment_path,
                expected_duration_seconds=display_duration,
            )
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")
            segment_bytes = segment_path.read_bytes()
            artifact_id = self._segment_artifact_id(sequence)
            stored = self.artifact_store.put_bytes(
                job_id=safe_job_id,
                artifact_id=artifact_id,
                data=segment_bytes,
                content_type="video/mp4",
            )
            artifact = self._artifact_entry(
                stored,
                expected_artifact_id=artifact_id,
                expected_data=segment_bytes,
                expected_content_type="video/mp4",
            )
        if ownership_check is not None and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")
        return (
            {
                "schema": NARRATED_PITCH_SEGMENT_SCHEMA,
                "sequence": sequence,
                "shot_id": str(cue["shot_id"]),
                "duration_seconds": duration,
                **artifact,
            },
        )

    def finalize_segments(
        self,
        *,
        brief: Mapping[str, Any] | Any,
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
        segments: Sequence[Mapping[str, Any]],
        ownership_check: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        """Assemble a complete pitch from integrity-checked card MP4 artifacts."""

        (
            safe_job_id,
            safe_brief,
            _safe_timeline,
            cues,
            panels,
            _images,
        ) = self._validated_pitch_inputs(
            brief,
            timeline,
            source_message,
            visual_storyboard,
            job_id,
            resolve_images=False,
        )
        if (
            not isinstance(segments, Sequence)
            or isinstance(segments, (str, bytes))
            or len(segments) != len(cues)
        ):
            raise NarratedPitchRenderError("incomplete_card_render")
        durations: list[float] = []
        loaded: list[bytes] = []
        total_segment_bytes = 0
        expected_fields = {
            "schema",
            "sequence",
            "shot_id",
            "duration_seconds",
            "artifact_id",
            "object_name",
            "content_type",
            "sha256",
            "byte_length",
        }
        for index, (cue, raw_segment) in enumerate(zip(cues, segments), start=1):
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")
            if not isinstance(raw_segment, Mapping) or set(raw_segment) != expected_fields:
                raise NarratedPitchRenderError("invalid_artifact_manifest")
            duration = raw_segment.get("duration_seconds")
            object_name = raw_segment.get("object_name")
            digest = raw_segment.get("sha256")
            byte_length = raw_segment.get("byte_length")
            if (
                raw_segment.get("schema") != NARRATED_PITCH_SEGMENT_SCHEMA
                or raw_segment.get("sequence") != index
                or raw_segment.get("shot_id") != cue.get("shot_id")
                or raw_segment.get("artifact_id") != self._segment_artifact_id(index)
                or raw_segment.get("content_type") != "video/mp4"
                or not isinstance(object_name, str)
                or not object_name.startswith(f"jobs/{safe_job_id}/artifacts/")
                or not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
                or isinstance(byte_length, bool)
                or not isinstance(byte_length, int)
                or not 0 < byte_length <= _MAX_VIDEO_BYTES
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration))
                or not 0 < float(duration) <= _MAX_SEGMENT_SECONDS
            ):
                raise NarratedPitchRenderError("invalid_artifact_manifest")
            total_segment_bytes += byte_length
            if total_segment_bytes > _MAX_VIDEO_BYTES:
                raise NarratedPitchRenderError("invalid_artifact_manifest")
            try:
                data = self.artifact_store.get_bytes(object_name)
            except Exception:
                raise NarratedPitchRenderError("visual_asset_load_failed") from None
            if (
                not isinstance(data, bytes)
                or len(data) != byte_length
                or sha256(data).hexdigest() != digest
            ):
                raise NarratedPitchRenderError("visual_asset_integrity_failed")
            durations.append(float(duration))
            loaded.append(data)
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")
        if sum(durations) > _MAX_PITCH_SECONDS:
            raise NarratedPitchRenderError("pitch_duration_exceeded")
        if ownership_check is not None and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")

        with tempfile.TemporaryDirectory(prefix="kira-narrated-pitch-final-") as temp_name:
            temp = Path(temp_name)
            segment_paths: list[Path] = []
            for index, data in enumerate(loaded, start=1):
                path = temp / f"segment-{index:04d}.mp4"
                path.write_bytes(data)
                segment_paths.append(path)
            video_path = temp / "narrated-pitch.mp4"
            self._concat_segments(segment_paths, video_path)
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")
            evidence = self._probe(
                video_path,
                expected_duration_seconds=sum(durations),
                card_count=len(cues),
            )
            video_bytes = video_path.read_bytes()
            if not 0 < len(video_bytes) <= _MAX_VIDEO_BYTES:
                raise NarratedPitchRenderError("invalid_rendered_video")
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")

            narration_value: Mapping[str, Any] | None = None
            narration_bytes: bytes | None = None
            subtitles_value: Mapping[str, Any] | None = None
            subtitle_bytes: bytes | None = None
            if self.include_narration_text:
                narration_bytes = pitch_narration_text(safe_brief, cues).encode("utf-8")
                narration_value = self.artifact_store.put_bytes(
                    job_id=safe_job_id,
                    artifact_id="narration.txt",
                    data=narration_bytes,
                    content_type="text/plain; charset=utf-8",
                )
                if ownership_check is not None and not ownership_check():
                    raise NarratedPitchRenderError("work_stopped")
            if self.include_subtitles:
                subtitle_bytes = self._subtitles(cues, durations).encode("utf-8")
                subtitles_value = self.artifact_store.put_bytes(
                    job_id=safe_job_id,
                    artifact_id="subtitles.srt",
                    data=subtitle_bytes,
                    content_type="application/x-subrip",
                )
                if ownership_check is not None and not ownership_check():
                    raise NarratedPitchRenderError("work_stopped")
            video_value = self.artifact_store.put_bytes(
                job_id=safe_job_id,
                artifact_id="narrated-pitch.mp4",
                data=video_bytes,
                content_type="video/mp4",
            )
            if ownership_check is not None and not ownership_check():
                raise NarratedPitchRenderError("work_stopped")

        video_entry = self._artifact_entry(
            video_value,
            expected_artifact_id="narrated-pitch.mp4",
            expected_data=video_bytes,
            expected_content_type="video/mp4",
        )
        video_entry.update(
            {
                "width": evidence.width,
                "height": evidence.height,
                "video_codec": evidence.video_codec,
                "audio_codec": evidence.audio_codec,
                "duration_seconds": evidence.duration_seconds,
            }
        )
        body: dict[str, Any] = {
            "schema": NARRATED_PITCH_SCHEMA,
            "status": "complete",
            "card_count": len(panels),
            "cue_count": len(cues),
            "video": video_entry,
            "narration_text": (
                self._artifact_entry(
                    narration_value,
                    expected_artifact_id="narration.txt",
                    expected_data=narration_bytes,
                    expected_content_type="text/plain; charset=utf-8",
                )
                if narration_value is not None and narration_bytes is not None
                else None
            ),
            "subtitles": (
                self._artifact_entry(
                    subtitles_value,
                    expected_artifact_id="subtitles.srt",
                    expected_data=subtitle_bytes,
                    expected_content_type="application/x-subrip",
                )
                if subtitles_value is not None and subtitle_bytes is not None
                else None
            ),
            "voice": {
                "provider": "Google Cloud Text-to-Speech",
                "framework": "google-cloud-texttospeech",
                "model": "Chirp 3: HD",
                "name": self.voice_name,
                "language_code": self.language_code,
                "audio_encoding": "LINEAR16",
                "segment_count": len(cues),
                "evidence_origin": (
                    "injected_test_client"
                    if self._tts_injected
                    else "live_google_provider_response"
                ),
            },
            "verification": {
                "status": "passed",
                "tool": "ffprobe",
                "scope": "container_codecs_dimensions_and_duration",
                "video_stream_count": evidence.video_stream_count,
                "audio_stream_count": evidence.audio_stream_count,
                "video_codec": evidence.video_codec,
                "audio_codec": evidence.audio_codec,
                "width": evidence.width,
                "height": evidence.height,
                "duration_seconds": evidence.duration_seconds,
            },
        }
        body["manifest_sha256"] = _manifest_sha256(body)
        return _immutable_manifest(body)

    def render(
        self,
        brief: Mapping[str, Any] | Any,
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
    ) -> Mapping[str, Any]:
        """Render, verify, privately store, and manifest a complete pitch.

        No partial result is returned.  Every planned card must have a matching
        available visual, valid synthesized audio, a successfully rendered
        segment, and coverage in the concatenated output.
        """

        (
            safe_job_id,
            safe_brief,
            _safe_timeline,
            cues,
            panels,
            images,
        ) = self._validated_pitch_inputs(
            brief,
            timeline,
            source_message,
            visual_storyboard,
            job_id,
            resolve_images=True,
        )

        with tempfile.TemporaryDirectory(prefix="kira-narrated-pitch-") as temp_name:
            temp = Path(temp_name)
            segment_paths: list[Path] = []
            segment_durations: list[float] = []
            audio_duration_total = 0.0
            rendered_duration_total = 0.0
            for index, (cue, image) in enumerate(zip(cues, images), start=1):
                image_path = temp / f"card-{index:04d}{self._image_extension(image)}"
                audio_path = temp / f"cue-{index:04d}.wav"
                segment_path = temp / f"segment-{index:04d}.mp4"
                image_path.write_bytes(image)
                segment = self._synthesize(str(cue.get("narration") or ""), audio_path)
                display_duration = self._display_duration_seconds(segment.duration_seconds)
                audio_duration_total += display_duration
                if audio_duration_total > _MAX_PITCH_SECONDS:
                    raise NarratedPitchRenderError("pitch_duration_exceeded")
                self._render_segment(
                    image_path,
                    segment.path,
                    segment_path,
                    display_duration_seconds=display_duration,
                )
                encoded_duration = self._probe_segment(
                    segment_path,
                    expected_duration_seconds=display_duration,
                )
                rendered_duration_total += encoded_duration
                if rendered_duration_total > _MAX_PITCH_SECONDS:
                    raise NarratedPitchRenderError("pitch_duration_exceeded")
                segment_paths.append(segment_path)
                segment_durations.append(encoded_duration)
            if len(segment_paths) != len(cues):
                raise NarratedPitchRenderError("incomplete_card_render")

            video_path = temp / "narrated-pitch.mp4"
            self._concat_segments(segment_paths, video_path)
            evidence = self._probe(
                video_path,
                expected_duration_seconds=sum(segment_durations),
                card_count=len(cues),
            )
            video_bytes = video_path.read_bytes()
            if not 0 < len(video_bytes) <= _MAX_VIDEO_BYTES:
                raise NarratedPitchRenderError("invalid_rendered_video")

            narration_value: Mapping[str, Any] | None = None
            narration_bytes: bytes | None = None
            subtitles_value: Mapping[str, Any] | None = None
            subtitle_bytes: bytes | None = None
            if self.include_narration_text:
                narration_bytes = pitch_narration_text(safe_brief, cues).encode("utf-8")
                narration_value = self.artifact_store.put_bytes(
                    job_id=safe_job_id,
                    artifact_id="narration.txt",
                    data=narration_bytes,
                    content_type="text/plain; charset=utf-8",
                )
            if self.include_subtitles:
                subtitle_bytes = self._subtitles(cues, segment_durations).encode("utf-8")
                subtitles_value = self.artifact_store.put_bytes(
                    job_id=safe_job_id,
                    artifact_id="subtitles.srt",
                    data=subtitle_bytes,
                    content_type="application/x-subrip",
                )
            video_value = self.artifact_store.put_bytes(
                job_id=safe_job_id,
                artifact_id="narrated-pitch.mp4",
                data=video_bytes,
                content_type="video/mp4",
            )

        video_entry = self._artifact_entry(
            video_value,
            expected_artifact_id="narrated-pitch.mp4",
            expected_data=video_bytes,
            expected_content_type="video/mp4",
        )
        video_entry.update(
            {
                "width": evidence.width,
                "height": evidence.height,
                "video_codec": evidence.video_codec,
                "audio_codec": evidence.audio_codec,
                "duration_seconds": evidence.duration_seconds,
            }
        )
        body: dict[str, Any] = {
            "schema": NARRATED_PITCH_SCHEMA,
            "status": "complete",
            "card_count": len(panels),
            "cue_count": len(cues),
            "video": video_entry,
            "narration_text": (
                self._artifact_entry(
                    narration_value,
                    expected_artifact_id="narration.txt",
                    expected_data=narration_bytes,
                    expected_content_type="text/plain; charset=utf-8",
                )
                if narration_value is not None and narration_bytes is not None
                else None
            ),
            "subtitles": (
                self._artifact_entry(
                    subtitles_value,
                    expected_artifact_id="subtitles.srt",
                    expected_data=subtitle_bytes,
                    expected_content_type="application/x-subrip",
                )
                if subtitles_value is not None and subtitle_bytes is not None
                else None
            ),
            "voice": {
                "provider": "Google Cloud Text-to-Speech",
                "framework": "google-cloud-texttospeech",
                "model": "Chirp 3: HD",
                "name": self.voice_name,
                "language_code": self.language_code,
                "audio_encoding": "LINEAR16",
                "segment_count": len(cues),
                "evidence_origin": (
                    "injected_test_client"
                    if self._tts_injected
                    else "live_google_provider_response"
                ),
            },
            "verification": {
                "status": "passed",
                "tool": "ffprobe",
                "scope": "container_codecs_dimensions_and_duration",
                "video_stream_count": evidence.video_stream_count,
                "audio_stream_count": evidence.audio_stream_count,
                "video_codec": evidence.video_codec,
                "audio_codec": evidence.audio_codec,
                "width": evidence.width,
                "height": evidence.height,
                "duration_seconds": evidence.duration_seconds,
            },
        }
        body["manifest_sha256"] = _manifest_sha256(body)
        return _immutable_manifest(body)

    # A descriptive alias is convenient for callers that expose several
    # renderer types; ``render`` remains the stable integration interface.
    def render_pitch(
        self,
        brief: Mapping[str, Any] | Any,
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
    ) -> Mapping[str, Any]:
        return self.render(
            brief=brief,
            timeline=timeline,
            source_message=source_message,
            visual_storyboard=visual_storyboard,
            job_id=job_id,
        )


__all__ = [
    "ArtifactStoreError",
    "CloudMediaDependencyError",
    "CloudMediaError",
    "CloudMediaValidationError",
    "DEFAULT_LANGUAGE_CODE",
    "DEFAULT_VOICE_NAME",
    "GoogleCloudArtifactStore",
    "GoogleCloudNarratedPitchRenderer",
    "NARRATED_PITCH_SCHEMA",
    "NarratedPitchRenderError",
]
