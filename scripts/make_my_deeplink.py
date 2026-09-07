#!/usr/bin/env python3
"""Re-mint any Coupang URL — including somebody else's affiliate short link —
as a deeplink that belongs to *you*.

Revenue is only attributed when the link is created through the Coupang
Partners open API with your own credentials, so this script always calls the
API; it never rewrites an `lptag` by hand. The share link handed back is your
own short link (a.retn.kr/s/...), not `link.coupang.com`.

Two modes:

  backend (default when an operator token is present)
      POST {ops base}/v1/deeplinks with a bearer token. The hosted backend owns
      the Coupang keys and the Firestore shortener, so nothing sensitive is
      needed locally.

        export OPENCLAW_SHOPPING_API_TOKEN=...
        export OPENCLAW_SHOPPING_OPS_BASE_URL=https://<ops-service-host>
        python3 scripts/make_my_deeplink.py https://link.coupang.com/a/gQxaDKRrXw

  direct
      Call the Coupang API from here with your keys, then shorten through the
      shortener configured in the environment.

        export COUPANG_ACCESS_KEY=... COUPANG_SECRET_KEY=...
        export OPENCLAW_SHOPPING_SHORTENER=firestore GOOGLE_CLOUD_PROJECT=...
        export OPENCLAW_SHOPPING_PUBLIC_BASE_URL=https://a.retn.kr
        python3 scripts/make_my_deeplink.py --mode direct <url> --sub-id blog

Standard-library only, consistent with the rest of the repo.
"""
import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse, request
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import _build_shortener_from_env  # noqa: E402
from client import CoupangApiError, CoupangPartnersClient  # noqa: E402
from recommendation import DISCLOSURE_TEXT  # noqa: E402
from security import validate_deeplink_url, validate_sub_id  # noqa: E402

