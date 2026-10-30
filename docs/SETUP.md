한국어 | [English](SETUP.en.md)

# 서버에 올리는 순서

이 글은 Bridge를 리눅스 서버에 올리고, Cue가 서명된 요청을 보내게 한 뒤, Grok이 전달된 알림을 받게 하는 순서입니다. 주소, 메일, 경로, 키는 모두 예시입니다. `hook.example.com`은 본인 도메인으로 바꿉니다. `replace-with-`로 시작하는 문자열은 비밀이 아니므로, 아래 명령이 출력한 값으로 바꿉니다.

준비와 응답 코드의 짧은 설명은 [README.md](../README.md), 본문 필드는 [DESIGN.md](DESIGN.md), 에이전트가 같은 순서를 실행할 때의 절차는 [AI_SETUP.md](AI_SETUP.md)에 있습니다.

예시에서 쓰는 값:

| 항목 | 예시 | 본인이 바꿀 것 |
| --- | --- | --- |
| 도메인 | `hook.example.com` | 본인 도메인 |
| 서버 IPv4 | `203.0.113.10` | 서버의 공인 IPv4 |
| 서버 IPv6 | `2001:db8::10` | 서버의 공인 IPv6. 없으면 생략 |
| 설치 폴더 | `/opt/grok-cue` | 원하는 폴더 |
| 설정 파일 | `/opt/grok-cue/.env` | 그 폴더 안의 `.env` |
| 수신 포트 | `127.0.0.1:8160` | 바깥에 열지 않는 포트 |
| 수신 메일 | `cue@example.com` | 허용할 메일 주소 |

## 0. 준비물

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

## 1. 도메인과 DNS

1. 도메인을 관리하는 곳에서 DNS 설정을 엽니다.
2. 이름 `hook`에 A 레코드를 추가합니다. 전체 이름은 `hook.example.com`입니다. 값은 서버의 공인 IPv4입니다. 이 문서의 `203.0.113.10`은 자리표시자입니다.
3. 서버에 IPv6가 있으면 같은 이름에 AAAA 레코드를 추가합니다. 이 문서의 `2001:db8::10`은 자리표시자입니다. IPv6가 없으면 이 레코드는 만들지 않습니다.
4. 저장합니다. DNS가 퍼지는 데 시간이 걸릴 수 있습니다.

확인:

```bash
dig +short hook.example.com A
dig +short hook.example.com AAAA
nslookup hook.example.com
```

A 조회 결과에 서버 IPv4가 보여야 합니다. AAAA를 만들었다면 그 결과에는 서버 IPv6가 보여야 합니다. 결과가 비어 있거나 다른 주소이면 레코드 이름과 값을 다시 확인합니다.

## 2. 리눅스 서버에 설치하고 실행

Python 3.11 이상이 필요합니다. 아래 경로는 예시입니다.

```bash
sudo mkdir -p /opt/grok-cue
sudo chown "$USER" /opt/grok-cue
git clone <저장소 주소> /opt/grok-cue
cd /opt/grok-cue
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
```

`.env`를 연 뒤 4절의 값을 넣습니다. 저장한 다음 권한을 좁힙니다.

```bash
chmod 600 /opt/grok-cue/.env
sudo chown bridge:bridge /opt/grok-cue/.env
stat -c '%U:%G %a' /opt/grok-cue/.env          # bridge:bridge 600
sudo -u bridge test -r /opt/grok-cue/.env && echo ok
```

`bridge`는 아래 systemd 유닛의 `User=`/`Group=`과 같은 예시 사용자입니다. 다른 사용자를 쓰면 `.env` 소유와 유닛의 `User=`/`Group=`을 함께 바꿉니다. 이 `chown`은 `.env`에만 적용합니다. 설치 디렉터리와 `.venv`의 소유자는 위의 `"$USER"` 설치 계정으로 둡니다.

```bash
pytest
```

`pytest`가 통과하면 프로세스를 켭니다. 이 주소는 서버 안에서만 받습니다. 인터넷에는 3절의 프록시로 엽니다.

