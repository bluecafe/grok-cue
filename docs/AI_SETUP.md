한국어 | [English](AI_SETUP.en.md)

# 에이전트 설치 절차

이 문서는 Grok 또는 Cue 같은 에이전트가 혼자 읽고 Bridge 설치를 수행하기 위한 절차다. 사람이 읽는 같은 순서는 [SETUP.md](SETUP.md)다. 주소, 메일, 경로, 키는 예시만 적는다. 사용자의 실제 값을 받기 전에는 예시를 그대로 실행하지 않는다.

환경값은 .env 한 곳입니다. 앱이 읽는 이름은 `app/settings.py`의 열두 개입니다. `PUBLIC_BASE_URL`은 문서와 확인 스크립트만 읽고, 앱은 읽지 않습니다. 응답 코드와 헤더 이름은 [README.md](../README.md), 본문 필드는 [DESIGN.md](DESIGN.md)와 같습니다. `/docs`, `/redoc`, `/openapi.json`은 `404`입니다.

## 0. 사용자에게 받을 입력

설치를 시작하기 전에 아래를 묻는다. 없는 항목은 추측하지 않고 멈춘다.

| 입력 | 필수 | 예시 | 비고 |
| --- | --- | --- | --- |
| `PUBLIC_BASE_URL` | 예 | `https://hook.example.com` | `.env`에만 적는다. 확인 스크립트가 이 값을 읽는다. 앱은 읽지 않는다 |
| 서버 공인 IPv4 | 예 | `203.0.113.10` | DNS A 레코드 값. 문서용 자리표시자다 |
| 서버 공인 IPv6 | 아니오 | `2001:db8::10` | 없으면 AAAA를 만들지 않는다 |
| 서버에 접속하는 방법 | 예 | 사용자가 승인한 셸 | 승인 전에는 SSH, 패키지 설치, 서비스 시작을 하지 않는다 |
| 설치 폴더 | 아니오 | `/opt/grok-cue` | 비우면 이 예시를 쓰고, 쓰기 전에 사용자에게 확인한다 |
| 내부 수신 주소 | 아니오 | `127.0.0.1:8160` | 인터넷에 열지 않는다 |
| `REPLY_TO_ALLOWLIST` | 예 | `cue@example.com` | 쉼표로 구분한 메일. 비우면 모든 요청이 `400`이다 |
| `REPLY_TO_DEFAULT` | 예 | `cue@example.com` | 생략·빈 `reply_to`에 쓴다. 허용 목록에 있어야 한다 |
| `GROK_WEBHOOK_URL` | 아니오 | `https://grok.example/hook` | 비우면 Grok으로 넘기지 않는다. 실제 URL은 사용자가 준다 |
| `GROK_WEBHOOK_KEY`를 서버 파일에 넣었는지 | URL을 쓸 때 예 | 넣었음 / 아직 | 키 값 자체는 채팅으로 받지 않는다 |
| `GROK_WEBHOOK_KEY_HEADER` | 아니오 | 비움 또는 `X-Grok-Key` | 비우면 `Authorization: Bearer` |
| 작업 저장소 | 작업을 보낼 때 예 | `owner/name` | `owner/name` 형식 |
| 기준 브랜치 | 작업을 보낼 때 예 | `main` | |
| full SHA | 작업을 보낼 때 예 | `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` | 40자 또는 64자. 짧은 SHA는 거절된다 |
| `handoff_id` | 작업을 보낼 때 예 | 사용자가 새로 만든 UUID | `00000000-0000-4000-8000-000000000000`은 예시이므로 실제 요청에 쓰지 않는다 |
| 교체 창을 쓸지 | 아니오 | 아니오 | 쓸 때만 secondary 변수를 채운다 |

키 값(`CUE_BRIDGE_TOKEN`, `CUE_BRIDGE_HMAC_KEY`, secondary, `GROK_WEBHOOK_KEY`)은 입력으로 받지 않는다. 서버 안에서 생성해 서버의 `.env`에만 쓴다. 사용자가 채팅에 키를 붙여 넣으면 그 값을 다시 출력하지 말고, 폐기 후 서버에서 새로 만들라고 말한다.

## 1. 역할

### Grok

