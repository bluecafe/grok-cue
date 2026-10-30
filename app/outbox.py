"""Outbox scheduling. A duplicate request never starts another send."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass

from app.forward import AttemptResult, GrokForwarder

MAX_ATTEMPTS = 7
BUDGET_SECONDS = 60 * 60
RETRY_AFTER_CAP_SECONDS = 30 * 60
# Gaps after attempts 1..6. Attempt 7 is the last try.
BACKOFF_SECONDS = (60, 120, 240, 480, 900, 1800)

def jitter_factor() -> float:
    """0% to -20%, so a wait never runs longer than the nominal backoff."""
    return random.uniform(0.8, 1.0)


def delay_seconds(completed_attempts: int, retry_after: float | None, jitter: float) -> float:
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, RETRY_AFTER_CAP_SECONDS)
    index = max(0, min(completed_attempts, len(BACKOFF_SECONDS)) - 1)
    return BACKOFF_SECONDS[index] * jitter


@dataclass
class OutboxJob:
    idempotency_key: str
    handoff_id: str
    payload: dict
    attempts: int
    first_attempt_at: float | None
    run: int = 1


def over_budget(first_attempt_at: float | None, now: float) -> bool:
    if first_attempt_at is None:
        return False
    return now > first_attempt_at + BUDGET_SECONDS


class MemoryOutbox:
    """Process-local outbox used when CUE_STATE_DB is empty."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def enqueue(
        self,
        key: str,
        handoff_id: str,
        payload: dict | None,
        *,
        enabled: bool,
        now: float,
    ) -> str:
        state = "queued" if enabled else "disabled"
        with self._lock:
            self._rows[key] = {
                "idempotency_key": key,
                "handoff_id": handoff_id,
                "payload": payload or {},
                "state": state,
                "attempts": 0,
                "first_attempt_at": None,
                "next_attempt_at": now if enabled else None,
                "run": 1,
            }
        return state

    def view(self, key: str) -> tuple[bool, str]:
        with self._lock:
            row = self._rows.get(key)
        if row is None:
            return False, "disabled"
        state = str(row["state"])
        return state == "delivered", state

    def snapshot(self, key: str) -> dict | None:
        with self._lock:
            row = self._rows.get(key)
            return None if row is None else dict(row)

    def claim_due(self, now: float) -> list[OutboxJob]:
        jobs: list[OutboxJob] = []
        with self._lock:
            for row in self._rows.values():
                if row["state"] not in {"queued", "retrying"}:
                    continue
                due = row["next_attempt_at"]
                if due is not None and float(due) > now:
                    continue
                if over_budget(row["first_attempt_at"], now) or int(row["attempts"]) >= MAX_ATTEMPTS:
                    row["state"] = "dead"
                    row["next_attempt_at"] = None
                    continue
                if row["first_attempt_at"] is None:
                    row["first_attempt_at"] = now
                row["state"] = "sending"
                jobs.append(_job(row))
        return jobs

    def recover_sending(self) -> None:
        with self._lock:
            for row in self._rows.values():
                if row["state"] == "sending":
                    row["state"] = "queued"
                    row["next_attempt_at"] = row["first_attempt_at"]

    def finish(self, job: OutboxJob, result: AttemptResult, now: float, jitter: float) -> str:
        with self._lock:
            row = self._rows[job.idempotency_key]
            attempts = int(row["attempts"]) + 1
            first = float(row["first_attempt_at"])
            state = _next_state(attempts, first, now, result, jitter)
            row["attempts"] = attempts
            row["state"] = state
            if state == "retrying":
                row["next_attempt_at"] = now + delay_seconds(attempts, result.retry_after, jitter)
            else:
                row["next_attempt_at"] = None
            return state

    async def pump(
        self,
        forwarder: GrokForwarder,
        now: float,
        jitter: float | None = None,
    ) -> None:
        factor = jitter_factor() if jitter is None else jitter
        for job in self.claim_due(now):
            result = await forwarder.attempt(job.payload)
            self.finish(job, result, now, factor)


def _job(row: dict) -> OutboxJob:
    return OutboxJob(
        idempotency_key=str(row["idempotency_key"]),
        handoff_id=str(row["handoff_id"]),
        payload=dict(row["payload"]),
        attempts=int(row["attempts"]),
        first_attempt_at=None if row["first_attempt_at"] is None else float(row["first_attempt_at"]),
        run=int(row["run"]),
    )


def _next_state(
    attempts: int,
    first_attempt_at: float,
    now: float,
    result: AttemptResult,
    jitter: float,
) -> str:
    if result.kind == "delivered":
        return "delivered"
    if result.kind == "dead" or attempts >= MAX_ATTEMPTS:
        return "dead"
    next_at = now + delay_seconds(attempts, result.retry_after, jitter)
    if next_at > first_attempt_at + BUDGET_SECONDS:
        return "dead"
    return "retrying"
