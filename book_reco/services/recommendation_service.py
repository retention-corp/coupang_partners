"""Recommendation service."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import BookRecord, ProviderError, RecommendationResponse, TrendingResponse
from ..persona import PersonaProfile
from ..providers.base import (
    BookMetadataProvider,
    BookRecommendationProvider,
    BookSearchProvider,
    TrendingBooksProvider,
)
from ..utils import LOGGER, score_candidate_with_persona


@runtime_checkable
class CuratedProvider(Protocol):
    """A curator-grade source that can both trend and recommend (e.g. saseo)."""

    def trending(self, limit: int = 10) -> list[BookRecord]: ...

    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]: ...


class RecommendationService:
    """Service layer for recommendation and trending flows.

    Provider precedence:
      trending:       curated → trending → fallback_trending (merged, curated first, ISBN13 dedup)
      recommend:      curated → recommendation → fallback_recommendation (first non-empty wins)
    """

    def __init__(
        self,
        search_provider: BookSearchProvider | None,
        recommendation_provider: BookRecommendationProvider | None,
        fallback_recommendation_provider: BookRecommendationProvider | None,
        trending_provider: TrendingBooksProvider | None,
        fallback_trending_provider: TrendingBooksProvider | None,
        metadata_provider: BookMetadataProvider | None = None,
        curated_provider: CuratedProvider | None = None,
    ) -> None:
        self.search_provider = search_provider
        self.recommendation_provider = recommendation_provider
        self.fallback_recommendation_provider = fallback_recommendation_provider
        self.trending_provider = trending_provider
        self.fallback_trending_provider = fallback_trending_provider
        self.metadata_provider = metadata_provider
        self.curated_provider = curated_provider

    def recommend_by_isbn(self, isbn13: str, limit: int = 5) -> RecommendationResponse:
        errors: list[ProviderError] = []
        seed = None
        if self.metadata_provider:
            try:
                seed = self.metadata_provider.describe(isbn13)
            except Exception as exc:
                LOGGER.warning("metadata lookup failed: %s", exc)
                errors.append(ProviderError(provider=type(self.metadata_provider).__name__, message=str(exc)))
        if not seed and self.search_provider:
            try:
                matches = self.search_provider.search(isbn13, limit=3)
                seed = next((book for book in matches if book.isbn13 == isbn13), matches[0] if matches else None)
            except Exception as exc:
                LOGGER.warning("seed search failed: %s", exc)
                errors.append(ProviderError(provider=type(self.search_provider).__name__, message=str(exc)))
        if not seed:
            errors.append(ProviderError(provider="seed", message=f"Unable to resolve ISBN13: {isbn13}", recoverable=False))
            return RecommendationResponse(seed=None, recommendations=[], errors=errors)
        return self._recommend_from_seed(seed, limit, errors)

    def recommend_by_query(self, query: str, limit: int = 5) -> RecommendationResponse:
        errors: list[ProviderError] = []
        if not self.search_provider:
            errors.append(ProviderError(provider="search", message="No search provider configured", recoverable=False))
            return RecommendationResponse(seed=None, recommendations=[], errors=errors)
        try:
            matches = self.search_provider.search(query, limit=5)
        except Exception as exc:
            LOGGER.warning("query search failed: %s", exc)
            errors.append(ProviderError(provider=type(self.search_provider).__name__, message=str(exc), recoverable=False))
            return RecommendationResponse(seed=None, recommendations=[], errors=errors)
        if not matches:
            errors.append(ProviderError(provider="search", message=f"No books found for query: {query}", recoverable=False))
            return RecommendationResponse(seed=None, recommendations=[], errors=errors)
        return self._recommend_from_seed(matches[0], limit, errors)

    def trending(self, limit: int = 10) -> TrendingResponse:
        """Merge curator (saseo) + loan trend + fallback. Curator entries come first.

        We deliberately do **not** short-circuit once `limit` is reached: downstream
        consumers (e.g. the Coupang bridge) may need to discard books with no matching
        product, and over-fetching across providers keeps the match rate healthy when
        any one provider returns stale or unmonetizable titles. ISBN13 dedup prevents
        duplicates; callers decide how many entries they actually want to surface.
        """

        errors: list[ProviderError] = []
        merged: list[BookRecord] = []
        seen_isbn: set[str] = set()
        seen_keys: set[str] = set()

        ordered = [
            ("curated", self.curated_provider),
            ("trending", self.trending_provider),
            ("fallback", self.fallback_trending_provider),
        ]
        for label, provider in ordered:
            if provider is None:
                continue
            try:
                books = provider.trending(limit=limit)
            except Exception as exc:
                LOGGER.warning("%s trending failed via %s: %s", label, type(provider).__name__, exc)
                errors.append(ProviderError(provider=type(provider).__name__, message=str(exc)))
                continue
            for book in books or []:
                key = book.isbn13 or f"{book.title}|{book.author}"
                if book.isbn13 and book.isbn13 in seen_isbn:
                    continue
                if key in seen_keys:
                    continue
                merged.append(book)
                seen_keys.add(key)
                if book.isbn13:
                    seen_isbn.add(book.isbn13)
        return TrendingResponse(books=merged, errors=errors)

    def rank_with_profile(
        self,
        books: list[BookRecord],
        profile: PersonaProfile | None,
    ) -> tuple[list[BookRecord], dict[str, list[dict[str, object]]]]:
        """Re-sort candidates using a persona profile. Returns (ordered_books, signal_index).

        `signal_index` maps `BookRecord.isbn13 or title` → list of signal dicts so the
        backend integration layer can attach per-recommendation explanations.

        A negative score (e.g. an `avoid_categories` hit) drops the candidate entirely
        rather than just ranking it low — this is the user's explicit veto.
        """

        if not profile or profile.is_empty() or not books:
            return books, {}

        scored: list[tuple[float, BookRecord, list[dict[str, object]]]] = []
        for book in books:
            score, signals = score_candidate_with_persona(book, profile)
            if score < 0:
                continue
            scored.append((score, book, signals))

        scored.sort(key=lambda entry: (-entry[0], entry[1].title))
        signal_index: dict[str, list[dict[str, object]]] = {}
        ordered: list[BookRecord] = []
        for _, book, signals in scored:
            ordered.append(book)
            key = book.isbn13 or book.title
            if signals:
                signal_index[key] = signals
        return ordered, signal_index

    def _recommend_from_seed(self, seed: BookRecord, limit: int, errors: list[ProviderError]) -> RecommendationResponse:
        # Curator first (librarian picks near the seed's category/author), then ISBN-driven
        # recommendation providers (e.g. Data4Library), then deterministic fallback.
        ordered: list[BookRecommendationProvider | CuratedProvider] = []
        if self.curated_provider is not None:
            ordered.append(self.curated_provider)
        if self.recommendation_provider is not None:
            ordered.append(self.recommendation_provider)
        if self.fallback_recommendation_provider is not None:
            ordered.append(self.fallback_recommendation_provider)

        for provider in ordered:
            try:
                books = provider.recommend(seed, limit=limit)
                if books:
                    return RecommendationResponse(seed=seed, recommendations=books, errors=errors)
            except Exception as exc:
                LOGGER.warning("recommendation failed via %s: %s", type(provider).__name__, exc)
                errors.append(ProviderError(provider=type(provider).__name__, message=str(exc)))
        return RecommendationResponse(seed=seed, recommendations=[], errors=errors)