```bash
source /opt/grok-cue/.venv/bin/activate
cd /opt/grok-cue
uvicorn app.main:app --host 127.0.0.1 --port 8160
```

앱은 작업 폴더의 `.env`를 읽습니다. `cd`를 빼면 설정 파일을 찾지 못합니다.

부팅 후에도 켜 두고 싶으면 systemd 유닛 예시를 `/etc/systemd/system/grok-cue.service`에 저장할 수 있습니다. 이 블록은 문서 안의 예시이며, 저장소에는 유닛 파일을 넣지 않습니다.

설치 디렉터리의 소유자와 브리지 서비스 계정은 다릅니다.

- 설치 디렉터리(`/opt/grok-cue`)의 소유자는 위에서 `sudo chown "$USER"`로 맞춘 설치 계정입니다. 앱 코드와 `.venv`도 그 계정 소유로 둡니다.
- `bridge`는 systemd `User=`/`Group=`의 예시 이름입니다. 설치 디렉터리를 `bridge` 소유로 바꾸지 않습니다. `bridge`는 앱 코드와 `.venv`를 읽고 실행할 수 있어야 합니다.
- `.env`만 소유자가 `bridge:bridge`이고 `chmod 600`입니다.

서비스를 시작하기 전에 아래를 실행합니다. `test`가 실패하면 `systemctl`로 가지 않습니다.

```bash
sudo -u bridge test -r /opt/grok-cue/app/main.py && echo ok
sudo -u bridge test -x /opt/grok-cue/.venv/bin/uvicorn && echo ok
sudo -u bridge test -r /opt/grok-cue/.env && echo ok
stat -c '%U:%G %a %n' /opt/grok-cue /opt/grok-cue/app/main.py /opt/grok-cue/.venv/bin/uvicorn /opt/grok-cue/.env
ls -l /opt/grok-cue/.env /opt/grok-cue/.venv/bin/uvicorn
```

성공 기준: 세 `test`가 각각 `ok`를 출력합니다. `stat`에서 설치 디렉터리, 앱 코드, uvicorn은 설치 계정 소유이고 `.env`는 `bridge:bridge 600`입니다. `ls -l`에서 `.env`는 `-rw-------`이고 소유자는 `bridge`입니다.

```ini
[Unit]
Description=Cue Grok Bridge
After=network.target

[Service]
Type=simple
User=bridge
Group=bridge
WorkingDirectory=/opt/grok-cue
EnvironmentFile=/opt/grok-cue/.env
ExecStart=/opt/grok-cue/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8160
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grok-cue.service
sudo systemctl status grok-cue.service
```

설정을 바꾼 뒤에는 `sudo systemctl restart grok-cue.service`로 다시 읽힙니다.

프로세스는 하나만 띄웁니다. `uvicorn --workers`나 서비스 인스턴스를 여러 개 두지 않습니다. 아웃박스 워커는 프로세스 안에 하나뿐입니다.

`ProtectSystem=strict`를 쓰면 DB 디렉터리는 읽기 전용으로 막힙니다. 예시 경로 `/var/lib/grok-cue`만 쓰려면 드롭인을 `/etc/systemd/system/grok-cue.service.d/state-db.conf`에 둡니다. 이 블록도 문서 예시입니다. 이미 `ProtectSystem=strict`인 유닛은 `ReadWritePaths`만 추가합니다. 아래 예시는 `ProtectSystem=strict`도 포함하므로, 기본 유닛에 없던 샌드박스가 새로 켜집니다.

```ini
[Service]
ProtectSystem=strict
ReadWritePaths=/var/lib/grok-cue
```

```bash
sudo mkdir -p /var/lib/grok-cue
sudo chown bridge:bridge /var/lib/grok-cue
sudo chmod 700 /var/lib/grok-cue
```

`.env`의 `CUE_STATE_DB` 예시는 `/var/lib/grok-cue/state.sqlite`입니다. 이 디렉터리를 쓸 수 없으면 `POST /v1/cue/tasks`는 `503 storage_unavailable`를 돌려줍니다. `GET /healthz`는 그대로 `ok`입니다.

