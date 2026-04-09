import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional, Tuple

from analytics import AnalyticsStore
from recommendation import recommend_products


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
        return {"ok": True, "service": "openclaw-shopping-backend"}

    def assist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        query = (payload.get("query") or "").strip()
        if not query:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'query' is required")
        budget = payload.get("budget")
        category = payload.get("category")
        evidence_snippets = payload.get("evidence_snippets") or []
        products = self._search_products(query=query, budget=budget, category=category)
        recommendations = recommend_products(
            query=query,
            products=products,
            budget=budget,
            evidence_snippets=evidence_snippets,
        )
        query_id = self.analytics_store.record_assist(
            query_text=query,
            budget=budget,
            category=category,
            evidence_snippets=evidence_snippets,
            recommendations=recommendations,
        )
        return {
            "query_id": query_id,
            "recommendations": recommendations,
            "risks": sorted({risk for item in recommendations for risk in item.get("risks", [])}),
            "summary": {
                "candidate_count": len(recommendations),
                "evidence_snippet_count": len(evidence_snippets),
            },
        }

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

    def _search_products(self, *, query: str, budget: Optional[int], category: Optional[str]) -> List[Dict[str, Any]]:
        if hasattr(self.adapter, "search"):
            return list(self.adapter.search(query=query, budget=budget, category=category))
        if hasattr(self.adapter, "search_products"):
            response = self.adapter.search_products(keyword=query, limit=10)
            return _extract_products(response)
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
            if self.path == "/v1/assist":
                self._send_json(HTTPStatus.OK, self.backend.assist(payload))
                return
            if self.path == "/v1/events":
                self._send_json(HTTPStatus.OK, self.backend.record_event(payload))
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


def build_server(*, host: str, port: int, adapter: Any, db_path: str) -> ThreadingHTTPServer:
    analytics_store = AnalyticsStore(db_path)
    backend = ShoppingBackend(adapter=adapter, analytics_store=analytics_store)
    handler = type("OpenClawRequestHandler", (_Handler,), {"backend": backend})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
