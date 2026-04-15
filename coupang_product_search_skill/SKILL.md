---
name: coupang-product-search
description: >-
  Search Coupang products through the repository's local coupang-mcp-compatible layer. Use when
  the user wants live product search, rocket-delivery filtering, budget search,
  best products, goldbox deals, or direct product comparison from Coupang MCP.
---

# Coupang Product Search

OpenClaw skill for the repository's built-in Coupang MCP-compatible tool layer.

## What this skill is for

- Direct Coupang product search
- Rocket-delivery-only search
- Budget-constrained search
- Product comparison
- Popular or seasonal recommendations
- Category best products and goldbox deals

## Hard rules

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
