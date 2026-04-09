import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib import error, request

try:
    from .analytics import AnalyticsStore
    from .client import CoupangPartnersClient
    from .recommendation import rank_products
except ImportError:  # pragma: no cover - direct script fallback
    from analytics import AnalyticsStore
    from client import CoupangPartnersClient
    from recommendation import rank_products


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def normalize_search_results(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("productData", "products", "items"):
                if isinstance(data.get(key), list):
                    return list(data.get(key))
        for key in ("products", "items", "data"):
            if isinstance(payload.get(key), list):
                return list(payload.get(key))
    if isinstance(candidates, list):
        return list(candidates)
    return []


def attach_deeplinks(adapter: Any, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    urls = [product.get("productUrl") or product.get("url") for product in products]
    urls = [url for url in urls if url]
    if not urls or not hasattr(adapter, "deeplink"):
        return products

    try:
        response = adapter.deeplink(urls)
    except Exception:
        return products

    generated = {}
    for item in response.get("data", []) if isinstance(response, dict) else []:
        original = item.get("originalUrl") or item.get("coupangUrl")
        deeplink = item.get("shortenUrl") or item.get("deeplink") or item.get("landingUrl")
        if original and deeplink:
            generated[original] = deeplink

    enriched = []
    for product in products:
        original = product.get("productUrl") or product.get("url")
        if original and generated.get(original):
            product = {**product, "deeplink": generated[original]}
        enriched.append(product)
    return enriched


class ShoppingBackendService:
    def __init__(self, *, adapter: Any, analytics: AnalyticsStore) -> None:
        self.adapter = adapter
        self.analytics = analytics

    def assist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        query = (payload.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")

        evidence_snippets = payload.get("evidence") or []
        budget_max = payload.get("budget_max")
        category = payload.get("category")
        search_payload = self.adapter.search_products(keyword=query, limit=10)
        products = attach_deeplinks(self.adapter, normalize_search_results(search_payload))
        feedback = self.analytics.product_feedback()
        recommendations = rank_products(
            query,
            products,
            budget_max=float(budget_max) if budget_max is not None else None,
            evidence_snippets=evidence_snippets,
            product_feedback=feedback,
        )
        analytics_refs = self.analytics.log_assist(
            query_text=query,
            budget_max=float(budget_max) if budget_max is not None else None,
            category=category,
            constraints={
                "category": category,
                "result_count": len(recommendations),
            },
            evidence_snippets=[
                {
                    "source": snippet.get("source", "user_supplied") if isinstance(snippet, dict) else "user_supplied",
                    "snippet": snippet.get("snippet") if isinstance(snippet, dict) else str(snippet),
                    "score": snippet.get("score", 0.0) if isinstance(snippet, dict) else 0.0,
                    "risks": snippet.get("risks", []) if isinstance(snippet, dict) else [],
                }
                for snippet in evidence_snippets
            ],
            recommendations=recommendations,
        )
        for recommendation, recommendation_id in zip(recommendations, analytics_refs["recommendation_ids"]):
            recommendation["recommendation_id"] = recommendation_id

        return {
            "ok": True,
            "query": query,
            "query_id": analytics_refs["query_id"],
            "recommendations": recommendations,
            "summary": {
                "returned": len(recommendations),
                "candidate_count": len(products),
            },
        }

    def record_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_type = payload.get("event_type")
        if not event_type:
            raise ValueError("event_type is required")
        event_id = self.analytics.record_event(
            event_type,
            query_id=payload.get("query_id"),
            recommendation_id=payload.get("recommendation_id"),
            session_id=payload.get("session_id"),
            payload=payload.get("payload"),
        )
        return {"ok": True, "event_id": event_id}

    def summary(self) -> Dict[str, Any]:
        return {"ok": True, "summary": self.analytics.summary()}


def make_handler(service: ShoppingBackendService) -> type[BaseHTTPRequestHandler]:
    class ShoppingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                return json_response(self, HTTPStatus.OK, {"ok": True, "service": "openclaw-shopping-backend"})
            if self.path == "/v1/admin/summary":
                return json_response(self, HTTPStatus.OK, service.summary())
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = read_json_body(self)
                if self.path == "/v1/assist":
                    return json_response(self, HTTPStatus.OK, service.assist(payload))
                if self.path == "/v1/events":
                    return json_response(self, HTTPStatus.OK, service.record_event(payload))
                return json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            except ValueError as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive boundary
                json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return None

    return ShoppingHandler


def create_server(
    *,
    adapter: Any,
    db_path: str = "analytics.sqlite3",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    service = ShoppingBackendService(
        adapter=adapter,
        analytics=AnalyticsStore(db_path),
    )
    return ThreadingHTTPServer((host, port), make_handler(service))


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OpenClaw shopping backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db-path", default="analytics.sqlite3")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    server = create_server(
        adapter=CoupangPartnersClient.from_env(),
        db_path=args.db_path,
        host=args.host,
        port=args.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual operation
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
