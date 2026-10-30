[한국어](README.md) | English

# grok-cue

Current version: `0.2.1` ([VERSION](VERSION), [changelog](CHANGELOG.en.md))

grok-cue is a small, signed "reception desk" between two AI assistant services:
[Cue](https://cue.im/), the personal-agent app by Manus, and
[Grok Bot](https://docs.x.ai/grok-bot/overview), xAI's agents that work on their own cloud computers.
The two assistants have no direct way to hand finished work to each other. grok-cue accepts a signed
finished-work notice from the Cue side over HTTP and, if configured, forwards the accepted notice as JSON to a
Grok Bot webhook.

> Unofficial project. Not affiliated with or endorsed by Cue (Manus) or xAI's Grok Bot.

> [!WARNING]
> grok-cue is a development preview and is **accept-only**. HTTP `202` means "received", nothing more.
> It never executes tasks, merges, deploys, pays, or sends mail; `executed` is always `false`.
> Review [Security](#security) before exposing it to the internet.

Setup guides: [docs/SETUP.en.md](docs/SETUP.en.md) (for people) and [docs/AI_SETUP.en.md](docs/AI_SETUP.en.md) (for agents).

## What works

- `POST /v1/cue/tasks` accepts a handoff notice with a Bearer token **and** an HMAC-SHA256 signature, with a ±300 s clock window. `X-Cue-Signature-Version: 2` is required. The signed input is `v2`, the method, the path, the timestamp, and the body, separated by newlines. A missing header or `1` returns `401`.
- Each authenticated signature is single-use; replaying it returns `401`.
- Primary and secondary tokens and HMAC keys for zero-downtime key rotation.
- Idempotency by `idempotency_key`: same bytes return the original `202`, different bytes return `409`.
- Strict body validation (64 KiB limit, `application/json`, `github_ref` shape, no `task_id` anywhere).
- `reply_to` limited to an env-configured allowlist of single email addresses.
- Optional forwarding of a fixed set of 10 fields (`handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`; [spec](docs/DESIGN.en.md#spec)) to `GROK_WEBHOOK_URL`. Each attempt times out at 5 s. Retries are at most 7 attempts including the first, inside 60 minutes from the first attempt.
- Secrets (tokens, keys, signatures) and request bodies are kept out of logs. A verified acceptance logs and stores only `signature_version=2`. The response body has no version.
- `GET /healthz` for liveness.

When `CUE_STATE_DB` is set, acceptance, signature-replay, and outbox rows stay in that SQLite file. When it is empty, they stay in process memory and are lost on restart.

## How it works

```
 Cue side ──signed POST──▶ grok-cue ──accepted JSON (optional)──▶ Grok Bot webhook
                              │
                              └─ verify token + HMAC + time → validate → 202 / 4xx
```

Forwarding happens only when both `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set, and only for the
first acceptance of an idempotency key. The `202` is returned after that acceptance is committed. The first `202` has `forwarded: false`. `forwarded: true` only after that row records a Grok 2xx. A forwarding failure does not change the `202` already returned; the worker retries inside the limit.

After the 0.2.0 deployment at 2026-10-30 23:00 KST, dummy requests with v1 and with no version header both returned `401` (`unknown_signature_version`) and nothing was queued in the outbox. A v2 dummy request returned `202`, was `delivered`, and stored `signature_version=2`. `healthz` returned `200`. One real Cue request at 2026-10-30 22:40 KST was confirmed as v2 (journal and DB `signature_version=2`).

## Install

Have the following ready before installing; check each with the command shown.

| Prerequisite | Check | Expected |
| --- | --- | --- |
| Git | `git --version` | A version is printed |
| Python 3.11 or newer (`requires-python = ">=3.11"` in `pyproject.toml`) | `python3 --version` | `Python 3.11` or newer |
| Caddy (server, for HTTPS) | `caddy version` | A version is printed |
| Service account `bridge` (server) | `id bridge` | `uid=…(bridge)` |

If the service account does not exist, create it on the server:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin bridge
id bridge
```

### Local

```bash
git clone <repository-url> grok-cue
cd grok-cue
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env    # then replace every replace-with-... value
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8160
curl http://127.0.0.1:8160/healthz   # → ok
```

Generate secrets with `openssl rand -hex 32` (use different values for the token and the HMAC key).
Never commit `.env`.

### Server

[docs/SETUP.en.md](docs/SETUP.en.md) covers installing to `/opt/grok-cue`, a `grok-cue.service` systemd
unit, and putting a TLS reverse proxy such as Caddy in front (example host `hook.example.com`).
Keep uvicorn bound to `127.0.0.1`. Run a single process only. Do not use `uvicorn --workers` or more than one service instance. The outbox worker is one task inside that process.

### Cue-side setup (unverified)

We could **not confirm** from Cue's public materials that Cue can itself send a POST with Bearer and HMAC signature headers. The generic sender requirements are in [SETUP.en.md, Cue-side setup](docs/SETUP.en.md#cue-side-setup-unverified); the reference sender is [`scripts/check_bridge.py`](scripts/check_bridge.py).

## Configuration

All settings live in one `.env` file (or the same names as environment variables). Nothing is hard-coded.
Restart the process after changes.

| Variable | Required | Purpose |
| --- | --- | --- |
| `CUE_BRIDGE_TOKEN` | yes | Inbound `Authorization: Bearer` value |
| `CUE_BRIDGE_HMAC_KEY` | yes | HMAC-SHA256 key; must differ from the token |
| `CUE_BRIDGE_TOKEN_SECONDARY` | no | Second accepted token during rotation |
| `CUE_BRIDGE_HMAC_KEY_SECONDARY` | no | Second accepted HMAC key during rotation |
| `REPLY_TO_ALLOWLIST` | optional in code, **needed in practice** | Comma-separated allowed emails. Empty rejects every POST with `reply_to_not_allowed` |
| `REPLY_TO_DEFAULT` | optional in code, **needed in practice** | Used when `reply_to` is omitted or blank; must be on the allowlist |
| `GROK_WEBHOOK_URL` | no | Forward target. Empty disables forwarding |
| `GROK_WEBHOOK_KEY` | no | Key sent to the forward target |
| `GROK_WEBHOOK_KEY_HEADER` | no | Empty: `Authorization: Bearer <key>`. Set: send the bare key in this header |
| `CUE_STATE_DB` | no | SQLite path for acceptance, replay, and the outbox. Empty: process memory only. No built-in path |
| `CUE_OUTBOX_POLL_SECONDS` | no | Outbox poll interval in seconds for this process. Default 5. Values ≤ 0 are rejected. Also pumps once at startup |
| `CUE_SUMMARY_RETENTION_DAYS` | no | Days before `summary` is removed from `delivered` and `dead` rows. Default 30. Values ≤ 0 are rejected. `queued` and `sending` rows are kept |

`PUBLIC_BASE_URL` (for example `https://hook.example.com`) is read only by docs and check scripts; the app ignores it.

## API

### Authentication

Every `POST /v1/cue/tasks` needs all of:

- `Authorization: Bearer <CUE_BRIDGE_TOKEN>`
- `X-Cue-Timestamp`: Unix seconds, within ±300 s of server time
- `X-Cue-Signature`: hex HMAC-SHA256. Hex is case-insensitive; a `sha256=` prefix is rejected.
- `X-Cue-Signature-Version: 2` is required. The input is `v2`, METHOD, PATH, timestamp, and raw_body, joined by newlines. Example: METHOD `POST`, PATH `/v1/cue/tasks`.
- A missing header, `1`, or any other value is `401` `{"error":"unauthorized"}`. The response body contains no signature and no secret.
- A verified acceptance logs and stores only `signature_version=2`. The signature string is not logged.

To retry, keep the exact body bytes and compute a new timestamp and signature.

```bash
# Placeholders only. Run this check BEFORE enabling forwarding,
# otherwise the fake handoff is really forwarded to Grok Bot.
TOKEN='replace-with-bearer-token'; KEY='replace-with-hmac-key'
TS="$(date +%s)"
BODY='{"schema_version":"1","handoff_id":"00000000-0000-4000-8000-000000000000","attempt":1,"report_seq":1,"repo":"owner/name","github_ref":{"repo":"owner/name","base_branch":"main","start_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"idempotency_key":"example-key-1","title":"example","status":"completed","summary":"example summary","created_at":"2026-10-29T00:00:00Z","links":[],"next_owner":"example"}'
SIG="$(printf 'v2\nPOST\n/v1/cue/tasks\n%s\n%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$KEY" -hex | awk '{print $NF}')"
curl -sS -D - http://127.0.0.1:8160/v1/cue/tasks \
  -H "Authorization: Bearer $TOKEN" -H "X-Cue-Timestamp: $TS" -H "X-Cue-Signature: $SIG" \
  -H "X-Cue-Signature-Version: 2" \
  -H "Content-Type: application/json" --data-binary "$BODY"
```

Field reference: [docs/DESIGN.en.md](docs/DESIGN.en.md).

### Idempotency

Same `idempotency_key` + same bytes → `202` with the original `handoff_id`; no second notify or forward. `forwarded` and `forward_state` are the outbox state at that moment.
Same key + different bytes (even reordered JSON keys) → `409 idempotency_conflict`. Requests rejected for `reply_to` do not reserve the key.
Same `(handoff_id, attempt, report_seq)` + different bytes → `409 sequence_conflict`. An older generation is stored only; the current generation and its forward are left unchanged.

### Responses

| Code | Body | When |
| --- | --- | --- |
| 200 | `ok` | `GET /healthz` |
| 202 | `handoff_id`, `status: "received"`, `executed: false`, `forwarded`, `forward_state` | After the acceptance commit, or an idempotent replay |
| 400 | `invalid_request` + `fields` | Size (over 64 KiB is rejected before auth), content type, JSON, schema |
| 400 | `task_id_not_allowed` | A `task_id` key anywhere in the body |
| 400 | `reply_to_not_allowed` | `reply_to` not usable. The allowlist is not in the response |
| 401 | `unauthorized` | Token, signature, time window, reused signature, or unknown signature version |
| 409 | `idempotency_conflict` | Same key, different body |
| 409 | `sequence_conflict` | Same `(handoff_id, attempt, report_seq)`, different body |
| 500 | `internal` | Unhandled error |
| 503 | `storage_unavailable` | `POST /v1/cue/tasks` could not use the state database. `GET /healthz` stays `ok` |

`forwarded: true` only after that outbox row records a Grok 2xx. The first `202` is sent before that, so `forwarded` is `false`.
`422` is never returned.

## Security

- Run behind TLS; bind the app to `127.0.0.1`.
- Keep `.env` at mode `600` and out of version control. Never `source` it into a shell; the check script reads `.env` itself.
- The 64 KiB body limit is checked before authentication. A large `Content-Length` is rejected immediately, and chunked bodies without a length stop being read as soon as they exceed 64 KiB.
- The 401 response never says which check failed.
- `/docs`, `/redoc`, and `/openapi.json` return `404`. The docs UI is not served.
- Every response includes `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`. `X-Content-Type-Options: nosniff` is added when it is not already set.
- Report vulnerabilities as described in [SECURITY.en.md](SECURITY.en.md). Contributions: [CONTRIBUTING.en.md](CONTRIBUTING.en.md).

## Dependencies

Outbound HTTP uses [`httpx2`](https://github.com/pydantic/httpx2), the Pydantic-maintained successor to `httpx`. This is intentional; `pyproject.toml` pins it to `httpx2==2.16.0`, and `requirements.lock` locks every dependency with sha256 hashes (`pip-compile --generate-hashes --extra dev`).

## License

MIT License. Full text in [LICENSE](LICENSE). © grok-cue contributors
