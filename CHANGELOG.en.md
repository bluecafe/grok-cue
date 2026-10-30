[한국어](CHANGELOG.md) | English

# Changelog

The current version is `0.0.4`. The value lives only in [`VERSION`](VERSION) at the repository root. The app does not read that file, and no response, including `/healthz`, includes the version.

## Version rules

Pre-1.0, and bumped in a SemVer-like way.

- Documentation changes and bug fixes are PATCH. Example: `0.0.1` → `0.0.2`.
- New features are MINOR. Example: `0.0.1` → `0.1.0`.
- Compatibility breaks (signature or field changes) are also a MINOR bump, and that entry is marked BREAKING.
- A version bump happens inside the pull request being merged. That pull request updates `VERSION` and this changelog together.
- The git tag `v0.0.1` is created only after merge, and only with user approval. The same applies to any other `vX.Y.Z` tag.

`0.0.1` is the features and docs at `2483c3b`. Later documentation corrections stay under Unreleased until a version bump.

## Unreleased

- Section 2.1 (b): push and pull requests are confirmed; merge and settings changes are target, current gap, and proposal. Section 12.1: non-breaking steps before 0.1.0 are 0.0.x PATCH; 0.1.0 is only for P8 and P13.
- Section 2.1 separates what the repository owner account can technically do from the actual scope of the credential Cue uses. Unverified credential scopes are marked unverified. An external review PR is a record: merge the incorporation PR first, then the operator merges the review PR and comments with that link, and nobody on our side commits to the Cue branch. A `needs_input` handoff is closed as `completed` when the owner sends the reply email drafted by the planner.
- Added the abnormal-handling analysis and design document at `docs/ABNORMAL_HANDLING.md`. Items written as proposals are not implemented.
- The agent setup docs (`docs/AI_SETUP.md`, `docs/AI_SETUP.en.md`) now state what HTTP `202` and `forwarded: true` mean. `202` means the bridge received and validated the request and recorded it in memory. It does not mean Grok received it, and it does not mean any task ran. `forwarded: true` means Grok delivery was scheduled as an `add_task` background task. It is not confirmation of a Grok 2xx response. The docs state that retries, an outbox, and persistent storage are not implemented.
- The 2026-10-29 E2E is one successful request record. Grok-side receipt was confirmed separately by Norn (user) at 16:05 KST.
- In docs and READMEs the owner is 노른(사용자) in Korean and Norn (user) in English. There was no existing personal-name wording to replace.
- Versioning was added only in `VERSION`, this changelog, and one README line. Application code was not changed, and no tag was created.

## 0.0.4 — 2026-10-30

PATCH. `0.1.0` is reserved for P8 and P13. The first `202` now has `forwarded: false` until a Grok 2xx. Retries are at most 7 attempts including the first, inside 60 minutes from the first attempt. Any other 4xx is `dead` immediately.

### Features

- The outbox and `forward_state` share the SQLite file used for acceptance and replay. The file mode is `0600`. If the database cannot be opened, the log is `state_db=unavailable` with no path and no body, and the response is `503`.
- `forwarded: true` only after a Grok 2xx. Sending the same body again does not start another delivery. Only timeouts, connection errors, 408, 425, 429, and 5xx are retried.
- Retries and restart recovery run on an in-process worker. It pumps once at startup, then every `CUE_OUTBOX_POLL_SECONDS` (default 5) using the wall clock, and it is cancelled on shutdown. If one pump raises, the worker keeps going and the log records only the exception class name. Rows left in `sending` and rows that are due are recovered before they are sent. A row is claimed atomically before send so the same row is not sent twice.

## 0.0.3 — 2026-10-30

PATCH. `0.1.0` is reserved for P8 HMAC v2 and the P13 sequence fields. The request fields, the signature, and the meaning of `202` are unchanged. There are no retries and no outbox.

### Features

- Acceptance records and replay prevention are stored in SQLite. The path is the `CUE_STATE_DB` environment variable. When it is empty, records stay in process memory. There is no built-in path. After a restart the same body returns the stored `202`, a different body is `409`, and the same signature is `401`. If the database cannot be written, the response is `503 storage_unavailable` and no `202` is returned. The raw signature is not stored; only its SHA-256 is.

## 0.0.2 — 2026-10-30

PATCH. `0.1.0` is reserved for the BREAKING release with P8 HMAC v2 and the P13 sequence fields. The request fields, the signature, and the meaning of the `202` response are unchanged. There are no retries and no outbox.

### Features

- Grok forward results are classified. 2xx is `delivered`. Timeout, connection errors, 408, 425, 429, and 5xx are `retryable`. Any other HTTP status is `dead`. An unexpected exception is also `dead`: it is not a timeout or a connection error, so it is not a retry candidate, and retrying an unknown failure could repeat a partial side effect. The log keeps the class and status code only, not the webhook key or the body. `forwarded` still means delivery was scheduled, not that a 2xx was confirmed.
- Auth-failure logs contain only a reason code. Tokens, HMAC keys, signatures, request bodies, and summaries are not logged.

## 0.0.1

### Features

- `POST /v1/cue/tasks` accepts a signed handoff. Both Bearer and HMAC-SHA256 must match, within ±300 seconds. A signature can be used once.
- During rotation the primary and secondary values are both accepted.
- `idempotency_key` returns `202` again for the same body and `409` for a different body. That record is process memory only. There are no retries, no outbox, and no persistent storage.
- The body is at most 64 KiB and `application/json`. `task_id` is rejected. `reply_to` must be on the allowlist.
- When both `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set, only the first acceptance of an idempotency key schedules the 8 forward fields to Grok with `add_task`. The timeout is 5 seconds. `executed` is always `false`.
- The bridge does not send mail, and it does not execute tasks, merge, deploy, or pay. Secrets and request bodies are not logged. `GET /healthz` only checks that the process responds.

### Docs

- Human setup: `docs/SETUP.md`, `docs/SETUP.en.md`
- Agent setup: `docs/AI_SETUP.md`, `docs/AI_SETUP.en.md`
- Field and forward spec: `docs/DESIGN.md`, `docs/DESIGN.en.md`
- Introduction and response table: `README.md`, `README.en.md`
- Reference sender: `scripts/check_bridge.py`
