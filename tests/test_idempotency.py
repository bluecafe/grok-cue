from __future__ import annotations

import json

from tests.conftest import Harness
from tests.support import HANDOFF_ID, task_body


def test_same_key_and_body_returns_original_without_renotify(harness: Harness) -> None:
    body = task_body()
    first = harness.post(body, timestamp=int(harness.now))
    second = harness.post(body, timestamp=int(harness.now) + 1)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == {
        "handoff_id": HANDOFF_ID,
        "status": "received",
        "executed": False,
        "forwarded": False,
    }
    assert second.json() == first.json()
    assert second.headers["cache-control"] == "no-store"
    assert len(harness.notifier.events) == 1


def test_same_key_different_body_conflicts_and_keeps_original(harness: Harness) -> None:
    original = task_body(summary="first body")
    other = task_body(summary="second body")
    assert original != other
    first = harness.post(original, timestamp=int(harness.now))
    conflict = harness.post(other, timestamp=int(harness.now) + 1)
    again = harness.post(original, timestamp=int(harness.now) + 2)
    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert again.status_code == 202
    assert again.json() == first.json()
    assert len(harness.notifier.events) == 1


def test_different_json_key_order_is_a_different_body(harness: Harness) -> None:
    original = task_body()
    resorted = json.dumps(
        json.loads(original),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert resorted != original
    assert harness.post(original).status_code == 202
    conflict = harness.post(resorted, timestamp=int(harness.now) + 1)
    assert conflict.status_code == 409
    assert len(harness.notifier.events) == 1
