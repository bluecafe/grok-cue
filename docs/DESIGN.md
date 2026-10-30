한국어 | [English](DESIGN.en.md)

# 요청 본문 설명

이 글은 `POST /v1/cue/tasks`가 받는 JSON을 쉬운 말로 적습니다. 서버가 하는 일과 사람이 맞추는 약속을 구분합니다. 준비, 인증, 응답 코드는 [README.md](../README.md)에 있습니다.

## 스펙

이 문서가 요청 필드의 유일한 스펙입니다. 코드(`app/models.py`)는 이 스펙과 정확히 같아야 하며, `tests/test_spec.py`가 이 절의 목록과 모델 필드를 비교합니다.

1. **본문 크기 제한:** 65536바이트(64 KiB). 인증보다 먼저 검사합니다. `Content-Length`가 이보다 크거나 숫자가 아니면 바로 거절하고, 길이가 없는 chunked 본문도 이 크기를 넘는 순간 읽기를 멈춥니다. 응답은 `400` `{"error":"invalid_request","fields":["body"]}`입니다. 이 값은 프로젝트에서 정한 것이며, 나중에 설정으로 바꿀 수 있게 할 수 있습니다.
2. **받는 필드:** 아래 15개뿐입니다. 이 밖의 키는 `400 invalid_request`입니다.
   - 요청 필드: `schema_version`, `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `idempotency_key`, `title`, `status`, `summary`, `created_at`, `links`, `next_owner`, `reply_to`, `requested_by`
   - `github_ref` 필드: `repo`, `base_branch`, `start_sha`, `paths`, `issue_or_pr_url`, `issue_or_pr_number`
3. **전달 규칙:** 접수된 15개 중 아래 10개만 이 순서로 Grok에 전달합니다. `schema_version`, `title`, `created_at`, `reply_to`, `requested_by`는 전달하지 않습니다. `github_ref`의 값이 없는 선택 항목은 빠집니다. `executed`, `forwarded`는 응답 전용이라 보내지 않습니다.
   - 전달 필드: `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`

## 서버가 하는 일

서명이 맞는 JSON을 접수합니다. 접수 응답은 `202`이고 `status`는 `received`입니다. `executed`는 항상 `false`입니다. 이 응답은 실행, 머지, 배포, 결제 승인이 아닙니다.

`GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`가 둘 다 있으면, 접수 커밋 뒤 정해진 10개 필드만 Grok에 넘깁니다([스펙](#스펙) 3번). 메일은 보내지 않습니다. 한 시도의 제한 시간은 5초이고, 재시도는 첫 시도를 포함해 최대 7회, 첫 시도부터 60분 안입니다. 전달이 실패해도 이미 돌려준 `202`는 바뀌지 않습니다. 이미 본 세대보다 이전 `(attempt, report_seq)`는 저장만 하고 넘기지 않습니다.

`GROK_WEBHOOK_KEY_HEADER`가 없으면 `Authorization: Bearer <GROK_WEBHOOK_KEY>`를 보냅니다. 헤더 이름을 적으면 그 헤더의 값으로 키만 보냅니다.

`forwarded`가 `true`이면 그 아웃박스 행이 Grok 2xx를 기록한 것입니다. 첫 `202`는 그 전에 나가므로 `forwarded`는 `false`입니다. 설정이 없으면 `forward_state`는 `disabled`입니다. 같은 본문을 다시 보내도 전달은 새로 하지 않고, `forwarded`는 그때의 아웃박스 상태입니다.

2026-10-30 23:00 KST에 0.2.0을 배포한 뒤 확인했습니다. v1과 버전 헤더가 없는 더미 요청은 둘 다 `401`(`unknown_signature_version`)이었고 아웃박스에는 쌓이지 않았습니다. v2 더미 요청은 `202`, `delivered`, `signature_version=2`였습니다. `healthz`는 `200`이었습니다.

2026-10-30 23:53 KST에 0.2.1 릴리스 `app-9875680`을 배포했습니다. 롤백 대상은 `app-5e34836`이고, 백업 이름은 `state.db.bak-5e34836`입니다. `healthz`는 `200`이었습니다. `/docs`, `/redoc`, `/openapi.json`은 `404`였습니다. 서명 없는 POST는 `401`이었습니다. `Content-Security-Policy`는 `default-src 'none'; frame-ancestors 'none'`이었습니다. v1은 `401`(`unknown_signature_version`)이었습니다. v2는 `202`, `delivered`, `signature_version=2`였습니다. 보관 기간은 환경 변수를 바꾸지 않은 기본 30일입니다.

`/docs`, `/redoc`, `/openapi.json`은 `404`입니다. 모든 응답에는 `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`이 있고, `X-Content-Type-Options: nosniff`가 없으면 붙입니다. `CUE_SUMMARY_RETENTION_DAYS`(기본 30, 유한한 양수. `nan`, `inf`, 0, 음수는 시작할 때 거절)가 지나면 `delivered`와 `dead` 행의 `summary`를 지웁니다. `queued`와 `sending`은 지우지 않습니다. 서명 재생 행은 서명 유효 시간(600초)이 지나면 삭제합니다.

본문에 없는 키는 거절합니다. `diff`, `patch`, `source`, 파일 본문, 비밀을 넣는 칸은 없습니다. 본문 상한은 64 KiB(65536바이트)입니다. 이 크기는 인증보다 먼저 검사하며, 넘으면 `400 invalid_request`(`fields`는 `body`)입니다.

## 사람이 맞추는 약속

작업 메일, Issue 또는 PR 본문, 웹훅 `github_ref`의 저장소·브랜치·SHA는 같아야 합니다. 하나라도 다르면 작업을 멈추고, 웹훅 `status`를 `needs_input`으로 보냅니다. 메일을 보내기 전에 Issue 본문의 SHA를 메일과 같게 고칩니다.

이 서버는 메일과 Issue를 읽지 않습니다. 서버가 확인하는 것은 받은 JSON뿐입니다. 최상위 `repo`와 `github_ref.repo`가 다르면 `400`이고, `fields`는 `repo`입니다.

메일과 웹훅에는 소스 전체, diff, 비밀, 큰 파일을 넣지 않습니다.

## 작업 상태

요청의 `status`는 작업 상태입니다. 응답의 `received`가 아닙니다. 값은 이 넷만입니다.

| 값 | 뜻 |
| --- | --- |
| `in_progress` | 작업을 진행 중이다 |
| `needs_input` | 다음 입력이 필요하다. 승인으로 쓰지 않는다 |
| `completed` | 지정된 위치에 결과를 올렸다 |
| `failed` | 작업을 끝내지 못했다 |

## github_ref

`repo`는 `owner/name` 형식입니다. 예시는 `owner/name`입니다. 각 조각은 영문, 숫자, `_`, `.`, `-`이고 슬래시는 하나이며 대소문자를 구분합니다.

필수 키는 `repo`, `base_branch`, `start_sha`입니다. `paths`, `issue_or_pr_url`, `issue_or_pr_number`는 선택입니다. 생략과 JSON `null`은 같습니다.

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

- `repo`: 최상위 `repo`와 같은 문자열입니다.
- `base_branch`: 1–255자. 공백이 없습니다.
- `start_sha`: 16진수 40자 또는 64자. 짧은 SHA는 거절합니다. 대소문자 hex는 받습니다.
- `paths`: 없거나 `null`이면 경로가 없는 것입니다. 배열이면 빈 배열도 됩니다. 최대 50개. 각 경로는 1–200자이고, 앞에 `/`가 없으며, `\`와 줄바꿈과 `..` 조각이 없습니다.
- `issue_or_pr_url`: `https://github.com/{repo}/issues/{n}` 또는 `https://github.com/{repo}/pull/{n}` 입니다. `{repo}`는 이 객체의 `repo`와 같습니다. 저장소가 다르면 `400`, `fields`는 `github_ref.issue_or_pr_url`입니다.
- `issue_or_pr_number`: JSON 정수 `1`–`2000000000`입니다. 문자열은 받지 않습니다. URL도 있으면 URL의 마지막 번호와 같아야 합니다. 다르면 `400`, `fields`는 `github_ref.issue_or_pr_number`입니다.

