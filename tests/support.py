"""Test client helpers. The HMAC formula here is the contract, not a shared app helper."""

from __future__ import annotations

import hashlib
import hmac
import json

FIXED_NOW = 1_700_000_000
START_SHA = "a" * 40
HANDOFF_ID = "11111111-1111-4111-8111-111111111111"
REPO = "example/repo"
TOKEN = "test-token"
HMAC_KEY = "test-hmac-key"


def sign(key: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def github_ref(**overrides: object) -> dict[str, object]:
    ref: dict[str, object] = {
        "repo": REPO,
        "base_branch": "main",
        "start_sha": START_SHA,
        "issue_or_pr_url": f"https://github.com/{REPO}/issues/1",
        "issue_or_pr_number": 1,
    }
    ref.update(overrides)
    return ref


def task_body(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "schema_version": "1",
        "handoff_id": HANDOFF_ID,
        "repo": REPO,
        "idempotency_key": "idem-1",
        "title": "dummy handoff",
        "status": "completed",
        "summary": "smoke",
        "created_at": "2026-10-28T12:00:00Z",
        "github_ref": github_ref(),
        "links": [],
        "next_owner": "example",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
