# Agentic Operations Runbook

Goal: keep `https://a.retn.kr` usable by OpenClaw, Hermes, Codex, ChatGPT GPTs,
Claude-style skills, and MCP clients without letting monitoring or retries
create an avoidable cost spike.

## Closed Loop

1. Shallow check every 5 minutes:

   ```bash
   python3 scripts/agent_closed_loop.py --client-id smoke-test
   ```

2. Deep canary after deploy and every 1-6 hours:

   ```bash
   python3 scripts/agent_closed_loop.py --client-id smoke-test --deep
   ```

3. Treat the JSON output as the source of truth:

   - `status=healthy`: no action needed.
   - `status=needs_recovery`: read `recovery` first.
   - `operator_commands`: commands that may be run by an operator; the script
     does not redeploy automatically.
   - `cost_guard`: cadence, Cloud Run cap, rate-limit, and public path contract.

## Cost Guardrails

- Public rate limit default: 12 requests per 60 seconds.
- Cloud Run deploy default: capped max instances via `MAX_INSTANCES`, currently
  defaulting to 2 in `scripts/deploy_gcp_cloud_run.sh`.
- Response cache default: 900 seconds.
- Shallow checks call only `/health` and `/openapi.json`.
- Deep checks use `limit=1` and must not be retried in a tight loop.
- Never print `COUPANG_*` keys or bearer tokens in monitor logs.

## Recovery Matrix

| Failure code | First response |
|---|---|
| `cloudflare_blocked` | Restore agent User-Agent allow rule; verify Python `urllib` clients. |
| `client_not_allowlisted` | Add the client id or use `hermes-agent`, `codex`, `openclaw-skill`, or `claw-*`. |
| `rate_limited` | Back off; rerun shallow first; avoid repeated deep checks. |
| `openapi_contract_mismatch` | Redeploy latest backend or roll back; keep docs/OpenAPI/routes aligned. |
| `short_deeplink_missing` | Check Coupang credentials, shortener provider, and Firestore permissions. |
| `shortlink_not_redirecting` | Inspect `short_links` storage and Cloud Run logs around `/s/<slug>`. |
| `network_error` | Check DNS, Cloudflare, and Cloud Run service availability. |

## Deploy Gate

Before deploy:

```bash
python3 -m unittest -q
python3 scripts/agent_closed_loop.py --allow-non-prod --base-url http://127.0.0.1:9883
```

Deploy:

```bash
RUN_SMOKE_TEST_AFTER_DEPLOY=true scripts/deploy_gcp_cloud_run.sh
```

After deploy:

```bash
python3 scripts/agent_closed_loop.py --client-id smoke-test
python3 scripts/agent_closed_loop.py --client-id smoke-test --deep
```

If deep check fails, do not loop. Read the Cloud Run logs:

```bash
gcloud run services logs read a-retn-shortener \
  --project retn-kr-website \
  --region us-central1 \
  --limit 100
```

## Agent Onboarding Checklist

- Agent can fetch `/openapi.json`.
- Agent sends `User-Agent`.
- Agent sends stable `X-OpenClaw-Client-Id`.
- Agent preserves affiliate disclosure.
- Agent uses `short_deeplink` for user-facing links.
- Agent treats 429 as a backoff signal.
- Agent does not request Coupang keys.
