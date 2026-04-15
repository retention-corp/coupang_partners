#!/usr/bin/env python3
"""Self-contained OpenClaw wrapper for the external Coupang MCP endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys

from coupang_mcp_client import CoupangMcpClient, DEFAULT_MCP_ENDPOINT, extract_tool_result, tool_argument_dict


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Call the external Coupang MCP endpoint from an OpenClaw skill.")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("COUPANG_MCP_ENDPOINT", DEFAULT_MCP_ENDPOINT),
        help="Streamable HTTP MCP endpoint.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("COUPANG_MCP_TIMEOUT_SECONDS", "20")),
        help="Request timeout in seconds.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search")
    search.add_argument("keyword")

    rocket = subparsers.add_parser("rocket")
    rocket.add_argument("keyword")

    budget = subparsers.add_parser("budget")
    budget.add_argument("keyword")
    budget.add_argument("--min-price", type=int)
    budget.add_argument("--max-price", type=int)

    compare = subparsers.add_parser("compare")
    compare.add_argument("query")

    recommendations = subparsers.add_parser("recommendations")
    recommendations.add_argument("query")

    seasonal = subparsers.add_parser("seasonal")
    seasonal.add_argument("query")

    best = subparsers.add_parser("best")
    best.add_argument("category")

    subparsers.add_parser("goldbox")
    subparsers.add_parser("init")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    client = CoupangMcpClient(endpoint=args.endpoint, timeout_seconds=args.timeout)

    try:
        if args.command == "init":
            response = client.initialize()
            payload = {"session_id": response.session_id, "payload": response.payload}
        elif args.command == "search":
            payload = run_tool(client, "search_coupang_products", keyword=args.keyword)
        elif args.command == "rocket":
            payload = run_tool(client, "search_coupang_rocket", keyword=args.keyword)
        elif args.command == "budget":
            payload = run_tool(
                client,
                "search_coupang_budget",
                keyword=args.keyword,
                min_price=args.min_price,
                max_price=args.max_price,
            )
        elif args.command == "compare":
            payload = run_tool(client, "compare_coupang_products", query=args.query)
        elif args.command == "recommendations":
            payload = run_tool(client, "get_coupang_recommendations", query=args.query)
        elif args.command == "seasonal":
            payload = run_tool(client, "get_coupang_seasonal", query=args.query)
        elif args.command == "best":
            payload = run_tool(client, "get_coupang_best_products", category=args.category)
        else:
            payload = run_tool(client, "get_coupang_goldbox")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": {"message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "data": payload}, ensure_ascii=False, indent=2))
    return 0


def run_tool(client: CoupangMcpClient, tool_name: str, **arguments):
    response = client.call_tool(tool_name, tool_argument_dict(arguments.items()))
    return {
        "session_id": response.session_id or client.session_id,
        "tool": tool_name,
        "payload": response.payload,
        "result": extract_tool_result(response.payload),
    }


if __name__ == "__main__":
    raise SystemExit(main())
