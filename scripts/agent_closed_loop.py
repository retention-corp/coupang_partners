#!/usr/bin/env python3
"""Closed-loop health check for agent-facing Coupang shopping workflows.

The default check is intentionally low-cost: health + OpenAPI only. Use
`--deep` for scheduled canaries that mint one recommendation/short link.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

DEFAULT_BASE_URL = "https://a.retn.kr"
DEFAULT_QUERY = "retn agent closed-loop canary"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_USER_AGENT = "OpenClawAgentClosedLoop/1.0 (+https://a.retn.kr)"
DEFAULT_CLIENT_ID = "smoke-test"
DEFAULT_MIN_DEEP_INTERVAL_SECONDS = 3600
EXPECTED_PUBLIC_PATHS = (
    "/health",
    "/openapi.json",
    "/v1/public/assist",
    "/v1/public/search",
    "/v1/public/goldbox",
    "/v1/public/best/{category_id}",
    "/s/{slug}",
)
EXPECTED_AGENT_ALLOWLIST = (
    "openclaw-skill",
    "openclaw-skill-*",
    "local-cli",
    "smoke-test",
    "agent-smoke",
    "closed-loop-monitor",
    "github-actions-agent-ops",
    "coupang-mcp-fallback",
    "hermes-agent",
    "codex",
    "claude-code-skill",
    "chatgpt-gpt",
    "claw-*",
)


class ClosedLoopError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: Optional[int] = None,
        body: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.body = body
        super().__init__(message)


class CheckResult:
    def __init__(
        self,
        name: str,
        ok: bool,
        code: str = "ok",
        detail: str = "",
        latency_ms: int = 0,
        request_id: Optional[str] = None,
    ) -> None:
        self.name = name
        self.ok = ok
        self.code = code
        self.detail = detail
        self.latency_ms = latency_ms
        self.request_id = request_id

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "code": self.code,
            "latency_ms": self.latency_ms,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.request_id:
            payload["request_id"] = self.request_id
        return payload


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _normalize_base_url(url: str, *, allow_non_prod: bool = False) -> str:
    candidate = (url or "").strip() or DEFAULT_BASE_URL
    parsed = parse.urlparse(candidate)
    if allow_non_prod and parsed.scheme in {"http", "https"} and parsed.hostname:
        return candidate.rstrip("/")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "a.retn.kr":
        raise ClosedLoopError(
            "invalid_base_url",
            f"base URL must be https://a.retn.kr unless --allow-non-prod is set: {candidate}",
        )
    return candidate.rstrip("/")


def _headers(client_id: str, surface: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": os.getenv("OPENCLAW_SHOPPING_USER_AGENT", DEFAULT_USER_AGENT),
        "X-OpenClaw-Client-Id": client_id,
        "X-OpenClaw-Surface": surface,
        "X-OpenClaw-Version": "closed-loop-1",
    }


def _read_state_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state_file(path: Optional[str], payload: Dict[str, Any]) -> None:
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _should_run_deep(
    *,
    state_file: Optional[str],
    min_interval_seconds: int,
    requested: bool,
    force: bool = False,
) -> tuple[bool, str]:
    if not requested:
        return False, ""
    if force or not state_file:
        return True, ""
    state = _read_state_file(state_file)
    last_started = state.get("last_deep_started_at")
    if not isinstance(last_started, (int, float)):
        return True, ""
    elapsed = time.time() - float(last_started)
    minimum = max(0, min_interval_seconds)
    if elapsed >= minimum:
        return True, ""
    return False, f"min_deep_interval_not_elapsed:{int(minimum - elapsed)}s"


def _record_deep_attempt_started(state_file: Optional[str]) -> None:
    state = _read_state_file(state_file)
    state["last_deep_started_at"] = time.time()
    _write_state_file(state_file, state)


def _record_run_finished(
    state_file: Optional[str],
    *,
    ok: bool,
    mode: str,
    failure_codes: List[str],
) -> None:
    state = _read_state_file(state_file)
    now = time.time()
    state.update(
        {
            "last_finished_at": now,
            "last_ok": ok,
            "last_mode": mode,
            "last_failure_codes": failure_codes,
        }
    )
    if mode == "deep":
        state["last_deep_finished_at"] = now
        state["last_deep_ok"] = ok
    _write_state_file(state_file, state)


def _request_json(
    method: str,
    url: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url=url,
        data=body,
        headers=_headers(client_id, surface),
        method=method,
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise _classify_http_error(method, url, exc.code, raw) from exc
    except error.URLError as exc:
        raise ClosedLoopError("network_error", f"{method} {url} failed: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClosedLoopError("invalid_json", f"{method} {url} returned invalid JSON", body=raw[:500]) from exc


def _request_redirect_status(
    url: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
) -> Dict[str, Any]:
    opener = request.build_opener(NoRedirectHandler)
    http_request = request.Request(
        url=url,
        headers=_headers(client_id, surface),
        method="HEAD",
    )
    try:
        with opener.open(http_request, timeout=timeout) as response:
            return {"status": response.status, "location": response.headers.get("Location")}
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return {"status": exc.code, "location": exc.headers.get("Location")}
        raw = exc.read().decode("utf-8", errors="replace")
        raise _classify_http_error("HEAD", url, exc.code, raw) from exc
    except error.URLError as exc:
        raise ClosedLoopError("network_error", f"HEAD {url} failed: {exc.reason}") from exc


def _classify_http_error(method: str, url: str, status: int, body: str) -> ClosedLoopError:
    lowered = body.lower()
    code = f"http_{status}"
    if status == 403 and ("cloudflare" in lowered or "error code: 1010" in lowered):
        code = "cloudflare_blocked"
    elif status == 403 and "client is not allowlisted" in lowered:
        code = "client_not_allowlisted"
    elif status == 429:
        code = "rate_limited"
    elif status == 404:
        code = "route_missing"
    return ClosedLoopError(code, f"{method} {url} returned HTTP {status}", http_status=status, body=body[:500])


def _timed_check(name: str, fn) -> CheckResult:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    try:
        payload = fn()
        latency_ms = int((time.monotonic() - started) * 1000)
        request_id = payload.get("requestId") if isinstance(payload, dict) else None
        return CheckResult(name=name, ok=True, latency_ms=latency_ms, request_id=request_id)
    except ClosedLoopError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return CheckResult(name=name, ok=False, code=exc.code, detail=exc.message, latency_ms=latency_ms)


def run_closed_loop(
    base_url: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
    deep: bool,
    allow_non_prod: bool = False,
    query: str = DEFAULT_QUERY,
    state_file: Optional[str] = None,
    min_deep_interval_seconds: int = DEFAULT_MIN_DEEP_INTERVAL_SECONDS,
    force_deep: bool = False,
) -> Dict[str, Any]:
    normalized = _normalize_base_url(base_url, allow_non_prod=allow_non_prod)
    effective_deep, deep_skip_reason = _should_run_deep(
        state_file=state_file,
        min_interval_seconds=min_deep_interval_seconds,
        requested=deep,
        force=force_deep,
    )
    checks: List[CheckResult] = []
    context: Dict[str, Any] = {
        "base_url": normalized,
        "client_id": client_id,
        "surface": surface,
        "mode": "deep" if effective_deep else "shallow",
        "requested_mode": "deep" if deep else "shallow",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if deep_skip_reason:
        context["deep_skipped"] = True
        context["deep_skip_reason"] = deep_skip_reason

    checks.append(
        _timed_check(
            "health",
            lambda: _request_json("GET", f"{normalized}/health", timeout=timeout, client_id=client_id, surface=surface),
        )
    )
    checks.append(
        _timed_check(
            "openapi",
            lambda: _check_openapi(normalized, timeout=timeout, client_id=client_id, surface=surface),
        )
    )

    short_deeplink = ""
    if effective_deep:
        _record_deep_attempt_started(state_file)
        assist_result = _timed_check(
            "public_assist",
            lambda: _check_assist(
                normalized,
                timeout=timeout,
                client_id=client_id,
                surface=surface,
                query=query,
                context=context,
            ),
        )
        checks.append(assist_result)
        short_deeplink = str(context.get("short_deeplink") or "")
        if short_deeplink:
            checks.append(
                _timed_check(
                    "shortlink_redirect",
                    lambda: _check_shortlink_redirect(
                        short_deeplink,
                        timeout=timeout,
                        client_id=client_id,
                        surface=surface,
                    ),
                )
            )

    failed = [check for check in checks if not check.ok]
    result = {
        "ok": not failed,
        "status": "healthy" if not failed else "needs_recovery",
        "context": context,
        "checks": [check.to_dict() for check in checks],
        "recovery": build_recovery_plan(failed, deep=effective_deep),
        "operator_commands": build_operator_commands(failed, base_url=normalized, client_id=client_id, deep=effective_deep),
        "cost_guard": build_cost_guard(deep=effective_deep),
    }
    _record_run_finished(
        state_file,
        ok=bool(result["ok"]),
        mode=str(context["mode"]),
        failure_codes=[check.code for check in failed],
    )
    return result


def _check_openapi(base_url: str, *, timeout: int, client_id: str, surface: str) -> Dict[str, Any]:
    payload = _request_json("GET", f"{base_url}/openapi.json", timeout=timeout, client_id=client_id, surface=surface)
    paths = payload.get("paths") or {}
    missing = []
    for path in EXPECTED_PUBLIC_PATHS:
        if path in {"/docs", "/openapi.json"}:
            continue
        if path == "/v1/public/best/{category_id}":
            if path not in paths and "/v1/public/best/{category}" not in paths:
                missing.append(path)
            continue
        if path not in paths:
            missing.append(path)
    if missing:
        raise ClosedLoopError("openapi_contract_mismatch", f"openapi missing public paths: {', '.join(missing)}")
    return {}


def _check_assist(
    base_url: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
    query: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    payload = _request_json(
        "POST",
        f"{base_url}/v1/public/assist",
        timeout=timeout,
        client_id=client_id,
        surface=surface,
        payload={"query": query, "limit": 1},
    )
    candidates: List[Dict[str, Any]] = []
    best_fit = payload.get("best_fit")
    if isinstance(best_fit, dict):
        candidates.append(best_fit)
    shortlist = payload.get("shortlist") or []
    if isinstance(shortlist, list):
        candidates.extend(item for item in shortlist if isinstance(item, dict))
    if not candidates:
        raise ClosedLoopError("empty_recommendation", "public assist returned no recommendation payload")
    short_deeplink = next((item.get("short_deeplink") for item in candidates if item.get("short_deeplink")), "")
    if not short_deeplink:
        raise ClosedLoopError("short_deeplink_missing", "public assist returned recommendations without short_deeplink")
    context["short_deeplink"] = short_deeplink
    return {"requestId": payload.get("requestId")}


def _check_shortlink_redirect(
    short_deeplink: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
) -> Dict[str, Any]:
    result = _request_redirect_status(short_deeplink, timeout=timeout, client_id=client_id, surface=surface)
    status = int(result.get("status") or 0)
    if status not in (301, 302, 303, 307, 308):
        raise ClosedLoopError("shortlink_not_redirecting", f"short link returned HTTP {status}")
    if not result.get("location"):
        raise ClosedLoopError("shortlink_location_missing", "short link redirect did not include Location")
    return {}


def build_recovery_plan(failed: List[CheckResult], *, deep: bool) -> List[Dict[str, str]]:
    if not failed:
        return [{"priority": "none", "action": "No recovery needed."}]
    codes = {item.code for item in failed}
    actions: List[Dict[str, str]] = []
    if "cloudflare_blocked" in codes:
        actions.append(
            {
                "priority": "P0",
                "action": "Add or restore User-Agent allow rule for agent clients in Cloudflare; verify Python urllib clients are not challenged.",
            }
        )
    if "client_not_allowlisted" in codes:
        actions.append(
            {
                "priority": "P0",
                "action": "Add the client id to OPENCLAW_SHOPPING_CLIENT_ALLOWLIST or switch the agent to an existing allowlisted id. SA default: " + ",".join(EXPECTED_AGENT_ALLOWLIST),
            }
        )
    if "rate_limited" in codes:
        actions.append(
            {
                "priority": "P1",
                "action": "Back off canary frequency; keep shallow checks frequent and run deep assist checks at a low cadence. Do not increase Cloud Run max instances as a first response.",
            }
        )
    if "route_missing" in codes or "openapi_contract_mismatch" in codes:
        actions.append(
            {
                "priority": "P0",
                "action": "Roll back or redeploy the latest backend revision; verify README, OpenAPI, and backend routes match.",
            }
        )
    if {"empty_recommendation", "short_deeplink_missing", "shortlink_not_redirecting", "shortlink_location_missing"} & codes:
        actions.append(
            {
                "priority": "P0",
                "action": "Inspect Coupang API credentials, shortener provider, Firestore permissions, and Cloud Run logs for shortener_error/assist_error. If only short links fail, keep serving raw affiliate links while fixing Firestore.",
            }
        )
    if "invalid_json" in codes:
        actions.append(
            {
                "priority": "P1",
                "action": "Check Cloudflare challenge pages, proxy errors, or non-JSON backend responses before retrying agent traffic.",
            }
        )
    if "network_error" in codes:
        actions.append(
            {
                "priority": "P1",
                "action": "Check DNS, Cloudflare, and Cloud Run service availability before redeploying.",
            }
        )
    if not actions:
        actions.append({"priority": "P1", "action": "Inspect failed check details and Cloud Run logs."})
    if deep:
        actions.append(
            {
                "priority": "guardrail",
                "action": "Do not retry deep checks in a tight loop; wait for the public rate-limit window before rerunning.",
            }
        )
    return actions


def build_operator_commands(
    failed: List[CheckResult],
    *,
    base_url: str,
    client_id: str,
    deep: bool,
) -> List[Dict[str, str]]:
    """Return copy/pasteable operator commands without running them automatically."""

    if not failed:
        return [
            {
                "name": "next_shallow_check",
                "command": f"python3 scripts/agent_closed_loop.py --base-url {base_url} --client-id {client_id}",
            }
        ]
    commands: List[Dict[str, str]] = [
        {
            "name": "cloud_run_recent_errors",
            "command": (
                "gcloud run services logs read a-retn-shortener "
                "--project retn-kr-website --region us-central1 --limit 100"
            ),
        },
        {
            "name": "deploy_hardened_revision",
            "command": "RUN_SMOKE_TEST_AFTER_DEPLOY=true scripts/deploy_gcp_cloud_run.sh",
        },
    ]
    if deep:
        commands.append(
            {
                "name": "rerun_shallow_before_deep",
                "command": f"python3 scripts/agent_closed_loop.py --base-url {base_url} --client-id {client_id}",
            }
        )
    else:
        commands.append(
            {
                "name": "low_cadence_deep_canary",
                "command": f"python3 scripts/agent_closed_loop.py --base-url {base_url} --client-id {client_id} --deep",
            }
        )
    return commands


def build_cost_guard(*, deep: bool) -> Dict[str, Any]:
    return {
        "default_mode": "shallow",
        "current_mode": "deep" if deep else "shallow",
        "cloud_run_defaults": {
            "min_instances": 0,
            "max_instances": 2,
            "public_rate_limit_requests": 12,
            "public_rate_limit_window_seconds": 60,
            "response_cache_ttl_seconds": 900,
        },
        "recommended_schedule": {
            "shallow": "every 5-15 minutes",
            "deep": "every 3-6 hours, plus once after deploy",
        },
        "cost_blast_radius": {
            "shallow": "GET /health + GET /openapi.json only; no Coupang API call and no short link mint.",
            "deep": "One limit=1 public assist call plus one short-link HEAD check.",
        },
        "max_request_budget_per_run": {
            "shallow": 2,
            "deep": 4,
        },
        "deep_canary_shape": {
            "assist_limit": 1,
            "shortlink_probe": "HEAD without following redirect",
            "stateful_throttle": f"{DEFAULT_MIN_DEEP_INTERVAL_SECONDS} seconds by default when --state-file is set",
        },
        "budget_rules": [
            "Never run deep checks more often than the public rate-limit window.",
            "Use limit=1 canaries only.",
            "Do not loop on 429; back off and alert.",
            "Keep Cloud Run max instances capped in deploy config.",
            "Keep response cache enabled for repeated canary queries.",
            "Keep public endpoint checks tokenless; never print Coupang or bearer secrets.",
        ],
        "next_check_after_seconds": 3600 if deep else 300,
        "expected_public_paths": list(EXPECTED_PUBLIC_PATHS),
        "expected_agent_allowlist": list(EXPECTED_AGENT_ALLOWLIST),
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run closed-loop agent workflow checks.")
    parser.add_argument("--base-url", default=os.getenv("OPENCLAW_SHOPPING_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--client-id", default=os.getenv("OPENCLAW_SHOPPING_CLIENT_ID") or DEFAULT_CLIENT_ID)
    parser.add_argument("--surface", default=os.getenv("OPENCLAW_SHOPPING_SURFACE") or "cli")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--deep", action="store_true", help="Run assist + shortlink redirect canary.")
    parser.add_argument("--allow-non-prod", action="store_true", help="Allow localhost/staging base URLs for tests.")
    parser.add_argument("--state-file", default=os.getenv("OPENCLAW_AGENT_CLOSED_LOOP_STATE_FILE") or "")
    parser.add_argument(
        "--min-deep-interval-seconds",
        type=int,
        default=int(os.getenv("OPENCLAW_AGENT_MIN_DEEP_INTERVAL_SECONDS") or DEFAULT_MIN_DEEP_INTERVAL_SECONDS),
        help="Skip deep checks when the state file says one started more recently than this interval.",
    )
    parser.add_argument("--force-deep", action="store_true", help="Ignore the state-file deep interval guard once.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_closed_loop(
            args.base_url,
            timeout=args.timeout,
            client_id=args.client_id,
            surface=args.surface,
            deep=args.deep,
            allow_non_prod=args.allow_non_prod,
            query=args.query,
            state_file=args.state_file or None,
            min_deep_interval_seconds=args.min_deep_interval_seconds,
            force_deep=args.force_deep,
        )
    except ClosedLoopError as exc:
        result = {
            "ok": False,
            "status": "needs_recovery",
            "checks": [],
            "recovery": build_recovery_plan(
                [CheckResult(name="startup", ok=False, code=exc.code, detail=exc.message)],
                deep=args.deep,
            ),
            "operator_commands": build_operator_commands(
                [CheckResult(name="startup", ok=False, code=exc.code, detail=exc.message)],
                base_url=args.base_url,
                client_id=args.client_id,
                deep=args.deep,
            ),
            "cost_guard": build_cost_guard(deep=args.deep),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
