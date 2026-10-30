"""SQLite idempotency and replay records.

The database path is supplied by the caller. This module does not choose a
filesystem location. The raw signature is not stored; only its SHA-256 is.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

from app.auth import REPLAY_TTL_SECONDS
from app.store import IdempotencyConflict, RegisterResult


class StorageUnavailable(Exception):
    """The state database could not be read or written."""


class ReplaySeen(Exception):
    """This authenticated signature was already stored."""


def signature_hash(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


class StateDB:
    """Idempotency rows and replay rows in one database file."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL
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
            self._conn = conn
        except sqlite3.Error:
            self._conn = None

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
    ) -> RegisterResult:
        """Commit the replay row and the acceptance row in one transaction.

        The caller returns 202 only after this commit succeeds.
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
                if row is None:
                    payload = json.dumps(response, separators=(",", ":"), sort_keys=True)
                    conn.execute(
                        "INSERT INTO idempotency (idempotency_key, body_sha256, response_json) VALUES (?, ?, ?)",
                        (key, digest, payload),
                    )
                    self._commit(conn)
                    stored = json.loads(payload)
                    if not isinstance(stored, dict):
                        raise StorageUnavailable
                    return RegisterResult(response=stored, created=True)
                if row[0] != digest:
                    self._commit(conn)
                    raise IdempotencyConflict
                self._commit(conn)
                stored = json.loads(row[1])
                if not isinstance(stored, dict):
                    raise StorageUnavailable
                return RegisterResult(response=stored, created=False)
            except sqlite3.Error:
                self._safe_rollback(conn)
                raise StorageUnavailable from None

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
