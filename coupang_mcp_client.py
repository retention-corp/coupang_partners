"""Local Coupang MCP-compatible client backed by Coupang Partners APIs."""

from __future__ import annotations

import itertools
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib import error, parse, request

from client import CoupangPartnersClient

_DEFAULT_HOSTED_BACKEND = "https://a.retn.kr"
_PUBLIC_ASSIST_PATH = "/v1/public/assist"
_HOSTED_CLIENT_ID_DEFAULT = "openclaw-skill"


def _hosted_client_id() -> str:
    return os.getenv("OPENCLAW_SHOPPING_CLIENT_ID") or _HOSTED_CLIENT_ID_DEFAULT


def _hosted_base_url() -> str:
    return (
        os.getenv("OPENCLAW_SHOPPING_BASE_URL")
        or os.getenv("OPENCLAW_SHOPPING_BACKEND_URL")
        or _DEFAULT_HOSTED_BACKEND
    ).rstrip("/")


class _HostedAssistClient:
    """Thin stdlib urllib client against POST /v1/public/assist."""

    def __init__(self, *, base_url: str = "", timeout_seconds: int = 20) -> None:
        self._base_url = base_url or _hosted_base_url()
        self._timeout = timeout_seconds

    def search_products(self, *, keyword: str) -> list[dict[str, Any]]:
        url = self._base_url + _PUBLIC_ASSIST_PATH
        body = json.dumps({"query": keyword}).encode()
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Client-Id": _hosted_client_id(),
            },
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            raise McpError(
                f"Hosted backend HTTP {exc.code} for {url}: {exc.reason}"
            ) from exc
        except error.URLError as exc:
            raise McpError(f"Hosted backend unreachable ({url}): {exc.reason}") from exc

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise McpError("Hosted backend returned non-JSON response") from exc

        error_body = payload.get("error")
        if error_body:
            raise McpError(f"Hosted backend error: {error_body}")

        items: list[dict[str, Any]] = []
        best_fit = payload.get("best_fit")
        if isinstance(best_fit, dict):
            items.append(best_fit)
        shortlist = payload.get("shortlist") or payload.get("recommendations") or []
        for entry in shortlist:
            if entry is best_fit:
                continue
            items.append(entry)
        return [_map_hosted_item(item) for item in items]

    def get_goldbox(self) -> Any:
        raise McpError(
            "get_coupang_goldbox requires COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY. "
            "Hosted fallback only supports search-style tools."
        )

    def get_bestcategories(self, category_id: Any) -> Any:
        raise McpError(
            "get_coupang_bestcategories requires COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY. "
            "Hosted fallback only supports search-style tools."
        )


def _map_hosted_item(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    return {
        "productId": item.get("product_id"),
        "productName": item.get("title"),
        "productPrice": item.get("price"),
        "productUrl": item.get("short_deeplink") or item.get("deeplink"),
        "productImage": meta.get("productImage"),
        "isRocket": bool(meta.get("isRocket")),
        "rating": item.get("rating"),
        "reviewCount": item.get("review_count"),
        "vendor": item.get("vendor"),
        "metadata": meta,
    }

DEFAULT_MCP_ENDPOINT = "local://coupang-mcp"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


class McpError(RuntimeError):
    """Raised when a local MCP-compatible tool call cannot be completed."""


@dataclass
class McpResponse:
    payload: Dict[str, Any]
    session_id: Optional[str] = None


class CoupangMcpClient:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_MCP_ENDPOINT,
        timeout_seconds: int = 20,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        client_name: str = "coupang-product-search",
        client_version: str = "1.0",
        partners_client: Optional[Any] = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.protocol_version = protocol_version
        self.client_name = client_name
        self.client_version = client_version
        self._partners_client = partners_client
        self._session_id: Optional[str] = None
        self._initialized = False
        self._ids = itertools.count(1)

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def initialize(self) -> McpResponse:
        self._session_id = self._session_id or f"session-{uuid.uuid4().hex[:12]}"
        self._initialized = True
        return McpResponse(
            payload={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "result": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "serverInfo": {
                        "name": self.client_name,
                        "version": self.client_version,
                    },
                },
            },
            session_id=self._session_id,
        )

    def tools_list(self) -> McpResponse:
        if not self._initialized:
            self.initialize()
        return McpResponse(
            payload={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "result": {
                    "tools": [
                        {"name": "search_coupang_products"},
                        {"name": "search_coupang_rocket"},
                        {"name": "search_coupang_budget"},
                        {"name": "compare_coupang_products"},
                        {"name": "get_coupang_recommendations"},
                        {"name": "get_coupang_seasonal"},
                        {"name": "get_coupang_best_products"},
                        {"name": "get_coupang_goldbox"},
                    ]
                },
            },
            session_id=self._session_id,
        )

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> McpResponse:
        if not self._initialized:
            self.initialize()
        result = _dispatch_tool(self._get_partners_client(), name, arguments or {})
        return McpResponse(
            payload={
                "jsonrpc": "2.0",
                "id": next(self._ids),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False),
                        }
                    ]
                },
            },
            session_id=self._session_id,
        )

    def _get_partners_client(self) -> Any:
        if self._partners_client is None:
            force_hosted = (os.getenv("OPENCLAW_SHOPPING_FORCE_HOSTED") or "").strip() == "1"
            if not force_hosted:
                try:
                    self._partners_client = CoupangPartnersClient.from_env(
                        timeout=self.timeout_seconds
                    )
                except Exception:
                    force_hosted = True
            if force_hosted:
                self._partners_client = _HostedAssistClient(
                    timeout_seconds=self.timeout_seconds
                )
        return self._partners_client


