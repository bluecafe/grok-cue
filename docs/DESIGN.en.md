[한국어](DESIGN.md) | English

# Request body

This document describes, in plain terms, the JSON accepted by `POST /v1/cue/tasks`. It separates what the server enforces from conventions people agree on. Setup, authentication, and response codes are in [README.en.md](../README.en.md).

## Spec

This document is the single spec for request fields. The code (`app/models.py`) must match it exactly; `tests/test_spec.py` compares the lists in this section with the model fields.

1. **Body size limit:** 65536 bytes (64 KiB), checked before authentication. A `Content-Length` above it (or non-numeric) is rejected immediately, and chunked bodies without a length stop being read once they exceed it. Response: `400` `{"error":"invalid_request","fields":["body"]}`. This value is a project decision and may become configurable later.
2. **Accepted fields:** exactly these 15. Any other key returns `400 invalid_request`.
   - Request fields: `schema_version`, `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `idempotency_key`, `title`, `status`, `summary`, `created_at`, `links`, `next_owner`, `reply_to`, `requested_by`
   - `github_ref` fields: `repo`, `base_branch`, `start_sha`, `paths`, `issue_or_pr_url`, `issue_or_pr_number`
3. **Forwarding rule:** of the 15 accepted fields, only these 10 are forwarded to Grok Bot, in this order. `schema_version`, `title`, `created_at`, `reply_to`, `requested_by` are not forwarded. Optional `github_ref` items without a value are omitted. `executed` and `forwarded` are response-only and not sent.
   - Forwarded fields: `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`

## What the server does

It accepts correctly signed JSON. The response is `202` with `status: "received"`. `executed` is always `false`. This response is not approval to execute, merge, deploy, or pay.

When both `GROK_WEBHOOK_URL` and `GROK_WEBHOOK_KEY` are set, only the 10 fields listed in the spec are forwarded to Grok Bot after the acceptance commit (see [Spec](#spec), item 3). No mail is sent. Each attempt times out at 5 s. Retries are at most 7 attempts including the first, inside 60 minutes from the first attempt. A failed forward does not change the `202` already returned. A report older than the stored `(attempt, report_seq)` is stored and not forwarded.

Without `GROK_WEBHOOK_KEY_HEADER` it sends `Authorization: Bearer <GROK_WEBHOOK_KEY>`; with a header name, the bare key goes in that header.

`forwarded: true` means that outbox row recorded a Grok 2xx. The first `202` goes out before that, so `forwarded` is `false`. Without settings, `forward_state` is `disabled`. Resending the same body does not forward again; `forwarded` is the outbox state at that moment.

After the 0.2.0 deployment at 2026-10-30 23:00 KST, dummy requests with v1 and with no version header both returned `401` (`unknown_signature_version`) and nothing was queued in the outbox. A v2 dummy request returned `202`, was `delivered`, and stored `signature_version=2`. `healthz` returned `200`.

At 2026-10-30 23:53 KST the 0.2.1 release `app-9875680` was deployed. The rollback target is `app-5e34836`, and the backup name is `state.db.bak-5e34836`. `healthz` returned `200`. `/docs`, `/redoc`, and `/openapi.json` returned `404`. An unsigned POST returned `401`. The `Content-Security-Policy` header was `default-src 'none'; frame-ancestors 'none'`. v1 returned `401` (`unknown_signature_version`). v2 returned `202`, was `delivered`, and stored `signature_version=2`. Retention stayed at the default 30 days, with no environment change.

`/docs`, `/redoc`, and `/openapi.json` return `404`. Every response includes `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, and `X-Content-Type-Options: nosniff` when that header is not already set. `CUE_SUMMARY_RETENTION_DAYS` must be a finite positive number (`nan`, `inf`, 0, and negative values are rejected at startup; default 30). After that many days, `summary` is removed from `delivered` and `dead` rows. `queued` and `sending` rows are kept. Replay rows are deleted once they are past the signature window (600 seconds).

Unknown keys are rejected. There are no fields for `diff`, `patch`, `source`, file contents, or secrets. The body limit is 64 KiB (65536 bytes). It is checked before authentication; larger bodies get `400 invalid_request` with `fields` = `body`.

## Conventions people follow

The repository, branch, and SHA in the task email, the issue or PR body, and the webhook `github_ref` must match. If any differ, stop the work and send the webhook with `status: "needs_input"`. Fix the SHA in the issue body to match the email before sending the email.

This server does not read email or issues; it checks only the JSON it receives. If top-level `repo` and `github_ref.repo` differ it returns `400` with `fields` = `repo`.

Never put full source, diffs, secrets, or large files into emails or webhooks.

## Work status

The request `status` is the work status, not the response's `received`. Only four values:

| Value | Meaning |
| --- | --- |
| `in_progress` | Work is in progress |
| `needs_input` | The next input is needed. Not an approval |
| `completed` | Results were posted to the agreed location |
| `failed` | The work could not be finished |

