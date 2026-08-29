from __future__ import annotations

from http import HTTPStatus
from threading import Thread
import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from all_things_cloud_app import Runtime, Server
from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    ConfigurationError,
    JobLeaseBusyError,
)


JOB_ID = "00000000-0000-0000-0000-000000000001"
ACCESS_CODE = "owner-judge-demo-code"
ACCESS_HASH = hashlib.sha256(ACCESS_CODE.encode("utf-8")).hexdigest()


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

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
        return self._job("queued", stage="waiting_for_worker")

    def cancel(self, job_id: str) -> dict[str, object]:
        self.calls.append(("cancel", job_id))
        return self._job("cancelled", stage="cancelled_before_worker_start")

    def retry(self, job_id: str) -> dict[str, object]:
        self.calls.append(("retry", job_id))
        return self._job("queued", stage="waiting_for_worker")

    def execute(self, job_id: str, *, attempt: int) -> dict[str, object]:
        self.calls.append(("execute", job_id))
        return self._job("succeeded", stage="complete")


class FakeRuntime:
    def __init__(self, role: str, *, access_hash: str = ACCESS_HASH) -> None:
        self.role = role
        self.demo_access_sha256 = access_hash if role == "api" else ""
        self.service = FakeService()

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
        data = json.dumps(payload).encode("utf-8")
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


class AllThingsCloudAppTests(unittest.TestCase):
    def test_worker_runtime_injects_configured_brief_and_visual_google_providers(self) -> None:
        repository = object()
        brief_provider = object()
        visual_provider = object()
        environment = {
            "KIRA_ALL_THINGS_SERVICE_ROLE": "worker",
            "GOOGLE_CLOUD_PROJECT": "video-studio-12345",
            "GOOGLE_CLOUD_LOCATION": "global",
            "KIRA_ALL_THINGS_GEMINI_MODEL": "gemini-3.5-flash",
            "KIRA_ALL_THINGS_IMAGE_MODEL": "gemini-3.1-flash-image",
        }
        with (
            patch("all_things_cloud_app.FirestoreJobRepository", return_value=repository),
            patch("all_things_cloud_app.GoogleGenAIBriefProvider", return_value=brief_provider),
            patch(
                "all_things_cloud_app.GoogleGenAIVisualPanelProvider",
                return_value=visual_provider,
            ),
        ):
            runtime = Runtime(environment)
        self.assertIs(runtime.service.provider, brief_provider)
        self.assertIs(runtime.service.visual_provider, visual_provider)
        self.assertEqual(runtime.config.image_model, "gemini-3.1-flash-image")
        self.assertTrue(runtime.health()["visual_storyboard_configured"])

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

    def test_api_serves_chat_and_complete_job_control_surface(self) -> None:
        running = RunningServer("api")
        try:
            with urlopen(running.base_url + "/", timeout=2) as response:
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, HTTPStatus.OK)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn("Video Studio Production Director", html)
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
                },
            )
            self.assertEqual(status, HTTPStatus.BAD_REQUEST)
            self.assertEqual(error["error_type"], "AllThingsError")

            status, _, completed = request_json(
                running,
                path,
                method="POST",
                payload={"job_id": JOB_ID, "attempt": 1},
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(completed["state"], "succeeded")
            self.assertEqual(running.runtime.service.calls, [("execute", JOB_ID)])
        finally:
            running.close()

    def test_admission_and_active_lease_errors_are_retryable_non_successes(self) -> None:
        api = RunningServer("api")
        try:
            def limited(_message: str) -> dict[str, object]:
                raise AdmissionLimitError("shared demo job admission limit reached", retry_after_seconds=7)

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
            def busy(_job_id: str, *, attempt: int) -> dict[str, object]:
                raise JobLeaseBusyError("worker lease is active", retry_after_seconds=11)

            worker.runtime.service.execute = busy  # type: ignore[method-assign]
            status, headers, error = request_json(
                worker,
                f"/internal/v1/jobs/{JOB_ID}:run",
                method="POST",
                payload={"job_id": JOB_ID, "attempt": 1},
            )
            self.assertEqual(status, HTTPStatus.CONFLICT)
            self.assertEqual(headers["Retry-After"], "11")
            self.assertEqual(error["retry_after_seconds"], 11)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
