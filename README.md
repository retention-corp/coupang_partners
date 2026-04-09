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

Run it with:

```bash
export COUPANG_ACCESS_KEY="your-access-key"
export COUPANG_SECRET_KEY="your-secret-key"
export OPENCLAW_SHOPPING_DB_PATH=".data/openclaw-shopping.sqlite3"
python3 backend.py
```

Public endpoints:

- `GET /healthz`
- `POST /v1/assist`
- `POST /v1/events`
- `GET /v1/admin/summary`

Sample request:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/assist \
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
export OPENCLAW_SHOPPING_BASE_URL="http://127.0.0.1:8765"

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

## Tests

```bash
python3 -m unittest -q
```
