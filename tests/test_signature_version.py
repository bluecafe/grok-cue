"""Verified acceptances record signature version 1 or 2, never the signature."""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app.persistence import StateDB
from tests.conftest import Harness, build_harness
from tests.support import HMAC_KEY, sign_v2, task_body


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
    body = task_body(idempotency_key="idem-ver-v2")
    timestamp = str(int(harness.now))
    signature = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", timestamp, body)
    accepted = harness.post(body, signature=signature, signature_version="2")
    assert accepted.status_code == 202
    assert harness.notifier.events[0][3] == 2
    assert "signature_version=2" in caplog.text
    assert "signature_version=1" not in caplog.text
    assert signature not in caplog.text
    assert HMAC_KEY not in caplog.text


def test_acceptance_row_stores_signature_version(tmp_path) -> None:
    database = str(tmp_path / "state.sqlite")
    harness = build_harness(state_db=database)
    accepted = harness.post(task_body(idempotency_key="idem-row-v2"), signature_version="2")
    assert accepted.status_code == 202
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
    assert _column(str(database), "idem-after-migrate") == 2

    StateDB(str(database))
    inspected = sqlite3.connect(database)
    try:
        names = [str(row[1]) for row in inspected.execute("PRAGMA table_info(idempotency)")]
    finally:
        inspected.close()
    assert names.count("signature_version") == 1
    harness.http.close()


def test_signature_version_migration_failure_is_503(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")

    def fail_migration(self, conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("migrate")

    monkeypatch.setattr(StateDB, "_add_signature_version_column", fail_migration)
    harness = build_harness(state_db=str(tmp_path / "state.sqlite"))
    response = harness.post(task_body(idempotency_key="idem-migrate-fail"))
    assert response.status_code == 503
    assert response.json() == {"error": "storage_unavailable"}
    assert "migrate" not in response.text
    assert HMAC_KEY not in response.text
    assert "state_db=unavailable" in caplog.text
    assert str(tmp_path) not in caplog.text
    harness.http.close()