Grok은 작업 메일을 보낸다. `GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`가 있으면 grok-cue는 접수 커밋 뒤 그 JSON을 아웃박스로 보낸다. 첫 `202`의 `forwarded`는 `false`다. `true`는 Grok 2xx가 기록된 뒤다.

- 루틴의 수신 주소가 `GROK_WEBHOOK_URL`이다.
- `GROK_WEBHOOK_KEY_HEADER`가 비어 있으면 헤더는 `Authorization: Bearer <GROK_WEBHOOK_KEY>`다.
- 헤더 이름을 적으면 그 헤더의 값으로 키만 온다. Bearer 접두사는 붙지 않는다.
- 받는 키는 `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`뿐이다. 스펙은 [DESIGN.md](DESIGN.md#스펙)다.
- `schema_version`, `title`, `created_at`, `reply_to`, `requested_by`는 오지 않는다.
- 한 시도의 제한 시간은 5초다. 재시도는 첫 시도를 포함해 최대 7회, 첫 시도부터 60분 안이다. 실패하거나 Grok이 2xx가 아니어도, 이미 돌려준 `202`는 바뀌지 않는다.
- Grok은 이 프로세스로 메일을 보내지 않는다. Bridge도 메일을 보내지 않는다.

### Cue

Cue 쪽 흐름:

1. 작업 메일을 받는다.
2. `handoff_id`와 GitHub ref를 맞춘다.
3. Cue의 작업과 산출물을 공유 저장소에 남긴다.
4. 결과 링크와 상태를 서명된 웹훅 요청 한 번으로 보낸다 (`POST /v1/cue/tasks`).
5. 같은 `handoff_id`로 답장을 이어 간다.

2026-10-29 E2E는 성공한 요청 기록 한 건이다. 그 환경이 서명된 JSON POST를 보냈고, Bridge는 HTTP `202`와 `status`=`received`, `executed`=`false`, `forwarded`=`true`를 돌려주었다. Grok 쪽 수신은 그 응답과 별개로 노른(사용자)가 16:05 KST에 확인했다. 이 기록은 그 요청 한 건만을 말한다. Cue 채팅의 모든 작업이 자동으로 보내진다는 뜻이 아니고, 다른 환경의 설치 호환도 증명하지 않는다. 그 뒤 0.1.0 더미 실서버 확인은 6/6 통과이다. Cue 실제 요청 1건이 접수되고 전달되었다. 2026-10-30 22:40 KST의 Cue 실제 요청 1건은 v2로 확인되었다(저널과 DB의 `signature_version=2`). 2026-10-30 23:00 KST에 0.2.0을 배포한 뒤, v1과 버전 헤더가 없는 더미 요청은 둘 다 `401`(`unknown_signature_version`)이었고 아웃박스에는 쌓이지 않았다. v2 더미 요청은 `202`, `delivered`, `signature_version=2`였다. `healthz`는 `200`이었다.

HTTP `202`는 접수를 커밋한 뒤의 응답이다. `CUE_STATE_DB`가 있으면 SQLite에 남고, 비어 있으면 프로세스 메모리뿐이라 재시작하면 사라진다. Grok이 받았다는 뜻도, 어떤 작업이 실행되었다는 뜻도 아니다. `forwarded: true`는 그 아웃박스 행이 Grok 2xx를 기록했을 때만이다. 첫 `202`의 `forwarded`는 `false`다. 재시도는 첫 시도를 포함해 최대 7회, 첫 시도부터 60분 안이다.

보내는 쪽의 일반 요구 사항은 [SETUP.md](SETUP.md)의 'Cue 쪽 설정'이고, 참고 송신기는 `scripts/check_bridge.py`다. 그 스크립트의 기본은 dry-run이다. 실제 전송은 `--send`를 붙일 때만 한다. 시험 요청은 전달이 켜져 있으면 Grok으로 나갈 수 있다. 그 첫 `202`는 Grok 2xx를 확인한 것이 아니다.

서명:

- `Authorization: Bearer <CUE_BRIDGE_TOKEN>`
- `X-Cue-Timestamp`: Unix 초. 서버 시각과 ±300초
- `X-Cue-Signature`: HMAC-SHA256 hex(대소문자 구분 없음). `X-Cue-Signature-Version: 2`가 필요하다. 입력은 `v2`, METHOD, PATH, timestamp, raw_body를 줄바꿈으로 잇는다. `sha256=`은 붙이지 않는다. 헤더가 없거나 `1`이거나 그 밖의 값이면 `401` `{"error":"unauthorized"}`이다. 응답에 서명이나 비밀을 넣지 않는다. 검증을 통과해 접수되면 로그와 접수 행에 `signature_version=2`만 남는다
- `Content-Type: application/json`
- 키는 `CUE_BRIDGE_HMAC_KEY`다. 교체 창이면 secondary도 받는다
- Bearer와 HMAC가 둘 다 맞아야 한다

재시도:

보내는 쪽이 다시 보낼 때는 아래를 따른다. Bridge의 재시도는 그와 별개로, 같은 접수 행을 최대 7회, 60분 안에서 다시 보낸다.

- 본문 바이트와 `idempotency_key`는 유지한다
- `X-Cue-Timestamp`와 `X-Cue-Signature`만 새로 만든다
- 같은 서명을 다시 보내면 `401`이다
- 새 서명의 같은 본문은 `202`이고 처음과 같은 `handoff_id`다. 전달은 다시 하지 않는다. `forwarded`는 그때의 아웃박스 상태이고, Grok 2xx가 기록된 뒤에만 `true`다
- 같은 키의 다른 본문은 `409 idempotency_conflict`다
- 같은 `(handoff_id, attempt, report_seq)`의 다른 본문은 `409 sequence_conflict`다. 이전 세대는 저장만 하고 전달하지 않는다

`github_ref` 규칙:

작업 메일, Issue 또는 PR 본문, 웹훅 `github_ref`의 저장소·브랜치·SHA는 모두 같아야 한다. 하나라도 다르면 Cue는 작업을 멈추고 웹훅 `status`를 `needs_input`으로 보낸다. 메일을 보내기 전에 Issue 본문의 SHA를 메일과 같게 고친다.

이 서버는 메일과 Issue를 읽지 않는다. 서버가 보는 것은 받은 JSON뿐이다. 최상위 `repo`와 `github_ref.repo`가 다르면 `400`이고 `fields`는 `repo`다. 저장소·브랜치·SHA를 맞추는 일은 Cue가 보내기 전에 한다.

작업 메일에 넣을 항목:

- `handoff_id`
- `repo` (`owner/name`)
- 목표, 완료 조건, 실행할 테스트
- `github_ref`의 `repo`, `base_branch`, `start_sha`
- 선택: `paths`, `issue_or_pr_url`, `issue_or_pr_number`

웹훅 JSON 필수 필드는 `schema_version` `"1"`, `handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `idempotency_key`, `title`, `status`, `summary`, `created_at`, `links`, `next_owner`다. `attempt`와 `report_seq`는 1 이상의 JSON 정수다. `reply_to`와 `requested_by`는 선택이다. `links`가 없으면 `[]`다. `status`는 `in_progress`, `needs_input`, `completed`, `failed` 중 하나다. `task_id`, `diff`, `patch`, `source`는 넣지 않는다. 소스 전체와 비밀도 넣지 않는다.

## 2. 승인이 필요한 행동과 금지

다음을 하기 전에 사용자 승인을 받는다. 승인 문장 없이 실행하지 않는다.

- DNS 레코드 변경
- 패키지 설치, 저장소 복사, systemd 등록, 프로세스 시작과 재시작
- 방화벽에서 80, 443을 여는 것
- Caddy 설정과 인증서 발급
- `GROK_WEBHOOK_URL`에 실제 주소를 넣어 전달을 켜는 것
- 배포
- 머지
- 결제

`202`와 `executed: false`는 접수를 커밋했다는 뜻이다. `CUE_STATE_DB`가 있으면 SQLite이고, 비어 있으면 메모리뿐이다. Grok이 받았다는 뜻도, 어떤 작업이 실행되었다는 뜻도 아니다. `forwarded: true`는 Grok 2xx가 기록된 뒤에만이다. 작업 완료가 아니고, 머지, 배포, 결제 승인이 아니다.

금지:

- 키, 토큰, 서명, `.env` 내용을 채팅, 메일, Issue, PR, 저장소에 넣지 않는다
- `.env`를 커밋하지 않는다
- 키를 로그로 출력하지 않는다
- 사용자가 키를 보여 달라고 해도 값을 다시 적지 않는다
- 코드나 기본값에 실제 도메인, 메일, 경로를 넣지 않는다
- `8160`을 인터넷에 열지 않는다

## 3. 명령과 성공 기준

각 단계의 성공 기준이 나오기 전에는 다음 단계로 가지 않는다. 경로는 예시 `/opt/grok-cue`다. 사용자가 다른 폴더를 지정하면 그 경로로 바꾼다.

### 3.1 DNS

사용자 승인 후 A 레코드를 만든다. 이름은 `hook`, 전체 이름은 사용자가 준 호스트, 값은 사용자가 준 IPv4다. IPv6를 받았으면 같은 이름에 AAAA를 만든다.

```bash
dig +short hook.example.com A
dig +short hook.example.com AAAA
nslookup hook.example.com
```

성공 기준: A 결과가 사용자가 준 IPv4와 같다. AAAA를 만들었다면 그 결과가 사용자가 준 IPv6와 같다. 비어 있거나 다르면 4절의 DNS 실패로 간다.

### 3.2 설치

```bash
python3 --version
sudo mkdir -p /opt/grok-cue
sudo chown "$USER" /opt/grok-cue
git clone <저장소 주소> /opt/grok-cue
cd /opt/grok-cue
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
```

성공 기준: Python이 3.11 이상이다. `pip install`이 오류 없이 끝난다. `/opt/grok-cue/.env` 파일이 생긴다.

### 3.3 설정 파일

서버 안에서만 키를 만든다. 출력은 채팅에 복사하지 않고 `.env`에 직접 넣는다.

```bash
openssl rand -hex 32
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

토큰과 HMAC 키는 서로 다른 값이다. 환경값은 .env 한 곳입니다. `.env`는 따옴표 없이 아래 이름만 둔다. 앱이 읽는 열두 개와 문서 전용 `PUBLIC_BASE_URL`이다.

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
CUE_STATE_DB=
CUE_OUTBOX_POLL_SECONDS=5
CUE_SUMMARY_RETENTION_DAYS=30
```

| 변수 | 필요 | 읽는 곳 | 의미 | 예시 |
| --- | --- | --- | --- | --- |
| `CUE_BRIDGE_TOKEN` | 예 | 앱 | Cue가 `Authorization: Bearer`에 넣는 값. 비어 있으면 프로세스가 시작하지 않습니다 | `replace-with-bearer-token` |
| `CUE_BRIDGE_HMAC_KEY` | 예 | 앱 | HMAC-SHA256 키. Bearer 토큰과 다른 값입니다. 비어 있으면 프로세스가 시작하지 않습니다 | `replace-with-hmac-key` |
| `CUE_BRIDGE_TOKEN_SECONDARY` | 아니오 | 앱 | 교체하는 동안 함께 받는 두 번째 Bearer. 비우면 primary만 받습니다 | 비움, 또는 새로 만든 토큰 |
| `CUE_BRIDGE_HMAC_KEY_SECONDARY` | 아니오 | 앱 | 교체하는 동안 함께 받는 두 번째 HMAC 키. 비우면 primary만 받습니다 | 비움, 또는 새로 만든 키 |
| `REPLY_TO_ALLOWLIST` | 코드상 선택, 실제로는 필요 | 앱 | 쉼표로 구분한 수신 메일. 대소문자는 무시합니다. 비우면 모든 요청을 `400`으로 거절합니다 | `cue@example.com` |
| `REPLY_TO_DEFAULT` | 코드상 선택, 실제로는 필요 | 앱 | `reply_to`를 생략했거나 비웠을 때 쓸 주소. 허용 목록에 있어야 합니다. 비어 있거나 목록 밖이면 `400`입니다 | `cue@example.com` |
| `GROK_WEBHOOK_URL` | 아니오 | 앱 | Grok 루틴이 받는 주소. 비우면 전달하지 않습니다 | 비움, 또는 `https://grok.example/hook` |
| `GROK_WEBHOOK_KEY` | 아니오 | 앱 | 그 주소에 보내는 키. URL과 키가 둘 다 있어야 전달합니다 | 비움, 또는 따로 만든 키 |
| `GROK_WEBHOOK_KEY_HEADER` | 아니오 | 앱 | 비우면 `Authorization: Bearer <GROK_WEBHOOK_KEY>`. 이름을 적으면 그 헤더의 값으로 키만 보냅니다 | 비움, 또는 `X-Grok-Key` |
| `CUE_STATE_DB` | 아니오 | 앱 | 접수·서명 재생·아웃박스 SQLite 경로. 비우면 프로세스 메모리만 씁니다 | 비움, 또는 `/var/lib/grok-cue/state.sqlite` |
| `CUE_OUTBOX_POLL_SECONDS` | 아니오 | 앱 | 아웃박스 주기(초). 기본 5. 0 이하는 시작하지 않습니다 | `5` |
| `CUE_SUMMARY_RETENTION_DAYS` | 아니오 | 앱 | `delivered`와 `dead` 행의 `summary`를 지울 때까지의 날 수. 기본 30. 0 이하는 시작하지 않습니다. `queued`와 `sending`은 지우지 않습니다 | `30` |
| `PUBLIC_BASE_URL` | 확인 스크립트 | 문서 전용 | 확인 스크립트가 부르는 주소. 앱은 읽지 않습니다. 비어 있으면 스크립트가 멈춥니다 | `https://hook.example.com` |

`replace-with-`는 자리표시자다. 서버에서 만든 값으로 바꾼다. `REPLY_TO_*`는 사용자가 준 주소로 바꾼다. `PUBLIC_BASE_URL`은 사용자가 준 공개 주소다. 로컬만 볼 때는 `http://127.0.0.1:8160`으로 둔다. Grok URL과 키는 사용자가 전달을 승인하기 전에는 비워 둔다.

```bash
chmod 600 /opt/grok-cue/.env
sudo chown bridge:bridge /opt/grok-cue/.env
stat -c '%U:%G %a' /opt/grok-cue/.env
sudo -u bridge test -r /opt/grok-cue/.env && echo ok
```

성공 기준: `stat`이 `bridge:bridge 600`을 출력하고 마지막 명령이 `ok`다. `bridge`는 systemd 유닛의 `User=`/`Group=`과 같은 사용자다. 서비스 계정이 없으면 사용자 승인 후 `sudo useradd --system --no-create-home --shell /usr/sbin/nologin bridge`로 만든다. 파일에는 위 표의 이름만 있다. 채팅 기록에 키 값이 없다.

### 3.4 테스트와 프로세스

작업 폴더는 설치 폴더여야 한다. 앱은 그 폴더의 `.env`를 읽는다.

```bash
cd /opt/grok-cue
source .venv/bin/activate
pytest
uvicorn app.main:app --host 127.0.0.1 --port 8160
```

성공 기준: pytest가 실패 없이 끝난다. 아래가 `200`이고 본문이 `ok`다. `.env`를 셸로 불러오지 않는다.

```bash
curl -sS -D - http://127.0.0.1:8160/healthz
```

계속 켜 두려면 사용자 승인 후 [SETUP.md](SETUP.md)의 systemd 예시 유닛을 `/etc/systemd/system/grok-cue.service`에 둔다. 그 블록은 문서 예시이며 저장소에 유닛 파일을 추가하지 않는다. 서비스를 켜기 전에 같은 문서의 소유자 확인을 통과시킨다. 설치 디렉터리와 `.venv`의 소유자는 설치 계정이고, `.env`만 `bridge:bridge`와 `chmod 600`이다. `bridge`는 앱 코드와 `.venv`를 읽고 실행할 수 있어야 한다. 확인 명령은 `sudo -u bridge test -r`, `sudo -u bridge test -x`, `stat`, `ls -l`이다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grok-cue.service
sudo systemctl status grok-cue.service
```

성공 기준: `systemctl is-active grok-cue.service`가 `active`다.

프로세스는 하나만 띄운다. `uvicorn --workers`를 붙이지 않고, 서비스 인스턴스도 하나다. `ProtectSystem=strict`이면 [SETUP.md](SETUP.md)의 `ReadWritePaths=/var/lib/grok-cue` 드롭인이 필요하다. 이미 strict인 유닛은 `ReadWritePaths`만 추가한다. 그 예시가 `ProtectSystem=strict`도 넣으면, 기본 유닛에 없던 샌드박스가 새로 켜진다. DB 디렉터리를 쓸 수 없으면 `POST /v1/cue/tasks`가 `503 storage_unavailable`를 돌려준다. `GET /healthz`는 `ok`다.

### 3.5 HTTPS

사용자 승인 후 Caddyfile에 다음을 넣는다. 호스트 이름은 사용자가 준 값이다.

```caddyfile
hook.example.com {
    request_body {
        max_size 64KiB
    }
    reverse_proxy 127.0.0.1:8160
}
```

80과 443만 연다. 8160은 열지 않는다. Caddy를 다시 읽힌다.

성공 기준: 사용자가 준 공개 주소(예시 `hook.example.com`)로 연 아래 명령이 `200`, 본문 `ok`다. 인증서 오류가 없다.

```bash
curl -sS -D - https://hook.example.com/healthz
```

### 3.6 서명 확인

> [!WARNING]
> 전달을 켜기 **전에만** 실행한다. `GROK_WEBHOOK_URL`/`KEY`가 설정되어 있으면 시험용 작업이 Grok으로 나갈 수 있다. 그 첫 `202`는 Grok의 2xx를 확인한 것이 아니다.

`.env`의 허용 목록과 기본 주소가 설정된 뒤에 한다. 저장소의 `scripts/check_bridge.py`를 쓴다(설명은 [SETUP.md](SETUP.md) 6절). `.env`는 스크립트 안의 `KEY=VALUE` 파서가 읽으므로 셸로 불러오지 않는다. 기본은 dry-run이라 아무것도 보내지 않고 요약만 출력한다. 실제 확인은 `--send`이며, 전달이 꺼져 있을 때만 사용자 승인 후 실행한다. `PUBLIC_BASE_URL`, `CUE_BRIDGE_TOKEN`, `CUE_BRIDGE_HMAC_KEY`가 없으면 멈춘다.

```bash
cd /opt/grok-cue
sudo -u bridge .venv/bin/python scripts/check_bridge.py            # dry-run
sudo -u bridge .venv/bin/python scripts/check_bridge.py --send     # 전달을 켜기 전에만
```

첫 `202`의 `forwarded`는 `false`가 정상이다. 웹훅이 켜져 있으면 `forward_state`는 `queued`다. URL이나 키가 하나라도 비어 있으면 `disabled`다. 스크립트는 같은 본문 재전송의 `forwarded`를 `false`로 기대한다. 서버는 2xx가 기록된 뒤의 재전송에서만 `forwarded`를 `true`로 둔다.

성공 기준:

| 요청 | 코드 | 본문에서 볼 것 |
| --- | --- | --- |
| `GET /healthz` | `200` | `ok` |
| 서명 없는 `POST /v1/cue/tasks` | `401` | `{"error":"unauthorized"}` |
| 서명된 첫 POST | `202` | `status`=`received`, `executed`=`false` |
| 같은 본문, 새 시각과 새 HMAC | `202` | 같은 `handoff_id`, 다시 전달하지 않음. 스크립트는 `forwarded`=`false`를 기대한다 |
| 같은 `idempotency_key`, 다른 본문 | `409` | `{"error":"idempotency_conflict"}` |
| 같은 `(handoff_id, attempt, report_seq)`, 다른 본문 | `409` | `{"error":"sequence_conflict"}` |

첫 `202`의 `forwarded`는 `false`다. URL과 키가 둘 다 있으면 `forward_state`는 `queued`이고, 하나라도 비어 있으면 `disabled`다. HTTP `202`는 접수를 커밋했다는 뜻이다. `CUE_STATE_DB`가 있으면 SQLite이고, 비어 있으면 메모리뿐이다. Grok이 받았다는 뜻도, 어떤 작업이 실행되었다는 뜻도 아니다. `forwarded: true`는 Grok 2xx가 기록된 뒤에만이다. 작업 완료도, Grok 루틴의 성공도 아니다.

같은 서명을 그대로 다시 보내면 `401`이다. 그 결과를 `202` 재전송으로 보지 않는다.

스크립트는 실행할 때마다 시험용 `handoff_id`와 `idempotency_key`를 새로 만들고, `title`은 `[TEST] connectivity check`, `summary`는 `connectivity test — not a real task`다. 실제 작업 요청에는 사용자가 새로 만든 UUID를 넣는다.

## 4. 실패했을 때

| 실패 | 처리 |
| --- | --- |
| 필수 입력이 없음 | 그 항목만 다시 묻고 멈춘다. 도메인이나 IP를 만들지 않는다 |
| `dig`가 비어 있거나 다른 주소 | DNS 이름과 값을 사용자에게 알리고 수정 승인을 받는다. 전파 전에는 HTTPS 단계로 가지 않는다 |
| `pip` 또는 `pytest` 실패 | 서비스와 프록시를 켜지 않는다. 실패 마지막 줄을 요약해 알린다. 비밀이 출력에 있으면 그 부분은 생략한다 |
| 프로세스가 바로 종료 | `CUE_BRIDGE_TOKEN`과 `CUE_BRIDGE_HMAC_KEY`가 비었거나, 허용 목록·`REPLY_TO_DEFAULT`가 메일 주소 하나가 아니다. 값을 채팅에 찍지 말고 이름만 확인한다 |
| 로컬 `/healthz`는 `200`인데 도메인은 실패 | Caddy 이름, `reverse_proxy 127.0.0.1:8160`, 80/443, 인증서 순으로 본다. 8160을 공개하는 쪽으로 우회하지 않는다 |
| 인증서 오류 | DNS가 이 서버를 가리키는지 다시 `dig`한다. Caddy가 실행 중인지 확인한다 |
| 서명 POST가 `401` | 시계가 300초 안인지, Bearer와 HMAC를 둘 다 넣었는지, `sha256=`을 빼었는지, 같은 서명을 재사용하지 않았는지 본다. 키 값은 출력하지 않는다 |
| `400` `reply_to_not_allowed` | 허용 목록이 비었거나 주소가 목록 밖이거나 기본 주소가 목록에 없다. 응답은 사유 코드뿐이고 허용 목록은 넣지 않는다. `.env`를 고치고 프로세스를 재시작한다 |
| `400` `invalid_request` | `fields`의 이름을 [DESIGN.md](DESIGN.md)와 맞춘다. 본문은 64 KiB 이하, `Content-Type`은 `application/json`이다 |
| `400` `task_id_not_allowed` | `task_id` 키를 제거하고 `handoff_id`만 남긴다 |
| `409` | 처음 보낸 바이트를 그대로 다시 보낸다. 키 순서가 바뀐 JSON은 다른 본문이다 |
| `202`인데 `forwarded`가 `false` | 웹훅이 켜져 있으면 첫 `202`의 `queued`는 정상이다. URL이나 키가 비어 있으면 `disabled`이 정상이다. `delivered` 뒤의 재전송만 `true`다 |
| `202`인데 Grok에 없음 | 첫 `202`는 2xx를 기다리지 않는다. URL, 헤더 이름, 시도당 5초, 최대 7회·60분을 사용자와 확인한다. Bridge의 `202`를 실패로 바꾸지 않는다 |
| 메일·Issue·웹훅의 저장소, 브랜치, SHA가 다름 | 웹훅을 성공으로 보내지 않는다. Cue는 멈추고 `status`=`needs_input`으로 보고한다 |
| 키를 이미 채팅에 올림 | 그 값을 반복하지 않는다. 사용자에게 폐기를 알리고 서버에서 새 키를 만들어 `.env`만 갱신한다 |
| 머지, 배포, 결제 요청 | 접수 `202`로 처리하지 않는다. 별도 승인이 있을 때만 그 작업을 진행한다 |

키를 바꿀 때는 새 값을 `CUE_BRIDGE_TOKEN_SECONDARY`와 `CUE_BRIDGE_HMAC_KEY_SECONDARY`에 넣고 재시작한다. 그동안 이전 값과 새 값을 모두 받는다. Cue가 새 값만 쓰면 새 값을 primary로 옮기고 secondary를 비운 뒤 다시 시작한다. 이 재시작도 사용자 승인이 필요하다.