def _dispatch_tool(client: Any, name: str, arguments: Dict[str, Any]) -> Any:
    if name == "search_coupang_products":
        return _search_products(client, keyword=_read_keyword(arguments))
    if name == "search_coupang_rocket":
        products = _search_products(client, keyword=_read_keyword(arguments))
        return [item for item in products if _is_rocket(item)]
    if name == "search_coupang_budget":
        products = _search_products(client, keyword=_read_keyword(arguments))
        min_price = _to_int(arguments.get("min_price"))
        max_price = _to_int(arguments.get("max_price"))
        return [item for item in products if _within_budget(item, min_price=min_price, max_price=max_price)]
    if name == "compare_coupang_products":
        terms = _split_compare_terms(_read_keyword(arguments))
        return {
            "terms": terms,
            "results": {term: _search_products(client, keyword=term)[:5] for term in terms},
        }
    if name == "get_coupang_recommendations":
        return _search_products(client, keyword=_read_keyword(arguments))[:10]
    if name == "get_coupang_seasonal":
        return _search_products(client, keyword=_read_keyword(arguments))[:10]
    if name == "get_coupang_best_products":
        category_id = arguments.get("category_id")
        if category_id not in (None, ""):
            return client.get_bestcategories(category_id)
        return _search_products(client, keyword=_read_keyword(arguments))[:10]
    if name == "get_coupang_goldbox":
        return client.get_goldbox()
    raise McpError(f"Unsupported tool: {name}")


def _search_products(client: Any, *, keyword: str) -> list[dict[str, Any]]:
    if not keyword:
        raise McpError("keyword is required")
    payload = client.search_products(keyword=keyword)
    return _extract_products(payload)


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("products", "productData", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    for key in ("products", "productData", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _read_keyword(arguments: Dict[str, Any]) -> str:
    return str(
        arguments.get("keyword")
        or arguments.get("query")
        or arguments.get("category")
        or arguments.get("season")
        or ""
    ).strip()


def _split_compare_terms(query: str) -> list[str]:
    normalized = query.replace("VS", "vs").replace(" Vs ", " vs ")
    terms = [term.strip() for term in normalized.split("vs") if term.strip()]
    return terms or [query]


def _is_rocket(product: Dict[str, Any]) -> bool:
    return bool(product.get("isRocket") or product.get("rocket") or product.get("rocket배송"))


def _within_budget(product: Dict[str, Any], *, min_price: Optional[int], max_price: Optional[int]) -> bool:
    price = _to_int(product.get("productPrice") or product.get("salePrice") or product.get("price"))
    if price is None:
        return False
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_tool_result(payload: Dict[str, Any]) -> Any:
    result = payload.get("result")
    if result is None:
        return payload
    if isinstance(result, dict) and "content" in result:
        return _normalize_content(result["content"])
    return result


def _normalize_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    normalized = []
    for item in content:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if item.get("type") == "text" and "text" in item:
            text = item["text"]
            try:
                normalized.append(json.loads(text))
            except (TypeError, ValueError):
                normalized.append(text)
            continue
        normalized.append(item)
    if len(normalized) == 1:
        return normalized[0]
    return normalized


def tool_argument_dict(pairs: Iterable[tuple[str, Any]]) -> Dict[str, Any]:
    return {key: value for key, value in pairs if value not in (None, "", [])}
