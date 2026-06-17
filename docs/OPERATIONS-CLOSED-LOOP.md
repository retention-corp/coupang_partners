# Closed-Loop Operations

Goal: keep `https://a.retn.kr` usable by external agents while avoiding runaway
traffic, broken short links, and silent attribution loss.

Cloud Run service: `a-retn-shortener`

Production base URL: `https://a.retn.kr`

## Loop

Run this loop hourly by default and after every deploy:

```bash
python3 scripts/agent_closed_loop.py
```

The default loop is shallow and only checks `/health` plus `/openapi.json`.
It does not mint recommendations or short links.

Run a deep canary after deploys or on a low-frequency schedule:

```bash
python3 scripts/agent_closed_loop.py --deep --client-id smoke-test --surface cli
```

For operator checks with admin summary:

```bash
export OPENCLAW_SHOPPING_API_TOKEN="operator-token"
python3 scripts/closed_loop_ops.py
```

The public scheduled loop is intentionally low-cost. Deep mode performs one
assist request and one short-link HEAD check; keep it out of tight cron loops.

## GitHub Actions

`.github/workflows/agent-ops-check.yml` runs the shallow public closed-loop check
hourly. It does not need secrets. If operator tokens are added later, keep them
scoped to read-only admin summary and never print the token.

## Cost Guard

Use admin mode for threshold checks:

```bash
python3 scripts/closed_loop_ops.py \
  --assist-limit-per-day 100000 \
  --shortlink-limit-per-day 100000
```

These are cumulative guardrails, not billing-grade counters. If a guard trips:

1. Check which client id or surface grew.
2. Lower public rate limits or pause the noisy caller.
3. Keep public assist available for known clients if traffic is legitimate.
4. Verify `short_deeplink` and `/s/<slug>` still work after any mitigation.

## Failure Map

`cloudflare_blocked`

- Check Cloudflare WAF/Bot rules.
- Confirm Python/agent User-Agent is not blocked.
- Keep `/health`, `/openapi.json`, `/v1/public/*`, and `/s/*` reachable.

`client_not_allowlisted`

- Add exact client id such as `hermes-agent`.
- For OpenClaw forks, prefer prefix allowlist entries such as `openclaw-skill-*`.
- Re-run `python3 scripts/agent_smoke.py --client-id <id>`.

`rate_limited`

- Inspect whether callers are missing stable `X-OpenClaw-Client-Id`.
- Do not raise limits before confirming there is no retry storm.
- Tune `OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC` and window values.

`backend_empty_result`

- Check Coupang API credentials, quota, and product search response shape.
- Verify recommendation fallback behavior with local tests.

`short_link_missing`

- Confirm Cloud Run uses `OPENCLAW_SHOPPING_SHORTENER=firestore`.
- Confirm `OPENCLAW_SHOPPING_PUBLIC_BASE_URL=https://a.retn.kr`.
- Check Firestore write permissions for `OPENCLAW_SHORT_LINKS_COLLECTION`.

`short_link_redirect_failed`

- Check `/s/<slug>` route logs.
- Check Firestore short link document exists.
- Check `a.retn.kr` domain mapping and DNS.

`backend_error` or `network_error`

- Check Cloud Run revision health and recent deploys.
- Check Cloudflare, domain mapping, and service logs.
- Roll back to the last passing revision if a deploy caused the failure.

## Deploy Gate

Before deploy:

```bash
python3 -m unittest -q
python3 scripts/agent_closed_loop.py --allow-non-prod --base-url http://127.0.0.1:9883
```

After deploy:

```bash
python3 scripts/agent_smoke.py --client-id smoke-test --surface cli
python3 scripts/agent_closed_loop.py --deep --client-id smoke-test --surface cli
```

If operator routes are enabled on the ops service, also run:

```bash
OPENCLAW_SHOPPING_API_TOKEN="operator-token" \
python3 scripts/closed_loop_ops.py
```

## Recovery Ownership

Keep money and production deploys single-owner. Parallel agents can inspect
docs, logs, tests, and dashboards, but one root operator should own:

- rate-limit changes
- allowlist changes
- Cloudflare rule changes
- Cloud Run deploy or rollback
- Secret Manager changes

After recovery, prove completion with:

- `agent_smoke.py` ok
- `agent_closed_loop.py` ok
- one live `short_deeplink`
- one live `/s/<slug>` redirect
- Cloud Run log line for the expected client id
