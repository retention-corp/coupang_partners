---
name: openclaw-shopping
summary: Thin public skill for querying a hosted Coupang-backed shopping assistant without exposing affiliate secrets.
---

# OpenClaw Shopping Skill

Use this skill when a user wants a product recommendation from the operator-hosted shopping backend.

## Goals

1. Capture the user's shopping intent clearly.
2. Send only request data that the hosted backend needs.
3. Return grounded recommendations with rationale and caveats.
4. Preserve the backend moat by never exposing operator secrets.

## Inputs to gather

- shopping query in natural language
- optional budget or price ceiling
- category or brand preferences
- hard exclusions
- optional evidence snippets the user explicitly wants considered

## Behavioral rules

- Never request `COUPANG_ACCESS_KEY` or `COUPANG_SECRET_KEY` from the user.
- Prefer explicit constraints over inferred assumptions.
- Present uncertainty honestly when evidence is weak.
- Include caveats alongside recommendations.
- Remind the operator to provide affiliate disclosure in user-facing surfaces that publish links.

## Hosted-backend expectation

The backend should expose:

- `GET /healthz`
- `POST /v1/assist`
- `POST /v1/events`

Recommended configuration:

```bash
export OPENCLAW_SHOPPING_BASE_URL="https://your-hosted-backend.example.com"
export OPENCLAW_SHOPPING_TIMEOUT_SECONDS="30"
```

## Suggested request shape

```json
{
  "query": "wireless noise cancelling headphones for flights",
  "budget": 250000,
  "constraints": {
    "category": "headphones",
    "must_have": ["noise cancelling", "bluetooth multipoint"],
    "avoid": ["on-ear"]
  },
  "evidence_snippets": [
    "User prefers long battery life and USB-C charging."
  ]
}
```

## Expected response shape

Return a concise shortlist with:

- item name
- why it was recommended
- notable risks or tradeoffs
- deeplink URL

## Operator note

If the CLI entrypoint or environment-variable names change during implementation, update this skill file and `openclaw_skill/README.md` in the same change.