## github_ref

`repo` is `owner/name`. Each part is letters, digits, `_`, `.`, `-`, with exactly one slash; case-sensitive.

Required keys: `repo`, `base_branch`, `start_sha`. Optional: `paths`, `issue_or_pr_url`, `issue_or_pr_number`. Omitted and JSON `null` are equivalent.

```json
{
  "repo": "owner/name",
  "base_branch": "main",
  "start_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "paths": ["optional/paths"],
  "issue_or_pr_url": "https://github.com/owner/name/issues/1",
  "issue_or_pr_number": 1
}
```

- `repo`: the same string as top-level `repo`.
- `base_branch`: 1–255 characters, no whitespace.
- `start_sha`: 40 or 64 hex characters. Short SHAs are rejected. Upper- or lowercase hex is accepted.
- `paths`: missing or `null` means none. An array may be empty. At most 50. Each path 1–200 characters, no leading `/`, no `\`, newlines, or `..` segments.
- `issue_or_pr_url`: `https://github.com/{repo}/issues/{n}` or `https://github.com/{repo}/pull/{n}`, where `{repo}` equals this object's `repo`. Otherwise `400` with `fields` = `github_ref.issue_or_pr_url`.
- `issue_or_pr_number`: JSON integer `1`–`2000000000`; strings are rejected. If a URL is also given, it must equal the URL's final number. Otherwise `400` with `fields` = `github_ref.issue_or_pr_number`.

The issue body should state the goal, done criteria, tests to run, `handoff_id`, `repo`, and `github_ref`. A blank template is at `.github/ISSUE_TEMPLATE/handoff.md`.

## Request fields

This table is the whole body. Any other key returns `400 invalid_request`.

| Field | Required | Description |
| --- | --- | --- |
| `schema_version` | yes | The string `"1"` |
| `handoff_id` | yes | UUID created by the sender; echoed in the response |
| `attempt` | yes | JSON integer `1`–`9223372036854775807`. Increases on each rerun of the same `handoff_id`. Booleans, fractions, and strings are rejected |
| `report_seq` | yes | JSON integer `1`–`9223372036854775807`. Increases on each report inside one `attempt`, and starts again at 1 when `attempt` changes |
| `repo` | yes | `owner/name`; must equal `github_ref.repo` |
| `github_ref` | yes | The object above |
| `idempotency_key` | yes | 1–200 characters, no whitespace or control characters. Body only |
| `title` | yes | 1–200 characters, no newlines, not whitespace-only |
| `status` | yes | `in_progress`, `needs_input`, `completed`, `failed` |
| `summary` | yes | 1–4000 characters, short summary, not whitespace-only |
| `created_at` | yes | ISO-8601 with a time zone |
| `links` | yes | Array of links; `[]` if none. `null` or omitted is rejected. At most 20, each an `http`/`https` URL of ≤ 500 characters without whitespace |
| `next_owner` | yes | Who is next. 1–200 characters, no newlines, not whitespace-only |
| `reply_to` | no | A single reply address; rules below |
| `requested_by` | no | Requester label. Omitted or `null`; if present 1–200 characters, no newlines, not whitespace-only |

A `task_id` key anywhere returns `400` `{"error":"task_id_not_allowed"}`. Error bodies never echo the summary or secrets.

## reply_to

Reply addresses are set only via environment variables. No address is hard-coded.

- `REPLY_TO_ALLOWLIST`: comma-separated, case-insensitive, exact single-address match. Empty means every request is rejected with `400`.
- `REPLY_TO_DEFAULT`: used when `reply_to` is omitted, `null`, empty, or whitespace. If it is empty or not on the allowlist, `400`.
- Rejected: off-list, multiple recipients, non-email values, values starting with `mailto:`.
- Response: `400` `{"error":"reply_to_not_allowed"}`. The allowlist and the rejected address are not returned.
- This check runs before idempotency storage, so rejected requests do not reserve the key.

## Accepted response

```json
{"handoff_id":"<uuid>","status":"received","executed":false,"forwarded":false,"forward_state":"queued"}
```

For the same key and the same body, `handoff_id`, `status`, and `executed` match the first acceptance. `forwarded` and `forward_state` are the outbox state at that moment. Logs never contain tokens, HMAC keys, the Grok key, signatures, bodies, or summaries. A verified acceptance logs and stores only `signature_version=2`. Without `X-Cue-Signature-Version: 2` the response is `401`.

Order is lexicographic on `(attempt, report_seq)`. A report older than the stored generation is recorded only: the current generation stays, and that report is not forwarded. A different body for the same `(handoff_id, attempt, report_seq)` returns `409` `{"error":"sequence_conflict"}` and leaves the first acceptance in place. A different body for the same `idempotency_key` is still `409` `{"error":"idempotency_conflict"}`.
