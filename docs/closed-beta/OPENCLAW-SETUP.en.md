# OpenClaw Beta Setup

This document is the agent/operator-facing setup contract for the closed beta.

## Goal

Make every beta installation hit the hosted backend `https://a.retn.kr` by default, with no local Coupang credentials on the client side.

## Required runtime env

Set these in the OpenClaw runtime environment:

```json
{
  "OPENCLAW_SHOPPING_BASE_URL": "https://a.retn.kr",
  "OPENCLAW_SHOPPING_API_TOKEN": "issued-per-beta-user-or-shared-beta-token"
}
```

## Required behavior

- Default to the hosted backend `https://a.retn.kr`
- Never require beta users to set `COUPANG_ACCESS_KEY` or `COUPANG_SECRET_KEY`
- Keep link generation server-side
- Always include a direct purchase link when recommendations are returned
- Prefer `short_deeplink`, then fall back to `deeplink`

## Local override rule

Only operators should override the backend for local testing.

Example:

```bash
export OPENCLAW_SHOPPING_BASE_URL="http://127.0.0.1:9883"
export OPENCLAW_SHOPPING_API_TOKEN="local-dev-token"
```

Do not present local override instructions as the primary beta path.

## Session hygiene

- Prefer fresh Discord threads for important verification
- Avoid relying on stale thread memory when backend routing changed recently
- If a reply mentions `127.0.0.1`, treat it as a routing/config/session-memory bug

## Acceptance checks

- Recommendation replies mention hosted purchase links, not localhost links
- Queries across arbitrary product categories still return category-relevant results
- Beta users do not need any Coupang secrets locally
- Hosted backend remains the default path even when users simply install the repo and skill
