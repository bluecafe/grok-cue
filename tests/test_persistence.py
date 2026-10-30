"""SQLite idempotency and replay records survive a new app on the same file."""

from __future__ import annotations

from pathlib import Path

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


def test_replay_store_unavailable_fails_closed(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-database-file"
    blocked.mkdir()
    harness = build_harness(state_db=str(blocked))
    response = harness.post(task_body())
    assert response.status_code == 503
    assert response.json() == {"error": "storage_unavailable"}
    assert harness.notifier.events == []
    _close(harness)
