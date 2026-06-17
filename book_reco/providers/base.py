"""Provider interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import BookRecord


class BookSearchProvider(ABC):
    """Search provider interface."""

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[BookRecord]:
        """Search books by query."""


class BookRecommendationProvider(ABC):
    """Recommendation provider interface."""

    @abstractmethod
    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]:
        """Recommend books from a seed book."""


class BookMetadataProvider(ABC):
    """Metadata provider interface."""

    @abstractmethod
    def describe(self, isbn13: str) -> BookRecord | None:
        """Describe a book by ISBN13."""


class TrendingBooksProvider(ABC):
    """Trending books provider interface."""

    @abstractmethod
    def trending(self, limit: int = 10) -> list[BookRecord]:
        """Return trending books."""
