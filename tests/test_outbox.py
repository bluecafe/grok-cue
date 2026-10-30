"""Outbox retries stay inside the documented 7-attempt, 60-minute budget."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx2 as httpx
import pytest

from app.outbox import BUDGET_SECONDS
from tests.support import FIXED_NOW, task_body
from tests.test_forward import Capture, _harness


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until predicate is true, or fail when timeout elapses."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(0.01)


def test_first_202_is_queued() -> None:
    capture = Capture(status=204)
    harness = _harness(capture)
    response = harness.post(task_body())
    body = response.json()
    assert response.status_code == 202
    assert body["forwarded"] is False
    assert body["forward_state"] == "queued"
    assert body["executed"] is False


def test_forwarded_true_only_after_2xx(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=500)
    waiting = _harness(capture, state_db=database, jitter=lambda: 1.0)
    first = waiting.post(task_body(idempotency_key="idem-after-2xx"))
    assert first.json()["forwarded"] is False
    assert first.json()["forward_state"] == "queued"
    assert waiting.outbox.snapshot("idem-after-2xx")["state"] == "retrying"
    waiting.http.close()

    delivered_capture = Capture(status=204)
    done = _harness(delivered_capture, state_db=database, jitter=lambda: 1.0)
    done.pump(FIXED_NOW + 60)
    replay = done.post(task_body(idempotency_key="idem-after-2xx"), timestamp=int(FIXED_NOW) + 3)
    assert replay.status_code == 202
    assert replay.json()["forwarded"] is True
    assert replay.json()["forward_state"] == "delivered"
    assert replay.json()["executed"] is False
    done.http.close()


def test_replay_returns_original_body_with_state() -> None:
    from tests.support import HANDOFF_ID

    capture = Capture(status=204)
    harness = _harness(capture)
    body = task_body(idempotency_key="idem-original-state")
    first = harness.post(body)
    second = harness.post(body, timestamp=int(harness.now) + 1)
    assert second.status_code == 202
    assert second.json()["handoff_id"] == HANDOFF_ID
    assert second.json()["status"] == "received"
    assert second.json()["executed"] is False
    assert second.json()["forward_state"] == "delivered"
    assert second.json()["forwarded"] is True
    assert first.json()["handoff_id"] == second.json()["handoff_id"]
    assert len(capture.requests) == 1


def test_replay_never_reforwards_queued_delivered_dead(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    queued = Capture(status=204)
    held = _harness(queued, state_db=database, jitter=lambda: 1.0, auto_pump=False)
    body = task_body(idempotency_key="idem-no-reforward")
    first = held.post(body)
    assert first.json()["forward_state"] == "queued"
    assert first.json()["forwarded"] is False
    again = held.post(body, timestamp=int(FIXED_NOW) + 1)
    assert again.json()["forward_state"] == "queued"
    assert queued.requests == []
    held.pump(float(FIXED_NOW))
    assert len(queued.requests) == 1
    delivered = held.post(body, timestamp=int(FIXED_NOW) + 2)
    assert delivered.json()["forward_state"] == "delivered"
    assert delivered.json()["forwarded"] is True
    assert len(queued.requests) == 1
    held.http.close()

    dead_db = str(tmp_path / "dead.sqlite")
    dead_capture = Capture(status=400)
    dead = _harness(dead_capture, state_db=dead_db, jitter=lambda: 1.0)
    dead_body = task_body(idempotency_key="idem-dead")
    dead.post(dead_body)
    assert dead_capture.requests
    replay = dead.post(dead_body, timestamp=int(FIXED_NOW) + 2)
    assert replay.json()["forward_state"] == "dead"
    assert replay.json()["forwarded"] is False
    assert len(dead_capture.requests) == 1
    dead.pump(FIXED_NOW + BUDGET_SECONDS)
    assert len(dead_capture.requests) == 1
    dead.http.close()


def test_seven_attempts_including_first_then_dead(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=503)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0)
    harness.post(task_body(idempotency_key="idem-seven"))
    moment = float(FIXED_NOW)
    gaps = (60, 120, 240, 480, 900, 1800)
    for gap in gaps:
        moment += gap
        harness.pump(moment)
    assert len(capture.requests) == 7
    snap = harness.outbox.snapshot("idem-seven")
    assert snap is not None
    assert snap["state"] == "dead"
    assert snap["attempts"] == 7
    harness.pump(moment + 60)
    assert len(capture.requests) == 7
    harness.http.close()


def test_retry_after_overrides_backoff_capped(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")

    def transport_for(header: str | None, status: int = 503) -> Capture:
        class HeaderCapture(Capture):
            def transport(self) -> httpx.MockTransport:
                base = super().transport()

                async def handler(request: httpx.Request) -> httpx.Response:
                    response = await base.handle_async_request(request) if False else None
                    self.requests.append(request)
                    headers = {}
                    if header is not None:
                        headers["Retry-After"] = header
                    return httpx.Response(status, headers=headers)

                return httpx.MockTransport(handler)

        return HeaderCapture()

    short = transport_for("10")
    harness = _harness(short, state_db=database, jitter=lambda: 1.0)
    harness.post(task_body(idempotency_key="idem-retry-after"))
    snap = harness.outbox.snapshot("idem-retry-after")
    assert snap is not None
    assert snap["next_attempt_at"] == float(FIXED_NOW) + 10
    harness.http.close()

    huge_db = str(tmp_path / "huge.sqlite")
    huge = transport_for("100000")
    huge_app = _harness(huge, state_db=huge_db, jitter=lambda: 1.0)
    huge_app.post(task_body(idempotency_key="idem-retry-cap"))
    capped = huge_app.outbox.snapshot("idem-retry-cap")
    assert capped is not None
    assert capped["next_attempt_at"] == float(FIXED_NOW) + 30 * 60
    huge_app.http.close()

    invalid_db = str(tmp_path / "invalid.sqlite")
    invalid = transport_for("soon")
    invalid_app = _harness(invalid, state_db=invalid_db, jitter=lambda: 1.0)
    invalid_app.post(task_body(idempotency_key="idem-retry-invalid"))
    nominal = invalid_app.outbox.snapshot("idem-retry-invalid")
    assert nominal is not None
    assert nominal["next_attempt_at"] == float(FIXED_NOW) + 60
    invalid_app.http.close()


def test_sixty_minute_budget_from_first_attempt(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=503)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0)
    harness.post(task_body(idempotency_key="idem-budget"))
    assert len(capture.requests) == 1
    harness.pump(float(FIXED_NOW) + BUDGET_SECONDS + 1)
    assert len(capture.requests) == 1
    snap = harness.outbox.snapshot("idem-budget")
    assert snap is not None
    assert snap["state"] == "dead"
    harness.http.close()


def test_4xx_is_dead_immediately(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=400)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0)
    response = harness.post(task_body(idempotency_key="idem-4xx"))
    assert response.json()["forward_state"] == "queued"
    assert response.json()["forwarded"] is False
    assert harness.outbox.snapshot("idem-4xx")["state"] == "dead"
    assert len(capture.requests) == 1
    harness.pump(float(FIXED_NOW) + 3600)
    assert len(capture.requests) == 1
    harness.http.close()


def test_sending_row_resent_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    capture = Capture(status=204)
    first = _harness(capture, state_db=str(database), jitter=lambda: 1.0, auto_pump=False)
    first.post(task_body())
    jobs = first.outbox.claim_due(float(FIXED_NOW))
    assert jobs
    assert first.outbox.snapshot("idem-1")["state"] == "sending"
    first.http.close()

    restarted_capture = Capture(status=204)
    restarted = _harness(restarted_capture, state_db=str(database), jitter=lambda: 1.0)
    assert restarted.outbox.snapshot("idem-1")["state"] == "queued"
    restarted.pump(float(FIXED_NOW))
    assert len(restarted_capture.requests) == 1
    assert restarted.outbox.snapshot("idem-1")["state"] == "delivered"
    restarted.http.close()


def test_retry_runs_as_time_passes_without_a_new_request(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    clock = {"now": float(FIXED_NOW)}
    capture = Capture(status=503)
    harness = _harness(
        capture,
        state_db=database,
        jitter=lambda: 1.0,
        clock=lambda: clock["now"],
        poll_seconds=0.05,
        lifespan=True,
    )
    harness.post(task_body(idempotency_key="idem-worker"))
    wait_until(lambda: len(capture.requests) == 1)
    clock["now"] = float(FIXED_NOW) + 60
    wait_until(lambda: len(capture.requests) == 2)
    harness.close()


def test_startup_pump_recovers_sending_and_due_rows(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    stale = Capture(status=204)
    first = _harness(stale, state_db=str(database), jitter=lambda: 1.0, auto_pump=False)
    first.post(task_body(idempotency_key="idem-stale-sending"))
    assert first.outbox.claim_due(float(FIXED_NOW))
    assert first.outbox.snapshot("idem-stale-sending")["state"] == "sending"
    first.http.close()

    recovered = Capture(status=204)
    started = _harness(
        recovered,
        state_db=str(database),
        jitter=lambda: 1.0,
        clock=lambda: float(FIXED_NOW),
        lifespan=True,
    )
    wait_until(lambda: len(recovered.requests) == 1)
    wait_until(lambda: started.outbox.snapshot("idem-stale-sending")["state"] == "delivered")
    started.close()

    due_capture = Capture(status=503)
    due_app = _harness(due_capture, state_db=str(database), jitter=lambda: 1.0, auto_pump=False)
    due_app.post(task_body(idempotency_key="idem-due"))
    due_app.pump(float(FIXED_NOW))
    assert due_app.outbox.snapshot("idem-due")["state"] == "retrying"
    due_app.http.close()

    resumed = Capture(status=204)
    resumed_app = _harness(
        resumed,
        state_db=str(database),
        jitter=lambda: 1.0,
        clock=lambda: float(FIXED_NOW) + 60,
        lifespan=True,
    )
    wait_until(lambda: len(resumed.requests) == 1)
    wait_until(lambda: resumed_app.outbox.snapshot("idem-due")["state"] == "delivered")
    resumed_app.close()


def test_worker_delivers_on_the_cycle_after_pump_raises(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=204)
    harness = _harness(
        capture,
        state_db=database,
        jitter=lambda: 1.0,
        poll_seconds=0.05,
        auto_pump=False,
        lifespan=True,
    )
    original = harness.forwarder.attempt
    raised = {"done": False}

    async def attempt(payload):
        if not raised["done"]:
            raised["done"] = True
            raise RuntimeError("boom-secret")
        return await original(payload)

    harness.forwarder.attempt = attempt
    harness.post(task_body(idempotency_key="idem-recover-worker"))
    wait_until(lambda: harness.outbox.snapshot("idem-recover-worker")["state"] == "delivered")
    assert len(capture.requests) == 1
    assert "class=RuntimeError" in caplog.text
    assert "boom-secret" not in caplog.text
    harness.close()


def test_concurrent_pump_does_not_double_send(tmp_path: Path) -> None:
    import asyncio

    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=204)
    harness = _harness(
        capture,
        state_db=database,
        jitter=lambda: 1.0,
        auto_pump=False,
    )
    harness.post(task_body(idempotency_key="idem-once"))
    assert capture.requests == []

    async def both() -> None:
        await asyncio.gather(
            harness.outbox.pump(harness.forwarder, float(FIXED_NOW), 1.0),
            harness.outbox.pump(harness.forwarder, float(FIXED_NOW), 1.0),
        )

    asyncio.run(both())
    assert len(capture.requests) == 1
    harness.http.close()


def test_state_db_file_mode_is_0600(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    harness = _harness(Capture(), state_db=str(database))
    harness.post(task_body(idempotency_key="idem-mode"))
    assert os.stat(database).st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        side = Path(str(database) + suffix)
        if side.exists():
            assert os.stat(side).st_mode & 0o777 == 0o600
    harness.http.close()
