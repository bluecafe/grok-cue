#!/usr/bin/env python3
"""Connectivity check for grok-cue. Standard library only.

Default is a dry run: it builds a signed TEST request and prints a summary
without sending anything and without printing secrets. Use --send to call the
server. Only use --send BEFORE forwarding (GROK_WEBHOOK_URL/KEY) is enabled,
otherwise the test handoff is really forwarded to Grok Bot.

Values are read from a .env file by a small KEY=VALUE parser. Nothing is
passed through a shell, so characters such as & $ ` ; are taken literally.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REQUIRED = ("PUBLIC_BASE_URL", "CUE_BRIDGE_TOKEN", "CUE_BRIDGE_HMAC_KEY")


def read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. No variable expansion, no command execution."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def test_body(handoff_id: str, idempotency_key: str, summary: str) -> bytes:
    payload = {
        "schema_version": "1",
        "handoff_id": handoff_id,
        "attempt": 1,
        "report_seq": 1,
        "repo": "owner/name",
        "github_ref": {"repo": "owner/name", "base_branch": "main", "start_sha": "a" * 40},
        "idempotency_key": idempotency_key,
        "title": "[TEST] connectivity check",
        "status": "completed",
        "summary": summary,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "links": [],
        "next_owner": "test",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(key: str, timestamp: str, raw: bytes) -> str:
    """Version-2 signature over method, path, timestamp, and the raw body."""
    prefix = f"v2\nPOST\n/v1/cue/tasks\n{timestamp}\n".encode("utf-8")
    return hmac.new(key.encode("utf-8"), prefix + raw, hashlib.sha256).hexdigest()


def signed_headers(token: str, key: str, raw: bytes, offset: int = 0) -> dict[str, str]:
    timestamp = str(int(time.time()) + offset)
    return {
        "Authorization": f"Bearer {token}",
        "X-Cue-Timestamp": timestamp,
        "X-Cue-Signature": sign(key, timestamp, raw),
        "X-Cue-Signature-Version": "2",
        "Content-Type": "application/json",
    }


def call(base: str, method: str, path: str, data: bytes | None, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", default=".env", help="path to the .env file (default: ./.env)")
    parser.add_argument("--send", action="store_true", help="actually send requests (default: dry run)")
    args = parser.parse_args()

    env = read_env(Path(args.env))
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        print("missing in", args.env + ":", ", ".join(missing))
        return 2
    base = env["PUBLIC_BASE_URL"].rstrip("/")
    token, key = env["CUE_BRIDGE_TOKEN"], env["CUE_BRIDGE_HMAC_KEY"]
    forwarding = bool(env.get("GROK_WEBHOOK_URL") and env.get("GROK_WEBHOOK_KEY"))

    handoff_id = str(uuid.uuid4())
    idem = f"test-{uuid.uuid4()}"
    raw = test_body(handoff_id, idem, "connectivity test — not a real task")

    print("target      ", base + "/v1/cue/tasks")
    print("handoff_id  ", handoff_id, "(test only)")
    print("idempotency ", idem)
    print("body bytes  ", len(raw))
    print("headers     ", "Authorization: Bearer <hidden>, X-Cue-Timestamp, X-Cue-Signature: <hidden>")
    print("forwarding  ", "ENABLED in .env - a --send would reach Grok Bot" if forwarding else "disabled in .env")
    if not args.send:
        print("dry run: nothing was sent. Re-run with --send to call the server.")
        return 0

    status, text = call(base, "GET", "/healthz", None, {})
    print("healthz ", status, text)
    assert status == 200 and text == "ok"

    status, text = call(base, "POST", "/v1/cue/tasks", b"{}", {"Content-Type": "application/json"})
    print("unsigned", status, text)
    assert status == 401

    status, text = call(base, "POST", "/v1/cue/tasks", raw, signed_headers(token, key, raw))
    print("signed  ", status, text)
    assert status == 202
    first = json.loads(text)
    assert first["status"] == "received" and first["executed"] is False

    status, text = call(base, "POST", "/v1/cue/tasks", raw, signed_headers(token, key, raw, 1))
    print("replay  ", status, text)
    assert status == 202
    second = json.loads(text)
    assert second["handoff_id"] == first["handoff_id"] and second["forwarded"] is False

    other = test_body(handoff_id, idem, "connectivity test — different body")
    status, text = call(base, "POST", "/v1/cue/tasks", other, signed_headers(token, key, other, 2))
    print("conflict", status, text)
    assert status == 409
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