## 3. HTTPS 역방향 프록시

Caddy가 `https://hook.example.com`을 받아 `127.0.0.1:8160`으로 넘기는 예시입니다. 이 블록도 문서 안의 예시입니다. 서버의 Caddyfile에 넣습니다.

```caddyfile
hook.example.com {
    request_body {
        max_size 64KiB
    }
    reverse_proxy 127.0.0.1:8160
}
```

`request_body`의 `max_size`는 앱의 64 KiB 제한을 프록시에서도 미리 거는 선택 설정입니다. 앱은 이 설정이 없어도 인증 전에 크기를 검사합니다.

Caddy를 다시 읽힌 뒤, 80번과 443번 포트가 서버 방화벽에서 열려 있어야 합니다. 8160은 바깥에 열지 않습니다. DNS가 이 서버를 가리키면 Caddy가 인증서를 받습니다.

로컬에서 프록시 없이 볼 때는 `http://127.0.0.1:8160`을 씁니다.

## 4. 환경 변수

환경값은 .env 한 곳입니다. 확인 스크립트도 그 파일을 읽습니다. 앱이 읽는 이름은 `app/settings.py`의 열두 개입니다. `PUBLIC_BASE_URL`은 문서와 확인 스크립트만 읽고, 앱은 읽지 않습니다.

값을 만들 때는 토큰과 HMAC 키를 서로 다른 값으로 두 번 실행합니다.

```bash
openssl rand -hex 32
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

두 명령 모두 32바이트를 16진수 문자열로 출력합니다. 어느 쪽이든 됩니다. 출력한 값을 `.env`에 따옴표 없이 붙입니다. 채팅, 메일, 저장소에는 붙이지 않습니다.

`.env` 예시:

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
| `CUE_STATE_DB` | 아니오 | 앱 | 접수·서명 재생·아웃박스 SQLite 경로. 비우면 프로세스 메모리만 씁니다. 기본 경로는 없습니다 | 비움, 또는 `/var/lib/grok-cue/state.sqlite` |
| `CUE_OUTBOX_POLL_SECONDS` | 아니오 | 앱 | 이 프로세스의 아웃박스 주기(초). 기본 5. 0 이하는 프로세스가 시작하지 않습니다. 시작 때도 한 번 pump합니다 | `5` |
| `CUE_SUMMARY_RETENTION_DAYS` | 아니오 | 앱 | `delivered`와 `dead` 행의 `summary`를 지울 때까지의 날 수. 기본 30. 0 이하는 시작하지 않습니다. `queued`와 `sending`은 지우지 않습니다 | `30` |
| `PUBLIC_BASE_URL` | 확인 스크립트 | 문서 전용 | 확인 스크립트가 부르는 주소. 앱은 읽지 않습니다. 비어 있으면 스크립트가 멈춥니다 | `https://hook.example.com` |

선택 항목은 비어 있으면 꺼진 것과 같습니다. `GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`를 예시처럼 비워 두면, 파일을 복사해도 Grok으로 넘기지 않습니다. 주소는 코드에 기본값으로 들어 있지 않습니다.

## 5. Cue와 Grok

### Cue가 보낼 요청

Cue는 `POST https://hook.example.com/v1/cue/tasks`로 JSON을 보냅니다. 로컬 확인이면 `http://127.0.0.1:8160/v1/cue/tasks`입니다.

헤더:

- `Authorization: Bearer <CUE_BRIDGE_TOKEN>`
- `X-Cue-Timestamp`: 지금 Unix 초. 서버 시각과 300초 안이어야 합니다
- `X-Cue-Signature`: HMAC-SHA256 hex(대소문자 구분 없음). `sha256=`은 붙이지 않습니다. `X-Cue-Signature-Version: 2`가 필요합니다. 입력은 `v2`, METHOD, PATH, timestamp, raw_body를 줄바꿈으로 이은 값입니다. 헤더가 없거나 `1`이거나 그 밖의 값이면 `401` `{"error":"unauthorized"}`입니다. 응답에 서명이나 비밀을 넣지 않습니다. 검증을 통과해 접수되면 로그와 접수 행에 `signature_version=2`만 남습니다
- `Content-Type: application/json`