Issue 본문에는 목표, 완료 조건, 실행할 테스트, `handoff_id`, `repo`, `github_ref`를 적습니다. 빈 칸은 `.github/ISSUE_TEMPLATE/handoff.md`에 있습니다.

## 요청 필드

이 표가 본문의 전부입니다. 표에 없는 키는 `400 invalid_request`입니다.

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `schema_version` | 예 | 문자열 `"1"` |
| `handoff_id` | 예 | 보낸 쪽이 만든 UUID. 응답에 같은 값을 돌려줍니다 |
| `attempt` | 예 | JSON 정수 `1`–`9223372036854775807`. 같은 `handoff_id`의 재실행마다 증가합니다. 불리언·소수·문자열은 거절합니다 |
| `report_seq` | 예 | JSON 정수 `1`–`9223372036854775807`. 같은 `attempt` 안에서 보고마다 증가하고, `attempt`가 바뀌면 1부터입니다 |
| `repo` | 예 | `owner/name`. `github_ref.repo`와 같아야 합니다 |
| `github_ref` | 예 | 위 객체 |
| `idempotency_key` | 예 | 1–200자. 공백과 제어 문자가 없습니다. 본문 안에만 둡니다 |
| `title` | 예 | 1–200자. 줄바꿈이 없고, 공백만이면 거절합니다 |
| `status` | 예 | `in_progress`, `needs_input`, `completed`, `failed` |
| `summary` | 예 | 1–4000자. 짧은 요약입니다. 공백만이면 거절합니다 |
| `created_at` | 예 | 시간대가 있는 ISO-8601 |
| `links` | 예 | 링크 배열. 없으면 `[]`. `null`과 생략은 거절합니다. 최대 20개. 각 값은 500자 이하 `http` 또는 `https` URL이고 공백이 없습니다 |
| `next_owner` | 예 | 다음 담당 표시. 1–200자. 줄바꿈이 없고, 공백만이면 거절합니다 |
| `reply_to` | 아니오 | 회신 주소 하나. 규칙은 아래 |
| `requested_by` | 아니오 | 요청자 표시. 생략 또는 `null`. 있으면 1–200자, 줄바꿈 없음, 공백만이면 거절 |

