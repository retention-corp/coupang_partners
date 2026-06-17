#!/usr/bin/env python3
"""Tiny stdio JSON-RPC server for agent MCP-style Coupang shopping tools.

The implementation is intentionally stdlib-only and hosted-first. By default it
uses https://a.retn.kr through CoupangMcpClient, so external agents do not need
Coupang credentials. Operator machines with COUPANG_* env vars can still use the
local Partners client path.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

from coupang_mcp_client import CoupangMcpClient, McpError, extract_tool_result


SERVER_NAME = "retn-coupang-shopping"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "search_coupang_products",
        "description": "Search Coupang products through the hosted shopping backend.",
        "inputSchema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}, "query": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "search_coupang_rocket",
        "description": "Search Coupang products and return Rocket-delivery matches.",
        "inputSchema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}, "query": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "search_coupang_budget",
        "description": "Search Coupang products within an optional KRW budget range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "query": {"type": "string"},
                "min_price": {"type": "integer"},
                "max_price": {"type": "integer"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "compare_coupang_products",
        "description": "Compare two or more product terms separated by 'vs'.",
        "inputSchema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}, "query": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "get_coupang_recommendations",
        "description": "Get recommendation-style product results for a query or category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "query": {"type": "string"},
                "category": {"type": "string"},
            },
        },
    },
    {
        "name": "get_coupang_best_products",
        "description": "Get Coupang best products by category when credentials are available; otherwise fallback to hosted search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category_id": {"type": "integer"},
                "keyword": {"type": "string"},
                "query": {"type": "string"},
            },
        },
    },
    {
        "name": "get_coupang_goldbox",
        "description": "Get Coupang Goldbox deals when local operator credentials are available.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _jsonrpc_result(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_message(client: CoupangMcpClient, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _jsonrpc_result(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "tools/list":
        return _jsonrpc_result(message_id, {"tools": TOOL_DEFINITIONS})
    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return _jsonrpc_error(message_id, -32602, "tools/call requires params.name")
        if not isinstance(arguments, dict):
            return _jsonrpc_error(message_id, -32602, "tools/call params.arguments must be an object")
        try:
            response = client.call_tool(tool_name, arguments)
            result = extract_tool_result(response.payload)
        except McpError as exc:
            return _jsonrpc_error(message_id, -32000, str(exc))
        except Exception as exc:  # noqa: BLE001
            return _jsonrpc_error(message_id, -32001, f"tool call failed: {exc}")
        return _jsonrpc_result(
            message_id,
            {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
        )
    return _jsonrpc_error(message_id, -32601, f"Unsupported method: {method}")


def serve_stdio(lines: Iterable[str], *, out: Any = sys.stdout, client: Optional[CoupangMcpClient] = None) -> int:
    shopping_client = client or CoupangMcpClient(timeout_seconds=int(os.getenv("COUPANG_MCP_TIMEOUT_SECONDS", "20")))
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            print(json.dumps(_jsonrpc_error(None, -32700, "Parse error")), file=out, flush=True)
            continue
        response = handle_message(shopping_client, message)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), file=out, flush=True)
    return 0


def main() -> int:
    return serve_stdio(sys.stdin)


if __name__ == "__main__":
    raise SystemExit(main())
