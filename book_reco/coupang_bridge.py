"""Bridge book_reco BookRecord → Coupang Partners affiliate product.

Query strategy (CLAUDE.md: ISBN-only lookup is not trusted — validate with title/author):

1. Primary query: "{title} {author}" — catches most Korean book listings on Coupang.
2. Fallback: "{title} {publisher}" when author is missing or the first pass has no matches.
3. ISBN13 is used **only to validate** a candidate (when Coupang returns it in product fields),
   never as the primary lookup term.

A candidate is accepted only when the book title's distinctive tokens overlap with the
Coupang product name, to avoid surfacing unrelated products when Coupang has no listing for
the book (this happens frequently for minor titles). Missed matches are dropped silently —
we never fabricate an affiliate link or claim we found the book.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .models import BookRecord
from .utils import LOGGER, tokenize_koreanish

# Matches a typical Coupang book product title: anything containing at least one of these.
_BOOK_HINT_TOKENS = ("도서", "책", "book")
_MIN_TOKEN_OVERLAP = 2
_MAX_CANDIDATES = 10

# A SearchFn is anything that accepts keyword args and returns the raw Coupang search payload
# (as returned by CoupangPartnersClient.search_products). We accept a callable rather than the
# client itself so tests can pass a pure function without a client instance.
SearchFn = Callable[..., Any]


def _coupang_products(payload: Any) -> list[dict[str, Any]]:
    """Flatten the `{data: {...}}` wrapper from CoupangPartnersClient.search_products."""

    if not payload:
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [p for p in data if isinstance(p, dict)]
        if isinstance(data, dict):
            for key in ("products", "productData", "items"):
                items = data.get(key)
                if isinstance(items, list):
                    return [p for p in items if isinstance(p, dict)]
        for key in ("products", "productData", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return [p for p in items if isinstance(p, dict)]
    return []


def _normalize_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": product.get("productName") or product.get("title") or "",
        "price": product.get("productPrice") or product.get("salePrice") or product.get("price"),
        "is_rocket": bool(product.get("isRocket") or product.get("is_rocket")),
        "is_free_shipping": bool(product.get("isFreeShipping") or product.get("is_free_shipping")),
        "rating": product.get("ratingAverage") or product.get("rating") or 0,
        "review_count": product.get("reviewCount") or product.get("review_count") or 0,
        "deeplink": product.get("productUrl") or product.get("deeplink") or product.get("url") or "",
        "category_name": product.get("categoryName") or "",
    }


def _title_tokens(title: str) -> set[str]:
    # Drop common book-hint suffix words that every book listing has; they're noise for matching.
    raw = tokenize_koreanish(title)
    return {t for t in raw if t not in {"도서", "책"}}


def _product_isbn(product: dict[str, Any]) -> str:
    for key in ("isbn", "isbn13", "isbnCode"):
        value = product.get(key)
        if isinstance(value, str):
            digits = re.sub(r"[^0-9]", "", value)
            if len(digits) >= 13:
                return digits[:13]
    return ""


def _matches(book: BookRecord, product: dict[str, Any]) -> bool:
    product_title = str(product.get("productName") or product.get("title") or "")
    if not product_title:
        return False

    # Fast path: ISBN13 exact match when Coupang happens to expose it.
    product_isbn = _product_isbn(product)
    if book.isbn13 and product_isbn and product_isbn == book.isbn13:
        return True

    book_tokens = _title_tokens(book.title)
    product_tokens = _title_tokens(product_title)
    if not book_tokens:
        return False
    overlap = book_tokens & product_tokens
    if len(overlap) < min(_MIN_TOKEN_OVERLAP, len(book_tokens)):
        return False

    # When we have an author, require at least partial author signal to weed out
    # same-title-different-book hits (common for generic titles).
    if book.author:
        author_primary = book.author.split(",")[0].strip().split()[0] if book.author.split() else ""
        if author_primary and len(author_primary) >= 2:
            haystack = f"{product_title} {product.get('vendor') or ''} {product.get('brand') or ''}"
            if author_primary not in haystack:
                # Allow if the title overlap is overwhelming (≥3 tokens or every book token).
                if len(overlap) < max(3, len(book_tokens)):
                    return False

    return True


def _queries_for(book: BookRecord) -> Iterable[str]:
    title = (book.title or "").strip()
    if not title:
        return
    seen: set[str] = set()

    def _yield(q: str) -> str | None:
        key = q.strip()
        if not key or key in seen:
            return None
        seen.add(key)
        return key

    primary = _yield(f"{title} {book.author}".strip() if book.author else title)
    if primary:
        yield primary
    if book.author:
        secondary = _yield(f"{title} 도서")
        if secondary:
            yield secondary
    if book.publisher:
        tertiary = _yield(f"{title} {book.publisher}")
        if tertiary:
            yield tertiary


def find_coupang_product(
    book: BookRecord,
    *,
    search_fn: SearchFn,
    limit: int = 5,
) -> dict[str, Any] | None:
    """Return the best-matching Coupang product for a book, or None when no match passes filters."""

    for query in _queries_for(book):
        try:
            raw = search_fn(keyword=query, limit=limit)
        except Exception as exc:
            LOGGER.warning("coupang search failed for %r: %s", query, exc)
            continue
        candidates = _coupang_products(raw)[:_MAX_CANDIDATES]
        if not candidates:
            continue
        for product in candidates:
            if _matches(book, product):
                return _normalize_product(product)
    return None


def attach_coupang_products(
    books: list[BookRecord],
    *,
    search_fn: SearchFn,
    limit: int = 5,
    max_matches: int | None = None,
) -> list[dict[str, Any]]:
    """Match books to Coupang products. Stops early once `max_matches` are found.

    `limit` is the per-search candidate cap (how many Coupang results to scan per query),
    not a cap on returned matches. Use `max_matches` to cap the final list — typically the
    caller passes a small N there and hands this function an over-fetched `books` list so
    misses can be absorbed silently.
    """

    matched: list[dict[str, Any]] = []
    for book in books:
        if max_matches is not None and len(matched) >= max_matches:
            break
        product = find_coupang_product(book, search_fn=search_fn, limit=limit)
        if not product:
            continue
        matched.append(
            {
                "book": book.to_dict(),
                "product": product,
                "source": book.source or "book_reco",
                "recommendation_reason": book.recommendation_reason,
            }
        )
    return matched


__all__ = [
    "attach_coupang_products",
    "find_coupang_product",
]
