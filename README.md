# Coupang Partners Client and OpenClaw Shopping Backend

Minimal Python tooling for Coupang Partners integrations using only the standard library.

This repository now ships:

- a reusable Coupang Partners API client
- a dependency-light hosted shopping backend
- a sqlite-backed analytics loop for recommendation feedback
- a public OpenClaw skill package under `openclaw_skill/`
- a thin CLI bridge under `bin/openclaw_shopping.py`

## What exists today

- HMAC-SHA256 authorization header generation
- Common JSON request helper
- `POST /deeplink`
- `POST /openapi/v2/products/reco`
- Thin wrappers for documented `products/*` and `reports/*` endpoints
- Hosted shopping backend via `backend.py`
- Recommendation engine and analytics modules at the repository root
- Public backend contract artifacts under `openclaw_skill/`
- Tests for backend, recommendation, analytics, CLI, and client flows

## Coupang API environment variables

```bash
export COUPANG_ACCESS_KEY="your-access-key"
export COUPANG_SECRET_KEY="your-secret-key"
```

## Quick start

```python
from coupang_partners import CoupangPartnersClient

client = CoupangPartnersClient.from_env()

deeplink_response = client.deeplink(
    [
        "https://www.coupang.com/np/search?component=&q=good&channel=user",
        "https://www.coupang.com/np/coupangglobal",
    ]
)

reco_payload = client.minimal_reco_v2_payload(
    site_id="my-site",
    site_domain="example.com",
    device_id="device-id-or-ad-id",
    image_size="200x200",
    user_puid="user-123",
)
reco_response = client.get_reco_v2(reco_payload)
```

## Hosted backend

The backend keeps economic capture in a hosted service instead of embedding
Coupang credentials in a public skill package.

Hosted-first recommendation:

- Public skills and thin clients should default to `https://a.retn.kr`
- Local backend execution is for operator development only
- Affiliate deeplink creation should stay server-side so published links keep the operator's attribution

Run it with:

```bash
export COUPANG_ACCESS_KEY="your-access-key"
export COUPANG_SECRET_KEY="your-secret-key"
export OPENCLAW_SHOPPING_API_TOKENS="replace-with-random-long-token"
export OPENCLAW_SHOPPING_DB_PATH=".data/openclaw-shopping.sqlite3"
python3 backend.py
```

Public endpoints:

- `GET /health`
- `POST /v1/assist`
- `POST /v1/events`

Protected endpoints require:

- `Authorization: Bearer <token>` when `OPENCLAW_SHOPPING_API_TOKENS` is configured
- optional `X-OpenClaw-Client-Id` for caller identification
- `GET /v1/admin/summary`

Optional hardening env vars:

- `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED=true` to enforce caller allowlisting
- `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST=shopping-copilot,discord-bot` for approved client IDs
- `OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC` / `OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_PUBLIC`
- `OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_AUTH` / `OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_AUTH`
- `OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_ADMIN` / `OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_ADMIN`

Client compatibility notes:

- Thin clients accept both `OPENCLAW_SHOPPING_API_TOKEN` and `OPENCLAW_SHOPPING_API_TOKENS`
- Thin clients accept `OPENCLAW_SHOPPING_BASE_URL`, `OPENCLAW_SHOPPING_BACKEND_URL`, or `SHOPPING_COPILOT_BASE_URL`

Default protection:

- request payload limits on `query` and `evidence_snippets`
- in-process rate limiting
- optional per-client allowlist enforcement
- optional separate public/auth/admin rate-limit buckets
- deeplink host allowlist (`coupang.com`, `link.coupang.com`, `www.coupang.com`)

Built-in short-link provider for local development:

```bash
export OPENCLAW_SHOPPING_SHORTENER="builtin"
export OPENCLAW_SHOPPING_PUBLIC_BASE_URL="https://go.example.com"
```

If `OPENCLAW_SHOPPING_PUBLIC_BASE_URL` points at a non-local host, also set
`OPENCLAW_SHOPPING_API_TOKEN` or `OPENCLAW_SHOPPING_API_TOKENS`, because the
backend treats that as a protected deployment shape.

Local development is intentionally a separate override path. Public docs and install defaults should keep pointing at `https://a.retn.kr`.

When enabled, recommendation responses include `short_deeplink`, and `/v1/deeplinks` includes `shortenedShareUrl`.
The backend also exposes `GET /s/<slug>` and redirects to the original affiliate URL.

Shared Firestore short-link provider for Cloud Run:

```bash
export OPENCLAW_SHOPPING_SHORTENER="firestore"
export OPENCLAW_SHOPPING_ANALYTICS_PROVIDER="firestore"
export GOOGLE_CLOUD_PROJECT="retn-kr-website"
export OPENCLAW_SHOPPING_PUBLIC_BASE_URL="https://a.retn.kr"
export OPENCLAW_SHORT_LINKS_COLLECTION="short_links"
export OPENCLAW_ANALYTICS_COLLECTION_PREFIX="shopping"
```

Notes:

- Firestore mode keeps short-link slugs durable across Cloud Run instance replacement.
- Firestore analytics mode keeps query/recommendation/event summaries shared across instances.
- If `FIRESTORE_EMULATOR_HOST` is set, the backend talks to the emulator without OAuth.
- If Firestore short-link generation fails, the backend now falls back to the original affiliate URL instead of dropping the request.

Sample request:

```bash
curl -sS -X POST https://a.retn.kr/v1/assist \
  -H 'Authorization: Bearer replace-with-random-long-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "30만원 이하 무선청소기, 원룸용",
    "constraints": {
      "must_have": ["저소음", "원룸"],
      "avoid": ["대형"]
    },
    "evidence_snippets": [
      {"text": "리뷰: 원룸에서 쓰기 좋음", "source": "manual"},
      {"text": "리뷰: 비교적 조용한 편", "source": "manual"}
    ]
  }'
```

CLI bridge example:

```bash
export OPENCLAW_SHOPPING_BASE_URL="https://a.retn.kr"
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-random-long-token"

python3 bin/openclaw_shopping.py \
  "30만원 이하 무선청소기, 원룸용" \
  --must-have 저소음 \
  --must-have 원룸 \
  --avoid 대형 \
  --evidence-snippet "리뷰: 자취방에서 쓰기 좋은 보조 청소기"
```

## OpenClaw integration expectations

The public OpenClaw skill should:

1. Accept a shopping request plus optional budget/category hints.
2. Send the request to a hosted backend owned by the operator.
3. Return grounded recommendations, rationale, caveats, and deeplinks.
4. Never request or expose Coupang affiliate secrets client-side.

See `openclaw_skill/README.md` for the public contract.

## Compliance and product guardrails

- Keys are intentionally not stored in this repository.
- Signatures are generated from `signed-date + HTTP_METHOD + path + raw_querystring`.
- `Reco v2` should be preferred for new work; `Reco v1` is kept only as a thin compatibility wrapper.
- `impressionUrl` returned by `Reco v2` must be triggered only when the recommendation is actually visible to a real user.
- Affiliate disclosure is still required anywhere generated links are published.
- Evidence ingestion should stay explicit, auditable, and degrade gracefully when page evidence is weak.
- The backend should not claim review/detail analysis when only metadata-level evidence is available.
- Low-confidence recommendation summaries should remain conservative and explicitly say when fit is metadata-only.
- Never commit real Coupang keys or bearer tokens to this repository. Use runtime env vars or Secret Manager only.

## Tests

```bash
python3 -m unittest -q
```
