import json
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from analytics import AnalyticsStore, build_analytics_store_from_env
from client import CoupangPartnersClient
from economics import build_economics_summary
from recommendation import (
    DISCLOSURE_TEXT,
    _coerce_int as _coerce_int_value,
    build_assist_response,
    build_search_queries,
    infer_exclusion_terms,
    normalize_request,
    recommend_products,
)
from product_page_evidence import enrich_products_with_page_evidence
from security import (
    build_rate_limiter_for_mode,
    build_rate_limiter_from_env,
    generate_request_id,
    is_client_allowlisted,
    log_event,
    normalize_client_ip,
    parse_bearer_token,
    rate_limit_key,
    shopping_auth_required_from_env,
    shopping_api_tokens_from_env,
    shopping_client_allowlist_enabled_from_env,
    shopping_client_allowlist_from_env,
    summarize_client,
    validate_deeplink_url,
    validate_payload_limits,
)
from url_shortener import BuiltinShortener, FirestoreShortener, UrlShortener


class BackendError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class ShoppingBackend:
    def __init__(
        self,
        *,
        adapter: Any,
        analytics_store: AnalyticsStore,
        shortener: Optional[UrlShortener] = None,
        allowed_deeplink_hosts: Optional[List[str]] = None,
    ) -> None:
        self.adapter = adapter
        self.analytics_store = analytics_store
        self.shortener = shortener
        self.allowed_deeplink_hosts = allowed_deeplink_hosts or [
            "coupang.com",
            "link.coupang.com",
        ]
        self._cache_ttl = _response_cache_ttl_from_env()
        self._cache_lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def _cache_get_or_compute(self, key: str, compute: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        if self._cache_ttl <= 0:
            return compute()
        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
        value = compute()
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + self._cache_ttl, value)
        return value

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "openclaw-shopping-backend",
            "version": "mvp",
        }

    def assist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload_error = validate_payload_limits(payload)
        if payload_error:
            raise BackendError(HTTPStatus.BAD_REQUEST, payload_error)
        if (payload.get("vertical") or "").strip().lower() == "book":
            return self._book_assist(payload)
        normalized = normalize_request(payload)
        query = normalized["query"]
        if not query:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'query' is required")
        evidence_snippets = _normalize_evidence_snippets(payload.get("evidence_snippets") or [])
        search_plan = build_search_queries(normalized)
        products = self._search_products(
            query=query,
            search_plan=search_plan,
        )
        products = enrich_products_with_page_evidence(
            products,
            max_products=_page_evidence_max_products_from_env(),
            timeout_seconds=_page_evidence_timeout_seconds_from_env(),
        )
        recommendations = recommend_products(
            query=query,
            products=_filter_products(
                products,
                infer_exclusion_terms(normalized),
                normalized.get("must_have"),
            ),
            budget=normalized["budget"],
            evidence_snippets=evidence_snippets,
            top_n=normalized["limit"],
            intent_type=normalized.get("intent_type"),
            sort_key=normalized.get("sort_key"),
            sort_direction=normalized.get("sort_direction"),
        )
        recommendations = self._attach_short_links(recommendations)
        query_id: Optional[str] = None
        try:
            query_id = self.analytics_store.record_assist(
                query_text=query,
                budget=normalized["budget"],
                category=normalized["category"],
                evidence_snippets=evidence_snippets,
                recommendations=recommendations,
            )
        except Exception as exc:
            log_event("analytics_error", stage="record_assist", query=query, error=str(exc))
        return build_assist_response(
            normalized=normalized,
            search_plan=search_plan,
            recommendations=recommendations,
            query_id=query_id,
        )

    def deeplinks(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        urls = payload.get("urls") or payload.get("coupangUrls") or []
        if not isinstance(urls, list) or not urls:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'urls' must be a non-empty list")
        invalid_urls = [url for url in urls if not validate_deeplink_url(str(url), self.allowed_deeplink_hosts)]
        if invalid_urls:
            raise BackendError(HTTPStatus.BAD_REQUEST, "Only approved Coupang URLs may be shortened")
        if hasattr(self.adapter, "deeplink"):
            response = self.adapter.deeplink(urls)
            return {"ok": True, "data": self._attach_short_links_to_deeplink_response(response)}
        raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Adapter does not support deeplink().")

    def goldbox(self) -> Dict[str, Any]:
        if not hasattr(self.adapter, "get_goldbox"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Adapter does not support get_goldbox().")

        def _compute() -> Dict[str, Any]:
            raw = self.adapter.get_goldbox()
            products = _extract_products(raw)
            normalized = [_normalize_search_product(p) for p in products]
            enriched = self._attach_short_links(normalized)
            return {
                "ok": True,
                "data": {
                    "deals": enriched,
                    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "disclosure": DISCLOSURE_TEXT,
            }

        return self._cache_get_or_compute("goldbox", _compute)

    def search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        keyword = (payload.get("keyword") or "").strip()
        if not keyword:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'keyword' is required")
        if len(keyword) > 200:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'keyword' must be 200 characters or fewer")
        rocket_only = bool(payload.get("rocket_only", False))
        max_price = _coerce_int_value(payload.get("max_price"))
        sort = (payload.get("sort") or "SIM").upper()
        if sort not in {"SIM", "SALE", "LOW", "HIGH"}:
            sort = "SIM"
        limit = max(1, min(int(payload.get("limit") or 5), 10))
        raw = self.adapter.search_products(keyword=keyword, limit=10)
        products = _extract_products(raw)
        if rocket_only:
            products = [p for p in products if p.get("isRocket") or p.get("is_rocket")]
        if max_price is not None:
            products = [p for p in products if (p.get("productPrice") or p.get("salePrice") or p.get("price") or 0) <= max_price]
        normalized = [_normalize_search_product(p) for p in products]
        if sort == "LOW":
            normalized.sort(key=lambda p: (p.get("price") is None, p.get("price") or float("inf")))
        elif sort == "HIGH":
            normalized.sort(key=lambda p: -(p.get("price") or 0))
        elif sort == "SALE":
            normalized.sort(key=lambda p: -(p.get("review_count") or 0))
        normalized = normalized[:limit]
        enriched = self._attach_short_links(normalized)
        return {
            "ok": True,
            "data": {"keyword": keyword, "results": enriched, "total": len(enriched)},
            "disclosure": DISCLOSURE_TEXT,
        }

    def best(self, category_id: str) -> Dict[str, Any]:
        if not hasattr(self.adapter, "get_bestcategories"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Adapter does not support get_bestcategories().")

        def _compute() -> Dict[str, Any]:
            raw = self.adapter.get_bestcategories(category_id)
            products = _extract_products(raw)
            normalized = [_normalize_search_product(p) for p in products]
            enriched = self._attach_short_links(normalized)
            return {
                "ok": True,
                "data": {"category_id": category_id, "products": enriched},
                "disclosure": DISCLOSURE_TEXT,
            }

        return self._cache_get_or_compute(f"best:{category_id}", _compute)

    def record_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_type = (payload.get("event_type") or "").strip()
        if not event_type:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'event_type' is required")
        event_id = self.analytics_store.record_event(
            event_type=event_type,
            query_id=payload.get("query_id"),
            recommendation_id=payload.get("recommendation_id"),
            metadata=payload.get("metadata") or {},
        )
        return {"ok": True, "event_id": event_id}

    def summary(self) -> Dict[str, Any]:
        summary = self.analytics_store.get_summary()
        try:
            if self.shortener:
                summary.update(self.shortener.get_summary())
            else:
                summary.setdefault("total_short_links", 0)
                summary.setdefault("total_short_link_clicks", 0)
        except Exception as exc:
            log_event("shortener_error", stage="summary", error=str(exc))
            summary.setdefault("total_short_links", 0)
            summary.setdefault("total_short_link_clicks", 0)
        summary["economics"] = build_economics_summary(summary)
        return summary

    def resolve_short_link(self, slug: str) -> Optional[str]:
        if hasattr(self.shortener, "resolve"):
            try:
                target = self.shortener.resolve(slug)
                if target and not validate_deeplink_url(target, self.allowed_deeplink_hosts):
                    log_event("shortener_error", stage="resolve_invalid_target", slug=slug, target=target)
                    return None
                return target
            except Exception as exc:
                log_event("shortener_error", stage="resolve", slug=slug, error=str(exc))
        return None

    def record_short_link_click(self, slug: str) -> None:
        if hasattr(self.shortener, "record_click"):
            try:
                self.shortener.record_click(slug)
            except Exception as exc:
                log_event("shortener_error", stage="record_click", slug=slug, error=str(exc))

    def _search_products(self, *, query: str, search_plan: List[str]) -> List[Dict[str, Any]]:
        if hasattr(self.adapter, "search"):
            return list(self.adapter.search(query=query, search_plan=search_plan))
        if hasattr(self.adapter, "search_products"):
            deduped: Dict[str, Dict[str, Any]] = {}
            for keyword in search_plan or [query]:
                response = self.adapter.search_products(keyword=keyword, limit=10)
                for product in _extract_products(response):
                    product_id = str(product.get("productId") or product.get("product_id") or product.get("id") or product.get("productName"))
                    deduped.setdefault(product_id, product)
            return list(deduped.values())
        raise BackendError(HTTPStatus.INTERNAL_SERVER_ERROR, "Adapter must define search() or search_products().")

    def _book_assist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Imported lazily so the book vertical never pulls its providers into cold paths.
        from book_reco.backend_integration import book_assist

        def _search(**kwargs: Any) -> Any:
            return self.adapter.search_products(**kwargs)

        def _shorten(url: str) -> str:
            if self.shortener is None:
                return url
            return self.shortener.shorten(url)

        def _validate_host(url: str) -> bool:
            return validate_deeplink_url(url, self.allowed_deeplink_hosts)

        response = book_assist(
            payload,
            search_products_fn=_search,
            shorten_fn=_shorten if self.shortener else None,
            validate_host_fn=_validate_host,
            disclosure_text=DISCLOSURE_TEXT,
        )

        try:
            self.analytics_store.record_assist(
                query_text=response.get("query", ""),
                budget=None,
                category="book",
                evidence_snippets=[],
                recommendations=response.get("recommendations", []),
            )
        except Exception as exc:
            log_event("analytics_error", stage="record_book_assist", error=str(exc))

        return response

    def _attach_short_links(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.shortener:
            return recommendations
        enriched: List[Dict[str, Any]] = []
        for item in recommendations:
            original = item.get("deeplink", "")
            shortened = original
            if original and validate_deeplink_url(original, self.allowed_deeplink_hosts):
                try:
                    shortened = self.shortener.shorten(original)
                except Exception as exc:
                    log_event("shortener_error", stage="shorten_recommendation", error=str(exc))
            elif original:
                log_event("shortener_error", stage="shorten_recommendation_invalid_target", target=original)
            enriched.append(
                {
                    **item,
                    "short_deeplink": shortened,
                }
            )
        return enriched

    def _attach_short_links_to_deeplink_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        if not self.shortener:
            return response
        data = response.get("data")
        if not isinstance(data, list):
            return response
        rewritten = []
        for item in data:
            original = item.get("shortenUrl") or item.get("shortUrl") or item.get("url") or item.get("originalUrl")
            shortened = original
            if original and validate_deeplink_url(original, self.allowed_deeplink_hosts):
                try:
                    shortened = self.shortener.shorten(original)
                except Exception as exc:
                    log_event("shortener_error", stage="shorten_deeplink_response", error=str(exc))
            elif original:
                log_event("shortener_error", stage="shorten_deeplink_response_invalid_target", target=original)
            rewritten.append(
                {
                    **item,
                    "shortenedShareUrl": shortened,
                }
            )
        return {**response, "data": rewritten}


class _Handler(BaseHTTPRequestHandler):
    backend: ShoppingBackend
    rate_limiter = build_rate_limiter_from_env()
    public_rate_limiter = build_rate_limiter_for_mode(public=True, authenticated=False)
    authenticated_rate_limiter = build_rate_limiter_for_mode(public=False, authenticated=True)
    admin_rate_limiter = build_rate_limiter_for_mode(public=False, authenticated=False)
    max_body_bytes = int(os.getenv("OPENCLAW_SHOPPING_MAX_BODY_BYTES", "65536"))

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_request(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request(head_only=False)

    def _handle_request(self, *, head_only: bool) -> None:
        request_id = generate_request_id()
        remote_addr = normalize_client_ip(self.client_address[0] if self.client_address else None)
        try:
            if self.path.startswith("/s/"):
                slug = self.path.split("/s/", 1)[1].split("?", 1)[0].strip()
                target = self.backend.resolve_short_link(slug)
                if not target:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Short link not found", "requestId": request_id})
                    return
                self.backend.record_short_link_click(slug)
                self.send_response(HTTPStatus.FOUND)
                self.send_header("X-Request-Id", request_id)
                self.send_header("Location", target)
                self.end_headers()
                log_event("shortlink_redirect", request_id=request_id, slug=slug, remote_addr=remote_addr)
                return
            if self.path in ("/health", "/healthz"):
                self._send_json(HTTPStatus.OK, {**self.backend.health(), "requestId": request_id}, head_only=head_only)
                return
            if self.path == "/v1/admin/summary":
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                self._send_json(HTTPStatus.OK, {**self.backend.summary(), "requestId": request_id}, head_only=head_only)
                return
            if self.path == "/v1/public/goldbox":
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.goldbox()
                log_event("goldbox_ok", request_id=request_id, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id}, head_only=head_only)
                return
            if self.path.startswith("/v1/public/best/"):
                category_id = self.path.split("/v1/public/best/", 1)[1].split("?", 1)[0].strip()
                if not category_id:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "category_id is required", "requestId": request_id}, head_only=head_only)
                    return
                if not category_id.isdigit():
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "category_id must be numeric", "requestId": request_id}, head_only=head_only)
                    return
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.best(category_id)
                log_event("best_ok", request_id=request_id, remote_addr=remote_addr, client=client_marker, category_id=category_id)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id}, head_only=head_only)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
        except BackendError as exc:
            log_event("request_error", request_id=request_id, path=self.path, remote_addr=remote_addr, status=exc.status, error=exc.message)
            self._send_json(exc.status, {"error": exc.message, "requestId": request_id}, head_only=head_only)
        except Exception as exc:
            log_event("request_error", request_id=request_id, path=self.path, remote_addr=remote_addr, status=500, error=str(exc))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error", "requestId": request_id}, head_only=head_only)

    def do_POST(self) -> None:  # noqa: N802
        request_id = generate_request_id()
        remote_addr = normalize_client_ip(self.client_address[0] if self.client_address else None)
        try:
            payload = self._read_json()
            if self.path == "/v1/public/search":
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.search(payload)
                log_event("search_ok", request_id=request_id, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if self.path in ("/v1/public/assist", "/v1/public/recommendations"):
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.assist(payload)
                log_event("assist_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if self.path in ("/v1/assist", "/v1/recommendations", "/internal/v1/assist", "/internal/v1/recommendations"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
                    return
                client_marker = self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.assist(payload)
                log_event("assist_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if self.path in ("/v1/events", "/internal/v1/events"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
                    return
                client_marker = self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.record_event(payload)
                log_event("event_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if self.path in ("/v1/deeplinks", "/internal/v1/deeplinks"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
                    return
                client_marker = self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.deeplinks(payload)
                log_event("deeplinks_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
        except BackendError as exc:
            log_event("request_error", request_id=request_id, path=self.path, remote_addr=remote_addr, status=exc.status, error=exc.message)
            self._send_json(exc.status, {"error": exc.message, "requestId": request_id})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body", "requestId": request_id})
        except Exception as exc:
            log_event("request_error", request_id=request_id, path=self.path, remote_addr=remote_addr, status=500, error=str(exc))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error", "requestId": request_id})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_json(self) -> Dict[str, Any]:
        raw_content_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_content_length)
        except (TypeError, ValueError) as exc:
            raise BackendError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header") from exc
        if content_length < 0:
            raise BackendError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header")
        if content_length > self.max_body_bytes:
            raise BackendError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        return json.loads(raw)

    def _send_json(self, status: int, payload: Dict[str, Any], *, head_only: bool = False) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("X-Request-Id", str(payload.get("requestId", "")))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if not head_only:
            self.wfile.write(encoded)

    def _authorize_public(self, *, request_id: str, remote_addr: str) -> str:
        client_id = self.headers.get("X-OpenClaw-Client-Id")
        allowlist = shopping_client_allowlist_from_env()
        allowlist_enabled = shopping_client_allowlist_enabled_from_env()
        if allowlist_enabled and not is_client_allowlisted(client_id, allowlist, allowlist_enabled):
            raise BackendError(HTTPStatus.FORBIDDEN, "Client is not allowlisted")
        client_marker = summarize_client(remote_addr, client_id, None)
        rate_limiter = self.public_rate_limiter or self.rate_limiter
        limiter_key = rate_limit_key(remote_addr, client_id, None, allowlisted_client=False)
        if rate_limiter and not rate_limiter.allow(limiter_key):
            raise BackendError(HTTPStatus.TOO_MANY_REQUESTS, "Rate limit exceeded")
        log_event("request_authorized", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
        return client_marker

    def _authorize_internal(self, *, request_id: str, remote_addr: str) -> str:
        api_tokens = shopping_api_tokens_from_env()
        token = parse_bearer_token(self.headers.get("Authorization"))
        client_id = self.headers.get("X-OpenClaw-Client-Id")
        client_marker = summarize_client(remote_addr, client_id, token)
        auth_required = shopping_auth_required_from_env()
        allowlist = shopping_client_allowlist_from_env()
        allowlist_enabled = shopping_client_allowlist_enabled_from_env()
        allowlisted_client = is_client_allowlisted(client_id, allowlist, allowlist_enabled)
        if allowlist_enabled and not allowlisted_client:
            raise BackendError(HTTPStatus.FORBIDDEN, "Client is not allowlisted")
        if auth_required and not api_tokens:
            raise BackendError(HTTPStatus.UNAUTHORIZED, "API auth is required but no token is configured on the server")
        if api_tokens and token not in api_tokens:
            raise BackendError(HTTPStatus.UNAUTHORIZED, "Missing or invalid bearer token")
        rate_limiter = self._pick_rate_limiter(token=token, internal=True)
        limiter_key = rate_limit_key(remote_addr, client_id, token, allowlisted_client=allowlisted_client)
        if rate_limiter and not rate_limiter.allow(limiter_key):
            raise BackendError(HTTPStatus.TOO_MANY_REQUESTS, "Rate limit exceeded")
        log_event("request_authorized", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
        return client_marker

    def _pick_rate_limiter(self, *, token: Optional[str], internal: bool):
        if self.path == "/v1/admin/summary":
            return self.admin_rate_limiter or self.rate_limiter
        if not internal:
            return self.public_rate_limiter or self.rate_limiter
        if token:
            return self.authenticated_rate_limiter or self.rate_limiter
        return self.public_rate_limiter or self.rate_limiter


def _normalize_search_product(product: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": product.get("productName") or product.get("title") or "",
        "price": product.get("productPrice") or product.get("salePrice") or product.get("price"),
        "is_rocket": bool(product.get("isRocket") or product.get("is_rocket")),
        "is_free_shipping": bool(product.get("isFreeShipping") or product.get("is_free_shipping")),
        "rating": product.get("ratingAverage") or product.get("rating") or 0,
        "review_count": product.get("reviewCount") or product.get("review_count") or 0,
        "deeplink": product.get("productUrl") or product.get("deeplink") or product.get("url") or "",
    }


def _extract_products(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("products", "productData", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    for key in ("products", "productData", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def _filter_products(products: List[Dict[str, Any]], avoid_terms: List[str], include_terms: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if not avoid_terms and not include_terms:
        return products
    filtered: List[Dict[str, Any]] = []
    for product in products:
        haystack = " ".join(
            [
                str(product.get("productName") or product.get("title") or ""),
                str(product.get("brand") or product.get("vendor") or ""),
                str(product.get("categoryName") or ""),
            ]
        )
        lowered_haystack = haystack.lower()
        if include_terms and not all(term.lower() in lowered_haystack for term in include_terms):
            continue
        if any(term and term in haystack for term in avoid_terms):
            continue
        filtered.append(product)
    return filtered


def _normalize_evidence_snippets(items: List[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({"text": text, "source": "user"})
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        normalized.append({"text": text, "source": item.get("source", "user")})
    return normalized


def build_server(
    *,
    host: str,
    port: int,
    adapter: Any,
    db_path: str,
    shortener: Optional[UrlShortener] = None,
    public_base_url: Optional[str] = None,
    allowed_deeplink_hosts: Optional[List[str]] = None,
) -> ThreadingHTTPServer:
    analytics_store = build_analytics_store_from_env(db_path=db_path)
    resolved_public_base_url = public_base_url or f"http://{host}:{port}"
    backend = ShoppingBackend(
        adapter=adapter,
        analytics_store=analytics_store,
        shortener=shortener or _build_shortener_from_env(db_path=db_path, public_base_url=resolved_public_base_url),
        allowed_deeplink_hosts=allowed_deeplink_hosts or _load_allowed_deeplink_hosts(),
    )
    handler = type("OpenClawRequestHandler", (_Handler,), {"backend": backend})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def build_backend_from_env() -> ShoppingBackend:
    db_path = os.getenv("OPENCLAW_SHOPPING_DB_PATH", ".data/openclaw-shopping.sqlite3")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    public_base_url = os.getenv("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", "http://127.0.0.1:8765")
    return ShoppingBackend(
        adapter=CoupangPartnersClient.from_env(),
        analytics_store=build_analytics_store_from_env(db_path=db_path),
        shortener=_build_shortener_from_env(db_path=db_path, public_base_url=public_base_url),
        allowed_deeplink_hosts=_load_allowed_deeplink_hosts(),
    )


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    resolved_host = host or os.getenv("OPENCLAW_SHOPPING_HOST", "0.0.0.0")
    resolved_port = port or int(os.getenv("PORT") or os.getenv("OPENCLAW_SHOPPING_PORT", "8765"))
    default_public_base_url = f"http://127.0.0.1:{resolved_port}"
    os.environ.setdefault("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", default_public_base_url)
    backend = build_backend_from_env()
    handler = type("OpenClawRequestHandler", (_Handler,), {"backend": backend})
    server = ThreadingHTTPServer((resolved_host, resolved_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()

def _build_shortener_from_env(*, db_path: str, public_base_url: str) -> Optional[UrlShortener]:
    provider = (os.getenv("OPENCLAW_SHOPPING_SHORTENER") or "").strip().lower()
    if provider in ("", "builtin", "local"):
        return BuiltinShortener(db_path=db_path, public_base_url=public_base_url)
    if provider in ("firestore", "gcp"):
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("OPENCLAW_GCP_PROJECT") or ""
        return FirestoreShortener(
            project_id=project_id,
            public_base_url=public_base_url,
            collection=os.getenv("OPENCLAW_SHORT_LINKS_COLLECTION", "short_links"),
            database=os.getenv("OPENCLAW_FIRESTORE_DATABASE", "(default)"),
            access_token=os.getenv("OPENCLAW_GCP_ACCESS_TOKEN"),
            emulator_host=os.getenv("FIRESTORE_EMULATOR_HOST"),
        )
    return None


def _load_allowed_deeplink_hosts() -> List[str]:
    raw = os.getenv("OPENCLAW_SHOPPING_ALLOWED_DEEPLINK_HOSTS", "coupang.com,link.coupang.com,www.coupang.com")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _page_evidence_max_products_from_env() -> int:
    raw = os.getenv("OPENCLAW_SHOPPING_PAGE_EVIDENCE_MAX_PRODUCTS", "3")
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(0, min(value, 5))


def _page_evidence_timeout_seconds_from_env() -> int:
    raw = os.getenv("OPENCLAW_SHOPPING_PAGE_EVIDENCE_TIMEOUT_SECONDS", "2")
    try:
        value = int(raw)
    except ValueError:
        return 2
    return max(1, min(value, 5))


def _response_cache_ttl_from_env() -> int:
    raw = os.getenv("OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS", "900")
    try:
        value = int(raw)
    except ValueError:
        return 900
    return max(0, value)


def _operator_routes_enabled() -> bool:
    value = (os.getenv("OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES") or "").strip().lower()
    if not value:
        return True
    return value in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    run_server()
