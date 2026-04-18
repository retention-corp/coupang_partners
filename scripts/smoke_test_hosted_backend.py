#!/usr/bin/env python3
import argparse
import json
import os
from typing import Any, Dict, Optional
from urllib import parse, request
from urllib.error import HTTPError, URLError

DEFAULT_BASE_URL = "https://a.retn.kr"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_QUERY = "retn hosted smoke test lightweight mask"


class SmokeTestError(RuntimeError):
    pass


def _auth_token() -> Optional[str]:
    singular = (os.getenv("OPENCLAW_SHOPPING_API_TOKEN") or "").strip()
    if singular:
        return singular
    plural = (os.getenv("OPENCLAW_SHOPPING_API_TOKENS") or "").strip()
    if not plural:
        return None
    return next((token.strip() for token in plural.split(",") if token.strip()), None)


def _normalize_base_url(url: str) -> str:
    candidate = (url or "").strip() or DEFAULT_BASE_URL
    parsed = parse.urlparse(candidate)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "a.retn.kr":
        raise SmokeTestError(f"smoke test base URL must be https://a.retn.kr in production mode: {candidate}")
    return candidate.rstrip("/")


def _request_json(
    method: str,
    url: str,
    *,
    timeout: int,
    token: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-OpenClaw-Client-Id": os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "smoke-test"),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        detail = raw or exc.reason
        raise SmokeTestError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SmokeTestError(f"{method} {url} failed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"{method} {url} returned invalid JSON: {raw}") from exc


def smoke_test(base_url: str, *, timeout: int, token: Optional[str], require_auth: bool, query: str) -> Dict[str, Any]:
    normalized_base_url = _normalize_base_url(base_url)
    checks = []

    health_payload = _request_json("GET", normalized_base_url + "/health", timeout=timeout)
    if not health_payload.get("ok"):
        raise SmokeTestError("health endpoint did not return ok=true")
    checks.append({"name": "health", "request_id": health_payload.get("requestId")})

    assist_payload = _request_json(
        "POST",
        normalized_base_url + "/v1/public/assist",
        timeout=timeout,
        payload={"query": query, "limit": 1},
    )
    shortlist = assist_payload.get("shortlist") or []
    if not assist_payload.get("best_fit") and not shortlist:
        raise SmokeTestError("public assist smoke test returned no recommendation payload")
    checks.append(
        {
            "name": "public_assist",
            "request_id": assist_payload.get("requestId"),
            "recommendation_count": len(shortlist) or (1 if assist_payload.get("best_fit") else 0),
        }
    )

    if not token:
        if require_auth:
            raise SmokeTestError("authenticated smoke test requested but no OPENCLAW_SHOPPING_API_TOKEN(S) available")
        return {"ok": True, "base_url": normalized_base_url, "auth_checked": False, "checks": checks}

    if require_auth:
        summary_payload = _request_json(
            "GET",
            normalized_base_url + "/v1/admin/summary",
            timeout=timeout,
            token=token,
        )
        if "total_short_links" not in summary_payload:
            raise SmokeTestError("admin summary smoke test did not include total_short_links")
        checks.append(
            {
                "name": "admin_summary",
                "request_id": summary_payload.get("requestId"),
                "total_short_links": summary_payload.get("total_short_links"),
            }
        )
    return {"ok": True, "base_url": normalized_base_url, "auth_checked": True, "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test the hosted shopping backend.")
    parser.add_argument("--base-url", default=os.getenv("PUBLIC_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--require-auth", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = smoke_test(
            args.base_url,
            timeout=args.timeout,
            token=_auth_token(),
            require_auth=args.require_auth,
            query=args.query,
        )
    except SmokeTestError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
