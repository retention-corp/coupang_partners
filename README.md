# Coupang Partners Client

Minimal Python client for Coupang Partners Open API using only the standard library.

## What it covers

- HMAC-SHA256 authorization header generation
- Common JSON request helper
- `POST /deeplink`
- `POST /openapi/v2/products/reco`
- Thin wrappers for documented `products/*` and `reports/*` endpoints

## Environment variables

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

## Notes

- Keys are intentionally not stored in this repository.
- Signatures are generated from `signed-date + HTTP_METHOD + path + raw_querystring`.
- `Reco v2` should be preferred for new work; `Reco v1` is kept only as a thin compatibility wrapper.
- `impressionUrl` returned by `Reco v2` must be triggered only when the recommendation is actually visible to a real user.

## Tests

```bash
python3 -m unittest coupang_partners.test_client
```
