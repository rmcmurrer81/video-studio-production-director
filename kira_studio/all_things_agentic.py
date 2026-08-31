"""All Things Agentic production-brief workflow contracts.

This module contains no network client and starts no worker.  It defines the
validated natural-language request, structured production brief, durable job
lifecycle, honest ETA calculation, and the orchestration seam used by the
Google Cloud adapters in :mod:`kira_studio.all_things_google`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re
import statistics
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4


JOB_SCHEMA = "video-studio.all-things-agentic-job/v1"
BRIEF_SCHEMA = "video-studio.production-brief/v1"
STORYBOARD_PACKAGE_SCHEMA = "video-studio.storyboard-edit-package/v1"
STORYBOARD_TIMELINE_SCHEMA = "video-studio.planned-edit-timeline/v1"
STORYBOARD_AUDIT_SCHEMA = "video-studio.coverage-continuity-audit/v1"
VISUAL_STORYBOARD_SCHEMA = "video-studio.visual-storyboard/v1"
NARRATED_PITCH_SCHEMA = "video-studio.narrated-pitch/v1"
NARRATED_PITCH_SEGMENT_SCHEMA = "video-studio.narrated-pitch-segment/v1"
PIPELINE_CHECKPOINT_SCHEMA = "video-studio.pipeline-checkpoint/v1"
PIPELINE_CONTINUATION_SCHEMA = "video-studio.pipeline-continuation/v1"
PIPELINE_PENDING_DISPATCH_SCHEMA = "video-studio.pipeline-pending-dispatch/v1"
VISUAL_CAPACITY_SCHEMA = "video-studio.visual-request-capacity/v1"
VISUAL_CAPACITY_WINDOW_SCHEMA = "video-studio.visual-capacity-window/v1"
STORYBOARD_FRAME_RATE = 24
# Leaves headroom beneath Firestore's document limit for the separately exposed
# creative brief, bounded request text, durable state, and provider evidence.
MAX_STORYBOARD_PACKAGE_BYTES = 520_000
MAX_VISUAL_STORYBOARD_BYTES = 440_000
MAX_VISUAL_PANEL_BYTES = 45_000
MAX_VISUAL_PANEL_COUNT = 6
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
# One ordinary feature screenplay can be sent end-to-end.  The separate UTF-8
# byte ceiling prevents a 160k-code-point non-ASCII request from consuming a
# disproportionate Firestore document or HTTP request.  Successful jobs discard
# the source request after provider use; deterministic outputs and hashes remain.
MAX_MESSAGE_CHARS = 160_000
# UTF-8 has at most four bytes per Unicode code point, so the byte limit keeps
# the complete 160k-character envelope available to non-English screenplays too.
MAX_MESSAGE_BYTES = 640_000
# Canonical JSON is kept substantially beneath Firestore's 1 MiB document
# ceiling.  The 148,576-byte reserve covers Firestore field/index overhead and
# keeps the bound conservative rather than relying on the service's hard error.
MAX_DURABLE_JOB_BYTES = 900_000
MAX_ATTEMPTS = 3
# The reviewed project quota allows two image requests per rolling 75-second
# safety window.
# Generate one pair per request, then schedule the next pair after a quiet gap.
DEFAULT_VISUAL_PANELS_PER_DISPATCH = 2
MAX_VISUAL_PANELS_PER_DISPATCH = 2
# A provider quota response is different from a failed creative plan.  Preserve
# already validated private panels and give the same application attempt four
# deterministic scheduled successors (90, 180, 360, then 720 seconds by
# default) instead of immediately consuming a user-visible retry.  Successful
# visual successors are also spaced by 75 seconds so a long screenplay does
# not immediately issue the next image request.  These are strict configuration
# bounds, not unbounded retry or jitter knobs.
DEFAULT_VISUAL_SUCCESSOR_DELAY_SECONDS = 75
MIN_VISUAL_SUCCESSOR_DELAY_SECONDS = 75
MAX_VISUAL_SUCCESSOR_DELAY_SECONDS = 900
VISUAL_CAPACITY_REQUEST_LIMIT = 2
VISUAL_CAPACITY_WINDOW_SECONDS = 75
# Admission permits four concurrent jobs.  At most one live window per job plus
# one stale/recovery window can therefore exist in the private FIFO.  Keep a
# small hard cap so corrupt state can never produce an unbounded document or an
# unbounded Cloud Tasks schedule.
MAX_VISUAL_CAPACITY_QUEUE_LENGTH = 8
# A fourth admitted job can legitimately wait behind three complete 1,800-second
# worker leases plus the reviewed 75-second safety gaps.  Keep the opaque FIFO
# turn long enough for that bounded worst case, while still pruning abandoned
# turns deterministically.
VISUAL_CAPACITY_TURN_TTL_SECONDS = 7_275
DEFAULT_VISUAL_QUOTA_MAX_DEFERRALS = 4
MAX_VISUAL_QUOTA_MAX_DEFERRALS = 4
DEFAULT_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS = 90
DEFAULT_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS = 720
MIN_VISUAL_QUOTA_DEFERRAL_SECONDS = 30
MAX_VISUAL_QUOTA_DEFERRAL_SECONDS = 900
MAX_TASK_SCHEDULE_DELAY_SECONDS = 7_200
# The production-brief schema permits 40 scenes and the deterministic compiler
# emits three cards per scene.  With two visual panels per dispatch, the largest
# valid plan needs 60 normal visual chunks plus four provider-429 successor
# attempts.  Every visual attempt may need one FIFO timing reconciliation, then
# 120 pitch-card workers and one finalizer, plus the initial planning worker:
# 2*(60+4)+120+2 = 250 total deliveries.  The value is an exclusive sequence
# bound, so valid sequences are 0..249. Capacity waits remain separate from
# (and cannot consume) the four provider deferrals.
MAX_PIPELINE_DISPATCHES = 250
DEFAULT_ADMISSION_COOLDOWN_SECONDS = 10
DEFAULT_ADMISSION_WINDOW_SECONDS = 3_600
DEFAULT_ADMISSION_MAX_JOBS = 4
MAX_ADMISSION_MAX_JOBS = 4
DEFAULT_WORKER_LEASE_SECONDS = 1_800
# Firestore TTL deletes the entire private job record after this bounded
# evidence/retry window.  The source is cleared earlier on success and at the
# final retry limit.  One day is ample for a judge to inspect or retry a demo
# job without turning uploaded screenplay text into indefinite storage.
DEFAULT_JOB_RETENTION_SECONDS = 86_400
MIN_JOB_RETENTION_SECONDS = 3_600
MAX_JOB_RETENTION_SECONDS = 604_800

# Canonical, non-sensitive failure codes emitted by NarratedPitchRenderError in
# all_things_cloud_media.py.  Never persist the exception message as a fallback:
# FFmpeg, provider, and source details can contain private paths or screenplay text.
NARRATED_PITCH_RENDER_DIAGNOSTIC_CODES = frozenset(
    {
        "card_render_failed",
        "incomplete_card_render",
        "incomplete_subtitle_coverage",
        "incomplete_visual_coverage",
        "invalid_artifact_manifest",
        "invalid_narration_cue",
        "invalid_narration_input",
        "invalid_pitch_brief",
        "invalid_pitch_timeline",
        "invalid_rendered_video",
        "invalid_source_message",
        "invalid_tts_audio",
        "invalid_visual_asset",
        "invalid_visual_storyboard",
        "pitch_duration_exceeded",
        "pitch_duration_mismatch",
        "pitch_probe_failed",
        "pitch_probe_mismatch",
        "pitch_render_failed",
        "segment_probe_failed",
        "segment_probe_mismatch",
        "tts_synthesis_failed",
        "unresolved_visual_asset",
        "work_stopped",
        "unsafe_concat_input",
        "unsafe_media_command",
        "visual_asset_integrity_failed",
        "visual_asset_job_mismatch",
        "visual_asset_load_failed",
        "visual_cue_identity_mismatch",
    }
)

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_LOCATION = re.compile(r"^(?:global|[a-z]+-[a-z0-9]+[0-9])$")
_REGION = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]$")
_MODEL_VERSION = re.compile(r"^gemini-(\d+)\.(\d+)(?:[-.][a-z0-9][a-z0-9._-]*)?$", re.IGNORECASE)
_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_COLLECTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")


class AllThingsError(ValueError):
    """Base error for invalid configuration, requests, and transitions."""


class ConfigurationError(AllThingsError):
    """The Google-backed workflow is not safely configured."""


class BriefValidationError(AllThingsError):
    """Gemini did not return the exact production-brief contract."""


class VisualPanelGenerationError(AllThingsError):
    """A visual provider failed without exposing its raw response."""

    ALLOWED_CODES = frozenset(
        {
            "provider_blocked",
            "generation_failed",
            "invalid_provider_asset",
            "mixed_panel_failures",
            "quota_or_rate_limited",
            "project_visual_capacity_unavailable",
            "renderer_not_configured",
        }
    )

    def __init__(self, code: str) -> None:
        selected = code if code in self.ALLOWED_CODES else "generation_failed"
        super().__init__("visual storyboard panel generation failed")
        self.code = selected


class JobTransitionError(AllThingsError):
    """A requested durable-job transition is not allowed."""


class JobNotFoundError(AllThingsError):
    """The requested durable job does not exist."""


class AdmissionLimitError(AllThingsError):
    """The shared judge-demo admission budget is temporarily exhausted."""

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class JobLeaseBusyError(AllThingsError):
    """A live worker lease still owns this Cloud Tasks delivery."""

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class PipelineCheckpointError(AllThingsError):
    """A durable continuation checkpoint failed closed validation."""


class PipelineContinuationDispatchError(AllThingsError):
    """The next bounded worker dispatch could not be durably scheduled."""


class JobDispatchPendingError(AllThingsError):
    """A durable continuation outbox still needs Cloud Tasks reconciliation."""

    def __init__(self, message: str, *, retry_after_seconds: int = 5) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, min(60, int(retry_after_seconds)))


class JobVisualCapacityPendingError(JobDispatchPendingError):
    """The same named task must retry after its FIFO visual turn matures."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        AllThingsError.__init__(
            self,
            "project image capacity is temporarily reserved by another job",
        )
        self.retry_after_seconds = max(
            1,
            min(MAX_TASK_SCHEDULE_DELAY_SECONDS, int(retry_after_seconds)),
        )


class PipelineWorkStopped(AllThingsError):
    """Chunk work stopped because cancellation or lease fencing won."""


class _VisualQuotaDeferred(AllThingsError):
    """Internal, redacted signal carrying only validated partial progress."""

    def __init__(
        self,
        panels: Sequence[Mapping[str, Any]],
        *,
        evidence_origin: str,
    ) -> None:
        super().__init__("visual provider quota requires a bounded successor")
        self.panels = tuple(dict(panel) for panel in panels)
        self.evidence_origin = evidence_origin


class _VisualCapacityDeferred(AllThingsError):
    """Internal signal for a denied project-wide image-request reservation."""

    def __init__(
        self,
        panels: Sequence[Mapping[str, Any]],
        *,
        evidence_origin: str,
        retry_after_seconds: int,
        window_active: bool = True,
    ) -> None:
        super().__init__("project image capacity requires a bounded successor")
        self.panels = tuple(dict(panel) for panel in panels)
        self.evidence_origin = evidence_origin
        self.retry_after_seconds = max(
            1,
            min(MAX_TASK_SCHEDULE_DELAY_SECONDS, int(retry_after_seconds)),
        )
        self.window_active = bool(window_active)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATES = frozenset({JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _visual_capacity_reservation_token(
    *,
    job_id: str,
    attempt: int,
    dispatch_sequence: int,
) -> str:
    """Derive the retry-stable opaque FIFO token for one visual dispatch."""

    if (
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            job_id,
        )
        is None
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or isinstance(dispatch_sequence, bool)
        or not isinstance(dispatch_sequence, int)
        or not 1 <= dispatch_sequence < MAX_PIPELINE_DISPATCHES
    ):
        raise PipelineCheckpointError("visual capacity window identity is invalid")
    return hashlib.sha256(
        (
            "video-studio.visual-capacity/v1:"
            f"{job_id}:a{attempt}:d{dispatch_sequence}"
        ).encode("utf-8")
    ).hexdigest()


def _environment_integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = environment.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _clean_string(value: Any, *, label: str, minimum: int = 1, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise BriefValidationError(f"{label} must be text")
    cleaned = " ".join(value.strip().split())
    if len(cleaned) < minimum or len(cleaned) > maximum:
        raise BriefValidationError(f"{label} must contain {minimum}-{maximum} characters")
    return cleaned


def _clean_string_list(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
    maximum: int = 12,
    item_maximum: int = 240,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise BriefValidationError(f"{label} must contain {minimum}-{maximum} items")
    cleaned = tuple(
        _clean_string(item, label=f"{label} item", maximum=item_maximum)
        for item in value
    )
    if len({item.casefold() for item in cleaned}) != len(cleaned):
        raise BriefValidationError(f"{label} must not contain duplicates")
    return cleaned


@dataclass(frozen=True)
class AllThingsConfig:
    """Validated, non-secret Google Cloud target for one contest deployment."""

    project: str
    location: str = "global"
    model: str = DEFAULT_GEMINI_MODEL
    image_model: str = DEFAULT_IMAGE_MODEL
    firestore_database: str = "(default)"
    jobs_collection: str = "all_things_agentic_jobs"
    tasks_location: str = "us-central1"
    tasks_queue: str = "video-studio-production-briefs"
    worker_url: str = ""
    tasks_service_account: str = ""
    artifacts_bucket: str = ""
    tts_voice: str = "en-US-Chirp3-HD-Aoede"
    admission_cooldown_seconds: int = DEFAULT_ADMISSION_COOLDOWN_SECONDS
    admission_window_seconds: int = DEFAULT_ADMISSION_WINDOW_SECONDS
    admission_max_jobs: int = DEFAULT_ADMISSION_MAX_JOBS
    worker_lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS
    visual_panels_per_dispatch: int = DEFAULT_VISUAL_PANELS_PER_DISPATCH
    visual_successor_delay_seconds: int = DEFAULT_VISUAL_SUCCESSOR_DELAY_SECONDS
    visual_quota_max_deferrals: int = DEFAULT_VISUAL_QUOTA_MAX_DEFERRALS
    visual_quota_base_deferral_seconds: int = (
        DEFAULT_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS
    )
    visual_quota_max_deferral_seconds: int = DEFAULT_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS
    job_retention_seconds: int = DEFAULT_JOB_RETENTION_SECONDS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "AllThingsConfig":
        return cls(
            project=environment.get("GOOGLE_CLOUD_PROJECT", "").strip(),
            location=environment.get("GOOGLE_CLOUD_LOCATION", "global").strip() or "global",
            model=environment.get("KIRA_ALL_THINGS_GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            image_model=environment.get(
                "KIRA_ALL_THINGS_IMAGE_MODEL", DEFAULT_IMAGE_MODEL
            ).strip(),
            firestore_database=environment.get("KIRA_ALL_THINGS_FIRESTORE_DATABASE", "(default)").strip(),
            jobs_collection=environment.get("KIRA_ALL_THINGS_JOBS_COLLECTION", "all_things_agentic_jobs").strip(),
            tasks_location=environment.get("KIRA_ALL_THINGS_TASKS_LOCATION", "us-central1").strip(),
            tasks_queue=environment.get("KIRA_ALL_THINGS_TASKS_QUEUE", "video-studio-production-briefs").strip(),
            worker_url=environment.get("KIRA_ALL_THINGS_WORKER_URL", "").strip().rstrip("/"),
            tasks_service_account=environment.get("KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT", "").strip(),
            artifacts_bucket=environment.get("KIRA_ALL_THINGS_ARTIFACTS_BUCKET", "").strip(),
            tts_voice=environment.get(
                "KIRA_ALL_THINGS_TTS_VOICE", "en-US-Chirp3-HD-Aoede"
            ).strip(),
            admission_cooldown_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_ADMISSION_COOLDOWN_SECONDS",
                DEFAULT_ADMISSION_COOLDOWN_SECONDS,
            ),
            admission_window_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_ADMISSION_WINDOW_SECONDS",
                DEFAULT_ADMISSION_WINDOW_SECONDS,
            ),
            admission_max_jobs=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_ADMISSION_MAX_JOBS",
                DEFAULT_ADMISSION_MAX_JOBS,
            ),
            worker_lease_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_WORKER_LEASE_SECONDS",
                DEFAULT_WORKER_LEASE_SECONDS,
            ),
            visual_panels_per_dispatch=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_VISUAL_PANELS_PER_DISPATCH",
                DEFAULT_VISUAL_PANELS_PER_DISPATCH,
            ),
            visual_successor_delay_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_VISUAL_SUCCESSOR_DELAY_SECONDS",
                DEFAULT_VISUAL_SUCCESSOR_DELAY_SECONDS,
            ),
            visual_quota_max_deferrals=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRALS",
                DEFAULT_VISUAL_QUOTA_MAX_DEFERRALS,
            ),
            visual_quota_base_deferral_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS",
                DEFAULT_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS,
            ),
            visual_quota_max_deferral_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS",
                DEFAULT_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS,
            ),
            job_retention_seconds=_environment_integer(
                environment,
                "KIRA_ALL_THINGS_JOB_RETENTION_SECONDS",
                DEFAULT_JOB_RETENTION_SECONDS,
            ),
        )

    def issues(self, *, require_dispatch: bool = True) -> tuple[str, ...]:
        issues: list[str] = []
        if not _PROJECT_ID.fullmatch(self.project):
            issues.append("GOOGLE_CLOUD_PROJECT must be a valid project ID")
        if not _LOCATION.fullmatch(self.location):
            issues.append("GOOGLE_CLOUD_LOCATION must be global or a valid region")
        model_leaf = self.model.rsplit("/", 1)[-1]
        match = _MODEL_VERSION.fullmatch(model_leaf)
        if match is None or (int(match.group(1)), int(match.group(2))) < (3, 5):
            issues.append("KIRA_ALL_THINGS_GEMINI_MODEL must identify Gemini 3.5 or newer")
        image_model_leaf = self.image_model.rsplit("/", 1)[-1]
        image_match = _MODEL_VERSION.fullmatch(image_model_leaf)
        if (
            image_match is None
            or (int(image_match.group(1)), int(image_match.group(2))) < (3, 1)
            or not image_model_leaf.casefold().endswith("-image")
        ):
            issues.append(
                "KIRA_ALL_THINGS_IMAGE_MODEL must identify Gemini 3.1 Image or newer"
            )
        if not self.firestore_database:
            issues.append("KIRA_ALL_THINGS_FIRESTORE_DATABASE is required")
        if not _COLLECTION_ID.fullmatch(self.jobs_collection):
            issues.append("KIRA_ALL_THINGS_JOBS_COLLECTION is invalid")
        if not _REGION.fullmatch(self.tasks_location):
            issues.append("KIRA_ALL_THINGS_TASKS_LOCATION must be a valid region")
        if not _SAFE_ID.fullmatch(self.tasks_queue):
            issues.append("KIRA_ALL_THINGS_TASKS_QUEUE is invalid")
        if self.artifacts_bucket and len(self.artifacts_bucket) > 222:
            issues.append("KIRA_ALL_THINGS_ARTIFACTS_BUCKET is invalid")
        if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}-Chirp3-HD-[A-Za-z]+", self.tts_voice):
            issues.append("KIRA_ALL_THINGS_TTS_VOICE must identify a Chirp 3 HD voice")
        if not 0 <= self.admission_cooldown_seconds <= 300:
            issues.append("KIRA_ALL_THINGS_ADMISSION_COOLDOWN_SECONDS must be from 0 to 300")
        if not 60 <= self.admission_window_seconds <= 86_400:
            issues.append("KIRA_ALL_THINGS_ADMISSION_WINDOW_SECONDS must be from 60 to 86400")
        if self.admission_max_jobs != DEFAULT_ADMISSION_MAX_JOBS:
            issues.append(
                "KIRA_ALL_THINGS_ADMISSION_MAX_JOBS must be exactly "
                f"{DEFAULT_ADMISSION_MAX_JOBS}"
            )
        if not 60 <= self.worker_lease_seconds <= 1_800:
            issues.append("KIRA_ALL_THINGS_WORKER_LEASE_SECONDS must be from 60 to 1800")
        if self.visual_panels_per_dispatch != DEFAULT_VISUAL_PANELS_PER_DISPATCH:
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_PANELS_PER_DISPATCH must be exactly "
                f"{DEFAULT_VISUAL_PANELS_PER_DISPATCH}"
            )
        if not (
            MIN_VISUAL_SUCCESSOR_DELAY_SECONDS
            <= self.visual_successor_delay_seconds
            <= MAX_VISUAL_SUCCESSOR_DELAY_SECONDS
        ):
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_SUCCESSOR_DELAY_SECONDS must be from "
                f"{MIN_VISUAL_SUCCESSOR_DELAY_SECONDS} to "
                f"{MAX_VISUAL_SUCCESSOR_DELAY_SECONDS}"
            )
        if self.visual_successor_delay_seconds != DEFAULT_VISUAL_SUCCESSOR_DELAY_SECONDS:
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_SUCCESSOR_DELAY_SECONDS must be exactly "
                f"{DEFAULT_VISUAL_SUCCESSOR_DELAY_SECONDS}"
            )
        if not 0 <= self.visual_quota_max_deferrals <= MAX_VISUAL_QUOTA_MAX_DEFERRALS:
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRALS must be from 0 to "
                f"{MAX_VISUAL_QUOTA_MAX_DEFERRALS}"
            )
        if not (
            MIN_VISUAL_QUOTA_DEFERRAL_SECONDS
            <= self.visual_quota_base_deferral_seconds
            <= MAX_VISUAL_QUOTA_DEFERRAL_SECONDS
        ):
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS must be from "
                f"{MIN_VISUAL_QUOTA_DEFERRAL_SECONDS} to "
                f"{MAX_VISUAL_QUOTA_DEFERRAL_SECONDS}"
            )
        if not (
            MIN_VISUAL_QUOTA_DEFERRAL_SECONDS
            <= self.visual_quota_max_deferral_seconds
            <= MAX_VISUAL_QUOTA_DEFERRAL_SECONDS
        ):
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS must be from "
                f"{MIN_VISUAL_QUOTA_DEFERRAL_SECONDS} to "
                f"{MAX_VISUAL_QUOTA_DEFERRAL_SECONDS}"
            )
        if (
            self.visual_quota_base_deferral_seconds
            > self.visual_quota_max_deferral_seconds
        ):
            issues.append(
                "KIRA_ALL_THINGS_VISUAL_QUOTA_BASE_DEFERRAL_SECONDS must not "
                "exceed KIRA_ALL_THINGS_VISUAL_QUOTA_MAX_DEFERRAL_SECONDS"
            )
        if not MIN_JOB_RETENTION_SECONDS <= self.job_retention_seconds <= MAX_JOB_RETENTION_SECONDS:
            issues.append(
                "KIRA_ALL_THINGS_JOB_RETENTION_SECONDS must be from "
                f"{MIN_JOB_RETENTION_SECONDS} to {MAX_JOB_RETENTION_SECONDS}"
            )
        if require_dispatch:
            if not self.worker_url.startswith("https://"):
                issues.append("KIRA_ALL_THINGS_WORKER_URL must be an HTTPS Cloud Run URL")
            if "@" not in self.tasks_service_account or not self.tasks_service_account.endswith(
                ".gserviceaccount.com"
            ):
                issues.append("KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT must be a service-account email")
        return tuple(issues)

    def assert_valid(self, *, require_dispatch: bool = True) -> None:
        issues = self.issues(require_dispatch=require_dispatch)
        if issues:
            raise ConfigurationError("; ".join(issues))

    def safe_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "location": self.location,
            "model": self.model,
            "image_model": self.image_model,
            "framework": "google-genai",
            "api": "Vertex AI v1",
            "firestore_database": self.firestore_database,
            "jobs_collection": self.jobs_collection,
            "tasks_location": self.tasks_location,
            "tasks_queue": self.tasks_queue,
            "worker_url": self.worker_url,
            "tasks_service_account": self.tasks_service_account,
            "artifacts_bucket": self.artifacts_bucket,
            "tts_voice": self.tts_voice,
            "admission_cooldown_seconds": self.admission_cooldown_seconds,
            "admission_window_seconds": self.admission_window_seconds,
            "admission_max_jobs": self.admission_max_jobs,
            "worker_lease_seconds": self.worker_lease_seconds,
            "visual_panels_per_dispatch": self.visual_panels_per_dispatch,
            "visual_successor_delay_seconds": self.visual_successor_delay_seconds,
            "visual_quota_max_deferrals": self.visual_quota_max_deferrals,
            "visual_quota_base_deferral_seconds": self.visual_quota_base_deferral_seconds,
            "visual_quota_max_deferral_seconds": self.visual_quota_max_deferral_seconds,
            "job_retention_seconds": self.job_retention_seconds,
        }

    def target_digest(self) -> str:
        return sha256_json(self.safe_dict())


