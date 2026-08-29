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

from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    AllThingsConfig,
    AllThingsError,
    AllThingsJobService,
    ConfigurationError,
    JobLeaseBusyError,
    JobNotFoundError,
)
from kira_studio.all_things_google import (
    CloudTasksDispatcher,
    FirestoreJobRepository,
    GoogleGenAIBriefProvider,
)


MAX_BODY_BYTES = 64 * 1024
_JOB_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36})$")
_CANCEL_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36}):cancel$")
_RETRY_PATH = re.compile(r"^/v1/jobs/(?P<job_id>[0-9a-f-]{36}):retry$")
_RUN_PATH = re.compile(r"^/internal/v1/jobs/(?P<job_id>[0-9a-f-]{36}):run$")
_ACCESS_HASH = re.compile(r"^[0-9a-f]{64}$")
_DEMO_PATH = Path(__file__).resolve().parent / "web" / "all-things-agentic.html"


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
        config.assert_valid(require_dispatch=role == "api")
        repository = FirestoreJobRepository(config)
        self.role = role
        # This is a one-way digest, never the owner/judge access code.
        self.demo_access_sha256 = access_hash
        self.config = config
        self.service = AllThingsJobService(
            config=config,
            repository=repository,
            dispatcher=CloudTasksDispatcher(config) if role == "api" else None,
            provider=GoogleGenAIBriefProvider(config) if role == "worker" else None,
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "video-studio-all-things-agentic",
            "role": self.role,
            "target": self.config.safe_dict(),
            "demo_access_required": self.role == "api",
            "live_provider_call_proven": False,
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
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            if self.path == "/healthz":
                self._json(HTTPStatus.OK, self.runtime.health())
                return
            if self.path == "/" and self.runtime.role == "api":
                self._html(HTTPStatus.OK, _DEMO_PATH.read_bytes())
                return
            match = _JOB_PATH.fullmatch(self.path)
            if match and self.runtime.role == "api":
                if not self._require_demo_access():
                    return
                self._json(HTTPStatus.OK, self.runtime.service.status(match.group("job_id")))
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
                    set(payload) != {"job_id", "attempt"}
                    or payload["job_id"] != run.group("job_id")
                    or isinstance(payload["attempt"], bool)
                    or not isinstance(payload["attempt"], int)
                    or payload["attempt"] < 1
                ):
                    raise AllThingsError("Cloud Tasks job binding is invalid")
                self._json(
                    HTTPStatus.OK,
                    self.runtime.service.execute(
                        run.group("job_id"), attempt=payload["attempt"]
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
