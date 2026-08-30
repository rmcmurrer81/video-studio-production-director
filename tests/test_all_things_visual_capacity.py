from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import unittest

from kira_studio.all_things_agentic import (
    AdmissionLimitError,
    AllThingsConfig,
    JobState,
    VISUAL_CAPACITY_REQUEST_LIMIT,
    VISUAL_CAPACITY_WINDOW_SECONDS,
    _visual_capacity_reservation_token,
)
from kira_studio.all_things_google import (
    FirestoreJobRepository,
    _load_visual_capacity_state,
    _remove_visual_capacity_tokens,
    _visual_capacity_update,
    _visual_token_sha256,
)

JOB_ID = "00000000-0000-4000-8000-000000000124"


def config() -> AllThingsConfig:
    return AllThingsConfig(
        project="video-studio-12345",
        worker_url="https://video-studio-worker-abc-uc.a.run.app",
        tasks_service_account=(
            "video-studio-tasks@video-studio-12345.iam.gserviceaccount.com"
        ),
        admission_cooldown_seconds=0,
    )


class _Snapshot:
    def __init__(self, value: dict[str, object] | None) -> None:
        self.exists = value is not None
        self._value = deepcopy(value)

    def to_dict(self) -> dict[str, object] | None:
        return deepcopy(self._value)


class _Document:
    def __init__(self, client: "_FirestoreClient", key: str) -> None:
        self.client = client
        self.key = key

    def create(self, value: dict[str, object]) -> None:
        if self.key in self.client.store:
            raise RuntimeError("already exists")
        self.client.store[self.key] = deepcopy(value)

    def update(self, value: dict[str, object]) -> None:
        current = deepcopy(self.client.store[self.key])
        current.update(deepcopy(value))
        self.client.store[self.key] = current

    def get(self, *, transaction: "_Transaction | None" = None) -> _Snapshot:
        if transaction is not None:
            return transaction.snapshot(self)
        return _Snapshot(self.client.store.get(self.key))


class _Collection:
    def __init__(self, client: "_FirestoreClient") -> None:
        self.client = client

    def document(self, key: str) -> _Document:
        return _Document(self.client, key)

    def where(self, field: str, operator: str, value: object) -> "_Query":
        if self.client.query_failure:
            raise RuntimeError("injected query failure")
        return _Query(self.client, field=field, operator=operator, value=value)


class _Query:
    def __init__(
        self,
        client: "_FirestoreClient",
        *,
        field: str,
        operator: str,
        value: object,
        limit_count: int | None = None,
    ) -> None:
        self.client = client
        self.field = field
        self.operator = operator
        self.value = value
        self.limit_count = limit_count

    def limit(self, count: int) -> "_Query":
        return _Query(
            self.client,
            field=self.field,
            operator=self.operator,
            value=self.value,
            limit_count=count,
        )

    def stream(self, *, transaction: "_Transaction | None" = None) -> object:
        matched: list[_Snapshot] = []
        for key in sorted(self.client.store):
            if key.startswith("_all_things_agentic_"):
                continue
            snapshot = (
                transaction.snapshot(_Document(self.client, key))
                if transaction is not None
                else _Snapshot(self.client.store.get(key))
            )
            raw = snapshot.to_dict()
            if not isinstance(raw, dict):
                continue
            candidate = raw.get(self.field)
            matches = (
                self.operator == "in"
                and isinstance(self.value, list)
                and candidate in self.value
            ) or (self.operator == "==" and candidate == self.value)
            if matches:
                matched.append(snapshot)
                if self.limit_count is not None and len(matched) >= self.limit_count:
                    break
        return iter(matched)


class _Transaction:
    def __init__(self, client: "_FirestoreClient") -> None:
        self.client = client
        self.staged: dict[str, dict[str, object]] = {}
        self.write_count = 0
        self.has_written = False

    def reset(self) -> None:
        self.staged = {}
        self.write_count = 0
        self.has_written = False

    def snapshot(self, ref: _Document) -> _Snapshot:
        if self.has_written:
            raise RuntimeError("Firestore transaction read after write")
        value = self.staged.get(ref.key, self.client.store.get(ref.key))
        return _Snapshot(value)

    def _before_write(self) -> None:
        self.write_count += 1
        self.has_written = True
        if self.client.fail_on_write == self.write_count:
            raise RuntimeError("injected transaction write failure")

    def create(self, ref: _Document, value: dict[str, object]) -> None:
        self._before_write()
        if ref.key in self.client.store or ref.key in self.staged:
            raise RuntimeError("already exists")
        self.staged[ref.key] = deepcopy(value)

    def set(self, ref: _Document, value: dict[str, object]) -> None:
        self._before_write()
        self.staged[ref.key] = deepcopy(value)

    def update(self, ref: _Document, value: dict[str, object]) -> None:
        self._before_write()
        current = deepcopy(self.staged.get(ref.key, self.client.store[ref.key]))
        current.update(deepcopy(value))
        self.staged[ref.key] = current

    def commit(self) -> None:
        self.client.store.update(deepcopy(self.staged))


