"""SQLite idempotency and replay records survive a new app on the same file."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_harness
from tests.support import HMAC_KEY, HANDOFF_ID, sign, task_body


def _close(harness) -> None:
    harness.http.close()


def test_same_key_after_restart_returns_stored(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    first_app = build_harness(state_db=str(database))
    original = task_body(summary="stored body")
    other = task_body(summary="other body")
    first = first_app.post(original, timestamp=int(first_app.now))
    assert first.status_code == 202
    assert first.json() == {
        "handoff_id": HANDOFF_ID,
        "status": "received",
        "executed": False,
        "forwarded": False,
        "forward_state": "disabled",
    }
    _close(first_app)

    restarted = build_harness(state_db=str(database))
    again = restarted.post(original, timestamp=int(restarted.now) + 1)
    conflict = restarted.post(other, timestamp=int(restarted.now) + 2)
    assert again.status_code == 202
    assert again.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert restarted.notifier.events == []
    _close(restarted)


def test_replay_rejected_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    body = task_body(idempotency_key="idem-replay-restart")
    first_app = build_harness(state_db=str(database))
    timestamp = str(int(first_app.now))
    signature = sign(HMAC_KEY, timestamp, body)
    first = first_app.post(body, timestamp=int(timestamp), signature=signature)
    assert first.status_code == 202
    _close(first_app)

    restarted = build_harness(state_db=str(database))
    replay = restarted.post(body, timestamp=int(timestamp), signature=signature)
    assert replay.status_code == 401
    assert replay.json() == {"error": "unauthorized"}
    assert restarted.notifier.events == []
    assert signature.encode() not in database.read_bytes()
    _close(restarted)


def test_replay_store_unavailable_fails_closed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="grok_cue")
    blocked = tmp_path / "not-a-database-file"
    blocked.mkdir()
    harness = build_harness(state_db=str(blocked))
    response = harness.post(task_body(summary="SUMMARY_SHOULD_NOT_BE_LOGGED"))
    assert response.status_code == 503
    assert response.json() == {"error": "storage_unavailable"}
    assert harness.notifier.events == []
    assert "state_db=unavailable" in caplog.text
    assert str(blocked) not in caplog.text
    assert "SUMMARY_SHOULD_NOT_BE_LOGGED" not in caplog.text
    _close(harness)