`task_id` 키가 어디에 있든 `400` `{"error":"task_id_not_allowed"}`입니다. 오류 본문에 요청 요약이나 비밀을 다시 넣지 않습니다.

## reply_to

수신 주소는 환경 변수로만 정합니다. 코드에 박힌 주소는 없습니다.

- `REPLY_TO_ALLOWLIST`: 쉼표로 구분합니다. 대소문자는 무시하고, 주소 하나만 정확히 일치해야 합니다. 비우면 목록은 비고, 모든 요청이 `400`입니다(코드상 선택이지만 실제로는 필요).
- `REPLY_TO_DEFAULT`: `reply_to`를 생략했거나, `null`이거나, 빈 문자열이나 공백만 있을 때 씁니다. 이 값이 비어 있거나 허용 목록에 없으면 `400`입니다.
- 거절: 목록 밖, 수신자 여러 개, 이메일이 아닌 값, `mailto:`로 시작.
- 응답: `400` `{"error":"reply_to_not_allowed"}`. 허용 목록과 거절된 주소는 돌려주지 않습니다.
- 이 검사는 멱등 저장보다 먼저입니다. 거절된 요청은 키를 차지하지 않습니다.

## 접수 응답

```json
{"handoff_id":"<uuid>","status":"received","executed":false,"forwarded":false,"forward_state":"queued"}
```

같은 키와 같은 본문이면 `handoff_id`, `status`, `executed`는 첫 접수와 같습니다. `forwarded`와 `forward_state`는 그때의 아웃박스 상태입니다. 로그에는 토큰, HMAC 키, Grok 키, 서명, 본문, 요약을 넣지 않습니다. 검증을 통과해 접수되면 `signature_version=2`만 로그와 접수 행에 남습니다. `X-Cue-Signature-Version: 2`가 없으면 `401`입니다.

순서는 `(attempt, report_seq)` 사전순입니다. 이미 저장한 세대보다 이전이면 그 보고는 저장만 하고, 현재 세대는 바꾸지 않으며 Grok으로 넘기지 않습니다. 같은 `(handoff_id, attempt, report_seq)`에 다른 본문 바이트면 `409` `{"error":"sequence_conflict"}`이고 처음 접수는 남습니다. 같은 `idempotency_key`에 다른 본문은 `409` `{"error":"idempotency_conflict"}`입니다.
