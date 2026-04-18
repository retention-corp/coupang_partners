"""Glue between `ShoppingBackend` and the book_reco recommendation stack.

Exposes a single entrypoint, `book_assist`, that:
1. Builds book providers/services on demand (fails gracefully to fallback curation).
2. Resolves the user's free-text query via `RecommendationService`.
3. Re-ranks the over-fetched pool through a persona profile (explicit payload hints
   merged with implicit analytics replay for the same `client_id`).
4. Maps each recommended book to a Coupang product via `coupang_bridge`.
5. Attaches short deeplinks, persona-signal explanations, and the affiliate disclosure.

Kept isolated from `backend.py` so the book vertical never pulls pydantic/httpx etc. into
the hosted backend runtime — the whole module is stdlib.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .coupang_bridge import attach_coupang_products
from .models import BookRecord, ProviderError, RecommendationResponse, TrendingResponse
from .persona import (
    PersonaProfile,
    build_from_analytics,
    build_from_notion,
    build_from_payload,
    merge,
)
from .providers.data4library import Data4LibraryProvider
from .providers.fallback import FallbackProvider
from .providers.naver import NaverBookProvider
from .providers.nlk import NLKMetadataProvider
from .providers.saseo import SaseoRecommendationProvider
from .services.recommendation_service import RecommendationService
from .utils import LOGGER

DISCLOSURE_TEXT_DEFAULT = "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음"

ShortenFn = Callable[[str], str]
ValidateHostFn = Callable[[str], bool]


def _safe(build: Callable[[], Any]) -> Any:
    try:
        return build()
    except Exception as exc:
        LOGGER.debug("book_reco provider skipped: %s", exc)
        return None


def _build_service() -> RecommendationService:
    search = _safe(NaverBookProvider)
    metadata = _safe(NLKMetadataProvider) or search
    data4 = _safe(Data4LibraryProvider)
    saseo = _safe(SaseoRecommendationProvider)
    fallback = FallbackProvider(search_provider=search)
    return RecommendationService(
        search_provider=search,
        recommendation_provider=data4,
        fallback_recommendation_provider=fallback,
        trending_provider=data4,
        fallback_trending_provider=fallback,
        metadata_provider=metadata,
        curated_provider=saseo,
    )


def _errors_to_dicts(errors: List[ProviderError]) -> List[Dict[str, Any]]:
    return [err.to_dict() for err in errors]


def _apply_short_link(
    deeplink: str,
    *,
    shorten_fn: Optional[ShortenFn],
    validate_host_fn: Optional[ValidateHostFn],
) -> str:
    if not deeplink or not shorten_fn:
        return deeplink
    if validate_host_fn is not None and not validate_host_fn(deeplink):
        return deeplink
    try:
        return shorten_fn(deeplink)
    except Exception as exc:
        LOGGER.warning("book_reco shortener_error: %s", exc)
        return deeplink


def _resolve_books(
    service: RecommendationService,
    *,
    query: str,
    isbn: str,
    limit: int,
) -> tuple[Optional[BookRecord], List[BookRecord], List[ProviderError]]:
    """Pick the right flow (isbn / query / trending). Returns (seed, books, errors)."""

    if isbn:
        response: RecommendationResponse = service.recommend_by_isbn(isbn, limit=limit)
        return response.seed, response.recommendations, response.errors
    if query:
        response = service.recommend_by_query(query, limit=limit)
        return response.seed, response.recommendations, response.errors
    trending: TrendingResponse = service.trending(limit=limit)
    return None, trending.books, trending.errors


def _build_profile(
    payload: Dict[str, Any],
    client_id: Optional[str],
    analytics_store: Any,
) -> PersonaProfile:
    """Combine explicit + implicit + notion signals into a merged PersonaProfile.

    Every source is optional; any failure degrades to the remaining sources silently.
    explicit > analytics > notion by precedence for scalar fields, union for lists.
    """

    explicit = build_from_payload(payload)
    implicit = build_from_analytics(analytics_store, client_id)
    notion = build_from_notion()
    return merge(explicit, implicit, notion)


def book_assist(
    payload: Dict[str, Any],
    *,
    search_products_fn: Callable[..., Any],
    shorten_fn: Optional[ShortenFn] = None,
    validate_host_fn: Optional[ValidateHostFn] = None,
    disclosure_text: str = DISCLOSURE_TEXT_DEFAULT,
    service_factory: Callable[[], RecommendationService] = _build_service,
    client_id: Optional[str] = None,
    analytics_store: Any = None,
) -> Dict[str, Any]:
    """Run the book-vertical flow.

    `search_products_fn` is the Coupang search adapter. `client_id` and
    `analytics_store` are optional: when both are present, the service replays the
    client's recent queries to derive an implicit persona profile. Explicit persona
    hints live under `payload["persona"]` and always win over inferred values on
    scalar fields while unioning on list fields.
    """

    query = str(payload.get("query") or "").strip()
    isbn = str(payload.get("isbn") or payload.get("isbn13") or "").strip()
    raw_limit = payload.get("limit")
    try:
        limit = max(1, min(int(raw_limit), 20)) if raw_limit is not None else 5
    except (TypeError, ValueError):
        limit = 5

    profile = _build_profile(payload, client_id, analytics_store)

    service = service_factory()
    # Over-fetch so the Coupang bridge can absorb books with no matching product without
    # under-filling the response. saseoApi.do returns a frozen ~10-item set; the fallback
    # curated list contributes recent bestsellers so match rate stays healthy.
    over_fetch = max(limit * 3, 10)
    seed, books, errors = _resolve_books(service, query=query, isbn=isbn, limit=over_fetch)

    # Persona-aware re-ranking when we have any signal. Empty profile → no-op.
    persona_signals: Dict[str, List[Dict[str, Any]]] = {}
    if not profile.is_empty():
        books, persona_signals = service.rank_with_profile(books, profile)

    # Match books to Coupang products. attach_coupang_products silently drops misses.
    matched = attach_coupang_products(
        books,
        search_fn=search_products_fn,
        limit=5,
        max_matches=limit,
    )

    # Attach short deeplinks + per-recommendation persona explanation.
    for entry in matched:
        product = entry.get("product") or {}
        deeplink = product.get("deeplink", "")
        product["short_deeplink"] = _apply_short_link(
            deeplink,
            shorten_fn=shorten_fn,
            validate_host_fn=validate_host_fn,
        )
        book_dict = entry.get("book") or {}
        key = book_dict.get("isbn13") or book_dict.get("title") or ""
        if key and key in persona_signals:
            entry["persona_signals"] = persona_signals[key]

    return {
        "ok": True,
        "vertical": "book",
        "query": query,
        "isbn": isbn,
        "seed": seed.to_dict() if seed else None,
        "books": [book.to_dict() for book in books],
        "recommendations": matched,
        "counts": {
            "books": len(books),
            "matched": len(matched),
            "persona_signals_used": sum(len(v) for v in persona_signals.values()),
        },
        "persona": profile.to_dict() if not profile.is_empty() else None,
        "errors": _errors_to_dicts(errors),
        "disclosure": disclosure_text,
    }


__all__ = ["book_assist", "DISCLOSURE_TEXT_DEFAULT"]
