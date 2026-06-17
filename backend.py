import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import parse
from urllib.parse import parse_qs, urlsplit

from analytics import AnalyticsStore, build_analytics_store_from_env
from client import CoupangApiError, CoupangPartnersClient
from economics import (
    build_economics_summary,
    read_click_reconciliation_row,
    read_proxy_gmv_rows,
)
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


# Surface enum per spec acquihire-sprint-1mo decision D9. Enum members are the fixed
# identifiers surfaces should send on `x-openclaw-surface`; the `claw-*` wildcard prefix
# (e.g., claw-shopping) is accepted separately so forks of the openclaw skill can self-tag
# without waiting on an enum change.
_SURFACE_ENUM = frozenset(
    {
        "claude-code-skill",
        "chatgpt-gpt",
        "codex",
        "claude-project",
        "hermes-agent",
        "openclaw-skill",
        "cli",
        "mcp",
    }
)

# Warn at most once per unknown surface string to avoid log spam on high-volume clients.
# Module-level set is fine — `_Handler` is per-request but shares the interpreter.
_WARNED_UNKNOWN_SURFACES: set = set()
_WARNED_UNKNOWN_SURFACES_LOCK = threading.Lock()


def _normalize_surface(raw_value: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return (normalized_surface, surface_raw).

    - Empty/missing → ("unknown", None) so the column reflects "unattributed request" rather
      than "bad header value".
    - Value in the known enum or matching the `claw-*` prefix → (value, None).
    - Otherwise → ("unknown", original_trimmed_value). A warning is emitted once per
      unrecognized value per process lifetime.
    """

    if not raw_value:
        return "unknown", None
    trimmed = raw_value.strip()
    if not trimmed:
        return "unknown", None
    lowered = trimmed.lower()
    if lowered in _SURFACE_ENUM:
        return lowered, None
    if lowered.startswith("claw-") and len(lowered) > len("claw-"):
        return lowered, None
    with _WARNED_UNKNOWN_SURFACES_LOCK:
        first_seen = lowered not in _WARNED_UNKNOWN_SURFACES
        if first_seen:
            _WARNED_UNKNOWN_SURFACES.add(lowered)
    if first_seen:
        log_event("surface_enum_unknown", surface_raw=trimmed[:64])
    return "unknown", trimmed[:128]


def _query_param(query: str, name: str) -> Optional[str]:
    """Return the first value for `name` in a raw query string, or None.

    Thin wrapper around parse_qs so admin-route handlers don't each re-implement
    "parse one string param" — returning None keeps the "omitted" branch
    explicit at the call site (admin endpoints use it to mean 'latest').
    """

    if not query:
        return None
    values = parse_qs(query, keep_blank_values=False).get(name) or []
    if not values:
        return None
    first = (values[0] or "").strip()
    return first or None


def _first_forwarded_for(header_value: Optional[str]) -> Optional[str]:
    """Return the first IP from an `X-Forwarded-For` header, or None if absent.

    XFF is a comma-separated chain of `client, proxy1, proxy2, ...`. Cloud Run's
    front-end proxy terminates TCP, so `self.client_address` is always a link-local
    169.254.x.x peer — the first XFF entry is the real caller. We do not validate the
    IP shape here: normalize_client_ip downstream will coerce garbage into "unknown".
    """

    if not header_value:
        return None
    first = header_value.split(",", 1)[0].strip()
    return first or None


def _attribution_kwargs(attribution: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    """Translate the attribution context dict into kwargs accepted by AnalyticsStore.record_event.

    Returns an empty dict when `attribution` is None so `record_event` keeps its pre-spec
    behavior for callers that have not been updated yet (e.g., book_intel tests).
    """

    if not attribution:
        return {}
    return {
        "surface": attribution.get("surface"),
        "surface_raw": attribution.get("surface_raw"),
        "client_id": attribution.get("client_id"),
        "client_version": attribution.get("client_version"),
        "utm_source": attribution.get("utm_source"),
    }


def _anonymous_client_id(remote_addr: Optional[str]) -> str:
    """Deterministic fallback so anonymous traffic still gets a stable session bucket.

    SHA1 of the normalized IP → hex-truncated. Matches the 'anonymous-<IP hash>' fallback
    specified in acquihire-sprint-1mo D4/D9. SHA1 is not a security choice here; it's
    cheap and widely understood, and the input is a short IP string.
    """

    source = (remote_addr or "unknown").encode("utf-8")
    digest = hashlib.sha1(source).hexdigest()[:16]
    return f"anonymous-{digest}"


class BackendError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------- #
# OpenAPI 3.1 document (spec R1.2 / acquihire-sprint-1mo T2)
# ---------------------------------------------------------------------------- #
# Kept as a module-level constant so `GET /openapi.json` is a O(1) serialize. The
# schema intentionally uses only static values — no dynamic env-derived URLs — so
# consumers (GPT Store, Claude Code marketplace, Swagger UI) get a stable,
# cache-friendly document. Header schema per spec D9: surface enum, client_id,
# version, utm_source.

_OPENAPI_ATTRIBUTION_HEADER_PARAMS: List[Dict[str, Any]] = [
    {
        "name": "x-openclaw-surface",
        "in": "header",
        "description": (
            "Caller surface tag. Known values: claude-code-skill, chatgpt-gpt, codex, "
            "claude-project, hermes-agent, openclaw-skill, cli, mcp. Values matching the `claw-*` prefix "
            "(e.g., claw-shopping) are also accepted. Anything else is normalized server-side "
            "to 'unknown' with the raw value preserved in analytics (never rejected)."
        ),
        "required": False,
        "schema": {
            "type": "string",
            "enum": [
                "claude-code-skill",
                "chatgpt-gpt",
                "codex",
                "claude-project",
                "hermes-agent",
                "openclaw-skill",
                "cli",
                "mcp",
                "unknown",
            ],
            "x-openclaw-wildcard-prefix": "claw-",
        },
        "example": "chatgpt-gpt",
    },
    {
        "name": "x-openclaw-client-id",
        "in": "header",
        "description": (
            "Stable per-caller identifier (UUID v4 recommended). When missing, the server "
            "derives a deterministic `anonymous-<sha1(ip)[:16]>` fallback so anonymous "
            "traffic still gets a stable session bucket."
        ),
        "required": False,
        "schema": {"type": "string", "maxLength": 128},
        "example": "a8f1c2b4-1234-4abc-8def-0123456789ab",
    },
    {
        "name": "x-openclaw-version",
        "in": "header",
        "description": "Integrator-provided version string (best-effort). Truncated to 64 chars.",
        "required": False,
        "schema": {"type": "string", "maxLength": 64},
        "example": "1.2.3",
    },
]

_OPENAPI_UTM_QUERY_PARAM: Dict[str, Any] = {
    "name": "utm_source",
    "in": "query",
    "description": "Ad-campaign attribution source (paid-marketing rollups per spec D7/R4.4).",
    "required": False,
    "schema": {"type": "string", "maxLength": 128},
    "example": "x-daily-builder",
}


def _public_endpoint_parameters() -> List[Dict[str, Any]]:
    """Common parameters shared by every `/v1/public/*` endpoint (headers + utm)."""

    return list(_OPENAPI_ATTRIBUTION_HEADER_PARAMS) + [dict(_OPENAPI_UTM_QUERY_PARAM)]


_OPENAPI_DOCUMENT: Dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {
        "title": "OpenClaw Shopping Backend",
        "description": (
            "Korean agent-shopping intent routing layer. Public tokenless endpoints for "
            "agent surfaces (Claude Code skill, ChatGPT GPT, OpenAI Codex, MCP, CLI) to "
            "submit shopping intents, fetch goldbox/best deals, and resolve affiliate "
            "short links. All public endpoints accept the 4 attribution headers defined "
            "in acquihire-sprint-1mo spec D9 (see parameters). Affiliate disclosure "
            "(`DISCLOSURE_TEXT`) is returned on every response that surfaces an affiliate "
            "link — do not strip it."
        ),
        "version": "1.0.0",
        "contact": {"name": "Retention Inc", "url": "https://retn.kr"},
    },
    "servers": [
        {"url": "https://a.retn.kr", "description": "Production"},
    ],
    "paths": {
        "/health": {
            "get": {
                "summary": "Liveness probe",
                "description": "Unauthenticated liveness check. Returns service identity.",
                "responses": {
                    "200": {
                        "description": "Service is up",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "ok": {"type": "boolean"},
                                        "service": {"type": "string"},
                                        "version": {"type": "string"},
                                        "requestId": {"type": "string"},
                                    },
                                    "required": ["ok"],
                                }
                            }
                        },
                    }
                },
            }
        },
        "/v1/public/assist": {
            "post": {
                "summary": "Recommend products for a shopping intent",
                "description": (
                    "Primary shopping-intent endpoint. Accepts a natural-language query "
                    "plus optional constraints/evidence and returns ranked product "
                    "recommendations with affiliate deeplinks and short links. Set "
                    "`vertical=book` to route through the book_reco pipeline."
                ),
                "parameters": _public_endpoint_parameters(),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/AssistRequest"},
                            "example": {
                                "query": "30만원 이하 무선청소기, 원룸용",
                                "constraints": {"must_have": ["저소음"], "avoid": ["대형"]},
                                "evidence_snippets": [
                                    {"text": "리뷰: 자취방에 잘 맞음", "source": "manual"}
                                ],
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Ranked recommendations",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AssistResponse"}
                            }
                        },
                    },
                    "400": {"$ref": "#/components/responses/BadRequest"},
                    "429": {"$ref": "#/components/responses/RateLimited"},
                },
            }
        },
        "/v1/public/search": {
            "post": {
                "summary": "Coupang keyword product search",
                "description": (
                    "Thin wrapper over Coupang Partners product search. Supports "
                    "rocket-only filter, max-price filter, and sort order "
                    "(SIM|SALE|LOW|HIGH)."
                ),
                "parameters": _public_endpoint_parameters(),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SearchRequest"},
                            "example": {
                                "keyword": "무선청소기",
                                "rocket_only": True,
                                "max_price": 300000,
                                "sort": "LOW",
                                "limit": 5,
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Search results",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SearchResponse"}
                            }
                        },
                    },
                    "400": {"$ref": "#/components/responses/BadRequest"},
                    "429": {"$ref": "#/components/responses/RateLimited"},
                },
            }
        },
        "/v1/public/goldbox": {
            "get": {
                "summary": "Coupang goldbox (daily deals)",
                "description": "Returns the current Coupang goldbox deal list with short-link enriched deeplinks.",
                "parameters": _public_endpoint_parameters(),
                "responses": {
                    "200": {
                        "description": "Goldbox deal list",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GoldboxResponse"}
                            }
                        },
                    },
                    "429": {"$ref": "#/components/responses/RateLimited"},
                },
            }
        },
        "/v1/public/best/{category_id}": {
            "get": {
                "summary": "Coupang best-sellers by category",
                "description": "Returns best-sellers for a numeric Coupang category id.",
                "parameters": [
                    {
                        "name": "category_id",
                        "in": "path",
                        "required": True,
                        "description": "Coupang category id (numeric string).",
                        "schema": {"type": "string", "pattern": "^[0-9]+$"},
                        "example": "1001",
                    },
                    *_public_endpoint_parameters(),
                ],
                "responses": {
                    "200": {
                        "description": "Category best-seller list",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/BestResponse"}
                            }
                        },
                    },
                    "400": {"$ref": "#/components/responses/BadRequest"},
                    "429": {"$ref": "#/components/responses/RateLimited"},
                },
            }
        },
        "/s/{slug}": {
            "get": {
                "summary": "Resolve and redirect an affiliate short link",
                "description": (
                    "302-redirects to the original Coupang affiliate URL registered under "
                    "the slug. Logs a `book_click` / shortlink_redirect event for "
                    "feedback and WAS attribution."
                ),
                "parameters": [
                    {
                        "name": "slug",
                        "in": "path",
                        "required": True,
                        "description": "Short-link slug.",
                        "schema": {"type": "string"},
                    },
                    *_OPENAPI_ATTRIBUTION_HEADER_PARAMS,
                ],
                "responses": {
                    "302": {
                        "description": "Redirect to the affiliate URL",
                        "headers": {
                            "Location": {"schema": {"type": "string", "format": "uri"}},
                            "X-Request-Id": {"schema": {"type": "string"}},
                        },
                    },
                    "404": {
                        "description": "Slug is not registered",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    },
                },
            }
        },
    },
    "components": {
        "schemas": {
            "AssistRequest": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Natural-language shopping intent."},
                    "vertical": {
                        "type": "string",
                        "description": "Optional vertical router. 'book' routes through book_reco.",
                        "enum": ["book"],
                    },
                    "constraints": {
                        "type": "object",
                        "properties": {
                            "must_have": {"type": "array", "items": {"type": "string"}},
                            "avoid": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "evidence_snippets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["text"],
                            "properties": {
                                "text": {"type": "string"},
                                "source": {"type": "string"},
                            },
                        },
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
            "AssistResponse": {
                "type": "object",
                "properties": {
                    "best_fit": {"$ref": "#/components/schemas/Recommendation"},
                    "alternates": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Recommendation"},
                    },
                    "normalized_intent": {"type": "object"},
                    "summary": {"type": "string"},
                    "disclosure": {"type": "string", "description": "Affiliate disclosure text (must not be stripped)."},
                    "query_id": {"type": "string"},
                    "requestId": {"type": "string"},
                },
            },
            "Recommendation": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "title": {"type": "string"},
                    "price": {"type": "integer"},
                    "deeplink": {"type": "string", "format": "uri"},
                    "short_deeplink": {"type": "string", "format": "uri"},
                    "rating": {"type": "number"},
                    "review_count": {"type": "integer"},
                },
            },
            "SearchRequest": {
                "type": "object",
                "required": ["keyword"],
                "properties": {
                    "keyword": {"type": "string", "maxLength": 200},
                    "rocket_only": {"type": "boolean"},
                    "max_price": {"type": "integer", "minimum": 0},
                    "sort": {"type": "string", "enum": ["SIM", "SALE", "LOW", "HIGH"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
            "SearchResponse": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "keyword": {"type": "string"},
                            "results": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/SearchResult"},
                            },
                            "total": {"type": "integer"},
                        },
                    },
                    "disclosure": {"type": "string"},
                    "requestId": {"type": "string"},
                },
            },
            "SearchResult": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "price": {"type": "integer"},
                    "is_rocket": {"type": "boolean"},
                    "is_free_shipping": {"type": "boolean"},
                    "rating": {"type": "number"},
                    "review_count": {"type": "integer"},
                    "deeplink": {"type": "string", "format": "uri"},
                    "short_deeplink": {"type": "string", "format": "uri"},
                },
            },
            "GoldboxResponse": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "deals": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/SearchResult"},
                            },
                            "fetched_at": {"type": "string", "format": "date-time"},
                        },
                    },
                    "disclosure": {"type": "string"},
                    "requestId": {"type": "string"},
                },
            },
            "BestResponse": {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "category_id": {"type": "string"},
                            "products": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/SearchResult"},
                            },
                        },
                    },
                    "disclosure": {"type": "string"},
                    "requestId": {"type": "string"},
                },
            },
            "Error": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "requestId": {"type": "string"},
                },
                "required": ["error"],
            },
        },
        "responses": {
            "BadRequest": {
                "description": "Request validation failed",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            },
            "RateLimited": {
                "description": "Rate limit exceeded for the public bucket",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            },
        },
    },
}


_DOCS_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>OpenClaw Shopping API - Docs</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css\" />
  <style>body { margin: 0; } #swagger-ui { max-width: 1200px; margin: 0 auto; }</style>
</head>
<body>
  <div id=\"swagger-ui\"></div>
  <script src=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js\" crossorigin></script>
  <script>
    window.addEventListener('load', function () {
      window.ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        layout: 'BaseLayout'
      });
    });
  </script>
</body>
</html>
"""


class ShoppingBackend:
    def __init__(
        self,
        *,
        adapter: Any,
        analytics_store: AnalyticsStore,
        shortener: Optional[UrlShortener] = None,
        allowed_deeplink_hosts: Optional[List[str]] = None,
        signal_store: Any = None,
    ) -> None:
        self.adapter = adapter
        self.analytics_store = analytics_store
        self.shortener = shortener
        self.signal_store = signal_store
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

    def assist(
        self,
        payload: Dict[str, Any],
        *,
        client_id: Optional[str] = None,
        attribution: Optional[Dict[str, Optional[str]]] = None,
    ) -> Dict[str, Any]:
        payload_error = validate_payload_limits(payload)
        if payload_error:
            raise BackendError(HTTPStatus.BAD_REQUEST, payload_error)
        if (payload.get("vertical") or "").strip().lower() == "book":
            return self._book_assist(payload, client_id=client_id, attribution=attribution)
        normalized = normalize_request(payload)
        query = normalized["query"]
        if not query:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'query' is required")
        evidence_snippets = _normalize_evidence_snippets(payload.get("evidence_snippets") or [])
        search_plan = build_search_queries(normalized)
        degraded_reason: Optional[str] = None
        try:
            products = self._search_products(
                query=query,
                search_plan=search_plan,
            )
        except CoupangApiError as exc:
            products = []
            degraded_reason = f"coupang_api_status_{exc.status_code}"
            log_event(
                "coupang_search_degraded",
                query=query,
                status_code=exc.status_code,
                reason=str(exc)[:200],
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
                client_id=client_id,
            )
        except Exception as exc:
            log_event("analytics_error", stage="record_assist", query=query, error=str(exc))
        # Emit an attribution-tagged `assist` event so WAS/session aggregation (T3) can
        # count a session per request without re-deriving from queries. Spec R1.1 requires
        # an `event_type='assist'` row with the 4 attribution columns populated.
        self._record_attribution_event(
            event_type="assist",
            query_id=query_id,
            attribution=attribution,
        )
        response = build_assist_response(
            normalized=normalized,
            search_plan=search_plan,
            recommendations=recommendations,
            query_id=query_id,
        )
        if degraded_reason:
            response["degraded"] = degraded_reason
        return response

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
            try:
                raw = self.adapter.get_goldbox()
            except Exception as exc:
                log_event("goldbox_error", error=str(exc))
                raise BackendError(HTTPStatus.BAD_GATEWAY, "Upstream goldbox request failed") from exc
            products = _extract_products(raw)
            normalized = [_normalize_search_product(p) for p in products]
            enriched = self._attach_short_links(normalized)
            product_list = self._attach_short_links_to_product_list(products)
            try:
                self.analytics_store.record_event(event_type="goldbox_viewed")
            except Exception as exc:
                log_event("analytics_error", stage="goldbox_viewed", error=str(exc))
            return {
                "ok": True,
                "products": product_list,
                "count": len(product_list),
                "data": {
                    "deals": enriched,
                    "products": product_list,
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

    def best_products(self, category_id: int = 1016) -> Dict[str, Any]:
        if not hasattr(self.adapter, "get_bestcategories"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Adapter does not support get_bestcategories().")

        def _compute() -> Dict[str, Any]:
            try:
                raw = self.adapter.get_bestcategories(category_id)
            except Exception as exc:
                log_event("best_products_error", error=str(exc), category_id=category_id)
                raise BackendError(HTTPStatus.BAD_GATEWAY, "Upstream best-products request failed") from exc
            products = _extract_products(raw)
            product_list = self._attach_short_links_to_product_list(products)
            try:
                self.analytics_store.record_event(event_type="best_products_viewed", metadata={"category_id": category_id})
            except Exception as exc:
                log_event("analytics_error", stage="best_products_viewed", error=str(exc), category_id=category_id)
            return {
                "ok": True,
                "category_id": category_id,
                "products": product_list,
                "count": len(product_list),
                "data": {"category_id": category_id, "products": product_list},
                "disclosure": DISCLOSURE_TEXT,
            }

        return self._cache_get_or_compute(f"best-products:{category_id}", _compute)

    def record_event(
        self,
        payload: Dict[str, Any],
        *,
        attribution: Optional[Dict[str, Optional[str]]] = None,
    ) -> Dict[str, Any]:
        event_type = (payload.get("event_type") or "").strip()
        if not event_type:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'event_type' is required")
        event_id = self.analytics_store.record_event(
            event_type=event_type,
            query_id=payload.get("query_id"),
            recommendation_id=payload.get("recommendation_id"),
            metadata=payload.get("metadata") or {},
            **_attribution_kwargs(attribution),
        )
        return {"ok": True, "event_id": event_id}

    def _record_attribution_event(
        self,
        *,
        event_type: str,
        query_id: Optional[str],
        attribution: Optional[Dict[str, Optional[str]]],
    ) -> None:
        """Best-effort write of a single tagged event row for the current request.

        Swallows exceptions the same way `record_assist` does so analytics failures never
        take down a request. The attribution dict carries the 4 spec-D9 columns.
        """

        try:
            self.analytics_store.record_event(
                event_type=event_type,
                query_id=query_id,
                metadata={},
                **_attribution_kwargs(attribution),
            )
        except Exception as exc:
            log_event("analytics_error", stage="record_attribution_event", event_type=event_type, error=str(exc))

    def metrics(self) -> Dict[str, Any]:
        summary = self.analytics_store.get_summary()
        adapter_name = type(self.adapter).__name__
        shortener_name = type(self.shortener).__name__ if self.shortener else "none"
        return {
            "ok": True,
            "adapter": adapter_name,
            "shortener": shortener_name,
            "counts": {
                "total_queries": summary.get("total_queries", 0),
                "total_recommendations": summary.get("total_recommendations", 0),
                "total_events": summary.get("total_events", 0),
            },
            "event_breakdown": summary.get("event_breakdown", []),
        }

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

    def proxy_gmv_rows(self, *, week_start: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return weekly proxy-GMV rows written by economics.compute_weekly_proxy_gmv.

        Reads the sqlite DB directly because the weekly batch is an out-of-process
        job — the admin endpoint is decoupled from the writer, so we never want to
        hold a shared connection. Returns an empty list when analytics is backed by
        a non-sqlite provider (e.g., Firestore) so the endpoint still 200s on prod.
        """

        db_path = getattr(self.analytics_store, "db_path", None)
        if not db_path:
            return []
        try:
            return read_proxy_gmv_rows(db_path, week_start=week_start)
        except Exception as exc:
            log_event("proxy_gmv_read_error", error=str(exc))
            return []

    def click_reconciliation_row(self, *, date_kst: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return a single click_reconciliation row (latest if `date_kst` is None)."""

        db_path = getattr(self.analytics_store, "db_path", None)
        if not db_path:
            return None
        try:
            return read_click_reconciliation_row(db_path, date_kst=date_kst)
        except Exception as exc:
            log_event("click_reconciliation_read_error", error=str(exc))
            return None

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

    def _book_assist(
        self,
        payload: Dict[str, Any],
        *,
        client_id: Optional[str] = None,
        attribution: Optional[Dict[str, Optional[str]]] = None,
    ) -> Dict[str, Any]:
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
            client_id=client_id,
            analytics_store=self.analytics_store,
            signal_store=self.signal_store,
        )

        try:
            self.analytics_store.record_assist(
                query_text=response.get("query", ""),
                budget=None,
                category="book",
                evidence_snippets=[],
                recommendations=response.get("recommendations", []),
                client_id=client_id,
            )
        except Exception as exc:
            log_event("analytics_error", stage="record_book_assist", error=str(exc))

        self._record_attribution_event(
            event_type="assist",
            query_id=None,
            attribution=attribution,
        )

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

    def _attach_short_links_to_product_list(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.shortener:
            return products
        enriched: List[Dict[str, Any]] = []
        for product in products:
            original = product.get("productUrl") or product.get("url") or ""
            shortened = original
            if original and validate_deeplink_url(original, self.allowed_deeplink_hosts):
                try:
                    shortened = self.shortener.shorten(original)
                except Exception as exc:
                    log_event("shortener_error", stage="shorten_product_list", error=str(exc))
            enriched.append({**product, "short_url": shortened})
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
            parsed_path = parse.urlsplit(self.path)
            path = parsed_path.path
            query_params = parse.parse_qs(parsed_path.query)
            if self.path.startswith("/s/"):
                slug = self.path.split("/s/", 1)[1].split("?", 1)[0].strip()
                target = self.backend.resolve_short_link(slug)
                if not target:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Short link not found", "requestId": request_id})
                    return
                self.backend.record_short_link_click(slug)
                # Real client attribution (spec R3.5/R3.6/T5 part 1): `remote_addr` is
                # the TCP peer, which on Cloud Run is a Google front-end link-local IP
                # (169.254.x.x) — useless for bot/dedup. XFF holds the real caller.
                client_ip = _first_forwarded_for(self.headers.get("X-Forwarded-For")) or remote_addr
                user_agent = (self.headers.get("User-Agent") or "")[:200]
                proxy_ip = remote_addr
                # Emit book_click for the feedback loop — slug is joined against recent
                # book_impression events in book_intel.feedback.rollup to recover isbn+cluster.
                source_tag = (
                    self.headers.get("X-OpenClaw-Source")
                    or _infer_click_source(self.headers.get("Referer", ""))
                )
                attribution = self._attribution_context()
                try:
                    self.backend.analytics_store.record_event(
                        event_type="book_click",
                        metadata={
                            "slug": slug,
                            "source": source_tag,
                            "client_id": self._client_id_from_headers(),
                            "referer": (self.headers.get("Referer") or "")[:200],
                        },
                        client_ip=client_ip,
                        user_agent=user_agent,
                        proxy_ip=proxy_ip,
                        **_attribution_kwargs(attribution),
                    )
                except Exception as exc:
                    log_event("feedback_error", stage="book_click", slug=slug, error=str(exc))
                # Durable shortlink_redirect event row — this is what the weekly proxy-GMV
                # batch and daily reconciliation job read from (spec T5 parts 4/5). The
                # metadata mirrors the log fields below so we can still eyeball a redirect
                # in stdout without hitting the events table.
                try:
                    self.backend.analytics_store.record_event(
                        event_type="shortlink_redirect",
                        metadata={
                            "slug": slug,
                            "request_id": request_id,
                            "referer": (self.headers.get("Referer") or "")[:200],
                        },
                        client_ip=client_ip,
                        user_agent=user_agent,
                        proxy_ip=proxy_ip,
                        **_attribution_kwargs(attribution),
                    )
                except Exception as exc:
                    log_event("shortlink_redirect_store_error", slug=slug, error=str(exc))
                self.send_response(HTTPStatus.FOUND)
                self.send_header("X-Request-Id", request_id)
                self.send_header("Location", target)
                self.end_headers()
                log_event(
                    "shortlink_redirect",
                    request_id=request_id,
                    slug=slug,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    proxy_ip=proxy_ip,
                )
                return
            if path in ("/health", "/healthz"):
                self._send_json(HTTPStatus.OK, {**self.backend.health(), "requestId": request_id}, head_only=head_only)
                return
            if path == "/openapi.json":
                # Public, unauthenticated spec endpoint per spec R1.2. Kept outside the
                # rate-limiter so GPT Store / Swagger UI / CDN fetchers don't eat into
                # the shared public bucket.
                self._send_json(HTTPStatus.OK, _OPENAPI_DOCUMENT, head_only=head_only)
                return
            if path in ("/docs", "/docs/"):
                # Swagger UI served via CDN (stdlib-only requirement forbids bundling
                # the assets). Points the UI at /openapi.json.
                self._send_html(HTTPStatus.OK, _DOCS_HTML, head_only=head_only)
                return
            if path == "/v1/admin/summary":
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                self._send_json(HTTPStatus.OK, {**self.backend.summary(), "requestId": request_id}, head_only=head_only)
                return
            if path == "/v1/admin/metrics":
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                self._send_json(HTTPStatus.OK, {**self.backend.metrics(), "requestId": request_id}, head_only=head_only)
                return
            if path == "/v1/admin/gmv/proxy":
                # Admin read-only view of the weekly shortlink proxy-GMV batch output
                # (spec T5 part 6 / R3.3). The batch itself runs out-of-process — this
                # endpoint is just a sqlite passthrough so the T6 pitch dashboard can
                # render without also holding the economics module.
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                week_start = _query_param(parsed_path.query, "week_start")
                rows = self.backend.proxy_gmv_rows(week_start=week_start)
                self._send_json(
                    HTTPStatus.OK,
                    {"rows": rows, "week_start": week_start, "requestId": request_id},
                    head_only=head_only,
                )
                return
            if path == "/v1/admin/click-reconciliation":
                # Daily click-gap row (spec T5 part 6 / R3.8). Returns 200 + a null row
                # rather than 404 on missing data, so dashboards can render "no data yet"
                # without branching on HTTP status.
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                date_kst = _query_param(parsed_path.query, "date")
                row = self.backend.click_reconciliation_row(date_kst=date_kst)
                self._send_json(
                    HTTPStatus.OK,
                    {"row": row, "date": date_kst, "requestId": request_id},
                    head_only=head_only,
                )
                return
            if path == "/v1/public/goldbox":
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.goldbox()
                log_event("goldbox_ok", request_id=request_id, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id}, head_only=head_only)
                return
            if path in ("/v1/goldbox", "/internal/v1/goldbox"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.goldbox()
                log_event("goldbox_ok", request_id=request_id, path=self.path, remote_addr=remote_addr)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id}, head_only=head_only)
                return
            if path.startswith("/v1/public/best/"):
                category_id = path.split("/v1/public/best/", 1)[1].strip()
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
            if path == "/v1/public/best-products":
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                category_id = _parse_category_id(query_params)
                response = self.backend.best_products(category_id)
                log_event("best_products_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker, category_id=category_id)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id}, head_only=head_only)
                return
            if path in ("/v1/best-products", "/internal/v1/best-products"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id}, head_only=head_only)
                    return
                self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                category_id = _parse_category_id(query_params)
                response = self.backend.best_products(category_id)
                log_event("best_products_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, category_id=category_id)
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
            # Strip query string for route matching so public endpoints accept
            # `?utm_source=...` (spec D9/R4.4 campaign attribution) without 404-ing.
            path_only = urlsplit(self.path).path
            if path_only == "/v1/public/search":
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.search(payload)
                log_event("search_ok", request_id=request_id, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if path_only in ("/v1/public/assist", "/v1/public/recommendations"):
                client_marker = self._authorize_public(request_id=request_id, remote_addr=remote_addr)
                attribution = self._attribution_context()
                response = self.backend.assist(
                    payload,
                    client_id=self._client_id_from_headers(),
                    attribution=attribution,
                )
                log_event("assist_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if path_only in ("/v1/assist", "/v1/recommendations", "/internal/v1/assist", "/internal/v1/recommendations"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
                    return
                client_marker = self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                attribution = self._attribution_context()
                response = self.backend.assist(
                    payload,
                    client_id=self._client_id_from_headers(),
                    attribution=attribution,
                )
                log_event("assist_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if path_only in ("/v1/events", "/internal/v1/events"):
                if not _operator_routes_enabled():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
                    return
                client_marker = self._authorize_internal(request_id=request_id, remote_addr=remote_addr)
                response = self.backend.record_event(payload, attribution=self._attribution_context())
                log_event("event_ok", request_id=request_id, path=self.path, remote_addr=remote_addr, client=client_marker)
                self._send_json(HTTPStatus.OK, {**response, "requestId": request_id})
                return
            if path_only in ("/v1/deeplinks", "/internal/v1/deeplinks"):
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

    def _client_id_from_headers(self) -> Optional[str]:
        """Extract a stable per-caller identifier from request headers.

        The OpenClaw skill ships `X-OpenClaw-Client-Id`; we also honour a more generic
        `X-Client-Id` fallback so third-party callers can opt into analytics replay
        without rebranding. Empty values are treated as absent so the analytics store
        keeps bucketing anonymous traffic separately.
        """

        for header in ("X-OpenClaw-Client-Id", "X-Client-Id"):
            value = (self.headers.get(header) or "").strip()
            if value:
                return value[:128]
        return None

    def _attribution_context(self) -> Dict[str, Optional[str]]:
        """Parse the 4 attribution inputs per spec D9/R1.1/R1.5.

        Headers:
          - x-openclaw-surface → normalized enum or 'unknown' (raw preserved on mismatch).
          - x-openclaw-client-id → otherwise `anonymous-<sha1 of ip>` fallback.
          - x-openclaw-version → raw string, truncated. Empty string when missing.
        Query string:
          - utm_source → ad-campaign attribution (paid-marketing rollups per D7/R4.4).

        Returned as a plain dict so it can be splatted into analytics writes and passed
        through to the ShoppingBackend without dragging a dataclass import.
        """

        raw_surface = self.headers.get("X-OpenClaw-Surface")
        surface, surface_raw = _normalize_surface(raw_surface)

        client_id = self._client_id_from_headers()
        if not client_id:
            remote_addr = normalize_client_ip(
                self.client_address[0] if self.client_address else None
            )
            client_id = _anonymous_client_id(remote_addr)

        raw_version = (self.headers.get("X-OpenClaw-Version") or "").strip()
        # Cap at 64 chars to avoid someone pasting a changelog into the header.
        client_version = raw_version[:64]

        utm_source: Optional[str] = None
        try:
            query_string = urlsplit(self.path).query
            if query_string:
                values = parse_qs(query_string, keep_blank_values=False).get("utm_source") or []
                if values:
                    utm_source = (values[0] or "").strip()[:128] or None
        except Exception:
            utm_source = None

        return {
            "surface": surface,
            "surface_raw": surface_raw,
            "client_id": client_id,
            "client_version": client_version,
            "utm_source": utm_source,
        }

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

    def _send_html(self, status: int, body: str, *, head_only: bool = False) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if not head_only:
            self.wfile.write(encoded)

    def _authorize_public(self, *, request_id: str, remote_addr: str) -> str:
        client_id = self.headers.get("X-OpenClaw-Client-Id")
        allowlist = shopping_client_allowlist_from_env()
        allowlist_enabled = shopping_client_allowlist_enabled_from_env()
        allowlisted_client = is_client_allowlisted(client_id, allowlist, allowlist_enabled)
        if allowlist_enabled and not allowlisted_client:
            raise BackendError(HTTPStatus.FORBIDDEN, "Client is not allowlisted")
        client_marker = summarize_client(remote_addr, client_id, None)
        rate_limiter = self.public_rate_limiter or self.rate_limiter
        limiter_key = rate_limit_key(remote_addr, client_id, None, allowlisted_client=allowlisted_client)
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
        if self.path in ("/v1/admin/summary", "/v1/admin/metrics"):
            return self.admin_rate_limiter or self.rate_limiter
        if not internal:
            return self.public_rate_limiter or self.rate_limiter
        if token:
            return self.authenticated_rate_limiter or self.rate_limiter
        return self.public_rate_limiter or self.rate_limiter


def _infer_click_source(referer: str) -> str:
    """Classify the click referrer into one of {blog, email, skill, api} for analytics."""

    referer = (referer or "").lower()
    if not referer:
        return "api"
    if "mail" in referer or "newsletter" in referer:
        return "email"
    if "retn.kr" in referer or "ghost" in referer or "blog" in referer:
        return "blog"
    if "openclaw" in referer or "skill" in referer:
        return "skill"
    return "api"


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
    def _walk(node: Any) -> List[Dict[str, Any]]:
        if not node:
            return []
        if isinstance(node, list):
            direct_products = [item for item in node if isinstance(item, dict) and (item.get("productId") or item.get("productName") or item.get("productUrl"))]
            if direct_products:
                return direct_products
            flattened: List[Dict[str, Any]] = []
            for item in node:
                if not isinstance(item, dict):
                    continue
                parent_category_id = item.get("categoryId")
                for wrapper_key in ("item", "product", "productItem"):
                    wrapped = item.get(wrapper_key)
                    if isinstance(wrapped, dict) and (wrapped.get("productId") or wrapped.get("productName") or wrapped.get("productUrl")):
                        if parent_category_id is not None and "categoryId" not in wrapped:
                            wrapped = {**wrapped, "categoryId": parent_category_id}
                        flattened.append(wrapped)
                for nested_key in ("products", "productData", "items"):
                    nested = item.get(nested_key)
                    nested_products = _walk(nested)
                    if nested_products:
                        if parent_category_id is not None:
                            nested_products = [
                                ({**product, "categoryId": parent_category_id} if isinstance(product, dict) and "categoryId" not in product else product)
                                for product in nested_products
                            ]
                        flattened.extend(nested_products)
            return flattened
        if isinstance(node, dict):
            for key in ("products", "productData", "items"):
                nested = node.get(key)
                nested_products = _walk(nested)
                if nested_products:
                    return nested_products
            for key in ("data", "bestCategories", "goldbox", "result", "payload"):
                nested = node.get(key)
                nested_products = _walk(nested)
                if nested_products:
                    return nested_products
        return []

    return _walk(payload)


def _parse_category_id(query_params: Dict[str, List[str]], default: int = 1016) -> int:
    raw = query_params.get("categoryId") or query_params.get("category_id") or []
    if not raw:
        return default
    try:
        return int(raw[0])
    except (TypeError, ValueError) as exc:
        raise BackendError(HTTPStatus.BAD_REQUEST, "categoryId must be an integer") from exc


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
        signal_store=_build_signal_store_from_env(db_path=db_path),
    )


def _build_signal_store_from_env(*, db_path: str) -> Any:
    """Build the book_intel SignalStore if book_intel is installed. Returns None if absent.

    Kept here (not imported at module top) so a deployment without the book_intel
    package — e.g., a minimal shopping-only backend — still boots clean and the book
    vertical just degrades to persona-only ranking.
    """

    try:
        from book_intel.feedback.signal_store import SignalStore
    except Exception:
        return None
    signal_db = os.getenv("OPENCLAW_BOOK_SIGNAL_DB_PATH") or db_path
    try:
        return SignalStore(signal_db)
    except Exception:
        return None


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
