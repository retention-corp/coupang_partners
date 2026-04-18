"""Coupang product-page review extraction.

Thin wrapper over the existing `product_page_evidence.fetch_product_page_evidence`.
Coupang rate-limits aggressive scrapers and their review HTML structure changes
frequently; we reuse the repo's already-hardened parser and expose only a small,
composer-friendly surface: title, description, and up to N first-person review
snippets.

The function is best-effort. Coupang not loading = empty payload, not exception.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from product_page_evidence import fetch_product_page_evidence

LOGGER = logging.getLogger("book_intel.sources.coupang_reviews")

# Heuristic: Coupang product snippets that read like a personal review tend to
# contain first-person Korean markers + minimum meaningful length. We filter on
# both to avoid surfacing promo copy or category breadcrumbs as "reviews".
_REVIEW_CUES = ("읽었", "샀어요", "좋아요", "만족", "구매", "선물", "추천", "별로", "실망")
_MIN_REVIEW_LEN = 15
_MAX_REVIEW_LEN = 280


def fetch_reviews(
    product: dict[str, Any],
    *,
    max_reviews: int = 5,
    timeout_seconds: int = 6,
) -> dict[str, Any]:
    """Return {title, description, top_reviews: [str]} for a Coupang product dict.

    `product` should carry the fields produced by `_normalize_search_product` or
    the Coupang Partners search response raw — landing_page_url / productUrl /
    pageKey + itemId + vendorItemId all work.
    """

    evidence = fetch_product_page_evidence(product, timeout_seconds=timeout_seconds)
    if not evidence:
        return {"title": "", "description": "", "top_reviews": []}

    title = (evidence.get("page_title") or "").strip()
    description = (evidence.get("page_description") or "").strip()
    snippets = evidence.get("page_snippets") or []
    reviews = _select_review_snippets(snippets, limit=max_reviews)

    return {
        "title": title,
        "description": description,
        "top_reviews": reviews,
    }


def _select_review_snippets(snippets: list[str], *, limit: int) -> list[str]:
    picked: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        text = _normalize(snippet)
        if not text:
            continue
        if len(text) < _MIN_REVIEW_LEN or len(text) > _MAX_REVIEW_LEN:
            continue
        if not any(cue in text for cue in _REVIEW_CUES):
            continue
        fingerprint = text[:32]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        picked.append(text)
        if len(picked) >= limit:
            break
    return picked


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text


__all__ = ["fetch_reviews"]