서명은 `v2`, METHOD, PATH, timestamp, 본문 바이트를 줄바꿈으로 잇습니다. 본문을 다시 JSON으로 만들면 바이트가 달라져 서명이 맞지 않습니다. 처음 보낸 바이트를 보관합니다.

다시 보낼 때:

- 본문 바이트와 그 안의 `idempotency_key`는 그대로 둡니다.
- `X-Cue-Timestamp`와 `X-Cue-Signature`만 새로 만듭니다.
- 같은 서명을 그대로 다시 보내면 `401`입니다.
- 서명을 새로 만든 같은 본문은 `202`이고, 처음과 같은 `handoff_id`를 돌려줍니다. Grok 전달은 다시 하지 않습니다. `forwarded`는 그때의 아웃박스 상태이고, Grok 2xx가 기록된 뒤에만 `true`입니다.
- 같은 `idempotency_key`로 다른 본문을 보내면 `409 idempotency_conflict`입니다.
- 같은 `(handoff_id, attempt, report_seq)`로 다른 본문을 보내면 `409 sequence_conflict`입니다. 이전 세대는 저장만 하고 전달하지 않습니다.

`reply_to`를 생략하면 `REPLY_TO_DEFAULT`를 씁니다. 그 주소는 `REPLY_TO_ALLOWLIST`에 있어야 합니다. 본문 필드의 이름과 길이는 [DESIGN.md](DESIGN.md)와 같습니다. `task_id` 키는 넣지 않습니다.

### Cue 쪽 설정

> [!NOTE]
> **2026-10-29 확인:** 그 환경이 서명된 JSON POST를 실제로 보냈고, HTTP `202`와 `status`=`received`, `executed`=`false`, `forwarded`=`true`를 받았습니다. 하류 수신은 16:05 KST에 노른(사용자)가 확인했습니다. 이 기록은 그 요청 한 건만 말합니다. 그 뒤 0.1.0 더미 실서버 확인은 6/6 통과입니다. Cue 실제 요청 1건이 접수되고 전달되었습니다. 2026-10-30 22:40 KST의 Cue 실제 요청 1건은 v2로 확인되었습니다(저널과 DB의 `signature_version=2`). 2026-10-30 23:00 KST에 0.2.0을 배포한 뒤 확인했습니다. v1과 버전 헤더가 없는 더미 요청은 둘 다 `401`(`unknown_signature_version`)이었고 아웃박스에는 쌓이지 않았습니다. v2 더미 요청은 `202`, `delivered`, `signature_version=2`였습니다. `healthz`는 `200`이었습니다. 지금의 `forwarded: true`는 Grok 2xx가 기록된 뒤에만입니다. 아래는 Cue의 화면이나 메뉴가 아니라, 보내는 쪽이 지켜야 하는 일반 요구 사항입니다.

Cue 쪽 흐름:

1. 작업 메일을 받습니다.
2. `handoff_id`와 GitHub ref를 맞춥니다.
3. Cue의 작업과 산출물을 공유 저장소에 남깁니다.
4. 결과 링크와 상태를 서명된 웹훅 요청 한 번으로 보냅니다.
5. 같은 `handoff_id`로 답장을 이어 갑니다.

보내는 쪽(Cue 에이전트, 또는 Cue가 작업을 맡긴 다른 프로그램)은 다음 세 값을 비밀로 보관해야 합니다.

| 값 | grok-cue 쪽 이름 | 보내는 쪽에서의 쓰임 |
| --- | --- | --- |
| 접수 주소 | `PUBLIC_BASE_URL` + `/v1/cue/tasks` | POST 대상. 예: `https://hook.example.com/v1/cue/tasks` |
| Bearer 토큰 | `CUE_BRIDGE_TOKEN` | `Authorization: Bearer <토큰>` 헤더 |
| HMAC 키 | `CUE_BRIDGE_HMAC_KEY` | `X-Cue-Signature` 계산용. 헤더로 보내지 않음 |

요청마다 보내는 쪽이 할 일:

