from __future__ import annotations

from http.client import HTTPConnection
from http import HTTPStatus
from threading import Thread
import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from all_things_cloud_app import MAX_BODY_BYTES, Runtime, Server
from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    ConfigurationError,
    JobLeaseBusyError,
    MAX_PIPELINE_DISPATCHES,
)


JOB_ID = "00000000-0000-0000-0000-000000000001"
OTHER_JOB_ID = "00000000-0000-0000-0000-000000000002"
ACCESS_CODE = "owner-judge-program-code"
ACCESS_HASH = hashlib.sha256(ACCESS_CODE.encode("utf-8")).hexdigest()


class FakeArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []

    def get_bytes(self, object_name: str) -> bytes:
        self.calls.append(object_name)
        return self.objects[object_name]


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.jobs: dict[str, dict[str, object]] = {}

    @staticmethod
    def _job(state: str, *, stage: str) -> dict[str, object]:
        return {
            "schema": "video-studio.all-things-agentic-job/v1",
            "job_id": JOB_ID,
            "state": state,
            "stage": stage,
            "progress": 0 if state == "queued" else 100,
            "eta": {
                "available": False,
                "low_seconds": None,
                "high_seconds": None,
                "sample_count": 0,
                "basis": "insufficient_completed_job_history",
            },
        }

    def submit(self, message: str) -> dict[str, object]:
        self.calls.append(("submit", message))
        return self._job("queued", stage="waiting_for_worker")

    def status(self, job_id: str) -> dict[str, object]:
        self.calls.append(("status", job_id))
        if job_id in self.jobs:
            return self.jobs[job_id]
        return self._job("queued", stage="waiting_for_worker")

    def cancel(self, job_id: str) -> dict[str, object]:
        self.calls.append(("cancel", job_id))
        return self._job("cancelled", stage="cancelled_before_worker_start")

    def retry(self, job_id: str) -> dict[str, object]:
        self.calls.append(("retry", job_id))
        return self._job("queued", stage="waiting_for_worker")

    def execute(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int,
    ) -> dict[str, object]:
        self.calls.append(("execute", job_id))
        return self._job("succeeded", stage="complete")


class FakeRuntime:
    def __init__(self, role: str, *, access_hash: str = ACCESS_HASH) -> None:
        self.role = role
        self.demo_access_sha256 = access_hash if role == "api" else ""
        self.service = FakeService()
        self.artifact_store = FakeArtifactStore()

    def health(self) -> dict[str, object]:
        return {
            "ok": True,
            "service": "video-studio-all-things-agentic",
            "role": self.role,
            "live_provider_call_proven": False,
        }


