"""attempt and report_seq order the handoff. Older reports do not move it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_harness
from tests.support import FIXED_NOW, HANDOFF_ID, task_body
from tests.test_forward import Capture, _harness


def _open(tmp_path: Path, durable: bool):
    if not durable:
        return build_harness()
    return build_harness(state_db=str(tmp_path / "state.sqlite"))


def _generation(harness):
    bridge = harness.http.app.state.bridge
    book = bridge.durable if bridge.durable is not None else bridge.store
    return book.current_generation(HANDOFF_ID)


@pytest.mark.parametrize("durable", [False, True])
def test_sequence_conflict_409(tmp_path: Path, durable: bool) -> None:
    harness = _open(tmp_path, durable)
    original = task_body(attempt=2, report_seq=1, summary="first", idempotency_key="idem-seq-a")
    other = task_body(attempt=2, report_seq=1, summary="second", idempotency_key="idem-seq-b")
    first = harness.post(original, timestamp=int(FIXED_NOW))
    conflict = harness.post(other, timestamp=int(FIXED_NOW) + 1)
    again = harness.post(original, timestamp=int(FIXED_NOW) + 2)
    same_key = task_body(attempt=2, report_seq=1, summary="third", idempotency_key="idem-seq-a")
    idem = harness.post(same_key, timestamp=int(FIXED_NOW) + 3)
    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "sequence_conflict"}
    assert again.status_code == 202
    assert again.json() == first.json()
    assert idem.status_code == 409
    assert idem.json() == {"error": "idempotency_conflict"}
    assert _generation(harness) == (2, 1)
    assert len(harness.notifier.events) == 1
    harness.http.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt", 0),
        ("attempt", -1),
        ("attempt", True),
        ("attempt", 1.5),
        ("attempt", "1"),
        ("report_seq", 0),
        ("report_seq", False),
        ("report_seq", None),
    ],
)
def test_sequence_numbers_must_be_integers_at_least_one(harness, field: str, value: object) -> None:
    payload = json.loads(task_body())
    payload[field] = value
    response = harness.post(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert field in response.json()["fields"]


def test_sequence_fields_are_required(harness) -> None:
    for offset, name in enumerate(("attempt", "report_seq")):
        payload = json.loads(task_body())
        payload.pop(name)
        response = harness.post(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            timestamp=int(harness.now) + offset,
        )
        assert response.status_code == 400
        assert name in response.json()["fields"]


@pytest.mark.parametrize("durable", [False, True])
def test_older_generation_is_recorded_without_changing_state(tmp_path: Path, durable: bool) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=204)
    harness = _harness(
        capture,
        state_db=database if durable else None,
        jitter=lambda: 1.0,
        auto_pump=False,
    )
    newer = task_body(
        attempt=2,
        report_seq=2,
        status="completed",
        idempotency_key="idem-new",
        summary="newer",
    )
    older = task_body(
        attempt=1,
        report_seq=99,
        status="in_progress",
        idempotency_key="idem-old",
        summary="older",
    )
    assert harness.post(newer, timestamp=int(FIXED_NOW)).status_code == 202
    harness.pump(float(FIXED_NOW))
    assert len(capture.requests) == 1
    snap = harness.outbox.snapshot("idem-new")
    assert snap is not None
    assert snap["state"] == "delivered"
    late = harness.post(older, timestamp=int(FIXED_NOW) + 1)
    assert late.status_code == 202
    assert late.json()["forwarded"] is False
    assert len(capture.requests) == 1
    assert harness.outbox.snapshot("idem-new") == snap
    assert _generation(harness) == (2, 2)
    replay = harness.post(older, timestamp=int(FIXED_NOW) + 2)
    assert replay.status_code == 202
    assert len(capture.requests) == 1
    mid = task_body(attempt=2, report_seq=1, idempotency_key="idem-mid", summary="mid")
    assert harness.post(mid, timestamp=int(FIXED_NOW) + 3).status_code == 202
    assert _generation(harness) == (2, 2)
    nxt = task_body(attempt=2, report_seq=3, idempotency_key="idem-next", summary="next")
    assert harness.post(nxt, timestamp=int(FIXED_NOW) + 4).status_code == 202
    assert _generation(harness) == (2, 3)
    harness.pump(float(FIXED_NOW) + 4)
    assert len(capture.requests) == 2
    sent = json.loads(capture.requests[1].content)
    assert (sent["attempt"], sent["report_seq"]) == (2, 3)
    if durable:
        harness.http.close()
        resumed_capture = Capture(status=204)
        resumed = _harness(
            resumed_capture,
            state_db=database,
            jitter=lambda: 1.0,
            auto_pump=False,
        )
        after = task_body(
            attempt=1,
            report_seq=5,
            idempotency_key="idem-after-restart",
            summary="after restart",
        )
        recorded = resumed.post(after, timestamp=int(FIXED_NOW) + 5)
        assert recorded.status_code == 202
        assert resumed_capture.requests == []
        assert _generation(resumed) == (2, 3)
        resumed.http.close()
    else:
        harness.http.close()