def _visual_quota_delay_seconds(
    config: AllThingsConfig,
    *,
    quota_deferrals_used: int,
) -> int:
    """Return the next deterministic, bounded quota delay.

    ``quota_deferrals_used`` is the number already committed in the immutable
    checkpoint.  A value of zero therefore produces the first/base delay.
    """

    if (
        isinstance(quota_deferrals_used, bool)
        or not isinstance(quota_deferrals_used, int)
        or not 0 <= quota_deferrals_used < MAX_VISUAL_QUOTA_MAX_DEFERRALS
    ):
        raise PipelineCheckpointError("quota-deferral count is outside the bound")
    return min(
        config.visual_quota_max_deferral_seconds,
        config.visual_quota_base_deferral_seconds * (2**quota_deferrals_used),
    )


def _build_pending_dispatch(
    *,
    attempt: int,
    predecessor_dispatch_sequence: int,
    dispatch_sequence: int,
    checkpoint_sha256: str,
    delay_seconds: int,
    delay_reason: str | None,
    prepared_at: datetime,
) -> dict[str, Any]:
    """Create the private, integrity-bound continuation dispatch outbox."""

    scheduled_epoch_seconds = math.ceil(prepared_at.timestamp()) + delay_seconds
    body: dict[str, Any] = {
        "schema": PIPELINE_PENDING_DISPATCH_SCHEMA,
        "application_attempt": attempt,
        "predecessor_dispatch_sequence": predecessor_dispatch_sequence,
        "dispatch_sequence": dispatch_sequence,
        "checkpoint_sha256": checkpoint_sha256,
        "delay_seconds": delay_seconds,
        "delay_reason": delay_reason,
        "prepared_at": prepared_at.isoformat(),
        "scheduled_epoch_seconds": scheduled_epoch_seconds,
    }
    body["manifest_sha256"] = sha256_json(body)
    return _validate_pending_dispatch(body)


def _validate_pending_dispatch(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "application_attempt",
        "predecessor_dispatch_sequence",
        "dispatch_sequence",
        "checkpoint_sha256",
        "delay_seconds",
        "delay_reason",
        "prepared_at",
        "scheduled_epoch_seconds",
        "manifest_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PipelineCheckpointError("pending dispatch contract is invalid")
    body = {key: value[key] for key in expected if key != "manifest_sha256"}
    attempt = value.get("application_attempt")
    predecessor = value.get("predecessor_dispatch_sequence")
    sequence = value.get("dispatch_sequence")
    delay = value.get("delay_seconds")
    reason = value.get("delay_reason")
    prepared_at = _parse_time_strict(value.get("prepared_at"))
    scheduled = value.get("scheduled_epoch_seconds")
    if (
        value.get("schema") != PIPELINE_PENDING_DISPATCH_SCHEMA
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or isinstance(predecessor, bool)
        or not isinstance(predecessor, int)
        or predecessor < 0
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence != predecessor + 1
        or sequence >= MAX_PIPELINE_DISPATCHES
        or not isinstance(value.get("checkpoint_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("checkpoint_sha256")))
        is None
        or isinstance(delay, bool)
        or not isinstance(delay, int)
        or not 0 <= delay <= MAX_TASK_SCHEDULE_DELAY_SECONDS
        or reason not in {None, "visual_quota", "visual_capacity", "visual_spacing"}
        or bool(delay) != bool(reason)
        or prepared_at is None
        or isinstance(scheduled, bool)
        or not isinstance(scheduled, int)
        or scheduled != math.ceil(prepared_at.timestamp()) + delay
        or value.get("manifest_sha256") != sha256_json(body)
    ):
        raise PipelineCheckpointError("pending dispatch integrity validation failed")
    return dict(value)


def _parse_time_strict(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_visual_capacity_result(value: Any) -> tuple[bool, int]:
    """Validate the redacted result of the transactional project-wide gate."""

    expected = {
        "schema",
        "granted",
        "retry_after_seconds",
        "window_seconds",
        "request_limit",
        "reservation_count",
        "window_active",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PipelineCheckpointError("visual capacity reservation is invalid")
    granted = value.get("granted")
    retry_after = value.get("retry_after_seconds")
    reservation_count = value.get("reservation_count")
    window_active = value.get("window_active")
    if (
        value.get("schema") != VISUAL_CAPACITY_SCHEMA
        or not isinstance(granted, bool)
        or isinstance(retry_after, bool)
        or not isinstance(retry_after, int)
        or isinstance(reservation_count, bool)
        or not isinstance(reservation_count, int)
        or not isinstance(window_active, bool)
        or value.get("window_seconds") != VISUAL_CAPACITY_WINDOW_SECONDS
        or value.get("request_limit") != VISUAL_CAPACITY_REQUEST_LIMIT
        or not 0 <= reservation_count <= VISUAL_CAPACITY_REQUEST_LIMIT
        or (granted and retry_after != 0)
        or (granted and not window_active)
        or (not granted and not 1 <= retry_after <= VISUAL_CAPACITY_WINDOW_SECONDS)
    ):
        raise PipelineCheckpointError("visual capacity reservation failed validation")
    return granted, retry_after, window_active


def _validate_visual_capacity_window(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "reservation_token",
        "not_before_epoch_seconds",
        "request_limit",
        "window_seconds",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PipelineCheckpointError("visual capacity window is invalid")
    token = value.get("reservation_token")
    not_before = value.get("not_before_epoch_seconds")
    if (
        value.get("schema") != VISUAL_CAPACITY_WINDOW_SCHEMA
        or not isinstance(token, str)
        or re.fullmatch(r"[0-9a-f]{64}", token) is None
        or isinstance(not_before, bool)
        or not isinstance(not_before, int)
        or not_before < 1
        or value.get("request_limit") != VISUAL_CAPACITY_REQUEST_LIMIT
        or value.get("window_seconds") != VISUAL_CAPACITY_WINDOW_SECONDS
    ):
        raise PipelineCheckpointError("visual capacity window failed validation")
    return dict(value)


@dataclass(frozen=True)
class SceneBrief:
    number: int
    purpose: str
    setting: str
    characters: tuple[str, ...]
    dialogue_required: bool

    @classmethod
    def from_mapping(cls, value: Any, *, expected_number: int) -> "SceneBrief":
        if not isinstance(value, Mapping) or set(value) != {
            "number",
            "purpose",
            "setting",
            "characters",
            "dialogue_required",
        }:
            raise BriefValidationError("each scene must contain exactly the required fields")
        number = value["number"]
        if isinstance(number, bool) or not isinstance(number, int) or number != expected_number:
            raise BriefValidationError("scene numbers must be consecutive and start at one")
        if not isinstance(value["dialogue_required"], bool):
            raise BriefValidationError("scene dialogue_required must be true or false")
        return cls(
            number=number,
            purpose=_clean_string(value["purpose"], label="scene purpose", maximum=1_200),
            setting=_clean_string(value["setting"], label="scene setting", maximum=240),
            characters=_clean_string_list(
                value["characters"], label="scene characters", maximum=12, item_maximum=120
            ),
            dialogue_required=value["dialogue_required"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "purpose": self.purpose,
            "setting": self.setting,
            "characters": list(self.characters),
            "dialogue_required": self.dialogue_required,
        }


@dataclass(frozen=True)
class ProductionBrief:
    title: str
    summary: str
    format: str
    target_audience: str
    duration_seconds: int
    genre: str
    tone: tuple[str, ...]
    visual_direction: str
    audio_direction: str
    deliverables: tuple[str, ...]
    scenes: tuple[SceneBrief, ...]
    clarifying_questions: tuple[str, ...]
    ready_for_production: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "ProductionBrief":
        fields = {
            "title",
            "summary",
            "format",
            "target_audience",
            "duration_seconds",
            "genre",
            "tone",
            "visual_direction",
            "audio_direction",
            "deliverables",
            "scenes",
            "clarifying_questions",
            "ready_for_production",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise BriefValidationError("production brief fields are incomplete or unsupported")
        duration = value["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 14_400:
            raise BriefValidationError("duration_seconds must be an integer from 5 to 14400")
        if not isinstance(value["scenes"], list) or not 1 <= len(value["scenes"]) <= 40:
            raise BriefValidationError("scenes must contain 1-40 items")
        if not isinstance(value["ready_for_production"], bool):
            raise BriefValidationError("ready_for_production must be true or false")
        questions = _clean_string_list(
            value["clarifying_questions"],
            label="clarifying_questions",
            maximum=6,
            item_maximum=300,
        )
        if value["ready_for_production"] and questions:
            raise BriefValidationError("a production-ready brief cannot retain clarifying questions")
        if not value["ready_for_production"] and not questions:
            raise BriefValidationError("a held brief must explain what still needs clarification")
        provider_scenes = tuple(
            SceneBrief.from_mapping(scene, expected_number=index)
            for index, scene in enumerate(value["scenes"], start=1)
        )
        scenes = _collapse_preexpanded_shot_rows(provider_scenes)
        return cls(
            title=_clean_string(value["title"], label="title", maximum=120),
            summary=_clean_string(value["summary"], label="summary", maximum=900),
            format=_clean_string(value["format"], label="format", maximum=80),
            target_audience=_clean_string(
                value["target_audience"], label="target_audience", maximum=240
            ),
            duration_seconds=duration,
            genre=_clean_string(value["genre"], label="genre", maximum=120),
            tone=_clean_string_list(value["tone"], label="tone", minimum=1, maximum=8, item_maximum=80),
            visual_direction=_clean_string(
                value["visual_direction"], label="visual_direction", maximum=600
            ),
            audio_direction=_clean_string(
                value["audio_direction"], label="audio_direction", maximum=600
            ),
            deliverables=_clean_string_list(
                value["deliverables"], label="deliverables", minimum=1, maximum=16, item_maximum=240
            ),
            scenes=scenes,
            clarifying_questions=questions,
            ready_for_production=value["ready_for_production"],
        )

    @classmethod
    def from_json(cls, text: str) -> "ProductionBrief":
        if not isinstance(text, str) or not text.strip():
            raise BriefValidationError("Gemini returned no production brief")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BriefValidationError("Gemini returned malformed production-brief JSON") from exc
        return cls.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BRIEF_SCHEMA,
            "title": self.title,
            "summary": self.summary,
            "format": self.format,
            "target_audience": self.target_audience,
            "duration_seconds": self.duration_seconds,
            "genre": self.genre,
            "tone": list(self.tone),
            "visual_direction": self.visual_direction,
            "audio_direction": self.audio_direction,
            "deliverables": list(self.deliverables),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "clarifying_questions": list(self.clarifying_questions),
            "ready_for_production": self.ready_for_production,
        }


PRODUCTION_BRIEF_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "format",
        "target_audience",
        "duration_seconds",
        "genre",
        "tone",
        "visual_direction",
        "audio_direction",
        "deliverables",
        "scenes",
        "clarifying_questions",
        "ready_for_production",
    ],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "format": {"type": "string"},
        "target_audience": {"type": "string"},
        "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 14400},
        "genre": {"type": "string"},
        "tone": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}},
        "visual_direction": {"type": "string"},
        "audio_direction": {"type": "string"},
        "deliverables": {
            "type": "array",
            "minItems": 1,
            "maxItems": 16,
            "items": {"type": "string"},
        },
        "scenes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["number", "purpose", "setting", "characters", "dialogue_required"],
                "properties": {
                    "number": {"type": "integer", "minimum": 1},
                    "purpose": {"type": "string"},
                    "setting": {"type": "string"},
                    "characters": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {"type": "string"},
                    },
                    "dialogue_required": {"type": "boolean"},
                },
            },
        },
        "clarifying_questions": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string"},
        },
        "ready_for_production": {"type": "boolean"},
    },
}


_SHOT_BLUEPRINTS: tuple[dict[str, str], ...] = (
    {
        "role": "establishing",
        "framing": "Wide establishing frame",
        "camera": "Locked frame or one slow, controlled reveal that establishes screen geography.",
    },
    {
        "role": "primary_coverage",
        "framing": "Medium primary coverage",
        "camera": "Eye-level coverage that protects the main action and stable eyelines.",
    },
    {
        "role": "continuity_bridge",
        "framing": "Detail, reaction, or environmental insert",
        "camera": "Locked insert from the established side of the axis for an editorial bridge.",
    },
)

_EXPLICIT_SHOT_ROW = re.compile(
    r"^\s*shot\s+0*(?P<scene>\d{1,3})\s*\.\s*0*(?P<shot>\d{1,3})\s*"
    r"[:\-–—)]\s*(?P<action>.+?)\s*$",
    re.IGNORECASE,
)
_EXPLICIT_SHOT_MARKER = re.compile(
    r"\bshot\s+0*(?P<scene>\d{1,3})\s*\.\s*0*(?P<shot>\d{1,3})\s*"
    r"[:\-–—)]\s*",
    re.IGNORECASE,
)


def _explicit_ordered_shot_rows(
    scenes: Sequence[SceneBrief],
) -> tuple[tuple[SceneBrief, int, int, str], ...]:
    """Recognize a provider response that already contains one row per shot.

    The brief schema intentionally exposes scenes, while the deterministic
    compiler normally creates three coverage cards for each scene. A model can
    nevertheless preserve a user's ``Shot 1.1`` / ``Shot 1.2`` / ``Shot 1.3``
    wording by returning each requested shot as a scene row. Expanding those
    rows again creates 27 cards from a nine-shot request. Only a complete,
    strictly ordered three-shots-per-scene grid is treated as pre-expanded;
    partial or ambiguous labels keep the ordinary compiler path.
    """

    rows: list[tuple[SceneBrief, int, int, str]] = []
    for scene in scenes:
        match = _EXPLICIT_SHOT_ROW.fullmatch(scene.purpose)
        if match is None:
            return ()
        action = match.group("action").strip()
        if not action:
            return ()
        rows.append(
            (
                scene,
                int(match.group("scene")),
                int(match.group("shot")),
                action,
            )
        )
    if not rows:
        return ()
    scene_numbers = sorted({row[1] for row in rows})
    if scene_numbers != list(range(1, len(scene_numbers) + 1)):
        return ()
    expected = [
        (scene_number, shot_number)
        for scene_number in scene_numbers
        for shot_number in range(1, len(_SHOT_BLUEPRINTS) + 1)
    ]
    if [(row[1], row[2]) for row in rows] != expected:
        return ()
    return tuple(rows)


def _collapse_preexpanded_shot_rows(
    scenes: Sequence[SceneBrief],
) -> tuple[SceneBrief, ...]:
    """Expose provider shot rows as the logical scene count the user asked for."""

    rows = _explicit_ordered_shot_rows(scenes)
    if not rows:
        return tuple(scenes)
    collapsed: list[SceneBrief] = []
    for story_scene_number in sorted({row[1] for row in rows}):
        group = [row for row in rows if row[1] == story_scene_number]
        characters = tuple(
            dict.fromkeys(name for scene, _group, _shot, _action in group for name in scene.characters)
        )
        purpose = " ".join(
            f"Shot {story_scene_number}.{shot_number}: {action}"
            for _scene, _group, shot_number, action in group
        )
        collapsed.append(
            SceneBrief(
                number=story_scene_number,
                purpose=purpose,
                setting=group[0][0].setting,
                characters=characters,
                dialogue_required=any(row[0].dialogue_required for row in group),
            )
        )
    return tuple(collapsed)


def _explicit_scene_shot_rows(
    brief: ProductionBrief,
) -> tuple[tuple[SceneBrief, int, int, str], ...]:
    """Read three ordered dotted shot directives retained inside each logical scene."""

    rows: list[tuple[SceneBrief, int, int, str]] = []
    for scene in brief.scenes:
        markers = list(_EXPLICIT_SHOT_MARKER.finditer(scene.purpose))
        if len(markers) != len(_SHOT_BLUEPRINTS):
            return ()
        for index, marker in enumerate(markers):
            story_scene_number = int(marker.group("scene"))
            shot_number = int(marker.group("shot"))
            if story_scene_number != scene.number or shot_number != index + 1:
                return ()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(scene.purpose)
            action = scene.purpose[marker.end() : end].strip(" ,;:-")
            if not action:
                return ()
            rows.append((scene, story_scene_number, shot_number, action))
    return tuple(rows)


def _explicit_shot_role(scene: SceneBrief, shot_number: int, action: str) -> str:
    """Choose the closest existing visual composition for an explicit shot."""

    lowered = action.casefold()
    if re.search(r"\b(?:establish|wide|master)\b", lowered):
        return "establishing"
    if re.search(r"\b(?:close[- ]?up|detail|insert)\b", lowered):
        return "continuity_bridge"
    if re.search(
        r"\b(?:medium|two[- ]?shot|over[- ]the[- ]shoulder|waist[- ]?up|full[- ]?body)\b",
        lowered,
    ):
        return "primary_coverage"
    if re.search(
        r"\b(?:speaks?|says?|answers?|replies?|broadcasts?|microphone|dialogue)\b",
        lowered,
    ):
        return "primary_coverage"
    if re.search(r"\b(?:reaction|looks?|sparks?)\b", lowered):
        return "continuity_bridge"
    card_characters = _explicit_card_characters(scene, action)
    if not card_characters:
        return "continuity_bridge"
    if len(card_characters) > 1:
        return "primary_coverage"
    return _SHOT_BLUEPRINTS[shot_number - 1]["role"]


def _shot_blueprint(role: str) -> Mapping[str, str]:
    for blueprint in _SHOT_BLUEPRINTS:
        if blueprint["role"] == role:
            return blueprint
    raise BriefValidationError("storyboard shot role is unsupported")


def _explicit_card_characters(scene: SceneBrief, action: str) -> tuple[str, ...]:
    """Keep only the logical-scene characters expressly present in one card."""

    selected = tuple(
        name
        for name in scene.characters
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", action, re.IGNORECASE)
    )
    if selected:
        return selected
    if scene.characters and re.search(
        r"\b(?:they|them|their|theirs|themselves|both|the pair|the friends|"
        r"the crew|together)\b",
        action,
        re.IGNORECASE,
    ):
        return scene.characters
    if len(scene.characters) == 1 and re.search(
        r"\b(?:he|him|his|himself|she|her|hers|herself|they|them|their|"
        r"speaks?|says?|answers?|replies?|broadcasts?|microphone|dialogue)\b",
        action,
        re.IGNORECASE,
    ):
        return scene.characters
    return ()


def _planned_timecode(frame: int, *, frame_rate: int = STORYBOARD_FRAME_RATE) -> str:
    if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
        raise BriefValidationError("planned frame positions must be non-negative integers")
    hours, remainder = divmod(frame, frame_rate * 3_600)
    minutes, remainder = divmod(remainder, frame_rate * 60)
    seconds, frames = divmod(remainder, frame_rate)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _planned_duration_seconds(frame_count: int) -> int | float:
    """Return a JSON-stable duration number for a planned frame count.

    Browsers serialize integral JSON numbers without a decimal suffix. Emit an
    integer at the source when the frame count is an exact second so a package
    downloaded through ``JSON.stringify`` keeps its manifest digest.
    """

    whole_seconds, remainder = divmod(frame_count, STORYBOARD_FRAME_RATE)
    if remainder == 0:
        return whole_seconds
    return round(frame_count / STORYBOARD_FRAME_RATE, 3)


def _equal_positive_allocation(total: int, count: int, *, minimum: int) -> tuple[int, ...]:
    if count < 1 or total < count * minimum:
        raise BriefValidationError("the planned duration cannot cover every storyboard card")
    distributable = total - (count * minimum)
    quotient, remainder = divmod(distributable, count)
    return tuple(minimum + quotient + (1 if index < remainder else 0) for index in range(count))


