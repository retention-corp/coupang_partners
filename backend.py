import hashlib
import html
import json
import os
import threading
import time
from http import cookies
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
from builders_shortener.admin_auth import (
    ADMIN,
    OWNER,
    AdminAuthError,
    AdminAuthService,
    ACCOUNT_STATUSES,
    build_admin_auth_service_from_env,
)
from url_shortener import BuiltinShortener, FirestoreShortener, UrlSafetyPolicy, UrlShortener


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


# Observation-only traffic classifier for public short-link requests. These
# markers intentionally stay narrow: a normal KakaoTalk/Discord in-app browser
# is a human redirect, while the platform's dedicated link-expansion crawler is
# a preview fetch. Classification never changes the 302 response.
_SHORTLINK_CLASSIFIER_VERSION = "2026-07-13-v1"
_SHORTLINK_PREVIEW_MARKERS = (
    ("discordbot", "discord"),
    ("telegrambot", "telegram"),
    ("slackbot-linkexpanding", "slack"),
    ("kakaotalk-scrap", "kakao"),
    ("kakaotalk-scraper", "kakao"),
)
_SHORTLINK_AUTOMATION_MARKERS = (
    ("openclawagentclosedloop", "openclaw-closed-loop"),
    ("claude-user", "claude-user"),
    ("riskscanner/", "risk-scanner"),
    ("headlesschrome/", "headless-chrome"),
    ("python-requests/", "python-requests"),
    ("python-urllib/", "python-urllib"),
    ("curl/", "curl"),
    ("wget/", "wget"),
    ("gvfs/", "gvfs"),
)


