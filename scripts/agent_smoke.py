#!/usr/bin/env python3
"""Agent-facing smoke test for the hosted Coupang shopping backend."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Optional
from urllib import error, parse, request

DEFAULT_BASE_URL = "https://a.retn.kr"
DEFAULT_QUERY = "retn agent smoke lightweight mask"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_USER_AGENT = "OpenClawAgentSmoke/1.0 (+https://a.retn.kr)"


class AgentSmokeError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, status: Optional[int] = None) -> None:
        self.error_code = error_code
        self.code = error_code
        self.kind = error_code
        self.message = message
        self.status = status
        super().__init__(message)


SmokeFailure = AgentSmokeError


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _classify_http_error(status: int, body: str) -> str:
    lowered = (body or "").lower()
    if status == 403 and ("cloudflare" in lowered or "cf-error-code" in lowered or "1010" in lowered):
        return "cloudflare_blocked"
    if status == 403 and "client is not allowlisted" in lowered:
        return "client_not_allowlisted"
    if status == 429:
        return "rate_limited"
    if status == 404:
        return "route_missing"
    return f"http_{status}"


def classify_http_failure(status: int, body: str) -> str:
    return _classify_http_error(status, body)


def classify_http_error(status: int, body: str) -> str:
    return _classify_http_error(status, body)


def recovery_plan(code: str) -> list[str]:
    plans = {
        "client_not_allowlisted": [
            "Add the caller id to OPENCLAW_SHOPPING_CLIENT_ALLOWLIST.",
            "Redeploy Cloud Run and rerun the same smoke check.",
        ],
        "cloudflare_blocked": [
            "Check Cloudflare WAF/Bot rules for agent User-Agent traffic.",
            "Rerun the smoke check with the same client id.",
        ],
        "rate_limited": [
            "Back off the caller and avoid deep retries.",
            "Inspect client-id traffic before increasing rate limits.",
        ],
        "short_link_missing": [
            "Verify Firestore shortener config and permissions.",
            "Check shortener_error logs.",
        ],
    }
    return plans.get(code, ["Inspect Cloud Run logs using the request id, then rerun the smoke check."])


def _normalize_base_url(
    url: str,
    *,
    allow_non_production: bool = False,
    allow_non_prod: Optional[bool] = None,
) -> str:
    if allow_non_prod is not None:
        allow_non_production = allow_non_prod
    candidate = (url or "").strip() or DEFAULT_BASE_URL
    parsed = parse.urlparse(candidate)
    if allow_non_production and parsed.scheme in {"http", "https"} and parsed.hostname:
        return candidate.rstrip("/")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "a.retn.kr":
        raise AgentSmokeError("invalid_base_url", f"base URL must be https://a.retn.kr: {candidate}")
    return candidate.rstrip("/")


def normalize_base_url(url: str, *, allow_non_prod: bool = False) -> str:
    return _normalize_base_url(url, allow_non_prod=allow_non_prod)


def _headers(*, client_id: str, surface: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": os.getenv("OPENCLAW_SHOPPING_USER_AGENT", DEFAULT_USER_AGENT),
        "X-OpenClaw-Client-Id": client_id,
        "X-OpenClaw-Surface": surface,
        "X-OpenClaw-Version": os.getenv("OPENCLAW_SHOPPING_CLIENT_VERSION", "agent-smoke"),
    }


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
    req = request.Request(url=url, data=body, headers=_headers(client_id=client_id, surface=surface), method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        code = _classify_http_error(exc.code, raw)
        raise AgentSmokeError(code, f"{method} {url} returned HTTP {exc.code}", status=exc.code) from exc
    except error.URLError as exc:
        raise AgentSmokeError("network_error", f"{method} {url} failed: {exc.reason}") from exc


def _check_short_redirect(
    short_deeplink: str,
    *,
    timeout: int,
    client_id: str,
    surface: str,
) -> Dict[str, Any]:
    opener = request.build_opener(NoRedirectHandler)
    req = request.Request(url=short_deeplink, headers=_headers(client_id=client_id, surface=surface), method="HEAD")
    try:
        with opener.open(req, timeout=timeout) as response:
            return {"status": response.status, "location": response.headers.get("Location")}
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return {"status": exc.code, "location": exc.headers.get("Location")}
        raw = exc.read().decode("utf-8", errors="replace")
        code = _classify_http_error(exc.code, raw)
        raise AgentSmokeError(code, f"HEAD {short_deeplink} returned HTTP {exc.code}", status=exc.code) from exc
    except error.URLError as exc:
        raise AgentSmokeError("shortlink_network_error", f"HEAD {short_deeplink} failed: {exc.reason}") from exc


def _extract_short_deeplink(payload: Dict[str, Any]) -> str:
    candidates = []
    if isinstance(payload.get("best_fit"), dict):
        candidates.append(payload["best_fit"])
    if isinstance(payload.get("shortlist"), list):
        candidates.extend(item for item in payload["shortlist"] if isinstance(item, dict))
    if isinstance(payload.get("recommendations"), list):
        candidates.extend(item for item in payload["recommendations"] if isinstance(item, dict))
    if not candidates:
        raise AgentSmokeError("backend_empty_result", "public assist returned no recommendation payload")
    return next((str(item.get("short_deeplink")) for item in candidates if item.get("short_deeplink")), "")


def recovery_plan(kind: str) -> list[str]:
    plans = {
        "cloudflare_blocked": "Check Cloudflare WAF/Bot rules for agent User-Agent traffic.",
        "client_not_allowlisted": "Redeploy with the caller in OPENCLAW_SHOPPING_CLIENT_ALLOWLIST.",
        "rate_limited": "Back off callers and inspect retry behavior before raising public limits.",
        "backend_empty_result": "Check Coupang API credentials, quota, and search response shape.",
        "short_link_missing": "Check Firestore shortener env and write permissions.",
        "short_link_redirect_failed": "Check Firestore short_links documents and /s/<slug> route logs.",
    }
    return [plans.get(kind, "Inspect Cloud Run logs, Cloudflare events, and the latest deployed revision.")]


def run_agent_smoke(
    base_url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    client_id: str = "smoke-test",
    surface: str = "cli",
    query: str = DEFAULT_QUERY,
    skip_assist: bool = False,
    allow_non_production: bool = False,
    allow_non_prod: Optional[bool] = None,
    require_shortlink: bool = True,
) -> Dict[str, Any]:
    if allow_non_prod is not None:
        allow_non_production = allow_non_prod
    base = _normalize_base_url(base_url, allow_non_production=allow_non_production)
    checks = []

    health = _request_json("GET", f"{base}/health", timeout=timeout, client_id=client_id, surface=surface)
    health_ok = bool(health.get("ok"))
    checks.append({"name": "health", "ok": health_ok, "request_id": health.get("requestId")})
    if not health_ok:
        raise AgentSmokeError("health_failed", "health endpoint did not return ok=true")

    openapi = _request_json("GET", f"{base}/openapi.json", timeout=timeout, client_id=client_id, surface=surface)
    paths = openapi.get("paths") or {}
    missing = []
    for path in (
        "/health",
        "/v1/public/assist",
        "/v1/public/search",
        "/v1/public/goldbox",
        "/v1/public/best/{category_id}",
        "/s/{slug}",
    ):
        if path == "/v1/public/best/{category_id}":
            if path not in paths and "/v1/public/best/{category}" not in paths:
                missing.append(path)
            continue
        if path not in paths:
            missing.append(path)
    checks.append({"name": "openapi", "ok": not missing, "missing": missing})
    if missing:
        raise AgentSmokeError("openapi_contract_missing", f"openapi missing paths: {', '.join(missing)}")

    if skip_assist:
        return {"ok": True, "base_url": base, "assist_checked": False, "checks": checks}

    assist = _request_json(
        "POST",
        f"{base}/v1/public/assist",
        timeout=timeout,
        client_id=client_id,
        surface=surface,
        payload={"query": query, "limit": 1},
    )
    short_deeplink = _extract_short_deeplink(assist)
    checks.append(
        {
            "name": "public_assist",
            "ok": True,
            "request_id": assist.get("requestId"),
            "has_short_deeplink": bool(short_deeplink),
        }
    )
    if require_shortlink and not short_deeplink:
        raise AgentSmokeError("short_link_missing", "public assist did not return short_deeplink")

    if short_deeplink:
        redirect = _check_short_redirect(short_deeplink, timeout=timeout, client_id=client_id, surface=surface)
        status = int(redirect.get("status") or 0)
        ok = status in {301, 302, 303, 307, 308} and bool(redirect.get("location"))
        checks.append({"name": "shortlink_redirect", "ok": ok, "status": status})
        if not ok:
            raise AgentSmokeError("short_link_redirect_failed", "short link did not redirect with Location", status=status)

    return {"ok": True, "base_url": base, "assist_checked": True, "checks": checks}


def smoke(**kwargs: Any) -> Dict[str, Any]:
    return run_agent_smoke(**kwargs)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an agent-facing hosted backend smoke test.")
    parser.add_argument("--base-url", default=os.getenv("OPENCLAW_SHOPPING_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--client-id", default=os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "smoke-test"))
    parser.add_argument("--surface", default=os.getenv("OPENCLAW_SHOPPING_SURFACE", "cli"))
    parser.add_argument("--skip-assist", action="store_true")
    parser.add_argument("--allow-non-prod", "--allow-non-production", dest="allow_non_production", action="store_true")
    parser.add_argument("--no-require-shortlink", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        result = run_agent_smoke(
            args.base_url,
            timeout=args.timeout,
            client_id=args.client_id,
            surface=args.surface,
            query=args.query,
            skip_assist=args.skip_assist,
            allow_non_production=args.allow_non_production,
            require_shortlink=not args.no_require_shortlink,
        )
    except AgentSmokeError as exc:
        result = {"ok": False, "failure_kind": exc.error_code, "status": exc.status, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
