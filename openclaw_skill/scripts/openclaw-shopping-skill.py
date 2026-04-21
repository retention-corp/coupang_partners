#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib import parse, request
from urllib.error import HTTPError, URLError

DEFAULT_HOSTED_BACKEND = "https://a.retn.kr"
DEFAULT_ASSIST_PATH = "/v1/public/assist"
DEFAULT_GOLDBOX_PATH = "/v1/public/goldbox"
DEFAULT_BEST_PRODUCTS_PATH = "/v1/public/best-products"
DEFAULT_INTERNAL_ASSIST_PATH = "/internal/v1/assist"
DEFAULT_INTERNAL_DEEPLINK_PATH = "/internal/v1/deeplinks"


def _backend_base_url():
    return (
        os.getenv("OPENCLAW_SHOPPING_BASE_URL")
        or os.getenv("OPENCLAW_SHOPPING_BACKEND_URL")
        or os.getenv("SHOPPING_COPILOT_BASE_URL")
        or DEFAULT_HOSTED_BACKEND
    )


def _auth_token():
    singular = (os.getenv("OPENCLAW_SHOPPING_API_TOKEN") or "").strip()
    if singular:
        return singular
    plural = (os.getenv("OPENCLAW_SHOPPING_API_TOKENS") or "").strip()
    if not plural:
        return ""
    return next((token.strip() for token in plural.split(",") if token.strip()), "")


def _allowed_backend_hosts():
    env_hosts = (os.getenv("OPENCLAW_SHOPPING_ALLOWED_BACKEND_HOSTS") or "").strip()
    hosts = [host.strip().lower() for host in env_hosts.split(",") if host.strip()]
    hosts.extend(["a.retn.kr", "127.0.0.1", "localhost"])
    return tuple(dict.fromkeys(hosts))


def _allow_non_prod_backend() -> bool:
    value = (os.getenv("OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _use_internal_api() -> bool:
    value = (os.getenv("OPENCLAW_SHOPPING_USE_INTERNAL_API") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalize_backend_base_url(url: str) -> str:
    candidate = (url or "").strip() or DEFAULT_HOSTED_BACKEND
    if _allow_non_prod_backend():
        _validate_backend_url(candidate)
        return candidate.rstrip("/")

    try:
        parsed = parse.urlparse(candidate)
    except ValueError:
        return DEFAULT_HOSTED_BACKEND

    if parsed.scheme == "https" and (parsed.hostname or "").lower() == "a.retn.kr":
        return candidate.rstrip("/")
    return DEFAULT_HOSTED_BACKEND


def _validate_backend_url(url: str) -> None:
    try:
        parsed = parse.urlparse(url)
    except ValueError as exc:
        raise RuntimeError(f"backend URL is invalid: {url}") from exc

    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    if not host or scheme not in {"http", "https"}:
        raise RuntimeError(f"backend URL must be http or https: {url}")

    if host in {"127.0.0.1", "localhost"}:
        return
    if scheme != "https":
        raise RuntimeError(f"backend URL must use https outside localhost: {url}")

    allowed_hosts = _allowed_backend_hosts()
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        raise RuntimeError(f"backend host is not approved: {host}")


def _request_json(url: str, payload):
    headers = {"Content-Type": "application/json"}
    auth_token = _auth_token()
    client_id = os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "openclaw-skill")
    if auth_token and _use_internal_api():
        headers["Authorization"] = f"Bearer {auth_token}"
    headers["X-OpenClaw-Client-Id"] = client_id
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str):
    headers = {"X-OpenClaw-Client-Id": os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "openclaw-skill")}
    auth_token = _auth_token()
    if auth_token and _use_internal_api():
        headers["Authorization"] = f"Bearer {auth_token}"
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload):
    _validate_backend_url(url)
    try:
        return _request_json(url, payload)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") or exc.reason
        raise RuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc


def _fetch_json(url: str):
    _validate_backend_url(url)
    try:
        return _get_json(url)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") or exc.reason
        raise RuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    recommend = subparsers.add_parser("recommend")
    recommend.add_argument("--backend", default=_backend_base_url())
    recommend.add_argument("--query", required=True)
    recommend.add_argument("--price-max", type=int, default=None)
    recommend.add_argument("--limit", type=int, default=5)
    recommend.add_argument("--include-term", action="append", default=[])
    recommend.add_argument("--exclude-term", action="append", default=[])

    goldbox = subparsers.add_parser("goldbox")
    goldbox.add_argument("--backend", default=_backend_base_url())

    best_products = subparsers.add_parser("best-products")
    best_products.add_argument("--backend", default=_backend_base_url())
    best_products.add_argument("--category-id", type=int, default=1016)

    deeplinks = subparsers.add_parser("deeplinks")
    deeplinks.add_argument("--backend", default=_backend_base_url())
    deeplinks.add_argument("--url", action="append", required=True)

    args = parser.parse_args()
    backend_base_url = _normalize_backend_base_url(args.backend)
    if not backend_base_url:
        parser.error("--backend is required when OPENCLAW_SHOPPING_BASE_URL is not set")
    try:
        if args.command == "recommend":
            payload = {
                "query": args.query,
                "budget": args.price_max,
                "limit": args.limit,
                "constraints": {
                    "must_have": args.include_term,
                    "avoid": args.exclude_term,
                },
            }
            assist_path = DEFAULT_INTERNAL_ASSIST_PATH if _use_internal_api() else DEFAULT_ASSIST_PATH
            result = _post_json(backend_base_url + assist_path, payload)
        elif args.command == "goldbox":
            result = _fetch_json(backend_base_url + DEFAULT_GOLDBOX_PATH)
        elif args.command == "best-products":
            result = _fetch_json(backend_base_url + f"{DEFAULT_BEST_PRODUCTS_PATH}?categoryId={args.category_id}")
        else:
            deeplink_path = DEFAULT_INTERNAL_DEEPLINK_PATH
            result = _post_json(backend_base_url + deeplink_path, {"urls": args.url})
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": {"message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