class RunningServer:
    def __init__(self, role: str, *, access_hash: str = ACCESS_HASH) -> None:
        self.runtime = FakeRuntime(role, access_hash=access_hash)
        self.server = Server(("127.0.0.1", 0), self.runtime)  # type: ignore[arg-type]
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def request_json(
    running: RunningServer,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    access_code: str | None = ACCESS_CODE,
) -> tuple[int, dict[str, object], object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if access_code is not None:
        headers["X-Video-Studio-Access"] = access_code
    request = Request(running.base_url + path, data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=2)
    except HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        decoded = json.loads(body.decode("utf-8"))
        return int(response.status), dict(response.headers), decoded


def request_bytes(
    running: RunningServer,
    path: str,
    *,
    access_code: str | None = ACCESS_CODE,
) -> tuple[int, dict[str, str], bytes]:
    headers: dict[str, str] = {}
    if access_code is not None:
        headers["X-Video-Studio-Access"] = access_code
    request = Request(running.base_url + path, headers=headers, method="GET")
    try:
        response = urlopen(request, timeout=2)
    except HTTPError as exc:
        response = exc
    with response:
        return int(response.status), dict(response.headers), response.read()


def artifact_descriptor(
    job_id: str,
    artifact_id: str,
    data: bytes,
    content_type: str,
) -> dict[str, object]:
    digest = hashlib.sha256(data).hexdigest()
    return {
        "artifact_id": artifact_id,
        "object_name": f"jobs/{job_id}/artifacts/{digest}/{artifact_id}",
        "sha256": digest,
        "bytes": len(data),
        "content_type": content_type,
    }


def completed_artifact_job(
    job_id: str,
    *,
    visual: dict[str, object] | None = None,
    pitch: dict[str, object] | None = None,
) -> dict[str, object]:
    job: dict[str, object] = {
        "schema": "video-studio.all-things-agentic-job/v1",
        "job_id": job_id,
        "state": "succeeded",
        "stage": "production_plan_and_pitch_ready",
        "progress": 100,
        "visual_storyboard": {
            "status": "complete",
            "representation": "private_artifact_route",
            "panels": [],
        },
        "pitch_preview": None,
    }
    if visual is not None:
        job["visual_storyboard"] = {
            "status": "complete",
            "representation": "private_artifact_route",
            "panels": [
                {
                    "status": "available",
                    "artifact_id": visual["artifact_id"],
                    "object_name": visual["object_name"],
                    "mime_type": visual["content_type"],
                    "content_sha256": visual["sha256"],
                    "byte_length": visual["bytes"],
                }
            ],
        }
    if pitch is not None:
        job["pitch_preview"] = {
            "status": "complete",
            "video": pitch,
        }
    return job


class AllThingsCloudAppTests(unittest.TestCase):
    def test_http_envelope_accepts_full_unicode_script_request_and_rejects_oversize(self) -> None:
        running = RunningServer("api")
        try:
            message = "😀" * 159_000
            status, _, queued = request_json(
                running,
                "/v1/jobs",
                method="POST",
                payload={"message": message},
            )
            self.assertEqual(status, HTTPStatus.ACCEPTED)
            self.assertEqual(queued["state"], "queued")
            self.assertEqual(running.runtime.service.calls[-1], ("submit", message))

            # Declare an oversized envelope without transmitting hundreds of
            # kilobytes after the server has already rejected its length. This
            # avoids a Windows TCP reset race while testing the same fail-fast
            # Content-Length boundary used in production.
            host, port = running.server.server_address
            connection = HTTPConnection(host, port, timeout=2)
            try:
                connection.request(
                    "POST",
                    "/v1/jobs",
                    body=b"",
                    headers={
                        "Content-Length": str(MAX_BODY_BYTES + 1),
                        "Content-Type": "application/json",
                        "X-Video-Studio-Access": ACCESS_CODE,
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(payload["error"], "request body is too large")
            finally:
                connection.close()
        finally:
            running.close()

    def test_worker_runtime_injects_configured_brief_and_visual_google_providers(self) -> None:
        repository = object()
        brief_provider = object()
        visual_provider = object()
        artifact_store = object()
        pitch_renderer = object()
        environment = {
            "KIRA_ALL_THINGS_SERVICE_ROLE": "worker",
            "GOOGLE_CLOUD_PROJECT": "video-studio-12345",
            "GOOGLE_CLOUD_LOCATION": "global",
            "KIRA_ALL_THINGS_GEMINI_MODEL": "gemini-3.5-flash",
            "KIRA_ALL_THINGS_IMAGE_MODEL": "gemini-3.1-flash-image",
            "KIRA_ALL_THINGS_ARTIFACTS_BUCKET": "video-studio-private-artifacts",
            "KIRA_ALL_THINGS_TTS_VOICE": "en-US-Chirp3-HD-Aoede",
        }
        with (
            patch("all_things_cloud_app.FirestoreJobRepository", return_value=repository),
            patch("all_things_cloud_app.GoogleGenAIBriefProvider", return_value=brief_provider),
            patch(
                "all_things_cloud_app.GoogleGenAIVisualPanelProvider",
                return_value=visual_provider,
            ),
            patch(
                "all_things_cloud_app.GoogleCloudArtifactStore",
                return_value=artifact_store,
            ) as store_factory,
            patch(
                "all_things_cloud_app.GoogleCloudNarratedPitchRenderer",
                return_value=pitch_renderer,
            ) as renderer_factory,
        ):
            runtime = Runtime(environment)
        self.assertIs(runtime.service.provider, brief_provider)
        self.assertIs(runtime.service.visual_provider, visual_provider)
        self.assertIs(runtime.service.artifact_store, artifact_store)
        self.assertIs(runtime.service.narrated_pitch_renderer, pitch_renderer)
        self.assertIs(runtime.artifact_store, artifact_store)
        store_factory.assert_called_once_with("video-studio-private-artifacts")
        renderer_factory.assert_called_once_with(
            artifact_store,
            voice_name="en-US-Chirp3-HD-Aoede",
        )
        self.assertEqual(runtime.config.image_model, "gemini-3.1-flash-image")
        self.assertTrue(runtime.health()["visual_storyboard_configured"])
        self.assertTrue(runtime.health()["private_artifacts_configured"])
        self.assertTrue(runtime.health()["narrated_pitch_configured"])

    def test_runtime_requires_private_artifact_bucket_for_both_roles(self) -> None:
        worker = {
            "KIRA_ALL_THINGS_SERVICE_ROLE": "worker",
            "GOOGLE_CLOUD_PROJECT": "video-studio-12345",
            "GOOGLE_CLOUD_LOCATION": "global",
            "KIRA_ALL_THINGS_GEMINI_MODEL": "gemini-3.5-flash",
            "KIRA_ALL_THINGS_IMAGE_MODEL": "gemini-3.1-flash-image",
        }
        with self.assertRaisesRegex(
            ConfigurationError, "KIRA_ALL_THINGS_ARTIFACTS_BUCKET is required"
        ):
            Runtime(worker)

        api = {
            "KIRA_ALL_THINGS_SERVICE_ROLE": "api",
            "KIRA_ALL_THINGS_DEMO_ACCESS_SHA256": ACCESS_HASH,
            "GOOGLE_CLOUD_PROJECT": "video-studio-12345",
            "GOOGLE_CLOUD_LOCATION": "global",
            "KIRA_ALL_THINGS_GEMINI_MODEL": "gemini-3.5-flash",
            "KIRA_ALL_THINGS_IMAGE_MODEL": "gemini-3.1-flash-image",
            "KIRA_ALL_THINGS_WORKER_URL": "https://worker.example.run.app",
            "KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT": (
                "worker@video-studio-12345.iam.gserviceaccount.com"
            ),
        }
        with self.assertRaisesRegex(
            ConfigurationError, "KIRA_ALL_THINGS_ARTIFACTS_BUCKET is required"
        ):
            Runtime(api)

    def test_api_runtime_fails_closed_when_access_digest_is_missing_or_invalid(self) -> None:
        with self.assertRaises(ConfigurationError):
            Runtime({"KIRA_ALL_THINGS_SERVICE_ROLE": "api"})
        with self.assertRaises(ConfigurationError):
            Runtime(
                {
                    "KIRA_ALL_THINGS_SERVICE_ROLE": "api",
                    "KIRA_ALL_THINGS_DEMO_ACCESS_SHA256": "not-a-sha256-digest",
                }
            )

    def test_job_routes_require_constant_digest_gate_and_emit_no_cors(self) -> None:
        running = RunningServer("api")
        try:
            status, headers, missing = request_json(
                running,
                "/v1/jobs",
                method="POST",
                payload={"message": "Make a scene."},
                access_code=None,
            )
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertNotIn("Access-Control-Allow-Origin", headers)

            status, _, wrong = request_json(
                running,
                f"/v1/jobs/{JOB_ID}",
                access_code="wrong-code",
            )
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(wrong, missing)

            status, headers, queued = request_json(
                running,
                "/v1/jobs",
                method="POST",
                payload={"message": "Make a scene."},
                access_code=ACCESS_CODE,
            )
            self.assertEqual(status, HTTPStatus.ACCEPTED)
            self.assertEqual(queued["state"], "queued")
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertNotIn(ACCESS_CODE, json.dumps(queued))
        finally:
            running.close()

    def test_private_visual_artifact_requires_access_and_returns_verified_bytes(self) -> None:
        running = RunningServer("api")
        try:
            data = b"\xff\xd8\xff\xe0private-storyboard-panel\xff\xd9"
            descriptor = artifact_descriptor(
                JOB_ID,
                "panel-001.jpg",
                data,
                "image/jpeg",
            )
            running.runtime.service.jobs[JOB_ID] = completed_artifact_job(
                JOB_ID, visual=descriptor
            )
            object_name = str(descriptor["object_name"])
            running.runtime.artifact_store.objects[object_name] = data
            path = f"/v1/jobs/{JOB_ID}/artifacts/panel-001.jpg"

            status, _, denied = request_json(running, path, access_code=None)
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(denied["error"], "valid program access code required")
            status, _, denied = request_json(running, path, access_code="wrong-code")
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(denied["error"], "valid program access code required")
            self.assertEqual(running.runtime.artifact_store.calls, [])

            status, headers, body = request_bytes(running, path)
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(body, data)
            self.assertEqual(headers["Content-Type"], "image/jpeg")
            self.assertEqual(int(headers["Content-Length"]), len(data))
            self.assertEqual(headers["Cache-Control"], "private, no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(running.runtime.artifact_store.calls, [object_name])

            exposed = json.dumps(headers).casefold()
            self.assertNotIn(object_name.casefold(), exposed)
            self.assertNotIn("gs://", exposed)
            self.assertNotIn("storage.googleapis.com", exposed)
            self.assertNotIn("location", {name.casefold() for name in headers})
        finally:
            running.close()

    def test_private_pitch_video_is_served_only_from_complete_job_manifest(self) -> None:
        running = RunningServer("api")
        try:
            data = b"\x00\x00\x00\x18ftypmp42verified-private-pitch"
            descriptor = artifact_descriptor(
                JOB_ID,
                "narrated-pitch.mp4",
                data,
                "video/mp4",
            )
            running.runtime.service.jobs[JOB_ID] = completed_artifact_job(
                JOB_ID, pitch=descriptor
            )
            object_name = str(descriptor["object_name"])
            running.runtime.artifact_store.objects[object_name] = data

            status, headers, body = request_bytes(
                running,
                f"/v1/jobs/{JOB_ID}/artifacts/narrated-pitch.mp4",
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(body, data)
            self.assertEqual(headers["Content-Type"], "video/mp4")
            self.assertEqual(int(headers["Content-Length"]), len(data))
            self.assertEqual(running.runtime.artifact_store.calls, [object_name])
        finally:
            running.close()

    def test_private_artifact_route_rejects_unknown_cross_job_and_duplicate_ids(self) -> None:
        running = RunningServer("api")
        try:
            data = b"private-video"
            other_descriptor = artifact_descriptor(
                OTHER_JOB_ID,
                "narrated-pitch.mp4",
                data,
                "video/mp4",
            )
            running.runtime.service.jobs[JOB_ID] = completed_artifact_job(JOB_ID)
            running.runtime.service.jobs[OTHER_JOB_ID] = completed_artifact_job(
                OTHER_JOB_ID, pitch=other_descriptor
            )
            other_object = str(other_descriptor["object_name"])
            running.runtime.artifact_store.objects[other_object] = data

            for job_id, artifact_id in (
                (JOB_ID, "unknown.mp4"),
                (JOB_ID, "narrated-pitch.mp4"),
                (OTHER_JOB_ID, "unknown.mp4"),
            ):
                with self.subTest(job_id=job_id, artifact_id=artifact_id):
                    status, _, payload = request_json(
                        running,
                        f"/v1/jobs/{job_id}/artifacts/{artifact_id}",
                    )
                    self.assertEqual(status, HTTPStatus.NOT_FOUND)
                    self.assertEqual(payload["error"], "artifact not found")
            self.assertEqual(running.runtime.artifact_store.calls, [])

            # An identifier appearing twice is ambiguous and therefore cannot
            # be used to select either object.
            duplicate_job = completed_artifact_job(
                JOB_ID,
                visual={
                    **other_descriptor,
                    "object_name": str(other_descriptor["object_name"]).replace(
                        OTHER_JOB_ID, JOB_ID
                    ),
                },
                pitch={
                    **other_descriptor,
                    "object_name": str(other_descriptor["object_name"]).replace(
                        OTHER_JOB_ID, JOB_ID
                    ),
                },
            )
            duplicate_job["job_id"] = JOB_ID
            running.runtime.service.jobs[JOB_ID] = duplicate_job
            status, _, payload = request_json(
                running,
                f"/v1/jobs/{JOB_ID}/artifacts/narrated-pitch.mp4",
            )
            self.assertEqual(status, HTTPStatus.NOT_FOUND)
            self.assertEqual(payload["error"], "artifact not found")
            self.assertEqual(running.runtime.artifact_store.calls, [])
        finally:
            running.close()

    def test_private_artifact_route_rejects_nonterminal_or_incomplete_manifests(self) -> None:
        running = RunningServer("api")
        try:
            data = b"private-panel"
            descriptor = artifact_descriptor(
                JOB_ID,
                "panel-001.jpg",
                data,
                "image/jpeg",
            )
            complete = completed_artifact_job(JOB_ID, visual=descriptor)
            cases: list[tuple[str, dict[str, object]]] = []

            running_job = dict(complete)
            running_job["state"] = "running"
            cases.append(("nonterminal job", running_job))

            partial_visual = dict(complete)
            partial_visual["visual_storyboard"] = dict(
                partial_visual["visual_storyboard"]  # type: ignore[arg-type]
            )
            partial_visual["visual_storyboard"]["status"] = "partial"  # type: ignore[index]
            cases.append(("partial visual storyboard", partial_visual))

            inline_visual = dict(complete)
            inline_visual["visual_storyboard"] = dict(
                inline_visual["visual_storyboard"]  # type: ignore[arg-type]
            )
            inline_visual["visual_storyboard"]["representation"] = "inline_base64"  # type: ignore[index]
            cases.append(("non-private visual storyboard", inline_visual))

            pitch_descriptor = artifact_descriptor(
                JOB_ID,
                "narrated-pitch.mp4",
                b"pitch",
                "video/mp4",
            )
            partial_pitch = completed_artifact_job(JOB_ID, pitch=pitch_descriptor)
            partial_pitch["pitch_preview"] = dict(
                partial_pitch["pitch_preview"]  # type: ignore[arg-type]
            )
            partial_pitch["pitch_preview"]["status"] = "partial"  # type: ignore[index]
            cases.append(("partial pitch manifest", partial_pitch))

            for label, job in cases:
                with self.subTest(label=label):
                    running.runtime.service.jobs[JOB_ID] = job
                    artifact_id = (
                        "narrated-pitch.mp4" if "pitch" in label else "panel-001.jpg"
                    )
                    status, _, payload = request_json(
                        running,
                        f"/v1/jobs/{JOB_ID}/artifacts/{artifact_id}",
                    )
                    self.assertEqual(status, HTTPStatus.NOT_FOUND)
                    self.assertEqual(payload["error"], "artifact not found")
            self.assertEqual(running.runtime.artifact_store.calls, [])
        finally:
            running.close()

    def test_private_artifact_route_fails_closed_on_manifest_or_byte_tampering(self) -> None:
        running = RunningServer("api")
        try:
            expected = b"expected-private-panel"
            descriptor = artifact_descriptor(
                JOB_ID,
                "panel-001.jpg",
                expected,
                "image/jpeg",
            )
            object_name = str(descriptor["object_name"])
            path = f"/v1/jobs/{JOB_ID}/artifacts/panel-001.jpg"

            for label, stored in (
                ("length mismatch", expected + b"!"),
                ("hash mismatch", b"X" * len(expected)),
            ):
                with self.subTest(label=label):
                    running.runtime.service.jobs[JOB_ID] = completed_artifact_job(
                        JOB_ID, visual=descriptor
                    )
                    running.runtime.artifact_store.objects[object_name] = stored
                    status, _, payload = request_json(running, path)
                    self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
                    self.assertEqual(payload["error_type"], "ConfigurationError")
                    self.assertNotIn(object_name, json.dumps(payload))

            malformed = dict(descriptor)
            malformed["object_name"] = "gs://public-looking-bucket/panel-001.jpg"
            running.runtime.service.jobs[JOB_ID] = completed_artifact_job(
                JOB_ID, visual=malformed
            )
            calls_before = list(running.runtime.artifact_store.calls)
            status, _, payload = request_json(running, path)
            self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
            self.assertEqual(payload["error_type"], "ConfigurationError")
            self.assertNotIn("gs://", json.dumps(payload))
            self.assertEqual(running.runtime.artifact_store.calls, calls_before)
        finally:
            running.close()

    def test_api_serves_chat_and_complete_job_control_surface(self) -> None:
        running = RunningServer("api")
        try:
            with urlopen(running.base_url + "/", timeout=2) as response:
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, HTTPStatus.OK)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn("Video Studio Storyboard Artist &amp; Production Planner", html)
            self.assertIn('type="password"', html)
            self.assertIn("/v1/jobs", html)

            status, headers, health = request_json(running, "/health")
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(health["role"], "api")
            self.assertFalse(health["live_provider_call_proven"])

            status, _, queued = request_json(
                running,
                "/v1/jobs",
                method="POST",
                payload={"message": "Make a one-minute dialogue scene."},
            )
            self.assertEqual(status, HTTPStatus.ACCEPTED)
            self.assertEqual(queued["state"], "queued")

            status, _, job = request_json(running, f"/v1/jobs/{JOB_ID}")
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(job["job_id"], JOB_ID)

            status, _, cancelled = request_json(
                running,
                f"/v1/jobs/{JOB_ID}:cancel",
                method="POST",
                payload={},
            )
            self.assertEqual(status, HTTPStatus.ACCEPTED)
            self.assertEqual(cancelled["state"], "cancelled")

            status, _, retried = request_json(
                running,
                f"/v1/jobs/{JOB_ID}:retry",
                method="POST",
                payload={},
            )
            self.assertEqual(status, HTTPStatus.ACCEPTED)
            self.assertEqual(retried["state"], "queued")
        finally:
            running.close()

    def test_api_serves_installable_shell_and_local_pdf_extractor_assets(self) -> None:
        running = RunningServer("api")
        try:
            expectations = {
                "/manifest.webmanifest": ("application/manifest+json", b'"display": "standalone"'),
                "/sw.js": ("text/javascript", b'const SHELL_CACHE = "video-studio-shell-v2"'),
                "/icons/video-studio-icon-192.svg": ("image/svg+xml", b"<svg"),
                "/icons/video-studio-icon-192.png": ("image/png", b"\x89PNG\r\n\x1a\n"),
                "/icons/video-studio-icon-512.png": ("image/png", b"\x89PNG\r\n\x1a\n"),
                "/vendor/pdfjs/pdf.mjs": ("text/javascript", b"globalThis.pdfjsLib"),
                "/vendor/pdfjs/pdf.worker.mjs": ("text/javascript", b"pdfjsVersion"),
                "/vendor/pdfjs/LICENSE": ("text/plain", b"Apache License"),
            }
            for path, (content_type, marker) in expectations.items():
                with self.subTest(path=path):
                    with urlopen(running.base_url + path, timeout=3) as response:
                        body = response.read()
                        self.assertEqual(response.status, HTTPStatus.OK)
                        self.assertIn(content_type, response.headers["Content-Type"])
                        self.assertIn(marker, body)
                        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            with urlopen(running.base_url + "/sw.js", timeout=3) as response:
                self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
            with urlopen(running.base_url + "/manifest.webmanifest", timeout=3) as response:
                manifest = json.loads(response.read().decode("utf-8"))
                self.assertEqual(manifest["display"], "standalone")
                self.assertEqual(
                    {icon["src"] for icon in manifest["icons"]},
                    {
                        "/icons/video-studio-icon-192.png",
                        "/icons/video-studio-icon-512.png",
                    },
                )
        finally:
            running.close()

    def test_api_rejects_extra_fields_and_cannot_execute_worker_route(self) -> None:
        running = RunningServer("api")
        try:
            status, _, error = request_json(
                running,
                "/v1/jobs",
                method="POST",
                payload={"message": "Make a scene.", "unreviewed": True},
            )
            self.assertEqual(status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(error["error_type"], "AllThingsError")

            status, _, error = request_json(
                running,
                f"/internal/v1/jobs/{JOB_ID}:run",
                method="POST",
                payload={"job_id": JOB_ID},
            )
            self.assertEqual(status, HTTPStatus.NOT_FOUND)
            self.assertEqual(error["error"], "route not found")
        finally:
            running.close()

    def test_worker_requires_exact_task_binding(self) -> None:
        running = RunningServer("worker")
        try:
            path = f"/internal/v1/jobs/{JOB_ID}:run"
            status, _, error = request_json(
                running,
                path,
                method="POST",
                payload={
                    "job_id": "00000000-0000-0000-0000-000000000002",
                    "attempt": 1,
                    "dispatch_sequence": 0,
                },
            )
            self.assertEqual(status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(error["error_type"], "AllThingsError")

            status, _, completed = request_json(
                running,
                path,
                method="POST",
                payload={"job_id": JOB_ID, "attempt": 1, "dispatch_sequence": 0},
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(completed["state"], "succeeded")
            self.assertEqual(running.runtime.service.calls, [("execute", JOB_ID)])
        finally:
            running.close()

    def test_worker_uses_shared_dispatch_sequence_bound(self) -> None:
        running = RunningServer("worker")
        try:
            path = f"/internal/v1/jobs/{JOB_ID}:run"
            status, _, completed = request_json(
                running,
                path,
                method="POST",
                payload={
                    "job_id": JOB_ID,
                    "attempt": 1,
                    "dispatch_sequence": MAX_PIPELINE_DISPATCHES - 1,
                },
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(completed["state"], "succeeded")

            for invalid in (True, MAX_PIPELINE_DISPATCHES):
                with self.subTest(dispatch_sequence=invalid):
                    status, _, error = request_json(
                        running,
                        path,
                        method="POST",
                        payload={
                            "job_id": JOB_ID,
                            "attempt": 1,
                            "dispatch_sequence": invalid,
                        },
                    )
                    self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                    self.assertEqual(error["error_type"], "AllThingsError")
        finally:
            running.close()

    def test_admission_and_active_lease_errors_are_retryable_non_successes(self) -> None:
        api = RunningServer("api")
        try:
            def limited(_message: str) -> dict[str, object]:
                raise AdmissionLimitError("shared program job admission limit reached", retry_after_seconds=7)

            api.runtime.service.submit = limited  # type: ignore[method-assign]
            status, headers, error = request_json(
                api,
                "/v1/jobs",
                method="POST",
                payload={"message": "One too many."},
            )
            self.assertEqual(status, HTTPStatus.TOO_MANY_REQUESTS)
            self.assertEqual(headers["Retry-After"], "7")
            self.assertEqual(error["retry_after_seconds"], 7)
        finally:
            api.close()

        worker = RunningServer("worker")
        try:
            def busy(
                _job_id: str,
                *,
                attempt: int,
                dispatch_sequence: int,
            ) -> dict[str, object]:
                raise JobLeaseBusyError("worker lease is active", retry_after_seconds=11)

            worker.runtime.service.execute = busy  # type: ignore[method-assign]
            status, headers, error = request_json(
                worker,
                f"/internal/v1/jobs/{JOB_ID}:run",
                method="POST",
                payload={"job_id": JOB_ID, "attempt": 1, "dispatch_sequence": 0},
            )
            self.assertEqual(status, HTTPStatus.CONFLICT)
            self.assertEqual(headers["Retry-After"], "11")
            self.assertEqual(error["retry_after_seconds"], 11)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
