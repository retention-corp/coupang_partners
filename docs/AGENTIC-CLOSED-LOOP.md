# Agentic Closed Loop

Goal: any supported agent can discover the backend, call it without secrets,
receive a short affiliate link, and get an actionable recovery plan when the
workflow breaks.

## Loop Shape

1. Shallow monitor checks `GET /health` and `GET /openapi.json`.
2. Deep canary checks `POST /v1/public/assist` with `limit=1`.
3. Deep canary verifies the returned `short_deeplink` redirects with 30x.
4. The script emits machine-readable JSON with `status`, `checks`,
   `cost_guard`, and `recovery`.
5. Operators or a scheduler alert on non-zero exit and follow the recovery
   actions in order.

## Commands

Shallow, cheap:

```bash
python3 scripts/agent_closed_loop.py --base-url https://a.retn.kr
```

Deep, after deploy or on a low-frequency timer:

```bash
python3 scripts/agent_closed_loop.py \
  --base-url https://a.retn.kr \
  --client-id smoke-test \
  --surface cli \
  --deep
```

Local dev:

```bash
python3 scripts/agent_closed_loop.py \
  --base-url http://127.0.0.1:9883 \
  --allow-non-prod \
  --deep
```

## Cost Guard

- Default monitor mode is shallow.
- Deep canary is one assist request with `limit=1` plus one short-link redirect.
- Do not auto-retry deep mode on `429`.
- Recommended cadence: shallow every 5 minutes, deep every 1 to 6 hours and
  after deploy.
- Cloud Run deploy defaults: `MIN_INSTANCES=0`, `MAX_INSTANCES=2`,
  public rate limit `12/60s`, response cache TTL `900s`.
- Keep `MAX_INSTANCES` capped unless there is a deliberate launch window.

## Recovery Map

- `cloudflare_blocked`: restore Cloudflare allow rule for Python/agent
  User-Agent traffic, then rerun shallow and one deep canary.
- `client_not_allowlisted`: update `ALLOWED_CLIENT_IDS` or
  `OPENCLAW_SHOPPING_CLIENT_ALLOWLIST`; expected SA list is
  `openclaw-skill,openclaw-skill-*,local-cli,smoke-test,agent-smoke,closed-loop-monitor,github-actions-agent-ops,coupang-mcp-fallback,hermes-agent,codex,claude-code-skill,chatgpt-gpt,claw-*`.
- `rate_limited`: stop deep retries, wait at least one public rate-limit window,
  and reduce scheduler cadence if it repeats.
- `openapi_contract_mismatch` or `route_missing`: redeploy latest known-good
  revision or roll back; docs, backend routes, and OpenAPI must match.
- `short_deeplink_missing` or `shortlink_not_redirecting`: inspect Cloud Run
  logs for `shortener_error` or `assist_error`, then verify Firestore and
  Coupang secret access.

## Deploy Hook

`scripts/deploy_gcp_cloud_run.sh` runs:

- hosted smoke test when `RUN_SMOKE_TEST_AFTER_DEPLOY=true`
- deep closed-loop canary when `RUN_CLOSED_LOOP_AFTER_DEPLOY=true`

Both defaults are enabled. Disable only for emergency deploys where an external
monitor will run the same checks.