def _weighted_positive_allocation(total: int, weights: Sequence[int]) -> tuple[int, ...]:
    if not weights or total < len(weights) or any(weight <= 0 for weight in weights):
        raise BriefValidationError("storyboard shot allocation is invalid")
    distributable = total - len(weights)
    weight_total = sum(weights)
    extras = [(distributable * weight) // weight_total for weight in weights]
    remainder = distributable - sum(extras)
    order = sorted(
        range(len(weights)),
        key=lambda index: (-(distributable * weights[index] % weight_total), index),
    )
    for index in order[:remainder]:
        extras[index] += 1
    return tuple(1 + extra for extra in extras)


def _scene_continuity(scene: SceneBrief) -> list[str]:
    requirements = [
        f"Keep the geography of {scene.setting} stable across every scene-{scene.number} card."
    ]
    if scene.characters:
        names = ", ".join(scene.characters)
        requirements.append(
            f"Match {names}'s wardrobe, props, eyelines, and screen direction across coverage."
        )
    else:
        requirements.append("Match props, eyelines, and screen direction across coverage.")
    requirements.append("Preserve the brief's visual direction across matching coverage.")
    return requirements


def _source_footage_guidance(scene: SceneBrief) -> str:
    subjects = ", ".join(scene.characters) if scene.characters else "the scene subjects"
    return (
        f"Select only verified source coverage of {subjects} in {scene.setting}; "
        "flag missing coverage instead of inventing a clip."
    )


def _bridge_shot_guidance(scene: SceneBrief) -> str:
    return (
        f"For the scene beat '{scene.purpose}', prefer a motivated reaction, prop, or "
        f"environmental cutaway from {scene.setting}; "
        "if none exists, record a coverage gap rather than implying unverified footage."
    )


def _shot_action(scene: SceneBrief, role: str) -> str:
    subjects = ", ".join(scene.characters) if scene.characters else "the scene subjects"
    if role == "establishing":
        return (
            f"Establish {scene.setting} and the spatial relationship of {subjects} before this "
            f"scene beat: {scene.purpose}"
        )
    if role == "primary_coverage":
        return (
            f"Stage {subjects} in {scene.setting} for the primary scene beat: {scene.purpose}"
        )
    return (
        f"Hold a reaction, prop, or environmental detail in {scene.setting} that directly "
        f"supports this beat: {scene.purpose}"
    )


def _shot_audio(brief: ProductionBrief, scene: SceneBrief, role: str) -> str:
    subjects = ", ".join(scene.characters) if scene.characters else "the scene subjects"
    if role == "primary_coverage" and scene.dialogue_required:
        prefix = (
            f"Protect intelligible dialogue coverage for {subjects} during this beat: "
            f"{scene.purpose}"
        )
    elif role == "primary_coverage":
        prefix = f"Carry motivated action sound for this beat: {scene.purpose} No dialogue is required."
    elif role == "establishing":
        prefix = f"Capture clean establishing ambience and room tone for {scene.setting}."
    else:
        prefix = f"Use clean room tone or motivated transition audio under the beat: {scene.purpose}"
    return f"{prefix} Brief audio direction: {brief.audio_direction}"


def compile_storyboard_timeline(brief: ProductionBrief) -> dict[str, Any]:
    """Deterministically expand a validated creative brief into planned shot cards.

    The compiler allocates frames and writes editorial guidance only. It does not
    select, alter, or claim the existence of source media.
    """

    total_frames = brief.duration_seconds * STORYBOARD_FRAME_RATE
    explicit_rows = _explicit_scene_shot_rows(brief)
    if explicit_rows:
        duration_frames = _equal_positive_allocation(
            total_frames,
            len(explicit_rows),
            minimum=1,
        )
        first_settings = {
            story_scene_number: next(
                scene.setting
                for scene, row_scene_number, _shot_number, _action in explicit_rows
                if row_scene_number == story_scene_number
            )
            for story_scene_number in sorted({row[1] for row in explicit_rows})
        }
        shots: list[dict[str, Any]] = []
        cursor = 0
        for sequence, ((scene, story_scene_number, shot_number, action), frames) in enumerate(
            zip(explicit_rows, duration_frames),
            start=1,
        ):
            out_frame = cursor + frames
            role = _explicit_shot_role(scene, shot_number, action)
            blueprint = _shot_blueprint(role)
            group_setting = first_settings[story_scene_number]
            card_characters = _explicit_card_characters(scene, action)
            logical_scene = SceneBrief(
                number=story_scene_number,
                purpose=action,
                setting=group_setting,
                characters=card_characters,
                dialogue_required=scene.dialogue_required,
            )
            shots.append(
                {
                    "shot_id": f"SC{story_scene_number:02d}-SH{shot_number:02d}",
                    "sequence": sequence,
                    # The public brief has already been normalized back to the
                    # user's logical scene count.
                    "scene_number": scene.number,
                    # story_scene_number binds dialogue and location flow to
                    # the user's actual scene rather than the provider row.
                    "story_scene_number": story_scene_number,
                    "characters": list(card_characters),
                    "role": role,
                    "planned_in_frame": cursor,
                    "planned_out_frame_exclusive": out_frame,
                    "planned_in_timecode": _planned_timecode(cursor),
                    "planned_out_timecode": _planned_timecode(out_frame),
                    "planned_duration_frames": frames,
                    "planned_duration_seconds": _planned_duration_seconds(frames),
                    "storyboard_card": {
                        "characters": list(card_characters),
                        "framing": blueprint["framing"],
                        "camera": blueprint["camera"],
                        "setting": group_setting,
                        "action": action,
                        "dialogue_or_audio": _shot_audio(brief, logical_scene, role),
                        "continuity_requirements": _scene_continuity(logical_scene),
                        "source_footage_guidance": _source_footage_guidance(logical_scene),
                        "bridge_shot_guidance": _bridge_shot_guidance(logical_scene),
                    },
                }
            )
            cursor = out_frame
        return {
            "schema": STORYBOARD_TIMELINE_SCHEMA,
            "layout": "explicit_ordered_shots",
            "timecode_basis": "planned_non_drop_24fps",
            "frame_rate": STORYBOARD_FRAME_RATE,
            "start_timecode": _planned_timecode(0),
            "end_timecode": _planned_timecode(total_frames),
            "duration_frames": total_frames,
            "duration_seconds": brief.duration_seconds,
            "shot_count": len(shots),
            "shots": shots,
        }

    scene_frames = _equal_positive_allocation(
        total_frames,
        len(brief.scenes),
        minimum=len(_SHOT_BLUEPRINTS),
    )
    shots: list[dict[str, Any]] = []
    cursor = 0
    sequence = 1
    for scene, allocated_frames in zip(brief.scenes, scene_frames):
        shot_frames = _weighted_positive_allocation(allocated_frames, (25, 50, 25))
        continuity = _scene_continuity(scene)
        source_guidance = _source_footage_guidance(scene)
        bridge_guidance = _bridge_shot_guidance(scene)
        for local_index, (blueprint, duration_frames) in enumerate(
            zip(_SHOT_BLUEPRINTS, shot_frames),
            start=1,
        ):
            out_frame = cursor + duration_frames
            role = blueprint["role"]
            shots.append(
                {
                    "shot_id": f"SC{scene.number:02d}-SH{local_index:02d}",
                    "sequence": sequence,
                    "scene_number": scene.number,
                    "role": role,
                    "planned_in_frame": cursor,
                    "planned_out_frame_exclusive": out_frame,
                    "planned_in_timecode": _planned_timecode(cursor),
                    "planned_out_timecode": _planned_timecode(out_frame),
                    "planned_duration_frames": duration_frames,
                    "planned_duration_seconds": _planned_duration_seconds(duration_frames),
                    "storyboard_card": {
                        "framing": blueprint["framing"],
                        "camera": blueprint["camera"],
                        "action": _shot_action(scene, role),
                        "dialogue_or_audio": _shot_audio(brief, scene, role),
                        "continuity_requirements": list(continuity),
                        "source_footage_guidance": source_guidance,
                        "bridge_shot_guidance": bridge_guidance,
                    },
                }
            )
            cursor = out_frame
            sequence += 1
    return {
        "schema": STORYBOARD_TIMELINE_SCHEMA,
        "timecode_basis": "planned_non_drop_24fps",
        "frame_rate": STORYBOARD_FRAME_RATE,
        "start_timecode": _planned_timecode(0),
        "end_timecode": _planned_timecode(total_frames),
        "duration_frames": total_frames,
        "duration_seconds": brief.duration_seconds,
        "shot_count": len(shots),
        "shots": shots,
    }


def _brief_from_export(value: Any) -> ProductionBrief:
    if not isinstance(value, Mapping) or value.get("schema") != BRIEF_SCHEMA:
        raise BriefValidationError("storyboard package contains an invalid production brief")
    exported = {key: item for key, item in value.items() if key != "schema"}
    raw_scenes = exported.get("scenes")
    if isinstance(raw_scenes, list) and raw_scenes:
        expanded: list[dict[str, Any]] = []
        for expected_scene_number, raw_scene in enumerate(raw_scenes, start=1):
            if not isinstance(raw_scene, Mapping):
                expanded = []
                break
            purpose = raw_scene.get("purpose")
            if not isinstance(purpose, str):
                expanded = []
                break
            markers = list(_EXPLICIT_SHOT_MARKER.finditer(purpose))
            if len(markers) != len(_SHOT_BLUEPRINTS):
                expanded = []
                break
            scene_rows: list[dict[str, Any]] = []
            for index, marker in enumerate(markers):
                story_scene_number = int(marker.group("scene"))
                shot_number = int(marker.group("shot"))
                if story_scene_number != expected_scene_number or shot_number != index + 1:
                    scene_rows = []
                    break
                end = (
                    markers[index + 1].start()
                    if index + 1 < len(markers)
                    else len(purpose)
                )
                action = purpose[marker.end() : end].strip(" ,;:-")
                if not action:
                    scene_rows = []
                    break
                row = dict(raw_scene)
                row["number"] = len(expanded) + len(scene_rows) + 1
                row["purpose"] = (
                    f"Shot {story_scene_number}.{shot_number}: {action}"
                )
                scene_rows.append(row)
            if len(scene_rows) != len(_SHOT_BLUEPRINTS):
                expanded = []
                break
            expanded.extend(scene_rows)
        if len(expanded) == len(raw_scenes) * len(_SHOT_BLUEPRINTS):
            exported["scenes"] = expanded
    return ProductionBrief.from_mapping(exported)


def _storyboard_package_body(
    brief: ProductionBrief,
    *,
    timeline: Mapping[str, Any],
) -> dict[str, Any]:
    brief_export = brief.to_dict()
    brief_digest = sha256_json(brief_export)
    explicit_layout = timeline.get("layout") == "explicit_ordered_shots"
    mutations = [
        "allocate_total_duration_as_contiguous_24fps_frames",
        (
            "preserve_explicit_ordered_shot_rows_without_double_expansion"
            if explicit_layout
            else "expand_each_scene_to_establishing_primary_and_bridge_coverage"
        ),
        "attach_plan_only_continuity_and_source_coverage_guidance",
    ]
    return {
        "schema": STORYBOARD_PACKAGE_SCHEMA,
        "package_id": f"storyboard-{brief_digest[:24]}",
        "brief_sha256": brief_digest,
        "status": "ready_for_editorial" if brief.ready_for_production else "clarification_required",
        "media_status": "unrendered_plan",
        "plan_only": True,
        "compiler": {
            "name": "video-studio-deterministic-storyboard-compiler",
            "version": "1",
            "mutations": mutations,
        },
        "production_brief": brief_export,
        "timeline": dict(timeline),
    }


def _audit_item(check_id: str, passed: bool, evidence: str) -> dict[str, str]:
    return {
        "id": check_id,
        "status": "pass" if passed else "fail",
        "evidence": evidence,
    }


def audit_storyboard_package(value: Mapping[str, Any]) -> dict[str, Any]:
    """Audit a package against the only timeline the deterministic compiler permits."""

    checks: list[dict[str, str]] = []
    brief: ProductionBrief | None = None
    try:
        brief = _brief_from_export(value.get("production_brief"))
    except BriefValidationError:
        pass
    checks.append(
        _audit_item(
            "creative_plan_contract",
            brief is not None,
            "Embedded production brief matches the exact validated creative-plan schema."
            if brief is not None
            else "Embedded production brief is missing or invalid.",
        )
    )
    expected: dict[str, Any] | None = None
    if brief is not None:
        expected_timeline = compile_storyboard_timeline(brief)
        expected = _storyboard_package_body(brief, timeline=expected_timeline)
        checks.append(
            _audit_item(
                "brief_digest",
                value.get("brief_sha256") == expected["brief_sha256"],
                "Creative-plan digest matches canonical JSON.",
            )
        )
        expected_top = set(expected)
        actual_top = set(value) - {"audit", "manifest_sha256"}
        checks.append(
            _audit_item(
                "package_contract",
                actual_top == expected_top
                and all(value.get(key) == expected[key] for key in expected_top - {"timeline"}),
                "Package identity, readiness, compiler, and plan-only media claims are deterministic.",
            )
        )
        actual_timeline = value.get("timeline")
        timeline_matches = actual_timeline == expected_timeline
        checks.append(
            _audit_item(
                "deterministic_timeline_contract",
                timeline_matches,
                "Every timeline field and per-card value matches deterministic compilation.",
            )
        )
        actual_shots = (
            actual_timeline.get("shots")
            if isinstance(actual_timeline, Mapping)
            else None
        )
        expected_shots = expected_timeline["shots"]
        ordered = isinstance(actual_shots, list) and [
            (shot.get("shot_id"), shot.get("sequence"), shot.get("scene_number"), shot.get("role"))
            for shot in actual_shots
            if isinstance(shot, Mapping)
        ] == [
            (shot["shot_id"], shot["sequence"], shot["scene_number"], shot["role"])
            for shot in expected_shots
        ]
        checks.append(
            _audit_item(
                "ordered_three_angle_coverage",
                ordered,
                f"Expected {len(expected_shots)} ordered establishing, primary, and bridge cards.",
            )
        )
        timed = isinstance(actual_timeline, Mapping) and all(
            actual_timeline.get(key) == expected_timeline[key]
            for key in {
                "schema",
                "timecode_basis",
                "frame_rate",
                "start_timecode",
                "end_timecode",
                "duration_frames",
                "duration_seconds",
                "shot_count",
            }
        ) and isinstance(actual_shots, list) and all(
            isinstance(actual, Mapping)
            and all(
                actual.get(key) == expected_shot[key]
                for key in {
                    "planned_in_frame",
                    "planned_out_frame_exclusive",
                    "planned_in_timecode",
                    "planned_out_timecode",
                    "planned_duration_frames",
                    "planned_duration_seconds",
                }
            )
            for actual, expected_shot in zip(actual_shots, expected_shots)
        ) and len(actual_shots) == len(expected_shots)
        checks.append(
            _audit_item(
                "contiguous_timeline",
                timed,
                "Planned frame ranges are positive, gap-free, non-overlapping, and end at the brief duration.",
            )
        )
        cards_match = isinstance(actual_shots, list) and len(actual_shots) == len(expected_shots) and all(
            isinstance(actual, Mapping)
            and actual.get("storyboard_card") == expected_shot["storyboard_card"]
            for actual, expected_shot in zip(actual_shots, expected_shots)
        )
        checks.append(
            _audit_item(
                "coverage_and_continuity_cards",
                cards_match,
                "Every card has deterministic framing, camera, action, audio/dialogue, continuity, source, and bridge guidance.",
            )
        )
        dialogue_covered = isinstance(actual_shots, list) and all(
            not scene.dialogue_required
            or any(
                isinstance(shot, Mapping)
                and shot.get("scene_number") == scene.number
                and shot.get("role") == "primary_coverage"
                and isinstance(shot.get("storyboard_card"), Mapping)
                and str(shot["storyboard_card"].get("dialogue_or_audio", "")).startswith(
                    "Protect intelligible dialogue coverage"
                )
                for shot in actual_shots
            )
            for scene in brief.scenes
        )
        checks.append(
            _audit_item(
                "dialogue_audio_coverage",
                dialogue_covered,
                "Every dialogue-required scene has a primary coverage card that protects intelligibility.",
            )
        )
    else:
        for check_id in (
            "brief_digest",
            "package_contract",
            "deterministic_timeline_contract",
            "ordered_three_angle_coverage",
            "contiguous_timeline",
            "coverage_and_continuity_cards",
            "dialogue_audio_coverage",
        ):
            checks.append(_audit_item(check_id, False, "Cannot verify without a valid creative plan."))
    structurally_valid = all(check["status"] == "pass" for check in checks)
    ready = bool(brief and brief.ready_for_production and structurally_valid)
    return {
        "schema": STORYBOARD_AUDIT_SCHEMA,
        "compiler": "deterministic_storyboard_audit/v1",
        "structurally_valid": structurally_valid,
        "ready_for_editorial": ready,
        "passed": ready,
        "checks": checks,
        "issue_codes": [check["id"] for check in checks if check["status"] == "fail"],
        "hold_reasons": list(brief.clarifying_questions) if brief and not brief.ready_for_production else [],
    }


def build_storyboard_package(
    brief: ProductionBrief,
    *,
    timeline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    expected_timeline = compile_storyboard_timeline(brief)
    selected_timeline = dict(timeline) if timeline is not None else expected_timeline
    if selected_timeline != expected_timeline:
        raise BriefValidationError("storyboard timeline differs from deterministic compilation")
    body = _storyboard_package_body(brief, timeline=selected_timeline)
    audit = audit_storyboard_package(body)
    if not audit["structurally_valid"]:
        raise BriefValidationError("storyboard package failed its deterministic audit")
    package = {**body, "audit": audit}
    package["manifest_sha256"] = sha256_json(package)
    if len(canonical_json(package).encode("utf-8")) > MAX_STORYBOARD_PACKAGE_BYTES:
        raise BriefValidationError("storyboard package exceeds the durable document size budget")
    validate_storyboard_package(package)
    return package


def validate_storyboard_package(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "package_id",
        "brief_sha256",
        "status",
        "media_status",
        "plan_only",
        "compiler",
        "production_brief",
        "timeline",
        "audit",
        "manifest_sha256",
    }:
        raise BriefValidationError("storyboard package fields are incomplete or unsupported")
    manifest = dict(value)
    supplied_digest = manifest.pop("manifest_sha256")
    if not isinstance(supplied_digest, str) or supplied_digest != sha256_json(manifest):
        raise BriefValidationError("storyboard package manifest digest is invalid")
    expected_audit = audit_storyboard_package(value)
    if value.get("audit") != expected_audit or not expected_audit["structurally_valid"]:
        raise BriefValidationError("storyboard package audit is invalid")
    return dict(value)


@dataclass(frozen=True)
class BriefProviderResult:
    brief: ProductionBrief
    execution: Mapping[str, Any]


class BriefProvider(Protocol):
    def create_brief(self, message: str, *, job_id: str) -> BriefProviderResult:
        ...


@dataclass(frozen=True)
class VisualPanelProviderResult:
    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    execution: Mapping[str, Any]


class VisualPanelProvider(Protocol):
    def create_panel(
        self,
        prompt: str,
        *,
        shot_id: str,
        job_id: str,
        reference_image: bytes | None = None,
    ) -> VisualPanelProviderResult:
        ...


class ArtifactStore(Protocol):
    """Private job-scoped artifact storage; implementations must never publish URLs."""

    def put_bytes(
        self,
        *,
        job_id: str,
        artifact_id: str,
        data: bytes,
        content_type: str,
    ) -> Mapping[str, Any]:
        ...

    def get_bytes(self, object_name: str) -> bytes:
        ...


class NarratedPitchRenderer(Protocol):
    """Render the complete narrated storyboard pitch or fail closed."""

    def render(
        self,
        *,
        brief: "ProductionBrief",
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
    ) -> Mapping[str, Any]:
        ...

    def render_segment_chunk(
        self,
        *,
        brief: "ProductionBrief",
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
        start_index: int,
        max_cards: int = 1,
        ownership_check: Callable[[], bool] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        ...

    def finalize_segments(
        self,
        *,
        brief: "ProductionBrief",
        timeline: Mapping[str, Any],
        source_message: str,
        visual_storyboard: Mapping[str, Any],
        job_id: str,
        segments: Sequence[Mapping[str, Any]],
        ownership_check: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        ...


_VISUAL_MISSING_REASONS = frozenset(
    {
        "held_for_clarification",
        "renderer_not_configured",
        "provider_blocked",
        "generation_failed",
        "invalid_provider_asset",
        "panel_limit_reached",
        "inline_budget_exhausted",
        "mixed_panel_failures",
        "quota_or_rate_limited",
    }
)

_VISUAL_DETAIL_FRAMING_PATTERN = re.compile(
    r"\b(?:detail|insert|close[- ]?up|extreme close|macro|foreground)\b",
    re.IGNORECASE,
)
_VISUAL_HAND_ACTION_PATTERN = re.compile(
    r"\b(?:hand|hands|finger|fingers|thumb|wrist|forearm|arm|arms|"
    r"holds|holding|grip|grips|gripping|grasp|grasps|grasping|"
    r"grab|grabs|grabbing|clutch|clutches|clutching|"
    r"press|presses|pressing|push|pushes|pushing|touch|touches|touching|"
    r"reach|reaches|reaching|pick up|picks up|picking up)\b",
    re.IGNORECASE,
)
_VISUAL_EXPLICIT_TIME_CHANGE_PATTERN = re.compile(
    r"\b(?:\d+\s+(?:years?|months?|days?)\s+later|years? later|months? later|"
    r"time jump|flashback|flash-forward|earlier in (?:his|her|their) life|"
    r"visibly older|aged by)\b",
    re.IGNORECASE,
)
_VISUAL_EXPLICIT_WARDROBE_CHANGE_PATTERN = re.compile(
    r"\b(?:wardrobe change|costume change|changes? (?:clothes|outfit|wardrobe)|"
    r"changed into|now wears?|new outfit|different clothes|hair (?:change|changes|cut)|"
    r"cuts? (?:his|her|their) hair)\b",
    re.IGNORECASE,
)


def _nonnegated_visual_change_match(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 48) : match.start()]
        if re.search(
            r"\b(?:no|not|never|without|avoid|avoids|avoiding)\b[^.!?;:]{0,40}$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        return True
    return False


def _scene_appearance_change_contract(scene: SceneBrief) -> str:
    context = f"{scene.purpose} {scene.setting}"
    time_change = _nonnegated_visual_change_match(
        _VISUAL_EXPLICIT_TIME_CHANGE_PATTERN,
        context,
    )
    wardrobe_change = _nonnegated_visual_change_match(
        _VISUAL_EXPLICIT_WARDROBE_CHANGE_PATTERN,
        context,
    )
    explicitly_named = tuple(
        name for name in scene.characters if re.search(rf"\b{re.escape(name)}\b", context, re.I)
    )
    if wardrobe_change and not explicitly_named:
        wardrobe_change = False
    if not time_change and not wardrobe_change:
        return (
            "Do not change apparent age, build, face, hair, or wardrobe. Preserve the most "
            "recently approved state, including garment cut, sleeves, patches, and harness."
        )
    permitted: list[str] = []
    if time_change:
        permitted.append("the stated passage of time or aging cue for the scene cast")
    if wardrobe_change:
        permitted.append(
            "the stated wardrobe or hair change for " + ", ".join(explicitly_named)
        )
    permission = " and ".join(permitted)
    return (
        f"Only {permission} may change, exactly as described by the current action. Preserve every "
        "other character trait. Use the approved result as the later reference. Time passage alone "
        "does not change wardrobe or hair, and a wardrobe change alone does not change age or build."
    )


def _shot_characters(scene: SceneBrief, shot: Mapping[str, Any]) -> tuple[str, ...]:
    """Return a validated per-card cast, falling back to the logical scene cast."""

    raw = shot.get("characters")
    if raw is None:
        return scene.characters
    if not isinstance(raw, list) or any(not isinstance(name, str) for name in raw):
        raise BriefValidationError("visual storyboard shot has invalid card characters")
    allowed = {name.casefold(): name for name in scene.characters}
    selected: list[str] = []
    for raw_name in raw:
        name = raw_name.strip()
        canonical = allowed.get(name.casefold())
        if canonical is None or canonical in selected:
            raise BriefValidationError("visual storyboard shot has invalid card characters")
        selected.append(canonical)
    return tuple(selected)


def storyboard_panel_prompt(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
) -> str:
    """Return the deterministic art-direction prompt for one planned shot."""

    scene_number = shot.get("scene_number")
    if (
        isinstance(scene_number, bool)
        or not isinstance(scene_number, int)
        or not 1 <= scene_number <= len(brief.scenes)
    ):
        raise BriefValidationError("visual storyboard shot has an invalid scene number")
    scene = brief.scenes[scene_number - 1]
    card = shot.get("storyboard_card")
    if not isinstance(card, Mapping):
        raise BriefValidationError("visual storyboard shot is missing its card")
    card_characters = _shot_characters(scene, shot)
    explicit_card_cast = "characters" in shot
    card_scene = SceneBrief(
        number=scene.number,
        purpose=(
            str(card.get("action") or scene.purpose)
            if explicit_card_cast
            else scene.purpose
        ),
        setting=str(card.get("setting") or scene.setting),
        characters=card_characters,
        dialogue_required=scene.dialogue_required,
    )
    role = shot.get("role")
    characters = ", ".join(card_characters)
    character_count = len(card_characters)
    if card_characters:
        if len(card_characters) <= 4:
            character_identity_locks = " ".join(
                f"Keep {name} as the same established person."
                for name in card_characters
            )
        else:
            character_identity_locks = (
                "Identity locks are individual: "
                + "; ".join(f"{name} remains {name}" for name in card_characters)
                + "."
            )
        shared_appearance_details = (
            "For every named person, preserve the same established person, apparent age and "
            "build, face and head shape, hair style, length, and color, and complete wardrobe "
            "including garment cut, sleeves, patches, and harness; establish these once if no "
            "reference exists. Never merge identities or features between people."
        )
        appearance_contract = (
            f"{character_identity_locks} {shared_appearance_details} "
            f"{_scene_appearance_change_contract(card_scene)}"
        )
        cast_contract = (
            f"The exact visible cast is {characters}, each exactly once. Add no extras, crowds, "
            "silhouettes, reflections, screen faces, mannequins, background bodies, or duplicates."
        )
        full_cast_direction = (
            f"Show all {character_count} required character{'s' if character_count != 1 else ''}."
        )
        if explicit_card_cast:
            bridge_cast_direction = (
                f"Show exactly {characters} in one tight connected reaction composition; "
                "show every required character once and nobody else."
            )
        else:
            bridge_cast_direction = (
                "Prefer a prop or environmental detail. If a face is essential, show one allowed "
                "character once in a tight natural crop and nobody else."
            )
    else:
        appearance_contract = ""
        cast_contract = (
            "This card has an empty cast. Show no people, faces, silhouettes, reflections, "
            "mannequins, hands, limbs, or other body fragments anywhere."
        )
        full_cast_direction = "Show no human figures."
        bridge_cast_direction = "Show no human figures."
    screen_content_contract = (
        "Any visible screen contains only abstract, text-free interface graphics or indicator "
        "lights, never a person, face, body part, portrait, reflection, or readable words."
    )
    explicit_hand_action = bool(
        _VISUAL_HAND_ACTION_PATTERN.search(
            (
                card_scene.purpose
                if explicit_card_cast
                else f"{card_scene.purpose} {card.get('action') or ''}"
            )
        )
    )
    if explicit_hand_action:
        hand_contract = (
            "The exact action requires hand contact. Show only the hands needed for that action, "
            "at natural scale, each connected to the correct visible wrist, arm, and body. Never "
            "duplicate a hand or add a detached foreground hand."
        )
    else:
        hand_contract = (
            "Do not invent a pointing, detached, oversized, duplicated, or foreground hand. "
            "For a detail card, keep hands and other body fragments out of frame."
        )
    body_contract = (
        "Every visible person is one anatomically complete, connected body or a natural crop at "
        "the outer canvas edge. Never add or detach a head, torso, arm, hand, hip, leg, or foot."
    )
    role_directions = {
        "establishing": (
            "Use a genuinely wide environmental master that clearly establishes location "
            "geography. Keep any required cast naturally inside the environment; do not turn this "
            "into a medium shot, over-the-shoulder view, reaction close-up, or prop insert. "
            f"{full_cast_direction}"
        ),
        "primary_coverage": (
            "Use action-focused medium, over-the-shoulder, waist-up, or full-body coverage exactly "
            "as requested by the current action and camera. Make faces, eyelines, and interaction "
            f"clear without repeating the establishing composition. {full_cast_direction}"
        ),
        "continuity_bridge": (
            "Use a tight reaction, prop insert, or environmental detail that matches the exact "
            "action. Do not repeat the establishing composition or stage a generic full-body "
            f"group. {bridge_cast_direction}"
        ),
    }
    if role not in role_directions:
        raise BriefValidationError("visual storyboard shot has an invalid coverage role")
    action = str(card.get("action") or "").strip().rstrip(".")
    setting = card_scene.setting.strip()
    framing = str(card.get("framing") or "").strip()
    camera = str(card.get("camera") or "").strip()
    cast_line = characters if characters else "no people"
    return (
        "Create one full-canvas 16:9 black-and-white professional pencil-and-ink cinematic "
        "storyboard illustration. Do not draw a storyboard template, border, prompt text, title, "
        "caption, metadata, shot label, timecode, logo, watermark, subtitle, or readable words. "
        f"Draw only this exact moment: {action}. "
        f"Required cast: {cast_line}. {cast_contract} "
        f"Required location and state: {setting}. Preserve its environment geometry, time of day, "
        "weather, lighting, damage, and object placement across cards. "
        f"Required framing and camera: {framing}; {camera}. {role_directions[role]} "
        "Lock each recurring prop: preserve silhouette, scale, material, value, and wear. Ordinary "
        "handheld props stay one-hand/palm-sized unless the action explicitly requires large; "
        "carrying never enlarges one or makes it a two-person load. Show the required count only; "
        "never redesign, duplicate, enlarge, or substitute. Omit story props absent from this exact "
        "action, even if visible in a reference; never place them in hands, clothing, foreground, "
        "or background. If a prior reference is supplied, preserve identity, wardrobe, prop design, "
        "and line-art style; for the same location, preserve geometry, time, weather, lighting, "
        "damage, and object placement. Required location/state overrides a different background. "
        "Use a materially distinct pose, blocking, composition, and emotion for this exact action; "
        "never clone a prior panel. "
        f"{appearance_contract} {body_contract} {hand_contract} {screen_content_contract} "
        f"Use this visual direction: {brief.visual_direction}."
    )


def visual_owner_review_gate(timeline: Mapping[str, Any]) -> dict[str, Any]:
    """Return a truthful manual visual-quality HOLD for a compiled timeline.

    The worker verifies bytes, dimensions, hashes, and ordering. It does not run an
    anatomy or composition detector. This gate therefore never claims that model
    output passed subjective visual review. It also identifies prompt patterns that
    deserve extra attention without claiming that a defect was actually detected.
    """

    shots = timeline.get("shots")
    if not isinstance(shots, list) or any(not isinstance(shot, Mapping) for shot in shots):
        raise BriefValidationError("visual owner review requires the compiled shot timeline")
    risk_flags: list[dict[str, str]] = []
    for shot in shots:
        card = shot.get("storyboard_card")
        if not isinstance(card, Mapping):
            raise BriefValidationError("visual owner review shot is missing its card")
        composition = " ".join(
            str(card.get(key) or "") for key in ("framing", "camera", "action")
        )
        if (
            _VISUAL_DETAIL_FRAMING_PATTERN.search(composition)
            and _VISUAL_HAND_ACTION_PATTERN.search(composition)
        ):
            risk_flags.append(
                {
                    "shot_id": str(shot.get("shot_id") or "unknown-shot"),
                    "code": "detail_hand_or_foreground_anatomy_risk",
                }
            )
    gate: dict[str, Any] = {
        "schema": "video-studio.visual-owner-review/v1",
        "status": "pending_owner_review",
        "release_decision": "hold",
        "verification_scope": "manual_story_anatomy_identity_continuity_composition",
        "risk_flagged_shot_ids": [flag["shot_id"] for flag in risk_flags],
        "risk_flags": risk_flags,
        "required_checks": [
            "story_and_action_match",
            "human_anatomy_and_proportion",
            "character_identity_and_continuity",
            "composition_and_readability",
        ],
    }
    gate["manifest_sha256"] = sha256_json(gate)
    return gate


def _visual_alt_text(brief: ProductionBrief, shot: Mapping[str, Any]) -> str:
    card = shot.get("storyboard_card")
    if not isinstance(card, Mapping):
        raise BriefValidationError("visual storyboard shot is missing its card")
    return _clean_string(
        f"Shot {shot.get('shot_id')}, {card.get('framing')}: {card.get('action')}",
        label="visual storyboard alt text",
        maximum=700,
    )


def _visual_prompt_digest(brief: ProductionBrief, shot: Mapping[str, Any]) -> str:
    return hashlib.sha256(storyboard_panel_prompt(brief, shot).encode("utf-8")).hexdigest()


def _selected_visual_indices(shot_count: int) -> frozenset[int]:
    if shot_count <= MAX_VISUAL_PANEL_COUNT:
        return frozenset(range(shot_count))
    last = shot_count - 1
    return frozenset(
        round(index * last / (MAX_VISUAL_PANEL_COUNT - 1))
        for index in range(MAX_VISUAL_PANEL_COUNT)
    )


def _missing_visual_panel(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    if reason not in _VISUAL_MISSING_REASONS:
        reason = "generation_failed"
    return {
        "shot_id": shot["shot_id"],
        "status": "missing",
        "alt_text": _visual_alt_text(brief, shot),
        "prompt_sha256": _visual_prompt_digest(brief, shot),
        "mime_type": None,
        "width": None,
        "height": None,
        "byte_length": None,
        "content_sha256": None,
        "data_base64": None,
        "missing_reason": reason,
    }


def _available_visual_panel(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
    result: VisualPanelProviderResult,
) -> dict[str, Any]:
    image = result.image_bytes
    if (
        not isinstance(image, bytes)
        or not 100 <= len(image) <= MAX_VISUAL_PANEL_BYTES
        or result.mime_type != "image/jpeg"
        or result.width != 768
        or result.height != 432
        or not image.startswith(b"\xff\xd8")
        or not image.endswith(b"\xff\xd9")
    ):
        raise VisualPanelGenerationError("invalid_provider_asset")
    return {
        "shot_id": shot["shot_id"],
        "status": "available",
        "alt_text": _visual_alt_text(brief, shot),
        "prompt_sha256": _visual_prompt_digest(brief, shot),
        "mime_type": "image/jpeg",
        "width": 768,
        "height": 432,
        "byte_length": len(image),
        "content_sha256": hashlib.sha256(image).hexdigest(),
        "data_base64": base64.b64encode(image).decode("ascii"),
        "missing_reason": None,
    }


def _artifact_id_for_panel(index: int, shot_id: Any) -> str:
    safe_shot = re.sub(r"[^a-z0-9_-]+", "-", str(shot_id).casefold()).strip("-")
    return f"storyboard-panel-{index + 1:03d}-{safe_shot or 'shot'}"


def _external_visual_panel(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
    result: VisualPanelProviderResult,
    *,
    artifact_store: ArtifactStore,
    artifact_id: str,
    job_id: str,
) -> dict[str, Any]:
    inline = _available_visual_panel(brief, shot, result)
    stored = dict(
        artifact_store.put_bytes(
            job_id=job_id,
            artifact_id=artifact_id,
            data=result.image_bytes,
            content_type="image/jpeg",
        )
    )
    if (
        stored.get("artifact_id") != artifact_id
        or stored.get("content_type") != "image/jpeg"
        or stored.get("sha256") != inline["content_sha256"]
        or stored.get("bytes") != inline["byte_length"]
    ):
        raise VisualPanelGenerationError("invalid_provider_asset")
    inline["data_base64"] = None
    inline["artifact_id"] = artifact_id
    inline["object_name"] = stored.get("object_name")
    if not isinstance(inline["object_name"], str) or not inline["object_name"]:
        raise VisualPanelGenerationError("invalid_provider_asset")
    return inline


def _shot_character_cast_key(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
) -> tuple[str, ...]:
    scene_number = shot.get("scene_number")
    if (
        isinstance(scene_number, bool)
        or not isinstance(scene_number, int)
        or not 1 <= scene_number <= len(brief.scenes)
    ):
        return ()
    scene = brief.scenes[scene_number - 1]
    return tuple(sorted({name.casefold() for name in _shot_characters(scene, shot)}))


_VISUAL_SETTING_STATE_SUFFIX = re.compile(
    r",\s*(?:moments? later|continuous|later|dawn|morning|afternoon|evening|night|"
    r"day|sunrise|sunset|storm|stormy|rain|snow|clear|overcast)\s*$",
    re.IGNORECASE,
)


def _shot_story_scene_number(shot: Mapping[str, Any]) -> int | None:
    value = shot.get("story_scene_number", shot.get("scene_number"))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _normalized_visual_setting(
    brief: ProductionBrief,
    shot: Mapping[str, Any],
) -> str:
    scene_number = shot.get("scene_number")
    if (
        isinstance(scene_number, bool)
        or not isinstance(scene_number, int)
        or not 1 <= scene_number <= len(brief.scenes)
    ):
        return ""
    card = shot.get("storyboard_card")
    raw = card.get("setting") if isinstance(card, Mapping) else None
    setting = str(raw or brief.scenes[scene_number - 1].setting)
    setting = re.sub(r"\s+", " ", setting).strip().casefold()
    setting = re.sub(r"^back in\s+", "", setting)
    return _VISUAL_SETTING_STATE_SUFFIX.sub("", setting).strip(" ,")


def _visual_reference_panel_index(
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    next_shot: Mapping[str, Any],
    *,
    available_indices: Sequence[int],
) -> int | None:
    """Choose a prior continuity reference without leaking unrelated empty-cast art."""

    shots = timeline.get("shots")
    if not isinstance(shots, list):
        return None
    wanted_cast = _shot_character_cast_key(brief, next_shot)
    wanted_cast_set = set(wanted_cast)
    wanted_story_scene = _shot_story_scene_number(next_shot)
    wanted_setting = _normalized_visual_setting(brief, next_shot)
    ranked: list[tuple[tuple[int, int, int, int, int], int]] = []
    for index in available_indices:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(shots):
            continue
        candidate = shots[index]
        if not isinstance(candidate, Mapping):
            continue
        candidate_cast = _shot_character_cast_key(brief, candidate)
        candidate_cast_set = set(candidate_cast)
        same_story_scene = (
            wanted_story_scene is not None
            and _shot_story_scene_number(candidate) == wanted_story_scene
        )
        same_setting = bool(
            wanted_setting
            and _normalized_visual_setting(brief, candidate) == wanted_setting
        )
        if wanted_cast:
            # A full-cast reference can safely anchor a solo card, while a solo
            # or empty reference cannot define a larger cast.
            if not wanted_cast_set.issubset(candidate_cast_set):
                continue
        else:
            # Empty-cast detail cards are especially prone to stray hands and
            # figures. Use only an empty-cast reference from this story scene or
            # the same normalized setting; never the unrelated last panel.
            if candidate_cast or not (same_story_scene or same_setting):
                continue
        canonical_coverage = int(
            candidate.get("role") in {"establishing", "primary_coverage"}
        )
        exact_cast = int(candidate_cast == wanted_cast)
        score = (
            int(same_story_scene),
            int(same_setting),
            canonical_coverage,
            exact_cast,
            index,
        )
        ranked.append((score, index))
    return max(ranked)[1] if ranked else None


def build_visual_storyboard(
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    *,
    provider: VisualPanelProvider | None,
    config: AllThingsConfig,
    job_id: str,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Build every panel to private storage, or the bounded legacy inline preview."""

    shots = timeline.get("shots")
    if not isinstance(shots, list) or any(not isinstance(shot, Mapping) for shot in shots):
        raise BriefValidationError("visual storyboard requires the compiled shot timeline")
    selected = (
        frozenset(range(len(shots)))
        if artifact_store is not None
        else _selected_visual_indices(len(shots))
    )
    panels: list[dict[str, Any]] = []
    generated_reference_images: dict[int, bytes] = {}
    evidence_origin = "not_attempted"
    for index, raw_shot in enumerate(shots):
        shot = dict(raw_shot)
        if not brief.ready_for_production:
            panels.append(_missing_visual_panel(brief, shot, "held_for_clarification"))
            continue
        if provider is None:
            panels.append(_missing_visual_panel(brief, shot, "renderer_not_configured"))
            continue
        if index not in selected:
            panels.append(_missing_visual_panel(brief, shot, "panel_limit_reached"))
            continue
        reference_index = _visual_reference_panel_index(
            brief,
            timeline,
            shot,
            available_indices=tuple(generated_reference_images),
        )
        selected_reference = generated_reference_images.get(reference_index)
        try:
            result = provider.create_panel(
                storyboard_panel_prompt(brief, shot),
                shot_id=str(shot["shot_id"]),
                job_id=job_id,
                reference_image=selected_reference,
            )
            if artifact_store is None:
                panel = _available_visual_panel(brief, shot, result)
            else:
                panel = _external_visual_panel(
                    brief,
                    shot,
                    result,
                    artifact_store=artifact_store,
                    artifact_id=_artifact_id_for_panel(index, shot.get("shot_id")),
                    job_id=job_id,
                )
            origin = result.execution.get("evidence_origin")
            if origin in {"injected_test_client", "live_google_provider_response"}:
                evidence_origin = str(origin)
            generated_reference_images[index] = result.image_bytes
        except VisualPanelGenerationError as exc:
            panel = _missing_visual_panel(brief, shot, exc.code)
        except Exception:
            panel = _missing_visual_panel(brief, shot, "generation_failed")
        if artifact_store is not None and "artifact_id" not in panel:
            panel["artifact_id"] = None
            panel["object_name"] = None
        panels.append(panel)
    if artifact_store is not None:
        for panel in panels:
            panel.setdefault("artifact_id", None)
            panel.setdefault("object_name", None)
    available = sum(panel["status"] == "available" for panel in panels)
    required = len(panels)
    if not brief.ready_for_production:
        status = "not_attempted"
    elif available == required:
        status = "complete"
    elif available:
        status = "partial"
    else:
        status = "unavailable"
    if available == 0 and brief.ready_for_production:
        evidence_origin = (
            "renderer_not_configured" if provider is None else "no_surviving_provider_asset"
        )
    body: dict[str, Any] = {
        "schema": VISUAL_STORYBOARD_SCHEMA,
        "status": status,
        "verification_scope": "technical_asset_integrity_only",
        "required_panel_count": required,
        "available_panel_count": available,
        "missing_panel_count": required - available,
        "representation": (
            "private_artifact_route" if artifact_store is not None else "inline_base64"
        ),
        "renderer": {
            "provider": "Vertex AI",
            "framework": "google-genai",
            "model": config.image_model,
            "location": config.location,
            "evidence_origin": evidence_origin,
        },
        "panels": panels,
    }
    body["manifest_sha256"] = sha256_json(body)
    while (
        artifact_store is None
        and len(canonical_json(body).encode("utf-8")) > MAX_VISUAL_STORYBOARD_BYTES
    ):
        replaced = False
        for index in range(len(panels) - 1, -1, -1):
            if panels[index]["status"] == "available":
                panels[index] = _missing_visual_panel(
                    brief, shots[index], "inline_budget_exhausted"
                )
                replaced = True
                break
        if not replaced:
            raise BriefValidationError("visual storyboard exceeds its durable document budget")
        available = sum(panel["status"] == "available" for panel in panels)
        body.update(
            {
                "status": "partial" if available else "unavailable",
                "available_panel_count": available,
                "missing_panel_count": required - available,
                "panels": panels,
            }
        )
        body["manifest_sha256"] = sha256_json(
            {key: value for key, value in body.items() if key != "manifest_sha256"}
        )
    validate_visual_storyboard(body, brief=brief, timeline=timeline)
    return body


def _checkpoint_artifact_id(attempt: int, dispatch_sequence: int) -> str:
    return f"pipeline-checkpoint-a{attempt:02d}-d{dispatch_sequence:03d}"


def _safe_artifact_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one private immutable artifact descriptor without a URL."""

    expected = {"artifact_id", "object_name", "sha256", "bytes", "content_type"}
    if set(value) != expected:
        raise PipelineCheckpointError("checkpoint artifact descriptor is invalid")
    artifact_id = value.get("artifact_id")
    object_name = value.get("object_name")
    digest = value.get("sha256")
    byte_length = value.get("bytes")
    content_type = value.get("content_type")
    if (
        not isinstance(artifact_id, str)
        or not _SAFE_ID.fullmatch(artifact_id)
        or not isinstance(object_name, str)
        or not object_name.startswith("jobs/")
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or not 1 <= byte_length <= MAX_DURABLE_JOB_BYTES
        or content_type != "application/json"
    ):
        raise PipelineCheckpointError("checkpoint artifact descriptor is invalid")
    return {
        "artifact_id": artifact_id,
        "object_name": object_name,
        "sha256": digest,
        "bytes": byte_length,
        "content_type": content_type,
    }


def _validate_checkpoint_panels(
    panels: Any,
    *,
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    job_id: str,
    next_panel_index: int,
) -> list[dict[str, Any]]:
    shots = timeline.get("shots")
    if (
        not isinstance(shots, list)
        or not isinstance(panels, list)
        or next_panel_index != len(panels)
        or not 0 <= next_panel_index <= len(shots)
    ):
        raise PipelineCheckpointError("checkpoint panel coverage is invalid")
    panel_fields = {
        "shot_id",
        "status",
        "alt_text",
        "prompt_sha256",
        "mime_type",
        "width",
        "height",
        "byte_length",
        "content_sha256",
        "data_base64",
        "missing_reason",
        "artifact_id",
        "object_name",
    }
    validated: list[dict[str, Any]] = []
    for index, raw_panel in enumerate(panels):
        shot = shots[index]
        if (
            not isinstance(shot, Mapping)
            or not isinstance(raw_panel, Mapping)
            or set(raw_panel) != panel_fields
        ):
            raise PipelineCheckpointError("checkpoint panel manifest is invalid")
        panel = dict(raw_panel)
        digest = panel.get("content_sha256")
        artifact_id = panel.get("artifact_id")
        object_name = panel.get("object_name")
        if (
            panel.get("shot_id") != shot.get("shot_id")
            or panel.get("status") != "available"
            or panel.get("alt_text") != _visual_alt_text(brief, shot)
            or panel.get("prompt_sha256") != _visual_prompt_digest(brief, shot)
            or panel.get("mime_type") != "image/jpeg"
            or panel.get("width") != 768
            or panel.get("height") != 432
            or isinstance(panel.get("byte_length"), bool)
            or not isinstance(panel.get("byte_length"), int)
            or not 100 <= int(panel["byte_length"]) <= MAX_VISUAL_PANEL_BYTES
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or panel.get("data_base64") is not None
            or panel.get("missing_reason") is not None
            or artifact_id != _artifact_id_for_panel(index, shot.get("shot_id"))
            or not isinstance(object_name, str)
            or not object_name.startswith(f"jobs/{job_id}/artifacts/{digest}/")
        ):
            raise PipelineCheckpointError("checkpoint panel manifest is invalid")
        validated.append(panel)
    return validated


def _validate_checkpoint_pitch_segments(
    segments: Any,
    *,
    timeline: Mapping[str, Any],
    job_id: str,
    next_pitch_index: int,
) -> list[dict[str, Any]]:
    """Validate the exact ordered prefix of private narrated-card MP4s."""

    shots = timeline.get("shots")
    if (
        not isinstance(shots, list)
        or not isinstance(segments, list)
        or next_pitch_index != len(segments)
        or not 0 <= next_pitch_index <= len(shots)
    ):
        raise PipelineCheckpointError("checkpoint pitch coverage is invalid")
    fields = {
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
    validated: list[dict[str, Any]] = []
    for index, raw_segment in enumerate(segments, start=1):
        shot = shots[index - 1]
        if (
            not isinstance(shot, Mapping)
            or not isinstance(raw_segment, Mapping)
            or set(raw_segment) != fields
        ):
            raise PipelineCheckpointError("checkpoint pitch segment is invalid")
        segment = dict(raw_segment)
        digest = segment.get("sha256")
        object_name = segment.get("object_name")
        duration = segment.get("duration_seconds")
        byte_length = segment.get("byte_length")
        if (
            segment.get("schema") != NARRATED_PITCH_SEGMENT_SCHEMA
            or segment.get("sequence") != index
            or segment.get("shot_id") != shot.get("shot_id")
            or segment.get("artifact_id") != f"pitch-card-{index:04d}.mp4"
            or segment.get("content_type") != "video/mp4"
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(object_name, str)
            or not object_name.startswith(f"jobs/{job_id}/artifacts/{digest}/")
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or not 1 <= byte_length <= 2 * 1024 * 1024 * 1024
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or not 0 < float(duration) <= 15 * 60
        ):
            raise PipelineCheckpointError("checkpoint pitch segment is invalid")
        validated.append(segment)
    return validated


def _reference_image_from_checkpoint(
    panels: Sequence[Mapping[str, Any]],
    *,
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    next_shot: Mapping[str, Any],
    artifact_store: ArtifactStore,
    job_id: str,
) -> bytes | None:
    if not panels:
        return None
    shots = timeline.get("shots")
    if not isinstance(shots, list) or len(panels) > len(shots):
        raise PipelineCheckpointError("checkpoint reference timeline is invalid")
    selected_index = _visual_reference_panel_index(
        brief,
        timeline,
        next_shot,
        available_indices=tuple(range(len(panels))),
    )
    if selected_index is None:
        return None
    panel = panels[selected_index]
    object_name = panel.get("object_name")
    if not isinstance(object_name, str) or not object_name.startswith(
        f"jobs/{job_id}/artifacts/"
    ):
        raise PipelineCheckpointError("checkpoint reference image is invalid")
    try:
        image = artifact_store.get_bytes(object_name)
    except Exception:
        raise PipelineCheckpointError("checkpoint reference image is unavailable") from None
    if (
        not isinstance(image, bytes)
        or len(image) != panel.get("byte_length")
        or hashlib.sha256(image).hexdigest() != panel.get("content_sha256")
        or not image.startswith(b"\xff\xd8")
        or not image.endswith(b"\xff\xd9")
    ):
        raise PipelineCheckpointError("checkpoint reference image failed integrity validation")
    return image


def _generate_external_visual_chunk(
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    *,
    provider: VisualPanelProvider | None,
    artifact_store: ArtifactStore,
    config: AllThingsConfig,
    job_id: str,
    existing_panels: Sequence[Mapping[str, Any]],
    ownership_check: Callable[[], bool] | None = None,
    capacity_reservation: Callable[[], Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Generate at most one configured chunk and fail closed on any missing card."""

    if not brief.ready_for_production or provider is None:
        raise VisualPanelGenerationError(
            "renderer_not_configured" if provider is None else "generation_failed"
        )
    shots = timeline.get("shots")
    if not isinstance(shots, list):
        raise BriefValidationError("visual storyboard requires the compiled shot timeline")
    panels = _validate_checkpoint_panels(
        list(existing_panels),
        brief=brief,
        timeline=timeline,
        job_id=job_id,
        next_panel_index=len(existing_panels),
    )
    stop = min(len(shots), len(panels) + config.visual_panels_per_dispatch)
    evidence_origin = "not_attempted"
    for index in range(len(panels), stop):
        if ownership_check is not None and not ownership_check():
            raise PipelineWorkStopped("visual chunk ownership ended")
        if capacity_reservation is None:
            raise PipelineCheckpointError(
                "visual provider call requires the project-wide capacity gate"
            )
        try:
            granted, retry_after, window_active = _validate_visual_capacity_result(
                capacity_reservation()
            )
        except PipelineCheckpointError:
            raise
        except Exception:
            # A gate outage must fail closed before the provider call.  The
            # durable scheduler gets one bounded capacity deferral rather than
            # bypassing the project-wide request limit.
            raise _VisualCapacityDeferred(
                panels,
                evidence_origin=evidence_origin,
                retry_after_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            ) from None
        if not granted:
            raise _VisualCapacityDeferred(
                panels,
                evidence_origin=evidence_origin,
                retry_after_seconds=retry_after,
                window_active=window_active,
            )
        # Cancellation after a reservation wastes a bounded slot but cannot
        # oversubscribe the provider quota or publish partial media.
        if ownership_check is not None and not ownership_check():
            raise PipelineWorkStopped("visual chunk ownership ended")
        shot = shots[index]
        if not isinstance(shot, Mapping):
            raise BriefValidationError("visual storyboard shot is invalid")
        reference_image = _reference_image_from_checkpoint(
            panels,
            brief=brief,
            timeline=timeline,
            next_shot=shot,
            artifact_store=artifact_store,
            job_id=job_id,
        )
        try:
            result = provider.create_panel(
                storyboard_panel_prompt(brief, shot),
                shot_id=str(shot["shot_id"]),
                job_id=job_id,
                reference_image=reference_image,
            )
            # Provider calls can be slow.  Fence cancellation or a reclaimed
            # lease again before producing a durable artifact from the result.
            if ownership_check is not None and not ownership_check():
                raise PipelineWorkStopped("visual chunk ownership ended")
            panel = _external_visual_panel(
                brief,
                shot,
                result,
                artifact_store=artifact_store,
                artifact_id=_artifact_id_for_panel(index, shot.get("shot_id")),
                job_id=job_id,
            )
        except VisualPanelGenerationError as exc:
            # A quota response may arrive after the first panel in this bounded
            # chunk.  Carry only already validated panel descriptors to the
            # orchestrator; provider text and exceptions never cross this seam.
            if exc.code == "quota_or_rate_limited":
                if ownership_check is not None and not ownership_check():
                    raise PipelineWorkStopped("visual chunk ownership ended") from None
                raise _VisualQuotaDeferred(
                    panels,
                    evidence_origin=evidence_origin,
                ) from None
            raise
        except PipelineWorkStopped:
            raise
        except Exception:
            raise VisualPanelGenerationError("generation_failed") from None
        origin = result.execution.get("evidence_origin")
        if origin in {"injected_test_client", "live_google_provider_response"}:
            evidence_origin = str(origin)
        panels.append(panel)
        if ownership_check is not None and not ownership_check():
            raise PipelineWorkStopped("visual chunk ownership ended")
    return panels, evidence_origin


def _complete_external_visual_storyboard(
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    *,
    panels: Sequence[Mapping[str, Any]],
    config: AllThingsConfig,
    job_id: str,
    evidence_origin: str,
) -> dict[str, Any]:
    shots = timeline.get("shots")
    required = len(shots) if isinstance(shots, list) else -1
    complete_panels = _validate_checkpoint_panels(
        list(panels),
        brief=brief,
        timeline=timeline,
        job_id=job_id,
        next_panel_index=len(panels),
    )
    if required < 0 or len(complete_panels) != required:
        raise PipelineCheckpointError("visual checkpoint is not complete")
    body: dict[str, Any] = {
        "schema": VISUAL_STORYBOARD_SCHEMA,
        "status": "complete",
        "verification_scope": "technical_asset_integrity_only",
        "required_panel_count": required,
        "available_panel_count": required,
        "missing_panel_count": 0,
        "representation": "private_artifact_route",
        "renderer": {
            "provider": "Vertex AI",
            "framework": "google-genai",
            "model": config.image_model,
            "location": config.location,
            "evidence_origin": evidence_origin,
        },
        "panels": complete_panels,
    }
    body["manifest_sha256"] = sha256_json(body)
    validate_visual_storyboard(body, brief=brief, timeline=timeline)
    return body


def _write_pipeline_checkpoint(
    *,
    artifact_store: ArtifactStore,
    job_id: str,
    attempt: int,
    dispatch_sequence: int,
    phase: str,
    request_sha256: str,
    target_digest: str,
    brief: ProductionBrief,
    storyboard_package: Mapping[str, Any],
    provider_execution: Mapping[str, Any],
    panels: Sequence[Mapping[str, Any]],
    pitch_segments: Sequence[Mapping[str, Any]],
    visual_evidence_origin: str,
    previous_checkpoint_sha256: str | None,
    max_dispatches: int,
    quota_deferrals_used: int,
    capacity_waits_used: int,
    visual_capacity_window: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Store one content-addressed checkpoint and return its bounded pointer."""

    timeline = storyboard_package.get("timeline")
    if not isinstance(timeline, Mapping):
        raise PipelineCheckpointError("checkpoint timeline is invalid")
    next_panel_index = len(panels)
    validated_panels = _validate_checkpoint_panels(
        list(panels),
        brief=brief,
        timeline=timeline,
        job_id=job_id,
        next_panel_index=next_panel_index,
    )
    required_panel_count = len(timeline.get("shots", []))
    next_pitch_index = len(pitch_segments)
    validated_pitch_segments = _validate_checkpoint_pitch_segments(
        list(pitch_segments),
        timeline=timeline,
        job_id=job_id,
        next_pitch_index=next_pitch_index,
    )
    required_pitch_count = required_panel_count
    validated_capacity_window = (
        _validate_visual_capacity_window(visual_capacity_window)
        if visual_capacity_window is not None
        else None
    )
    if (
        phase
        not in {
            "visual_storyboard",
            "narrated_pitch",
            "narrated_pitch_finalize",
        }
        or (
            phase == "visual_storyboard"
            and (
                next_panel_index >= required_panel_count
                or next_pitch_index != 0
            )
        )
        or (
            phase == "narrated_pitch"
            and (
                next_panel_index != required_panel_count
                or next_pitch_index >= required_pitch_count
            )
        )
        or (
            phase == "narrated_pitch_finalize"
            and (
                next_panel_index != required_panel_count
                or next_pitch_index != required_pitch_count
            )
        )
        or not 1 <= dispatch_sequence < max_dispatches <= MAX_PIPELINE_DISPATCHES
        or isinstance(quota_deferrals_used, bool)
        or not isinstance(quota_deferrals_used, int)
        or not 0 <= quota_deferrals_used <= MAX_VISUAL_QUOTA_MAX_DEFERRALS
        or isinstance(capacity_waits_used, bool)
        or not isinstance(capacity_waits_used, int)
        or not 0
        <= capacity_waits_used
        <= math.ceil(required_panel_count / VISUAL_CAPACITY_REQUEST_LIMIT)
        + MAX_VISUAL_QUOTA_MAX_DEFERRALS
        or (phase == "visual_storyboard")
        != (validated_capacity_window is not None)
        or re.fullmatch(r"[0-9a-f]{64}", request_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", target_digest) is None
        or (
            previous_checkpoint_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", previous_checkpoint_sha256) is None
        )
    ):
        raise PipelineCheckpointError("checkpoint bounds are invalid")
    package = validate_storyboard_package(storyboard_package)
    body: dict[str, Any] = {
        "schema": PIPELINE_CHECKPOINT_SCHEMA,
        "job_id": job_id,
        "application_attempt": attempt,
        "dispatch_sequence": dispatch_sequence,
        "phase": phase,
        "request_sha256": request_sha256,
        "target_digest": target_digest,
        "brief_sha256": sha256_json(brief.to_dict()),
        "storyboard_manifest_sha256": package["manifest_sha256"],
        "brief": brief.to_dict(),
        "storyboard_package": package,
        "provider_execution": dict(provider_execution),
        "panels": validated_panels,
        "pitch_segments": validated_pitch_segments,
        "visual_evidence_origin": visual_evidence_origin,
        "next_panel_index": next_panel_index,
        "required_panel_count": required_panel_count,
        "next_pitch_index": next_pitch_index,
        "required_pitch_count": required_pitch_count,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "max_dispatches": max_dispatches,
        "quota_deferrals_used": quota_deferrals_used,
        "capacity_waits_used": capacity_waits_used,
        "visual_capacity_window": validated_capacity_window,
    }
    body["manifest_sha256"] = sha256_json(body)
    encoded = canonical_json(body).encode("utf-8")
    if len(encoded) > MAX_DURABLE_JOB_BYTES:
        raise PipelineCheckpointError("checkpoint exceeds its durable size budget")
    artifact_id = _checkpoint_artifact_id(attempt, dispatch_sequence)
    stored = _safe_artifact_descriptor(
        dict(
            artifact_store.put_bytes(
                job_id=job_id,
                artifact_id=artifact_id,
                data=encoded,
                content_type="application/json",
            )
        )
    )
    if (
        stored["artifact_id"] != artifact_id
        or stored["sha256"] != hashlib.sha256(encoded).hexdigest()
        or stored["bytes"] != len(encoded)
        or not str(stored["object_name"]).startswith(f"jobs/{job_id}/artifacts/")
    ):
        raise PipelineCheckpointError("stored checkpoint evidence is invalid")
    continuation: dict[str, Any] = {
        "schema": PIPELINE_CONTINUATION_SCHEMA,
        "status": "pending",
        "application_attempt": attempt,
        "dispatch_sequence": dispatch_sequence,
        "phase": phase,
        "next_panel_index": next_panel_index,
        "required_panel_count": required_panel_count,
        "next_pitch_index": next_pitch_index,
        "required_pitch_count": required_pitch_count,
        "dispatches_used": dispatch_sequence,
        "max_dispatches": max_dispatches,
        "quota_deferrals_used": quota_deferrals_used,
        "capacity_waits_used": capacity_waits_used,
        "visual_capacity_window": validated_capacity_window,
        "checkpoint_sha256": stored["sha256"],
        "checkpoint": stored,
    }
    continuation["manifest_sha256"] = sha256_json(continuation)
    return continuation


def _load_pipeline_checkpoint(
    continuation_value: Any,
    *,
    artifact_store: ArtifactStore,
    job_id: str,
    attempt: int,
    dispatch_sequence: int,
    source_message: str,
    request_sha256: str,
    target_digest: str,
) -> dict[str, Any]:
    expected_continuation_fields = {
        "schema",
        "status",
        "application_attempt",
        "dispatch_sequence",
        "phase",
        "next_panel_index",
        "required_panel_count",
        "next_pitch_index",
        "required_pitch_count",
        "dispatches_used",
        "max_dispatches",
        "quota_deferrals_used",
        "capacity_waits_used",
        "visual_capacity_window",
        "checkpoint_sha256",
        "checkpoint",
        "manifest_sha256",
    }
    if (
        not isinstance(continuation_value, Mapping)
        or set(continuation_value) != expected_continuation_fields
    ):
        raise PipelineCheckpointError("continuation pointer is invalid")
    continuation = dict(continuation_value)
    supplied_continuation_digest = continuation.pop("manifest_sha256")
    if (
        not isinstance(supplied_continuation_digest, str)
        or supplied_continuation_digest != sha256_json(continuation)
        or continuation.get("schema") != PIPELINE_CONTINUATION_SCHEMA
        or continuation.get("status") != "pending"
        or continuation.get("application_attempt") != attempt
        or continuation.get("dispatch_sequence") != dispatch_sequence
        or continuation.get("dispatches_used") != dispatch_sequence
        or not 1 <= dispatch_sequence < int(continuation.get("max_dispatches", 0))
        or int(continuation.get("max_dispatches", 0)) > MAX_PIPELINE_DISPATCHES
        or isinstance(continuation.get("quota_deferrals_used"), bool)
        or not isinstance(continuation.get("quota_deferrals_used"), int)
        or not 0
        <= int(continuation.get("quota_deferrals_used", -1))
        <= MAX_VISUAL_QUOTA_MAX_DEFERRALS
        or isinstance(continuation.get("capacity_waits_used"), bool)
        or not isinstance(continuation.get("capacity_waits_used"), int)
        or int(continuation.get("capacity_waits_used", -1)) < 0
    ):
        raise PipelineCheckpointError("continuation pointer failed integrity validation")
    descriptor = _safe_artifact_descriptor(dict(continuation["checkpoint"]))
    if (
        descriptor["artifact_id"] != _checkpoint_artifact_id(attempt, dispatch_sequence)
        or descriptor["sha256"] != continuation.get("checkpoint_sha256")
        or not str(descriptor["object_name"]).startswith(f"jobs/{job_id}/artifacts/")
    ):
        raise PipelineCheckpointError("continuation checkpoint binding is invalid")
    try:
        encoded = artifact_store.get_bytes(str(descriptor["object_name"]))
    except Exception:
        raise PipelineCheckpointError("continuation checkpoint is unavailable") from None
    if (
        not isinstance(encoded, bytes)
        or len(encoded) != descriptor["bytes"]
        or hashlib.sha256(encoded).hexdigest() != descriptor["sha256"]
    ):
        raise PipelineCheckpointError("continuation checkpoint bytes are invalid")
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PipelineCheckpointError("continuation checkpoint JSON is invalid") from None
    expected_checkpoint_fields = {
        "schema",
        "job_id",
        "application_attempt",
        "dispatch_sequence",
        "phase",
        "request_sha256",
        "target_digest",
        "brief_sha256",
        "storyboard_manifest_sha256",
        "brief",
        "storyboard_package",
        "provider_execution",
        "panels",
        "pitch_segments",
        "visual_evidence_origin",
        "next_panel_index",
        "required_panel_count",
        "next_pitch_index",
        "required_pitch_count",
        "previous_checkpoint_sha256",
        "max_dispatches",
        "quota_deferrals_used",
        "capacity_waits_used",
        "visual_capacity_window",
        "manifest_sha256",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected_checkpoint_fields:
        raise PipelineCheckpointError("continuation checkpoint fields are invalid")
    checkpoint = dict(raw)
    supplied_checkpoint_digest = checkpoint.pop("manifest_sha256")
    if (
        not isinstance(supplied_checkpoint_digest, str)
        or supplied_checkpoint_digest != sha256_json(checkpoint)
        or checkpoint.get("schema") != PIPELINE_CHECKPOINT_SCHEMA
        or checkpoint.get("job_id") != job_id
        or checkpoint.get("application_attempt") != attempt
        or checkpoint.get("dispatch_sequence") != dispatch_sequence
        or checkpoint.get("phase") != continuation.get("phase")
        or checkpoint.get("request_sha256") != request_sha256
        or checkpoint.get("target_digest") != target_digest
        or checkpoint.get("next_panel_index") != continuation.get("next_panel_index")
        or checkpoint.get("required_panel_count")
        != continuation.get("required_panel_count")
        or checkpoint.get("next_pitch_index")
        != continuation.get("next_pitch_index")
        or checkpoint.get("required_pitch_count")
        != continuation.get("required_pitch_count")
        or checkpoint.get("max_dispatches") != continuation.get("max_dispatches")
        or checkpoint.get("quota_deferrals_used")
        != continuation.get("quota_deferrals_used")
        or checkpoint.get("capacity_waits_used")
        != continuation.get("capacity_waits_used")
        or checkpoint.get("visual_capacity_window")
        != continuation.get("visual_capacity_window")
        or sha256_json({"message": source_message}) != request_sha256
    ):
        raise PipelineCheckpointError("continuation checkpoint identity is invalid")
    raw_brief = checkpoint.get("brief")
    if not isinstance(raw_brief, Mapping) or raw_brief.get("schema") != BRIEF_SCHEMA:
        raise PipelineCheckpointError("continuation brief is invalid")
    try:
        brief = ProductionBrief.from_mapping(
            {key: value for key, value in raw_brief.items() if key != "schema"}
        )
        if sha256_json(brief.to_dict()) != checkpoint.get("brief_sha256"):
            raise PipelineCheckpointError("continuation brief digest is invalid")
        storyboard_package = validate_storyboard_package(checkpoint["storyboard_package"])
    except BriefValidationError:
        raise PipelineCheckpointError("continuation production plan is invalid") from None
    if (
        storyboard_package.get("manifest_sha256")
        != checkpoint.get("storyboard_manifest_sha256")
        or storyboard_package.get("manifest_sha256")
        != build_storyboard_package(brief).get("manifest_sha256")
    ):
        raise PipelineCheckpointError("continuation production plan binding is invalid")
    timeline = storyboard_package.get("timeline")
    if not isinstance(timeline, Mapping):
        raise PipelineCheckpointError("continuation timeline is invalid")
    next_panel_index = checkpoint.get("next_panel_index")
    if isinstance(next_panel_index, bool) or not isinstance(next_panel_index, int):
        raise PipelineCheckpointError("continuation panel offset is invalid")
    panels = _validate_checkpoint_panels(
        checkpoint.get("panels"),
        brief=brief,
        timeline=timeline,
        job_id=job_id,
        next_panel_index=next_panel_index,
    )
    required = len(timeline.get("shots", []))
    next_pitch_index = checkpoint.get("next_pitch_index")
    if isinstance(next_pitch_index, bool) or not isinstance(next_pitch_index, int):
        raise PipelineCheckpointError("continuation pitch offset is invalid")
    pitch_segments = _validate_checkpoint_pitch_segments(
        checkpoint.get("pitch_segments"),
        timeline=timeline,
        job_id=job_id,
        next_pitch_index=next_pitch_index,
    )
    phase = checkpoint.get("phase")
    raw_capacity_window = checkpoint.get("visual_capacity_window")
    capacity_window = (
        _validate_visual_capacity_window(raw_capacity_window)
        if raw_capacity_window is not None
        else None
    )
    if (
        checkpoint.get("required_panel_count") != required
        or checkpoint.get("required_pitch_count") != required
        or phase
        not in {
            "visual_storyboard",
            "narrated_pitch",
            "narrated_pitch_finalize",
        }
        or (phase == "visual_storyboard") != (capacity_window is not None)
        or int(checkpoint.get("capacity_waits_used", -1))
        > math.ceil(required / VISUAL_CAPACITY_REQUEST_LIMIT)
        + MAX_VISUAL_QUOTA_MAX_DEFERRALS
        or (
            phase == "visual_storyboard"
            and (next_panel_index >= required or next_pitch_index != 0)
        )
        or (
            phase == "narrated_pitch"
            and (next_panel_index != required or next_pitch_index >= required)
        )
        or (
            phase == "narrated_pitch_finalize"
            and (next_panel_index != required or next_pitch_index != required)
        )
        or not isinstance(checkpoint.get("provider_execution"), Mapping)
        or checkpoint.get("visual_evidence_origin")
        not in {
            "not_attempted",
            "injected_test_client",
            "live_google_provider_response",
        }
    ):
        raise PipelineCheckpointError("continuation phase is invalid")
    checkpoint["manifest_sha256"] = supplied_checkpoint_digest
    checkpoint["brief_object"] = brief
    checkpoint["panels"] = panels
    checkpoint["pitch_segments"] = pitch_segments
    checkpoint["visual_capacity_window"] = capacity_window
    return checkpoint


def fit_visual_storyboard_to_job_budget(
    visual_storyboard: Mapping[str, Any],
    *,
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
    record_without_visual: Mapping[str, Any],
) -> dict[str, Any]:
    """Fit optional inline panels inside the conservative durable-job budget.

    Request text is discarded on successful completion, but a complex plan and
    six inline planning images can still approach Firestore's document ceiling.
    This function removes the last optional image first and records an explicit
    ``inline_budget_exhausted`` placeholder; it never truncates the audited plan.
    """

    body = dict(visual_storyboard)
    panels = [dict(panel) for panel in body.get("panels", [])]
    shots = timeline.get("shots")
    if not isinstance(shots, list) or len(shots) != len(panels):
        raise BriefValidationError("visual storyboard cannot be fitted without its timeline")

    while True:
        projected = {**dict(record_without_visual), "visual_storyboard": body}
        if len(canonical_json(projected).encode("utf-8")) <= MAX_DURABLE_JOB_BYTES:
            validate_visual_storyboard(body, brief=brief, timeline=timeline)
            return body
        replacement_index = next(
            (
                index
                for index in range(len(panels) - 1, -1, -1)
                if panels[index].get("status") == "available"
            ),
            None,
        )
        if replacement_index is None:
            raise BriefValidationError("completed job exceeds the durable document size budget")
        panels[replacement_index] = _missing_visual_panel(
            brief,
            shots[replacement_index],
            "inline_budget_exhausted",
        )
        available = sum(panel.get("status") == "available" for panel in panels)
        required = len(panels)
        body.update(
            {
                "status": "partial" if available else "unavailable",
                "available_panel_count": available,
                "missing_panel_count": required - available,
                "panels": panels,
            }
        )
        body["manifest_sha256"] = sha256_json(
            {key: value for key, value in body.items() if key != "manifest_sha256"}
        )


def validate_visual_storyboard(
    value: Any,
    *,
    brief: ProductionBrief,
    timeline: Mapping[str, Any],
) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "status",
        "verification_scope",
        "required_panel_count",
        "available_panel_count",
        "missing_panel_count",
        "representation",
        "renderer",
        "panels",
        "manifest_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise BriefValidationError("visual storyboard fields are incomplete or unsupported")
    manifest = dict(value)
    supplied_digest = manifest.pop("manifest_sha256")
    if not isinstance(supplied_digest, str) or supplied_digest != sha256_json(manifest):
        raise BriefValidationError("visual storyboard manifest digest is invalid")
    shots = timeline.get("shots")
    panels = value.get("panels")
    if not isinstance(shots, list) or not isinstance(panels, list) or len(panels) != len(shots):
        raise BriefValidationError("visual storyboard panel count does not match the timeline")
    renderer = value.get("renderer")
    if not isinstance(renderer, Mapping) or set(renderer) != {
        "provider",
        "framework",
        "model",
        "location",
        "evidence_origin",
    } or any(not isinstance(item, str) or not item for item in renderer.values()):
        raise BriefValidationError("visual storyboard renderer evidence is invalid")
    representation = value.get("representation")
    if representation not in {"inline_base64", "private_artifact_route"}:
        raise BriefValidationError("visual storyboard representation is invalid")
    panel_fields = {
        "shot_id",
        "status",
        "alt_text",
        "prompt_sha256",
        "mime_type",
        "width",
        "height",
        "byte_length",
        "content_sha256",
        "data_base64",
        "missing_reason",
    }
    if representation == "private_artifact_route":
        panel_fields |= {"artifact_id", "object_name"}
    available = 0
    for shot, panel in zip(shots, panels):
        if not isinstance(shot, Mapping) or not isinstance(panel, Mapping) or set(panel) != panel_fields:
            raise BriefValidationError("visual storyboard panel fields are invalid")
        if (
            panel.get("shot_id") != shot.get("shot_id")
            or panel.get("alt_text") != _visual_alt_text(brief, shot)
            or panel.get("prompt_sha256") != _visual_prompt_digest(brief, shot)
        ):
            raise BriefValidationError("visual storyboard panel identity is invalid")
        if panel.get("status") == "available":
            if representation == "inline_base64":
                try:
                    image = base64.b64decode(str(panel.get("data_base64")), validate=True)
                except Exception as exc:
                    raise BriefValidationError("visual storyboard image encoding is invalid") from exc
                valid_asset = (
                    panel.get("byte_length") == len(image)
                    and panel.get("content_sha256") == hashlib.sha256(image).hexdigest()
                    and image.startswith(b"\xff\xd8")
                    and image.endswith(b"\xff\xd9")
                )
            else:
                valid_asset = (
                    panel.get("data_base64") is None
                    and isinstance(panel.get("artifact_id"), str)
                    and bool(_SAFE_ID.fullmatch(str(panel.get("artifact_id"))))
                    and isinstance(panel.get("object_name"), str)
                    and str(panel.get("object_name")).startswith("jobs/")
                    and isinstance(panel.get("content_sha256"), str)
                    and bool(re.fullmatch(r"[0-9a-f]{64}", str(panel.get("content_sha256"))))
                )
            if (
                panel.get("mime_type") != "image/jpeg"
                or panel.get("width") != 768
                or panel.get("height") != 432
                or not isinstance(panel.get("byte_length"), int)
                or not 100 <= int(panel.get("byte_length")) <= MAX_VISUAL_PANEL_BYTES
                or panel.get("missing_reason") is not None
                or not valid_asset
            ):
                raise BriefValidationError("visual storyboard image asset is invalid")
            available += 1
        elif panel.get("status") == "missing":
            if panel.get("missing_reason") not in _VISUAL_MISSING_REASONS or any(
                panel.get(key) is not None
                for key in {
                    "mime_type",
                    "width",
                    "height",
                    "byte_length",
                    "content_sha256",
                    "data_base64",
                    *( {"artifact_id", "object_name"} if representation == "private_artifact_route" else set() ),
                }
            ):
                raise BriefValidationError("visual storyboard missing-panel evidence is invalid")
        else:
            raise BriefValidationError("visual storyboard panel status is invalid")
    required = len(shots)
    expected_status = (
        "not_attempted"
        if not brief.ready_for_production
        else "complete"
        if available == required
        else "partial"
        if available
        else "unavailable"
    )
    if (
        value.get("schema") != VISUAL_STORYBOARD_SCHEMA
        or value.get("verification_scope") != "technical_asset_integrity_only"
        or value.get("representation") != representation
        or value.get("status") != expected_status
        or value.get("required_panel_count") != required
        or value.get("available_panel_count") != available
        or value.get("missing_panel_count") != required - available
        or len(canonical_json(value).encode("utf-8")) > MAX_VISUAL_STORYBOARD_BYTES
    ):
        raise BriefValidationError("visual storyboard summary is invalid")
    return dict(value)


class JobRepository(Protocol):
    def admit_submission(
        self,
        record: Mapping[str, Any],
        *,
        now: str,
        cooldown_seconds: int,
        window_seconds: int,
        max_jobs: int,
    ) -> Mapping[str, Any]:
        ...

    def create(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def get(self, job_id: str) -> Mapping[str, Any]:
        ...

    def claim(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        lease_expires_at: str,
        now: str,
    ) -> Mapping[str, Any] | None:
        ...

    def continue_job(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        cancelled_patch: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def defer_claimed(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        cancelled_patch: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def confirm_continuation_dispatch(
        self,
        job_id: str,
        dispatch: Mapping[str, Any],
        *,
        attempt: int,
        dispatch_sequence: int,
        pending_manifest_sha256: str,
    ) -> Mapping[str, Any]:
        ...

    def reserve_visual_request(
        self,
        *,
        now: str,
        window_seconds: int,
        max_requests: int,
        reservation_token: str,
    ) -> Mapping[str, Any]:
        ...

    def prepare_visual_window(
        self,
        *,
        now: str,
        reservation_token: str,
        window_seconds: int,
        max_requests: int,
    ) -> Mapping[str, Any]:
        ...

    def complete_visual_window(
        self,
        *,
        now: str,
        reservation_token: str,
        window_seconds: int,
        max_requests: int,
    ) -> Mapping[str, Any]:
        ...

    def update(self, job_id: str, patch: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def update_claimed(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
        lease_token: str,
    ) -> Mapping[str, Any]:
        ...

    def finalize(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
        lease_token: str,
        cancelled_patch: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def mark_dispatch_failed(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        attempt: int,
    ) -> Mapping[str, Any]:
        ...

    def request_cancel(self, job_id: str, *, now: str) -> Mapping[str, Any]:
        ...

    def prepare_retry(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        now: str,
        cooldown_seconds: int,
        window_seconds: int,
        max_jobs: int,
    ) -> Mapping[str, Any]:
        ...

    def recent_success_durations(self, *, limit: int = 20) -> Sequence[float]:
        ...


class JobDispatcher(Protocol):
    def enqueue(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int,
        delay_seconds: int = 0,
        scheduled_epoch_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        ...


def eta_payload(durations: Sequence[float], *, progress: int) -> dict[str, Any]:
    valid = sorted(
        float(item)
        for item in durations
        if isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(float(item))
        and float(item) > 0
    )
    if not valid:
        return {
            "available": False,
            "low_seconds": None,
            "high_seconds": None,
            "sample_count": 0,
            "basis": "no_completed_live_jobs",
        }
    median = statistics.median(valid)
    remaining = max(0.0, min(1.0, (100 - progress) / 100))
    low = max(0, round(median * remaining * 0.75))
    high = max(low, round(median * remaining * 1.50))
    return {
        "available": True,
        "low_seconds": low,
        "high_seconds": high,
        "sample_count": len(valid),
        "basis": "completed_live_job_durations",
    }


def public_job(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded, secret-free job representation exposed by the API."""

    allowed = {
        "schema",
        "job_id",
        "parent_job_id",
        "request_sha256",
        "state",
        "stage",
        "progress",
        "attempt",
        "max_attempts",
        "cancel_requested",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "duration_seconds",
        "eta",
        "brief",
        "storyboard_package",
        "visual_storyboard",
        "pitch_preview",
        "error",
        "dispatch",
        "execution",
        "target",
        "worker_claim_count",
        "lease_expires_at",
        "dispatch_sequence",
        "max_dispatches",
        "continuation",
        "input_retention",
        "record_expires_at",
    }
    result = {key: record.get(key) for key in sorted(allowed) if key in record}
    continuation = result.get("continuation")
    if isinstance(continuation, Mapping):
        # Artifact object names and checkpoint digests are internal worker
        # capabilities, not part of the public polling contract.
        public_fields = {
            "schema",
            "status",
            "application_attempt",
            "dispatch_sequence",
            "phase",
            "next_panel_index",
            "required_panel_count",
            "next_pitch_index",
            "required_pitch_count",
            "dispatches_used",
            "max_dispatches",
            "quota_deferrals_used",
            "capacity_waits_used",
        }
        public_continuation = {
            key: continuation[key]
            for key in sorted(public_fields)
            if key in continuation
        }
        public_continuation["manifest_sha256"] = sha256_json(public_continuation)
        result["continuation"] = public_continuation
    return result


def _canonical_diagnostic_code(
    value: Any,
    allowed_codes: frozenset[str],
) -> str | None:
    if type(value) is not str:
        return None
    return next((code for code in allowed_codes if value == code), None)


def _visual_storyboard_incomplete_code(
    visual_storyboard: Mapping[str, Any],
) -> str:
    """Reduce missing panels to one non-sensitive, allowlisted failure code."""

    panels = visual_storyboard.get("panels")
    if not isinstance(panels, list):
        return "mixed_panel_failures"
    reasons: set[str] = set()
    for panel in panels:
        if not isinstance(panel, Mapping):
            return "mixed_panel_failures"
        if panel.get("status") != "missing":
            continue
        reason = _canonical_diagnostic_code(
            panel.get("missing_reason"),
            VisualPanelGenerationError.ALLOWED_CODES,
        )
        if reason is None or reason == "mixed_panel_failures":
            return "mixed_panel_failures"
        reasons.add(reason)
    return next(iter(reasons)) if len(reasons) == 1 else "mixed_panel_failures"


def _visual_panel_diagnostic_code(exc: Exception) -> str | None:
    if type(exc) is not VisualPanelGenerationError:
        return None
    return _canonical_diagnostic_code(
        exc.code,
        VisualPanelGenerationError.ALLOWED_CODES,
    )


def _narrated_pitch_render_diagnostic_code(exc: Exception) -> str | None:
    """Return only a canonical code from the typed cloud-media render error."""

    # The lazy import keeps this orchestration module independent of the concrete
    # renderer on successful and non-media paths.
    from .all_things_cloud_media import NarratedPitchRenderError

    if type(exc) is not NarratedPitchRenderError:
        return None
    return _canonical_diagnostic_code(
        exc.code,
        NARRATED_PITCH_RENDER_DIAGNOSTIC_CODES,
    )


class AllThingsJobService:
    """Idempotent natural-chat to structured-brief job orchestration."""

    def __init__(
        self,
        *,
        config: AllThingsConfig,
        repository: JobRepository,
        dispatcher: JobDispatcher | None = None,
        provider: BriefProvider | None = None,
        visual_provider: VisualPanelProvider | None = None,
        artifact_store: ArtifactStore | None = None,
        narrated_pitch_renderer: NarratedPitchRenderer | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.dispatcher = dispatcher
        self.provider = provider
        self.visual_provider = visual_provider
        self.artifact_store = artifact_store
        self.narrated_pitch_renderer = narrated_pitch_renderer

    @staticmethod
    def _message(value: Any) -> str:
        if not isinstance(value, str):
            raise AllThingsError("message must be text")
        cleaned = value.strip()
        try:
            encoded_length = len(cleaned.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise AllThingsError("message must contain valid Unicode text") from exc
        if not cleaned or len(cleaned) > MAX_MESSAGE_CHARS or encoded_length > MAX_MESSAGE_BYTES:
            raise AllThingsError(
                f"message must contain 1-{MAX_MESSAGE_CHARS} characters and no more "
                f"than {MAX_MESSAGE_BYTES} UTF-8 bytes"
            )
        return cleaned

    def submit(self, message: str) -> dict[str, Any]:
        self.config.assert_valid(require_dispatch=True)
        if self.dispatcher is None:
            raise ConfigurationError("the API service has no Cloud Tasks dispatcher")
        cleaned = self._message(message)
        now = iso_now()
        record_expires_at = (
            _parse_time(now) + timedelta(seconds=self.config.job_retention_seconds)
        ).isoformat()
        job_id = str(uuid4())
        record: dict[str, Any] = {
            "schema": JOB_SCHEMA,
            "job_id": job_id,
            "parent_job_id": None,
            "message": cleaned,
            "input_retention": "bounded_retry_until_record_expiry",
            "record_expires_at": record_expires_at,
            "request_sha256": sha256_json({"message": cleaned}),
            "state": JobState.QUEUED.value,
            "stage": "waiting_for_cloud_task",
            "progress": 0,
            "attempt": 1,
            "max_attempts": MAX_ATTEMPTS,
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "eta": eta_payload(self.repository.recent_success_durations(), progress=0),
            "brief": None,
            "storyboard_package": None,
            "visual_storyboard": None,
            "pitch_preview": None,
            "error": None,
            "dispatch": None,
            "execution": None,
            "worker_claim_count": 0,
            "lease_token": None,
            "lease_expires_at": None,
            "dispatch_sequence": 0,
            "max_dispatches": None,
            "continuation": None,
            "pending_dispatch": None,
            "target": self.config.safe_dict(),
            "target_digest": self.config.target_digest(),
        }
        # Admission and job creation are one repository transaction.  A
        # process crash can therefore leave neither an orphan active slot nor
        # an accepted job that bypassed the project-wide four-job bound.
        self.repository.admit_submission(
            record,
            now=now,
            cooldown_seconds=self.config.admission_cooldown_seconds,
            window_seconds=self.config.admission_window_seconds,
            max_jobs=self.config.admission_max_jobs,
        )
        try:
            dispatch = dict(
                self.dispatcher.enqueue(job_id, attempt=1, dispatch_sequence=0)
            )
        except Exception as exc:
            failed = self.repository.mark_dispatch_failed(
                job_id,
                {
                    "state": JobState.FAILED.value,
                    "stage": "dispatch_failed",
                    "updated_at": iso_now(),
                    "completed_at": iso_now(),
                    "error": {
                        "code": "cloud_tasks_dispatch_failed",
                        "type": type(exc).__name__,
                        "retryable": True,
                    },
                },
                attempt=1,
            )
            return public_job(failed)
        saved = self.repository.update(
            job_id,
            {"dispatch": dispatch, "updated_at": iso_now()},
        )
        return public_job(saved)

    def status(self, job_id: str) -> dict[str, Any]:
        return public_job(self.repository.get(job_id))

    def cancel(self, job_id: str) -> dict[str, Any]:
        return public_job(self.repository.request_cancel(job_id, now=iso_now()))

    def retry(self, job_id: str) -> dict[str, Any]:
        self.config.assert_valid(require_dispatch=True)
        if self.dispatcher is None:
            raise ConfigurationError("the API service has no Cloud Tasks dispatcher")
        now = iso_now()
        record_expires_at = (
            _parse_time(now) + timedelta(seconds=self.config.job_retention_seconds)
        ).isoformat()
        durations = self.repository.recent_success_durations()
        retried = self.repository.prepare_retry(
            job_id,
            {
                "state": JobState.QUEUED.value,
                "stage": "waiting_for_cloud_task",
                "progress": 0,
                "cancel_requested": False,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "eta": eta_payload(durations, progress=0),
                "brief": None,
                "storyboard_package": None,
                "visual_storyboard": None,
                "pitch_preview": None,
                "error": None,
                "dispatch": None,
                "execution": None,
                "worker_claim_count": 0,
                "lease_token": None,
                "lease_expires_at": None,
                "dispatch_sequence": 0,
                "max_dispatches": None,
                "continuation": None,
                "pending_dispatch": None,
                "input_retention": "bounded_retry_until_record_expiry",
                "record_expires_at": record_expires_at,
            },
            now=now,
            cooldown_seconds=self.config.admission_cooldown_seconds,
            window_seconds=self.config.admission_window_seconds,
            max_jobs=self.config.admission_max_jobs,
        )
        attempt = int(retried["attempt"])
        try:
            dispatch = dict(
                self.dispatcher.enqueue(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=0,
                )
            )
        except Exception as exc:
            failed = self.repository.mark_dispatch_failed(
                job_id,
                {
                    "state": JobState.FAILED.value,
                    "stage": "dispatch_failed",
                    "updated_at": iso_now(),
                    "completed_at": iso_now(),
                    "error": {
                        "code": "cloud_tasks_dispatch_failed",
                        "type": type(exc).__name__,
                        "retryable": retried["attempt"] < retried["max_attempts"],
                    },
                    **_failed_input_retention_patch(retried),
                },
                attempt=attempt,
            )
            return public_job(failed)
        saved = self.repository.update(
            job_id,
            {"dispatch": dispatch, "updated_at": iso_now()},
        )
        return public_job(saved)

    def execute(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int = 0,
    ) -> dict[str, Any]:
        self.config.assert_valid(require_dispatch=False)
        if self.provider is None:
            raise ConfigurationError("the worker service has no Gemini provider")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise JobTransitionError("Cloud Tasks attempt binding is invalid")
        if (
            isinstance(dispatch_sequence, bool)
            or not isinstance(dispatch_sequence, int)
            or not 0 <= dispatch_sequence < MAX_PIPELINE_DISPATCHES
        ):
            raise JobTransitionError("Cloud Tasks dispatch sequence binding is invalid")
        before_claim = self.repository.get(job_id)
        raw_pending = before_claim.get("pending_dispatch")
        if isinstance(raw_pending, Mapping):
            pending = _validate_pending_dispatch(raw_pending)
            if (
                pending["application_attempt"] == attempt
                and pending["predecessor_dispatch_sequence"] == dispatch_sequence
            ):
                return self._reconcile_pending_dispatch(
                    job_id,
                    attempt=attempt,
                    predecessor_dispatch_sequence=dispatch_sequence,
                )
            if (
                pending["application_attempt"] == attempt
                and pending["dispatch_sequence"] == dispatch_sequence
                and before_claim.get("state") != JobState.QUEUED.value
            ):
                raise JobDispatchPendingError(
                    "continuation target is not ready to claim"
                )
        now_value = utc_now()
        now = now_value.isoformat()
        lease_token = str(uuid4())
        lease_expires_at = (
            now_value + timedelta(seconds=self.config.worker_lease_seconds)
        ).isoformat()
        durations = self.repository.recent_success_durations()
        claimed = self.repository.claim(
            job_id,
            {
                "state": JobState.RUNNING.value,
                "stage": "validating_request",
                "progress": 10,
                "started_at": now,
                "updated_at": now,
                "eta": eta_payload(durations, progress=10),
            },
            attempt=attempt,
            dispatch_sequence=dispatch_sequence,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            now=now,
        )
        if claimed is None:
            current = self.repository.get(job_id)
            current_attempt = int(current.get("attempt", 0))
            current_sequence = int(current.get("dispatch_sequence", -1))
            current_pending = current.get("pending_dispatch")
            if isinstance(current_pending, Mapping):
                pending = _validate_pending_dispatch(current_pending)
                if (
                    pending["application_attempt"] == attempt
                    and pending["predecessor_dispatch_sequence"]
                    == dispatch_sequence
                ):
                    return self._reconcile_pending_dispatch(
                        job_id,
                        attempt=attempt,
                        predecessor_dispatch_sequence=dispatch_sequence,
                    )
            if current_attempt == attempt and current_sequence > dispatch_sequence:
                return public_job(current)
            if (
                current_attempt == attempt
                and current_sequence == dispatch_sequence
                and current.get("state") in {
                JobState.RUNNING.value,
                JobState.CANCELLING.value,
                }
            ):
                raise JobLeaseBusyError(
                    "worker lease is still active; Cloud Tasks should retry",
                    retry_after_seconds=_lease_retry_after(current.get("lease_expires_at")),
                )
            if (
                current_attempt == attempt
                and current_sequence == dispatch_sequence
                and current.get("state") == JobState.QUEUED.value
            ):
                raise JobDispatchPendingError(
                    "continuation target is waiting for durable dispatch"
                )
            return public_job(current)
        if claimed.get("cancel_requested"):
            return public_job(
                self._finish_cancelled(
                    job_id,
                    attempt=attempt,
                    lease_token=lease_token,
                    started_at=claimed.get("started_at"),
                )
            )
        if dispatch_sequence > 0:
            return self._execute_continuation(
                job_id,
                attempt=attempt,
                dispatch_sequence=dispatch_sequence,
                lease_token=lease_token,
                claimed=claimed,
                durations=durations,
            )
        owned = self.repository.update_claimed(
            job_id,
            {
                "stage": "calling_gemini",
                "progress": 40,
                "updated_at": iso_now(),
                "eta": eta_payload(durations, progress=40),
            },
            attempt=attempt,
            lease_token=lease_token,
        )
        if owned.get("lease_token") != lease_token:
            return public_job(owned)
        if owned.get("cancel_requested"):
            return public_job(
                self._finish_cancelled(
                    job_id,
                    attempt=attempt,
                    lease_token=lease_token,
                    started_at=claimed.get("started_at"),
                )
            )
        failure_code = "brief_generation_failed"
        try:
            source_message = str(claimed["message"])
            result = self.provider.create_brief(source_message, job_id=job_id)
            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "validating_creative_plan",
                    "progress": 70,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=70),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            brief = ProductionBrief.from_mapping(
                {key: value for key, value in result.brief.to_dict().items() if key != "schema"}
            )
            failure_code = "storyboard_compilation_failed"
            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "compiling_storyboard_timeline",
                    "progress": 82,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=82),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            timeline = compile_storyboard_timeline(brief)
            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "auditing_coverage_and_continuity",
                    "progress": 94,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=94),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            storyboard_package = build_storyboard_package(brief, timeline=timeline)
            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "generating_visual_storyboard",
                    "progress": 97,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=97),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            shots = timeline.get("shots")
            if (
                brief.ready_for_production
                and self.artifact_store is not None
                and isinstance(shots, list)
                and len(shots) >= 1
            ):
                failure_code = "visual_storyboard_incomplete"
                visual_chunk_count = math.ceil(
                    len(shots) / self.config.visual_panels_per_dispatch
                )
                max_dispatches = (
                    2
                    * (
                        visual_chunk_count
                        + self.config.visual_quota_max_deferrals
                    )
                    + len(shots)
                    + 2
                )
                if max_dispatches > MAX_PIPELINE_DISPATCHES:
                    raise PipelineCheckpointError(
                        "production plan exceeds the continuation dispatch bound"
                    )
                # The planning dispatch never calls the image provider.  It
                # durably queues the first FIFO-governed visual chunk, so the
                # strict 2*(C+D) + N + 2 delivery bound remains exact even when
                # every visual attempt needs one late-window reconciliation.
                return self._queue_continuation(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=dispatch_sequence,
                    lease_token=lease_token,
                    started_at=claimed.get("started_at"),
                    request_sha256=str(claimed.get("request_sha256") or ""),
                    target_digest=str(claimed.get("target_digest") or ""),
                    brief=brief,
                    storyboard_package=storyboard_package,
                    provider_execution=result.execution,
                    panels=(),
                    pitch_segments=(),
                    visual_evidence_origin="not_attempted",
                    previous_checkpoint_sha256=None,
                    max_dispatches=max_dispatches,
                    phase="visual_storyboard",
                    quota_deferrals_used=0,
                    capacity_waits_used=0,
                )
            visual_storyboard = build_visual_storyboard(
                brief,
                timeline,
                provider=self.visual_provider,
                config=self.config,
                job_id=job_id,
                artifact_store=self.artifact_store,
            )
            pitch_preview: dict[str, Any] | None = None
            if brief.ready_for_production and self.artifact_store is not None:
                failure_code = "visual_storyboard_incomplete"
                if visual_storyboard.get("status") != "complete":
                    raise VisualPanelGenerationError(
                        _visual_storyboard_incomplete_code(visual_storyboard)
                    )
                if self.narrated_pitch_renderer is None:
                    raise ConfigurationError("the worker service has no narrated pitch renderer")
                owned = self.repository.update_claimed(
                    job_id,
                    {
                        "stage": "rendering_narrated_pitch",
                        "progress": 99,
                        "updated_at": iso_now(),
                        "eta": eta_payload(durations, progress=99),
                    },
                    attempt=attempt,
                    lease_token=lease_token,
                )
                if owned.get("lease_token") != lease_token:
                    return public_job(owned)
                if owned.get("cancel_requested"):
                    return public_job(
                        self._finish_cancelled(
                            job_id,
                            attempt=attempt,
                            lease_token=lease_token,
                            started_at=claimed.get("started_at"),
                        )
                    )
                failure_code = "narrated_pitch_render_failed"
                pitch_preview = dict(
                    self.narrated_pitch_renderer.render(
                        brief=brief,
                        timeline=timeline,
                        source_message=source_message,
                        visual_storyboard=visual_storyboard,
                        job_id=job_id,
                    )
                )
                video = pitch_preview.get("video")
                pitch_digest = pitch_preview.get("manifest_sha256")
                pitch_body = {
                    key: value
                    for key, value in pitch_preview.items()
                    if key != "manifest_sha256"
                }
                if (
                    pitch_preview.get("schema") != NARRATED_PITCH_SCHEMA
                    or pitch_preview.get("status") != "complete"
                    or pitch_preview.get("card_count") != len(timeline.get("shots", []))
                    or pitch_preview.get("cue_count") != len(timeline.get("shots", []))
                    or not isinstance(pitch_digest, str)
                    or pitch_digest != sha256_json(pitch_body)
                    or not isinstance(video, Mapping)
                    or video.get("content_type") != "video/mp4"
                    or video.get("video_codec") != "h264"
                    or video.get("audio_codec") != "aac"
                    or video.get("width") != 1920
                    or video.get("height") != 1080
                ):
                    raise BriefValidationError("narrated pitch manifest is incomplete")
            finished_at = utc_now()
            started_at = _parse_time(claimed.get("started_at"))
            execution = {
                **dict(result.execution),
                "pipeline": {
                    "steps": [
                        "gemini_structured_creative_plan",
                        "deterministic_storyboard_timeline_compile",
                        "deterministic_coverage_continuity_audit",
                        "complete_gemini_visual_storyboard",
                        "google_cloud_tts_narration",
                        "ffmpeg_narrated_pitch_mp4",
                    ],
                    "storyboard_package_schema": STORYBOARD_PACKAGE_SCHEMA,
                    "manifest_sha256": storyboard_package["manifest_sha256"],
                    "media_status": (
                        "narrated_storyboard_pitch_mp4"
                        if pitch_preview is not None
                        else "unrendered_plan"
                    ),
                    "visual_storyboard_schema": VISUAL_STORYBOARD_SCHEMA,
                    "visual_storyboard_status": visual_storyboard["status"],
                    "visual_owner_review": visual_owner_review_gate(timeline),
                },
            }
            success_patch: dict[str, Any] = {
                "state": JobState.SUCCEEDED.value,
                "stage": (
                    "technical_package_ready_owner_visual_review_hold"
                    if brief.ready_for_production
                    else "clarification_required"
                ),
                "progress": 100,
                "updated_at": finished_at.isoformat(),
                "completed_at": finished_at.isoformat(),
                "duration_seconds": _elapsed(started_at, finished_at),
                "eta": {
                    "available": True,
                    "low_seconds": 0,
                    "high_seconds": 0,
                    "sample_count": len(durations),
                    "basis": "complete",
                },
                # The source screenplay has already served its one provider
                # call.  Removing it protects privacy and creates deterministic
                # Firestore headroom for the reviewed outputs.
                "message": None,
                "input_retention": "discarded_after_provider_use",
                "brief": brief.to_dict(),
                "storyboard_package": storyboard_package,
                "visual_storyboard": visual_storyboard,
                "pitch_preview": pitch_preview,
                "execution": execution,
                "error": None,
            }
            if visual_storyboard.get("representation") == "inline_base64":
                visual_storyboard = fit_visual_storyboard_to_job_budget(
                    visual_storyboard,
                    brief=brief,
                    timeline=timeline,
                    record_without_visual={
                        **claimed,
                        **success_patch,
                        "visual_storyboard": None,
                        "pitch_preview": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                    },
                )
                success_patch["visual_storyboard"] = visual_storyboard
            success_patch["execution"]["pipeline"]["visual_storyboard_status"] = (
                visual_storyboard["status"]
            )
            if len(
                canonical_json({**claimed, **success_patch}).encode("utf-8")
            ) > MAX_DURABLE_JOB_BYTES:
                raise BriefValidationError("completed job exceeds the durable document size budget")
        except Exception as exc:
            if isinstance(exc, JobDispatchPendingError):
                raise
            if type(exc) is PipelineWorkStopped:
                current = self.repository.get(job_id)
                if (
                    current.get("lease_token") == lease_token
                    and current.get("cancel_requested")
                ):
                    current = self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                return public_job(current)
            if type(exc) is PipelineContinuationDispatchError:
                failure_code = "pipeline_continuation_dispatch_failed"
            elif type(exc) is PipelineCheckpointError:
                failure_code = "pipeline_checkpoint_invalid"
            finished_at = utc_now()
            started_at = _parse_time(claimed.get("started_at"))
            error: dict[str, Any] = {
                "code": failure_code,
                "type": type(exc).__name__,
                "retryable": int(claimed.get("attempt", 1))
                < int(claimed.get("max_attempts", MAX_ATTEMPTS)),
            }
            if failure_code == "visual_storyboard_incomplete":
                diagnostic_code = _visual_panel_diagnostic_code(exc)
                if diagnostic_code is not None:
                    error["diagnostic_code"] = diagnostic_code
                if diagnostic_code == "quota_or_rate_limited":
                    error.update(
                        {
                            "quota_deferrals_exhausted": (
                                self.config.visual_quota_max_deferrals == 0
                            ),
                            "quota_deferrals_used": 0,
                            "quota_deferral_limit": (
                                self.config.visual_quota_max_deferrals
                            ),
                        }
                    )
            elif failure_code == "narrated_pitch_render_failed":
                diagnostic_code = _narrated_pitch_render_diagnostic_code(exc)
                if diagnostic_code is not None:
                    error["diagnostic_code"] = diagnostic_code
            failed = self.repository.finalize(
                job_id,
                {
                    "state": JobState.FAILED.value,
                    "stage": failure_code,
                    "updated_at": finished_at.isoformat(),
                    "completed_at": finished_at.isoformat(),
                    "duration_seconds": _elapsed(started_at, finished_at),
                    "eta": eta_payload(durations, progress=100),
                    "error": error,
                    "brief": None,
                    "storyboard_package": None,
                    "visual_storyboard": None,
                    "pitch_preview": None,
                    "execution": None,
                    "continuation": None,
                    **_failed_input_retention_patch(claimed),
                },
                attempt=attempt,
                lease_token=lease_token,
                cancelled_patch=_cancelled_patch(
                    started_at=claimed.get("started_at"),
                    finished_at=finished_at,
                ),
            )
            return public_job(failed)
        succeeded = self.repository.finalize(
            job_id,
            success_patch,
            attempt=attempt,
            lease_token=lease_token,
            cancelled_patch=_cancelled_patch(
                started_at=claimed.get("started_at"),
                finished_at=finished_at,
            ),
        )
        return public_job(succeeded)

    def _queue_continuation(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        started_at: Any,
        request_sha256: str,
        target_digest: str,
        brief: ProductionBrief,
        storyboard_package: Mapping[str, Any],
        provider_execution: Mapping[str, Any],
        panels: Sequence[Mapping[str, Any]],
        pitch_segments: Sequence[Mapping[str, Any]],
        visual_evidence_origin: str,
        previous_checkpoint_sha256: str | None,
        max_dispatches: int,
        phase: str,
        quota_deferrals_used: int,
        capacity_waits_used: int = 0,
        visual_capacity_window: Mapping[str, Any] | None = None,
        delay_seconds: int = 0,
        delay_reason: str | None = None,
    ) -> dict[str, Any]:
        if self.artifact_store is None:
            raise PipelineCheckpointError("continuation requires private artifact storage")
        if self.dispatcher is None:
            raise PipelineContinuationDispatchError(
                "continuation requires a Cloud Tasks dispatcher"
            )
        prepared_at = utc_now()
        next_sequence = dispatch_sequence + 1
        if phase == "visual_storyboard":
            visual_capacity_window = (
                _validate_visual_capacity_window(visual_capacity_window)
                if visual_capacity_window is not None
                else self._prepare_visual_capacity_window(
                    now=prepared_at,
                    job_id=job_id,
                    attempt=attempt,
                    dispatch_sequence=next_sequence,
                )
            )
            window_delay = max(
                0,
                int(visual_capacity_window["not_before_epoch_seconds"])
                - math.ceil(prepared_at.timestamp()),
            )
            if window_delay > delay_seconds:
                delay_seconds = window_delay
                delay_reason = "visual_capacity"
        elif visual_capacity_window is not None:
            raise PipelineCheckpointError(
                "non-visual continuation cannot carry a capacity window"
            )
        if delay_reason not in {
            None,
            "visual_quota",
            "visual_capacity",
            "visual_spacing",
        }:
            raise PipelineCheckpointError("continuation delay reason is invalid")
        if bool(delay_seconds) != bool(delay_reason):
            raise PipelineCheckpointError(
                "continuation delay and reason must be supplied together"
            )
        try:
            continuation = _write_pipeline_checkpoint(
                artifact_store=self.artifact_store,
                job_id=job_id,
                attempt=attempt,
                dispatch_sequence=next_sequence,
                phase=phase,
                request_sha256=request_sha256,
                target_digest=target_digest,
                brief=brief,
                storyboard_package=storyboard_package,
                provider_execution=provider_execution,
                panels=panels,
                pitch_segments=pitch_segments,
                visual_evidence_origin=visual_evidence_origin,
                previous_checkpoint_sha256=previous_checkpoint_sha256,
                max_dispatches=max_dispatches,
                quota_deferrals_used=quota_deferrals_used,
                capacity_waits_used=capacity_waits_used,
                visual_capacity_window=visual_capacity_window,
            )
        except Exception:
            if visual_capacity_window is not None:
                self._complete_visual_capacity_window(visual_capacity_window)
            raise
        required = int(continuation["required_panel_count"])
        generated = int(continuation["next_panel_index"])
        narrated = int(continuation["next_pitch_index"])
        progress = min(
            99,
            97
            + math.floor(generated / max(1, required))
            + math.floor(narrated / max(1, required)),
        )
        try:
            pending_dispatch = _build_pending_dispatch(
                attempt=attempt,
                predecessor_dispatch_sequence=dispatch_sequence,
                dispatch_sequence=next_sequence,
                checkpoint_sha256=str(continuation["checkpoint_sha256"]),
                delay_seconds=delay_seconds,
                delay_reason=delay_reason,
                prepared_at=prepared_at,
            )
        except Exception:
            if visual_capacity_window is not None:
                self._complete_visual_capacity_window(visual_capacity_window)
            raise
        queued_at = prepared_at.isoformat()
        try:
            queued = self.repository.continue_job(
                job_id,
                {
                "state": JobState.QUEUED.value,
                "stage": (
                    "waiting_for_visual_quota_deferral"
                    if delay_reason == "visual_quota"
                    else (
                        "waiting_for_project_visual_capacity"
                        if delay_reason == "visual_capacity"
                        else (
                            "waiting_for_visual_capacity_spacing"
                            if delay_reason == "visual_spacing"
                            else (
                                "waiting_for_visual_storyboard_continuation"
                                if phase == "visual_storyboard"
                                else (
                                    "waiting_for_narrated_pitch_card_continuation"
                                    if phase == "narrated_pitch"
                                    else "waiting_for_narrated_pitch_finalization"
                                )
                            )
                        )
                    )
                ),
                "progress": progress,
                "updated_at": queued_at,
                "eta": eta_payload(self.repository.recent_success_durations(), progress=progress),
                "dispatch": None,
                "dispatch_sequence": next_sequence,
                "max_dispatches": max_dispatches,
                "continuation": continuation,
                "pending_dispatch": pending_dispatch,
                "error": None,
                },
                attempt=attempt,
                dispatch_sequence=dispatch_sequence,
                lease_token=lease_token,
                cancelled_patch=_cancelled_patch(
                    started_at=started_at,
                    finished_at=utc_now(),
                ),
            )
        except Exception:
            # The transaction may have committed even when its response was
            # lost.  Keep both deterministic capacity tokens intact and let
            # the predecessor task reconcile the exact durable state instead
            # of risking release of a live successor turn.
            raise JobDispatchPendingError(
                "continuation state transition is durably pending"
            ) from None
        queued_pending = queued.get("pending_dispatch")
        if not isinstance(queued_pending, Mapping):
            # Cancellation, terminal state, or stale lease won the prepare
            # transaction.  Such a worker must never create a successor task.
            return public_job(queued)
        validated = _validate_pending_dispatch(queued_pending)
        if validated["manifest_sha256"] != pending_dispatch["manifest_sha256"]:
            return public_job(queued)
        return self._reconcile_pending_dispatch(
            job_id,
            attempt=attempt,
            predecessor_dispatch_sequence=dispatch_sequence,
        )

    def _reconcile_pending_dispatch(
        self,
        job_id: str,
        *,
        attempt: int,
        predecessor_dispatch_sequence: int,
    ) -> dict[str, Any]:
        """Ensure and confirm exactly one named successor from the durable outbox."""

        if self.dispatcher is None:
            raise JobDispatchPendingError(
                "continuation dispatch is durably pending"
            )
        current = self.repository.get(job_id)
        raw_pending = current.get("pending_dispatch")
        if raw_pending is None:
            return public_job(current)
        pending = _validate_pending_dispatch(raw_pending)
        if (
            pending["application_attempt"] != attempt
            or pending["predecessor_dispatch_sequence"]
            != predecessor_dispatch_sequence
        ):
            return public_job(current)
        if (
            current.get("state") != JobState.QUEUED.value
            or current.get("cancel_requested")
            or int(current.get("attempt", 0)) != attempt
            or int(current.get("dispatch_sequence", -1))
            != int(pending["dispatch_sequence"])
        ):
            return public_job(current)
        try:
            dispatch = dict(
                self.dispatcher.enqueue(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=int(pending["dispatch_sequence"]),
                    scheduled_epoch_seconds=int(
                        pending["scheduled_epoch_seconds"]
                    ),
                )
            )
            if (
                dispatch.get("attempt") != attempt
                or dispatch.get("dispatch_sequence")
                != pending["dispatch_sequence"]
                or dispatch.get("scheduled_epoch_seconds")
                != pending["scheduled_epoch_seconds"]
                or not isinstance(dispatch.get("task_name"), str)
                or not str(dispatch["task_name"]).endswith(
                    f"{job_id}-a{attempt}-d{int(pending['dispatch_sequence']):03d}"
                )
            ):
                raise JobTransitionError("continuation dispatch receipt is invalid")
            confirmed = self.repository.confirm_continuation_dispatch(
                job_id,
                dispatch,
                attempt=attempt,
                dispatch_sequence=int(pending["dispatch_sequence"]),
                pending_manifest_sha256=str(pending["manifest_sha256"]),
            )
        except Exception:
            # The prepared outbox and released lease remain durable.  Returning
            # a retryable non-success lets the predecessor task reconcile the
            # same name/schedule without rerunning provider work.
            raise JobDispatchPendingError(
                "continuation dispatch is durably pending"
            ) from None
        return public_job(confirmed)

    def _execute_continuation(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        claimed: Mapping[str, Any],
        durations: Sequence[float],
    ) -> dict[str, Any]:
        failure_code = "pipeline_checkpoint_invalid"
        try:
            if self.artifact_store is None:
                raise PipelineCheckpointError(
                    "continuation requires private artifact storage"
                )
            source_message = claimed.get("message")
            if not isinstance(source_message, str) or not source_message:
                raise PipelineCheckpointError(
                    "continuation source was discarded before completion"
                )
            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "validating_pipeline_checkpoint",
                    "progress": 97,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=97),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            checkpoint = _load_pipeline_checkpoint(
                claimed.get("continuation"),
                artifact_store=self.artifact_store,
                job_id=job_id,
                attempt=attempt,
                dispatch_sequence=dispatch_sequence,
                source_message=source_message,
                request_sha256=str(claimed.get("request_sha256") or ""),
                target_digest=str(claimed.get("target_digest") or ""),
            )
            brief = checkpoint.pop("brief_object")
            if not isinstance(brief, ProductionBrief):
                raise PipelineCheckpointError("continuation brief could not be restored")
            storyboard_package = checkpoint["storyboard_package"]
            timeline = storyboard_package.get("timeline")
            if not isinstance(timeline, Mapping):
                raise PipelineCheckpointError("continuation timeline could not be restored")
            panels = checkpoint["panels"]
            pitch_segments = checkpoint["pitch_segments"]
            phase = checkpoint["phase"]
            quota_deferrals_used = int(checkpoint["quota_deferrals_used"])
            capacity_waits_used = int(checkpoint["capacity_waits_used"])
            if quota_deferrals_used > self.config.visual_quota_max_deferrals:
                raise PipelineCheckpointError(
                    "continuation quota-deferral count exceeds the configured bound"
                )
            if phase == "visual_storyboard":
                failure_code = "visual_storyboard_incomplete"
                owned = self.repository.update_claimed(
                    job_id,
                    {
                        "stage": "generating_visual_storyboard",
                        "progress": 98,
                        "updated_at": iso_now(),
                        "eta": eta_payload(durations, progress=98),
                    },
                    attempt=attempt,
                    lease_token=lease_token,
                )
                if owned.get("lease_token") != lease_token:
                    return public_job(owned)
                if owned.get("cancel_requested"):
                    return public_job(
                        self._finish_cancelled(
                            job_id,
                            attempt=attempt,
                            lease_token=lease_token,
                            started_at=claimed.get("started_at"),
                        )
                    )
                evidence_origin = str(checkpoint["visual_evidence_origin"])
                capacity_window = _validate_visual_capacity_window(
                    checkpoint["visual_capacity_window"]
                )
                capacity_wait_limit = math.ceil(
                    int(checkpoint["required_panel_count"])
                    / self.config.visual_panels_per_dispatch
                ) + self.config.visual_quota_max_deferrals
                try:
                    panels, new_evidence_origin = _generate_external_visual_chunk(
                        brief,
                        timeline,
                        provider=self.visual_provider,
                        artifact_store=self.artifact_store,
                        config=self.config,
                        job_id=job_id,
                        existing_panels=panels,
                        ownership_check=lambda: self._visual_chunk_is_owned(
                            job_id,
                            attempt=attempt,
                            lease_token=lease_token,
                        ),
                        capacity_reservation=lambda: self._reserve_visual_capacity(
                            capacity_window
                        ),
                    )
                except _VisualQuotaDeferred as deferred:
                    if quota_deferrals_used >= self.config.visual_quota_max_deferrals:
                        raise VisualPanelGenerationError("quota_or_rate_limited") from None
                    deferred_origin = evidence_origin
                    if deferred.evidence_origin != "not_attempted":
                        deferred_origin = deferred.evidence_origin
                    return self._queue_continuation(
                        job_id,
                        attempt=attempt,
                        dispatch_sequence=dispatch_sequence,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                        request_sha256=str(claimed.get("request_sha256") or ""),
                        target_digest=str(claimed.get("target_digest") or ""),
                        brief=brief,
                        storyboard_package=storyboard_package,
                        provider_execution=checkpoint["provider_execution"],
                        panels=deferred.panels,
                        pitch_segments=pitch_segments,
                        visual_evidence_origin=deferred_origin,
                        previous_checkpoint_sha256=str(
                            claimed["continuation"]["checkpoint_sha256"]
                        ),
                        max_dispatches=int(checkpoint["max_dispatches"]),
                        phase="visual_storyboard",
                        quota_deferrals_used=quota_deferrals_used + 1,
                        capacity_waits_used=capacity_waits_used,
                        delay_seconds=_visual_quota_delay_seconds(
                            self.config,
                            quota_deferrals_used=quota_deferrals_used,
                        ),
                        delay_reason="visual_quota",
                    )
                except _VisualCapacityDeferred as deferred:
                    deferred_origin = evidence_origin
                    if deferred.evidence_origin != "not_attempted":
                        deferred_origin = deferred.evidence_origin
                    if deferred.window_active:
                        # Ordinary FIFO contention is delivery timing, not a
                        # provider failure and not a new pipeline dispatch.  Put
                        # this exact fenced sequence back in QUEUED and make the
                        # same named Cloud Task retry it.  Repeated early wakes
                        # therefore cannot consume the provider-429 budget or
                        # violate MAX_PIPELINE_DISPATCHES.
                        deferred_job = self.repository.defer_claimed(
                            job_id,
                            {
                                "state": JobState.QUEUED.value,
                                "stage": "waiting_for_project_visual_capacity",
                                "updated_at": iso_now(),
                                "eta": eta_payload(durations, progress=98),
                                "error": None,
                            },
                            attempt=attempt,
                            dispatch_sequence=dispatch_sequence,
                            lease_token=lease_token,
                            cancelled_patch=_cancelled_patch(
                                started_at=claimed.get("started_at"),
                                finished_at=utc_now(),
                            ),
                        )
                        if (
                            deferred_job.get("state") == JobState.QUEUED.value
                            and int(deferred_job.get("attempt", 0)) == attempt
                            and int(deferred_job.get("dispatch_sequence", -1))
                            == dispatch_sequence
                        ):
                            raise JobVisualCapacityPendingError(
                                retry_after_seconds=deferred.retry_after_seconds
                            )
                        return public_job(deferred_job)
                    if capacity_waits_used >= capacity_wait_limit:
                        raise VisualPanelGenerationError(
                            "project_visual_capacity_unavailable"
                        ) from None
                    retry_window: Mapping[str, Any] | None = capacity_window
                    retry_window = None
                    return self._queue_continuation(
                        job_id,
                        attempt=attempt,
                        dispatch_sequence=dispatch_sequence,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                        request_sha256=str(claimed.get("request_sha256") or ""),
                        target_digest=str(claimed.get("target_digest") or ""),
                        brief=brief,
                        storyboard_package=storyboard_package,
                        provider_execution=checkpoint["provider_execution"],
                        panels=deferred.panels,
                        pitch_segments=pitch_segments,
                        visual_evidence_origin=deferred_origin,
                        previous_checkpoint_sha256=str(
                            claimed["continuation"]["checkpoint_sha256"]
                        ),
                        max_dispatches=int(checkpoint["max_dispatches"]),
                        phase="visual_storyboard",
                        quota_deferrals_used=quota_deferrals_used,
                        capacity_waits_used=capacity_waits_used + 1,
                        visual_capacity_window=retry_window,
                        delay_seconds=deferred.retry_after_seconds,
                        delay_reason="visual_capacity",
                    )
                if new_evidence_origin != "not_attempted":
                    evidence_origin = new_evidence_origin
                required = int(checkpoint["required_panel_count"])
                next_phase = (
                    "narrated_pitch" if len(panels) == required else "visual_storyboard"
                )
                return self._queue_continuation(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=dispatch_sequence,
                    lease_token=lease_token,
                    started_at=claimed.get("started_at"),
                    request_sha256=str(claimed.get("request_sha256") or ""),
                    target_digest=str(claimed.get("target_digest") or ""),
                    brief=brief,
                    storyboard_package=storyboard_package,
                    provider_execution=checkpoint["provider_execution"],
                    panels=panels,
                    pitch_segments=pitch_segments,
                    visual_evidence_origin=evidence_origin,
                    previous_checkpoint_sha256=str(
                        claimed["continuation"]["checkpoint_sha256"]
                    ),
                    max_dispatches=int(checkpoint["max_dispatches"]),
                    phase=next_phase,
                    quota_deferrals_used=quota_deferrals_used,
                    capacity_waits_used=capacity_waits_used,
                )

            failure_code = "narrated_pitch_render_failed"
            visual_storyboard = _complete_external_visual_storyboard(
                brief,
                timeline,
                panels=panels,
                config=self.config,
                job_id=job_id,
                evidence_origin=str(checkpoint["visual_evidence_origin"]),
            )
            if self.narrated_pitch_renderer is None:
                raise ConfigurationError("the worker service has no narrated pitch renderer")

            if phase == "narrated_pitch":
                owned = self.repository.update_claimed(
                    job_id,
                    {
                        "stage": "rendering_narrated_pitch_card",
                        "progress": 99,
                        "updated_at": iso_now(),
                        "eta": eta_payload(durations, progress=99),
                    },
                    attempt=attempt,
                    lease_token=lease_token,
                )
                if owned.get("lease_token") != lease_token:
                    return public_job(owned)
                if owned.get("cancel_requested"):
                    return public_job(
                        self._finish_cancelled(
                            job_id,
                            attempt=attempt,
                            lease_token=lease_token,
                            started_at=claimed.get("started_at"),
                        )
                    )
                new_segments = list(
                    self.narrated_pitch_renderer.render_segment_chunk(
                        brief=brief,
                        timeline=timeline,
                        source_message=source_message,
                        visual_storyboard=visual_storyboard,
                        job_id=job_id,
                        start_index=len(pitch_segments),
                        max_cards=1,
                        ownership_check=lambda: self._visual_chunk_is_owned(
                            job_id,
                            attempt=attempt,
                            lease_token=lease_token,
                        ),
                    )
                )
                if len(new_segments) != 1:
                    raise PipelineCheckpointError(
                        "narrated pitch card chunk is not exactly bounded"
                    )
                pitch_segments = _validate_checkpoint_pitch_segments(
                    [*pitch_segments, *new_segments],
                    timeline=timeline,
                    job_id=job_id,
                    next_pitch_index=len(pitch_segments) + 1,
                )
                next_phase = (
                    "narrated_pitch_finalize"
                    if len(pitch_segments) == len(timeline.get("shots", []))
                    else "narrated_pitch"
                )
                return self._queue_continuation(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=dispatch_sequence,
                    lease_token=lease_token,
                    started_at=claimed.get("started_at"),
                    request_sha256=str(claimed.get("request_sha256") or ""),
                    target_digest=str(claimed.get("target_digest") or ""),
                    brief=brief,
                    storyboard_package=storyboard_package,
                    provider_execution=checkpoint["provider_execution"],
                    panels=panels,
                    pitch_segments=pitch_segments,
                    visual_evidence_origin=str(checkpoint["visual_evidence_origin"]),
                    previous_checkpoint_sha256=str(
                        claimed["continuation"]["checkpoint_sha256"]
                    ),
                    max_dispatches=int(checkpoint["max_dispatches"]),
                    phase=next_phase,
                    quota_deferrals_used=quota_deferrals_used,
                )

            owned = self.repository.update_claimed(
                job_id,
                {
                    "stage": "finalizing_narrated_pitch",
                    "progress": 99,
                    "updated_at": iso_now(),
                    "eta": eta_payload(durations, progress=99),
                },
                attempt=attempt,
                lease_token=lease_token,
            )
            if owned.get("lease_token") != lease_token:
                return public_job(owned)
            if owned.get("cancel_requested"):
                return public_job(
                    self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                )
            pitch_preview = dict(
                self.narrated_pitch_renderer.finalize_segments(
                    brief=brief,
                    timeline=timeline,
                    source_message=source_message,
                    visual_storyboard=visual_storyboard,
                    job_id=job_id,
                    segments=pitch_segments,
                    ownership_check=lambda: self._visual_chunk_is_owned(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                    ),
                )
            )
            self._validate_pitch_preview(pitch_preview, timeline=timeline)
            finished_at = utc_now()
            started_at = _parse_time(claimed.get("started_at"))
            continuation_summary: dict[str, Any] = {
                "schema": PIPELINE_CONTINUATION_SCHEMA,
                "status": "complete",
                "application_attempt": attempt,
                "dispatch_sequence": dispatch_sequence,
                "dispatches_used": dispatch_sequence + 1,
                "max_dispatches": int(checkpoint["max_dispatches"]),
                "quota_deferrals_used": quota_deferrals_used,
                "required_panel_count": len(panels),
                "required_pitch_count": len(pitch_segments),
                "checkpoint_sha256": str(
                    claimed["continuation"]["checkpoint_sha256"]
                ),
            }
            continuation_summary["manifest_sha256"] = sha256_json(
                continuation_summary
            )
            execution = {
                **dict(checkpoint["provider_execution"]),
                "pipeline": {
                    "steps": [
                        "gemini_structured_creative_plan",
                        "deterministic_storyboard_timeline_compile",
                        "deterministic_coverage_continuity_audit",
                        "bounded_checkpointed_gemini_visual_storyboard",
                        "bounded_checkpointed_google_cloud_tts_cards",
                        "bounded_ffmpeg_narrated_pitch_finalization",
                    ],
                    "storyboard_package_schema": STORYBOARD_PACKAGE_SCHEMA,
                    "manifest_sha256": storyboard_package["manifest_sha256"],
                    "media_status": "narrated_storyboard_pitch_mp4",
                    "visual_storyboard_schema": VISUAL_STORYBOARD_SCHEMA,
                    "visual_storyboard_status": "complete",
                    "visual_owner_review": visual_owner_review_gate(timeline),
                    "continuation_schema": PIPELINE_CONTINUATION_SCHEMA,
                    "continuation_dispatches": dispatch_sequence + 1,
                    "visual_quota_deferrals_used": quota_deferrals_used,
                },
            }
            success_patch: dict[str, Any] = {
                "state": JobState.SUCCEEDED.value,
                "stage": "technical_package_ready_owner_visual_review_hold",
                "progress": 100,
                "updated_at": finished_at.isoformat(),
                "completed_at": finished_at.isoformat(),
                "duration_seconds": _elapsed(started_at, finished_at),
                "eta": {
                    "available": True,
                    "low_seconds": 0,
                    "high_seconds": 0,
                    "sample_count": len(durations),
                    "basis": "complete",
                },
                "message": None,
                "input_retention": "discarded_after_provider_use",
                "brief": brief.to_dict(),
                "storyboard_package": storyboard_package,
                "visual_storyboard": visual_storyboard,
                "pitch_preview": pitch_preview,
                "execution": execution,
                "continuation": continuation_summary,
                "error": None,
            }
            if len(canonical_json({**claimed, **success_patch}).encode("utf-8")) > MAX_DURABLE_JOB_BYTES:
                raise BriefValidationError(
                    "completed job exceeds the durable document size budget"
                )
            succeeded = self.repository.finalize(
                job_id,
                success_patch,
                attempt=attempt,
                lease_token=lease_token,
                cancelled_patch=_cancelled_patch(
                    started_at=claimed.get("started_at"),
                    finished_at=finished_at,
                ),
            )
            return public_job(succeeded)
        except Exception as exc:
            if isinstance(exc, JobDispatchPendingError):
                raise
            if type(exc) is PipelineWorkStopped:
                current = self.repository.get(job_id)
                if (
                    current.get("lease_token") == lease_token
                    and current.get("cancel_requested")
                ):
                    current = self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                return public_job(current)
            if _narrated_pitch_render_diagnostic_code(exc) == "work_stopped":
                current = self.repository.get(job_id)
                if (
                    current.get("lease_token") == lease_token
                    and current.get("cancel_requested")
                ):
                    current = self._finish_cancelled(
                        job_id,
                        attempt=attempt,
                        lease_token=lease_token,
                        started_at=claimed.get("started_at"),
                    )
                return public_job(current)
            if type(exc) is VisualPanelGenerationError:
                failure_code = "visual_storyboard_incomplete"
            elif type(exc) is PipelineContinuationDispatchError:
                failure_code = "pipeline_continuation_dispatch_failed"
            elif type(exc) is PipelineCheckpointError:
                failure_code = "pipeline_checkpoint_invalid"
            finished_at = utc_now()
            error: dict[str, Any] = {
                "code": failure_code,
                "type": type(exc).__name__,
                "retryable": int(claimed.get("attempt", 1))
                < int(claimed.get("max_attempts", MAX_ATTEMPTS)),
            }
            if failure_code == "visual_storyboard_incomplete":
                diagnostic_code = _visual_panel_diagnostic_code(exc)
                if diagnostic_code is not None:
                    error["diagnostic_code"] = diagnostic_code
                if diagnostic_code == "quota_or_rate_limited":
                    continuation_value = claimed.get("continuation")
                    deferrals_used = (
                        int(continuation_value.get("quota_deferrals_used", 0))
                        if isinstance(continuation_value, Mapping)
                        else 0
                    )
                    error.update(
                        {
                            "quota_deferrals_exhausted": (
                                deferrals_used
                                >= self.config.visual_quota_max_deferrals
                            ),
                            "quota_deferrals_used": deferrals_used,
                            "quota_deferral_limit": (
                                self.config.visual_quota_max_deferrals
                            ),
                        }
                    )
            elif failure_code == "narrated_pitch_render_failed":
                diagnostic_code = _narrated_pitch_render_diagnostic_code(exc)
                if diagnostic_code is not None:
                    error["diagnostic_code"] = diagnostic_code
            failed = self.repository.finalize(
                job_id,
                {
                    "state": JobState.FAILED.value,
                    "stage": failure_code,
                    "updated_at": finished_at.isoformat(),
                    "completed_at": finished_at.isoformat(),
                    "duration_seconds": _elapsed(
                        _parse_time(claimed.get("started_at")), finished_at
                    ),
                    "eta": eta_payload(durations, progress=100),
                    "error": error,
                    "brief": None,
                    "storyboard_package": None,
                    "visual_storyboard": None,
                    "pitch_preview": None,
                    "execution": None,
                    "continuation": None,
                    **_failed_input_retention_patch(claimed),
                },
                attempt=attempt,
                lease_token=lease_token,
                cancelled_patch=_cancelled_patch(
                    started_at=claimed.get("started_at"),
                    finished_at=finished_at,
                ),
            )
            return public_job(failed)

    def _visual_chunk_is_owned(
        self,
        job_id: str,
        *,
        attempt: int,
        lease_token: str,
    ) -> bool:
        """Heartbeat and fence every expensive visual-panel boundary."""

        owned = self.repository.update_claimed(
            job_id,
            {"updated_at": iso_now()},
            attempt=attempt,
            lease_token=lease_token,
        )
        return bool(
            owned.get("lease_token") == lease_token
            and owned.get("state") == JobState.RUNNING.value
            and not owned.get("cancel_requested")
        )

    def _prepare_visual_capacity_window(
        self,
        *,
        now: datetime | None = None,
        job_id: str,
        attempt: int,
        dispatch_sequence: int,
    ) -> dict[str, Any]:
        """Join the FIFO with a retry-stable token for one visual dispatch."""

        selected_now = now or utc_now()
        # A process can die after reserving the FIFO turn but before advancing
        # the job.  Deriving the token from the fenced successor identity makes
        # the predecessor's eventual Cloud Tasks retry rejoin the same turn
        # instead of leaking a second orphan reservation.
        token = _visual_capacity_reservation_token(
            job_id=job_id,
            attempt=attempt,
            dispatch_sequence=dispatch_sequence,
        )
        result = self.repository.prepare_visual_window(
            now=selected_now.isoformat(),
            reservation_token=token,
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
        )
        return _validate_visual_capacity_window(result)

    def _reserve_visual_capacity(
        self,
        window: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Reserve one external image request in the shared project window."""

        validated = _validate_visual_capacity_window(window)
        return self.repository.reserve_visual_request(
            now=iso_now(),
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
            reservation_token=str(validated["reservation_token"]),
        )

    @staticmethod
    def _visual_capacity_window_delay(window: Mapping[str, Any]) -> int:
        validated = _validate_visual_capacity_window(window)
        return max(
            0,
            min(
                MAX_TASK_SCHEDULE_DELAY_SECONDS,
                int(validated["not_before_epoch_seconds"])
                - math.ceil(utc_now().timestamp()),
            ),
        )

    def _complete_visual_capacity_window(
        self,
        window: Mapping[str, Any],
    ) -> None:
        """Release one FIFO turn after success or any non-capacity failure."""

        validated = _validate_visual_capacity_window(window)
        self.repository.complete_visual_window(
            now=iso_now(),
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
            reservation_token=str(validated["reservation_token"]),
        )

    @staticmethod
    def _validate_pitch_preview(
        pitch_preview: Mapping[str, Any],
        *,
        timeline: Mapping[str, Any],
    ) -> None:
        video = pitch_preview.get("video")
        pitch_digest = pitch_preview.get("manifest_sha256")
        pitch_body = {
            key: value
            for key, value in pitch_preview.items()
            if key != "manifest_sha256"
        }
        shot_count = len(timeline.get("shots", []))
        if (
            pitch_preview.get("schema") != NARRATED_PITCH_SCHEMA
            or pitch_preview.get("status") != "complete"
            or pitch_preview.get("card_count") != shot_count
            or pitch_preview.get("cue_count") != shot_count
            or not isinstance(pitch_digest, str)
            or pitch_digest != sha256_json(pitch_body)
            or not isinstance(video, Mapping)
            or video.get("content_type") != "video/mp4"
            or video.get("video_codec") != "h264"
            or video.get("audio_codec") != "aac"
            or video.get("width") != 1920
            or video.get("height") != 1080
        ):
            raise BriefValidationError("narrated pitch manifest is incomplete")

    def _finish_cancelled(
        self,
        job_id: str,
        *,
        attempt: int,
        lease_token: str,
        started_at: Any,
    ) -> Mapping[str, Any]:
        finished_at = utc_now()
        patch = _cancelled_patch(started_at=started_at, finished_at=finished_at)
        return self.repository.finalize(
            job_id,
            patch,
            attempt=attempt,
            lease_token=lease_token,
            cancelled_patch=patch,
        )


def _cancelled_patch(*, started_at: Any, finished_at: datetime) -> dict[str, Any]:
    return {
        "state": JobState.CANCELLED.value,
        "stage": "cancelled_without_promoting_brief",
        "progress": 100,
        "cancel_requested": True,
        "updated_at": finished_at.isoformat(),
        "completed_at": finished_at.isoformat(),
        "duration_seconds": _elapsed(_parse_time(started_at), finished_at),
        "eta": {
            "available": True,
            "low_seconds": 0,
            "high_seconds": 0,
            "sample_count": 0,
            "basis": "cancelled",
        },
        "brief": None,
        "storyboard_package": None,
        "visual_storyboard": None,
        "pitch_preview": None,
        "execution": None,
        "continuation": None,
        "pending_dispatch": None,
        "error": None,
    }


def _failed_input_retention_patch(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep retry input only while another bounded application attempt exists."""

    attempt = int(record.get("attempt", 1))
    maximum = int(record.get("max_attempts", MAX_ATTEMPTS))
    if attempt >= maximum:
        return {
            "message": None,
            "input_retention": "discarded_at_retry_limit",
        }
    return {"input_retention": "bounded_retry_until_record_expiry"}


def _lease_retry_after(value: Any) -> int:
    expires_at = _parse_time(value)
    return max(1, math.ceil((expires_at - utc_now()).total_seconds()))


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed(started: datetime, finished: datetime) -> float:
    return max(0.0, round((finished - started).total_seconds(), 6))


__all__ = [
    "AdmissionLimitError",
    "AllThingsConfig",
    "AllThingsError",
    "AllThingsJobService",
    "BRIEF_SCHEMA",
    "STORYBOARD_AUDIT_SCHEMA",
    "STORYBOARD_FRAME_RATE",
    "MAX_DURABLE_JOB_BYTES",
    "MAX_MESSAGE_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_PIPELINE_DISPATCHES",
    "MAX_STORYBOARD_PACKAGE_BYTES",
    "MAX_VISUAL_PANEL_BYTES",
    "MAX_VISUAL_PANEL_COUNT",
    "MAX_VISUAL_STORYBOARD_BYTES",
    "NARRATED_PITCH_RENDER_DIAGNOSTIC_CODES",
    "STORYBOARD_PACKAGE_SCHEMA",
    "STORYBOARD_TIMELINE_SCHEMA",
    "VISUAL_STORYBOARD_SCHEMA",
    "BriefProviderResult",
    "BriefValidationError",
    "ConfigurationError",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_IMAGE_MODEL",
    "JobLeaseBusyError",
    "JobDispatchPendingError",
    "JobVisualCapacityPendingError",
    "JobNotFoundError",
    "JobState",
    "JobTransitionError",
    "ProductionBrief",
    "PRODUCTION_BRIEF_RESPONSE_SCHEMA",
    "VisualPanelGenerationError",
    "VisualPanelProvider",
    "VisualPanelProviderResult",
    "audit_storyboard_package",
    "build_visual_storyboard",
    "fit_visual_storyboard_to_job_budget",
    "build_storyboard_package",
    "compile_storyboard_timeline",
    "public_job",
    "storyboard_panel_prompt",
    "visual_owner_review_gate",
    "validate_visual_storyboard",
    "validate_storyboard_package",
]
