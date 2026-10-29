한국어 | [English](CONTRIBUTING.en.md)

# 기여

로컬에서만 확인합니다. 이 저장소는 GitHub Actions를 쓰지 않으며, 워크플로를 추가하거나 다시 켜지 않습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
pytest
```

`.env.example`은 자리표시자입니다. `.env`와 실제 토큰, HMAC 키, Grok 키는 커밋하지 않습니다.
