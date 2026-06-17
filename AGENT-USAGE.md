# Agent Usage Entrypoint

Use [docs/AGENT-USAGE.md](docs/AGENT-USAGE.md) as the full contract for
Hermes, Codex, OpenClaw, Claude-style skills, ChatGPT GPTs, and MCP clients.

Production base URL: `https://a.retn.kr`

Machine-readable manifest: [agent_manifest.json](agent_manifest.json)

Closed-loop operations: [docs/OPERATIONS-CLOSED-LOOP.md](docs/OPERATIONS-CLOSED-LOOP.md)

Quick smoke:

```bash
python3 scripts/agent_smoke.py --client-id hermes-agent --surface mcp
```
