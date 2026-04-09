import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from analytics import AnalyticsStore
from client import CoupangPartnersClient
from recommendation import build_assist_response, build_search_queries, normalize_request, recommend_products


class BackendError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


class ShoppingBackend:
    def __init__(self, *, adapter: Any, analytics_store: AnalyticsStore) -> None:
        self.adapter = adapter
        self.analytics_store = analytics_store

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "openclaw-shopping-backend",
            "version": "mvp",
        }

    def assist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
        recommendations = recommend_products(
            query=query,
            products=_filter_products(products, normalized.get("avoid", [])),
            budget=normalized["budget"],
            evidence_snippets=evidence_snippets,
            top_n=normalized["limit"],
        )
        query_id = self.analytics_store.record_assist(
            query_text=query,
            budget=normalized["budget"],
            category=normalized["category"],
            evidence_snippets=evidence_snippets,
            recommendations=recommendations,
        )
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
        if hasattr(self.adapter, "deeplink"):
            response = self.adapter.deeplink(urls)
            return {"ok": True, "data": response}
        raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Adapter does not support deeplink().")

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
        return self.analytics_store.get_summary()

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


class _Handler(BaseHTTPRequestHandler):
    backend: ShoppingBackend

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/healthz":
                self._send_json(HTTPStatus.OK, self.backend.health())
                return
            if self.path == "/v1/admin/summary":
                self._send_json(HTTPStatus.OK, self.backend.summary())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except BackendError as exc:
            self._send_json(exc.status, {"error": exc.message})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            if self.path in ("/v1/assist", "/v1/recommendations"):
                self._send_json(HTTPStatus.OK, self.backend.assist(payload))
                return
            if self.path == "/v1/events":
                self._send_json(HTTPStatus.OK, self.backend.record_event(payload))
                return
            if self.path == "/v1/deeplinks":
                self._send_json(HTTPStatus.OK, self.backend.deeplinks(payload))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except BackendError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_json(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        return json.loads(raw)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _extract_products(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        for key in ("products", "productData", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    for key in ("products", "productData", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def _filter_products(products: List[Dict[str, Any]], avoid_terms: List[str]) -> List[Dict[str, Any]]:
    if not avoid_terms:
        return products
    filtered: List[Dict[str, Any]] = []
    for product in products:
        haystack = " ".join(
            [
                str(product.get("productName") or product.get("title") or ""),
                str(product.get("brand") or product.get("vendor") or ""),
            ]
        )
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


def build_server(*, host: str, port: int, adapter: Any, db_path: str) -> ThreadingHTTPServer:
    analytics_store = AnalyticsStore(db_path)
    backend = ShoppingBackend(adapter=adapter, analytics_store=analytics_store)
    handler = type("OpenClawRequestHandler", (_Handler,), {"backend": backend})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def build_backend_from_env() -> ShoppingBackend:
    db_path = os.getenv("OPENCLAW_SHOPPING_DB_PATH", ".data/openclaw-shopping.sqlite3")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return ShoppingBackend(
        adapter=CoupangPartnersClient.from_env(),
        analytics_store=AnalyticsStore(db_path),
    )


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    resolved_host = host or os.getenv("OPENCLAW_SHOPPING_HOST", "127.0.0.1")
    resolved_port = port or int(os.getenv("OPENCLAW_SHOPPING_PORT", "8765"))
    backend = build_backend_from_env()
    handler = type("OpenClawRequestHandler", (_Handler,), {"backend": backend})
    server = ThreadingHTTPServer((resolved_host, resolved_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
