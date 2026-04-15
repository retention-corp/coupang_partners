"""Skill-local import shim for the repository's local Coupang MCP layer."""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coupang_mcp_client import (  # noqa: F401
    DEFAULT_MCP_ENDPOINT,
    DEFAULT_PROTOCOL_VERSION,
    CoupangMcpClient,
    McpError,
    McpResponse,
    extract_tool_result,
    tool_argument_dict,
)
