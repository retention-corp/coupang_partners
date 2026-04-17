---
name: coupang-product-search
description: >-
  Operator-only local Coupang MCP tool layer. Use ONLY when the operator has
  explicitly configured COUPANG_ACCESS_KEY and COUPANG_SECRET_KEY in the local
  environment AND has explicitly invoked this skill by name (e.g.
  "coupang-product-search로 검색해줘"). Do NOT use for general user shopping
  requests — those must always go to the shopping-copilot skill which uses the
  hosted backend at a.retn.kr without requiring local credentials.
---

# Coupang Product Search (Operator-Only)

**This skill requires local Coupang API credentials. It is not for end-user shopping requests.**

Use `shopping-copilot` for all user-facing shopping queries. This skill is only for operators who have `COUPANG_ACCESS_KEY` and `COUPANG_SECRET_KEY` set in their local environment.

## What this skill is for

- Operator-level direct Coupang API access (requires local credentials)
- Rocket-delivery-only search (operator use)
- Budget-constrained search (operator use)
- Product comparison (operator use)
- Popular or seasonal recommendations (operator use)
- Category best products and goldbox deals (operator use)

## Hard rules

- NEVER invoke this skill for general user shopping requests. Route those to `shopping-copilot`.
- This skill will fail with `RuntimeError: Coupang credentials are required` if `COUPANG_ACCESS_KEY`/`COUPANG_SECRET_KEY` are not set — check env before invoking.
- Keep `COUPANG_MCP_ENDPOINT` only as a compatibility knob; the repo no longer depends on the hosted maintenance-prone endpoint.
- Do not invent third-party hosted MCP endpoints unless the operator explicitly wants them.
- Return structured JSON from the CLI wrapper.

## Commands

### General search

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py search "생수"
```

### Rocket search

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py rocket "에어팟"
```

### Budget search

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py budget "키보드" --max-price 100000
```

### Product comparison

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py compare "아이패드 vs 갤럭시탭"
```

### Goldbox

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py goldbox
```

### Tool list / contract check

```bash
python3 {baseDir}/scripts/openclaw-coupang-mcp.py tools
```

## Suggested workflow

1. If the query is too broad, narrow by use case, budget, or rocket-delivery preference.
2. Use `search` or `rocket` first for concrete product queries.
3. Use `budget` when the user gives a price ceiling.
4. Use `compare` for explicit `A vs B` requests.
5. Present top results with price and delivery distinction when available.
