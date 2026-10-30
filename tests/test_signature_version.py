"""Verified acceptances record signature version 1 or 2, never the signature."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app.persistence import StateDB
from tests.conftest import Harness, build_harness
from tests.support import HMAC_KEY, sign, sign_v2, task_body


def _column(database: str, key: str) -> int | None:
    conn = sqlite3.connect(database)
    try:
        row = conn.execute(
            "SELECT signature_version FROM idempotency WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return None if row[0] is None else int(row[0])


def test_acceptance_log_records_version_not_the_signature(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")
    body = task_body(idempotency_key="idem-ver-v1")
    timestamp = str(int(harness.now))
    signature = sign(HMAC_KEY, timestamp, body)
    accepted = harness.post(body, signature=signature)
    v2_body = task_body(idempotency_key="idem-ver-v2", report_seq=2)
    v2_timestamp = str(int(harness.now) + 1)
    v2_signature = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", v2_timestamp, v2_body)
    v2 = harness.post(
        v2_body,
        timestamp=int(harness.now) + 1,
        signature=v2_signature,
        signature_version="2",
    )
    assert accepted.status_code == 202
    assert v2.status_code == 202
    assert "signature_version=1" in caplog.text
    assert "signature_version=2" in caplog.text
    assert signature not in caplog.text
    assert v2_signature not in caplog.text
    assert HMAC_KEY not in caplog.text


def test_acceptance_row_stores_signature_version(tmp_path) -> None:
    database = str(tmp_path / "state.sqlite")
    harness = build_harness(state_db=database)
    v1 = harness.post(task_body(idempotency_key="idem-row-v1"))
    v2 = harness.post(
        task_body(idempotency_key="idem-row-v2", report_seq=2),
        timestamp=int(harness.now) + 1,
        signature_version="2",
    )
    assert v1.status_code == 202
    assert v2.status_code == 202
    assert _column(database, "idem-row-v1") == 1
    assert _column(database, "idem-row-v2") == 2
    harness.http.close()


def test_old_schema_db_gains_nullable_signature_version(tmp_path) -> None:
    database = tmp_path / "state.sqlite"
    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE idempotency (
            idempotency_key TEXT PRIMARY KEY,
            body_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO idempotency (idempotency_key, body_sha256, response_json) VALUES (?, ?, ?)",
        ("old-key", "aa", "{}"),
    )
    conn.commit()
    conn.close()

    harness = build_harness(state_db=str(database))
    accepted = harness.post(task_body(idempotency_key="idem-after-migrate"))
    assert accepted.status_code == 202
    assert _column(str(database), "old-key") is None
    assert _column(str(database), "idem-after-migrate") == 1

    StateDB(str(database))
    inspected = sqlite3.connect(database)
    try:
        names = [str(row[1]) for row in inspected.execute("PRAGMA table_info(idempotency)")]
    finally:
        inspected.close()
    assert names.count("signature_version") == 1
    harness.http.close()
