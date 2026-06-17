"""MCP server for book_reco (optional entrypoint).

Requires the `mcp` package (not part of the hosted backend runtime). Only used when a user
runs the local MCP server directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from .cli import build_services
from .models import MCPResult

mcp = FastMCP("book-reco")


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return MCPResult(ok=True, data=data).to_dict()


@mcp.tool(description="Search Korean books by query and return normalized results.")
def search_book(query: str) -> dict[str, Any]:
    search_service, _ = build_services()
    response = search_service.search(query)
    return _ok(response.to_dict())


@mcp.tool(description="Recommend Korean books from an ISBN13 or free-text query.")
def recommend_book(isbn13: str = "", query: str = "") -> dict[str, Any]:
    _, recommendation_service = build_services()
    if isbn13:
        response = recommendation_service.recommend_by_isbn(isbn13)
    else:
        response = recommendation_service.recommend_by_query(query)
    return _ok(response.to_dict())


@mcp.tool(description="Return trending Korean books.")
def trending_books() -> dict[str, Any]:
    _, recommendation_service = build_services()
    response = recommendation_service.trending()
    return _ok(response.to_dict())


@mcp.tool(description="Describe a Korean book by ISBN13.")
def describe_book(isbn13: str) -> dict[str, Any]:
    search_service, _ = build_services()
    response = search_service.describe(isbn13)
    return _ok(response.to_dict())


def run() -> None:
    """Run the MCP server."""

    asyncio.run(mcp.run_async())


if __name__ == "__main__":
    run()
