"""Glue between `ShoppingBackend` and the book_reco recommendation stack.

Exposes a single entrypoint, `book_assist`, that:
1. Builds book providers/services on demand (fails gracefully to fallback curation).
2. Resolves the user's free-text query via `RecommendationService`.
3. Maps each recommended book to a Coupang product via `coupang_bridge`.
4. Attaches short deeplinks + the affiliate disclosure.

Kept isolated from `backend.py` so the book vertical never pulls pydantic/httpx etc. into
the hosted backend runtime — the whole module is stdlib.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .coupang_bridge import attach_coupang_products
from .models import BookRecord, ProviderError, RecommendationResponse, TrendingResponse
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


def book_assist(
    payload: Dict[str, Any],
    *,
    search_products_fn: Callable[..., Any],
    shorten_fn: Optional[ShortenFn] = None,
    validate_host_fn: Optional[ValidateHostFn] = None,
    disclosure_text: str = DISCLOSURE_TEXT_DEFAULT,
    service_factory: Callable[[], RecommendationService] = _build_service,
) -> Dict[str, Any]:
    """Run the book-vertical flow. `search_products_fn` is the Coupang search adapter."""

    query = str(payload.get("query") or "").strip()
    isbn = str(payload.get("isbn") or payload.get("isbn13") or "").strip()
    raw_limit = payload.get("limit")
    try:
        limit = max(1, min(int(raw_limit), 20)) if raw_limit is not None else 5
    except (TypeError, ValueError):
        limit = 5

    service = service_factory()
    # Over-fetch so the Coupang bridge can absorb books with no matching product without
    # under-filling the response. saseoApi.do returns a frozen ~10-item set; the fallback
    # curated list contributes recent bestsellers so match rate stays healthy.
    over_fetch = max(limit * 3, 10)
    seed, books, errors = _resolve_books(service, query=query, isbn=isbn, limit=over_fetch)

    # Match books to Coupang products. attach_coupang_products silently drops misses.
    matched = attach_coupang_products(
        books,
        search_fn=search_products_fn,
        limit=5,
        max_matches=limit,
    )

    # Attach short deeplinks.
    for entry in matched:
        product = entry.get("product") or {}
        deeplink = product.get("deeplink", "")
        product["short_deeplink"] = _apply_short_link(
            deeplink,
            shorten_fn=shorten_fn,
            validate_host_fn=validate_host_fn,
        )

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
        },
        "errors": _errors_to_dicts(errors),
        "disclosure": disclosure_text,
    }


__all__ = ["book_assist", "DISCLOSURE_TEXT_DEFAULT"]
