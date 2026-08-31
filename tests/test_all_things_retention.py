from __future__ import annotations

from datetime import datetime, timezone
import unittest

from kira_studio.all_things_agentic import (
    AllThingsConfig,
    AllThingsJobService,
    DEFAULT_JOB_RETENTION_SECONDS,
    MAX_JOB_RETENTION_SECONDS,
    MIN_JOB_RETENTION_SECONDS,
    _failed_input_retention_patch,
)
from kira_studio.all_things_google import FirestoreJobRepository


def config(**overrides: object) -> AllThingsConfig:
    values: dict[str, object] = {
        "project": "video-studio-12345",
        "worker_url": "https://video-studio-worker-abc-uc.a.run.app",
        "tasks_service_account": (
            "video-studio-tasks@video-studio-12345.iam.gserviceaccount.com"
        ),
        "admission_cooldown_seconds": 0,
    }
    values.update(overrides)
    return AllThingsConfig(**values)  # type: ignore[arg-type]


class _Snapshot:
    def __init__(self, value: dict[str, object] | None) -> None:
        self.exists = value is not None
        self._value = value

    def to_dict(self) -> dict[str, object] | None:
        return dict(self._value) if self._value is not None else None


class _Document:
    def __init__(self) -> None:
        self.value: dict[str, object] | None = None

    def create(self, value: dict[str, object]) -> None:
        self.value = dict(value)

    def update(self, value: dict[str, object]) -> None:
        assert self.value is not None
        self.value.update(value)

    def get(self, **_kwargs: object) -> _Snapshot:
        return _Snapshot(self.value)


class _Collection:
    def __init__(self) -> None:
        self.documents: dict[str, _Document] = {}

    def document(self, job_id: str) -> _Document:
        return self.documents.setdefault(job_id, _Document())


class _FirestoreClient:
    def __init__(self) -> None:
        self.jobs = _Collection()

    def collection(self, _name: str) -> _Collection:
        return self.jobs


class _MemoryRepository:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def admit_submission(
        self, record: dict[str, object], **_kwargs: object
    ) -> dict[str, object]:
        self.create(record)
        return {}

    def recent_success_durations(self, **_kwargs: object) -> tuple[()]:
        return ()

    def create(self, record: dict[str, object]) -> dict[str, object]:
        saved = dict(record)
        self.records[str(saved["job_id"])] = saved
        return saved

    def update(self, job_id: str, patch: dict[str, object]) -> dict[str, object]:
        self.records[job_id].update(patch)
        return dict(self.records[job_id])


class _Dispatcher:
    def enqueue(
        self, job_id: str, *, attempt: int, dispatch_sequence: int
    ) -> dict[str, object]:
        return {
            "job_id": job_id,
            "attempt": attempt,
            "dispatch_sequence": dispatch_sequence,
        }


class RetentionContractTests(unittest.TestCase):
    def test_config_default_and_bounds(self) -> None:
        self.assertEqual(config().job_retention_seconds, DEFAULT_JOB_RETENTION_SECONDS)
        self.assertEqual(config(job_retention_seconds=MIN_JOB_RETENTION_SECONDS).issues(), ())
        self.assertEqual(config(job_retention_seconds=MAX_JOB_RETENTION_SECONDS).issues(), ())
        self.assertTrue(config(job_retention_seconds=MIN_JOB_RETENTION_SECONDS - 1).issues())
        self.assertTrue(config(job_retention_seconds=MAX_JOB_RETENTION_SECONDS + 1).issues())

    def test_environment_parses_bounded_retention(self) -> None:
        parsed = AllThingsConfig.from_environment(
            {
                "GOOGLE_CLOUD_PROJECT": "video-studio-12345",
                "KIRA_ALL_THINGS_JOB_RETENTION_SECONDS": "7200",
            }
        )
        self.assertEqual(parsed.job_retention_seconds, 7200)

    def test_submit_exposes_iso_expiry_and_bounded_input_contract(self) -> None:
        repository = _MemoryRepository()
        before = datetime.now(timezone.utc)
        queued = AllThingsJobService(
            config=config(job_retention_seconds=7200),
            repository=repository,  # type: ignore[arg-type]
            dispatcher=_Dispatcher(),
        ).submit("A private screenplay scene for a judge program review.")
        expiry = datetime.fromisoformat(str(queued["record_expires_at"]))
        self.assertGreaterEqual((expiry - before).total_seconds(), 7199)
        self.assertLessEqual((expiry - before).total_seconds(), 7201)
        self.assertEqual(
            queued["input_retention"], "bounded_retry_until_record_expiry"
        )

    def test_firestore_writes_native_ttl_but_reads_iso(self) -> None:
        client = _FirestoreClient()
        repository = FirestoreJobRepository(config(), client=client)  # type: ignore[arg-type]
        expires_at = "2026-08-30T12:34:56+00:00"
        repository.create(
            {
                "job_id": "job-retention",
                "record_expires_at": expires_at,
            }
        )
        stored = client.jobs.documents["job-retention"].value
        assert stored is not None
        self.assertIsInstance(stored["record_expires_at"], datetime)
        self.assertEqual(
            repository.get("job-retention")["record_expires_at"], expires_at
        )

    def test_final_attempt_discards_source_while_retryable_failure_keeps_it(self) -> None:
        self.assertEqual(
            _failed_input_retention_patch({"attempt": 2, "max_attempts": 3}),
            {"input_retention": "bounded_retry_until_record_expiry"},
        )
        self.assertEqual(
            _failed_input_retention_patch({"attempt": 3, "max_attempts": 3}),
            {"message": None, "input_retention": "discarded_at_retry_limit"},
        )


if __name__ == "__main__":
    unittest.main()