class _TransactionalModule:
    def __init__(self, *, retry_callback_once: bool = False) -> None:
        self.retry_callback_once = retry_callback_once
        self.callback_calls = 0

    def transactional(self, callback: object) -> object:
        def run(transaction: _Transaction) -> object:
            assert callable(callback)
            if self.retry_callback_once:
                self.callback_calls += 1
                callback(transaction)
                transaction.reset()
            self.callback_calls += 1
            result = callback(transaction)
            transaction.commit()
            return result

        return run


class _FirestoreClient:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, object]] = {}
        self.fail_on_write: int | None = None
        self.query_failure = False
        self.jobs = _Collection(self)

    def collection(self, _name: str) -> _Collection:
        return self.jobs

    def transaction(self) -> _Transaction:
        return _Transaction(self)


def _window(token: str, *, not_before: int) -> dict[str, object]:
    return {
        "reservation_token": token,
        "not_before_epoch_seconds": not_before,
        "request_limit": VISUAL_CAPACITY_REQUEST_LIMIT,
        "window_seconds": VISUAL_CAPACITY_WINDOW_SECONDS,
    }


def _continuation(token: str, *, not_before: int) -> dict[str, object]:
    return {"visual_capacity_window": _window(token, not_before=not_before)}


def _gate(tokens: list[str], *, now: datetime) -> dict[str, object]:
    created = math.ceil(now.timestamp()) - 1
    queue = [
        {
            "token_sha256": _visual_token_sha256(token),
            "not_before_epoch_seconds": created
            + (index * VISUAL_CAPACITY_WINDOW_SECONDS),
            "requests_used": 0,
            "created_epoch_seconds": created,
        }
        for index, token in enumerate(tokens)
    ]
    return _visual_capacity_update(
        queue=queue,
        reservations=[],
        now=now,
        window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
        max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
    )


def _legacy_admission(
    *,
    window_started: datetime,
    window_seconds: int = 3_600,
    max_jobs: int = 24,
    cooldown_seconds: int = 3,
) -> dict[str, object]:
    return {
        "schema": "video-studio.all-things-agentic-admission/v1",
        "window_started_at": window_started.isoformat(),
        "last_admitted_at": window_started.isoformat(),
        "count": max_jobs,
        "max_jobs": max_jobs,
        "window_seconds": window_seconds,
        "cooldown_seconds": cooldown_seconds,
    }


