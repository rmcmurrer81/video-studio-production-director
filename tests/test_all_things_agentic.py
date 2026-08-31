from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import unittest

from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    AllThingsConfig,
    AllThingsError,
    AllThingsJobService,
    BriefProviderResult,
    BriefValidationError,
    ConfigurationError,
    JobDispatchPendingError,
    JobLeaseBusyError,
    JobNotFoundError,
    JobState,
    JobTransitionError,
    JobVisualCapacityPendingError,
    MAX_DURABLE_JOB_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_CHARS,
    MAX_PIPELINE_DISPATCHES,
    NARRATED_PITCH_SCHEMA,
    PIPELINE_CONTINUATION_SCHEMA,
    PRODUCTION_BRIEF_RESPONSE_SCHEMA,
    ProductionBrief,
    PipelineCheckpointError,
    STORYBOARD_FRAME_RATE,
    STORYBOARD_PACKAGE_SCHEMA,
    VisualPanelGenerationError,
    VisualPanelProviderResult,
    VISUAL_CAPACITY_REQUEST_LIMIT,
    VISUAL_CAPACITY_SCHEMA,
    VISUAL_CAPACITY_WINDOW_SCHEMA,
    VISUAL_CAPACITY_WINDOW_SECONDS,
    _load_pipeline_checkpoint,
    _visual_capacity_reservation_token,
    _write_pipeline_checkpoint,
    audit_storyboard_package,
    build_storyboard_package,
    build_visual_storyboard,
    canonical_json,
    compile_storyboard_timeline,
    eta_payload,
    fit_visual_storyboard_to_job_budget,
    sha256_json,
    storyboard_panel_prompt,
    visual_owner_review_gate,
    validate_storyboard_package,
    validate_visual_storyboard,
)
from kira_studio.all_things_cloud_media import NarratedPitchRenderError
from kira_studio.all_things_google import (
    CloudTasksDispatcher,
    GoogleGenAIBriefProvider,
    SYSTEM_INSTRUCTION,
    VERTEX_PRODUCTION_BRIEF_RESPONSE_SCHEMA,
)


def valid_config(**overrides: object) -> AllThingsConfig:
    values = {
        "project": "video-studio-12345",
        "location": "global",
        "model": "gemini-3.5-flash",
        "firestore_database": "(default)",
        "jobs_collection": "all_things_agentic_jobs",
        "tasks_location": "us-central1",
        "tasks_queue": "video-studio-production-briefs",
        "worker_url": "https://video-studio-worker-abc-uc.a.run.app",
        "tasks_service_account": "video-studio-tasks@video-studio-12345.iam.gserviceaccount.com",
        "admission_cooldown_seconds": 0,
        "worker_lease_seconds": 1_800,
    }
    values.update(overrides)
    return AllThingsConfig(**values)  # type: ignore[arg-type]


def brief_mapping(*, ready: bool = True) -> dict[str, object]:
    return {
        "title": "The Last Repair",
        "summary": "Two old friends decide whether their failing ship can carry them away from Earth.",
        "format": "dialogue scene",
        "target_audience": "adult science-fiction viewers",
        "duration_seconds": 60,
        "genre": "science fiction drama",
        "tone": ["intimate", "hopeful"],
        "visual_direction": "A grounded repair shop with practical lights and restrained camera movement.",
        "audio_direction": "Quiet machinery, room tone, and dialogue kept clearly above the ambience.",
        "deliverables": ["one-minute dialogue scene", "review copy"],
        "scenes": [
            {
                "number": 1,
                "purpose": "The friends confront the decision and choose to leave together.",
                "setting": "orbital repair shop",
                "characters": ["Mara", "Jon"],
                "dialogue_required": True,
            }
        ],
        "clarifying_questions": [] if ready else ["Should the ending feel hopeful or uncertain?"],
        "ready_for_production": ready,
    }


