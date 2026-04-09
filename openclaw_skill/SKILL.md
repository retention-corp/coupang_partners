# OpenClaw Shopping Skill

Use the hosted shopping backend to request evidence-backed Coupang product recommendations without exposing affiliate secrets in the public skill package.

## Required setup

- Set `OPENCLAW_SHOPPING_BACKEND_URL` to the deployed backend base URL.
- Do not add Coupang affiliate keys to this skill package.
- When publishing affiliate links, include the required disclosure for your channel.

## Example flow

1. Accept the user's product request and optional budget/category hints.
2. Optionally gather user-visible evidence snippets (copied text or explicit browser snapshots).
3. Send the query to `POST /v1/assist` on the hosted backend.
4. Present the ranked shortlist, rationale, risks, and deeplinks.
