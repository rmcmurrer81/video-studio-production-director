"""All Things Agentic production-brief workflow contracts.

This module contains no network client and starts no worker.  It defines the
validated natural-language request, structured production brief, durable job
lifecycle, honest ETA calculation, and the orchestration seam used by the
Google Cloud adapters in :mod:`kira_studio.all_things_google`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re
import statistics
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4


JOB_SCHEMA = "video-studio.all-things-agentic-job/v1"
BRIEF_SCHEMA = "video-studio.production-brief/v1"
STORYBOARD_PACKAGE_SCHEMA = "video-studio.storyboard-edit-package/v1"
STORYBOARD_TIMELINE_SCHEMA = "video-studio.planned-edit-timeline/v1"
STORYBOARD_AUDIT_SCHEMA = "video-studio.coverage-continuity-audit/v1"
STORYBOARD_FRAME_RATE = 24
# Leaves headroom beneath Firestore's document limit for the separately exposed
# creative brief, bounded request text, durable state, and provider evidence.
MAX_STORYBOARD_PACKAGE_BYTES = 520_000
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
MAX_MESSAGE_CHARS = 20_000
MAX_ATTEMPTS = 3
DEFAULT_ADMISSION_COOLDOWN_SECONDS = 3
DEFAULT_ADMISSION_WINDOW_SECONDS = 3_600
DEFAULT_ADMISSION_MAX_JOBS = 24
DEFAULT_WORKER_LEASE_SECONDS = 360

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
    firestore_database: str = "(default)"
    jobs_collection: str = "all_things_agentic_jobs"
    tasks_location: str = "us-central1"
    tasks_queue: str = "video-studio-production-briefs"
    worker_url: str = ""
    tasks_service_account: str = ""
    admission_cooldown_seconds: int = DEFAULT_ADMISSION_COOLDOWN_SECONDS
    admission_window_seconds: int = DEFAULT_ADMISSION_WINDOW_SECONDS
    admission_max_jobs: int = DEFAULT_ADMISSION_MAX_JOBS
    worker_lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "AllThingsConfig":
        return cls(
            project=environment.get("GOOGLE_CLOUD_PROJECT", "").strip(),
            location=environment.get("GOOGLE_CLOUD_LOCATION", "global").strip() or "global",
            model=environment.get("KIRA_ALL_THINGS_GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
            firestore_database=environment.get("KIRA_ALL_THINGS_FIRESTORE_DATABASE", "(default)").strip(),
            jobs_collection=environment.get("KIRA_ALL_THINGS_JOBS_COLLECTION", "all_things_agentic_jobs").strip(),
            tasks_location=environment.get("KIRA_ALL_THINGS_TASKS_LOCATION", "us-central1").strip(),
            tasks_queue=environment.get("KIRA_ALL_THINGS_TASKS_QUEUE", "video-studio-production-briefs").strip(),
            worker_url=environment.get("KIRA_ALL_THINGS_WORKER_URL", "").strip().rstrip("/"),
            tasks_service_account=environment.get("KIRA_ALL_THINGS_TASKS_SERVICE_ACCOUNT", "").strip(),
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
        if not self.firestore_database:
            issues.append("KIRA_ALL_THINGS_FIRESTORE_DATABASE is required")
        if not _COLLECTION_ID.fullmatch(self.jobs_collection):
            issues.append("KIRA_ALL_THINGS_JOBS_COLLECTION is invalid")
        if not _REGION.fullmatch(self.tasks_location):
            issues.append("KIRA_ALL_THINGS_TASKS_LOCATION must be a valid region")
        if not _SAFE_ID.fullmatch(self.tasks_queue):
            issues.append("KIRA_ALL_THINGS_TASKS_QUEUE is invalid")
        if not 0 <= self.admission_cooldown_seconds <= 300:
            issues.append("KIRA_ALL_THINGS_ADMISSION_COOLDOWN_SECONDS must be from 0 to 300")
        if not 60 <= self.admission_window_seconds <= 86_400:
            issues.append("KIRA_ALL_THINGS_ADMISSION_WINDOW_SECONDS must be from 60 to 86400")
        if not 1 <= self.admission_max_jobs <= 500:
            issues.append("KIRA_ALL_THINGS_ADMISSION_MAX_JOBS must be from 1 to 500")
        if not 60 <= self.worker_lease_seconds <= 1_800:
            issues.append("KIRA_ALL_THINGS_WORKER_LEASE_SECONDS must be from 60 to 1800")
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
            "framework": "google-genai",
            "api": "Vertex AI v1",
            "firestore_database": self.firestore_database,
            "jobs_collection": self.jobs_collection,
            "tasks_location": self.tasks_location,
            "tasks_queue": self.tasks_queue,
            "worker_url": self.worker_url,
            "tasks_service_account": self.tasks_service_account,
            "admission_cooldown_seconds": self.admission_cooldown_seconds,
            "admission_window_seconds": self.admission_window_seconds,
            "admission_max_jobs": self.admission_max_jobs,
            "worker_lease_seconds": self.worker_lease_seconds,
        }

    def target_digest(self) -> str:
        return sha256_json(self.safe_dict())


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
            purpose=_clean_string(value["purpose"], label="scene purpose", maximum=360),
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
        scenes = tuple(
            SceneBrief.from_mapping(scene, expected_number=index)
            for index, scene in enumerate(value["scenes"], start=1)
        )
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
        f"Keep the location geography specified for scene {scene.number} stable across its cards."
    ]
    if scene.characters:
        requirements.append(
            f"Match every listed scene-{scene.number} character's wardrobe, props, eyelines, and screen direction."
        )
    else:
        requirements.append("Match props, eyelines, and screen direction across coverage.")
    requirements.append("Preserve the brief's visual direction across matching coverage.")
    return requirements


def _source_footage_guidance(scene: SceneBrief) -> str:
    return (
        f"Select only verified source coverage matching the setting and subjects listed for scene {scene.number}; "
        "flag missing coverage instead of inventing a clip."
    )


def _bridge_shot_guidance(scene: SceneBrief) -> str:
    return (
        f"For scene {scene.number}, prefer a neutral reaction, prop, or environmental cutaway; "
        "if none exists, record a coverage gap rather than implying unverified footage."
    )


def _shot_action(scene: SceneBrief, role: str) -> str:
    if role == "establishing":
        return (
            f"Establish the location and spatial relationships specified for scene {scene.number} "
            "before its primary action."
        )
    if role == "primary_coverage":
        return f"Execute scene {scene.number}'s stated purpose as the primary planned action."
    return (
        "Hold a reaction, prop, or environmental detail that preserves the scene purpose "
        "and gives the editor a clean transition option."
    )


def _shot_audio(brief: ProductionBrief, scene: SceneBrief, role: str) -> str:
    if role == "primary_coverage" and scene.dialogue_required:
        prefix = "Protect intelligible dialogue coverage."
    elif role == "primary_coverage":
        prefix = "Carry motivated action sound and ambience; no dialogue is required."
    elif role == "establishing":
        prefix = "Capture clean establishing ambience and room tone."
    else:
        prefix = "Use clean room tone or motivated transition audio under the bridge."
    return f"{prefix} Follow production_brief.audio_direction without adding unrequested sound."


def compile_storyboard_timeline(brief: ProductionBrief) -> dict[str, Any]:
    """Deterministically expand a validated creative brief into planned shot cards.

    The compiler allocates frames and writes editorial guidance only. It does not
    select, alter, or claim the existence of source media.
    """

    total_frames = brief.duration_seconds * STORYBOARD_FRAME_RATE
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
    return ProductionBrief.from_mapping(
        {key: item for key, item in value.items() if key != "schema"}
    )


def _storyboard_package_body(
    brief: ProductionBrief,
    *,
    timeline: Mapping[str, Any],
) -> dict[str, Any]:
    brief_export = brief.to_dict()
    brief_digest = sha256_json(brief_export)
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
            "mutations": [
                "allocate_total_duration_as_contiguous_24fps_frames",
                "expand_each_scene_to_establishing_primary_and_bridge_coverage",
                "attach_plan_only_continuity_and_source_coverage_guidance",
            ],
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
                and "Protect intelligible dialogue coverage."
                in str(shot["storyboard_card"].get("dialogue_or_audio", ""))
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


class JobRepository(Protocol):
    def admit_submission(
        self,
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
        lease_token: str,
        lease_expires_at: str,
        now: str,
    ) -> Mapping[str, Any] | None:
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

    def prepare_retry(self, job_id: str, patch: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def recent_success_durations(self, *, limit: int = 20) -> Sequence[float]:
        ...


class JobDispatcher(Protocol):
    def enqueue(self, job_id: str, *, attempt: int) -> Mapping[str, Any]:
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
        "error",
        "dispatch",
        "execution",
        "target",
        "worker_claim_count",
        "lease_expires_at",
    }
    return {key: record.get(key) for key in sorted(allowed) if key in record}


class AllThingsJobService:
    """Idempotent natural-chat to structured-brief job orchestration."""

    def __init__(
        self,
        *,
        config: AllThingsConfig,
        repository: JobRepository,
        dispatcher: JobDispatcher | None = None,
        provider: BriefProvider | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.dispatcher = dispatcher
        self.provider = provider

    @staticmethod
    def _message(value: Any) -> str:
        if not isinstance(value, str):
            raise AllThingsError("message must be text")
        cleaned = value.strip()
        if not cleaned or len(cleaned) > MAX_MESSAGE_CHARS:
            raise AllThingsError(f"message must contain 1-{MAX_MESSAGE_CHARS} characters")
        return cleaned

    def submit(self, message: str) -> dict[str, Any]:
        self.config.assert_valid(require_dispatch=True)
        if self.dispatcher is None:
            raise ConfigurationError("the API service has no Cloud Tasks dispatcher")
        cleaned = self._message(message)
        now = iso_now()
        self.repository.admit_submission(
            now=now,
            cooldown_seconds=self.config.admission_cooldown_seconds,
            window_seconds=self.config.admission_window_seconds,
            max_jobs=self.config.admission_max_jobs,
        )
        job_id = str(uuid4())
        record: dict[str, Any] = {
            "schema": JOB_SCHEMA,
            "job_id": job_id,
            "parent_job_id": None,
            "message": cleaned,
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
            "error": None,
            "dispatch": None,
            "execution": None,
            "worker_claim_count": 0,
            "lease_token": None,
            "lease_expires_at": None,
            "target": self.config.safe_dict(),
            "target_digest": self.config.target_digest(),
        }
        self.repository.create(record)
        try:
            dispatch = dict(self.dispatcher.enqueue(job_id, attempt=1))
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
                "error": None,
                "dispatch": None,
                "execution": None,
                "worker_claim_count": 0,
                "lease_token": None,
                "lease_expires_at": None,
            },
        )
        attempt = int(retried["attempt"])
        try:
            dispatch = dict(self.dispatcher.enqueue(job_id, attempt=attempt))
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
                },
                attempt=attempt,
            )
            return public_job(failed)
        saved = self.repository.update(
            job_id,
            {"dispatch": dispatch, "updated_at": iso_now()},
        )
        return public_job(saved)

    def execute(self, job_id: str, *, attempt: int) -> dict[str, Any]:
        self.config.assert_valid(require_dispatch=False)
        if self.provider is None:
            raise ConfigurationError("the worker service has no Gemini provider")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise JobTransitionError("Cloud Tasks attempt binding is invalid")
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
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            now=now,
        )
        if claimed is None:
            current = self.repository.get(job_id)
            current_attempt = int(current.get("attempt", 0))
            if current_attempt == attempt and current.get("state") in {
                JobState.RUNNING.value,
                JobState.CANCELLING.value,
            }:
                raise JobLeaseBusyError(
                    "worker lease is still active; Cloud Tasks should retry",
                    retry_after_seconds=_lease_retry_after(current.get("lease_expires_at")),
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
            result = self.provider.create_brief(str(claimed["message"]), job_id=job_id)
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
        except Exception as exc:
            finished_at = utc_now()
            started_at = _parse_time(claimed.get("started_at"))
            failed = self.repository.finalize(
                job_id,
                {
                    "state": JobState.FAILED.value,
                    "stage": failure_code,
                    "updated_at": finished_at.isoformat(),
                    "completed_at": finished_at.isoformat(),
                    "duration_seconds": _elapsed(started_at, finished_at),
                    "eta": eta_payload(durations, progress=100),
                    "error": {
                        "code": failure_code,
                        "type": type(exc).__name__,
                        "retryable": int(claimed.get("attempt", 1))
                        < int(claimed.get("max_attempts", MAX_ATTEMPTS)),
                    },
                    "brief": None,
                    "storyboard_package": None,
                    "execution": None,
                },
                attempt=attempt,
                lease_token=lease_token,
                cancelled_patch=_cancelled_patch(
                    started_at=claimed.get("started_at"),
                    finished_at=finished_at,
                ),
            )
            return public_job(failed)
        finished_at = utc_now()
        started_at = _parse_time(claimed.get("started_at"))
        succeeded = self.repository.finalize(
            job_id,
            {
                "state": JobState.SUCCEEDED.value,
                "stage": (
                    "storyboard_package_ready"
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
                "brief": brief.to_dict(),
                "storyboard_package": storyboard_package,
                "execution": {
                    **dict(result.execution),
                    "pipeline": {
                        "steps": [
                            "gemini_structured_creative_plan",
                            "deterministic_storyboard_timeline_compile",
                            "deterministic_coverage_continuity_audit",
                        ],
                        "storyboard_package_schema": STORYBOARD_PACKAGE_SCHEMA,
                        "manifest_sha256": storyboard_package["manifest_sha256"],
                        "media_status": "unrendered_plan",
                    },
                },
                "error": None,
            },
            attempt=attempt,
            lease_token=lease_token,
            cancelled_patch=_cancelled_patch(
                started_at=claimed.get("started_at"),
                finished_at=finished_at,
            ),
        )
        return public_job(succeeded)

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
        "execution": None,
        "error": None,
    }


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
    "MAX_STORYBOARD_PACKAGE_BYTES",
    "STORYBOARD_PACKAGE_SCHEMA",
    "STORYBOARD_TIMELINE_SCHEMA",
    "BriefProviderResult",
    "BriefValidationError",
    "ConfigurationError",
    "DEFAULT_GEMINI_MODEL",
    "JobLeaseBusyError",
    "JobNotFoundError",
    "JobState",
    "JobTransitionError",
    "ProductionBrief",
    "PRODUCTION_BRIEF_RESPONSE_SCHEMA",
    "audit_storyboard_package",
    "build_storyboard_package",
    "compile_storyboard_timeline",
    "public_job",
    "validate_storyboard_package",
]
