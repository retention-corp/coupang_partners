#!/usr/bin/env python3
"""Closed-loop operations check for the hosted shopping backend.

Runs the cheap public agent smoke test, optionally reads admin summary with a
bearer token, and emits machine-readable recovery actions. It does not mutate
production state beyond the single public assist and shortlink HEAD performed
by `agent_smoke`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_smoke


DEFAULT_BASE_URL = "https://a.retn.kr"
DEFAULT_TIMEOUT_SECONDS = 20


class ClosedLoopFailure(RuntimeError):
    pass


def _auth_token() -> Optional[str]:
    singular = (os.getenv("OPENCLAW_SHOPPING_API_TOKEN") or "").strip()
    if singular:
        return singular
    plural = (os.getenv("OPENCLAW_SHOPPING_API_TOKENS") or "").strip()
    if not plural:
        return None
    return next((token.strip() for token in plural.split(",") if token.strip()), None)


def _request_admin_summary(base_url: str, *, token: str, timeout: int, client_id: str) -> Dict[str, Any]:
    req = request.Request(
        f"{base_url.rstrip('/')}/v1/admin/summary",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": agent_smoke.DEFAULT_USER_AGENT,
            "X-OpenClaw-Client-Id": client_id,
            "X-OpenClaw-Surface": "cli",
            "X-OpenClaw-Version": os.getenv("OPENCLAW_SHOPPING_CLIENT_VERSION", "closed-loop-check"),
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ClosedLoopFailure(f"admin summary HTTP {exc.code}: {raw or exc.reason}") from exc
    except error.URLError as exc:
        raise ClosedLoopFailure(f"admin summary network error: {exc.reason}") from exc
    return json.loads(raw)


def _threshold_flag(summary: Dict[str, Any], field: str, max_value: Optional[int]) -> Optional[Dict[str, Any]]:
    if max_value is None:
        return None
    value = summary.get(field)
    if value is None:
        return {"field": field, "ok": False, "reason": "missing", "max": max_value}
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        return {"field": field, "ok": False, "reason": "non_numeric", "value": value, "max": max_value}
    return {"field": field, "ok": numeric_value <= max_value, "value": numeric_value, "max": max_value}


def _recovery_actions(failure_kind: Optional[str], admin_error: Optional[str], threshold_flags: list[Dict[str, Any]]) -> list[str]:
    actions = []
    if failure_kind == "cloudflare_blocked":
        actions.append("Check Cloudflare WAF/Bot rules for Python agent User-Agent and allow /v1/public/* plus /s/*.")
    elif failure_kind == "client_not_allowlisted":
        actions.append("Add the caller client id to OPENCLAW_SHOPPING_CLIENT_ALLOWLIST or use a prefix rule such as claw-*.")
    elif failure_kind == "rate_limited":
        actions.append("Inspect public rate-limit settings and client-id distribution; avoid retry storms before raising limits.")
    elif failure_kind == "short_link_missing":
        actions.append("Verify OPENCLAW_SHOPPING_SHORTENER=firestore and Firestore permissions; confirm assist responses include short_deeplink.")
    elif failure_kind in {"short_link_redirect_failed", "shortlink_network_error", "shortlink_not_redirecting", "shortlink_missing_location"}:
        actions.append("Check Firestore short_links collection, domain mapping for a.retn.kr, and /s/<slug> redirect logs.")
    elif failure_kind == "backend_empty_result":
        actions.append("Check Coupang API credentials/quota and recommendation fallback behavior.")
    elif failure_kind in {"network_error", "backend_error", "health_failed"}:
        actions.append("Check Cloud Run revision health, recent deploys, and service logs for a-retn-shortener.")
    elif failure_kind == "openapi_contract_missing":
        actions.append("Compare live /openapi.json with backend.py route definitions; redeploy if code and live schema diverged.")

    if admin_error and "gcloud" in admin_error.lower():
        actions.append("gcloud service describe failed; refresh local gcloud auth or run the check from CI with read-only Cloud Run permissions.")
    elif admin_error:
        actions.append("Admin summary unavailable; verify operator routes, bearer token, and a-retn-shortener-ops routing before trusting cost guards.")
    for flag in threshold_flags:
        if not flag.get("ok"):
            actions.append(f"Cost guard tripped for {flag.get('field')}; inspect traffic source and pause noisy agents if growth is unexpected.")
    if not actions:
        actions.append("No recovery action required.")
    return actions


def run_check(args: argparse.Namespace) -> Dict[str, Any]:
    smoke_result: Optional[Dict[str, Any]] = None
    smoke_failure_kind: Optional[str] = None
    try:
        smoke_result = agent_smoke.smoke(
            base_url=args.base_url,
            allow_non_prod=args.allow_non_prod,
            timeout=args.timeout,
            query=args.query,
            client_id=args.client_id,
            surface=args.surface,
            skip_assist=getattr(args, "skip_assist", False),
        )
    except agent_smoke.AgentSmokeError as exc:
        smoke_failure_kind = exc.error_code
        smoke_result = {
            "ok": False,
            "failure_kind": exc.error_code,
            "status": exc.status,
            "error": str(exc),
        }

    admin_summary = None
    admin_error = None
    threshold_flags: list[Dict[str, Any]] = []
    token = _auth_token()
    if args.require_admin and not token:
        admin_error = "admin token required but OPENCLAW_SHOPPING_API_TOKEN(S) is empty"
    elif token:
        try:
            base = agent_smoke._normalize_base_url(args.base_url, allow_non_production=args.allow_non_prod)
            admin_summary = _request_admin_summary(base, token=token, timeout=args.timeout, client_id=args.client_id)
        except Exception as exc:
            admin_error = str(exc)

    if admin_summary:
        for flag in (
            _threshold_flag(admin_summary, "total_queries", args.max_total_queries),
            _threshold_flag(admin_summary, "total_short_links", args.max_total_short_links),
            _threshold_flag(admin_summary, "total_events", args.max_total_events),
        ):
            if flag:
                threshold_flags.append(flag)

    gcloud_summary = None
    if getattr(args, "include_gcloud", False):
        gcloud_summary = _gcloud_service_summary()

    gcloud_error = gcloud_summary.get("error") if isinstance(gcloud_summary, dict) and not gcloud_summary.get("ok", True) else None
    ok = (
        bool(smoke_result and smoke_result.get("ok"))
        and not admin_error
        and not gcloud_error
        and all(flag.get("ok") for flag in threshold_flags)
    )
    return {
        "ok": ok,
        "smoke": smoke_result,
        "admin_summary_checked": bool(admin_summary),
        "admin_error": admin_error,
        "cost_guard_flags": threshold_flags,
        "gcloud": gcloud_summary,
        "recovery_actions": _recovery_actions(smoke_failure_kind, admin_error or gcloud_error, threshold_flags),
    }


def _gcloud_service_summary() -> Dict[str, Any]:
    cmd = [
        "gcloud",
        "run",
        "services",
        "describe",
        os.getenv("SERVICE_NAME", "a-retn-shortener"),
        "--project",
        os.getenv("PROJECT_ID", "retn-kr-website"),
        "--region",
        os.getenv("REGION", "us-central1"),
        "--format=json",
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(completed.stdout)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    template = payload.get("spec", {}).get("template", {})
    annotations = template.get("metadata", {}).get("annotations", {})
    containers = template.get("spec", {}).get("containers", [])
    env_names = []
    if containers:
        env_names = sorted(item.get("name") for item in containers[0].get("env", []) if item.get("name"))
    return {
        "ok": True,
        "latest_ready_revision": payload.get("status", {}).get("latestReadyRevisionName"),
        "max_scale": annotations.get("autoscaling.knative.dev/maxScale"),
        "min_scale": annotations.get("autoscaling.knative.dev/minScale"),
        "env_names": env_names,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run closed-loop health and cost guard checks.")
    parser.add_argument("--base-url", default=os.getenv("OPENCLAW_SHOPPING_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--query", default=agent_smoke.DEFAULT_QUERY)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--client-id", default=os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "closed-loop-monitor"))
    parser.add_argument("--surface", default=os.getenv("OPENCLAW_SHOPPING_SURFACE", "cli"))
    parser.add_argument("--allow-non-prod", action="store_true")
    parser.add_argument("--skip-assist", action="store_true", help="Only check /health and /openapi.json.")
    parser.add_argument("--include-gcloud", action="store_true", help="Read Cloud Run service config without mutating it.")
    parser.add_argument("--require-admin", action="store_true")
    parser.add_argument("--max-total-queries", type=int)
    parser.add_argument("--max-total-short-links", type=int)
    parser.add_argument("--max-total-events", type=int)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    result = run_check(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
