---
name: shopping-copilot
description: >-
  Hosted shopping copilot that turns a natural-language shopping request into
  evidence-backed recommendations and affiliate deeplinks. Use when the user
  wants an agent to reason about product fit or directly search shopping
  results. Trigger even without the explicit `shopping-copilot` prefix when the
  user is clearly asking to buy, compare, search, or recommend a physical
  product. Strong triggers include 쇼핑 추천, 상품 추천, 뭐 사야 해, 골라줘,
  추천해줘, 찾아줘, 검색해줘, 보여줘, 링크 줘, 어디서 사, 쿠팡에서, 가성비,
  최저가, 제일 긴, 제일 싼, 평점 높은, 리뷰 많은, big size/대두/큰 머리,
  착용감, 편하게 쓸 수 있는, 마스크, 청소기, 양말, 케이블, 오트밀크, 책,
  도서, 자기계발서, 소설, 경제경영, 내 스타일, 내 취향, 나한테 맞는 같은
  product-seeking phrases. Also triggers on 골드박스, 오늘 특가, 지금 특가,
  타임딜, 당일 특가, 베스트, 인기상품, 카테고리 베스트, 전자제품 베스트,
  로켓배송만, 로켓만, 로켓으로, 로켓 상품만. Example shopping queries include
  "30만원 이하 무선청소기, 소음 적고 원룸용", "쿠팡에서 AUX 선 제일 긴거
  제품 찾아줘", "머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크
  찾아줘", "쿠팡에서 요즘 볼만한 자기계발서 3개만 찾아줘. 내 스타일을 알아보고
  추천해라", "오늘 골드박스 뭐야?", "전자제품 베스트 보여줘",
  "로켓배송만 무선청소기 10만원 이하".
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
- Working even when the user does not explicitly say `shopping-copilot으로`, as long as the request is clearly a shopping/product-finding request
- Handling comparison-style requests such as longest, cheapest, highest-rated, and most-reviewed products
- Handling book and content-like product requests when the user is still clearly asking what to buy on Coupang
- Handling style-aware requests such as `내 스타일`, `내 취향`, `나한테 맞는` as shopping personalization rather than generic life advice

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

### 3) Structured search (rocket filter / budget / sort)

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py search \
  --backend https://a.retn.kr \
  --keyword "무선청소기" \
  --rocket-only \
  --max-price 300000 \
  --sort SALE \
  --limit 5
```

- `--sort` options: `SIM` (관련성), `SALE` (인기), `LOW` (낮은가격), `HIGH` (높은가격)
- Use `search` when the user says "로켓배송만", "로켓만", or specifies a budget ceiling alongside a keyword.

### 4) Goldbox — today's deals

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py goldbox \
  --backend https://a.retn.kr
```

- Use when the user says "골드박스", "오늘 특가", "지금 특가", "타임딜", "당일 특가".

### 5) Category best products

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py best \
  --backend https://a.retn.kr \
  --category "전자제품"
```

- Supported category names: 여성패션, 남성패션, 유아동패션, 신발/패션잡화, 뷰티, 출산/유아동, 식품, 주방용품, 생활용품, 문구/오피스, 가전디지털 (전자제품), 스포츠/레저, 자동차용품
- Use when the user says "베스트", "인기상품", or "카테고리 베스트".

### 2) Build deeplinks

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py deeplinks \
  --backend https://a.retn.kr \
  --url https://www.coupang.com/vp/products/123
```

## Suggested agent workflow

Route based on intent first:

| User says | Command to use |
|---|---|
| "골드박스", "오늘 특가", "타임딜" | `goldbox` |
| "베스트", "카테고리 베스트", "인기상품" | `best --category ...` |
| "로켓배송만", "로켓만" + keyword | `search --rocket-only` |
| keyword + budget ceiling | `search --max-price` |
| keyword + "인기순"/"리뷰많은" | `search --sort SALE` |
| keyword + "최저가"/"제일 싼" | `search --sort LOW` |
| Natural language recommendation | `recommend` |

1. For goldbox/best: call directly, no clarifying questions needed.
2. For search: extract keyword, rocket preference, price ceiling, and sort intent from the user’s text.
3. For recommend: ask for constraints if missing (budget, use case, size).
4. Prefer the hosted backend from `OPENCLAW_SHOPPING_BASE_URL`; default is `https://a.retn.kr`.
5. Always include `short_deeplink` (prefer over `deeplink`) for each result.
6. If the user asks for books or taste-sensitive products, use `recommend` and personalize within the shopping flow.

## Trigger examples

- `쿠팡에서 AUX 선 제일 긴거 찾아줘`
- `쿠팡에서 3.5mm 연장선 긴 거 보여줘`
- `쿠팡에서 제일 싼 무선 마우스 찾아줘`
- `무선청소기 추천해줘`
- `머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘`
- `대두가 써도 안 아픈 KF94 마스크 추천해줘`
- `브레빌 870으로 라떼 만들 오트밀크 골라줘`
- `쿠팡에서 요즘 볼만한 자기계발서 3개만 찾아줘. 내 스타일을 알아보고 추천해라`
- `내 취향에 맞는 책 3권만 쿠팡에서 골라줘`

## Backend compatibility

The hosted backend should support:

- `POST /v1/public/assist`
- `GET /health`
