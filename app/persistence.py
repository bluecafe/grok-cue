"""SQLite idempotency and replay records.

The database path is supplied by the caller. This module does not choose a
filesystem location. The raw signature is not stored; only its SHA-256 is.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable

from app.auth import REPLAY_TTL_SECONDS
from app.forward import AttemptResult, GrokForwarder
from app.outbox import (
    DEFAULT_SUMMARY_RETENTION_SECONDS,
    MAX_ATTEMPTS,
    OutboxJob,
    _next_state,
    delay_seconds,
    drop_summary,
    jitter_factor,
    over_budget,
)
from app.store import IdempotencyConflict, RegisterResult, SequenceConflict, is_older_generation

logger = logging.getLogger("grok_cue")


class StorageUnavailable(Exception):
    """The state database could not be read or written."""


class ReplaySeen(Exception):
    """This authenticated signature was already stored."""


def signature_hash(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


class StateDB:
    """Idempotency rows and replay rows in one database file."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._pump_lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None
        self.summary_retention_seconds = float(DEFAULT_SUMMARY_RETENTION_SECONDS)
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(path, 0o600)
            conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            self._restrict(path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    signature_version INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS replay (
                    sig_sha256 TEXT PRIMARY KEY,
                    ts INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    idempotency_key TEXT PRIMARY KEY,
                    handoff_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    first_attempt_at REAL,
                    next_attempt_at REAL,
                    run INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_sequence (
                    handoff_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    report_seq INTEGER NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    PRIMARY KEY (handoff_id, attempt, report_seq)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS handoff_generation (
                    handoff_id TEXT PRIMARY KEY,
                    attempt INTEGER NOT NULL,
                    report_seq INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_attempt (
                    idempotency_key TEXT NOT NULL,
                    run INTEGER NOT NULL,
                    try_no INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    http_status INTEGER,
                    PRIMARY KEY (idempotency_key, run, try_no)
                )
                """
            )
            conn.execute(
                """
                UPDATE outbox
                SET state = 'queued', next_attempt_at = NULL
                WHERE state = 'sending'
                """
            )
            self._add_signature_version_column(conn)
            self._add_finished_at_column(conn)
            self._conn = conn
            self._restrict(path)
        except (OSError, sqlite3.Error):
            self._conn = None
            logger.info("state_db=unavailable")

    def seen(self, signature: str, now: float) -> bool:
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                self._prune(conn, now)
                found = self._replay_exists(conn, signature)
                self._commit(conn)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None
            return found

    def remember(self, signature: str, timestamp: int, now: float) -> None:
        """Spend a signature that will not be accepted. One transaction."""
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                self._prune(conn, now)
                self._insert_replay(conn, signature, timestamp)
                self._commit(conn)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None

    def accept(
        self,
        signature: str,
        timestamp: int,
        now: float,
        key: str,
        raw_body: bytes,
        response: dict[str, object],
        *,
        handoff_id: str,
        attempt: int,
        report_seq: int,
        signature_version: int,
        forward_payload: dict | None,
        forwarding: bool,
    ) -> RegisterResult:
        """Commit the replay row and the acceptance row in one transaction.

        The caller returns 202 only after this commit succeeds.
        An older generation is stored and is not placed in the outbox.
        """
        digest = hashlib.sha256(raw_body).hexdigest()
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                self._prune(conn, now)
                if not self._insert_replay(conn, signature, timestamp):
                    self._commit(conn)
                    raise ReplaySeen
                row = conn.execute(
                    "SELECT body_sha256, response_json FROM idempotency WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    if row[0] != digest:
                        self._commit(conn)
                        raise IdempotencyConflict
                    self._commit(conn)
                    stored = json.loads(row[1])
                    if not isinstance(stored, dict):
                        raise StorageUnavailable
                    return RegisterResult(response=stored, created=False, enqueue=False)
                sequence = conn.execute(
                    """
                    SELECT body_sha256, idempotency_key
                    FROM report_sequence
                    WHERE handoff_id = ? AND attempt = ? AND report_seq = ?
                    """,
                    (handoff_id, attempt, report_seq),
                ).fetchone()
                if sequence is not None:
                    if sequence[0] != digest:
                        self._commit(conn)
                        raise SequenceConflict
                    original = conn.execute(
                        "SELECT response_json FROM idempotency WHERE idempotency_key = ?",
                        (sequence[1],),
                    ).fetchone()
                    if original is None:
                        self._safe_rollback(conn)
                        raise StorageUnavailable
                    conn.execute(
                        """
                        INSERT INTO idempotency (
                            idempotency_key, body_sha256, response_json, signature_version
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (key, digest, original[0], signature_version),
                    )
                    self._commit(conn)
                    stored = json.loads(original[0])
                    if not isinstance(stored, dict):
                        raise StorageUnavailable
                    return RegisterResult(response=stored, created=False, enqueue=False)
                payload = json.dumps(response, separators=(",", ":"), sort_keys=True)
                conn.execute(
                    """
                    INSERT INTO idempotency (
                        idempotency_key, body_sha256, response_json, signature_version
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (key, digest, payload, signature_version),
                )
                conn.execute(
                    """
                    INSERT INTO report_sequence (
                        handoff_id, attempt, report_seq, body_sha256, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (handoff_id, attempt, report_seq, digest, key),
                )
                cursor = conn.execute(
                    "SELECT attempt, report_seq FROM handoff_generation WHERE handoff_id = ?",
                    (handoff_id,),
                ).fetchone()
                current = None if cursor is None else (int(cursor[0]), int(cursor[1]))
                older = is_older_generation(current, attempt, report_seq)
                if not older:
                    conn.execute(
                        """
                        INSERT INTO handoff_generation (handoff_id, attempt, report_seq)
                        VALUES (?, ?, ?)
                        ON CONFLICT(handoff_id) DO UPDATE SET
                            attempt = excluded.attempt,
                            report_seq = excluded.report_seq
                        """,
                        (handoff_id, attempt, report_seq),
                    )
                    self._insert_outbox(conn, key, handoff_id, forward_payload, forwarding, now)
                self._commit(conn)
                stored = json.loads(payload)
                if not isinstance(stored, dict):
                    raise StorageUnavailable
                return RegisterResult(response=stored, created=True, enqueue=not older)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None

    def current_generation(self, handoff_id: str) -> tuple[int, int] | None:
        with self._lock:
            conn = self._require()
            row = conn.execute(
                "SELECT attempt, report_seq FROM handoff_generation WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if row is None:
            return None
        return int(row[0]), int(row[1])

    def view(self, key: str) -> tuple[bool, str]:
        with self._lock:
            conn = self._require()
            row = conn.execute(
                "SELECT state FROM outbox WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return False, "disabled"
        state = str(row[0])
        return state == "delivered", state

    def snapshot(self, key: str) -> dict | None:
        with self._lock:
            conn = self._require()
            row = conn.execute(
                """
                SELECT state, attempts, first_attempt_at, next_attempt_at
                FROM outbox WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "state": row[0],
            "attempts": row[1],
            "first_attempt_at": row[2],
            "next_attempt_at": row[3],
        }

    def claim_due(self, now: float) -> list[OutboxJob]:
        jobs: list[OutboxJob] = []
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                rows = conn.execute(
                    """
                    SELECT idempotency_key, handoff_id, payload_json, attempts,
                           first_attempt_at, run
                    FROM outbox
                    WHERE state IN ('queued', 'retrying')
                      AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    """,
                    (now,),
                ).fetchall()
                for key, handoff_id, payload_json, attempts, first_at, run in rows:
                    if over_budget(first_at, now) or int(attempts) >= MAX_ATTEMPTS:
                        conn.execute(
                            """
                            UPDATE outbox
                            SET state = 'dead', next_attempt_at = NULL, finished_at = ?
                            WHERE idempotency_key = ?
                            """,
                            (now, key),
                        )
                        continue
                    started = now if first_at is None else float(first_at)
                    conn.execute(
                        """
                        UPDATE outbox
                        SET state = 'sending', first_attempt_at = ?
                        WHERE idempotency_key = ?
                        """,
                        (started, key),
                    )
                    payload = json.loads(payload_json)
                    if not isinstance(payload, dict):
                        payload = {}
                    jobs.append(
                        OutboxJob(
                            idempotency_key=str(key),
                            handoff_id=str(handoff_id),
                            payload=payload,
                            attempts=int(attempts),
                            first_attempt_at=started,
                            run=int(run),
                        )
                    )
                self._commit(conn)
                self._restrict(self._path)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None
        return jobs

    def finish(self, job: OutboxJob, result: AttemptResult, now: float, jitter: float) -> str:
        attempts = job.attempts + 1
        first = float(job.first_attempt_at if job.first_attempt_at is not None else now)
        state = _next_state(attempts, first, now, result, jitter)
        next_at = None
        finished_at = None
        if state == "retrying":
            next_at = now + delay_seconds(attempts, result.retry_after, jitter)
        else:
            finished_at = now
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                conn.execute(
                    """
                    UPDATE outbox
                    SET state = ?, attempts = ?, next_attempt_at = ?,
                        finished_at = CASE WHEN ? IS NULL THEN finished_at ELSE ? END
                    WHERE idempotency_key = ?
                    """,
                    (state, attempts, next_at, finished_at, finished_at, job.idempotency_key),
                )
                conn.execute(
                    """
                    INSERT INTO forward_attempt (
                        idempotency_key, run, try_no, started_at, outcome, http_status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.idempotency_key,
                        job.run,
                        attempts,
                        now,
                        result.kind,
                        result.http_status,
                    ),
                )
                self._commit(conn)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None
        return state

    def recover_sending(self) -> None:
        """Stale in-flight rows become due again. Attempt counts stay put."""
        with self._lock:
            conn = self._require()
            conn.execute(
                """
                UPDATE outbox
                SET state = 'queued', next_attempt_at = NULL
                WHERE state = 'sending'
                """
            )

    async def pump(
        self,
        forwarder: GrokForwarder,
        now: float,
        jitter: float | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        async with self._pump_lock:
            self.recover_sending()
            self.apply_retention(now)
            factor = jitter_factor() if jitter is None else jitter
            for job in self.claim_due(now):
                result = await forwarder.attempt(job.payload)
                finished_at = now if clock is None else clock()
                self.finish(job, result, finished_at, factor)

    def _insert_outbox(
        self,
        conn: sqlite3.Connection,
        key: str,
        handoff_id: str,
        payload: dict | None,
        forwarding: bool,
        now: float,
    ) -> None:
        state = "queued" if forwarding else "disabled"
        body = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True)
        conn.execute(
            """
            INSERT INTO outbox (
                idempotency_key, handoff_id, payload_json, state, attempts,
                first_attempt_at, next_attempt_at, run
            ) VALUES (?, ?, ?, ?, 0, NULL, ?, 1)
            """,
            (key, handoff_id, body, state, now if forwarding else None),
        )

    def stored_payload(self, key: str) -> dict | None:
        with self._lock:
            conn = self._require()
            row = conn.execute(
                "SELECT payload_json FROM outbox WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            return None
        return payload

    def replay_count(self) -> int:
        with self._lock:
            conn = self._require()
            row = conn.execute("SELECT COUNT(*) FROM replay").fetchone()
        if row is None:
            return 0
        return int(row[0])

    def apply_retention(self, now: float) -> None:
        """Clear summaries on finished rows and drop replay rows past the window.

        Queued and sending rows are not updated. Replay cleanup uses the
        signature validity window, not the summary retention period.
        """
        cutoff = now - float(self.summary_retention_seconds)
        with self._lock:
            conn = self._require()
            try:
                self._begin(conn)
                self._prune(conn, now)
                rows = conn.execute(
                    """
                    SELECT idempotency_key, payload_json
                    FROM outbox
                    WHERE state IN ('delivered', 'dead')
                      AND finished_at IS NOT NULL
                      AND finished_at <= ?
                    """,
                    (cutoff,),
                ).fetchall()
                for key, payload_json in rows:
                    payload = json.loads(payload_json)
                    if not isinstance(payload, dict) or not drop_summary(payload):
                        continue
                    conn.execute(
                        """
                        UPDATE outbox
                        SET payload_json = ?
                        WHERE idempotency_key = ? AND state IN ('delivered', 'dead')
                        """,
                        (
                            json.dumps(payload, separators=(",", ":"), sort_keys=True),
                            key,
                        ),
                    )
                self._commit(conn)
            except (sqlite3.Error, json.JSONDecodeError):
                self._safe_rollback(conn)
                raise StorageUnavailable from None

    def _add_finished_at_column(self, conn: sqlite3.Connection) -> None:
        """Record when a row became delivered or dead. Older rows use the first attempt."""
        self._begin(conn)
        try:
            names = {str(row[1]) for row in conn.execute("PRAGMA table_info(outbox)").fetchall()}
            if "finished_at" not in names:
                conn.execute("ALTER TABLE outbox ADD COLUMN finished_at REAL")
                conn.execute(
                    """
                    UPDATE outbox
                    SET finished_at = COALESCE(first_attempt_at, CAST(strftime('%s','now') AS INTEGER))
                    WHERE state IN ('delivered', 'dead') AND finished_at IS NULL
                    """
                )
            self._commit(conn)
        except sqlite3.Error:
            self._safe_rollback(conn)
            raise

    def _add_signature_version_column(self, conn: sqlite3.Connection) -> None:
        """Add the nullable acceptance column once. Existing rows stay NULL."""
        self._begin(conn)
        try:
            names = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(idempotency)").fetchall()
            }
            if "signature_version" not in names:
                conn.execute("ALTER TABLE idempotency ADD COLUMN signature_version INTEGER")
            self._commit(conn)
        except sqlite3.Error:
            self._safe_rollback(conn)
            raise

    def _restrict(self, path: str) -> None:
        for candidate in (path, path + "-wal", path + "-shm"):
            if os.path.exists(candidate):
                os.chmod(candidate, 0o600)

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageUnavailable
        return self._conn

    def _begin(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")

    def _commit(self, conn: sqlite3.Connection) -> None:
        conn.execute("COMMIT")

    def _rollback(self, conn: sqlite3.Connection) -> None:
        conn.execute("ROLLBACK")

    def _safe_rollback(self, conn: sqlite3.Connection) -> None:
        try:
            self._rollback(conn)
        except sqlite3.Error:
            return

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        cutoff = int(now) - REPLAY_TTL_SECONDS
        conn.execute("DELETE FROM replay WHERE ts < ?", (cutoff,))

    def _replay_exists(self, conn: sqlite3.Connection, signature: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM replay WHERE sig_sha256 = ?",
            (signature_hash(signature),),
        ).fetchone()
        return row is not None

    def _insert_replay(self, conn: sqlite3.Connection, signature: str, timestamp: int) -> bool:
        conn.execute(
            "INSERT OR IGNORE INTO replay (sig_sha256, ts) VALUES (?, ?)",
            (signature_hash(signature), timestamp),
        )
        changed = conn.execute("SELECT changes()").fetchone()
        return changed is not None and changed[0] == 1