1. 본문 JSON 바이트를 한 번 만들고 보관합니다.
2. 현재 Unix 초를 `X-Cue-Timestamp`에 넣습니다.
3. `X-Cue-Signature-Version: 2`와 함께 `v2`, METHOD, PATH, timestamp, 본문 바이트를 줄바꿈으로 이은 HMAC-SHA256 hex를 `X-Cue-Signature`에 넣습니다.
4. `Authorization`, `Content-Type: application/json`과 함께 POST합니다.

이 순서를 그대로 구현한 **참고용 송신기**가 [`scripts/check_bridge.py`](../scripts/check_bridge.py)입니다(6절). 기본은 dry-run이라 아무것도 보내지 않습니다. 실제로 보내려면 `--send`가 필요합니다. 시험 요청은 Grok Bot에 실제 전달을 일으킬 수 있습니다. 2026-10-29 확인은 현재 Cue 환경의 한 건입니다. 다른 환경에서 Cue가 이 요청을 직접 보내지 못하면, 이 스크립트와 같은 방식으로 보냅니다.

### Grok이 받을 알림

`GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`가 둘 다 있으면, 접수 커밋 뒤 Bridge가 그 주소로 JSON을 보냅니다. 한 시도의 제한 시간은 5초입니다. 재시도는 첫 시도를 포함해 최대 7회, 첫 시도부터 60분 안입니다. 실패해도 Cue에게 이미 돌려준 `202`는 바뀌지 않습니다.

Grok 루틴은 그 주소에서 POST를 받습니다. `GROK_WEBHOOK_KEY_HEADER`가 비어 있으면 헤더는 `Authorization: Bearer <GROK_WEBHOOK_KEY>`입니다. 헤더 이름을 적으면 그 이름의 값으로 키만 옵니다. Bearer 접두사는 붙지 않습니다.

