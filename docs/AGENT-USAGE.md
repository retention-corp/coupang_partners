# Agent Usage

This is the contract for Hermes, Codex, Claude Code, OpenClaw, and other agents
that want to use the hosted Coupang shopping backend without owning Coupang
Partner credentials.

## Base Contract

- Production base URL: `https://a.retn.kr`
- OpenAPI: `https://a.retn.kr/openapi.json`
- Human-readable docs: `https://a.retn.kr/docs`
- Public calls are tokenless.
- Do not ask users for `COUPANG_ACCESS_KEY` or `COUPANG_SECRET_KEY`.
- Prefer `short_deeplink` over `deeplink` in user-facing answers.
- Include affiliate disclosure anywhere links are shown.

## Recommended Headers

Every agent call should send:

```http
User-Agent: <agent-name>/<version> (+https://a.retn.kr)
X-OpenClaw-Client-Id: <stable-client-id>
X-OpenClaw-Surface: <surface>
X-OpenClaw-Version: <agent-or-skill-version>
```

Recommended client ids:

- `openclaw-skill`
- `openclaw-skill-*`
- `local-cli`
- `smoke-test`
- `agent-smoke`
- `closed-loop-monitor`
- `github-actions-agent-ops`
- `hermes-agent`
- `codex`
- `claude-code-skill`
- `chatgpt-gpt`
- `coupang-mcp-fallback`
- `claw-*`

Recommended surfaces: `openclaw-skill`, `codex`, `claude-code-skill`,
`chatgpt-gpt`, `cli`, `mcp`, or a `claw-*` surface.

`OPENCLAW_SHOPPING_CLIENT_ALLOWLIST` supports exact ids and prefix patterns such
as `openclaw-skill-*` and `claw-*`.

Production deploy default:

```text
openclaw-skill,openclaw-skill-*,local-cli,smoke-test,agent-smoke,closed-loop-monitor,github-actions-agent-ops,coupang-mcp-fallback,hermes-agent,codex,claude-code-skill,chatgpt-gpt,claw-*
```

## HTTP Example

```bash
curl -sS -X POST https://a.retn.kr/v1/public/assist \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: HermesAgent/1.0 (+https://a.retn.kr)' \
  -H 'X-OpenClaw-Client-Id: hermes-agent' \
  -H 'X-OpenClaw-Surface: mcp' \
  -d '{"query":"원룸용 무선청소기 30만원 이하", "limit": 3}'
```

Expected success shape:

- HTTP `200`
- `best_fit` or `shortlist` present
- each recommendation should contain `short_deeplink` when shortener is healthy
- `disclosure` present

## CLI Example

```bash
OPENCLAW_SHOPPING_CLIENT_ID=hermes-agent \
python3 bin/openclaw_shopping.py "원룸용 무선청소기 30만원 이하"
```

## Stdio MCP Server

Use the stdio server when an agent supports MCP-style JSON-RPC tools:

```json
{
  "mcpServers": {
    "retn-coupang-shopping": {
      "command": "python3",
      "args": ["/Users/gyusupsim/Projects/products/coupang_partners/coupang_mcp_server.py"],
      "env": {
        "OPENCLAW_SHOPPING_BASE_URL": "https://a.retn.kr",
        "OPENCLAW_SHOPPING_CLIENT_ID": "hermes-agent",
        "OPENCLAW_SHOPPING_USER_AGENT": "HermesAgent/1.0 (+https://a.retn.kr)",
        "OPENCLAW_SHOPPING_FORCE_HOSTED": "1"
      }
    }
  }
}
```

Available tools:

- `search_coupang_products`
- `search_coupang_rocket`
- `search_coupang_budget`
- `compare_coupang_products`
- `get_coupang_recommendations`
- `get_coupang_best_products`
- `get_coupang_goldbox`

## MCP-Compatible CLI

The repo also has an MCP-compatible in-process client and CLI:

```bash
OPENCLAW_SHOPPING_FORCE_HOSTED=1 \
OPENCLAW_SHOPPING_CLIENT_ID=coupang-mcp-fallback \
python3 bin/coupang_mcp.py search "USB C 케이블"
```

Use it as a CLI bridge or import `CoupangMcpClient` directly when a stdio
server is not needed.

## Live Agent Smoke Check

Run this before handing the backend to another agent surface:

```bash
python3 scripts/agent_smoke.py --client-id hermes-agent --surface mcp
```

The smoke check verifies:

- `/health`
- `/openapi.json`
- `/v1/public/assist`
- `short_deeplink`
- `/s/<slug>` redirect

On failure it prints a machine-readable `reason`. For recovery actions and
cost guardrails, run `python3 scripts/agent_closed_loop.py --deep` and
`python3 scripts/closed_loop_check.py --require-admin` when an operator token is
available. `python3 scripts/closed_loop_ops.py --auto-recover` is the opt-in
production recovery controller.

## Failure Handling

| Reason | Meaning | First action |
| --- | --- | --- |
| `client_not_allowlisted` | backend allowlist rejected the caller | add or fix `X-OpenClaw-Client-Id`, redeploy |
| `cloudflare_blocked` | Cloudflare blocked the request before backend | set stable `User-Agent`, inspect WAF event |
| `rate_limited` | public bucket is exhausted | back off, reduce retries, inspect caller volume |
| `backend_empty_result` | backend returned no recommendation | inspect Coupang API/quota logs |
| `short_link_missing` | assist worked but shortener failed | inspect Firestore shortener env and permissions |
| `short_link_redirect_failed` | slug exists but redirect failed | inspect `/s/<slug>` logs and Firestore reads |

Agents should not retry these failures in a tight loop. Report the `reason`,
`requestId` when present, and the recovery steps.