def _classify_shortlink_request(user_agent: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return (event_type, agent) without affecting redirect behavior."""

    normalized = (user_agent or "").strip().lower()
    for marker, agent in _SHORTLINK_PREVIEW_MARKERS:
        if marker in normalized:
            return "preview_fetch", agent
    for marker, agent in _SHORTLINK_AUTOMATION_MARKERS:
        if marker in normalized:
            return "automation_fetch", agent
    return "human_redirect", None


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
                    "deeplink": {
                        "type": "string",
                        "format": "uri",
                        "deprecated": True,
                        "description": "Legacy raw affiliate URL. Do not expose to users; migrate to short_deeplink.",
                    },
                    "short_deeplink": {
                        "type": "string",
                        "format": "uri",
                        "description": "Canonical public affiliate link for user-facing output.",
                    },
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
                    "deeplink": {
                        "type": "string",
                        "format": "uri",
                        "deprecated": True,
                        "description": "Legacy raw affiliate URL. Do not expose to users; migrate to short_deeplink.",
                    },
                    "short_deeplink": {
                        "type": "string",
                        "format": "uri",
                        "description": "Canonical public affiliate link for user-facing output.",
                    },
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


_ADMIN_LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>a.retn.kr Admin Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f7f7f3; color: #1d1d1f; }
    main { width: min(420px, calc(100vw - 32px)); margin: 12vh auto; }
    form { display: grid; gap: 12px; padding: 24px; background: #fff; border: 1px solid #deded8; border-radius: 8px; }
    label { display: grid; gap: 6px; font-size: 14px; }
    input, button { font: inherit; padding: 10px 12px; border-radius: 6px; border: 1px solid #c8c8c2; }
    button { background: #1d1d1f; color: #fff; cursor: pointer; }
    .error { color: #a32121; min-height: 20px; }
  </style>
</head>
<body>
  <main>
    <h1>a.retn.kr Admin</h1>
    <form id="login-form">
      <label>Username <input name="username" autocomplete="username" required /></label>
      <label>Password <input name="password" type="password" autocomplete="current-password" required /></label>
      <button type="submit">Sign in</button>
      <div class="error" id="error"></div>
    </form>
  </main>
  <script>
    document.getElementById('login-form').addEventListener('submit', async function (event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const response = await fetch('/admin/api/session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: form.get('username'), password: form.get('password')})
      });
      if (response.ok) {
        location.href = '/admin';
        return;
      }
      const payload = await response.json().catch(() => ({}));
      document.getElementById('error').textContent = payload.error || 'Sign in failed';
    });
  </script>
</body>
</html>
"""


def _admin_dashboard_html(user: Dict[str, Any], links: List[Dict[str, Any]], users: List[Dict[str, Any]]) -> str:
    link_rows = "\n".join(
        "<tr><td>{slug}</td><td><a href=\"{short_url}\">{short_url}</a></td><td>{target}</td><td>{owner}</td><td>{active}</td></tr>".format(
            slug=html.escape(str(item.get("slug") or "")),
            short_url=html.escape(str(item.get("short_url") or "")),
            target=html.escape(str(item.get("target_url") or "")),
            owner=html.escape(str(item.get("owner_username") or item.get("owner_status") or "")),
            active="active" if item.get("active", True) else "inactive",
        )
        for item in links
    ) or "<tr><td colspan=\"5\">No links yet.</td></tr>"
    user_rows = "\n".join(
        "<tr><td>{username}</td><td>{role}</td><td>{status}</td></tr>".format(
            username=html.escape(str(item.get("username") or "")),
            role=html.escape(str(item.get("role") or "")),
            status=html.escape(str(item.get("status") or ("approved" if item.get("active", True) else "suspended"))),
        )
        for item in users
    ) or "<tr><td colspan=\"3\">No users visible.</td></tr>"
    can_write = user.get("role") in {OWNER, ADMIN}
    create_form = """
    <section>
      <h2>Create Links</h2>
      <form id="link-form">
        <textarea name="rows" rows="5" placeholder="https://example.com, optional-alias"></textarea>
        <button type="submit">Create</button>
      </form>
      <pre id="result"></pre>
    </section>
    """ if can_write else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>a.retn.kr Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; color: #1d1d1f; background: #fafaf7; }}
    header, main {{ width: min(1120px, calc(100vw - 32px)); margin: 0 auto; }}
    header {{ display: flex; justify-content: space-between; align-items: center; padding: 24px 0; }}
    section {{ margin: 0 0 28px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #deded8; }}
    th, td {{ text-align: left; border-bottom: 1px solid #ecece6; padding: 10px; vertical-align: top; overflow-wrap: anywhere; }}
    textarea {{ width: 100%; box-sizing: border-box; font: inherit; padding: 10px; border: 1px solid #c8c8c2; border-radius: 6px; }}
    button {{ font: inherit; padding: 9px 12px; border: 1px solid #1d1d1f; border-radius: 6px; background: #1d1d1f; color: #fff; cursor: pointer; }}
    pre {{ white-space: pre-wrap; background: #fff; border: 1px solid #deded8; padding: 10px; min-height: 20px; }}
  </style>
</head>
<body>
  <header>
    <div><h1>Short Links</h1><div>{html.escape(str(user.get("username")))} · {html.escape(str(user.get("role")))}</div></div>
    <button id="logout">Sign out</button>
  </header>
  <main>
    {create_form}
    <section>
      <h2>Links</h2>
      <table><thead><tr><th>Slug</th><th>Short URL</th><th>Target</th><th>Owner</th><th>Status</th></tr></thead><tbody>{link_rows}</tbody></table>
    </section>
    <section>
      <h2>Users</h2>
      <table><thead><tr><th>Username</th><th>Role</th><th>Status</th></tr></thead><tbody>{user_rows}</tbody></table>
    </section>
  </main>
  <script>
    const form = document.getElementById('link-form');
    if (form) {{
      form.addEventListener('submit', async function (event) {{
        event.preventDefault();
        const rows = String(new FormData(form).get('rows') || '').split(/\\n+/).map((line) => {{
          const parts = line.split(',');
          return {{url: (parts[0] || '').trim(), alias: (parts[1] || '').trim() || undefined}};
        }}).filter((row) => row.url);
        const response = await fetch('/admin/api/short-links', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{rows}})
        }});
        document.getElementById('result').textContent = JSON.stringify(await response.json(), null, 2);
      }});
    }}
    document.getElementById('logout').addEventListener('click', async function () {{
      await fetch('/admin/api/session', {{method: 'DELETE'}});
      location.href = '/admin/login';
    }});
  </script>
</body>
</html>
"""


def _parse_admin_session_cookie(header_value: Optional[str]) -> Optional[str]:
    if not header_value:
        return None
    jar = cookies.SimpleCookie()
    try:
        jar.load(header_value)
    except cookies.CookieError:
        return None
    morsel = jar.get("admin_session")
    return morsel.value if morsel else None


def _admin_account_status_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    if "status" not in payload:
        return None
    status = str(payload.get("status") or "").strip().lower()
    if status not in ACCOUNT_STATUSES:
        allowed = ", ".join(sorted(ACCOUNT_STATUSES))
        raise BackendError(HTTPStatus.BAD_REQUEST, f"status must be one of: {allowed}")
    return status


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

    def admin_list_short_links(self, *, actor_username: str, actor_role: str) -> List[Dict[str, Any]]:
        if not self.shortener or not hasattr(self.shortener, "list_links"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Short-link admin storage is not configured")
        owner_username = None if actor_role in {OWNER, ADMIN} else actor_username
        return [
            record.to_dict()
            for record in self.shortener.list_links(owner_username=owner_username, include_inactive=True)
        ]

    def admin_create_short_links(self, payload: Dict[str, Any], *, actor_username: str) -> Dict[str, Any]:
        if not self.shortener or not hasattr(self.shortener, "create_links"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Short-link admin storage is not configured")
        rows = payload.get("rows")
        if rows is None:
            rows = [
                {
                    "url": payload.get("url") or payload.get("target_url") or payload.get("targetUrl"),
                    "alias": payload.get("alias"),
                }
            ]
        if not isinstance(rows, list) or not rows:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'rows' must be a non-empty list")
        normalized_rows = []
        for row in rows:
            if not isinstance(row, dict):
                raise BackendError(HTTPStatus.BAD_REQUEST, "Each short-link row must be an object")
            normalized_rows.append(
                {
                    "url": row.get("url") or row.get("target_url") or row.get("targetUrl"),
                    "alias": row.get("alias"),
                }
            )
        return self.shortener.create_links(normalized_rows, owner_username=actor_username)

    def admin_update_short_link(self, slug: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.shortener or not hasattr(self.shortener, "update_link"):
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Short-link admin storage is not configured")
        if "active" not in payload:
            raise BackendError(HTTPStatus.BAD_REQUEST, "'active' is required")
        return self.shortener.update_link(slug, active=bool(payload.get("active"))).to_dict()

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
    admin_auth: Optional[AdminAuthService] = None
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
                    log_event(
                        "redirect_error",
                        request_id=request_id,
                        slug=slug,
                        reason="short_link_not_found",
                    )
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Short link not found", "requestId": request_id})
                    return
                self.backend.record_short_link_click(slug)
                # Real client attribution (spec R3.5/R3.6/T5 part 1): `remote_addr` is
                # the TCP peer, which on Cloud Run is a Google front-end link-local IP
                # (169.254.x.x) — useless for bot/dedup. XFF holds the real caller.
                client_ip = _first_forwarded_for(self.headers.get("X-Forwarded-For")) or remote_addr
                user_agent = (self.headers.get("User-Agent") or "")[:200]
                proxy_ip = remote_addr
                traffic_event_type, traffic_agent = _classify_shortlink_request(user_agent)
                classification_metadata = {
                    "slug": slug,
                    "request_id": request_id,
                    "referer": (self.headers.get("Referer") or "")[:200],
                    "agent": traffic_agent,
                    "classifier_version": _SHORTLINK_CLASSIFIER_VERSION,
                    "observation_mode": True,
                }
                # Emit book_click for the feedback loop — slug is joined against recent
                # book_impression events in book_intel.feedback.rollup to recover isbn+cluster.
                source_tag = (
                    self.headers.get("X-OpenClaw-Source")
                    or _infer_click_source(self.headers.get("Referer", ""))
                )
                attribution = self._attribution_context()
                try:
                    self.backend.analytics_store.record_event(
                        event_type=traffic_event_type,
                        metadata=classification_metadata,
                        client_ip=client_ip,
                        user_agent=user_agent,
                        proxy_ip=proxy_ip,
                        **_attribution_kwargs(attribution),
                    )
                except Exception as exc:
                    log_event(
                        "shortlink_classification_store_error",
                        slug=slug,
                        traffic_class=traffic_event_type,
                        error=str(exc),
                    )
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
                            "traffic_class": traffic_event_type,
                            "traffic_agent": traffic_agent,
                            "classifier_version": _SHORTLINK_CLASSIFIER_VERSION,
                            "observation_mode": True,
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
                    traffic_class=traffic_event_type,
                    traffic_agent=traffic_agent,
                    classifier_version=_SHORTLINK_CLASSIFIER_VERSION,
                    observation_mode=True,
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
            if path in ("/admin/login", "/admin/login/"):
                self._send_html(HTTPStatus.OK, _ADMIN_LOGIN_HTML, head_only=head_only)
                return
            if path in ("/admin", "/admin/"):
                actor = self._authorize_admin_session()
                links = self.backend.admin_list_short_links(actor_username=actor.username, actor_role=actor.role)
                users: List[Dict[str, Any]] = []
                try:
                    if self.admin_auth:
                        users = self.admin_auth.list_users(actor)
                except AdminAuthError:
                    users = []
                self._send_html(
                    HTTPStatus.OK,
                    _admin_dashboard_html(actor.public_dict(), links, users),
                    head_only=head_only,
                )
                return
            if path == "/admin/api/me":
                actor = self._authorize_admin_session()
                self._send_json(HTTPStatus.OK, {"ok": True, "user": actor.public_dict(), "requestId": request_id}, head_only=head_only)
                return
            if path == "/admin/api/short-links":
                actor = self._authorize_admin_session()
                self._require_admin_permission(actor, "short_link:read")
                links = self.backend.admin_list_short_links(actor_username=actor.username, actor_role=actor.role)
                self._send_json(HTTPStatus.OK, {"ok": True, "links": links, "requestId": request_id}, head_only=head_only)
                return
            if path == "/admin/api/users":
                actor = self._authorize_admin_session()
                self._require_admin_permission(actor, "user:read")
                users = self._admin_auth_service().list_users(actor)
                self._send_json(HTTPStatus.OK, {"ok": True, "users": users, "requestId": request_id}, head_only=head_only)
                return
            if path == "/admin/api/audit":
                actor = self._authorize_admin_session()
                self._require_admin_permission(actor, "audit:read")
                limit = _coerce_int_value(_query_param(parsed_path.query, "limit")) or 100
                events = [
                    {
                        "action": event.action,
                        "actor": event.actor,
                        "target": event.target,
                        "outcome": event.outcome,
                        "timestamp": event.timestamp,
                        "metadata": event.metadata,
                    }
                    for event in self._admin_auth_service().store.list_audit_events(limit=max(1, min(limit, 500)))
                ]
                self._send_json(HTTPStatus.OK, {"ok": True, "events": events, "requestId": request_id}, head_only=head_only)
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
        except AdminAuthError as exc:
            status = HTTPStatus.FORBIDDEN if "not allowed" in str(exc) else HTTPStatus.UNAUTHORIZED
            self._send_json(status, {"error": str(exc), "requestId": request_id}, head_only=head_only)
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
            if path_only == "/admin/api/session":
                token = self._admin_auth_service().authenticate(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
                actor = self._admin_auth_service().resolve_session(token)
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "token": token, "user": actor.public_dict(), "requestId": request_id},
                    set_cookie=f"admin_session={token}; HttpOnly; SameSite=Lax; Path=/admin; Max-Age=28800",
                )
                return
            if path_only == "/admin/api/short-links":
                actor = self._authorize_admin_session()
                self._require_admin_permission(actor, "short_link:write")
                response = self.backend.admin_create_short_links(payload, actor_username=actor.username)
                self._admin_auth_service()._audit(
                    "short_link.create",
                    actor,
                    "batch",
                    "success" if response.get("ok") else "partial",
                    {"rows": len(response.get("results") or [])},
                )
                status = HTTPStatus.OK if response.get("ok") else HTTPStatus.MULTI_STATUS
                self._send_json(status, {**response, "requestId": request_id})
                return
            if path_only == "/admin/api/users":
                actor = self._authorize_admin_session()
                user = self._admin_auth_service().create_user(
                    actor,
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    str(payload.get("role") or ADMIN),
                    status=_admin_account_status_from_payload(payload) or "approved",
                )
                self._send_json(HTTPStatus.CREATED, {"ok": True, "user": user.public_dict(), "requestId": request_id})
                return
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
        except AdminAuthError as exc:
            status = HTTPStatus.FORBIDDEN if "not allowed" in str(exc) else HTTPStatus.UNAUTHORIZED
            self._send_json(status, {"error": str(exc), "requestId": request_id})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body", "requestId": request_id})
        except Exception as exc:
            log_event("request_error", request_id=request_id, path=self.path, remote_addr=remote_addr, status=500, error=str(exc))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error", "requestId": request_id})

    def do_PATCH(self) -> None:  # noqa: N802
        request_id = generate_request_id()
        try:
            payload = self._read_json()
            path = urlsplit(self.path).path
            if path.startswith("/admin/api/short-links/"):
                actor = self._authorize_admin_session()
                self._require_admin_permission(actor, "short_link:write")
                slug = parse.unquote(path.rsplit("/", 1)[-1])
                link = self.backend.admin_update_short_link(slug, payload)
                self._admin_auth_service()._audit(
                    "short_link.update",
                    actor,
                    slug,
                    "success",
                    {"active": link.get("active")},
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "link": link, "requestId": request_id})
                return
            if path.startswith("/admin/api/users/"):
                actor = self._authorize_admin_session()
                username = parse.unquote(path.rsplit("/", 1)[-1])
                user = self._admin_auth_service().update_user(
                    actor,
                    username,
                    password=str(payload["password"]) if "password" in payload else None,
                    role=str(payload["role"]) if "role" in payload else None,
                    active=bool(payload["active"]) if "active" in payload else None,
                    status=_admin_account_status_from_payload(payload),
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "user": user.public_dict(), "requestId": request_id})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
        except BackendError as exc:
            self._send_json(exc.status, {"error": exc.message, "requestId": request_id})
        except AdminAuthError as exc:
            status = HTTPStatus.FORBIDDEN if "not allowed" in str(exc) else HTTPStatus.UNAUTHORIZED
            self._send_json(status, {"error": str(exc), "requestId": request_id})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON body", "requestId": request_id})
        except Exception as exc:
            log_event("request_error", request_id=request_id, path=self.path, status=500, error=str(exc))
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error", "requestId": request_id})

    def do_DELETE(self) -> None:  # noqa: N802
        request_id = generate_request_id()
        try:
            path = urlsplit(self.path).path
            if path == "/admin/api/session":
                token = self._admin_session_token()
                actor = self._authorize_admin_session()
                self._admin_auth_service().logout(actor, token)
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "requestId": request_id},
                    set_cookie="admin_session=; HttpOnly; SameSite=Lax; Path=/admin; Max-Age=0",
                )
                return
            if path.startswith("/admin/api/users/"):
                actor = self._authorize_admin_session()
                username = parse.unquote(path.rsplit("/", 1)[-1])
                removed = self._admin_auth_service().delete_user(actor, username)
                self._send_json(HTTPStatus.OK, {"ok": True, "removed": removed, "requestId": request_id})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found", "requestId": request_id})
        except AdminAuthError as exc:
            status = HTTPStatus.FORBIDDEN if "not allowed" in str(exc) else HTTPStatus.UNAUTHORIZED
            self._send_json(status, {"error": str(exc), "requestId": request_id})
        except Exception as exc:
            log_event("request_error", request_id=request_id, path=self.path, status=500, error=str(exc))
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

    def _send_json(
        self,
        status: int,
        payload: Dict[str, Any],
        *,
        head_only: bool = False,
        set_cookie: Optional[str] = None,
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("X-Request-Id", str(payload.get("requestId", "")))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
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

    def _admin_auth_service(self) -> AdminAuthService:
        if not self.admin_auth:
            raise BackendError(HTTPStatus.NOT_IMPLEMENTED, "Admin auth is not configured")
        return self.admin_auth

    def _admin_session_token(self) -> str:
        token = parse_bearer_token(self.headers.get("Authorization"))
        if token:
            return token
        cookie_token = _parse_admin_session_cookie(self.headers.get("Cookie"))
        if cookie_token:
            return cookie_token
        raise AdminAuthError("admin authentication is required")

    def _authorize_admin_session(self):
        return self._admin_auth_service().resolve_session(self._admin_session_token())

    def _require_admin_permission(self, actor: Any, permission: str) -> None:
        self._admin_auth_service().require_permission(actor, permission)

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
        path = urlsplit(self.path).path
        if path.startswith("/admin/") or path in ("/admin", "/v1/admin/summary", "/v1/admin/metrics"):
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
    admin_auth_service: Optional[AdminAuthService] = None,
) -> ThreadingHTTPServer:
    analytics_store = build_analytics_store_from_env(db_path=db_path)
    resolved_public_base_url = public_base_url or f"http://{host}:{port}"
    backend = ShoppingBackend(
        adapter=adapter,
        analytics_store=analytics_store,
        shortener=shortener or _build_shortener_from_env(db_path=db_path, public_base_url=resolved_public_base_url),
        allowed_deeplink_hosts=allowed_deeplink_hosts or _load_allowed_deeplink_hosts(),
    )
    handler = type(
        "OpenClawRequestHandler",
        (_Handler,),
        {"backend": backend, "admin_auth": admin_auth_service or build_admin_auth_service_from_env()},
    )
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
    handler = type(
        "OpenClawRequestHandler",
        (_Handler,),
        {"backend": backend, "admin_auth": build_admin_auth_service_from_env()},
    )
    server = ThreadingHTTPServer((resolved_host, resolved_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()

def _build_shortener_from_env(*, db_path: str, public_base_url: str) -> Optional[UrlShortener]:
    provider = (os.getenv("OPENCLAW_SHOPPING_SHORTENER") or "").strip().lower()
    safety_policy = UrlSafetyPolicy.from_env()
    if provider in ("", "builtin", "local"):
        return BuiltinShortener(db_path=db_path, public_base_url=public_base_url, safety_policy=safety_policy)
    if provider in ("firestore", "gcp"):
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("OPENCLAW_GCP_PROJECT") or ""
        return FirestoreShortener(
            project_id=project_id,
            public_base_url=public_base_url,
            collection=os.getenv("OPENCLAW_SHORT_LINKS_COLLECTION", "short_links"),
            database=os.getenv("OPENCLAW_FIRESTORE_DATABASE", "(default)"),
            access_token=os.getenv("OPENCLAW_GCP_ACCESS_TOKEN"),
            emulator_host=os.getenv("FIRESTORE_EMULATOR_HOST"),
            safety_policy=safety_policy,
            safety_collection=os.getenv("OPENCLAW_SHORTENER_SAFETY_COLLECTION", "url_safety_cache"),
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
