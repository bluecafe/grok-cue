한국어 | [English](README.en.md)

# grok-cue

현재 버전: `0.2.0` ([VERSION](VERSION), [변경 기록](CHANGELOG.md))

grok-cue는 두 AI 비서 서비스 사이에 놓이는 작은 "접수 창구" 서버입니다. 하나는 Manus가 만든 개인용 에이전트 앱 [Cue](https://cue.im/)이고, 다른 하나는 클라우드 컴퓨터에서 일하는 xAI의 에이전트 [Grok Bot](https://docs.x.ai/grok-bot/overview)입니다.
두 비서는 끝낸 일을 서로에게 직접 넘겨줄 방법이 없습니다. grok-cue는 Cue 쪽이 서명해서 보낸 "작업 완료" 알림을 HTTP로 받고, 설정해 두었다면 Grok Bot 웹훅으로 접수한 내용을 JSON으로 넘깁니다.

> 비공식 프로젝트입니다. Cue(Manus) 및 xAI의 Grok Bot과 제휴하거나 그들로부터 보증받지 않았습니다.

> [!WARNING]
> grok-cue는 개발 미리보기 버전이며 **받기만 합니다**. HTTP `202`는 "받았다"는 뜻일 뿐입니다.
> 작업 실행, 머지, 배포, 결제, 메일 발송은 절대 하지 않습니다. 응답의 `executed`는 항상 `false`입니다.
> 인터넷에 공개하기 전에 [보안](#보안)을 꼭 읽어 주세요.

설치 안내: [docs/SETUP.md](docs/SETUP.md)(사람용), [docs/AI_SETUP.md](docs/AI_SETUP.md)(에이전트용)

## 되는 것

- `POST /v1/cue/tasks`로 작업 인계 알림을 받습니다. Bearer 토큰과 HMAC-SHA256 서명이 **둘 다** 맞아야 하고, 허용 시각 차이는 ±300초입니다. `X-Cue-Signature-Version: 2`가 필요합니다. 서명 대상은 `v2`, 메서드, 경로, 시각, 본문을 줄바꿈으로 이은 값입니다. 헤더가 없거나 `1`이면 `401`입니다.
- 인증에 성공한 서명은 한 번만 쓸 수 있습니다. 같은 서명을 다시 보내면 `401`입니다.
- 키를 바꾸는 동안 서비스를 멈추지 않도록 기본 토큰·키와 보조 토큰·키를 함께 받습니다.
- `idempotency_key`로 중복을 막습니다. 같은 바이트면 처음의 `202`를 그대로 돌려주고, 다른 바이트면 `409`입니다.
- 본문을 엄격하게 검사합니다(64 KiB 제한, `application/json`, `github_ref` 형식, 어디에도 `task_id` 금지).
- `reply_to`는 환경 변수로 정한 메일 주소 허용 목록 안에서만 받습니다.
- 선택적으로, 정해진 10개 필드(`handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`)([스펙](docs/DESIGN.md#스펙))만 `GROK_WEBHOOK_URL`로 전달합니다. 한 시도의 HTTP 제한 시간은 5초입니다. 재시도는 첫 시도를 포함해 최대 7회, 첫 시도 시작부터 60분 안입니다.
- 토큰, 키, 서명 같은 비밀과 요청 본문은 로그에 남기지 않습니다. 검증을 통과해 접수되면 로그와 접수 행에는 `signature_version=2`만 남습니다. 응답 본문에는 버전을 넣지 않습니다.
- `GET /healthz`로 서버가 켜져 있는지 확인할 수 있습니다.

`CUE_STATE_DB`가 있으면 접수, 서명 재생 방지, 아웃박스는 그 SQLite 파일에 남습니다. 비어 있으면 프로세스 메모리뿐이라 재시작하면 사라집니다.

## 동작 방식

```
 Cue 쪽 ──서명된 POST──▶ grok-cue ──접수 JSON(선택)──▶ Grok Bot 웹훅
                            │
                            └─ 토큰·HMAC·시각 확인 → 본문 검사 → 202 / 4xx
```

전달은 `GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`가 둘 다 설정되어 있을 때만, 그리고 같은 멱등 키의 첫 접수에서만 일어납니다. `202`는 그 접수를 커밋한 뒤에 나갑니다. 첫 `202`의 `forwarded`는 `false`입니다. `forwarded: true`는 그 행이 Grok 2xx를 기록한 뒤에만입니다. 전달이 실패해도 이미 돌려준 `202`는 바뀌지 않고, 워커가 한도 안에서 다시 시도합니다.

## 설치

설치 전에 아래가 준비되어 있어야 합니다. 오른쪽 명령으로 확인합니다.

| 준비물 | 확인 명령 | 기대 결과 |
| --- | --- | --- |
| Git | `git --version` | 버전이 출력됨 |
| Python 3.11 이상(`pyproject.toml`의 `requires-python = ">=3.11"`) | `python3 --version` | `Python 3.11` 이상 |
| Caddy(서버에서 HTTPS용) | `caddy version` | 버전이 출력됨 |
| 서비스 계정 `bridge`(서버) | `id bridge` | `uid=…(bridge)` |

서비스 계정이 없으면 서버에서 만듭니다.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin bridge
id bridge
```

### 내 컴퓨터에서 시험

```bash
git clone <저장소 주소> grok-cue
cd grok-cue
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env    # 그다음 replace-with-... 값을 모두 바꿉니다
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8160
curl http://127.0.0.1:8160/healthz   # → ok
```

비밀값은 `openssl rand -hex 32`로 만듭니다. 토큰과 HMAC 키에는 서로 다른 값을 넣습니다.
`.env`는 절대 커밋하지 않습니다.

### 서버

[docs/SETUP.md](docs/SETUP.md)에 `/opt/grok-cue` 설치, systemd 유닛 `grok-cue.service`, 앞단 TLS 역방향 프록시(Caddy 등, 예시 호스트 `hook.example.com`) 설정이 있습니다.
uvicorn은 `127.0.0.1`에만 묶어 둡니다. 프로세스는 하나만 띄웁니다. `uvicorn --workers`나 여러 서비스 인스턴스는 쓰지 않습니다. 아웃박스 워커는 그 프로세스 안에 하나뿐입니다.

### Cue 쪽 설정 (미검증)

Cue가 Bearer와 HMAC 서명 헤더를 붙인 POST를 직접 보낼 수 있는지는 Cue 공개 자료에서 **확인하지 못했습니다**. 보내는 쪽이 지켜야 할 일반 요구 사항은 [SETUP.md의 Cue 쪽 설정](docs/SETUP.md#cue-쪽-설정-미검증)에 있고, 참고용 송신기는 [`scripts/check_bridge.py`](scripts/check_bridge.py)입니다.

## 설정

모든 설정은 `.env` 파일 하나(또는 같은 이름의 환경 변수)에 둡니다. 코드에 박아 둔 값은 없습니다.
바꾼 뒤에는 프로세스를 다시 시작합니다.

| 변수 | 필수 | 용도 |
| --- | --- | --- |
| `CUE_BRIDGE_TOKEN` | 예 | 들어오는 요청의 `Authorization: Bearer` 값 |
| `CUE_BRIDGE_HMAC_KEY` | 예 | HMAC-SHA256 키. 토큰과 다른 값이어야 함 |
| `CUE_BRIDGE_TOKEN_SECONDARY` | 아니오 | 키 교체 중 함께 받는 두 번째 토큰 |
| `CUE_BRIDGE_HMAC_KEY_SECONDARY` | 아니오 | 키 교체 중 함께 받는 두 번째 HMAC 키 |
| `REPLY_TO_ALLOWLIST` | 코드상 선택, **실제로는 필요** | 쉼표로 구분한 허용 메일 주소. 비우면 모든 POST가 `reply_to_not_allowed`로 거절됨 |
| `REPLY_TO_DEFAULT` | 코드상 선택, **실제로는 필요** | `reply_to`를 생략하거나 비웠을 때 쓰는 주소. 허용 목록 안에 있어야 함 |
| `GROK_WEBHOOK_URL` | 아니오 | 전달할 주소. 비우면 전달하지 않음 |
| `GROK_WEBHOOK_KEY` | 아니오 | 전달 주소에 보내는 키 |
| `GROK_WEBHOOK_KEY_HEADER` | 아니오 | 비우면 `Authorization: Bearer <key>`. 헤더 이름을 적으면 그 헤더에 키 값만 보냄 |
| `CUE_STATE_DB` | 아니오 | 접수·서명 재생·아웃박스 SQLite 경로. 비우면 프로세스 메모리만 씀. 기본 경로는 없음 |
| `CUE_OUTBOX_POLL_SECONDS` | 아니오 | 이 프로세스의 아웃박스 주기(초). 기본 5. 0 이하는 거절. 시작 때도 한 번 pump |

`PUBLIC_BASE_URL`(예: `https://hook.example.com`)은 문서와 확인 스크립트만 읽고, 앱은 무시합니다.

## API

### 인증

모든 `POST /v1/cue/tasks`에는 다음이 모두 필요합니다.

- `Authorization: Bearer <CUE_BRIDGE_TOKEN>`
- `X-Cue-Timestamp`: Unix 초. 서버 시각과 ±300초 이내
- `X-Cue-Signature`: HMAC-SHA256 hex. hex 대소문자는 구분하지 않고, `sha256=` 접두사는 거절됩니다.
- `X-Cue-Signature-Version: 2`가 필요합니다. 입력은 `v2`, METHOD, PATH, timestamp, raw_body를 줄바꿈으로 이은 값입니다. 예: METHOD `POST`, PATH `/v1/cue/tasks`.
- 헤더가 없거나 `1`이거나 그 밖의 값이면 `401` `{"error":"unauthorized"}`입니다. 응답 본문에 서명이나 비밀을 넣지 않습니다.
- 검증을 통과해 접수되면 로그와 접수 행에 `signature_version=2`만 남깁니다. 서명 문자열은 로그에 넣지 않습니다.

다시 보낼 때는 본문 바이트를 그대로 두고 시각과 서명만 새로 계산합니다.

```bash
# 자리표시자만 사용합니다. 전달을 켜기 "전에"만 실행하세요.
# 전달이 켜져 있으면 이 가짜 작업이 Grok Bot에 실제로 전달됩니다.
TOKEN='replace-with-bearer-token'; KEY='replace-with-hmac-key'
TS="$(date +%s)"
BODY='{"schema_version":"1","handoff_id":"00000000-0000-4000-8000-000000000000","attempt":1,"report_seq":1,"repo":"owner/name","github_ref":{"repo":"owner/name","base_branch":"main","start_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"idempotency_key":"example-key-1","title":"example","status":"completed","summary":"example summary","created_at":"2026-10-29T00:00:00Z","links":[],"next_owner":"example"}'
SIG="$(printf 'v2\nPOST\n/v1/cue/tasks\n%s\n%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$KEY" -hex | awk '{print $NF}')"
curl -sS -D - http://127.0.0.1:8160/v1/cue/tasks \
  -H "Authorization: Bearer $TOKEN" -H "X-Cue-Timestamp: $TS" -H "X-Cue-Signature: $SIG" \
  -H "X-Cue-Signature-Version: 2" \
  -H "Content-Type: application/json" --data-binary "$BODY"
```

필드 설명: [docs/DESIGN.md](docs/DESIGN.md)

### 멱등(중복 방지)

같은 `idempotency_key`와 같은 바이트 → `202`. `handoff_id`는 처음과 같고, 알림과 전달은 다시 하지 않습니다. `forwarded`와 `forward_state`는 그 시점의 아웃박스 상태입니다.
같은 키와 다른 바이트(JSON 키 순서만 바뀐 경우 포함) → `409 idempotency_conflict`. `reply_to` 때문에 거절된 요청은 키를 차지하지 않습니다.
같은 `(handoff_id, attempt, report_seq)`와 다른 바이트 → `409 sequence_conflict`. 이전 세대는 저장만 하고 현재 세대와 전달은 바꾸지 않습니다.

### 응답

| 코드 | 본문 | 언제 |
| --- | --- | --- |
| 200 | `ok` | `GET /healthz` |
| 202 | `handoff_id`, `status: "received"`, `executed: false`, `forwarded`, `forward_state` | 접수 커밋 뒤, 또는 같은 요청의 재전송 |
| 400 | `invalid_request` + `fields` | 크기(64 KiB 초과는 인증보다 먼저 거절), Content-Type, JSON, 스키마 |
| 400 | `task_id_not_allowed` | 본문 어딘가에 `task_id` 키가 있음 |
| 400 | `reply_to_not_allowed` + `allowed` | `reply_to`를 쓸 수 없음 |
| 401 | `unauthorized` | 토큰, 서명, 시각 범위, 서명 재사용, 알 수 없는 서명 버전 |
| 409 | `idempotency_conflict` | 같은 키, 다른 본문 |
| 409 | `sequence_conflict` | 같은 `(handoff_id, attempt, report_seq)`, 다른 본문 |
| 500 | `internal` | 처리하지 못한 오류 |
| 503 | `storage_unavailable` | `POST /v1/cue/tasks`에서 상태 DB를 쓰지 못함. `GET /healthz`는 `ok` |

`forwarded: true`는 그 아웃박스 행이 Grok 2xx를 기록했을 때만입니다. 첫 `202`는 그 전에 나가므로 `forwarded`는 `false`입니다.
`422`는 돌려주지 않습니다.

## 보안

- TLS 뒤에서 운영하고, 앱은 `127.0.0.1`에만 묶습니다.
- `.env`는 권한 `600`으로 두고 버전 관리에 넣지 않습니다. 셸에 `source`하지 마세요. 확인 스크립트는 `.env`를 직접 읽습니다.
- 본문 크기(64 KiB)는 인증보다 먼저 검사합니다. `Content-Length`가 크면 바로 거절하고, 길이가 없는 chunked 요청도 64 KiB를 넘는 순간 읽기를 멈춥니다.
- `401` 응답은 어느 검사에서 실패했는지 알려 주지 않습니다.
- 취약점은 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요. 기여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.

## 의존성

HTTP 전송에는 `httpx`가 아니라 Pydantic이 관리하는 후속 패키지 [`httpx2`](https://github.com/pydantic/httpx2)를 씁니다. 의도한 선택이며, `pyproject.toml`에서 `httpx2==2.16.0`으로 고정하고, `requirements.lock`에 모든 의존성을 sha256 해시와 함께 잠가 둡니다(`pip-compile --generate-hashes --extra dev`).

## 라이선스

MIT 라이선스입니다. 전문은 [LICENSE](LICENSE)에 있습니다. © grok-cue contributors
