# OpenClaw Shopping Skill Package

This public package is intentionally thin: it calls a hosted backend that owns
Coupang credentials, analytics, and recommendation learning.

## Files

- `SKILL.md` — operator-facing OpenClaw instructions
- `_meta.json` — lightweight package metadata
- `scripts/openclaw-shopping-skill.py` — JSON CLI wrapper around the hosted backend

## Required setup

- Set `OPENCLAW_SHOPPING_BASE_URL` or pass `--backend`.
- Keep Coupang secrets server-side.
- Preserve affiliate disclosure wherever links are shown.

## Example

```bash
python3 scripts/openclaw-shopping-skill.py recommend \
  --backend http://127.0.0.1:8765 \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000 \
  --limit 3
```

The `recommend` command posts to `/v1/recommendations`, which is supported as an alias of `/v1/assist` on the hosted backend.
