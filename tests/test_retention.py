"""Finished rows lose summary after the retention period. In-flight rows do not."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.auth import REPLAY_TTL_SECONDS, ReplayCache
from tests.support import FIXED_NOW, HMAC_KEY, HANDOFF_ID, sign_v2, task_body
from tests.test_forward import Capture, _harness

SUMMARY = "summary-should-expire"
RETENTION = 30 * 86400


def _age_finished_at(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("UPDATE outbox SET finished_at = 0")
    conn.commit()
    conn.close()


def test_delivered_summary_expires_only_after_the_retention_period(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=204)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0, auto_pump=False)
    harness.post(task_body(summary=SUMMARY))
    harness.pump(float(FIXED_NOW))
    assert harness.outbox.snapshot("idem-1")["state"] == "delivered"
    assert harness.outbox.stored_payload("idem-1")["summary"] == SUMMARY
    harness.pump(float(FIXED_NOW) + RETENTION - 1)
    assert harness.outbox.stored_payload("idem-1")["summary"] == SUMMARY
    harness.pump(float(FIXED_NOW) + RETENTION)
    expired = harness.outbox.stored_payload("idem-1")
    assert expired is not None
    assert "summary" not in expired
    assert expired["handoff_id"] == HANDOFF_ID
    assert harness.outbox.snapshot("idem-1")["state"] == "delivered"
    harness.http.close()


def test_dead_summary_expires_after_the_retention_period(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=400)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0, auto_pump=False)
    harness.post(task_body(summary=SUMMARY, idempotency_key="idem-dead"))
    harness.pump(float(FIXED_NOW))
    assert harness.outbox.snapshot("idem-dead")["state"] == "dead"
    assert harness.outbox.stored_payload("idem-dead")["summary"] == SUMMARY
    harness.outbox.apply_retention(float(FIXED_NOW) + RETENTION)
    assert "summary" not in harness.outbox.stored_payload("idem-dead")
    harness.http.close()


def test_queued_and_sending_keep_summary(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=204)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0, auto_pump=False)
    harness.post(task_body(summary=SUMMARY, idempotency_key="idem-queued"))
    harness.post(
        task_body(summary=SUMMARY, idempotency_key="idem-sending", report_seq=2),
        timestamp=int(FIXED_NOW) + 1,
    )
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE outbox SET next_attempt_at = ? WHERE idempotency_key = ?",
        (1e18, "idem-queued"),
    )
    conn.commit()
    conn.close()
    jobs = harness.outbox.claim_due(float(FIXED_NOW))
    assert [job.idempotency_key for job in jobs] == ["idem-sending"]
    assert harness.outbox.snapshot("idem-queued")["state"] == "queued"
    assert harness.outbox.snapshot("idem-sending")["state"] == "sending"
    _age_finished_at(database)
    harness.outbox.apply_retention(float(FIXED_NOW) + RETENTION + 10)
    assert harness.outbox.snapshot("idem-queued")["state"] == "queued"
    assert harness.outbox.snapshot("idem-sending")["state"] == "sending"
    assert harness.outbox.stored_payload("idem-queued")["summary"] == SUMMARY
    assert harness.outbox.stored_payload("idem-sending")["summary"] == SUMMARY
    assert capture.requests == []
    harness.http.close()


def test_retrying_summary_is_kept(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    capture = Capture(status=503)
    harness = _harness(capture, state_db=database, jitter=lambda: 1.0, auto_pump=False)
    harness.post(task_body(summary=SUMMARY, idempotency_key="idem-retry"))
    harness.pump(float(FIXED_NOW))
    assert harness.outbox.snapshot("idem-retry")["state"] == "retrying"
    _age_finished_at(database)
    harness.outbox.apply_retention(float(FIXED_NOW) + RETENTION + 10)
    assert harness.outbox.snapshot("idem-retry")["state"] == "retrying"
    assert harness.outbox.stored_payload("idem-retry")["summary"] == SUMMARY
    harness.http.close()


def test_replay_rows_past_the_signature_window_are_deleted(tmp_path: Path) -> None:
    database = str(tmp_path / "state.sqlite")
    harness = _harness(Capture(status=204), state_db=database, jitter=lambda: 1.0, auto_pump=False)
    aged = task_body(summary=SUMMARY, idempotency_key="idem-aged", report_seq=1)
    fresh = task_body(summary=SUMMARY, idempotency_key="idem-fresh", report_seq=2)
    aged_ts = str(int(FIXED_NOW))
    fresh_ts = str(int(FIXED_NOW) + 1)
    aged_sig = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", aged_ts, aged)
    fresh_sig = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", fresh_ts, fresh)
    assert harness.post(aged, timestamp=int(aged_ts), signature=aged_sig).status_code == 202
    assert harness.post(fresh, timestamp=int(fresh_ts), signature=fresh_sig).status_code == 202
    assert harness.outbox.replay_count() == 2
    conn = sqlite3.connect(database)
    conn.execute(
        "UPDATE replay SET ts = ? WHERE ts = (SELECT MIN(ts) FROM replay)",
        (int(FIXED_NOW) - REPLAY_TTL_SECONDS - 5,),
    )
    conn.commit()
    conn.close()
    harness.outbox.apply_retention(float(FIXED_NOW) + 1)
    assert harness.outbox.replay_count() == 1
    reused = harness.post(aged, timestamp=int(aged_ts), signature=aged_sig)
    still_spent = harness.post(fresh, timestamp=int(fresh_ts), signature=fresh_sig)
    assert reused.status_code == 202
    assert still_spent.status_code == 401
    assert REPLAY_TTL_SECONDS == 600
    harness.http.close()


def test_memory_rows_follow_the_same_retention_rules() -> None:
    capture = Capture(status=204)
    queued = _harness(capture, jitter=lambda: 1.0, auto_pump=False)
    queued.post(task_body(summary=SUMMARY, idempotency_key="idem-queued"))
    queued.outbox.apply_retention(float(FIXED_NOW) + RETENTION + 10)
    assert queued.outbox.snapshot("idem-queued")["state"] == "queued"
    assert queued.outbox.stored_payload("idem-queued")["summary"] == SUMMARY
    queued.http.close()

    delivered_capture = Capture(status=204)
    delivered = _harness(delivered_capture, jitter=lambda: 1.0, auto_pump=False)
    delivered.post(task_body(summary=SUMMARY, idempotency_key="idem-delivered", report_seq=2))
    delivered.pump(float(FIXED_NOW))
    assert delivered.outbox.snapshot("idem-delivered")["state"] == "delivered"
    delivered.pump(float(FIXED_NOW) + RETENTION - 1)
    assert delivered.outbox.stored_payload("idem-delivered")["summary"] == SUMMARY
    delivered.pump(float(FIXED_NOW) + RETENTION)
    assert "summary" not in delivered.outbox.stored_payload("idem-delivered")
    delivered.http.close()

    sending_capture = Capture(status=204)
    sending = _harness(sending_capture, jitter=lambda: 1.0, auto_pump=False)
    sending.post(task_body(summary=SUMMARY, idempotency_key="idem-sending", report_seq=3))
    assert sending.outbox.claim_due(float(FIXED_NOW))
    assert sending.outbox.snapshot("idem-sending")["state"] == "sending"
    sending.outbox._rows["idem-sending"]["finished_at"] = 0
    sending.outbox.apply_retention(float(FIXED_NOW) + RETENTION + 10)
    assert sending.outbox.snapshot("idem-sending")["state"] == "sending"
    assert sending.outbox.stored_payload("idem-sending")["summary"] == SUMMARY
    assert sending_capture.requests == []
    sending.http.close()


def test_memory_replay_past_the_window_is_forgotten() -> None:
    cache = ReplayCache()
    ts = int(FIXED_NOW)
    assert cache.seen_or_remember("sig-a", ts, float(ts)) is False
    cache.prune(float(ts) + REPLAY_TTL_SECONDS)
    assert cache.seen_or_remember("sig-a", ts, float(ts) + REPLAY_TTL_SECONDS) is True
    cache.prune(float(ts) + REPLAY_TTL_SECONDS + 1)
    assert cache.seen_or_remember("sig-a", ts, float(ts) + REPLAY_TTL_SECONDS + 1) is False
