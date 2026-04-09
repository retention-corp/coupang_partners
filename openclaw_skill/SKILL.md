---
name: shopping-copilot
description: >-
  Hosted shopping copilot that turns a natural-language shopping request into
  evidence-backed recommendations and affiliate deeplinks. Use when the user
  wants an agent to reason about product fit, not just search.
metadata: {"clawdbot":{"emoji":"🛒","requires":{"bins":["python3"]}}}
---

# Shopping Copilot

Hosted shopping copilot for OpenClaw.

## What this skill is for

- Accepting a natural-language shopping request
- Asking the hosted backend for grounded recommendations
- Returning structured JSON with evidence, risks, and affiliate links

## Hard rules

- This skill never exposes Coupang credentials.
- This skill always uses the hosted backend.
- When returning recommendations, preserve the affiliate disclosure text.

## Output format

All commands print JSON to stdout.

- Success: `{ "ok": true, "data": ... }`
- Failure: `{ "ok": false, "error": { "message": "..." } }`

## Commands

### 1) Recommend products

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py recommend \
  --backend http://127.0.0.1:8765 \
  --query "30만원 이하 무선청소기, 소음 적고 원룸용" \
  --price-max 300000 \
  --limit 5
```

### 2) Build deeplinks

```bash
python3 {baseDir}/scripts/openclaw-shopping-skill.py deeplinks \
  --backend http://127.0.0.1:8765 \
  --url https://www.coupang.com/vp/products/123
```

## Suggested agent workflow

1. Ask for the user’s actual shopping constraints.
2. Start with `recommend`.
3. Present the best 1–3 recommendations with evidence and risks.
4. Use `deeplinks` only when the user wants action-ready links.

## Backend compatibility

The hosted backend should support:

- `POST /v1/assist`
- `POST /v1/deeplinks`
- `POST /v1/events`
- `GET /healthz`
