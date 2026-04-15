# Coupang Product Search Skill

This OpenClaw skill wraps the repository's local Coupang MCP-compatible layer
and exposes common Coupang product-search flows through the local CLI wrapper
in this repository.

## Files

- `SKILL.md`
- `_meta.json`
- `scripts/openclaw-coupang-mcp.py`

## Environment

- `COUPANG_MCP_ENDPOINT` is kept as a compatibility knob, but the repo now ships its own implementation
- `COUPANG_MCP_TIMEOUT_SECONDS` defaults to `20`
