#!/usr/bin/env python3
"""Closed-loop health, cost, and recovery controller for the hosted backend."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, request

DEFAULT_BASE_URL = "https://a.retn.kr"
DEFAULT_ASSIST_LIMIT_PER_DAY = 1500
DEFAULT_SHORTLINK_LIMIT_PER_DAY = 5000
DEFAULT_RECOVERY_COMMAND = "scripts/deploy_gcp_cloud_run.sh"


class ClosedLoopError(RuntimeError):
    pass


def _load_agent_smoke_module():
    script_path = Path(__file__).resolve().parent / "agent_smoke.py"
    spec = importlib.util.spec_from_file_location("agent_smoke", script_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ClosedLoopError("cannot load agent_smoke.py")
    spec.loader.exec_module(module)
    return module


def _auth_token() -> Optional[str]:
    singular = (os.getenv("OPENCLAW_SHOPPING_API_TOKEN") or "").strip()
    if singular:
        return singular
    plural = (os.getenv("OPENCLAW_SHOPPING_API_TOKENS") or "").strip()
    if not plural:
        return None
    return next((token.strip() for token in plural.split(",") if token.strip()), None)


def _admin_summary(base_url: str, *, timeout: int, token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    req = request.Request(
        url=base_url.rstrip("/") + "/v1/admin/summary",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "OpenClawShoppingClosedLoop/1.0 (+https://a.retn.kr)",
            "X-OpenClaw-Client-Id": "closed-loop-monitor",
            "X-OpenClaw-Surface": "cli",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "admin_error": f"HTTP {exc.code}: {raw or exc.reason}"}
    except Exception as exc:
        return {"ok": False, "admin_error": str(exc)}


def _estimate_daily_count(summary: Optional[Dict[str, Any]], key: str) -> Optional[int]:
    if not summary or summary.get("ok") is False:
        return None
    value = summary.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def run_closed_loop(
    *,
    base_url: str,
    timeout: int,
    client_id: str,
    surface: str,
    allow_non_prod: bool,
    auto_recover: bool,
    assist_limit_per_day: int,
    shortlink_limit_per_day: int,
    recovery_command: str,
) -> Dict[str, Any]:
    smoke_module = _load_agent_smoke_module()
    checks: Dict[str, Any] = {}
    actions = []
    severity = "ok"

    try:
        smoke = smoke_module.run_agent_smoke(
            base_url,
            allow_non_prod=allow_non_prod,
            timeout=timeout,
            query="closed loop health check lightweight mask",
            client_id=client_id,
            surface=surface,
            require_shortlink=True,
        )
        checks["agent_smoke"] = smoke
    except Exception as exc:
        code = getattr(exc, "code", "agent_smoke_failed")
        checks["agent_smoke"] = {"ok": False, "code": code, "error": str(exc)}
        severity = "critical" if code not in {"rate_limited"} else "warning"
        if code in {"cloudflare_blocked", "client_not_allowlisted", "short_link_missing", "shortlink_not_redirecting", "network_error"}:
            actions.append("repair_agent_gateway")

    summary = _admin_summary(base_url, timeout=timeout, token=_auth_token())
    checks["admin_summary"] = summary if summary is not None else {"ok": None, "skipped": "missing_operator_token"}
    total_queries = _estimate_daily_count(summary, "total_queries")
    total_short_links = _estimate_daily_count(summary, "total_short_links")
    if total_queries is not None and total_queries > assist_limit_per_day:
        severity = "critical"
        actions.append("tighten_public_rate_limits")
    if total_short_links is not None and total_short_links > shortlink_limit_per_day:
        severity = "critical"
        actions.append("disable_or_tighten_shortlink_generation")
    if summary and summary.get("admin_error"):
        severity = "warning" if severity == "ok" else severity
        actions.append("check_operator_routes_or_token")

    result: Dict[str, Any] = {
        "ok": severity == "ok",
        "severity": severity,
        "base_url": base_url.rstrip("/"),
        "checks": checks,
        "actions": sorted(set(actions)),
        "auto_recover": False,
    }

    if auto_recover and severity == "critical" and "repair_agent_gateway" in actions:
        completed = subprocess.run(recovery_command.split(), text=True, capture_output=True)
        result["auto_recover"] = True
        result["recovery"] = {
            "command": recovery_command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
        result["ok"] = completed.returncode == 0

    return result


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run closed-loop checks and optional safe recovery.")
    parser.add_argument("--base-url", default=os.getenv("PUBLIC_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--client-id", default=os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "closed-loop-monitor"))
    parser.add_argument("--surface", default=os.getenv("OPENCLAW_SHOPPING_SURFACE", "cli"))
    parser.add_argument("--allow-non-prod", action="store_true")
    parser.add_argument("--auto-recover", action="store_true")
    parser.add_argument("--assist-limit-per-day", type=int, default=int(os.getenv("OPENCLAW_ASSIST_LIMIT_PER_DAY", DEFAULT_ASSIST_LIMIT_PER_DAY)))
    parser.add_argument("--shortlink-limit-per-day", type=int, default=int(os.getenv("OPENCLAW_SHORTLINK_LIMIT_PER_DAY", DEFAULT_SHORTLINK_LIMIT_PER_DAY)))
    parser.add_argument("--recovery-command", default=os.getenv("OPENCLAW_RECOVERY_COMMAND", DEFAULT_RECOVERY_COMMAND))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    result = run_closed_loop(
        base_url=args.base_url,
        timeout=args.timeout,
        client_id=args.client_id,
        surface=args.surface,
        allow_non_prod=args.allow_non_prod,
        auto_recover=args.auto_recover,
        assist_limit_per_day=args.assist_limit_per_day,
        shortlink_limit_per_day=args.shortlink_limit_per_day,
        recovery_command=args.recovery_command,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
