# OpenClaw Shopping Skill Package

This public package is intentionally thin: it calls a hosted backend that owns
Coupang credentials, analytics, and recommendation learning.

The skill is meant to cover both recommendation-style prompts and direct
shopping search requests such as "쿠팡에서 AUX 선 제일 긴거 찾아줘".

## Files

- `SKILL.md` — operator-facing OpenClaw instructions
- `_meta.json` — lightweight package metadata
- `scripts/openclaw-shopping-skill.py` — JSON CLI wrapper around the hosted backend

## Required setup

- Hosted default backend is `https://a.retn.kr`.
- Override `OPENCLAW_SHOPPING_BASE_URL` or pass `--backend` only when you intentionally run a different backend.
- Set `OPENCLAW_SHOPPING_API_TOKEN` when the backend requires bearer auth.
- Keep Coupang secrets server-side.
- Preserve affiliate disclosure wherever links are shown.

## Example

```bash
python3 scripts/openclaw-shopping-skill.py recommend \
  --backend https://a.retn.kr \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000 \
  --limit 3
```

The `recommend` command posts to `/v1/recommendations`, which is supported as an alias of `/v1/assist` on the hosted backend.
Prefer `OPENCLAW_SHOPPING_BASE_URL` or the hosted production URL over `127.0.0.1` unless you are intentionally testing a local backend.

## Local development override

Use a local backend only for operator testing.

```bash
export OPENCLAW_SHOPPING_BASE_URL="http://127.0.0.1:9883"
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-local-dev-token"
```
