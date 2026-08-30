"""Cloud Run HTTP entry point for the All Things Agentic workflow.

Deploy this source twice with ``KIRA_ALL_THINGS_SERVICE_ROLE`` set to ``api``
or ``worker``.  The API service is the user-facing chat/job surface.  Cloud
Tasks invokes the private worker service with OIDC and Cloud Run IAM performs
authentication before this handler runs.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    AllThingsConfig,
    AllThingsError,
    AllThingsJobService,
    ConfigurationError,
    JobDispatchPendingError,
    JobLeaseBusyError,
    JobNotFoundError,
    MAX_PIPELINE_DISPATCHES,
)
from kira_studio.all_things_google import (
    CloudTasksDispatcher,
    FirestoreJobRepository,
    GoogleGenAIBriefProvider,
    GoogleGenAIVisualPanelProvider,
)
from kira_studio.all_things_cloud_media import (
    GoogleCloudArtifactStore,
    GoogleCloudNarratedPitchRenderer,
)


# Holds the 160k-character screenplay envelope plus bounded clarification and
# source metadata while rejecting unexpectedly large request bodies.
MAX_BODY_BYTES = 700 * 1024
_JOB_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36})$")
_CANCEL_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36}):cancel$")
_RETRY_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36}):retry$")
_RUN_PATH = re.compile(r"^/internal/v1/jobs/(?P<job_id>[0-9a-f-]{36}):run$")
_ARTIFACT_PATH = re.compile(
    r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36})/artifacts/"
    r"(?P<artifact_id>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_ACCESS_HASH = re.compile(r"^[0-9a-f]{64}$")
_DEMO_PATH = Path(__file__).resolve().parent / "web" / "all-things-agentic.html"
_WEB_PATH = _DEMO_PATH.parent
_PUBLIC_ASSETS: dict[str, tuple[Path, str, str]] = {
    "/manifest.webmanifest": (
        _WEB_PATH / "manifest.webmanifest",
        "application/manifest+json; charset=utf-8",
        "public, max-age=300",
    ),
    "/sw.js": (
        _WEB_PATH / "sw.js",
        "text/javascript; charset=utf-8",
        "no-cache",
    ),
    "/icons/video-studio-icon-192.svg": (
        _WEB_PATH / "video-studio-icon-192.svg",
        "image/svg+xml; charset=utf-8",
        "public, max-age=86400, immutable",
    ),
    "/icons/video-studio-icon-512.svg": (
        _WEB_PATH / "video-studio-icon-512.svg",
        "image/svg+xml; charset=utf-8",
        "public, max-age=86400, immutable",
    ),
    "/icons/video-studio-icon-192.png": (
        _WEB_PATH / "video-studio-icon-192.png",
        "image/png",
        "public, max-age=86400, immutable",
    ),
    "/icons/video-studio-icon-512.png": (
        _WEB_PATH / "video-studio-icon-512.png",
        "image/png",
        "public, max-age=86400, immutable",
    ),
    "/vendor/pdfjs/pdf.mjs": (
        _WEB_PATH / "vendor" / "pdfjs" / "pdf.mjs",
        "text/javascript; charset=utf-8",
        "public, max-age=86400, immutable",
    ),
    "/vendor/pdfjs/pdf.worker.mjs": (
        _WEB_PATH / "vendor" / "pdfjs" / "pdf.worker.mjs",
        "text/javascript; charset=utf-8",
        "public, max-age=86400, immutable",
    ),
    "/vendor/pdfjs/LICENSE": (
        _WEB_PATH / "vendor" / "pdfjs" / "LICENSE",
        "text/plain; charset=utf-8",
        "public, max-age=86400, immutable",
    ),
}


class Runtime:
    def __init__(self, environment: Mapping[str, str]) -> None:
        role = environment.get("KIRA_ALL_THINGS_SERVICE_ROLE", "").strip().casefold()
        if role not in {"api", "worker"}:
            raise ConfigurationError("KIRA_ALL_THINGS_SERVICE_ROLE must be api or worker")
        access_hash = environment.get("KIRA_ALL_THINGS_DEMO_ACCESS_SHA256", "").strip().casefold()
        if role == "api" and not _ACCESS_HASH.fullmatch(access_hash):
            raise ConfigurationError(
                "KIRA_ALL_THINGS_DEMO_ACCESS_SHA256 must be a lowercase SHA-256 hex digest"
            )
        config = AllThingsConfig.from_environment(environment)
        # A worker also dispatches bounded continuation tasks.  Keep startup
        # compatible with a bootstrap deployment, but surface the missing
        # continuation wiring and fail closed if a long job reaches that path.
        config.assert_valid(require_dispatch=role == "api")
        if not config.artifacts_bucket:
            raise ConfigurationError("KIRA_ALL_THINGS_ARTIFACTS_BUCKET is required")
        repository = FirestoreJobRepository(config)
        artifact_store = GoogleCloudArtifactStore(config.artifacts_bucket)
        self.role = role
        # This is a one-way digest, never the owner/judge access code.
        self.demo_access_sha256 = access_hash
        self.config = config
        self.artifact_store = artifact_store
        continuation_dispatch_configured = not config.issues(require_dispatch=True)
        self.continuation_dispatch_configured = continuation_dispatch_configured
        self.service = AllThingsJobService(
            config=config,
            repository=repository,
            dispatcher=(
                CloudTasksDispatcher(config)
                if role == "api" or continuation_dispatch_configured
                else None
            ),
            provider=GoogleGenAIBriefProvider(config) if role == "worker" else None,
            visual_provider=(
                GoogleGenAIVisualPanelProvider(config) if role == "worker" else None
            ),
            artifact_store=artifact_store,
            narrated_pitch_renderer=(
                GoogleCloudNarratedPitchRenderer(
                    artifact_store,
                    voice_name=config.tts_voice,
                )
                if role == "worker"
                else None
            ),
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "video-studio-all-things-agentic",
            "role": self.role,
            "target": self.config.safe_dict(),
            "demo_access_required": self.role == "api",
            "live_provider_call_proven": False,
            "visual_storyboard_configured": self.role == "worker",
            "private_artifacts_configured": bool(self.config.artifacts_bucket),
            "narrated_pitch_configured": self.role == "worker",
            "continuation_dispatch_configured": self.continuation_dispatch_configured,
            "note": "Health verifies configuration only; completed jobs carry live provider evidence.",
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoStudioAllThings/1"

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        # Cloud Run captures stdout/stderr. Avoid request bodies and credentials.
        super().log_message(format, *args)

    def _json(
        self,
        status: HTTPStatus | int,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: HTTPStatus | int, body: bytes) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _asset(
        self,
        status: HTTPStatus | int,
        body: bytes,
        *,
        content_type: str,
        cache_control: str,
        service_worker: bool = False,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if service_worker:
            self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Mapping[str, Any]:
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise AllThingsError("Content-Length is invalid") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise AllThingsError("request body is too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AllThingsError("request body must be UTF-8 JSON") from exc
        if not isinstance(payload, Mapping):
            raise AllThingsError("request body must be a JSON object")
        return payload

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, JobNotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, AdmissionLimitError):
            status = HTTPStatus.TOO_MANY_REQUESTS
        elif isinstance(exc, JobLeaseBusyError):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, JobDispatchPendingError):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        elif isinstance(exc, ConfigurationError):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        elif isinstance(exc, AllThingsError):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        message = str(exc) if isinstance(exc, AllThingsError) else "internal service error"
        payload: dict[str, Any] = {
            "ok": False,
            "error": message,
            "error_type": type(exc).__name__,
        }
        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after, int) and retry_after > 0:
            payload["retry_after_seconds"] = retry_after
            headers["Retry-After"] = str(retry_after)
        self._json(status, payload, headers=headers)

    def _require_demo_access(self) -> bool:
        """Authenticate an API job request without retaining plaintext access."""

        expected = getattr(self.runtime, "demo_access_sha256", "")
        provided = self.headers.get("X-Video-Studio-Access")
        if (
            not isinstance(expected, str)
            or not _ACCESS_HASH.fullmatch(expected)
            or not isinstance(provided, str)
            or not 1 <= len(provided) <= 256
        ):
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "valid demo access code required"},
            )
            return False
        supplied = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "valid demo access code required"},
            )
            return False
        return True

    @staticmethod
    def _artifact_descriptor(
        job: Mapping[str, Any], artifact_id: str
    ) -> dict[str, Any]:
        """Resolve only artifacts declared by this exact completed job.

        The browser never supplies a GCS object name.  It supplies a bounded
        artifact identifier, and this method binds that identifier to the
        immutable manifest already stored on the succeeded job.
        """

        if job.get("state") != "succeeded" or ".." in artifact_id:
            raise JobNotFoundError("artifact not found")
        candidates: list[dict[str, Any]] = []
        visuals = job.get("visual_storyboard")
        if (
            isinstance(visuals, Mapping)
            and visuals.get("status") == "complete"
            and visuals.get("representation") == "private_artifact_route"
        ):
            panels = visuals.get("panels")
            if isinstance(panels, list):
                for panel in panels:
                    if (
                        isinstance(panel, Mapping)
                        and panel.get("status") == "available"
                        and panel.get("artifact_id") == artifact_id
                    ):
                        candidates.append(
                            {
                                "artifact_id": artifact_id,
                                "object_name": panel.get("object_name"),
                                "content_type": panel.get("mime_type"),
                                "sha256": panel.get("content_sha256"),
                                "byte_length": panel.get("byte_length"),
                            }
                        )
        pitch = job.get("pitch_preview")
        if isinstance(pitch, Mapping) and pitch.get("status") == "complete":
            for key in ("video", "narration_text", "subtitles"):
                value = pitch.get(key)
                if isinstance(value, Mapping) and value.get("artifact_id") == artifact_id:
                    candidates.append(dict(value))
        if len(candidates) != 1:
            raise JobNotFoundError("artifact not found")
        descriptor = candidates[0]
        object_name = descriptor.get("object_name")
        content_type = descriptor.get("content_type")
        digest = descriptor.get("sha256")
        byte_length = descriptor.get("byte_length", descriptor.get("bytes"))
        if (
            descriptor.get("byte_length") is not None
            and descriptor.get("bytes") is not None
            and descriptor.get("byte_length") != descriptor.get("bytes")
        ):
            raise ConfigurationError("completed artifact manifest is invalid")
        if (
            not isinstance(object_name, str)
            or not object_name.startswith(f"jobs/{job.get('job_id')}/artifacts/")
            or not isinstance(content_type, str)
            or content_type
            not in {
                "image/jpeg",
                "video/mp4",
                "text/plain; charset=utf-8",
                "application/x-subrip",
            }
            or not isinstance(digest, str)
            or _ACCESS_HASH.fullmatch(digest) is None
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 1
        ):
            raise ConfigurationError("completed artifact manifest is invalid")
        descriptor["byte_length"] = byte_length
        return descriptor

    def _serve_private_artifact(self, job_id: str, artifact_id: str) -> None:
        job = self.runtime.service.status(job_id)
        descriptor = self._artifact_descriptor(job, artifact_id)
        data = self.runtime.artifact_store.get_bytes(descriptor["object_name"])
        if (
            len(data) != descriptor["byte_length"]
            or hashlib.sha256(data).hexdigest() != descriptor["sha256"]
        ):
            raise ConfigurationError("completed artifact bytes failed integrity validation")
        self._asset(
            HTTPStatus.OK,
            data,
            content_type=descriptor["content_type"],
            cache_control="private, no-store",
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            request_path = urlsplit(self.path).path
            if request_path == "/health":
                self._json(HTTPStatus.OK, self.runtime.health())
                return
            if request_path == "/" and self.runtime.role == "api":
                self._html(HTTPStatus.OK, _DEMO_PATH.read_bytes())
                return
            asset = _PUBLIC_ASSETS.get(request_path)
            if asset and self.runtime.role == "api":
                asset_path, content_type, cache_control = asset
                self._asset(
                    HTTPStatus.OK,
                    asset_path.read_bytes(),
                    content_type=content_type,
                    cache_control=cache_control,
                    service_worker=request_path == "/sw.js",
                )
                return
            match = _JOB_PATH.fullmatch(request_path)
            if match and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                self._json(HTTPStatus.OK, self.runtime.service.status(match.group("job_id")))
                return
            artifact = _ARTIFACT_PATH.fullmatch(request_path)
            if artifact and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                self._serve_private_artifact(
                    artifact.group("job_id"), artifact.group("artifact_id")
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "route not found"})
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            if self.path == "/v1/jobs" and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                payload = self._body()
                if set(payload) != {"message"}:
                    raise AllThingsError("job submission accepts only message")
                job = self.runtime.service.submit(payload["message"])
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if job.get("state") == "failed" and job.get("stage") == "dispatch_failed"
                    else HTTPStatus.ACCEPTED
                )
                self._json(status, job)
                return
            cancel = _CANCEL_PATH.fullmatch(self.path)
            if cancel and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                self._body_if_present()
                self._json(
                    HTTPStatus.ACCEPTED,
                    self.runtime.service.cancel(cancel.group("job_id")),
                )
                return
            retry = _RETRY_PATH.fullmatch(self.path)
            if retry and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                self._body_if_present()
                job = self.runtime.service.retry(retry.group("job_id"))
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if job.get("state") == "failed" and job.get("stage") == "dispatch_failed"
                    else HTTPStatus.ACCEPTED
                )
                self._json(status, job)
                return
            run = _RUN_PATH.fullmatch(self.path)
            if run and self.runtime.role == "worker":
                payload = self._body()
                if (
                    set(payload) != {"job_id", "attempt", "dispatch_sequence"}
                    or payload["job_id"] != run.group("job_id")
                    or isinstance(payload["attempt"], bool)
                    or not isinstance(payload["attempt"], int)
                    or payload["attempt"] < 1
                    or isinstance(payload["dispatch_sequence"], bool)
                    or not isinstance(payload["dispatch_sequence"], int)
                    or not 0 <= payload["dispatch_sequence"] < MAX_PIPELINE_DISPATCHES
                ):
                    raise AllThingsError("Cloud Tasks job binding is invalid")
                self._json(
                    HTTPStatus.OK,
                    self.runtime.service.execute(
                        run.group("job_id"),
                        attempt=payload["attempt"],
                        dispatch_sequence=payload["dispatch_sequence"],
                    ),
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "route not found"})
        except Exception as exc:
            self._error(exc)

    def _body_if_present(self) -> None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise AllThingsError("Content-Length is invalid") from exc
        if length:
            payload = self._body()
            if payload:
                raise AllThingsError("this action accepts no body fields")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], runtime: Runtime) -> None:
        super().__init__(address, Handler)
        self.runtime = runtime


def main() -> None:
    runtime = Runtime(os.environ)
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError as exc:
        raise ConfigurationError("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError("PORT must be from 1 to 65535")
    Server(("0.0.0.0", port), runtime).serve_forever()


if __name__ == "__main__":
    main()
