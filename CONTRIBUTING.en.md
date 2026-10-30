[한국어](CONTRIBUTING.md) | English

# Contributing

Checks run locally only. This repository does not use GitHub Actions; do not add or re-enable workflows.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
cp .env.example .env
pytest
```

`.env.example` contains placeholders. Never commit `.env` or real tokens, HMAC keys, or Grok keys.

When bumping the version, update `VERSION`, `pyproject.toml`, `__version__` in `app/__init__.py`, `CHANGELOG.md`, and `CHANGELOG.en.md` together.
