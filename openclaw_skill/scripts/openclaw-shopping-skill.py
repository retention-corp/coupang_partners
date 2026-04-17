#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib import parse, request
from urllib.error import HTTPError, URLError

DEFAULT_HOSTED_BACKEND = "https://a.retn.kr"
DEFAULT_ASSIST_PATH = "/v1/public/assist"
DEFAULT_INTERNAL_ASSIST_PATH = "/internal/v1/assist"
DEFAULT_INTERNAL_DEEPLINK_PATH = "/internal/v1/deeplinks"
DEFAULT_SEARCH_PATH = "/v1/public/search"
DEFAULT_GOLDBOX_PATH = "/v1/public/goldbox"
DEFAULT_BEST_PATH = "/v1/public/best"

CATEGORY_MAP = {
    "여성패션": "1001", "여성": "1001", "여성의류": "1001",
    "남성패션": "1002", "남성": "1002", "남성의류": "1002",
    "유아동패션": "1003", "아동패션": "1003", "키즈패션": "1003",
    "신발": "1007", "패션잡화": "1007",
    "뷰티": "1010", "화장품": "1010", "beauty": "1010",
    "출산/유아동": "1011", "출산": "1011", "유아동": "1011", "육아": "1011", "아기": "1011",
    "식품": "1012", "신선식품": "1012", "food": "1012",
    "주방용품": "1013", "주방": "1013",
    "생활용품": "1014", "생활": "1014",
    "문구/오피스": "1015", "문구": "1015", "오피스": "1015", "사무용품": "1015",
    "가전디지털": "1016", "전자제품": "1016", "가전": "1016", "전자": "1016", "electronics": "1016", "디지털": "1016",
    "스포츠/레저": "1017", "스포츠": "1017", "레저": "1017", "sports": "1017", "헬스": "1017", "건강": "1017",
    "자동차용품": "1018", "자동차": "1018", "차량용품": "1018", "차량": "1018",
}


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


def _http_json(url: str, *, method: str = "GET", payload=None):
    _validate_backend_url(url)
    client_id = os.getenv("OPENCLAW_SHOPPING_CLIENT_ID", "openclaw-skill")
    headers = {"X-OpenClaw-Client-Id": client_id}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        auth_token = _auth_token()
        if auth_token and _use_internal_api():
            headers["Authorization"] = f"Bearer {auth_token}"
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8") or exc.reason
        raise RuntimeError(f"backend returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc


def _post_json(url: str, payload):
    return _http_json(url, method="POST", payload=payload)


def _get_json(url: str):
    return _http_json(url, method="GET")


def _resolve_category_id(category: str) -> str:
    return CATEGORY_MAP.get(category.strip().lower(), category.strip())


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

    deeplinks = subparsers.add_parser("deeplinks")
    deeplinks.add_argument("--backend", default=_backend_base_url())
    deeplinks.add_argument("--url", action="append", required=True)

    search_cmd = subparsers.add_parser("search")
    search_cmd.add_argument("--backend", default=_backend_base_url())
    search_cmd.add_argument("--keyword", required=True)
    search_cmd.add_argument("--rocket-only", action="store_true", default=False)
    search_cmd.add_argument("--max-price", type=int, default=None)
    search_cmd.add_argument("--sort", default="SIM", choices=["SIM", "SALE", "LOW", "HIGH"])
    search_cmd.add_argument("--limit", type=int, default=5)

    goldbox_cmd = subparsers.add_parser("goldbox")
    goldbox_cmd.add_argument("--backend", default=_backend_base_url())

    best_cmd = subparsers.add_parser("best")
    best_cmd.add_argument("--backend", default=_backend_base_url())
    best_cmd.add_argument("--category", required=True, help="카테고리명 또는 ID (예: 전자제품, 뷰티, 1001)")

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
        elif args.command == "deeplinks":
            result = _post_json(backend_base_url + DEFAULT_INTERNAL_DEEPLINK_PATH, {"urls": args.url})
        elif args.command == "search":
            payload = {
                "keyword": args.keyword,
                "rocket_only": args.rocket_only,
                "max_price": args.max_price,
                "sort": args.sort,
                "limit": args.limit,
            }
            result = _post_json(backend_base_url + DEFAULT_SEARCH_PATH, payload)
        elif args.command == "goldbox":
            result = _get_json(backend_base_url + DEFAULT_GOLDBOX_PATH)
        elif args.command == "best":
            category_id = _resolve_category_id(args.category)
            result = _get_json(f"{backend_base_url}{DEFAULT_BEST_PATH}/{category_id}")
        else:
            result = _post_json(backend_base_url + DEFAULT_INTERNAL_DEEPLINK_PATH, {"urls": args.url})
        output = {"ok": True, "data": result}
        if isinstance(result, dict) and "disclosure" in result:
            output["disclosure"] = result["disclosure"]
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": {"message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
