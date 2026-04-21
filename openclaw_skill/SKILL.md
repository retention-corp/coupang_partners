---
name: shopping-copilot
description: >-
  Hosted shopping copilot that turns a natural-language shopping request into
  evidence-backed recommendations and affiliate deeplinks. Use when the user
  wants an agent to reason about product fit or directly search shopping
  results. Trigger when the user asks for 쇼핑 추천, 상품 추천, 뭐 사야 해,
  골라줘, 추천해줘, 찾아줘, 검색해줘, 보여줘, 링크 줘, or gives a natural-language
  shopping query such as "30만원 이하 무선청소기, 소음 적고 원룸용" or
  "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘".
metadata: {"clawdbot":{"emoji":"🛒","requires":{"bins":["python3"]}}}
---

# Shopping Copilot

Hosted shopping copilot for OpenClaw.

## What this skill is for

- Accepting a natural-language shopping request
- Handling direct Korean shopping search requests, not only recommendation prompts
- Asking the hosted backend for grounded recommendations
- Returning structured JSON with evidence, risks, and affiliate links
- Matching direct Korean shopping intents without requiring explicit command syntax
- Handling comparison-style requests such as longest, cheapest, highest-rated, and most-reviewed products

## Hard rules

- This skill never exposes Coupang credentials.
- This skill defaults to the hosted backend at `https://a.retn.kr`.
- Closed beta traffic is pinned to `https://a.retn.kr` unless the operator explicitly enables `OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND=true`.
- Public skill traffic should use the hosted public gateway path without requiring an operator bearer token.
- When the operator explicitly enables `OPENCLAW_SHOPPING_USE_INTERNAL_API=true`, this skill should send `OPENCLAW_SHOPPING_API_TOKEN` as a bearer token.
- When returning recommendations, preserve the affiliate disclosure text.
- When returning recommendations, always include the action-ready affiliate link for each recommended item. Prefer `short_deeplink`, then `deeplink`.

## Output format

All commands print JSON to stdout.

- Success: `{ "ok": true, "data": ... }`
- Failure: `{ "ok": false, "error": { "message": "..." } }`

## Commands

### 1) Recommend products

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py recommend \
  --backend https://a.retn.kr \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000 \
  --limit 5
```

### 2) Build deeplinks

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py deeplinks \
  --backend https://a.retn.kr \
  --url https://www.coupang.com/vp/products/123
```

### 3) View goldbox deals

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py goldbox \
  --backend https://a.retn.kr
```

### 4) View category best products

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py best-products \
  --backend https://a.retn.kr \
  --category-id 1016
```

## Suggested agent workflow

1. Ask for the user’s actual shopping constraints.
2. Start with `recommend` for both recommendation-style and direct search-style shopping requests.
3. Prefer the hosted backend from `OPENCLAW_SHOPPING_BASE_URL`; in closed beta, stale localhost overrides must still resolve to `https://a.retn.kr`.
4. If no backend env is set, use the hosted default `https://a.retn.kr`.
5. Present the best 1–3 recommendations with evidence, risks, and direct purchase links.
6. Use `deeplinks` only when the user wants action-ready links.

## Trigger examples

- `쿠팡에서 AUX 선 제일 긴거 찾아줘`
- `쿠팡에서 3.5mm 연장선 긴 거 보여줘`
- `쿠팡에서 제일 싼 무선 마우스 찾아줘`
- `무선청소기 추천해줘`

## Backend compatibility

The hosted backend should support:

- `POST /v1/public/assist`
- `GET /health`
- `GET /v1/public/goldbox`
- `GET /v1/public/best-products?categoryId=...`
