# Coupang Partners Client and OpenClaw Shopping Docs

Minimal Python tooling for Coupang Partners integrations using only the standard library.

This repository currently ships a reusable Coupang Partners client and the documentation package for the planned OpenClaw hosted-shopping flow described in `.omx/plans/prd-openclaw-shopping-backend.md`.

## What exists today

- HMAC-SHA256 authorization header generation
- Common JSON request helper
- `POST /deeplink`
- `POST /openapi/v2/products/reco`
- Thin wrappers for documented `products/*` and `reports/*` endpoints
- Documentation for the hosted backend, operator guardrails, and public OpenClaw skill handoff

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

## Hosted backend plan

The approved backend work keeps economic capture in a hosted service instead of embedding Coupang credentials in a public skill package.

Planned public backend endpoints:

- `GET /healthz`
- `POST /v1/assist`
- `POST /v1/events`
- `GET /v1/admin/summary`

Recommended supporting documents in this repo:

- `docs/openclaw-shopping-backend.md` — deployment model, data flow, guardrails, and manual smoke-test checklist
- `openclaw_skill/README.md` — public skill packaging and installation notes
- `openclaw_skill/SKILL.md` — operator-facing skill instructions for OpenClaw usage

## OpenClaw integration expectations

The public OpenClaw skill should:

1. Accept a shopping request plus optional budget/category hints.
2. Send the request to a hosted backend owned by the operator.
3. Return grounded recommendations, rationale, caveats, and deeplinks.
4. Never request or expose Coupang affiliate secrets client-side.

See `openclaw_skill/README.md` for the public-skill contract.

## Compliance and product guardrails

- Keys are intentionally not stored in this repository.
- Signatures are generated from `signed-date + HTTP_METHOD + path + raw_querystring`.
- `Reco v2` should be preferred for new work; `Reco v1` is kept only as a thin compatibility wrapper.
- `impressionUrl` returned by `Reco v2` must be triggered only when the recommendation is actually visible to a real user.
- Affiliate disclosure is still required anywhere generated links are published.
- Evidence ingestion should stay explicit, auditable, and user- or operator-supplied.

## Tests

```bash
python3 -m unittest
```
