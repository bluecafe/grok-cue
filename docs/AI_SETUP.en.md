[한국어](AI_SETUP.md) | English

# Agent setup procedure

This document is a procedure for an agent such as Grok Bot or Cue to read on its own and install grok-cue. The human-oriented version is [SETUP.en.md](SETUP.en.md). Addresses, emails, paths, and keys are examples only. Do not run examples verbatim before receiving the user's real values.

All settings live in one `.env` file. The app reads the nine names in `app/settings.py`. `PUBLIC_BASE_URL` is read only by docs and the check script, never by the app. Response codes and header names follow [README.en.md](../README.en.md); body fields follow [DESIGN.en.md](DESIGN.en.md).

## 0. Inputs to get from the user

Ask for these before starting. If something is missing, stop; do not guess.

| Input | Required | Example | Notes |
| --- | --- | --- | --- |
| `PUBLIC_BASE_URL` | yes | `https://hook.example.com` | Write it only in `.env`. The check script reads it; the app does not |
| Server public IPv4 | yes | `203.0.113.10` | DNS A record value. Documentation placeholder |
| Server public IPv6 | no | `2001:db8::10` | If none, do not create AAAA |
| How to reach the server | yes | A shell the user approved | No SSH, package installs, or service starts before approval |
| Install folder | no | `/opt/grok-cue` | If empty use this example, but confirm with the user first |
| Internal listen address | no | `127.0.0.1:8160` | Never exposed to the internet |
| `REPLY_TO_ALLOWLIST` | yes | `cue@example.com` | Comma-separated emails. Empty rejects every request with `400` |
| `REPLY_TO_DEFAULT` | yes | `cue@example.com` | Used for omitted/blank `reply_to`. Must be on the allowlist |
| `GROK_WEBHOOK_URL` | no | `https://grok.example/hook` | Empty: no forwarding. The user provides the real URL |
| Whether `GROK_WEBHOOK_KEY` is in the server file | yes if a URL is used | done / not yet | Never receive the key value itself over chat |
| `GROK_WEBHOOK_KEY_HEADER` | no | empty or `X-Grok-Key` | Empty: `Authorization: Bearer` |
| Work repository | yes when sending work | `owner/name` | `owner/name` format |
| Base branch | yes when sending work | `main` | |
| Full SHA | yes when sending work | `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` | 40 or 64 characters. Short SHAs are rejected |
| `handoff_id` | yes when sending work | A UUID the user generated | `00000000-0000-4000-8000-000000000000` is an example; never use it in real requests |
| Use a rotation window? | no | no | Fill secondary variables only if yes |

Never accept key values (`CUE_BRIDGE_TOKEN`, `CUE_BRIDGE_HMAC_KEY`, secondaries, `GROK_WEBHOOK_KEY`) as input. Generate them on the server and write them only to the server's `.env`. If the user pastes a key into chat, do not repeat it; tell them to revoke it and generate a new one on the server.

## 1. Roles

### Grok Bot