받는 JSON 키는 이 10개뿐입니다([DESIGN.md 스펙](DESIGN.md#스펙)).

`handoff_id`, `attempt`, `report_seq`, `repo`, `github_ref`, `status`, `summary`, `links`, `next_owner`, `idempotency_key`

`schema_version`, `title`, `created_at`, `reply_to`, `requested_by`는 포함되지 않습니다. 메일은 이 프로세스가 보내지 않습니다.

`202`는 접수를 커밋한 뒤의 응답입니다. `forwarded: true`는 그 행이 Grok 2xx를 기록했을 때만입니다. 첫 `202`의 `forwarded`는 `false`입니다. URL이나 키가 없으면 `forward_state`는 `disabled`입니다. 같은 본문을 다시 받아도 전달은 새로 하지 않습니다. `executed`는 항상 `false`입니다.

## 6. 동작 확인

> [!WARNING]
> `--send`는 전달을 켜기 **전에만** 쓰세요. `.env`에 `GROK_WEBHOOK_URL`과 `GROK_WEBHOOK_KEY`가 둘 다 있으면 시험용 작업이 Grok Bot에 실제로 전달됩니다. 스크립트는 이 경우 `forwarding ENABLED`라고 먼저 알려 줍니다.

먼저 `.env`에 토큰, HMAC 키, `REPLY_TO_ALLOWLIST=cue@example.com`, `REPLY_TO_DEFAULT=cue@example.com`을 넣고 프로세스를 다시 시작합니다. 허용 목록이 비어 있으면 서명된 요청도 `400`입니다.

헬스 체크는 인증이 없습니다. 주소는 본인 도메인으로 바꿉니다.

```bash
curl -sS -D - https://hook.example.com/healthz
```

`200`과 본문 `ok`이면 프로세스가 켜져 있습니다. `/docs`, `/redoc`, `/openapi.json`은 `404`입니다. 모든 응답에 `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`이 있고, `X-Content-Type-Options: nosniff`가 없으면 붙입니다.

### 확인 스크립트 `scripts/check_bridge.py`

저장소에 들어 있는 스크립트입니다. 표준 라이브러리만 씁니다. `.env`를 셸로 불러오지 않고 스크립트 안의 작은 `KEY=VALUE` 파서로 읽으므로, 값에 `&`, `$`, `;` 같은 문자가 있어도 셸이 해석하지 않고, 비밀값이 셸 환경에 남지도 않습니다.

- **기본은 dry-run입니다.** 서명된 시험 요청을 만들어 요약(대상 주소, 시험용 `handoff_id`, 본문 크기, 전달 설정 여부)만 출력하고 **아무것도 보내지 않습니다.** 토큰과 서명은 `<hidden>`으로 가립니다.
- **`--send`를 붙일 때만** 실제로 요청을 보냅니다. 헬스 체크 `200`, 서명 없는 POST `401`, 서명된 첫 POST `202`, 같은 본문 재전송 `202`(같은 `handoff_id`, 다시 전달하지 않음), 같은 키의 다른 본문 `409`를 차례로 확인합니다. 스크립트는 그 재전송의 `forwarded`를 `false`로 기대합니다. 서버의 `forwarded`는 그때의 아웃박스 상태이고, Grok 2xx가 기록된 뒤에만 `true`입니다.
- 시험용 값만 씁니다. `title`은 `[TEST] connectivity check`, `summary`는 `connectivity test — not a real task`, `handoff_id`와 `idempotency_key`는 실행할 때마다 새로 만듭니다. 상태 값과 필드는 스펙 그대로입니다.

설치 폴더에서 실행합니다. 로컬만 볼 때는 `.env`의 `PUBLIC_BASE_URL`을 `http://127.0.0.1:8160`으로 둡니다.

```bash
cd /opt/grok-cue
sudo -u bridge .venv/bin/python scripts/check_bridge.py            # dry-run, 보내지 않음
sudo -u bridge .venv/bin/python scripts/check_bridge.py --send     # 전달을 켜기 전에만
```

`.env`가 다른 곳에 있으면 `--env 경로`로 지정합니다. 같은 시각과 같은 서명을 그대로 다시 보내는 것은 스크립트의 재전송과 다릅니다. 그 경우는 `401`입니다.

## 7. 자주 막히는 곳

| 보이는 것 | 의미 | 확인 |
| --- | --- | --- |
| `401` `unauthorized` | Bearer, HMAC, 시각, 서명 재사용 중 하나가 맞지 않습니다. 어느 쪽인지는 알려 주지 않습니다 | 토큰과 HMAC 키가 서버 `.env`와 같은지 봅니다. `X-Cue-Timestamp`가 서버 시각과 300초 안인지 봅니다. 시계가 어긋나 있으면 맞춥니다. `sha256=`을 붙이지 않습니다. 같은 서명을 다시 보냈다면 시각과 서명만 새로 만듭니다 |
| `400` `reply_to_not_allowed` | 주소를 받을 수 없습니다. 응답은 사유 코드뿐이고, 허용 목록과 거절된 주소는 돌려주지 않습니다 | `REPLY_TO_ALLOWLIST`가 비어 있는지, `reply_to`가 그 목록에 있는지, `REPLY_TO_DEFAULT`가 비어 있거나 목록 밖인지 봅니다. `mailto:`와 수신자 여러 개도 거절됩니다. 바꾼 뒤 프로세스를 다시 시작합니다 |
| `400` `invalid_request` | 본문 형식입니다. `fields`에 항목이 있습니다 | `Content-Type`이 `application/json`인지, 본문이 64 KiB 이하인지, [DESIGN.md](DESIGN.md)의 필드 이름과 맞는지 봅니다 |
| `400` `task_id_not_allowed` | 본문 어딘가에 `task_id`가 있습니다 | 그 키를 빼고 `handoff_id`만 씁니다 |
| `409` `idempotency_conflict` | 같은 `idempotency_key`로 다른 본문 바이트를 보냈습니다 | 처음 보낸 바이트를 그대로 다시 보냅니다. JSON 키 순서만 바뀌어도 다른 본문입니다. 첫 접수는 그대로 남습니다 |
| `409` `sequence_conflict` | 같은 `(handoff_id, attempt, report_seq)`로 다른 본문 바이트를 보냈습니다 | 그 세대의 첫 본문을 유지합니다. 이전 세대는 저장만 하고 전달하지 않습니다 |
| `503` `storage_unavailable` | `POST /v1/cue/tasks`가 상태 DB를 쓰지 못했습니다. `GET /healthz`는 `ok`일 수 있습니다 | `CUE_STATE_DB` 디렉터리를 쓸 수 있는지 봅니다. `ProtectSystem=strict`이면 `ReadWritePaths`가 필요합니다 |
| `202`인데 `forwarded`가 `false` | 아직 Grok 2xx가 기록되지 않았거나 전달이 꺼져 있습니다 | 웹훅이 켜져 있으면 첫 `202`는 `forward_state` `queued`가 정상입니다. URL과 키가 하나라도 비어 있으면 `disabled`입니다. `delivered`가 된 뒤의 재전송만 `forwarded`가 `true`입니다 |
| `202`인데 Grok에 도착하지 않음 | 첫 `202`는 2xx를 기다리지 않습니다. 실패해도 그 `202`는 바뀌지 않습니다 | URL과 키 헤더 이름을 봅니다. 한 시도는 5초이고, 재시도는 최대 7회, 60분 안입니다 |
| `dig` 결과가 비어 있음 | DNS가 아직 이 서버를 가리키지 않습니다 | A 또는 AAAA 이름과 값을 확인합니다. 퍼질 때까지 기다린 뒤 `dig`와 `nslookup`을 다시 실행합니다 |
| 브라우저 또는 curl이 인증서 오류 | HTTPS 인증서를 받지 못했습니다 | DNS가 이 서버를 가리키는지, 80번과 443번이 열려 있는지, Caddyfile의 이름이 `hook.example.com`인지, Caddy가 실행 중인지 봅니다 |
| 로컬 `8160`은 되는데 도메인은 안 됨 | 프록시 앞단입니다 | `uvicorn`이 `127.0.0.1:8160`에 있는지, Caddy의 `reverse_proxy`가 그 주소인지 봅니다. 8160을 인터넷에 열 필요는 없습니다 |
| 프로세스가 바로 종료 | 필수 설정이 비었거나 형식이 틀립니다 | `CUE_BRIDGE_TOKEN`과 `CUE_BRIDGE_HMAC_KEY`가 있는지 봅니다. 허용 목록의 각 항목과 `REPLY_TO_DEFAULT`는 메일 주소 하나여야 합니다 |

`422`는 쓰지 않습니다. 스키마 오류는 `400`입니다. 처리하지 못한 오류는 `500` `{"error":"internal"}`입니다.

## 8. 키를 지키는 법

- `CUE_BRIDGE_TOKEN`, `CUE_BRIDGE_HMAC_KEY`, `GROK_WEBHOOK_KEY`와 교체용 키는 서버의 `.env`에만 둡니다.
- 그 파일의 소유자는 `bridge:bridge`이고, `chmod 600`으로 그 서비스 계정만 읽게 합니다. 설치 디렉터리와 `.venv`의 소유자와는 다릅니다.
- 저장소, 채팅, 메일, Issue에 값을 붙이지 않습니다. `.env.example`의 `replace-with-` 문자열은 자리표시자입니다.
- 로그에는 토큰, HMAC 키, Grok 키, 서명, 본문, 요약을 남기지 않습니다.
- 키를 바꿀 때는 새 값을 `CUE_BRIDGE_TOKEN_SECONDARY`와 `CUE_BRIDGE_HMAC_KEY_SECONDARY`에 넣고 프로세스를 다시 시작합니다. 그 동안에는 이전 값과 새 값 모두 받습니다. 한 요청에는 받은 Bearer 중 하나와 받은 HMAC 키 중 하나가 모두 맞아야 합니다.
- Cue가 새 값만 쓰게 되면, 새 값을 primary로 옮기고 secondary를 비운 뒤 다시 시작합니다. 이전 값은 더 이상 받지 않습니다.
- Bearer와 HMAC는 항상 둘 다 필요합니다. 하나만 맞춰서는 `401`입니다.
