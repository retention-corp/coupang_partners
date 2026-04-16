# OpenClaw Shopping Skill Package

This public package is intentionally thin: it calls a hosted backend that owns
Coupang credentials, analytics, and recommendation learning.

The skill is meant to cover both recommendation-style prompts and direct
shopping search requests such as "쿠팡에서 AUX 선 제일 긴거 찾아줘".

It should also be discoverable when the user does not explicitly type
`shopping-copilot으로`, as long as the request clearly looks like shopping or
product-finding intent.

This includes taste-sensitive product requests such as books, masks, gadgets,
or other items where the user says things like `내 스타일`, `내 취향`, or
`나한테 맞는`.

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

Natural-language examples the router should pick up even without a skill prefix:

- `머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘`
- `대두가 써도 덜 아픈 KF94 마스크 추천해줘`
- `쿠팡에서 AUX 선 제일 긴 거 찾아줘`
- `쿠팡에서 요즘 볼만한 자기계발서 3개만 찾아줘. 내 스타일을 알아보고 추천해라`
- `내 취향에 맞는 책 3권만 쿠팡에서 골라줘`

## Local development override

Use a local backend only for operator testing, and only with the explicit non-production escape hatch.

```bash
export OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND="true"
export OPENCLAW_SHOPPING_BASE_URL="http://127.0.0.1:9883"
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-local-dev-token"
export OPENCLAW_SHOPPING_USE_INTERNAL_API="true"
```