Grok Bot sends the task email. When `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set, grok-cue schedules delivery of the accepted JSON with `add_task`. That schedule does not mean Grok received it.

- The routine's receiving address is `GROK_WEBHOOK_URL`.
- If `GROK_WEBHOOK_KEY_HEADER` is empty, the header is `Authorization: Bearer <GROK_WEBHOOK_KEY>`.
- If a header name is set, the bare key arrives in that header, without a Bearer prefix.
- Received keys are only `handoff_id`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`. Spec: [DESIGN.en.md](DESIGN.en.md#spec).
- `schema_version`, `title`, `created_at`, `reply_to`, `requested_by` are not sent.
- Timeout is 5 s. If the background POST fails, or Grok does not return 2xx, the `202` already returned does not change.
- Grok Bot does not send mail through this process, and grok-cue never sends mail.

### Cue

Cue-side flow:

1. Receive the task email.
2. Match `handoff_id` and the GitHub ref.
3. Record Cue's work and outputs in the shared repository.
4. Send the result link and status in one signed webhook request (`POST /v1/cue/tasks`).
5. Continue replies on the same `handoff_id`.

The 2026-10-29 E2E is a single successful request record. The current Cue environment sent a signed JSON POST, and the bridge returned HTTP `202` with `status`=`received`, `executed`=`false`, and `forwarded`=`true`. Grok-side receipt was confirmed separately by Norn (user) at 16:05 KST. This record covers only that one request. It does not prove that every Cue chat task is sent automatically, and it does not prove install compatibility in other environments.

HTTP `202` means the bridge received and validated the request and recorded it in memory. It does not mean Grok received it, and it does not mean any task ran. Currently, `forwarded: true` only means Grok delivery was scheduled as an `add_task` background task. It is not confirmation of a Grok 2xx response. Retries, an outbox, and persistent storage are not implemented.

The generic sender requirement is in [SETUP.en.md](SETUP.en.md), 'Cue-side setup'. The reference sender is `scripts/check_bridge.py`. Its default is a dry run. A real send requires explicit `--send`. A test request can schedule a real Grok delivery with `add_task`. That schedule is not confirmation of a Grok 2xx response.

Signing:

- `Authorization: Bearer <CUE_BRIDGE_TOKEN>`
- `X-Cue-Timestamp`: Unix seconds, within ±300 s of server time
- `X-Cue-Signature`: HMAC-SHA256 hex (case-insensitive) of `{timestamp}.{raw_body}`. No `sha256=` prefix
- `Content-Type: application/json`
- Key is `CUE_BRIDGE_HMAC_KEY`; during rotation the secondary is also accepted
- Both Bearer and HMAC must match

Retries:

The bridge does not implement retries, an outbox, or persistent storage. An acceptance is kept only in process memory. When the sender sends again, follow the rules below.

- Keep the body bytes and `idempotency_key`
- Regenerate only `X-Cue-Timestamp` and `X-Cue-Signature`
- Resending the same signature returns `401`
- Same body with a new signature returns `202` with the original `handoff_id` and `forwarded: false`
- Different body with the same key returns `409`

`github_ref` rules:

The repository, branch, and SHA in the task email, the issue or PR body, and the webhook `github_ref` must all match. If any differ, Cue stops and sends the webhook with `status: "needs_input"`. Fix the issue body's SHA to match the email before sending the email.

This server does not read email or issues; it sees only the JSON it receives. If top-level `repo` and `github_ref.repo` differ it returns `400` with `fields` = `repo`. Matching repository, branch, and SHA is Cue's job before sending.

The task email should contain:

- `handoff_id`
- `repo` (`owner/name`)
- Goal, done criteria, tests to run
- `github_ref` `repo`, `base_branch`, `start_sha`
- Optional: `paths`, `issue_or_pr_url`, `issue_or_pr_number`

Required webhook JSON fields: `schema_version` `"1"`, `handoff_id`, `repo`, `github_ref`, `idempotency_key`, `title`, `status`, `summary`, `created_at`, `links`, `next_owner`. `reply_to` and `requested_by` are optional. `links` is `[]` if none. `status` is one of `in_progress`, `needs_input`, `completed`, `failed`. Never include `task_id`, `diff`, `patch`, `source`, full source, or secrets.

## 2. Actions needing approval, and prohibitions

Get the user's approval before any of the following. Do not proceed without an explicit approval message.

- Changing DNS records
- Installing packages, cloning the repository, registering systemd, starting or restarting the process
- Opening 80 and 443 in the firewall
- Caddy configuration and certificate issuance
- Enabling forwarding by setting a real `GROK_WEBHOOK_URL`
- Deploying
- Merging
- Paying

`202` with `executed: false` means the bridge received and validated the request and recorded it in memory. It does not mean Grok received it, and it does not mean any task ran. `forwarded: true` only means Grok delivery was scheduled as an `add_task` background task. It is not confirmation of a Grok 2xx response. It is not task completion, and it is not approval to merge, deploy, or pay.

Prohibited:

- Putting keys, tokens, signatures, or `.env` contents into chat, email, issues, PRs, or the repository
- Committing `.env`
- Printing keys to logs
- Repeating a key value even if the user asks to see it
- Putting real domains, emails, or paths into code or defaults
- Exposing `8160` to the internet

## 3. Commands and success criteria

Do not move to the next step until its success criteria are met. The path is the example `/opt/grok-cue`; substitute the user's folder if given.

### 3.1 DNS

After approval, create the A record: name `hook`, full name the user's host, value the user's IPv4. If an IPv6 was given, create AAAA with the same name.

```bash
dig +short hook.example.com A
dig +short hook.example.com AAAA
nslookup hook.example.com
```

Success: A matches the user's IPv4; AAAA (if created) matches the user's IPv6. If empty or different, go to DNS failure in section 4.

### 3.2 Install

```bash
python3 --version
sudo mkdir -p /opt/grok-cue
sudo chown "$USER" /opt/grok-cue
git clone <repository-url> /opt/grok-cue
cd /opt/grok-cue
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
```

Success: Python is 3.11 or newer, `pip install` finishes without errors, and `/opt/grok-cue/.env` exists.

### 3.3 Settings file

Generate keys only on the server. Write the output directly into `.env`; never copy it into chat.

```bash
openssl rand -hex 32
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

The token and HMAC key must differ. All settings live in one `.env`, without quotes, containing only these names: the nine the app reads plus the docs-only `PUBLIC_BASE_URL`.

```dotenv
CUE_BRIDGE_TOKEN=replace-with-bearer-token
CUE_BRIDGE_HMAC_KEY=replace-with-hmac-key
CUE_BRIDGE_TOKEN_SECONDARY=
CUE_BRIDGE_HMAC_KEY_SECONDARY=
REPLY_TO_ALLOWLIST=cue@example.com
REPLY_TO_DEFAULT=cue@example.com
GROK_WEBHOOK_URL=
GROK_WEBHOOK_KEY=
GROK_WEBHOOK_KEY_HEADER=
PUBLIC_BASE_URL=https://hook.example.com
```

| Variable | Required | Read by | Meaning | Example |
| --- | --- | --- | --- | --- |
| `CUE_BRIDGE_TOKEN` | yes | app | Value Cue puts in `Authorization: Bearer`. Process will not start if empty | `replace-with-bearer-token` |
| `CUE_BRIDGE_HMAC_KEY` | yes | app | HMAC-SHA256 key, different from the token. Process will not start if empty | `replace-with-hmac-key` |
| `CUE_BRIDGE_TOKEN_SECONDARY` | no | app | Second Bearer during rotation. Empty: primary only | empty, or a new token |
| `CUE_BRIDGE_HMAC_KEY_SECONDARY` | no | app | Second HMAC key during rotation. Empty: primary only | empty, or a new key |
| `REPLY_TO_ALLOWLIST` | optional in code, needed in practice | app | Comma-separated emails, case-insensitive. Empty rejects every request with `400` | `cue@example.com` |
| `REPLY_TO_DEFAULT` | optional in code, needed in practice | app | Used when `reply_to` is omitted or blank. Must be on the allowlist; otherwise `400` | `cue@example.com` |
| `GROK_WEBHOOK_URL` | no | app | Grok Bot routine's receiving address. Empty: no forwarding | empty, or `https://grok.example/hook` |
| `GROK_WEBHOOK_KEY` | no | app | Key sent to that address. Both URL and key needed to forward | empty, or a separate key |
| `GROK_WEBHOOK_KEY_HEADER` | no | app | Empty: `Authorization: Bearer <GROK_WEBHOOK_KEY>`. Set: bare key in that header | empty, or `X-Grok-Key` |
| `PUBLIC_BASE_URL` | check script | docs only | Address the check script calls. App ignores it. Script stops if empty | `https://hook.example.com` |

`replace-with-` values are placeholders; replace them with values generated on the server. Replace `REPLY_TO_*` with the user's addresses. `PUBLIC_BASE_URL` is the user's public address (`http://127.0.0.1:8160` for local-only checks). Leave the Grok URL and key empty until the user approves forwarding.

```bash
chmod 600 /opt/grok-cue/.env
sudo chown bridge:bridge /opt/grok-cue/.env
stat -c '%U:%G %a' /opt/grok-cue/.env
sudo -u bridge test -r /opt/grok-cue/.env && echo ok
```

Success: `stat` prints `bridge:bridge 600` and the last command prints `ok`. `bridge` is the same user as the systemd unit's `User=`/`Group=`; if the service account does not exist, create it after approval with `sudo useradd --system --no-create-home --shell /usr/sbin/nologin bridge`; the file contains only the names above; no key values appear in the chat history.

### 3.4 Tests and process

The working directory must be the install folder; the app reads `.env` from there.

```bash
cd /opt/grok-cue
source .venv/bin/activate
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8160
```

Success: pytest finishes without failures. The following returns `200` with body `ok`. Never load `.env` into a shell.

```bash
curl -sS -D - http://127.0.0.1:8160/healthz
```

To keep it running, after approval place the example unit from [SETUP.en.md](SETUP.en.md) at `/etc/systemd/system/grok-cue.service`. That block is a documentation example; do not add a unit file to the repository. Before starting the service, pass the ownership checks in that guide. The install directory and `.venv` stay owned by the installing account. Only `.env` is `bridge:bridge` with `chmod 600`. `bridge` must be able to read and execute the app code and the venv. The checks are `sudo -u bridge test -r`, `sudo -u bridge test -x`, `stat`, and `ls -l`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grok-cue.service
sudo systemctl status grok-cue.service
```

Success: `systemctl is-active grok-cue.service` prints `active`.

### 3.5 HTTPS

After approval, add this to the Caddyfile. The host name is the user's value.

```caddyfile
hook.example.com {
    request_body {
        max_size 64KiB
    }
    reverse_proxy 127.0.0.1:8160
}
```

Open only 80 and 443; never 8160. Reload Caddy.

Success: the following, using the user's public host (example `hook.example.com`), returns `200` with body `ok` and no certificate error.

```bash
curl -sS -D - https://hook.example.com/healthz
```

### 3.6 Signature check

> [!WARNING]
> Run this only **before** forwarding is enabled. With `GROK_WEBHOOK_URL`/`KEY` set, Grok delivery of the test handoff is scheduled with `add_task` and a background POST is sent. That `202` is not confirmation of a Grok 2xx response.

Do this after the allowlist and default address are set in `.env`. Use the repository's `scripts/check_bridge.py` (described in [SETUP.en.md](SETUP.en.md) section 6). A `KEY=VALUE` parser inside the script reads `.env`, so never load it into a shell. The default is a dry run that sends nothing and prints a summary. The real check is `--send`; run it only after user approval and only while forwarding is disabled. It stops if `PUBLIC_BASE_URL`, `CUE_BRIDGE_TOKEN`, or `CUE_BRIDGE_HMAC_KEY` is missing.

```bash
cd /opt/grok-cue
sudo -u bridge .venv/bin/python scripts/check_bridge.py            # dry-run
sudo -u bridge .venv/bin/python scripts/check_bridge.py --send     # only before forwarding is enabled
```

When both `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set, also confirm the first JSON has `forwarded: true`. If either is empty, `false` is correct for the first one too. The script therefore asserts `forwarded: false` only for the second request.

Success criteria:

| Request | Code | Look for in body |
| --- | --- | --- |
| `GET /healthz` | `200` | `ok` |
| Unsigned `POST /v1/cue/tasks` | `401` | `{"error":"unauthorized"}` |
| First signed POST | `202` | `status`=`received`, `executed`=`false` |
| Same body, new timestamp and HMAC | `202` | Same `handoff_id`, `forwarded`=`false` |
| Same `idempotency_key`, different body | `409` | `{"error":"idempotency_conflict"}` |

The first `202` has `forwarded: true` only when both `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set. With only one, `forwarded` is `false`. HTTP `202` means the bridge received and validated the request and recorded it in memory. It does not mean Grok received it, and it does not mean any task ran. `forwarded: true` only means Grok delivery was scheduled as an `add_task` background task. It is not confirmation of a Grok 2xx response, task completion, or success of the Grok Bot routine.

Resending the exact same signature returns `401`; do not treat that as a `202` replay.

The script generates a fresh test `handoff_id` and `idempotency_key` on every run, with `title` `[TEST] connectivity check` and `summary` `connectivity test — not a real task`. Real requests use a UUID the user generated.

## 4. When something fails

| Failure | Action |
| --- | --- |
| Required input missing | Ask only for that item and stop. Do not invent a domain or IP |
| `dig` empty or a different address | Report the DNS name and value to the user and get approval to fix. Do not proceed to HTTPS before propagation |
| `pip` or `pytest` fails | Do not start the service or proxy. Summarize the last failure line; omit any secrets in the output |
| Process exits immediately | `CUE_BRIDGE_TOKEN`/`CUE_BRIDGE_HMAC_KEY` empty, or an allowlist entry/`REPLY_TO_DEFAULT` is not a single email. Check names only; never print values |
| Local `/healthz` is `200` but domain fails | Check Caddy name, `reverse_proxy 127.0.0.1:8160`, 80/443, certificate, in that order. Never work around by exposing 8160 |
| Certificate error | Re-run `dig` to confirm DNS points here. Confirm Caddy is running |
| Signed POST returns `401` | Clock within 300 s, both Bearer and HMAC present, no `sha256=`, signature not reused. Never print key values |
| `400` `reply_to_not_allowed` | Allowlist empty, address off-list, or default not on the list. Fix `.env` and restart |
| `400` `invalid_request` | Match `fields` names against [DESIGN.en.md](DESIGN.en.md). Body ≤ 64 KiB, `Content-Type: application/json` |
| `400` `task_id_not_allowed` | Remove the `task_id` key; keep only `handoff_id` |
| `409` | Resend the original bytes. Reordered JSON keys are a different body |
| `202` but `forwarded` is `false` | Check both URL and key are set. If either is empty this is expected. Replays are also `false` |
| `202` but nothing at Grok Bot | `forwarded: true` only means delivery was scheduled with `add_task`. It is not confirmation of a Grok 2xx response. Check URL, header name, and the 5 s limit with the user. Do not turn the bridge's `202` into a failure |
| Repository, branch, or SHA differ between email, issue, and webhook | Do not send a success webhook. Cue stops and reports `status: "needs_input"` |
| A key was already posted in chat | Do not repeat it. Tell the user to revoke it, generate a new key on the server, and update only `.env` |
| A request to merge, deploy, or pay | Do not treat it as handled by the `202`. Proceed only with separate approval |

To rotate keys, put new values in `CUE_BRIDGE_TOKEN_SECONDARY` and `CUE_BRIDGE_HMAC_KEY_SECONDARY` and restart. Both old and new values are accepted meanwhile. Once Cue uses only the new values, move them to primary, clear secondary, and restart. These restarts also need user approval.