def explicit_live_shape_mapping() -> dict[str, object]:
    value = brief_mapping()
    value.update(
        {
            "title": "Harbor Signal",
            "summary": "Lila and Theo repair a storm-damaged transmitter and warn the harbor.",
            "duration_seconds": 54,
            "scenes": [
                {
                    "number": index,
                    "purpose": purpose,
                    "setting": setting,
                    "characters": ["Lila", "Theo"],
                    "dialogue_required": logical_scene in {1, 3},
                }
                for index, (logical_scene, purpose, setting) in enumerate(
                    (
                        (1, "Shot 1.1: Establish the cramped harbor radio workshop as a storm rattles the windows.", "HARBOR RADIO WORKSHOP, DAWN"),
                        (1, "Shot 1.2: Close-up of Lila holding a handwritten frequency note beside the receiver.", "HARBOR RADIO WORKSHOP, DAWN"),
                        (1, "Shot 1.3: Medium shot of Theo nodding and grabbing the tool bag.", "HARBOR RADIO WORKSHOP, DAWN"),
                        (2, "Shot 2.1: Wide shot establishes the storm-battered rooftop transmitter.", "ROOFTOP TRANSMITTER, STORM"),
                        (2, "Shot 2.2: Close-up of a fraying cable whipping against the antenna mast.", "ROOFTOP TRANSMITTER, STORM"),
                        (2, "Shot 2.3: Full-body shot of Lila and Theo struggling together to secure the cable.", "ROOFTOP TRANSMITTER, STORM"),
                        (3, "Shot 3.1: The radio receiver sparks and a green indicator turns on.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
                        (3, "Shot 3.2: Lila broadcasts a clear warning into the microphone.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
                        (3, "Shot 3.3: Lila and Theo exchange relieved looks as the storm begins to fade.", "BACK IN HARBOR RADIO WORKSHOP, MOMENTS LATER"),
                    ),
                    start=1,
                )
            ],
        }
    )
    return value


def full_screenplay_mapping() -> dict[str, object]:
    value = brief_mapping()
    value.update(
        {
            "title": "The Thirty-Six Decisions",
            "summary": (
                "Twelve linked sequences follow two engineers as they protect a "
                "failing orbital refuge and decide what future they can still build."
            ),
            "duration_seconds": 720,
            "scenes": [
                {
                    "number": number,
                    "purpose": (
                        f"Sequence {number} advances the orbital-refuge crisis and "
                        "preserves the engineers' shared decision across the next beat."
                    ),
                    "setting": f"orbital refuge sector {number}",
                    "characters": ["Mara", "Jon"],
                    "dialogue_required": True,
                }
                for number in range(1, 13)
            ],
        }
    )
    return value


def browser_json_number_roundtrip(value: object) -> object:
    """Model JavaScript JSON.stringify's integral-number representation."""

    if isinstance(value, dict):
        return {key: browser_json_number_roundtrip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_json_number_roundtrip(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class MemoryRepository:
    def __init__(self, *, enforce_visual_capacity: bool = False) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.admission: dict[str, object] | None = None
        self.enforce_visual_capacity = enforce_visual_capacity
        self.visual_reservations: list[datetime] = []
        self.visual_windows: list[dict[str, object]] = []
        self.confirm_failures_remaining = 0

    @staticmethod
    def _continuation_token(value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        window = value.get("visual_capacity_window")
        if not isinstance(window, dict):
            return None
        token = window.get("reservation_token")
        return token if isinstance(token, str) else None

    def _release_visual_tokens(self, *tokens: str | None) -> None:
        selected = {token for token in tokens if token is not None}
        if not selected:
            return
        head_removed = bool(
            self.visual_windows
            and self.visual_windows[0].get("reservation_token") in selected
        )
        self.visual_windows = [
            item
            for item in self.visual_windows
            if item.get("reservation_token") not in selected
        ]
        if head_removed and self.visual_windows:
            now_epoch = math.ceil(time.time())
            prior = now_epoch + VISUAL_CAPACITY_WINDOW_SECONDS
            for position, item in enumerate(self.visual_windows):
                if position:
                    prior += VISUAL_CAPACITY_WINDOW_SECONDS
                item["not_before_epoch_seconds"] = max(
                    int(item["not_before_epoch_seconds"]), prior
                )
                prior = int(item["not_before_epoch_seconds"])

    @staticmethod
    def _active_slot(record: dict[str, object], *, now: datetime) -> dict[str, object]:
        target = record.get("target")
        lease_seconds = (
            int(target.get("worker_lease_seconds", 1_800))
            if isinstance(target, dict)
            else 1_800
        )
        record_expires_at = datetime.fromisoformat(str(record["record_expires_at"]))
        return {
            "job_id": str(record["job_id"]),
            "attempt": int(record["attempt"]),
            "slot_expires_at": (
                record_expires_at + timedelta(seconds=lease_seconds)
            ).isoformat(),
        }

    def _release_admission_slot(self, job_id: str) -> None:
        if self.admission is None:
            return
        active = self.admission.get("active_slots")
        if isinstance(active, list):
            self.admission["active_slots"] = [
                item
                for item in active
                if isinstance(item, dict) and item.get("job_id") != job_id
            ]

    def admit_submission(
        self,
        record: dict[str, object],
        *,
        now: str,
        cooldown_seconds: int,
        window_seconds: int,
        max_jobs: int,
    ) -> dict[str, object]:
        now_value = datetime.fromisoformat(now)
        if str(record["job_id"]) in self.records:
            raise JobTransitionError("job already exists")
        current = deepcopy(self.admission) if self.admission else None
        active_slots = (
            [
                item
                for item in current.get("active_slots", [])
                if isinstance(item, dict)
                and datetime.fromisoformat(str(item["slot_expires_at"])) > now_value
            ]
            if current
            else []
        )
        window_started = (
            datetime.fromisoformat(str(current["window_started_at"])) if current else now_value
        )
        if current is None or (now_value - window_started).total_seconds() >= window_seconds:
            count = 0
            last_admitted = None
            window_started = now_value
        else:
            count = int(current["count"])
            last_admitted = datetime.fromisoformat(str(current["last_admitted_at"]))
        retry_after = 0
        if count >= max_jobs:
            retry_after = max(
                retry_after,
                int(window_seconds - (now_value - window_started).total_seconds() + 0.999),
            )
        if last_admitted is not None and cooldown_seconds:
            retry_after = max(
                retry_after,
                int(cooldown_seconds - (now_value - last_admitted).total_seconds() + 0.999),
            )
        if len(active_slots) >= max_jobs:
            earliest = min(
                datetime.fromisoformat(str(item["slot_expires_at"]))
                for item in active_slots
            )
            retry_after = max(
                retry_after,
                max(1, math.ceil((earliest - now_value).total_seconds())),
            )
        if retry_after > 0:
            raise AdmissionLimitError(
                "shared demo job admission limit reached",
                retry_after_seconds=retry_after,
            )
        active_slots.append(self._active_slot(record, now=now_value))
        self.admission = {
            "window_started_at": window_started.isoformat(),
            "last_admitted_at": now_value.isoformat(),
            "count": count + 1,
            "active_slots": active_slots,
        }
        self.records[str(record["job_id"])] = deepcopy(record)
        return deepcopy(self.admission)

    def create(self, record: dict[str, object]) -> dict[str, object]:
        job_id = str(record["job_id"])
        self.records[job_id] = deepcopy(record)
        return deepcopy(record)

    def reserve_visual_request(
        self,
        *,
        now: str,
        window_seconds: int,
        max_requests: int,
        reservation_token: str,
    ) -> dict[str, object]:
        now_value = datetime.fromisoformat(now)
        window = next(
            (
                item
                for item in self.visual_windows
                if item.get("reservation_token") == reservation_token
            ),
            None,
        )
        if window is None:
            return {
                "schema": VISUAL_CAPACITY_SCHEMA,
                "granted": False,
                "retry_after_seconds": 1,
                "window_seconds": window_seconds,
                "request_limit": max_requests,
                "reservation_count": 0,
                "window_active": False,
            }
        if not self.enforce_visual_capacity:
            # Even the permissive fixture records the real provider request so
            # the next FIFO window receives the production 75-second spacing.
            # It skips only denial behavior, not durable pacing metadata.
            self.visual_reservations.append(now_value)
            window["requests_used"] = int(window["requests_used"]) + 1
            return {
                "schema": VISUAL_CAPACITY_SCHEMA,
                "granted": True,
                "retry_after_seconds": 0,
                "window_seconds": window_seconds,
                "request_limit": max_requests,
                "reservation_count": 1,
                "window_active": True,
            }
        self.visual_reservations = [
            value
            for value in self.visual_reservations
            if (now_value - value).total_seconds() < window_seconds
        ]
        is_head = self.visual_windows[0] is window
        not_before = int(window["not_before_epoch_seconds"])
        granted = (
            is_head
            and math.ceil(now_value.timestamp()) >= not_before
            and len(self.visual_reservations) < max_requests
            and int(window["requests_used"]) < max_requests
        )
        retry_after = 0
        if granted:
            self.visual_reservations.append(now_value)
            window["requests_used"] = int(window["requests_used"]) + 1
        else:
            retry_after = max(
                1,
                not_before - math.ceil(now_value.timestamp()),
            )
        return {
            "schema": VISUAL_CAPACITY_SCHEMA,
            "granted": granted,
            "retry_after_seconds": retry_after,
            "window_seconds": window_seconds,
            "request_limit": max_requests,
            "reservation_count": len(self.visual_reservations),
            "window_active": True,
        }

    def prepare_visual_window(
        self,
        *,
        now: str,
        reservation_token: str,
        window_seconds: int,
        max_requests: int,
    ) -> dict[str, object]:
        now_value = datetime.fromisoformat(now)
        self.visual_reservations = [
            value
            for value in self.visual_reservations
            if (now_value - value).total_seconds() < window_seconds
        ]
        existing = next(
            (
                item
                for item in self.visual_windows
                if item.get("reservation_token") == reservation_token
            ),
            None,
        )
        if existing is None:
            not_before = math.ceil(now_value.timestamp())
            if self.visual_windows:
                not_before = max(
                    not_before,
                    int(self.visual_windows[-1]["not_before_epoch_seconds"])
                    + window_seconds,
                )
            elif self.visual_reservations:
                not_before = max(
                    not_before,
                    math.ceil(self.visual_reservations[-1].timestamp())
                    + window_seconds,
                )
            existing = {
                "reservation_token": reservation_token,
                "not_before_epoch_seconds": not_before,
                "requests_used": 0,
            }
            self.visual_windows.append(existing)
        return {
            "schema": VISUAL_CAPACITY_WINDOW_SCHEMA,
            "reservation_token": reservation_token,
            "not_before_epoch_seconds": int(existing["not_before_epoch_seconds"]),
            "request_limit": max_requests,
            "window_seconds": window_seconds,
        }

    def complete_visual_window(
        self,
        *,
        now: str,
        reservation_token: str,
        window_seconds: int,
        max_requests: int,
    ) -> dict[str, object]:
        before = len(self.visual_windows)
        self._release_visual_tokens(reservation_token)
        return {
            "schema": VISUAL_CAPACITY_SCHEMA,
            "released": len(self.visual_windows) != before,
            "queue_depth": len(self.visual_windows),
        }

    def get(self, job_id: str) -> dict[str, object]:
        if job_id not in self.records:
            raise JobNotFoundError(job_id)
        return deepcopy(self.records[job_id])

    def update(self, job_id: str, patch: dict[str, object]) -> dict[str, object]:
        if job_id not in self.records:
            raise JobNotFoundError(job_id)
        self.records[job_id].update(deepcopy(patch))
        return self.get(job_id)

    def claim(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        lease_expires_at: str,
        now: str,
    ) -> dict[str, object] | None:
        record = self.get(job_id)
        record_expires_at = datetime.fromisoformat(str(record["record_expires_at"]))
        if datetime.fromisoformat(now) >= record_expires_at:
            return None
        if (
            int(record["attempt"]) != attempt
            or int(record.get("dispatch_sequence", -1)) != dispatch_sequence
        ):
            return None
        state = record["state"]
        reclaiming = state in {JobState.RUNNING.value, JobState.CANCELLING.value}
        if reclaiming:
            expiry = record.get("lease_expires_at")
            if isinstance(expiry, str) and datetime.fromisoformat(expiry) > datetime.fromisoformat(now):
                return None
        elif state != JobState.QUEUED.value or record["cancel_requested"]:
            return None
        update = {
            **patch,
            "lease_token": lease_token,
            "lease_expires_at": lease_expires_at,
            "worker_claim_count": int(record.get("worker_claim_count", 0)) + 1,
        }
        if reclaiming:
            update["started_at"] = record.get("started_at") or now
            if record.get("cancel_requested"):
                update["state"] = JobState.CANCELLING.value
        pending = record.get("pending_dispatch")
        if (
            isinstance(pending, dict)
            and pending.get("application_attempt") == attempt
            and pending.get("dispatch_sequence") == dispatch_sequence
        ):
            update["pending_dispatch"] = None
        return self.update(job_id, update)

    def continue_job(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        cancelled_patch: dict[str, object],
    ) -> dict[str, object]:
        record = self.get(job_id)
        current_token = self._continuation_token(record.get("continuation"))
        proposed_token = self._continuation_token(patch.get("continuation"))
        sequence_matches = (
            int(record["attempt"]) == attempt
            and int(record.get("dispatch_sequence", -1)) == dispatch_sequence
        )
        if not sequence_matches or record.get("lease_token") != lease_token:
            # A replacement worker for the same application attempt and
            # dispatch sequence derives the same successor token.  A stale
            # predecessor must not remove that shared prepared token merely
            # because its lease no longer matches while that replacement is
            # still RUNNING.  A terminal record or an advanced sequence makes
            # the proposed token unambiguously provisional, so clean it up.
            if (
                (
                    record["state"]
                    in {
                        JobState.CANCELLED.value,
                        JobState.SUCCEEDED.value,
                        JobState.FAILED.value,
                        JobState.CANCELLING.value,
                    }
                    or not sequence_matches
                )
                and proposed_token != current_token
            ):
                self._release_visual_tokens(proposed_token)
            return record
        selected = (
            cancelled_patch
            if record.get("cancel_requested")
            or record["state"] == JobState.CANCELLING.value
            else patch
        )
        if selected is cancelled_patch:
            selected = {**selected, "pending_dispatch": None}
            self._release_visual_tokens(current_token, proposed_token)
            self._release_admission_slot(job_id)
        elif current_token != proposed_token:
            self._release_visual_tokens(current_token)
        return self.update(
            job_id,
            {**selected, "lease_token": None, "lease_expires_at": None},
        )

    def defer_claimed(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
        dispatch_sequence: int,
        lease_token: str,
        cancelled_patch: dict[str, object],
    ) -> dict[str, object]:
        record = self.get(job_id)
        if (
            int(record["attempt"]) != attempt
            or int(record.get("dispatch_sequence", -1)) != dispatch_sequence
            or record.get("lease_token") != lease_token
        ):
            return record
        cancellation_wins = bool(record.get("cancel_requested")) or (
            record["state"] == JobState.CANCELLING.value
        )
        if cancellation_wins:
            self._release_visual_tokens(
                self._continuation_token(record.get("continuation"))
            )
            selected = {**cancelled_patch, "pending_dispatch": None}
            self._release_admission_slot(job_id)
        else:
            selected = {
                **patch,
                "state": JobState.QUEUED.value,
                "dispatch_sequence": dispatch_sequence,
            }
        return self.update(
            job_id,
            {**selected, "lease_token": None, "lease_expires_at": None},
        )

    def confirm_continuation_dispatch(
        self,
        job_id: str,
        dispatch: dict[str, object],
        *,
        attempt: int,
        dispatch_sequence: int,
        pending_manifest_sha256: str,
    ) -> dict[str, object]:
        if self.confirm_failures_remaining:
            self.confirm_failures_remaining -= 1
            raise RuntimeError("lost confirm response")
        record = self.get(job_id)
        pending = record.get("pending_dispatch")
        if pending is None:
            return record
        if (
            not isinstance(pending, dict)
            or pending.get("manifest_sha256") != pending_manifest_sha256
            or pending.get("application_attempt") != attempt
            or pending.get("dispatch_sequence") != dispatch_sequence
        ):
            raise JobTransitionError("pending continuation dispatch changed")
        if (
            int(record["attempt"]) != attempt
            or int(record.get("dispatch_sequence", -1)) != dispatch_sequence
            or record["state"] != JobState.QUEUED.value
            or record.get("cancel_requested")
        ):
            return record
        return self.update(
            job_id,
            {"dispatch": deepcopy(dispatch), "pending_dispatch": None},
        )

    def update_claimed(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
        lease_token: str,
    ) -> dict[str, object]:
        record = self.get(job_id)
        if (
            int(record["attempt"]) != attempt
            or record.get("lease_token") != lease_token
            or record["state"] not in {JobState.RUNNING.value, JobState.CANCELLING.value}
            or record.get("cancel_requested")
        ):
            return record
        return self.update(job_id, patch)

    def finalize(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
        lease_token: str,
        cancelled_patch: dict[str, object],
    ) -> dict[str, object]:
        record = self.get(job_id)
        if int(record["attempt"]) != attempt or record.get("lease_token") != lease_token:
            return record
        if record["state"] in {JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value}:
            return record
        selected = (
            cancelled_patch
            if record.get("cancel_requested") or record["state"] == JobState.CANCELLING.value
            else patch
        )
        successor_sequence = int(record.get("dispatch_sequence", -1)) + 1
        prepared_successor = (
            _visual_capacity_reservation_token(
                job_id=job_id,
                attempt=attempt,
                dispatch_sequence=successor_sequence,
            )
            if 1 <= successor_sequence < MAX_PIPELINE_DISPATCHES
            else None
        )
        self._release_visual_tokens(
            self._continuation_token(record.get("continuation")),
            prepared_successor,
        )
        self._release_admission_slot(job_id)
        return self.update(
            job_id,
            {**selected, "lease_token": None, "lease_expires_at": None},
        )

    def mark_dispatch_failed(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        attempt: int,
    ) -> dict[str, object]:
        record = self.get(job_id)
        if int(record["attempt"]) != attempt or record["state"] != JobState.QUEUED.value:
            return record
        self._release_admission_slot(job_id)
        return self.update(job_id, patch)

    def request_cancel(self, job_id: str, *, now: str) -> dict[str, object]:
        record = self.get(job_id)
        if record["state"] in {JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value}:
            return record
        if record["state"] == JobState.QUEUED.value:
            self._release_visual_tokens(
                self._continuation_token(record.get("continuation"))
            )
            self._release_admission_slot(job_id)
            return self.update(
                job_id,
                {
                    "state": JobState.CANCELLED.value,
                    "stage": "cancelled_before_worker_start",
                    "progress": 100,
                    "cancel_requested": True,
                    "updated_at": now,
                    "completed_at": now,
                    "duration_seconds": 0.0,
                    "dispatch": None,
                    "continuation": None,
                    "pending_dispatch": None,
                },
            )
        return self.update(
            job_id,
            {
                "state": JobState.CANCELLING.value,
                "stage": "cancellation_requested",
                "cancel_requested": True,
                "updated_at": now,
            },
        )

    def prepare_retry(
        self,
        job_id: str,
        patch: dict[str, object],
        *,
        now: str,
        cooldown_seconds: int,
        window_seconds: int,
        max_jobs: int,
    ) -> dict[str, object]:
        record = self.get(job_id)
        if record["state"] not in {JobState.FAILED.value, JobState.CANCELLED.value}:
            raise JobTransitionError("invalid retry")
        if int(record["attempt"]) >= int(record["max_attempts"]):
            raise JobTransitionError("retry limit")
        now_value = datetime.fromisoformat(now)
        current = deepcopy(self.admission) if self.admission else {
            "window_started_at": now,
            "last_admitted_at": now,
            "count": 0,
            "active_slots": [],
        }
        active_slots = [
            item
            for item in current.get("active_slots", [])
            if isinstance(item, dict)
            and item.get("job_id") != job_id
            and datetime.fromisoformat(str(item["slot_expires_at"])) > now_value
        ]
        if len(active_slots) >= max_jobs:
            earliest = min(
                datetime.fromisoformat(str(item["slot_expires_at"]))
                for item in active_slots
            )
            raise AdmissionLimitError(
                "shared demo active-job limit reached",
                retry_after_seconds=max(
                    1, math.ceil((earliest - now_value).total_seconds())
                ),
            )
        update = {**patch, "attempt": int(record["attempt"]) + 1}
        retried = {**record, **update}
        active_slots.append(self._active_slot(retried, now=now_value))
        current["active_slots"] = active_slots
        self.admission = current
        return self.update(job_id, update)

    def recent_success_durations(self, *, limit: int = 20) -> tuple[float, ...]:
        return tuple(
            float(record["duration_seconds"])
            for record in list(self.records.values())[-limit:]
            if record.get("state") == JobState.SUCCEEDED.value
            and isinstance(record.get("duration_seconds"), (int, float))
            and float(record["duration_seconds"]) > 0
        )


class RecordingDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.job_ids: list[str] = []
        self.attempts: list[int] = []
        self.dispatch_sequences: list[int] = []
        self.delay_seconds: list[int] = []
        self.scheduled_epoch_seconds: list[int | None] = []
        self.fail = fail
        self.failures_remaining = 0

    def enqueue(
        self,
        job_id: str,
        *,
        attempt: int,
        dispatch_sequence: int,
        delay_seconds: int = 0,
        scheduled_epoch_seconds: int | None = None,
    ) -> dict[str, object]:
        self.job_ids.append(job_id)
        self.attempts.append(attempt)
        self.dispatch_sequences.append(dispatch_sequence)
        self.delay_seconds.append(delay_seconds)
        self.scheduled_epoch_seconds.append(scheduled_epoch_seconds)
        if self.fail or self.failures_remaining:
            if self.failures_remaining:
                self.failures_remaining -= 1
            raise RuntimeError("unavailable")
        scheduled = scheduled_epoch_seconds
        if scheduled is None and delay_seconds:
            scheduled = math.ceil(time.time()) + delay_seconds
        return {
            "provider": "Google Cloud Tasks",
            "task_name": (
                f"projects/p/locations/l/queues/q/tasks/"
                f"{job_id}-a{attempt}-d{dispatch_sequence:03d}"
            ),
            "attempt": attempt,
            "dispatch_sequence": dispatch_sequence,
            "schedule_delay_seconds": delay_seconds,
            "scheduled_epoch_seconds": scheduled,
        }


class StaticProvider:
    def __init__(self, *, error: Exception | None = None, callback: object | None = None) -> None:
        self.error = error
        self.callback = callback
        self.calls: list[tuple[str, str]] = []

    def create_brief(self, message: str, *, job_id: str) -> BriefProviderResult:
        self.calls.append((message, job_id))
        if callable(self.callback):
            self.callback(job_id)
        if self.error:
            raise self.error
        return BriefProviderResult(
            brief=ProductionBrief.from_mapping(brief_mapping()),
            execution={"evidence_origin": "test_double", "provider": "test"},
        )


class FullScreenplayProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_brief(self, message: str, *, job_id: str) -> BriefProviderResult:
        self.calls.append((message, job_id))
        return BriefProviderResult(
            brief=ProductionBrief.from_mapping(full_screenplay_mapping()),
            execution={"evidence_origin": "test_double", "provider": "test"},
        )


class MaximumScreenplayProvider:
    """Return the schema maximum: forty scenes and 120 compiled cards."""

    def create_brief(self, message: str, *, job_id: str) -> BriefProviderResult:
        value = brief_mapping()
        value.update(
            {
                "duration_seconds": 2_400,
                "scenes": [
                    {
                        "number": number,
                        "purpose": f"Advance maximum-size sequence {number}.",
                        "setting": f"production location {number}",
                        "characters": ["Mara", "Jon"],
                        "dialogue_required": True,
                    }
                    for number in range(1, 41)
                ],
            }
        )
        return BriefProviderResult(
            brief=ProductionBrief.from_mapping(value),
            execution={"evidence_origin": "test_double", "provider": "test"},
        )


class OneInactiveWindowPerVisualAttemptRepository(MemoryRepository):
    """Force one fail-closed FIFO reconciliation before each provider attempt."""

    def __init__(self) -> None:
        super().__init__()
        self.prepared_window_count = 0
        self.capacity_wait_count = 0
        self._inactive_tokens: set[str] = set()

    def prepare_visual_window(self, **kwargs: object) -> dict[str, object]:
        token = str(kwargs["reservation_token"])
        existed = any(
            item.get("reservation_token") == token for item in self.visual_windows
        )
        result = super().prepare_visual_window(**kwargs)  # type: ignore[arg-type]
        if not existed:
            self.prepared_window_count += 1
            if self.prepared_window_count % 2:
                self._inactive_tokens.add(token)
        return result

    def reserve_visual_request(self, **kwargs: object) -> dict[str, object]:
        token = str(kwargs["reservation_token"])
        if token in self._inactive_tokens:
            self._inactive_tokens.remove(token)
            self._release_visual_tokens(token)
            self.capacity_wait_count += 1
            return {
                "schema": VISUAL_CAPACITY_SCHEMA,
                "granted": False,
                "retry_after_seconds": 1,
                "window_seconds": int(kwargs["window_seconds"]),
                "request_limit": int(kwargs["max_requests"]),
                "reservation_count": 0,
                "window_active": False,
            }
        return super().reserve_visual_request(**kwargs)  # type: ignore[arg-type]


class StaticVisualProvider:
    def __init__(self, *, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.calls: list[tuple[str, str, str, bool]] = []
        self.image = b"\xff\xd8" + (b"visual-storyboard" * 20) + b"\xff\xd9"

    def create_panel(
        self,
        prompt: str,
        *,
        shot_id: str,
        job_id: str,
        reference_image: bytes | None = None,
    ) -> VisualPanelProviderResult:
        self.calls.append((prompt, shot_id, job_id, reference_image is not None))
        if self.error_code:
            raise VisualPanelGenerationError(self.error_code)
        return VisualPanelProviderResult(
            image_bytes=self.image,
            mime_type="image/jpeg",
            width=768,
            height=432,
            execution={"evidence_origin": "injected_test_client"},
        )


class FailingVisualProvider:
    def __init__(self, *errors: Exception) -> None:
        self.errors = errors
        self.call_count = 0

    def create_panel(self, *_args: object, **_kwargs: object) -> VisualPanelProviderResult:
        error = self.errors[min(self.call_count, len(self.errors) - 1)]
        self.call_count += 1
        raise error


class StaticArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(
        self,
        *,
        job_id: str,
        artifact_id: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, object]:
        digest = hashlib.sha256(data).hexdigest()
        object_name = f"jobs/{job_id}/artifacts/{digest}/{artifact_id}"
        self.objects[object_name] = bytes(data)
        return {
            "artifact_id": artifact_id,
            "object_name": object_name,
            "content_type": content_type,
            "sha256": digest,
            "bytes": len(data),
        }

    def get_bytes(self, object_name: str) -> bytes:
        return self.objects[object_name]


class FailingNarratedPitchRenderer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def render(self, **_kwargs: object) -> dict[str, object]:
        raise self.error

    def render_segment_chunk(self, **_kwargs: object) -> list[dict[str, object]]:
        raise self.error

    def finalize_segments(self, **_kwargs: object) -> dict[str, object]:
        raise self.error


class StaticNarratedPitchRenderer:
    def __init__(self, artifact_store: StaticArtifactStore) -> None:
        self.artifact_store = artifact_store
        self.calls = 0
        self.segment_calls: list[int] = []
        self.finalize_calls = 0

    def render(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        timeline = kwargs["timeline"]
        job_id = str(kwargs["job_id"])
        assert isinstance(timeline, dict)
        shot_count = len(timeline["shots"])
        stored = self.artifact_store.put_bytes(
            job_id=job_id,
            artifact_id="narrated-pitch.mp4",
            data=b"focused-test-mp4",
            content_type="video/mp4",
        )
        video = {
            **stored,
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
        }
        body: dict[str, object] = {
            "schema": NARRATED_PITCH_SCHEMA,
            "status": "complete",
            "card_count": shot_count,
            "cue_count": shot_count,
            "video": video,
        }
        body["manifest_sha256"] = sha256_json(body)
        return body

    def render_segment_chunk(self, **kwargs: object) -> list[dict[str, object]]:
        start_index = int(kwargs["start_index"])
        ownership_check = kwargs.get("ownership_check")
        if callable(ownership_check) and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")
        self.segment_calls.append(start_index)
        timeline = kwargs["timeline"]
        job_id = str(kwargs["job_id"])
        assert isinstance(timeline, dict)
        shot = timeline["shots"][start_index]
        sequence = start_index + 1
        stored = self.artifact_store.put_bytes(
            job_id=job_id,
            artifact_id=f"pitch-card-{sequence:04d}.mp4",
            data=f"focused-segment-{sequence}".encode("ascii"),
            content_type="video/mp4",
        )
        return [
            {
                "schema": "video-studio.narrated-pitch-segment/v1",
                "sequence": sequence,
                "shot_id": shot["shot_id"],
                "duration_seconds": 1.0,
                "artifact_id": stored["artifact_id"],
                "object_name": stored["object_name"],
                "content_type": stored["content_type"],
                "sha256": stored["sha256"],
                "byte_length": stored["bytes"],
            }
        ]

    def finalize_segments(self, **kwargs: object) -> dict[str, object]:
        ownership_check = kwargs.get("ownership_check")
        if callable(ownership_check) and not ownership_check():
            raise NarratedPitchRenderError("work_stopped")
        self.finalize_calls += 1
        return self.render(**kwargs)


class UniqueVisualProvider:
    def __init__(self, *, cancel_after_call: int | None = None) -> None:
        self.cancel_after_call = cancel_after_call
        self.cancel_callback: object | None = None
        self.calls: list[tuple[str, str, str, bool]] = []
        self.reference_images: list[bytes | None] = []

    def create_panel(
        self,
        prompt: str,
        *,
        shot_id: str,
        job_id: str,
        reference_image: bytes | None = None,
    ) -> VisualPanelProviderResult:
        self.calls.append((prompt, shot_id, job_id, reference_image is not None))
        self.reference_images.append(reference_image)
        if self.cancel_after_call == len(self.calls) and callable(self.cancel_callback):
            self.cancel_callback(job_id)
        image = b"\xff\xd8" + (shot_id.encode("ascii") * 12) + b"\xff\xd9"
        return VisualPanelProviderResult(
            image_bytes=image,
            mime_type="image/jpeg",
            width=768,
            height=432,
            execution={"evidence_origin": "injected_test_client"},
        )


class QuotaPatternVisualProvider(UniqueVisualProvider):
    def __init__(self, *, quota_calls: set[int] | None = None) -> None:
        super().__init__()
        self.quota_calls = set(quota_calls or ())
        self.attempted_shot_ids: list[str] = []

    def create_panel(
        self,
        prompt: str,
        *,
        shot_id: str,
        job_id: str,
        reference_image: bytes | None = None,
    ) -> VisualPanelProviderResult:
        call_number = len(self.attempted_shot_ids) + 1
        self.attempted_shot_ids.append(shot_id)
        if call_number in self.quota_calls or -1 in self.quota_calls:
            try:
                raise RuntimeError(
                    "PRIVATE provider payload, project number, and screenplay must stay redacted"
                )
            except RuntimeError:
                raise VisualPanelGenerationError("quota_or_rate_limited") from None
        return super().create_panel(
            prompt,
            shot_id=shot_id,
            job_id=job_id,
            reference_image=reference_image,
        )


class FakeModels:
    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.generate_calls: list[dict[str, object]] = []

    def get(self, *, model: str) -> object:
        self.get_calls.append(model)
        return object()

    def generate_content(self, **kwargs: object) -> object:
        self.generate_calls.append(dict(kwargs))
        return type(
            "Response",
            (),
            {"text": json.dumps(brief_mapping()), "response_id": "test-response"},
        )()


class FakeGenAIClient:
    def __init__(self) -> None:
        self.models = FakeModels()


class FakeTasksClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        task = kwargs["task"]
        return type("Task", (), {"name": task["name"]})()


class AllThingsAgenticTests(unittest.TestCase):
    def _execute_visual_storyboard_failure(
        self,
        visual_provider: object,
        *,
        source_message: str = "Make a short scene.",
        config: AllThingsConfig | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        repository = MemoryRepository()
        service = AllThingsJobService(
            config=config or valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(),
            provider=StaticProvider(),
            visual_provider=visual_provider,  # type: ignore[arg-type]
            artifact_store=StaticArtifactStore(),
        )
        queued = service.submit(source_message)
        job_id = str(queued["job_id"])
        failed = dict(queued)
        while failed["state"] == JobState.QUEUED.value:
            raw = repository.get(job_id)
            failed = dict(
                service.execute(
                    job_id,
                    attempt=1,
                    dispatch_sequence=int(raw["dispatch_sequence"]),
                )
            )
        return failed, repository.get(str(queued["job_id"]))

    def test_visual_storyboard_failure_records_unique_allowlisted_reason(self) -> None:
        failed, durable = self._execute_visual_storyboard_failure(
            StaticVisualProvider(error_code="quota_or_rate_limited"),
            config=valid_config(visual_quota_max_deferrals=0),
        )

        self.assertEqual(failed["stage"], "visual_storyboard_incomplete")
        self.assertEqual(failed["error"]["diagnostic_code"], "quota_or_rate_limited")
        self.assertEqual(durable["error"]["diagnostic_code"], "quota_or_rate_limited")

    def test_visual_storyboard_chunk_fails_fast_on_first_allowlisted_reason(self) -> None:
        failed, _durable = self._execute_visual_storyboard_failure(
            FailingVisualProvider(
                VisualPanelGenerationError("provider_blocked"),
                VisualPanelGenerationError("quota_or_rate_limited"),
            )
        )

        self.assertEqual(failed["error"]["diagnostic_code"], "provider_blocked")

    def test_visual_storyboard_failure_redacts_arbitrary_exception_and_source_text(self) -> None:
        source_text = "PRIVATE SCREENPLAY: the vault phrase is amber-nine."
        exception_text = f"provider response echoed {source_text} from C:\\private\\panel.png"
        failed, durable = self._execute_visual_storyboard_failure(
            FailingVisualProvider(RuntimeError(exception_text)),
            source_message=source_text,
        )

        self.assertEqual(failed["error"]["diagnostic_code"], "generation_failed")
        self.assertEqual(durable["error"]["diagnostic_code"], "generation_failed")
        self.assertNotIn(exception_text, json.dumps(failed))
        self.assertNotIn(source_text, json.dumps(failed))
        self.assertNotIn(exception_text, json.dumps(durable["error"]))

    def _execute_pitch_render_failure(
        self,
        error: Exception,
        *,
        source_message: str = "Make a short scene.",
    ) -> tuple[dict[str, object], dict[str, object]]:
        repository = MemoryRepository()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(),
            provider=StaticProvider(),
            visual_provider=StaticVisualProvider(),
            artifact_store=StaticArtifactStore(),
            narrated_pitch_renderer=FailingNarratedPitchRenderer(error),
        )
        queued = service.submit(source_message)
        job_id = str(queued["job_id"])
        failed = dict(queued)
        while failed["state"] == JobState.QUEUED.value:
            raw = repository.get(job_id)
            failed = dict(
                service.execute(
                    job_id,
                    attempt=1,
                    dispatch_sequence=int(raw["dispatch_sequence"]),
                )
            )
        return failed, repository.get(str(queued["job_id"]))

    def test_narrated_pitch_failure_records_allowlisted_diagnostic_code(self) -> None:
        failed, durable = self._execute_pitch_render_failure(
            NarratedPitchRenderError("pitch_probe_failed")
        )

        self.assertEqual(failed["state"], JobState.FAILED.value)
        self.assertEqual(failed["error"]["code"], "narrated_pitch_render_failed")
        self.assertEqual(failed["error"]["diagnostic_code"], "pitch_probe_failed")
        self.assertEqual(durable["error"]["diagnostic_code"], "pitch_probe_failed")

    def test_narrated_pitch_failure_redacts_arbitrary_exception_and_source_text(self) -> None:
        source_text = "PRIVATE SCREENPLAY: the launch phrase is violet-seven."
        exception_text = f"ffprobe stderr echoed {source_text} from C:\\private\\source.mov"
        for error in (
            NarratedPitchRenderError(exception_text),
            RuntimeError(exception_text),
        ):
            with self.subTest(error_type=type(error).__name__):
                failed, durable = self._execute_pitch_render_failure(
                    error,
                    source_message=source_text,
                )

                self.assertNotIn("diagnostic_code", failed["error"])
                self.assertNotIn("diagnostic_code", durable["error"])
                self.assertNotIn(exception_text, json.dumps(failed))
                self.assertNotIn(source_text, json.dumps(failed))
                self.assertNotIn(exception_text, json.dumps(durable["error"]))

    def test_configuration_requires_verified_contest_model_family_and_real_targets(self) -> None:
        config = valid_config()
        self.assertEqual(config.issues(), ())
        self.assertEqual(config.visual_panels_per_dispatch, 2)
        self.assertEqual(config.visual_successor_delay_seconds, 75)
        self.assertEqual(config.visual_quota_max_deferrals, 4)
        self.assertEqual(config.visual_quota_base_deferral_seconds, 90)
        self.assertEqual(config.visual_quota_max_deferral_seconds, 720)
        self.assertEqual(config.safe_dict()["visual_successor_delay_seconds"], 75)
        self.assertNotEqual(
            config.target_digest(),
            valid_config(visual_successor_delay_seconds=76).target_digest(),
        )
        for invalid_panels in (1, 3):
            with self.subTest(visual_panels_per_dispatch=invalid_panels):
                with self.assertRaises(ConfigurationError):
                    valid_config(
                        visual_panels_per_dispatch=invalid_panels
                    ).assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(model="gemini-2.5-flash").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(model="future-model").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(image_model="imagen-4").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(project="", worker_url="http://localhost:8080").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(visual_quota_max_deferrals=5).assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(visual_quota_base_deferral_seconds=29).assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(visual_quota_max_deferral_seconds=901).assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(
                visual_quota_base_deferral_seconds=721,
                visual_quota_max_deferral_seconds=720,
            ).assert_valid()
        for invalid_delay in (0, 74, 76, 901):
            with self.subTest(visual_successor_delay_seconds=invalid_delay):
                with self.assertRaises(ConfigurationError):
                    valid_config(
                        visual_successor_delay_seconds=invalid_delay
                    ).assert_valid()
        for invalid_jobs in (1, 3, 5):
            with self.subTest(admission_max_jobs=invalid_jobs):
                with self.assertRaises(ConfigurationError):
                    valid_config(admission_max_jobs=invalid_jobs).assert_valid()

    def test_shipped_environment_example_is_valid_with_reviewed_bounds(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "contest_config"
            / "all_things_agentic.env.example"
        )
        environment: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            environment[key] = value
        config = AllThingsConfig.from_environment(environment)
        self.assertEqual(config.issues(), ())
        self.assertEqual(config.admission_max_jobs, 4)
        self.assertEqual(config.visual_panels_per_dispatch, 2)
        self.assertEqual(config.visual_successor_delay_seconds, 75)
        self.assertEqual(config.visual_quota_max_deferrals, 4)
        self.assertEqual(config.visual_quota_base_deferral_seconds, 90)
        self.assertEqual(config.visual_quota_max_deferral_seconds, 720)
        self.assertEqual(config.worker_lease_seconds, 1_800)

    def test_checkpoint_capacity_wait_boundary_is_symmetric_on_write_and_load(
        self,
    ) -> None:
        source = "Plan every bounded sequence without losing its FIFO checkpoint."
        job_id = "00000000-0000-4000-8000-000000000123"
        attempt = 1
        dispatch_sequence = 1
        config = valid_config()
        brief = ProductionBrief.from_mapping(full_screenplay_mapping())
        package = build_storyboard_package(brief)
        artifact_store = StaticArtifactStore()
        request_sha256 = sha256_json({"message": source})
        target_digest = config.target_digest()
        window = {
            "schema": VISUAL_CAPACITY_WINDOW_SCHEMA,
            "reservation_token": "a" * 64,
            "not_before_epoch_seconds": 1_700_000_000,
            "request_limit": VISUAL_CAPACITY_REQUEST_LIMIT,
            "window_seconds": VISUAL_CAPACITY_WINDOW_SECONDS,
        }
        # Thirty-six panels need eighteen provider chunks.  The durable bound
        # deliberately permits C+4 inactive-window recoveries, exactly 22.
        continuation = _write_pipeline_checkpoint(
            artifact_store=artifact_store,
            job_id=job_id,
            attempt=attempt,
            dispatch_sequence=dispatch_sequence,
            phase="visual_storyboard",
            request_sha256=request_sha256,
            target_digest=target_digest,
            brief=brief,
            storyboard_package=package,
            provider_execution={"evidence_origin": "test_double"},
            panels=(),
            pitch_segments=(),
            visual_evidence_origin="not_attempted",
            previous_checkpoint_sha256=None,
            max_dispatches=82,
            quota_deferrals_used=4,
            capacity_waits_used=22,
            visual_capacity_window=window,
        )
        loaded = _load_pipeline_checkpoint(
            continuation,
            artifact_store=artifact_store,
            job_id=job_id,
            attempt=attempt,
            dispatch_sequence=dispatch_sequence,
            source_message=source,
            request_sha256=request_sha256,
            target_digest=target_digest,
        )
        self.assertEqual(loaded["capacity_waits_used"], 22)

        # Re-sign a structurally valid test artifact at C+5 to prove the
        # loader independently rejects the same boundary the writer enforces.
        excessive = deepcopy(continuation)
        descriptor = excessive["checkpoint"]
        object_name = str(descriptor["object_name"])
        body = json.loads(artifact_store.objects[object_name].decode("utf-8"))
        body["capacity_waits_used"] = 23
        body_without_digest = {
            key: value for key, value in body.items() if key != "manifest_sha256"
        }
        body["manifest_sha256"] = sha256_json(body_without_digest)
        encoded = canonical_json(body).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        artifact_store.objects[object_name] = encoded
        descriptor["sha256"] = digest
        descriptor["bytes"] = len(encoded)
        excessive["checkpoint_sha256"] = digest
        excessive["capacity_waits_used"] = 23
        continuation_without_digest = {
            key: value
            for key, value in excessive.items()
            if key != "manifest_sha256"
        }
        excessive["manifest_sha256"] = sha256_json(continuation_without_digest)
        with self.assertRaises(PipelineCheckpointError):
            _load_pipeline_checkpoint(
                excessive,
                artifact_store=artifact_store,
                job_id=job_id,
                attempt=attempt,
                dispatch_sequence=dispatch_sequence,
                source_message=source,
                request_sha256=request_sha256,
                target_digest=target_digest,
            )

    def test_visual_prompt_and_owner_gate_fail_closed_for_detail_hand_risk(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        risky = deepcopy(timeline)
        risky_card = risky["shots"][2]["storyboard_card"]
        risky_card["action"] = (
            "Detail insert: Mara holds a silver compass while pressing the jump button."
        )

        prompt = storyboard_panel_prompt(brief, risky["shots"][2])
        self.assertIn("The exact action requires hand contact", prompt)
        self.assertIn("only the hands needed for that action", prompt)
        self.assertIn("connected to the correct visible wrist, arm, and body", prompt)
        self.assertIn("Never duplicate a hand or add a detached foreground hand", prompt)
        self.assertIn("Lock every recurring named or described prop as one physical object", prompt)
        self.assertNotIn("For a detail card, keep hands", prompt)

        review = visual_owner_review_gate(risky)
        self.assertEqual(review["status"], "pending_owner_review")
        self.assertEqual(review["release_decision"], "hold")
        self.assertEqual(review["risk_flagged_shot_ids"], ["SC01-SH03"])
        self.assertEqual(
            review["risk_flags"],
            [
                {
                    "shot_id": "SC01-SH03",
                    "code": "detail_hand_or_foreground_anatomy_risk",
                }
            ],
        )
        body = {key: value for key, value in review.items() if key != "manifest_sha256"}
        self.assertEqual(review["manifest_sha256"], sha256_json(body))
        self.assertNotIn("detected_defect", review)

        ordinary = visual_owner_review_gate(timeline)
        self.assertEqual(ordinary["release_decision"], "hold")
        self.assertEqual(ordinary["risk_flagged_shot_ids"], [])

    def test_visual_prompts_enforce_materially_distinct_role_specific_compositions(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        prompts = {
            shot["role"]: storyboard_panel_prompt(brief, shot)
            for shot in timeline["shots"]
        }

        for prompt in prompts.values():
            self.assertIn("Draw only this exact moment", prompt)
            self.assertIn("Required cast", prompt)
            self.assertIn("Required location and state", prompt)
            self.assertIn("Required framing and camera", prompt)
            self.assertIn("Preserve its environment geometry, time of day, weather, lighting, damage", prompt)
            self.assertIn("Every visible person is one anatomically complete, connected body", prompt)
            self.assertIn("Never add or detach a head, torso, arm, hand, hip, leg, or foot", prompt)
            self.assertIn("Any visible screen contains only abstract, text-free interface graphics", prompt)
            self.assertIn("never a person, face, body part, portrait, reflection, or readable words", prompt)
            self.assertIn("Lock every recurring named or described prop as one physical object", prompt)
            self.assertNotIn("Project:", prompt)
            self.assertNotIn("Scene purpose:", prompt)
            self.assertNotIn("continuity ID", prompt)

        establishing = prompts["establishing"]
        self.assertIn("If a prior reference is supplied, preserve established character identity", establishing)
        self.assertIn("Change only the requested camera, crop, blocking, and action", establishing)
        self.assertIn("The exact visible cast is Mara, Jon, each exactly once", establishing)
        self.assertIn("Add no extras, crowds, silhouettes, reflections", establishing)
        self.assertIn("genuinely wide environmental master", establishing)
        self.assertIn("clearly establishes location geography", establishing)
        self.assertIn("do not turn this into a medium shot", establishing)

        primary = prompts["primary_coverage"]
        self.assertIn("action-focused medium, over-the-shoulder, waist-up, or full-body coverage", primary)
        self.assertIn("faces, eyelines, and interaction", primary)
        self.assertIn("without repeating the establishing composition", primary)

        bridge = prompts["continuity_bridge"]
        self.assertIn("tight reaction, prop insert, or environmental detail", bridge)
        self.assertIn("Do not repeat the establishing composition", bridge)
        self.assertIn("Prefer a prop or environmental detail", bridge)
        self.assertIn("If a face is essential, show one allowed character once", bridge)
        self.assertIn("For a detail card, keep hands and other body fragments out of frame", bridge)

        self.assertEqual(len(set(prompts.values())), 3)

    def test_visual_prompt_for_empty_cast_bans_anatomy_fragments_everywhere(self) -> None:
        value = brief_mapping()
        value["scenes"] = [
            {
                "number": 1,
                "purpose": "An unoccupied console confirms the evacuation route.",
                "setting": "empty orbital control room",
                "characters": [],
                "dialogue_required": False,
            }
        ]
        brief = ProductionBrief.from_mapping(value)
        prompt = storyboard_panel_prompt(brief, compile_storyboard_timeline(brief)["shots"][0])

        self.assertIn("Required cast: no people", prompt)
        self.assertIn("This card has an empty cast", prompt)
        self.assertIn("no people, faces, silhouettes, reflections, mannequins, hands, limbs", prompt)

    def test_visual_prompts_repeat_project_character_appearance_and_wardrobe_anchors(self) -> None:
        value = brief_mapping()
        value.update(
            {
                "title": "The Last Jump",
                "summary": "Mara and Dax repair a damaged orbital station and choose to escape.",
                "visual_direction": "Grounded orbital repair-bay line art with practical equipment.",
                "scenes": [
                    {
                        "number": 1,
                        "purpose": "Mara discovers an approaching signal while Dax checks the damage.",
                        "setting": "orbital repair bay",
                        "characters": ["Mara", "Dax"],
                        "dialogue_required": True,
                    },
                    {
                        "number": 2,
                        "purpose": "Dax and Mara choose to escape before the signal arrives.",
                        "setting": "damaged space-station control room",
                        "characters": ["Dax", "Mara"],
                        "dialogue_required": True,
                    },
                ],
            }
        )
        brief = ProductionBrief.from_mapping(value)
        timeline = compile_storyboard_timeline(brief)
        prompts = [storyboard_panel_prompt(brief, shot) for shot in timeline["shots"]]
        self.assertEqual(len(prompts), 6)
        for prompt in prompts:
            self.assertIn("Required cast:", prompt)
            self.assertIn("the same established person, apparent age and build", prompt)
            self.assertIn("face and head shape, hair style, length, and color", prompt)
            self.assertIn("complete wardrobe including garment cut, sleeves, patches, and harness", prompt)
            self.assertIn("establish these once if no reference exists", prompt)
            self.assertIn("Do not change apparent age, build, face, hair, or wardrobe", prompt)
            self.assertIn("Preserve the most recently approved state", prompt)
            self.assertNotIn("continuity ID", prompt)
            self.assertNotIn("APPEARANCE ANCHOR", prompt)

    def test_visual_prompt_limits_an_explicit_time_or_wardrobe_change_to_the_stated_change(self) -> None:
        value = brief_mapping()
        value["scenes"] = [
            {
                "number": 1,
                "purpose": "Five years later, Mara changes outfit into a formal coat.",
                "setting": "orbital repair shop",
                "characters": ["Mara"],
                "dialogue_required": False,
            }
        ]
        brief = ProductionBrief.from_mapping(value)
        prompt = storyboard_panel_prompt(brief, compile_storyboard_timeline(brief)["shots"][0])
        self.assertIn("the stated passage of time or aging cue for the scene cast", prompt)
        self.assertIn("the stated wardrobe or hair change for Mara", prompt)
        self.assertIn("Time passage alone does not change wardrobe or hair", prompt)
        self.assertIn("Use the approved result as the later reference", prompt)

    def test_generic_character_anchor_does_not_invent_traits_or_accept_negated_changes(self) -> None:
        value = brief_mapping()
        value.update(
            {
                "title": "Summer Door",
                "summary": "A child enters a sunlit garden.",
                "visual_direction": "Preserve every concrete character choice from the source.",
                "scenes": [
                    {
                        "number": 1,
                        "purpose": (
                            "Mara, a bald seven-year-old in a sleeveless summer dress, enters. "
                            "No time jump or costume change occurs."
                        ),
                        "setting": "garden",
                        "characters": ["Mara"],
                        "dialogue_required": False,
                    }
                ],
            }
        )
        brief = ProductionBrief.from_mapping(value)
        prompt = storyboard_panel_prompt(brief, compile_storyboard_timeline(brief)["shots"][0])
        self.assertIn("bald seven-year-old in a sleeveless summer dress", prompt)
        self.assertIn("the same established person, apparent age and build", prompt)
        self.assertNotIn("adult in their 30s", prompt)
        self.assertNotIn("low bun", prompt)
        self.assertNotIn("repair jumpsuit", prompt)
        self.assertNotIn("Only the stated passage of time", prompt)
        self.assertIn("Do not change apparent age, build, face, hair, or wardrobe", prompt)

    def test_character_identity_locks_are_casefolded_and_prompts_stay_concise(self) -> None:
        value = brief_mapping()
        value["scenes"] = [
            {
                "number": 1,
                "purpose": "Mara leads the group into the room.",
                "setting": "briefing room",
                "characters": ["Mara"],
                "dialogue_required": False,
            },
            {
                "number": 2,
                "purpose": "MARA continues the briefing without changing appearance.",
                "setting": "briefing room",
                "characters": ["MARA"],
                "dialogue_required": False,
            },
        ]
        brief = ProductionBrief.from_mapping(value)
        timeline = compile_storyboard_timeline(brief)
        first = storyboard_panel_prompt(brief, timeline["shots"][0])
        second = storyboard_panel_prompt(brief, timeline["shots"][3])
        for prompt in (first, second):
            self.assertIn("the same established person, apparent age and build", prompt)
            self.assertNotIn("continuity ID", prompt)
            self.assertNotRegex(prompt, r"[0-9A-F]{10}")

        crowded = brief_mapping()
        crowded["scenes"] = [
            {
                "number": 1,
                "purpose": "The full ensemble reviews the plan without changing appearance.",
                "setting": "briefing room",
                "characters": [
                    "Ari", "Bea", "Cal", "Dee", "Eli", "Fay",
                    "Gus", "Hope", "Ian", "Joy", "Kai", "Lux",
                ],
                "dialogue_required": False,
            }
        ]
        crowded_brief = ProductionBrief.from_mapping(crowded)
        crowded_timeline = compile_storyboard_timeline(crowded_brief)
        self.assertLessEqual(
            max(
                len(storyboard_panel_prompt(crowded_brief, shot))
                for shot in crowded_timeline["shots"]
            ),
            3_500,
        )

    def test_local_visual_build_keeps_full_cast_reference_across_scene_bridge(self) -> None:
        value = brief_mapping()
        value["scenes"] = [
            {
                "number": 1,
                "purpose": "Mara and Jon assess the damaged controls.",
                "setting": "orbital repair bay",
                "characters": ["Mara", "Jon"],
                "dialogue_required": True,
            },
            {
                "number": 2,
                "purpose": "Mara and Jon agree to leave together.",
                "setting": "station departure console",
                "characters": ["Mara", "Jon"],
                "dialogue_required": True,
            },
        ]
        brief = ProductionBrief.from_mapping(value)
        provider = UniqueVisualProvider()
        storyboard = build_visual_storyboard(
            brief,
            compile_storyboard_timeline(brief),
            provider=provider,
            config=valid_config(),
            job_id="local-reference-test",
            artifact_store=StaticArtifactStore(),
        )
        self.assertEqual(storyboard["status"], "complete")
        scene_one_primary = b"\xff\xd8" + (b"SC01-SH02" * 12) + b"\xff\xd9"
        scene_one_bridge = b"\xff\xd8" + (b"SC01-SH03" * 12) + b"\xff\xd9"
        self.assertEqual(provider.reference_images[3], scene_one_primary)
        self.assertNotEqual(provider.reference_images[3], scene_one_bridge)

    def test_visual_reference_selection_prefers_story_scene_then_setting_and_cast(self) -> None:
        brief = ProductionBrief.from_mapping(explicit_live_shape_mapping())
        timeline = compile_storyboard_timeline(brief)
        provider = UniqueVisualProvider()

        storyboard = build_visual_storyboard(
            brief,
            timeline,
            provider=provider,
            config=valid_config(),
            job_id="explicit-reference-selection",
            artifact_store=StaticArtifactStore(),
        )

        self.assertEqual(storyboard["status"], "complete")
        def image(shot_id: str) -> bytes:
            return b"\xff\xd8" + (shot_id.encode("ascii") * 12) + b"\xff\xd9"
        # No unrelated prior cast or location is leaked into a new empty-cast scene.
        self.assertIsNone(provider.reference_images[3])
        # An empty-cast detail keeps the empty establishing panel from its own story scene.
        self.assertEqual(provider.reference_images[4], image("SC02-SH01"))
        # Returning to the workshop normalizes BACK IN / MOMENTS LATER without using
        # the unrelated rooftop panel simply because it was generated last.
        self.assertEqual(provider.reference_images[6], image("SC01-SH01"))
        # A solo card prefers the same normalized-setting identity reference.
        self.assertEqual(provider.reference_images[7], image("SC01-SH02"))
        # The two-person reaction uses the prior full-cast image, not a solo card.
        self.assertEqual(provider.reference_images[8], image("SC02-SH03"))

    def test_visual_reference_allows_full_cast_anchor_for_solo_card(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        timeline["shots"][1]["characters"] = ["Mara"]
        timeline["shots"][1]["storyboard_card"]["characters"] = ["Mara"]
        provider = UniqueVisualProvider()

        storyboard = build_visual_storyboard(
            brief,
            timeline,
            provider=provider,
            config=valid_config(),
            job_id="solo-from-full-cast-reference",
            artifact_store=StaticArtifactStore(),
        )

        self.assertEqual(storyboard["status"], "complete")
        self.assertEqual(
            provider.reference_images[1],
            b"\xff\xd8" + (b"SC01-SH01" * 12) + b"\xff\xd9",
        )

    def test_visual_storyboard_is_bounded_ordered_and_cryptographically_validated(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        provider = StaticVisualProvider()
        first = build_visual_storyboard(
            brief,
            timeline,
            provider=provider,
            config=valid_config(),
            job_id="job-visual",
        )
        second = build_visual_storyboard(
            brief,
            timeline,
            provider=StaticVisualProvider(),
            config=valid_config(),
            job_id="job-visual",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["available_panel_count"], 3)
        self.assertTrue(provider.calls[1][3])
        self.assertEqual(
            validate_visual_storyboard(first, brief=brief, timeline=timeline),
            first,
        )

        tampered = deepcopy(first)
        tampered["panels"][0]["data_base64"] = tampered["panels"][1]["data_base64"][:-4] + "AAAA"
        from kira_studio.all_things_agentic import sha256_json

        body = {key: value for key, value in tampered.items() if key != "manifest_sha256"}
        tampered["manifest_sha256"] = sha256_json(body)
        with self.assertRaises(BriefValidationError):
            validate_visual_storyboard(tampered, brief=brief, timeline=timeline)

    def test_visual_storyboard_failures_are_truthful_nonfatal_and_large_plans_are_previewed(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        unavailable = build_visual_storyboard(
            brief,
            timeline,
            provider=StaticVisualProvider(error_code="quota_or_rate_limited"),
            config=valid_config(),
            job_id="job-quota",
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertTrue(
            all(
                panel["missing_reason"] == "quota_or_rate_limited"
                for panel in unavailable["panels"]
            )
        )

        held_brief = ProductionBrief.from_mapping(brief_mapping(ready=False))
        held_provider = StaticVisualProvider()
        held = build_visual_storyboard(
            held_brief,
            compile_storyboard_timeline(held_brief),
            provider=held_provider,
            config=valid_config(),
            job_id="job-held",
        )
        self.assertEqual(held["status"], "not_attempted")
        self.assertEqual(held_provider.calls, [])

        expanded = brief_mapping()
        expanded["scenes"] = [
            {
                **deepcopy(expanded["scenes"][0]),
                "number": number,
                "purpose": f"Decision beat {number} advances the friends' departure plan.",
            }
            for number in range(1, 4)
        ]
        expanded_brief = ProductionBrief.from_mapping(expanded)
        preview = build_visual_storyboard(
            expanded_brief,
            compile_storyboard_timeline(expanded_brief),
            provider=StaticVisualProvider(),
            config=valid_config(),
            job_id="job-preview",
        )
        self.assertEqual(preview["status"], "partial")
        self.assertEqual(preview["available_panel_count"], 6)
        self.assertEqual(preview["missing_panel_count"], 3)

    def test_production_brief_is_exact_and_holds_on_unanswered_questions(self) -> None:
        ready = ProductionBrief.from_mapping(brief_mapping())
        self.assertTrue(ready.ready_for_production)
        self.assertEqual(ready.scenes[0].number, 1)
        held = ProductionBrief.from_mapping(brief_mapping(ready=False))
        self.assertFalse(held.ready_for_production)
        with self.assertRaises(BriefValidationError):
            ProductionBrief.from_mapping({**brief_mapping(), "extra": "not allowed"})
        invalid = brief_mapping()
        invalid["clarifying_questions"] = ["Which ending?"]
        with self.assertRaises(BriefValidationError):
            ProductionBrief.from_mapping(invalid)

        over_limits: list[tuple[str, dict[str, object]]] = []
        too_many_tones = brief_mapping()
        too_many_tones["tone"] = [f"tone-{index}" for index in range(9)]
        over_limits.append(("tone", too_many_tones))
        too_many_deliverables = brief_mapping()
        too_many_deliverables["deliverables"] = [f"item-{index}" for index in range(17)]
        over_limits.append(("deliverables", too_many_deliverables))
        too_many_scenes = brief_mapping()
        too_many_scenes["scenes"] = [
            {
                "number": number,
                "purpose": f"Purpose {number}",
                "setting": "Set",
                "characters": [],
                "dialogue_required": False,
            }
            for number in range(1, 42)
        ]
        over_limits.append(("scenes", too_many_scenes))
        too_many_characters = brief_mapping()
        too_many_characters["scenes"][0]["characters"] = [  # type: ignore[index]
            f"Character {index}" for index in range(13)
        ]
        over_limits.append(("characters", too_many_characters))
        too_many_questions = brief_mapping(ready=False)
        too_many_questions["clarifying_questions"] = [
            f"Question {index}?" for index in range(7)
        ]
        over_limits.append(("clarifying_questions", too_many_questions))
        for label, candidate in over_limits:
            with self.subTest(limit=label), self.assertRaises(BriefValidationError):
                ProductionBrief.from_mapping(candidate)

    def test_storyboard_package_is_deterministic_self_auditing_and_plan_only(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        first = build_storyboard_package(brief)
        second = build_storyboard_package(brief)
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], STORYBOARD_PACKAGE_SCHEMA)
        self.assertEqual(first["media_status"], "unrendered_plan")
        self.assertTrue(first["plan_only"])
        self.assertTrue(first["audit"]["structurally_valid"])
        self.assertTrue(first["audit"]["ready_for_editorial"])
        self.assertEqual(first, validate_storyboard_package(first))
        shots = first["timeline"]["shots"]
        self.assertEqual(
            [shot["role"] for shot in shots],
            ["establishing", "primary_coverage", "continuity_bridge"],
        )
        self.assertEqual(shots[0]["planned_in_timecode"], "00:00:00:00")
        self.assertEqual(shots[-1]["planned_out_timecode"], "00:01:00:00")
        for shot in shots:
            card = shot["storyboard_card"]
            self.assertTrue(card["framing"])
            self.assertTrue(card["camera"])
            self.assertTrue(card["action"])
            self.assertTrue(card["dialogue_or_audio"])
            self.assertTrue(card["continuity_requirements"])
            self.assertIn("flag missing coverage", card["source_footage_guidance"])
            self.assertIn("unverified footage", card["bridge_shot_guidance"])

        tampered = deepcopy(first)
        tampered["timeline"]["shots"][1]["planned_in_frame"] += 1
        audit = audit_storyboard_package(tampered)
        self.assertFalse(audit["structurally_valid"])
        self.assertIn("contiguous_timeline", audit["issue_codes"])
        with self.assertRaises(BriefValidationError):
            validate_storyboard_package(tampered)

        tampered_cast = deepcopy(first)
        tampered_cast["timeline"]["shots"][1]["characters"] = ["Jon"]
        tampered_cast["audit"] = audit_storyboard_package(tampered_cast)
        tampered_cast["manifest_sha256"] = sha256_json(
            {
                key: item
                for key, item in tampered_cast.items()
                if key != "manifest_sha256"
            }
        )
        self.assertFalse(tampered_cast["audit"]["structurally_valid"])
        self.assertIn(
            "deterministic_timeline_contract",
            tampered_cast["audit"]["issue_codes"],
        )
        with self.assertRaises(BriefValidationError):
            validate_storyboard_package(tampered_cast)

    def test_explicit_three_scene_nine_shot_brief_is_not_double_expanded(self) -> None:
        raw_actions = [
            str(scene["purpose"]).split(":", 1)[1].strip()
            for scene in explicit_live_shape_mapping()["scenes"]  # type: ignore[index]
        ]
        brief = ProductionBrief.from_mapping(explicit_live_shape_mapping())
        self.assertEqual(len(brief.scenes), 3)
        self.assertEqual([scene.number for scene in brief.scenes], [1, 2, 3])
        self.assertIn("Shot 1.1:", brief.scenes[0].purpose)
        self.assertIn("Shot 1.3:", brief.scenes[0].purpose)

        timeline = compile_storyboard_timeline(brief)
        self.assertEqual(timeline["layout"], "explicit_ordered_shots")
        self.assertEqual(timeline["shot_count"], 9)
        self.assertEqual(
            [shot["shot_id"] for shot in timeline["shots"]],
            [f"SC{scene:02d}-SH{shot:02d}" for scene in range(1, 4) for shot in range(1, 4)],
        )
        self.assertEqual(
            [shot["scene_number"] for shot in timeline["shots"]],
            [1, 1, 1, 2, 2, 2, 3, 3, 3],
        )
        self.assertEqual(
            [shot["storyboard_card"]["action"] for shot in timeline["shots"]],
            raw_actions,
        )
        self.assertEqual(len(set(raw_actions)), 9)
        self.assertEqual(
            [shot["characters"] for shot in timeline["shots"]],
            [[], ["Lila"], ["Theo"], [], [], ["Lila", "Theo"], [], ["Lila"], ["Lila", "Theo"]],
        )
        self.assertEqual(
            [shot["role"] for shot in timeline["shots"]],
            [
                "establishing", "continuity_bridge", "primary_coverage",
                "establishing", "continuity_bridge", "primary_coverage",
                "continuity_bridge", "primary_coverage", "continuity_bridge",
            ],
        )

        receiver_prompt = storyboard_panel_prompt(brief, timeline["shots"][6])
        lila_prompt = storyboard_panel_prompt(brief, timeline["shots"][7])
        reaction_prompt = storyboard_panel_prompt(brief, timeline["shots"][8])
        self.assertIn("Required cast: no people", receiver_prompt)
        self.assertNotIn("Keep Lila as", receiver_prompt)
        self.assertIn("Required cast: Lila", lila_prompt)
        self.assertIn("Keep Lila as the same established person", lila_prompt)
        self.assertNotIn("Required cast: Lila, Theo", lila_prompt)
        self.assertIn("tight connected reaction composition", reaction_prompt)
        self.assertIn("Keep Lila as the same established person", reaction_prompt)
        self.assertIn("Keep Theo as the same established person", reaction_prompt)
        self.assertIn("Never merge identities or features between people", reaction_prompt)
        self.assertNotIn(
            "Keep Lila, Theo as the same established person",
            reaction_prompt,
        )
        self.assertNotIn("Shot 3.1:", reaction_prompt)
        self.assertNotIn("Project:", reaction_prompt)
        self.assertNotIn("Scene purpose:", reaction_prompt)
        self.assertNotIn("continuity ID", reaction_prompt)
        self.assertEqual(
            reaction_prompt.count(str(timeline["shots"][8]["storyboard_card"]["action"])),
            1,
        )

        package = build_storyboard_package(brief)
        self.assertEqual(len(package["production_brief"]["scenes"]), 3)
        self.assertEqual(package["timeline"]["shot_count"], 9)
        self.assertTrue(package["audit"]["structurally_valid"])
        self.assertTrue(package["audit"]["ready_for_editorial"])
        self.assertEqual(validate_storyboard_package(package), package)

    def test_explicit_pronoun_cast_inherits_scene_cast_but_prop_insert_stays_empty(self) -> None:
        value = explicit_live_shape_mapping()
        value["scenes"][5]["purpose"] = (  # type: ignore[index]
            "Shot 2.3: They struggle together to secure the cable."
        )
        brief = ProductionBrief.from_mapping(value)
        shots = compile_storyboard_timeline(brief)["shots"]
        self.assertEqual(shots[4]["characters"], [])
        self.assertEqual(shots[5]["characters"], ["Lila", "Theo"])

    def test_long_explicit_actions_roundtrip_through_package_audit(self) -> None:
        value = explicit_live_shape_mapping()
        for scene in value["scenes"]:  # type: ignore[union-attr]
            marker, action = str(scene["purpose"]).split(":", 1)
            expanded_action = (
                action.strip() + " " + ("background remains stable " * 24)
            )[:390]
            scene["purpose"] = f"{marker}: {expanded_action}"
        brief = ProductionBrief.from_mapping(value)
        self.assertTrue(all(len(scene.purpose) > 1_200 for scene in brief.scenes))
        package = build_storyboard_package(brief)
        self.assertTrue(package["audit"]["structurally_valid"])
        self.assertEqual(validate_storyboard_package(package), package)

    def test_storyboard_package_manifest_survives_browser_json_download(self) -> None:
        package = build_storyboard_package(ProductionBrief.from_mapping(brief_mapping()))
        downloaded = browser_json_number_roundtrip(package)
        self.assertEqual(package, downloaded)
        self.assertEqual(downloaded, validate_storyboard_package(downloaded))
        whole_second_shots = [
            shot
            for shot in package["timeline"]["shots"]
            if shot["planned_duration_frames"] % STORYBOARD_FRAME_RATE == 0
        ]
        self.assertTrue(whole_second_shots)
        self.assertTrue(
            all(isinstance(shot["planned_duration_seconds"], int) for shot in whole_second_shots)
        )

    def test_storyboard_timeline_handles_minimum_duration_and_maximum_scenes(self) -> None:
        value = brief_mapping()
        value["duration_seconds"] = 5
        value["scenes"] = [
            {
                "number": number,
                "purpose": f"Coverage purpose {number}",
                "setting": f"Set {number}",
                "characters": [],
                "dialogue_required": False,
            }
            for number in range(1, 41)
        ]
        timeline = compile_storyboard_timeline(ProductionBrief.from_mapping(value))
        self.assertEqual(timeline["shot_count"], 120)
        self.assertEqual(timeline["duration_frames"], 120)
        self.assertEqual(timeline["shots"][0]["planned_in_frame"], 0)
        self.assertEqual(timeline["shots"][-1]["planned_out_frame_exclusive"], 120)
        self.assertTrue(all(shot["planned_duration_frames"] == 1 for shot in timeline["shots"]))
        self.assertEqual(timeline["end_timecode"], "00:00:05:00")

    def test_clarification_package_is_structurally_valid_but_not_editorial_ready(self) -> None:
        package = build_storyboard_package(
            ProductionBrief.from_mapping(brief_mapping(ready=False))
        )
        self.assertEqual(package["status"], "clarification_required")
        self.assertTrue(package["audit"]["structurally_valid"])
        self.assertFalse(package["audit"]["ready_for_editorial"])
        self.assertFalse(package["audit"]["passed"])
        self.assertEqual(
            package["audit"]["hold_reasons"],
            ["Should the ending feel hopeful or uncertain?"],
        )

    def test_natural_chat_runs_as_durable_async_job_and_returns_structured_brief(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        provider = StaticProvider()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=dispatcher,
            provider=provider,
        )
        queued = service.submit("Make a one-minute dialogue scene in a repair shop.")
        self.assertEqual(queued["state"], "queued")
        self.assertFalse(queued["eta"]["available"])
        self.assertNotIn("message", queued)
        self.assertEqual(dispatcher.job_ids, [queued["job_id"]])

        completed = service.execute(str(queued["job_id"]), attempt=1)
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["brief"]["schema"], "video-studio.production-brief/v1")
        self.assertEqual(completed["brief"]["scenes"][0]["number"], 1)
        self.assertEqual(completed["storyboard_package"]["schema"], STORYBOARD_PACKAGE_SCHEMA)
        self.assertTrue(completed["storyboard_package"]["audit"]["passed"])
        self.assertEqual(completed["storyboard_package"]["timeline"]["shot_count"], 3)
        self.assertEqual(completed["execution"]["evidence_origin"], "test_double")
        self.assertEqual(
            completed["execution"]["pipeline"]["manifest_sha256"],
            completed["storyboard_package"]["manifest_sha256"],
        )
        durable = repository.get(str(queued["job_id"]))
        self.assertIsNone(durable["message"])
        self.assertEqual(durable["input_retention"], "discarded_after_provider_use")
        self.assertLessEqual(
            len(json.dumps(durable, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            MAX_DURABLE_JOB_BYTES,
        )

    def test_screenplay_message_bound_accepts_ordinary_full_scripts_and_fails_closed(self) -> None:
        repository = MemoryRepository()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(),
        )
        full_script = "A" * MAX_MESSAGE_CHARS
        queued = service.submit(full_script)
        self.assertEqual(len(repository.get(str(queued["job_id"]))["message"]), MAX_MESSAGE_CHARS)
        full_unicode_script = "😀" * MAX_MESSAGE_CHARS
        self.assertEqual(len(full_unicode_script.encode("utf-8")), MAX_MESSAGE_BYTES)
        unicode_queued = service.submit(full_unicode_script)
        self.assertEqual(
            len(repository.get(str(unicode_queued["job_id"]))["message"]),
            MAX_MESSAGE_CHARS,
        )
        with self.assertRaises(AllThingsError):
            service.submit("A" * (MAX_MESSAGE_CHARS + 1))
        with self.assertRaises(AllThingsError):
            service.submit("\ud800")

    def test_visual_sidecar_sheds_optional_panels_before_durable_job_limit(self) -> None:
        brief = ProductionBrief.from_mapping(brief_mapping())
        timeline = compile_storyboard_timeline(brief)
        provider = StaticVisualProvider()
        provider.image = b"\xff\xd8" + (b"x" * 44_996) + b"\xff\xd9"
        visual = build_visual_storyboard(
            brief,
            timeline,
            provider=provider,
            config=valid_config(),
            job_id="00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(visual["available_panel_count"], 3)
        base = {"bounded_non_visual_record": "r" * 750_000}
        fitted = fit_visual_storyboard_to_job_budget(
            visual,
            brief=brief,
            timeline=timeline,
            record_without_visual=base,
        )
        self.assertLess(fitted["available_panel_count"], 3)
        self.assertTrue(
            any(panel.get("missing_reason") == "inline_budget_exhausted" for panel in fitted["panels"])
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    {**base, "visual_storyboard": fitted},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            MAX_DURABLE_JOB_BYTES,
        )

    def test_cancelled_job_never_promotes_provider_result(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        service: AllThingsJobService

        def cancel_during_call(job_id: str) -> None:
            service.cancel(job_id)

        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=dispatcher,
            provider=StaticProvider(callback=cancel_during_call),
        )
        queued = service.submit("Make a short scene.")
        cancelled = service.execute(str(queued["job_id"]), attempt=1)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(cancelled["brief"])
        self.assertIsNone(cancelled["execution"])

    def test_failed_job_can_retry_but_retry_is_bounded(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=dispatcher,
            provider=StaticProvider(error=RuntimeError("provider unavailable")),
        )
        queued = service.submit("Make a short scene.")
        failed = service.execute(str(queued["job_id"]), attempt=1)
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["error"]["retryable"])
        retried = service.retry(str(queued["job_id"]))
        self.assertEqual(retried["state"], "queued")
        self.assertEqual(retried["attempt"], 2)
        service.provider = StaticProvider(error=RuntimeError("still unavailable"))
        service.execute(str(queued["job_id"]), attempt=2)
        service.retry(str(queued["job_id"]))
        service.execute(str(queued["job_id"]), attempt=3)
        with self.assertRaises(JobTransitionError):
            service.retry(str(queued["job_id"]))

    def test_eta_is_unknown_without_history_then_uses_real_duration_samples(self) -> None:
        self.assertFalse(eta_payload([], progress=25)["available"])
        estimate = eta_payload([10.0, 12.0, 14.0], progress=50)
        self.assertTrue(estimate["available"])
        self.assertEqual(estimate["sample_count"], 3)
        self.assertLessEqual(estimate["low_seconds"], estimate["high_seconds"])

    def test_google_genai_adapter_performs_model_lookup_and_structured_generation(self) -> None:
        client = FakeGenAIClient()
        provider = GoogleGenAIBriefProvider(valid_config(), client=client)
        result = provider.create_brief("Make a scene.", job_id="job-1")
        self.assertEqual(client.models.get_calls, ["gemini-3.5-flash"])
        self.assertEqual(client.models.generate_calls[0]["model"], "gemini-3.5-flash")
        config = client.models.generate_calls[0]["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(
            config["response_json_schema"], VERTEX_PRODUCTION_BRIEF_RESPONSE_SCHEMA
        )
        self.assertEqual(
            PRODUCTION_BRIEF_RESPONSE_SCHEMA["properties"]["tone"]["maxItems"], 8
        )
        self.assertNotIn(
            "maxItems", json.dumps(VERTEX_PRODUCTION_BRIEF_RESPONSE_SCHEMA)
        )
        self.assertIn(
            "minItems", json.dumps(VERTEX_PRODUCTION_BRIEF_RESPONSE_SCHEMA)
        )
        self.assertNotIn("response_schema", config)
        system_instruction = config["system_instruction"]
        self.assertIn("CLIENT-IMPORTED SCRIPT SOURCE", system_instruction)
        self.assertIn("chronological", system_instruction)
        self.assertIn("cover the included source from its beginning through its ending", system_instruction)
        self.assertRegex(system_instruction, r"If coverage says\s+full_text")
        self.assertIn("If coverage says excerpts", system_instruction)
        self.assertIn("never", system_instruction)
        self.assertIn("omitted sections", system_instruction)
        self.assertEqual(result.execution["evidence_origin"], "injected_test_client")
        provider.create_brief("Make another scene.", job_id="job-2")
        self.assertEqual(client.models.get_calls, ["gemini-3.5-flash"])

    def test_imported_script_instruction_preserves_exact_canon_identifiers(self) -> None:
        instruction = re.sub(r"\s+", " ", SYSTEM_INSTRUCTION.casefold())
        for scene_contract in (
            "three scenes with three shots per scene",
            "exactly three scene objects, not nine scene objects",
            "without double expansion",
        ):
            self.assertIn(scene_contract, instruction)
        for required_contract in (
            "exact named props",
            "alphanumeric and lettered designations",
            "quoted labels",
            "recurring canon terms",
            "carry them consistently",
            "never generalize, rename, renumber, or merge",
            "lettered or alphanumeric equipment designation",
            "unqualified generic item",
        ):
            with self.subTest(required_contract=required_contract):
                self.assertIn(required_contract, instruction)

    def test_full_screenplay_completes_36_private_panels_across_bounded_dispatches(
        self,
    ) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        provider = FullScreenplayProvider()
        visual_provider = UniqueVisualProvider()
        artifact_store = StaticArtifactStore()
        pitch_renderer = StaticNarratedPitchRenderer(artifact_store)
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=provider,
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=pitch_renderer,
        )

        queued = service.submit("Plan the complete twelve-sequence screenplay.")
        job_id = str(queued["job_id"])
        visual_deltas: list[int] = []
        current = queued
        while current["state"] == JobState.QUEUED.value:
            raw_before = repository.get(job_id)
            self.assertEqual(
                raw_before["message"],
                "Plan the complete twelve-sequence screenplay.",
            )
            before = len(visual_provider.calls)
            current = service.execute(
                job_id,
                attempt=1,
                dispatch_sequence=int(raw_before["dispatch_sequence"]),
            )
            visual_deltas.append(len(visual_provider.calls) - before)

        self.assertEqual(current["state"], JobState.SUCCEEDED.value, current)
        self.assertEqual(current["dispatch_sequence"], 55)
        self.assertEqual(current["max_dispatches"], 82)
        self.assertEqual(visual_deltas, [0] + ([2] * 18) + ([0] * 37))
        self.assertEqual(dispatcher.dispatch_sequences, list(range(56)))
        self.assertEqual(dispatcher.attempts, [1] * 56)
        scene_one_primary = b"\xff\xd8" + (b"SC01-SH02" * 12) + b"\xff\xd9"
        scene_one_bridge = b"\xff\xd8" + (b"SC01-SH03" * 12) + b"\xff\xd9"
        self.assertEqual(visual_provider.reference_images[3], scene_one_primary)
        self.assertNotEqual(visual_provider.reference_images[3], scene_one_bridge)
        # The outbox persists an absolute not-before time; reconciliation uses
        # that exact timestamp instead of recomputing a relative delay.
        self.assertEqual(dispatcher.delay_seconds, [0] * 56)
        visual_schedules = dispatcher.scheduled_epoch_seconds[2:19]
        self.assertEqual(len(visual_schedules), 17)
        self.assertTrue(all(isinstance(value, int) for value in visual_schedules))
        self.assertEqual(
            [
                int(visual_schedules[index])
                - int(dispatcher.scheduled_epoch_seconds[1])
                for index in range(len(visual_schedules))
            ],
            [75 * (index + 1) for index in range(17)],
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(pitch_renderer.calls, 1)
        self.assertEqual(pitch_renderer.segment_calls, list(range(36)))
        self.assertEqual(pitch_renderer.finalize_calls, 1)
        continuation = current["continuation"]
        self.assertEqual(continuation["schema"], PIPELINE_CONTINUATION_SCHEMA)
        self.assertEqual(continuation["status"], "complete")
        self.assertEqual(continuation["dispatches_used"], 56)
        self.assertEqual(continuation["required_pitch_count"], 36)

        visuals = current["visual_storyboard"]
        self.assertEqual(visuals["status"], "complete")
        self.assertEqual(visuals["representation"], "private_artifact_route")
        self.assertEqual(visuals["required_panel_count"], 36)
        self.assertEqual(visuals["available_panel_count"], 36)
        self.assertEqual(visuals["missing_panel_count"], 0)
        panels = visuals["panels"]
        self.assertEqual(len(panels), 36)
        self.assertEqual(len({panel["shot_id"] for panel in panels}), 36)
        self.assertEqual(len({panel["artifact_id"] for panel in panels}), 36)
        for panel in panels:
            self.assertEqual(panel["status"], "available")
            self.assertEqual(panel["mime_type"], "image/jpeg")
            self.assertIsNone(panel["data_base64"])
            self.assertIsNone(panel["missing_reason"])
            image = artifact_store.get_bytes(panel["object_name"])
            self.assertEqual(hashlib.sha256(image).hexdigest(), panel["content_sha256"])

        durable = repository.get(job_id)
        self.assertIsNone(durable["message"])
        self.assertEqual(durable["input_retention"], "discarded_after_provider_use")
        self.assertEqual(durable["attempt"], 1)
        self.assertEqual(durable["max_attempts"], 3)

    def test_quota_deferral_checkpoints_partial_chunk_and_resumes_same_attempt(
        self,
    ) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        provider = FullScreenplayProvider()
        visual_provider = QuotaPatternVisualProvider(quota_calls={2})
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(
                visual_panels_per_dispatch=2,
                visual_quota_max_deferrals=3,
                visual_quota_base_deferral_seconds=90,
                visual_quota_max_deferral_seconds=720,
            ),
            repository=repository,
            dispatcher=dispatcher,
            provider=provider,
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )

        queued = service.submit("Plan the screenplay without duplicating quota-held panels.")
        job_id = str(queued["job_id"])
        planned = service.execute(job_id, attempt=1, dispatch_sequence=0)
        self.assertEqual(planned["stage"], "waiting_for_visual_storyboard_continuation")
        deferred = service.execute(job_id, attempt=1, dispatch_sequence=1)

        self.assertEqual(deferred["state"], JobState.QUEUED.value)
        self.assertEqual(deferred["stage"], "waiting_for_visual_quota_deferral")
        self.assertEqual(deferred["attempt"], 1)
        self.assertEqual(deferred["dispatch_sequence"], 2)
        self.assertEqual(deferred["continuation"]["next_panel_index"], 1)
        self.assertEqual(deferred["continuation"]["quota_deferrals_used"], 1)
        self.assertIsNone(deferred["visual_storyboard"])
        self.assertIsNone(deferred["pitch_preview"])
        self.assertEqual(dispatcher.delay_seconds, [0, 0, 0])
        self.assertEqual(
            int(dispatcher.scheduled_epoch_seconds[2])
            - int(dispatcher.scheduled_epoch_seconds[1]),
            90,
        )
        self.assertTrue(dispatcher.job_ids[2].endswith(job_id))
        self.assertEqual(dispatcher.attempts[:3], [1, 1, 1])

        resumed = service.execute(job_id, attempt=1, dispatch_sequence=2)
        self.assertEqual(resumed["state"], JobState.QUEUED.value)
        self.assertEqual(resumed["continuation"]["next_panel_index"], 3)
        self.assertEqual(resumed["continuation"]["quota_deferrals_used"], 1)

        current = resumed
        while current["state"] == JobState.QUEUED.value:
            raw = repository.get(job_id)
            current = service.execute(
                job_id,
                attempt=1,
                dispatch_sequence=int(raw["dispatch_sequence"]),
            )

        self.assertEqual(current["state"], JobState.SUCCEEDED.value)
        self.assertEqual(current["attempt"], 1)
        self.assertEqual(current["dispatch_sequence"], 56)
        self.assertEqual(current["max_dispatches"], 80)
        self.assertEqual(current["continuation"]["quota_deferrals_used"], 1)
        self.assertEqual(
            current["execution"]["pipeline"]["visual_quota_deferrals_used"],
            1,
        )
        panels = current["visual_storyboard"]["panels"]
        self.assertEqual(len(panels), 36)
        self.assertEqual(len({panel["shot_id"] for panel in panels}), 36)
        self.assertEqual(len({panel["artifact_id"] for panel in panels}), 36)
        self.assertEqual(dispatcher.dispatch_sequences, list(range(57)))
        self.assertEqual(dispatcher.delay_seconds, [0] * 57)
        self.assertEqual(visual_provider.attempted_shot_ids[:3], [
            panels[0]["shot_id"],
            panels[1]["shot_id"],
            panels[1]["shot_id"],
        ])

    def test_two_jobs_share_fifo_without_spending_provider_deferral_budget(
        self,
    ) -> None:
        repository = MemoryRepository(enforce_visual_capacity=True)
        dispatcher = RecordingDispatcher()
        visual_provider = UniqueVisualProvider()
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(admission_cooldown_seconds=0),
            repository=repository,
            dispatcher=dispatcher,
            provider=FullScreenplayProvider(),
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )
        first = service.submit("Plan first concurrent screenplay.")
        second = service.submit("Plan second concurrent screenplay.")
        first_id = str(first["job_id"])
        second_id = str(second["job_id"])
        service.execute(first_id, attempt=1, dispatch_sequence=0)
        service.execute(second_id, attempt=1, dispatch_sequence=0)

        for _ in range(2):
            with self.assertRaises(JobVisualCapacityPendingError) as caught:
                service.execute(second_id, attempt=1, dispatch_sequence=1)
            self.assertGreaterEqual(caught.exception.retry_after_seconds, 1)
            waiting = repository.get(second_id)
            self.assertEqual(waiting["state"], JobState.QUEUED.value)
            self.assertEqual(waiting["dispatch_sequence"], 1)
            self.assertEqual(waiting["continuation"]["capacity_waits_used"], 0)
            self.assertEqual(waiting["continuation"]["quota_deferrals_used"], 0)
            self.assertEqual(visual_provider.calls, [])

        first_visual = service.execute(first_id, attempt=1, dispatch_sequence=1)
        self.assertEqual(first_visual["dispatch_sequence"], 2)
        self.assertEqual(len(visual_provider.calls), 2)

        # Advance the deterministic in-memory clock state without sleeping.
        # This models the reviewed 75-second rolling window expiring while
        # preserving the second job's FIFO position.
        repository.visual_reservations.clear()
        second_token = repository._continuation_token(
            repository.get(second_id)["continuation"]
        )
        second_window = next(
            item
            for item in repository.visual_windows
            if item["reservation_token"] == second_token
        )
        second_window["not_before_epoch_seconds"] = math.ceil(time.time())
        second_visual = service.execute(second_id, attempt=1, dispatch_sequence=1)

        self.assertEqual(second_visual["state"], JobState.QUEUED.value)
        self.assertEqual(second_visual["dispatch_sequence"], 2)
        self.assertEqual(second_visual["continuation"]["capacity_waits_used"], 0)
        self.assertEqual(second_visual["continuation"]["quota_deferrals_used"], 0)
        self.assertEqual(len(visual_provider.calls), 4)

    def test_quota_deferral_limit_fails_truthfully_without_partial_public_media(
        self,
    ) -> None:
        source = "PRIVATE SCREENPLAY quota-redaction phrase amber-nine"
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(
                visual_quota_max_deferrals=4,
                visual_quota_base_deferral_seconds=90,
                visual_quota_max_deferral_seconds=720,
            ),
            repository=repository,
            dispatcher=dispatcher,
            provider=FullScreenplayProvider(),
            visual_provider=QuotaPatternVisualProvider(quota_calls={-1}),
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )

        current = service.submit(source)
        job_id = str(current["job_id"])
        for sequence in range(6):
            current = service.execute(
                job_id,
                attempt=1,
                dispatch_sequence=sequence,
            )

        self.assertEqual(current["state"], JobState.FAILED.value)
        self.assertEqual(current["attempt"], 1)
        self.assertEqual(current["error"], {
            "code": "visual_storyboard_incomplete",
            "type": "VisualPanelGenerationError",
            "retryable": True,
            "diagnostic_code": "quota_or_rate_limited",
            "quota_deferrals_exhausted": True,
            "quota_deferrals_used": 4,
            "quota_deferral_limit": 4,
        })
        self.assertIsNone(current["brief"])
        self.assertIsNone(current["storyboard_package"])
        self.assertIsNone(current["visual_storyboard"])
        self.assertIsNone(current["pitch_preview"])
        self.assertIsNone(current["continuation"])
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1, 2, 3, 4, 5])
        self.assertEqual(dispatcher.delay_seconds, [0, 0, 0, 0, 0, 0])
        base_schedule = int(dispatcher.scheduled_epoch_seconds[1])
        self.assertEqual(
            [
                int(value) - base_schedule
                for value in dispatcher.scheduled_epoch_seconds[2:]
            ],
            [90, 180, 360, 720],
        )
        encoded = json.dumps(current)
        self.assertNotIn(source, encoded)
        self.assertNotIn("PRIVATE provider payload", encoded)
        self.assertEqual(repository.get(job_id)["message"], source)

    def test_maximum_40_scene_plan_receives_a_sufficient_finite_dispatch_budget(
        self,
    ) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=MaximumScreenplayProvider(),
            visual_provider=UniqueVisualProvider(),
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )

        queued = service.submit("Plan every scene in the maximum-size screenplay.")
        continued = service.execute(
            str(queued["job_id"]),
            attempt=1,
            dispatch_sequence=0,
        )

        self.assertEqual(continued["state"], JobState.QUEUED.value)
        self.assertEqual(continued["max_dispatches"], MAX_PIPELINE_DISPATCHES)
        self.assertEqual(MAX_PIPELINE_DISPATCHES, 250)
        self.assertEqual(continued["continuation"]["required_panel_count"], 120)
        self.assertEqual(continued["continuation"]["required_pitch_count"], 120)
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1])

    def test_worst_case_capacity_and_quota_schedule_finishes_at_sequence_249(
        self,
    ) -> None:
        repository = OneInactiveWindowPerVisualAttemptRepository()
        dispatcher = RecordingDispatcher()
        artifact_store = StaticArtifactStore()
        visual_provider = QuotaPatternVisualProvider(quota_calls={1, 2, 3, 4})
        pitch_renderer = StaticNarratedPitchRenderer(artifact_store)
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=dispatcher,
            provider=MaximumScreenplayProvider(),
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=pitch_renderer,
        )

        current = service.submit("Exercise every bounded continuation delivery.")
        job_id = str(current["job_id"])
        while current["state"] == JobState.QUEUED.value:
            durable = repository.get(job_id)
            current = service.execute(
                job_id,
                attempt=1,
                dispatch_sequence=int(durable["dispatch_sequence"]),
            )

        self.assertEqual(current["state"], JobState.SUCCEEDED.value, current)
        self.assertEqual(current["dispatch_sequence"], 249)
        self.assertEqual(current["max_dispatches"], MAX_PIPELINE_DISPATCHES)
        self.assertEqual(current["continuation"]["dispatches_used"], 250)
        self.assertEqual(current["continuation"]["max_dispatches"], 250)
        self.assertEqual(current["continuation"]["quota_deferrals_used"], 4)
        self.assertEqual(repository.capacity_wait_count, 64)
        self.assertEqual(repository.prepared_window_count, 128)
        self.assertEqual(len(visual_provider.attempted_shot_ids), 124)
        self.assertEqual(len(visual_provider.calls), 120)
        self.assertEqual(dispatcher.dispatch_sequences, list(range(250)))
        self.assertEqual(pitch_renderer.segment_calls, list(range(120)))
        self.assertEqual(pitch_renderer.finalize_calls, 1)

    def test_stale_dispatch_sequence_cannot_duplicate_checkpointed_panels(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        visual_provider = UniqueVisualProvider()
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=FullScreenplayProvider(),
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )
        queued = service.submit("Build every card exactly once.")
        job_id = str(queued["job_id"])
        service.execute(job_id, attempt=1, dispatch_sequence=0)
        continued = service.execute(job_id, attempt=1, dispatch_sequence=1)
        self.assertEqual(continued["dispatch_sequence"], 2)
        self.assertEqual(len(visual_provider.calls), 2)

        stale = service.execute(job_id, attempt=1, dispatch_sequence=1)
        self.assertEqual(stale["state"], JobState.QUEUED.value)
        self.assertEqual(stale["dispatch_sequence"], 2)
        self.assertEqual(len(visual_provider.calls), 2)
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1, 2])

    def test_tampered_checkpoint_fails_closed_and_retry_starts_new_attempt(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        visual_provider = UniqueVisualProvider()
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=FullScreenplayProvider(),
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )
        queued = service.submit("Reject any modified checkpoint.")
        job_id = str(queued["job_id"])
        service.execute(job_id, attempt=1, dispatch_sequence=0)
        service.execute(job_id, attempt=1, dispatch_sequence=1)
        checkpoint = repository.get(job_id)["continuation"]["checkpoint"]
        object_name = str(checkpoint["object_name"])
        artifact_store.objects[object_name] += b"tampered"

        failed = service.execute(job_id, attempt=1, dispatch_sequence=2)
        self.assertEqual(failed["state"], JobState.FAILED.value)
        self.assertEqual(failed["error"]["code"], "pipeline_checkpoint_invalid")
        self.assertTrue(failed["error"]["retryable"])
        self.assertEqual(len(visual_provider.calls), 2)
        self.assertIsNone(failed["continuation"])
        self.assertEqual(
            repository.get(job_id)["message"],
            "Reject any modified checkpoint.",
        )

        retried = service.retry(job_id)
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(retried["dispatch_sequence"], 0)
        self.assertIsNone(retried["continuation"])
        self.assertEqual(dispatcher.attempts, [1, 1, 1, 2])
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1, 2, 0])

    def test_visual_chunk_observes_cancellation_between_panels(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        visual_provider = UniqueVisualProvider(cancel_after_call=2)
        artifact_store = StaticArtifactStore()
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=FullScreenplayProvider(),
            visual_provider=visual_provider,
            artifact_store=artifact_store,
            narrated_pitch_renderer=StaticNarratedPitchRenderer(artifact_store),
        )
        visual_provider.cancel_callback = service.cancel
        queued = service.submit("Stop promptly when I cancel.")
        job_id = str(queued["job_id"])
        service.execute(job_id, attempt=1, dispatch_sequence=0)
        cancelled = service.execute(job_id, attempt=1, dispatch_sequence=1)

        self.assertEqual(cancelled["state"], JobState.CANCELLED.value)
        self.assertEqual(len(visual_provider.calls), 2)
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1])
        self.assertIsNone(cancelled["continuation"])
        self.assertIsNone(cancelled["visual_storyboard"])
        self.assertEqual(repository.get(job_id)["message"], "Stop promptly when I cancel.")

    def test_narrated_pitch_card_observes_cancellation_without_a_successor(self) -> None:
        class CancellingPitchRenderer(StaticNarratedPitchRenderer):
            cancel_callback: object | None = None

            def render_segment_chunk(self, **kwargs: object) -> list[dict[str, object]]:
                job_id = str(kwargs["job_id"])
                if callable(self.cancel_callback):
                    self.cancel_callback(job_id)
                ownership_check = kwargs.get("ownership_check")
                if callable(ownership_check):
                    ownership_check()
                raise NarratedPitchRenderError("work_stopped")

        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        artifact_store = StaticArtifactStore()
        pitch_renderer = CancellingPitchRenderer(artifact_store)
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=StaticProvider(),
            visual_provider=UniqueVisualProvider(),
            artifact_store=artifact_store,
            narrated_pitch_renderer=pitch_renderer,
        )
        pitch_renderer.cancel_callback = service.cancel
        queued = service.submit("Cancel while the first narrated card is running.")
        job_id = str(queued["job_id"])
        service.execute(job_id, attempt=1, dispatch_sequence=0)
        service.execute(job_id, attempt=1, dispatch_sequence=1)
        service.execute(job_id, attempt=1, dispatch_sequence=2)
        cancelled = service.execute(job_id, attempt=1, dispatch_sequence=3)

        self.assertEqual(cancelled["state"], JobState.CANCELLED.value)
        self.assertEqual(dispatcher.dispatch_sequences, [0, 1, 2, 3])
        self.assertIsNone(cancelled["continuation"])
        self.assertIsNone(cancelled["pitch_preview"])
        self.assertEqual(
            repository.get(job_id)["message"],
            "Cancel while the first narrated card is running.",
        )

    def test_stale_pitch_dispatch_cannot_duplicate_private_card_artifact(self) -> None:
        repository = MemoryRepository()
        dispatcher = RecordingDispatcher()
        artifact_store = StaticArtifactStore()
        pitch_renderer = StaticNarratedPitchRenderer(artifact_store)
        service = AllThingsJobService(
            config=valid_config(visual_panels_per_dispatch=2),
            repository=repository,
            dispatcher=dispatcher,
            provider=StaticProvider(),
            visual_provider=UniqueVisualProvider(),
            artifact_store=artifact_store,
            narrated_pitch_renderer=pitch_renderer,
        )
        queued = service.submit("Render each narrated card exactly once.")
        job_id = str(queued["job_id"])
        service.execute(job_id, attempt=1, dispatch_sequence=0)
        service.execute(job_id, attempt=1, dispatch_sequence=1)
        service.execute(job_id, attempt=1, dispatch_sequence=2)
        continued = service.execute(job_id, attempt=1, dispatch_sequence=3)

        self.assertEqual(continued["dispatch_sequence"], 4)
        self.assertEqual(pitch_renderer.segment_calls, [0])
        stale = service.execute(job_id, attempt=1, dispatch_sequence=3)
        self.assertEqual(stale["state"], JobState.QUEUED.value)
        self.assertEqual(stale["dispatch_sequence"], 4)
        self.assertEqual(pitch_renderer.segment_calls, [0])
        segment_names = [
            name for name in artifact_store.objects if name.endswith("pitch-card-0001.mp4")
        ]
        self.assertEqual(len(segment_names), 1)

    def test_cloud_tasks_dispatch_is_oidc_bound_to_private_worker(self) -> None:
        client = FakeTasksClient()
        dispatcher = CloudTasksDispatcher(valid_config(), client=client)
        receipt = dispatcher.enqueue(
            "00000000-0000-0000-0000-000000000001",
            attempt=1,
            dispatch_sequence=0,
        )
        self.assertEqual(receipt["provider"], "Google Cloud Tasks")
        task = client.calls[0]["task"]
        request = task["http_request"]
        self.assertEqual(task["dispatch_deadline"], {"seconds": 1_740})
        self.assertEqual(
            task["name"],
            "projects/video-studio-12345/locations/us-central1/queues/video-studio-production-briefs/tasks/00000000-0000-0000-0000-000000000001-a1-d000",
        )
        self.assertEqual(
            json.loads(request["body"].decode("utf-8")),
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "attempt": 1,
                "dispatch_sequence": 0,
            },
        )
        self.assertEqual(
            request["url"],
            "https://video-studio-worker-abc-uc.a.run.app/internal/v1/jobs/00000000-0000-0000-0000-000000000001:run",
        )
        self.assertEqual(
            request["oidc_token"]["service_account_email"],
            valid_config().tasks_service_account,
        )
        self.assertNotIn("authorization", {key.casefold() for key in request["headers"]})

    def test_quota_successor_is_named_and_has_an_explicit_bounded_schedule(self) -> None:
        client = FakeTasksClient()
        before = math.ceil(time.time())
        receipt = CloudTasksDispatcher(valid_config(), client=client).enqueue(
            "00000000-0000-0000-0000-000000000001",
            attempt=2,
            dispatch_sequence=7,
            delay_seconds=720,
        )
        after = math.ceil(time.time())
        task = client.calls[0]["task"]

        self.assertTrue(task["name"].endswith("-a2-d007"))
        self.assertEqual(receipt["task_name"], task["name"])
        self.assertEqual(receipt["attempt"], 2)
        self.assertEqual(receipt["dispatch_sequence"], 7)
        self.assertEqual(receipt["schedule_delay_seconds"], 720)
        scheduled = task["schedule_time"]["seconds"]
        self.assertEqual(receipt["scheduled_epoch_seconds"], scheduled)
        self.assertGreaterEqual(scheduled, before + 720)
        self.assertLessEqual(scheduled, after + 720)
        with self.assertRaises(JobTransitionError):
            CloudTasksDispatcher(valid_config(), client=FakeTasksClient()).enqueue(
                "00000000-0000-0000-0000-000000000001",
                attempt=2,
                dispatch_sequence=8,
                delay_seconds=7_201,
            )

    def test_cloud_tasks_dispatch_accepts_last_bounded_sequence_and_rejects_beyond_it(
        self,
    ) -> None:
        client = FakeTasksClient()
        dispatcher = CloudTasksDispatcher(valid_config(), client=client)
        last_sequence = MAX_PIPELINE_DISPATCHES - 1

        receipt = dispatcher.enqueue(
            "00000000-0000-0000-0000-000000000001",
            attempt=3,
            dispatch_sequence=last_sequence,
        )

        self.assertEqual(receipt["dispatch_sequence"], last_sequence)
        self.assertTrue(client.calls[0]["task"]["name"].endswith("-a3-d249"))
        for invalid in (True, MAX_PIPELINE_DISPATCHES):
            with self.subTest(dispatch_sequence=invalid):
                with self.assertRaises(JobTransitionError):
                    dispatcher.enqueue(
                        "00000000-0000-0000-0000-000000000001",
                        attempt=3,
                        dispatch_sequence=invalid,
                    )

    def test_named_task_reconciles_an_accepted_but_lost_create_response(self) -> None:
        class AlreadyExists(Exception):
            pass

        class AmbiguousClient(FakeTasksClient):
            def create_task(self, **kwargs: object) -> object:
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    raise TimeoutError("response lost")
                raise AlreadyExists("named task already exists")

        client = AmbiguousClient()
        receipt = CloudTasksDispatcher(valid_config(), client=client).enqueue(
            "00000000-0000-0000-0000-000000000001",
            attempt=2,
            dispatch_sequence=3,
        )
        self.assertTrue(receipt["deduplicated"])
        self.assertEqual(receipt["attempt"], 2)
        self.assertEqual(client.calls[0]["task"]["name"], client.calls[1]["task"]["name"])

    def test_active_lease_retries_and_expired_lease_is_reclaimed(self) -> None:
        repository = MemoryRepository()
        provider = StaticProvider()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(),
            provider=provider,
        )
        queued = service.submit("Make a crash-safe scene.")
        job_id = str(queued["job_id"])
        now = datetime.now(timezone.utc)
        repository.claim(
            job_id,
            {
                "state": JobState.RUNNING.value,
                "stage": "calling_gemini",
                "progress": 40,
                "started_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
            attempt=1,
            dispatch_sequence=0,
            lease_token="lost-worker",
            lease_expires_at=(now + timedelta(minutes=6)).isoformat(),
            now=now.isoformat(),
        )
        with self.assertRaises(JobLeaseBusyError):
            service.execute(job_id, attempt=1)
        self.assertEqual(provider.calls, [])

        repository.records[job_id]["lease_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        recovered = service.execute(job_id, attempt=1)
        self.assertEqual(recovered["state"], JobState.SUCCEEDED.value)
        self.assertEqual(recovered["worker_claim_count"], 2)
        self.assertEqual(len(provider.calls), 1)

    def test_transactional_finalize_makes_late_cancellation_win(self) -> None:
        class LateCancelRepository(MemoryRepository):
            service: AllThingsJobService
            fired = False

            def finalize(
                self,
                job_id: str,
                patch: dict[str, object],
                *,
                attempt: int,
                lease_token: str,
                cancelled_patch: dict[str, object],
            ) -> dict[str, object]:
                if patch.get("storyboard_package") and not self.fired:
                    self.fired = True
                    self.service.cancel(job_id)
                return super().finalize(
                    job_id,
                    patch,
                    attempt=attempt,
                    lease_token=lease_token,
                    cancelled_patch=cancelled_patch,
                )

        repository = LateCancelRepository()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(),
            provider=StaticProvider(),
        )
        repository.service = service
        queued = service.submit("Make a cancellable scene.")
        cancelled = service.execute(str(queued["job_id"]), attempt=1)
        self.assertEqual(cancelled["state"], JobState.CANCELLED.value)
        self.assertIsNone(cancelled["brief"])
        self.assertIsNone(cancelled["storyboard_package"])
        self.assertIsNone(cancelled["execution"])

    def test_ambiguous_dispatch_error_never_overwrites_completed_worker(self) -> None:
        class AcceptedThenLostDispatcher:
            service: AllThingsJobService

            def enqueue(
                self,
                job_id: str,
                *,
                attempt: int,
                dispatch_sequence: int,
            ) -> dict[str, object]:
                self.service.execute(
                    job_id,
                    attempt=attempt,
                    dispatch_sequence=dispatch_sequence,
                )
                raise TimeoutError("task accepted; response lost")

        repository = MemoryRepository()
        dispatcher = AcceptedThenLostDispatcher()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=dispatcher,
            provider=StaticProvider(),
        )
        dispatcher.service = service
        completed = service.submit("Make an idempotent scene.")
        self.assertEqual(completed["state"], JobState.SUCCEEDED.value)
        self.assertIsNotNone(completed["brief"])
        self.assertIsNotNone(completed["execution"])

    def test_shared_demo_admission_window_caps_new_jobs(self) -> None:
        repository = MemoryRepository()
        service = AllThingsJobService(
            config=valid_config(admission_cooldown_seconds=0),
            repository=repository,
            dispatcher=RecordingDispatcher(),
        )
        for number in range(1, 5):
            service.submit(f"Bounded job {number}.")
        with self.assertRaises(AdmissionLimitError) as caught:
            service.submit("Fifth bounded job.")
        self.assertGreaterEqual(caught.exception.retry_after_seconds, 1)

    def test_dispatch_failure_is_durable_and_truthful(self) -> None:
        repository = MemoryRepository()
        service = AllThingsJobService(
            config=valid_config(),
            repository=repository,
            dispatcher=RecordingDispatcher(fail=True),
        )
        failed = service.submit("Make a scene.")
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["error"]["code"], "cloud_tasks_dispatch_failed")
        self.assertNotIn("unavailable", json.dumps(failed))

    def test_documented_cloud_tasks_recovery_window_outlives_worker_lease(self) -> None:
        docs = (
            Path(__file__).resolve().parents[1] / "docs" / "ALL_THINGS_AGENTIC_SETUP.md"
        ).read_text(encoding="utf-8")
        worker = re.search(
            r"--timeout (?P<timeout>\d+).*KIRA_ALL_THINGS_WORKER_LEASE_SECONDS=(?P<lease>\d+)",
            docs,
        )
        queue = re.search(
            r"queues update .*--max-attempts (?P<attempts>\d+) "
            r"--max-retry-duration (?P<duration>\d+)s",
            docs,
        )
        self.assertIsNotNone(worker)
        self.assertIsNotNone(queue)
        assert worker is not None and queue is not None
        self.assertGreater(int(worker.group("lease")), int(worker.group("timeout")))
        self.assertGreater(int(queue.group("duration")), int(worker.group("lease")))
        self.assertGreaterEqual(int(queue.group("attempts")), MAX_PIPELINE_DISPATCHES)
        self.assertGreaterEqual(int(queue.group("duration")), 21_600)
        self.assertIn("three **application attempts**", docs)
        self.assertIn("maxAttempts=3` / `maxRetryDuration=300s` policy is unsafe", docs)

    def test_documented_request_budgets_match_the_bounded_worker_calls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        setup = (root / "docs" / "ALL_THINGS_AGENTIC_SETUP.md").read_text(
            encoding="utf-8"
        )
        architecture = (root / "docs" / "ARCHITECTURE.md").read_text(
            encoding="utf-8"
        )
        for docs in (setup, architecture):
            normalized = docs.casefold()
            self.assertIn("600 seconds", normalized)
            self.assertIn("sequence zero", normalized)
            self.assertIn("two-panel visual continuation", normalized)
            self.assertNotIn("1,510 seconds", docs)
            self.assertNotIn("1,210 seconds", docs)

    def test_documented_continuation_worker_can_enqueue_successor_tasks(self) -> None:
        docs = (
            Path(__file__).resolve().parents[1] / "docs" / "ALL_THINGS_AGENTIC_SETUP.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'gcloud tasks queues add-iam-policy-binding video-studio-production-briefs --project $env:AT_PROJECT --location $env:AT_REGION --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/cloudtasks.enqueuer',
            docs,
        )
        self.assertIn(
            'gcloud iam service-accounts add-iam-policy-binding $env:AT_TASKS_SA --project $env:AT_PROJECT --member "serviceAccount:$($env:AT_WORKER_SA)" --role roles/iam.serviceAccountUser',
            docs,
        )


if __name__ == "__main__":
    unittest.main()