class FirestoreVisualCapacityTests(unittest.TestCase):
    def _repository(
        self, *, retry_callback_once: bool = False
    ) -> tuple[FirestoreJobRepository, _FirestoreClient, _TransactionalModule]:
        client = _FirestoreClient()
        repository = FirestoreJobRepository(config(), client=client)  # type: ignore[arg-type]
        module = _TransactionalModule(retry_callback_once=retry_callback_once)
        repository._firestore = module
        return repository, client, module

    @staticmethod
    def _running_job(
        old_token: str,
        *,
        not_before: int,
        lease_token: str = "lease-a",
        state: str = JobState.RUNNING.value,
        cancel_requested: bool = False,
    ) -> dict[str, object]:
        return {
            "job_id": JOB_ID,
            "attempt": 1,
            "dispatch_sequence": 1,
            "state": state,
            "cancel_requested": cancel_requested,
            "lease_token": lease_token,
            "lease_expires_at": "2026-08-30T20:00:00+00:00",
            "continuation": _continuation(old_token, not_before=not_before),
        }

    @staticmethod
    def _admission_job(
        job_id: str,
        *,
        now: datetime,
        state: str = JobState.QUEUED.value,
        attempt: int = 1,
    ) -> dict[str, object]:
        return {
            "job_id": job_id,
            "attempt": attempt,
            "max_attempts": 3,
            "dispatch_sequence": 0,
            "state": state,
            "cancel_requested": False,
            "lease_token": None,
            "lease_expires_at": None,
            "record_expires_at": (now + timedelta(days=1)).isoformat(),
            "continuation": None,
        }

    @staticmethod
    def _active_job_ids(client: _FirestoreClient) -> list[str]:
        ledger = client.store.get("_all_things_agentic_admission", {})
        return [
            str(item["job_id"])
            for item in ledger.get("active_slots", [])
            if isinstance(item, dict)
        ]

    def test_atomic_admission_rollback_and_callback_retry(self) -> None:
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        record = self._admission_job(JOB_ID, now=now)
        repository, client, _module = self._repository()
        client.fail_on_write = 2
        with self.assertRaisesRegex(RuntimeError, "injected transaction"):
            repository.admit_submission(
                record,
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertNotIn(JOB_ID, client.store)
        self.assertNotIn("_all_things_agentic_admission", client.store)

        repository, client, module = self._repository(retry_callback_once=True)
        repository.admit_submission(
            record,
            now=now.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertEqual(module.callback_calls, 2)
        self.assertIn(JOB_ID, client.store)
        self.assertEqual(self._active_job_ids(client), [JOB_ID])

    def test_active_job_limit_survives_hour_boundary_and_releases(self) -> None:
        repository, client, _module = self._repository()
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        job_ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 6)]
        for job_id in job_ids[:4]:
            repository.admit_submission(
                self._admission_job(job_id, now=now),
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        later = now + timedelta(seconds=3_601)
        with self.assertRaises(AdmissionLimitError):
            repository.admit_submission(
                self._admission_job(job_ids[4], now=later),
                now=later.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertNotIn(job_ids[4], client.store)
        self.assertEqual(len(self._active_job_ids(client)), 4)

        repository.mark_dispatch_failed(
            job_ids[0],
            {
                "state": JobState.FAILED.value,
                "updated_at": later.isoformat(),
            },
            attempt=1,
        )
        repository.admit_submission(
            self._admission_job(job_ids[4], now=later),
            now=later.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertEqual(len(self._active_job_ids(client)), 4)
        self.assertNotIn(job_ids[0], self._active_job_ids(client))
        self.assertIn(job_ids[4], self._active_job_ids(client))

    def test_queued_cancel_releases_slot_but_running_cancel_request_retains_it(self) -> None:
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        for running in (False, True):
            with self.subTest(running=running):
                repository, client, _module = self._repository()
                repository.admit_submission(
                    self._admission_job(JOB_ID, now=now),
                    now=now.isoformat(),
                    cooldown_seconds=0,
                    window_seconds=3_600,
                    max_jobs=4,
                )
                if running:
                    client.store[JOB_ID].update(
                        {
                            "state": JobState.RUNNING.value,
                            "lease_token": "lease-a",
                        }
                    )
                repository.request_cancel(JOB_ID, now=now.isoformat())
                self.assertEqual(
                    JOB_ID in self._active_job_ids(client),
                    running,
                )

    def test_retry_reacquires_slot_and_obeys_active_limit(self) -> None:
        repository, client, _module = self._repository()
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        active_ids = [f"00000000-0000-4000-8001-{index:012d}" for index in range(4)]
        for job_id in active_ids:
            repository.admit_submission(
                self._admission_job(job_id, now=now),
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        failed_id = "00000000-0000-4000-8002-000000000001"
        failed = self._admission_job(
            failed_id, now=now, state=JobState.FAILED.value
        )
        client.store[failed_id] = deepcopy(failed)
        retry_patch = {
            "state": JobState.QUEUED.value,
            "record_expires_at": (now + timedelta(days=2)).isoformat(),
        }
        with self.assertRaises(AdmissionLimitError):
            repository.prepare_retry(
                failed_id,
                retry_patch,
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertEqual(client.store[failed_id]["attempt"], 1)

        repository.mark_dispatch_failed(
            active_ids[0],
            {"state": JobState.FAILED.value, "updated_at": now.isoformat()},
            attempt=1,
        )
        retried = repository.prepare_retry(
            failed_id,
            retry_patch,
            now=now.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertEqual(retried["attempt"], 2)
        self.assertIn(failed_id, self._active_job_ids(client))

    def test_expired_job_cannot_claim_and_slot_keeps_lease_margin(self) -> None:
        repository, client, _module = self._repository()
        admitted = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        expiry = admitted + timedelta(hours=1)
        record = self._admission_job(JOB_ID, now=admitted)
        record["record_expires_at"] = expiry.isoformat()
        repository.admit_submission(
            record,
            now=admitted.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        claimed = repository.claim(
            JOB_ID,
            {"state": JobState.RUNNING.value},
            attempt=1,
            dispatch_sequence=0,
            lease_token="lease-a",
            lease_expires_at=(expiry + timedelta(seconds=1_800)).isoformat(),
            now=expiry.isoformat(),
        )
        self.assertIsNone(claimed)

        fifth = self._admission_job(
            "00000000-0000-4000-8003-000000000001",
            now=expiry + timedelta(seconds=1),
        )
        # Fill the other three active slots after the original admission.
        for index in range(3):
            job_id = f"00000000-0000-4000-8004-{index:012d}"
            repository.admit_submission(
                self._admission_job(job_id, now=admitted),
                now=admitted.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        with self.assertRaises(AdmissionLimitError):
            repository.admit_submission(
                fifth,
                now=(expiry + timedelta(seconds=1)).isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        after_margin = expiry + timedelta(seconds=1_801)
        repository.admit_submission(
            {**fifth, "record_expires_at": (after_margin + timedelta(days=1)).isoformat()},
            now=after_margin.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertIn(str(fifth["job_id"]), self._active_job_ids(client))

    def test_legacy_admission_migrates_only_after_complete_drain(self) -> None:
        now = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        record = self._admission_job(JOB_ID, now=now)
        for state in (
            JobState.QUEUED.value,
            JobState.RUNNING.value,
            JobState.CANCELLING.value,
        ):
            with self.subTest(state=state):
                repository, client, _module = self._repository()
                client.store["_all_things_agentic_admission"] = _legacy_admission(
                    window_started=now - timedelta(hours=2)
                )
                client.store["00000000-0000-4000-8009-000000000001"] = (
                    self._admission_job(
                        "00000000-0000-4000-8009-000000000001",
                        now=now,
                        state=state,
                    )
                )
                with self.assertRaisesRegex(
                    AdmissionLimitError, "legacy jobs must drain"
                ):
                    repository.admit_submission(
                        record,
                        now=now.isoformat(),
                        cooldown_seconds=0,
                        window_seconds=3_600,
                        max_jobs=4,
                    )
                self.assertNotIn(JOB_ID, client.store)
                self.assertEqual(
                    client.store["_all_things_agentic_admission"]["schema"],
                    "video-studio.all-things-agentic-admission/v1",
                )

        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(hours=2)
        )
        repository.admit_submission(
            record,
            now=now.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertEqual(
            client.store["_all_things_agentic_admission"]["schema"],
            "video-studio.all-things-agentic-admission/v2",
        )
        self.assertEqual(self._active_job_ids(client), [JOB_ID])

    def test_legacy_migration_waits_for_window_and_visual_fifo(self) -> None:
        now = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        record = self._admission_job(JOB_ID, now=now)

        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(minutes=30)
        )
        with self.assertRaisesRegex(AdmissionLimitError, "legacy admission ledger"):
            repository.admit_submission(
                record,
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )

        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(hours=2)
        )
        client.store["_all_things_agentic_visual_capacity"] = _gate(
            ["9" * 64], now=now
        )
        with self.assertRaisesRegex(AdmissionLimitError, "legacy jobs must drain"):
            repository.admit_submission(
                record,
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertNotIn(JOB_ID, client.store)

        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(hours=2)
        )
        client.store["_all_things_agentic_visual_capacity"] = (
            _visual_capacity_update(
                queue=[],
                reservations=[now],
                now=now,
                window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
                max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
            )
        )
        with self.assertRaisesRegex(AdmissionLimitError, "legacy jobs must drain"):
            repository.admit_submission(
                record,
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertNotIn(JOB_ID, client.store)

    def test_legacy_migration_fails_closed_when_active_query_is_unavailable(self) -> None:
        now = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(hours=2)
        )
        client.query_failure = True
        with self.assertRaisesRegex(RuntimeError, "injected query failure"):
            repository.admit_submission(
                self._admission_job(JOB_ID, now=now),
                now=now.isoformat(),
                cooldown_seconds=0,
                window_seconds=3_600,
                max_jobs=4,
            )
        self.assertNotIn(JOB_ID, client.store)

    def test_retry_safely_migrates_a_drained_legacy_ledger(self) -> None:
        now = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        repository, client, _module = self._repository()
        client.store["_all_things_agentic_admission"] = _legacy_admission(
            window_started=now - timedelta(hours=2)
        )
        failed = self._admission_job(JOB_ID, now=now, state=JobState.FAILED.value)
        client.store[JOB_ID] = failed
        retried = repository.prepare_retry(
            JOB_ID,
            {
                "state": JobState.QUEUED.value,
                "record_expires_at": (now + timedelta(days=2)).isoformat(),
            },
            now=now.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        self.assertEqual(retried["attempt"], 2)
        self.assertEqual(
            client.store["_all_things_agentic_admission"]["schema"],
            "video-studio.all-things-agentic-admission/v2",
        )
        self.assertEqual(self._active_job_ids(client), [JOB_ID])

    def test_all_claimed_terminal_paths_release_active_slot(self) -> None:
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        for route in ("continue_cancel", "defer_cancel", "finalize"):
            with self.subTest(route=route):
                repository, client, _module = self._repository()
                repository.admit_submission(
                    self._admission_job(JOB_ID, now=now),
                    now=now.isoformat(),
                    cooldown_seconds=0,
                    window_seconds=3_600,
                    max_jobs=4,
                )
                token = (route[0] * 64).replace("c", "a").replace("d", "b").replace("f", "c")
                client.store[JOB_ID].update(
                    {
                        "state": (
                            JobState.CANCELLING.value
                            if route != "finalize"
                            else JobState.RUNNING.value
                        ),
                        "cancel_requested": route != "finalize",
                        "dispatch_sequence": 1,
                        "lease_token": "lease-a",
                        "lease_expires_at": (now + timedelta(minutes=30)).isoformat(),
                        "continuation": _continuation(token, not_before=epoch),
                    }
                )
                client.store["_all_things_agentic_visual_capacity"] = _gate(
                    [token], now=now
                )
                if route == "continue_cancel":
                    repository.continue_job(
                        JOB_ID,
                        {
                            "state": JobState.QUEUED.value,
                            "dispatch_sequence": 2,
                        },
                        attempt=1,
                        dispatch_sequence=1,
                        lease_token="lease-a",
                        cancelled_patch={
                            "state": JobState.CANCELLED.value,
                            "continuation": None,
                        },
                    )
                elif route == "defer_cancel":
                    repository.defer_claimed(
                        JOB_ID,
                        {"state": JobState.QUEUED.value},
                        attempt=1,
                        dispatch_sequence=1,
                        lease_token="lease-a",
                        cancelled_patch={
                            "state": JobState.CANCELLED.value,
                            "continuation": None,
                        },
                    )
                else:
                    repository.finalize(
                        JOB_ID,
                        {"state": JobState.SUCCEEDED.value},
                        attempt=1,
                        lease_token="lease-a",
                        cancelled_patch={"state": JobState.CANCELLED.value},
                    )
                self.assertNotIn(JOB_ID, self._active_job_ids(client))

    def test_stale_claimed_terminal_attempt_keeps_active_slot(self) -> None:
        now = datetime.now(timezone.utc)
        repository, client, _module = self._repository()
        repository.admit_submission(
            self._admission_job(JOB_ID, now=now),
            now=now.isoformat(),
            cooldown_seconds=0,
            window_seconds=3_600,
            max_jobs=4,
        )
        client.store[JOB_ID].update(
            {
                "state": JobState.RUNNING.value,
                "lease_token": "lease-current",
                "lease_expires_at": (now + timedelta(minutes=30)).isoformat(),
            }
        )
        unchanged = repository.finalize(
            JOB_ID,
            {"state": JobState.SUCCEEDED.value},
            attempt=1,
            lease_token="lease-stale",
            cancelled_patch={"state": JobState.CANCELLED.value},
        )
        self.assertEqual(unchanged["state"], JobState.RUNNING.value)
        self.assertIn(JOB_ID, self._active_job_ids(client))

    def test_continue_atomically_releases_old_turn_and_advances_job(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        old_token, new_token = "a" * 64, "b" * 64
        client.store[JOB_ID] = self._running_job(
            old_token, not_before=epoch
        )
        client.store["_all_things_agentic_visual_capacity"] = _gate(
            [old_token, new_token], now=now
        )

        updated = repository.continue_job(
            JOB_ID,
            {
                "state": JobState.QUEUED.value,
                "dispatch_sequence": 2,
                "continuation": _continuation(new_token, not_before=epoch + 75),
            },
            attempt=1,
            dispatch_sequence=1,
            lease_token="lease-a",
            cancelled_patch={"state": JobState.CANCELLED.value},
        )

        self.assertEqual(updated["state"], JobState.QUEUED.value)
        self.assertEqual(client.store[JOB_ID]["dispatch_sequence"], 2)
        self.assertIsNone(client.store[JOB_ID]["lease_token"])
        persisted = client.store["_all_things_agentic_visual_capacity"]
        self.assertEqual(
            [item["token_sha256"] for item in persisted["queue"]],
            [_visual_token_sha256(new_token)],
        )

    def test_continue_rolls_back_both_documents_if_second_write_fails(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        old_token, new_token = "c" * 64, "d" * 64
        original_job = self._running_job(old_token, not_before=epoch)
        original_gate = _gate([old_token, new_token], now=now)
        client.store[JOB_ID] = deepcopy(original_job)
        client.store["_all_things_agentic_visual_capacity"] = deepcopy(original_gate)
        client.fail_on_write = 2

        with self.assertRaisesRegex(RuntimeError, "injected transaction"):
            repository.continue_job(
                JOB_ID,
                {
                    "state": JobState.QUEUED.value,
                    "dispatch_sequence": 2,
                    "continuation": _continuation(new_token, not_before=epoch + 75),
                },
                attempt=1,
                dispatch_sequence=1,
                lease_token="lease-a",
                cancelled_patch={"state": JobState.CANCELLED.value},
            )

        self.assertEqual(client.store[JOB_ID], original_job)
        self.assertEqual(
            client.store["_all_things_agentic_visual_capacity"], original_gate
        )

    def test_firestore_callback_retry_is_idempotent(self) -> None:
        repository, client, module = self._repository(retry_callback_once=True)
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        old_token, new_token = "e" * 64, "f" * 64
        client.store[JOB_ID] = self._running_job(old_token, not_before=epoch)
        client.store["_all_things_agentic_visual_capacity"] = _gate(
            [old_token, new_token], now=now
        )

        repository.continue_job(
            JOB_ID,
            {
                "state": JobState.QUEUED.value,
                "dispatch_sequence": 2,
                "continuation": _continuation(new_token, not_before=epoch + 75),
            },
            attempt=1,
            dispatch_sequence=1,
            lease_token="lease-a",
            cancelled_patch={"state": JobState.CANCELLED.value},
        )

        self.assertEqual(module.callback_calls, 2)
        queue = client.store["_all_things_agentic_visual_capacity"]["queue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["token_sha256"], _visual_token_sha256(new_token))

    def test_stale_same_sequence_lease_retains_shared_successor_token(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        old_token, successor_token = "1" * 64, "2" * 64
        client.store[JOB_ID] = self._running_job(
            old_token, not_before=epoch, lease_token="lease-b"
        )
        original_gate = _gate([old_token, successor_token], now=now)
        client.store["_all_things_agentic_visual_capacity"] = deepcopy(original_gate)

        unchanged = repository.continue_job(
            JOB_ID,
            {
                "state": JobState.QUEUED.value,
                "dispatch_sequence": 2,
                "continuation": _continuation(
                    successor_token, not_before=epoch + 75
                ),
            },
            attempt=1,
            dispatch_sequence=1,
            lease_token="lease-a",
            cancelled_patch={"state": JobState.CANCELLED.value},
        )

        self.assertEqual(unchanged["lease_token"], "lease-b")
        self.assertEqual(
            client.store["_all_things_agentic_visual_capacity"], original_gate
        )

    def test_stale_terminal_same_sequence_releases_provisional_successor(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        old_token, successor_token = "3" * 64, "4" * 64
        client.store[JOB_ID] = self._running_job(
            old_token,
            not_before=epoch,
            lease_token="",
            state=JobState.CANCELLED.value,
            cancel_requested=True,
        )
        client.store["_all_things_agentic_visual_capacity"] = _gate(
            [old_token, successor_token], now=now
        )

        repository.continue_job(
            JOB_ID,
            {
                "state": JobState.QUEUED.value,
                "dispatch_sequence": 2,
                "continuation": _continuation(
                    successor_token, not_before=epoch + 75
                ),
            },
            attempt=1,
            dispatch_sequence=1,
            lease_token="lease-a",
            cancelled_patch={"state": JobState.CANCELLED.value},
        )

        queue = client.store["_all_things_agentic_visual_capacity"]["queue"]
        self.assertEqual(
            [item["token_sha256"] for item in queue],
            [_visual_token_sha256(old_token)],
        )

    def test_running_cancellation_finalization_releases_current_turn(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        token = "5" * 64
        prepared_successor = _visual_capacity_reservation_token(
            job_id=JOB_ID,
            attempt=1,
            dispatch_sequence=2,
        )
        client.store[JOB_ID] = self._running_job(
            token,
            not_before=epoch,
            state=JobState.CANCELLING.value,
            cancel_requested=True,
        )
        client.store["_all_things_agentic_visual_capacity"] = _gate(
            [token, prepared_successor], now=now
        )

        cancelled = repository.finalize(
            JOB_ID,
            {"state": JobState.SUCCEEDED.value},
            attempt=1,
            lease_token="lease-a",
            cancelled_patch={
                "state": JobState.CANCELLED.value,
                "continuation": None,
            },
        )

        self.assertEqual(cancelled["state"], JobState.CANCELLED.value)
        self.assertEqual(
            client.store["_all_things_agentic_visual_capacity"]["queue"], []
        )

    def test_cancellation_release_of_current_and_prepared_turn_is_atomic(self) -> None:
        repository, client, _module = self._repository()
        now = datetime.now(timezone.utc)
        epoch = math.ceil(now.timestamp())
        current_token = "6" * 64
        prepared_successor = _visual_capacity_reservation_token(
            job_id=JOB_ID,
            attempt=1,
            dispatch_sequence=2,
        )
        original_job = self._running_job(
            current_token,
            not_before=epoch,
            state=JobState.CANCELLING.value,
            cancel_requested=True,
        )
        original_gate = _gate([current_token, prepared_successor], now=now)
        client.store[JOB_ID] = deepcopy(original_job)
        client.store["_all_things_agentic_visual_capacity"] = deepcopy(original_gate)
        client.fail_on_write = 2

        with self.assertRaisesRegex(RuntimeError, "injected transaction"):
            repository.finalize(
                JOB_ID,
                {"state": JobState.SUCCEEDED.value},
                attempt=1,
                lease_token="lease-a",
                cancelled_patch={
                    "state": JobState.CANCELLED.value,
                    "continuation": None,
                },
            )

        self.assertEqual(client.store[JOB_ID], original_job)
        self.assertEqual(
            client.store["_all_things_agentic_visual_capacity"], original_gate
        )

    def test_late_head_removal_of_full_fifo_persists_and_reloads(self) -> None:
        created_at = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        created_epoch = math.ceil(created_at.timestamp())
        queue = [
            {
                "token_sha256": hashlib.sha256(str(index).encode()).hexdigest(),
                "not_before_epoch_seconds": created_epoch
                + (index * VISUAL_CAPACITY_WINDOW_SECONDS),
                "requests_used": 0,
                "created_epoch_seconds": created_epoch,
            }
            for index in range(8)
        ]
        late_now = created_at + timedelta(seconds=7_000)
        remaining, removed = _remove_visual_capacity_tokens(
            queue,
            token_sha256_values={str(queue[0]["token_sha256"])},
            reservations=[],
            now=late_now,
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
        )
        persisted = _visual_capacity_update(
            queue=remaining,
            reservations=[],
            now=late_now,
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
        )

        loaded, reservations = _load_visual_capacity_state(
            persisted,
            now=late_now,
            window_seconds=VISUAL_CAPACITY_WINDOW_SECONDS,
            max_requests=VISUAL_CAPACITY_REQUEST_LIMIT,
        )

        self.assertTrue(removed)
        self.assertEqual(len(loaded), 7)
        self.assertEqual(reservations, [])
        self.assertGreater(
            int(loaded[-1]["not_before_epoch_seconds"]) - created_epoch,
            7_200,
        )
        self.assertTrue(
            all(
                int(loaded[index]["not_before_epoch_seconds"])
                - int(loaded[index - 1]["not_before_epoch_seconds"])
                >= VISUAL_CAPACITY_WINDOW_SECONDS
                for index in range(1, len(loaded))
            )
        )


if __name__ == "__main__":
    unittest.main()
