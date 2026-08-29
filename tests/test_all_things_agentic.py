from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unittest

from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    AllThingsConfig,
    AllThingsError,
    AllThingsJobService,
    BriefProviderResult,
    BriefValidationError,
    ConfigurationError,
    JobLeaseBusyError,
    JobNotFoundError,
    JobState,
    JobTransitionError,
    MAX_DURABLE_JOB_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_MESSAGE_CHARS,
    PRODUCTION_BRIEF_RESPONSE_SCHEMA,
    ProductionBrief,
    STORYBOARD_FRAME_RATE,
    STORYBOARD_PACKAGE_SCHEMA,
    VisualPanelGenerationError,
    VisualPanelProviderResult,
    audit_storyboard_package,
    build_storyboard_package,
    build_visual_storyboard,
    compile_storyboard_timeline,
    eta_payload,
    fit_visual_storyboard_to_job_budget,
    validate_storyboard_package,
    validate_visual_storyboard,
)
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
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.admission: dict[str, object] | None = None

    def admit_submission(
        self,
        *,
        now: str,
        cooldown_seconds: int,
        window_seconds: int,
        max_jobs: int,
    ) -> dict[str, object]:
        now_value = datetime.fromisoformat(now)
        current = deepcopy(self.admission) if self.admission else None
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
        if retry_after > 0:
            raise AdmissionLimitError(
                "shared demo job admission limit reached",
                retry_after_seconds=retry_after,
            )
        self.admission = {
            "window_started_at": window_started.isoformat(),
            "last_admitted_at": now_value.isoformat(),
            "count": count + 1,
        }
        return deepcopy(self.admission)

    def create(self, record: dict[str, object]) -> dict[str, object]:
        job_id = str(record["job_id"])
        self.records[job_id] = deepcopy(record)
        return deepcopy(record)

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
        lease_token: str,
        lease_expires_at: str,
        now: str,
    ) -> dict[str, object] | None:
        record = self.get(job_id)
        if int(record["attempt"]) != attempt:
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
        return self.update(job_id, update)

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
        return self.update(job_id, patch)

    def request_cancel(self, job_id: str, *, now: str) -> dict[str, object]:
        record = self.get(job_id)
        if record["state"] in {JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value}:
            return record
        if record["state"] == JobState.QUEUED.value:
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

    def prepare_retry(self, job_id: str, patch: dict[str, object]) -> dict[str, object]:
        record = self.get(job_id)
        if record["state"] not in {JobState.FAILED.value, JobState.CANCELLED.value}:
            raise JobTransitionError("invalid retry")
        if int(record["attempt"]) >= int(record["max_attempts"]):
            raise JobTransitionError("retry limit")
        return self.update(job_id, {**patch, "attempt": int(record["attempt"]) + 1})

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
        self.fail = fail

    def enqueue(self, job_id: str, *, attempt: int) -> dict[str, object]:
        self.job_ids.append(job_id)
        self.attempts.append(attempt)
        if self.fail:
            raise RuntimeError("unavailable")
        return {
            "provider": "Google Cloud Tasks",
            "task_name": f"projects/p/locations/l/queues/q/tasks/{job_id}-a{attempt}",
            "attempt": attempt,
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
    def test_configuration_requires_verified_contest_model_family_and_real_targets(self) -> None:
        self.assertEqual(valid_config().issues(), ())
        with self.assertRaises(ConfigurationError):
            valid_config(model="gemini-2.5-flash").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(model="future-model").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(image_model="imagen-4").assert_valid()
        with self.assertRaises(ConfigurationError):
            valid_config(project="", worker_url="http://localhost:8080").assert_valid()

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

    def test_cloud_tasks_dispatch_is_oidc_bound_to_private_worker(self) -> None:
        client = FakeTasksClient()
        dispatcher = CloudTasksDispatcher(valid_config(), client=client)
        receipt = dispatcher.enqueue(
            "00000000-0000-0000-0000-000000000001", attempt=1
        )
        self.assertEqual(receipt["provider"], "Google Cloud Tasks")
        task = client.calls[0]["task"]
        request = task["http_request"]
        self.assertEqual(
            task["name"],
            "projects/video-studio-12345/locations/us-central1/queues/video-studio-production-briefs/tasks/00000000-0000-0000-0000-000000000001-a1",
        )
        self.assertEqual(
            json.loads(request["body"].decode("utf-8")),
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "attempt": 1,
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
            "00000000-0000-0000-0000-000000000001", attempt=2
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

            def enqueue(self, job_id: str, *, attempt: int) -> dict[str, object]:
                self.service.execute(job_id, attempt=attempt)
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
            config=valid_config(admission_max_jobs=2, admission_cooldown_seconds=0),
            repository=repository,
            dispatcher=RecordingDispatcher(),
        )
        service.submit("First bounded job.")
        service.submit("Second bounded job.")
        with self.assertRaises(AdmissionLimitError) as caught:
            service.submit("Third bounded job.")
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
        self.assertGreater(int(queue.group("attempts")), 3)
        self.assertIn("three **application attempts**", docs)
        self.assertIn("maxAttempts=3` / `maxRetryDuration=300s` policy is unsafe", docs)


if __name__ == "__main__":
    unittest.main()
