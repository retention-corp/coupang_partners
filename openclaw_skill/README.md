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
- Closed beta clients are pinned to `https://a.retn.kr` even if a stale local backend override is present.
- Public skill calls are tokenless by default and use the hosted public assist path.
- Keep Coupang secrets server-side.
- Preserve affiliate disclosure wherever links are shown.
- Only operators should set `OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND=true` for local or staging backend tests.
- Only operators should set `OPENCLAW_SHOPPING_USE_INTERNAL_API=true` together with a bearer token.

## Example

```bash
python3 scripts/openclaw-shopping-skill.py recommend \
  --backend https://a.retn.kr \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000 \
  --limit 3
```

The `recommend` command posts to `/v1/public/assist` on the hosted backend by default.
Closed beta traffic should always resolve to the hosted production URL.

The `deeplinks` command is operator-only and uses internal routes. Public recommendation responses already include action-ready links.

## Local development override

Use a local backend only for operator testing, and only with the explicit non-production escape hatch.

```bash
export OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND="true"
export OPENCLAW_SHOPPING_BASE_URL="http://127.0.0.1:9883"
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-local-dev-token"
export OPENCLAW_SHOPPING_USE_INTERNAL_API="true"
```