ALLOWED_HOSTS = ("coupang.com", "link.coupang.com", "www.coupang.com")
AFFILIATE_HOSTS = {"link.coupang.com"}
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_PUBLIC_BASE_URL = "https://a.retn.kr"
MAX_REDIRECTS = 8
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Only identity-bearing query params survive normalization. Everything else on a
# shared link is the other partner's tracking (lptag/itime/traceid/pageKey/...),
# and carrying it into our own request would credit their affiliate id.
KEEP_QUERY_PARAMS = ("itemId", "vendorItemId")
_PRODUCT_ID_PATTERN = re.compile(r"/vp/products/(\d+)")
_META_REFRESH_PATTERN = re.compile(
    r"""<meta[^>]+http-equiv=['"]?refresh['"]?[^>]+content=['"][^'"]*url=([^'"]+)['"]""",
    re.IGNORECASE,
)
_JS_REDIRECT_PATTERN = re.compile(
    r"""(?:location\.(?:href|replace\()|window\.location\s*=)\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


class DeeplinkError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# short-link resolution + normalization
# --------------------------------------------------------------------------


def _is_affiliate_short_link(url: str) -> bool:
    host = (parse.urlparse(url).hostname or "").lower()
    return host in AFFILIATE_HOSTS


def _extract_body_redirect(html: str, base_url: str) -> Optional[str]:
    """Coupang short links sometimes land on an interstitial that redirects via
    <meta refresh> or JS instead of a 3xx."""
    for pattern in (_META_REFRESH_PATTERN, _JS_REDIRECT_PATTERN):
        match = pattern.search(html)
        if match:
            candidate = parse.urljoin(base_url, match.group(1).strip())
            if validate_deeplink_url(candidate, ALLOWED_HOSTS):
                return candidate
    return None


def resolve_url(url: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS, opener: Any = None) -> str:
    """Follow an affiliate short link until it lands on a coupang.com page."""
    if not validate_deeplink_url(url, ALLOWED_HOSTS):
        raise DeeplinkError(f"not a Coupang URL: {url}")
    if not _is_affiliate_short_link(url):
        return url

    urlopen = opener or request.urlopen
    current = url
    for _ in range(MAX_REDIRECTS):
        http_request = request.Request(
            url=current,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
            method="GET",
        )
        try:
            with urlopen(http_request, timeout=timeout) as response:
                landed = response.geturl()
                raw = response.read(200_000).decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise DeeplinkError(f"short link resolution failed ({exc.code}) for {current}") from exc
        except (URLError, OSError) as exc:
            raise DeeplinkError(f"short link resolution failed for {current}: {exc}") from exc

        if not validate_deeplink_url(landed, ALLOWED_HOSTS):
            raise DeeplinkError(f"short link left the Coupang domain: {landed}")
        if not _is_affiliate_short_link(landed):
            return landed
        nested = _extract_body_redirect(raw, landed)
        if not nested or nested == current:
            raise DeeplinkError(f"could not resolve short link to a product URL: {url}")
        current = nested

    raise DeeplinkError(f"too many redirects while resolving {url}")


def normalize_product_url(url: str) -> str:
    """Strip the previous partner's tracking params, keep product identity."""
    parsed = parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    netloc = "www.coupang.com" if host in {"coupang.com", "m.coupang.com", "www.coupang.com"} else parsed.netloc
    kept: List[Tuple[str, str]] = [
        (key, value)
        for key, value in parse.parse_qsl(parsed.query, keep_blank_values=False)
        if key in KEEP_QUERY_PARAMS
    ]
    return parse.urlunparse((parsed.scheme or "https", netloc, parsed.path, "", parse.urlencode(kept), ""))


def product_id_of(url: str) -> Optional[str]:
    match = _PRODUCT_ID_PATTERN.search(parse.urlparse(url).path)
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# minting: hosted operator backend, or the Coupang API directly
# --------------------------------------------------------------------------


def operator_token() -> Optional[str]:
    singular = (os.getenv("OPENCLAW_SHOPPING_API_TOKEN") or "").strip()
    if singular:
        return singular
    plural = (os.getenv("OPENCLAW_SHOPPING_API_TOKENS") or "").strip()
    if not plural:
        return None
    return next((token.strip() for token in plural.split(",") if token.strip()), None)


def ops_base_url() -> str:
    for name in (
        "OPENCLAW_SHOPPING_OPS_BASE_URL",
        "OPENCLAW_SHOPPING_BASE_URL",
        "OPENCLAW_SHOPPING_BACKEND_URL",
        "SHOPPING_COPILOT_BASE_URL",
    ):
        candidate = (os.getenv(name) or "").strip()
        if candidate:
            if parse.urlparse(candidate).scheme != "https":
                raise DeeplinkError(f"{name} must be an https URL: {candidate}")
            return candidate.rstrip("/")
    return DEFAULT_PUBLIC_BASE_URL


def public_base_url() -> str:
    return (os.getenv("OPENCLAW_SHOPPING_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).rstrip("/")


def mint_via_backend(
    urls: List[str],
    *,
    sub_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> List[Dict[str, Any]]:
    token = operator_token()
    if not token:
        raise DeeplinkError(
            "backend mode needs OPENCLAW_SHOPPING_API_TOKEN (or _TOKENS); "
            "use --mode direct with COUPANG_ACCESS_KEY/COUPANG_SECRET_KEY instead."
        )
    payload: Dict[str, Any] = {"urls": urls}
    if sub_id:
        payload["subId"] = sub_id
    http_request = request.Request(
        url=f"{ops_base_url()}/v1/deeplinks",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-OpenClaw-Surface": "cli",
        },
        method="POST",
    )
    urlopen = opener or request.urlopen
    try:
        with urlopen(http_request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DeeplinkError(f"operator backend rejected the request ({exc.code}): {detail}") from exc
    except (URLError, OSError) as exc:
        raise DeeplinkError(f"operator backend unreachable: {exc}") from exc

    inner = body.get("data") if isinstance(body, dict) else None
    data = inner.get("data") if isinstance(inner, dict) else inner
    if not isinstance(data, list):
        raise DeeplinkError(f"unexpected deeplink response: {body!r}")
    return data


def mint_direct(
    urls: List[str],
    *,
    sub_id: Optional[str] = None,
    client: Optional[Any] = None,
    shortener: Optional[Any] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    partners = client or CoupangPartnersClient.from_env(timeout=timeout)
    response = partners.deeplink(urls, sub_id=sub_id) if sub_id else partners.deeplink(urls)
    data = (response or {}).get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise DeeplinkError(f"unexpected deeplink response: {response!r}")

    if shortener is None:
        shortener = _build_shortener_from_env(
            db_path=os.getenv("OPENCLAW_SHOPPING_DB_PATH", ".data/openclaw-shopping.sqlite3"),
            public_base_url=public_base_url(),
        )
    if shortener is None:
        return data

    enriched: List[Dict[str, Any]] = []
    for item in data:
        target = item.get("shortenUrl") or item.get("landingUrl") or item.get("originalUrl") or ""
        share = target
        if target and validate_deeplink_url(target, ALLOWED_HOSTS):
            try:
                share = shortener.shorten(target) or target
            except Exception as exc:  # keep the affiliate URL rather than dropping the result
                print(f"warning: shortener failed, falling back to the Coupang URL: {exc}", file=sys.stderr)
        enriched.append({**item, "shortenedShareUrl": share})
    return enriched


def convert(
    urls: List[str],
    *,
    mode: str = "auto",
    sub_id: Optional[str] = None,
    resolve: bool = True,
    client: Optional[Any] = None,
    shortener: Optional[Any] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> List[Dict[str, Any]]:
    if sub_id is not None and not validate_sub_id(sub_id):
        raise DeeplinkError("--sub-id must be 1-32 chars of [A-Za-z0-9_-]")

    normalized = [
        normalize_product_url(resolve_url(raw, timeout=timeout, opener=opener) if resolve else raw)
        for raw in urls
    ]

    resolved_mode = mode
    if resolved_mode == "auto":
        resolved_mode = "backend" if operator_token() else "direct"
    if resolved_mode == "backend":
        minted = mint_via_backend(normalized, sub_id=sub_id, timeout=timeout, opener=opener)
    elif resolved_mode == "direct":
        minted = mint_direct(normalized, sub_id=sub_id, client=client, shortener=shortener, timeout=timeout)
    else:
        raise DeeplinkError(f"unknown mode: {mode}")

    results: List[Dict[str, Any]] = []
    for index, source in enumerate(urls):
        item = minted[index] if index < len(minted) else {}
        coupang_deeplink = item.get("shortenUrl") or item.get("shortUrl") or item.get("landingUrl") or ""
        results.append(
            {
                "input": source,
                "normalized_url": normalized[index],
                "product_id": product_id_of(normalized[index]),
                "mode": resolved_mode,
                "sub_id": sub_id,
                "coupang_deeplink": coupang_deeplink,
                "share_url": item.get("shortenedShareUrl") or coupang_deeplink,
                "disclosure": DISCLOSURE_TEXT,
            }
        )
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-mint any Coupang URL as your own Coupang Partners deeplink + short link.",
    )
    parser.add_argument("urls", nargs="+", help="Coupang product URLs or link.coupang.com short links.")
    parser.add_argument(
        "--mode",
        choices=("auto", "backend", "direct"),
        default="auto",
        help="auto (default): backend when an operator token is set, otherwise direct.",
    )
    parser.add_argument("--sub-id", default=None, help="Coupang channel/sub id for per-channel revenue reporting.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a human summary.")
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="Skip short-link resolution (inputs must already be coupang.com product URLs).",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-request timeout in seconds.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        results = convert(
            args.urls,
            mode=args.mode,
            sub_id=args.sub_id,
            resolve=not args.no_resolve,
            timeout=args.timeout,
        )
    except (DeeplinkError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except CoupangApiError as exc:
        print(f"error: Coupang API rejected the request ({exc.status_code}): {exc.payload}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    for result in results:
        print(f"입력        : {result['input']}")
        print(f"정규화      : {result['normalized_url']}")
        if result["product_id"]:
            print(f"상품 ID     : {result['product_id']}")
        if result["sub_id"]:
            print(f"채널(subId) : {result['sub_id']}")
        print(f"파트너스    : {result['coupang_deeplink'] or '(발급 실패)'}")
        print(f"내 숏링크   : {result['share_url'] or '(발급 실패)'}")
        print(result["disclosure"])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
