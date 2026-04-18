"""Search service."""

from __future__ import annotations

from ..models import DescribeResponse, ProviderError, SearchResponse
from ..providers.base import BookMetadataProvider, BookSearchProvider
from ..utils import LOGGER


class SearchService:
    """Service layer for searching and describing books."""

    def __init__(
        self,
        search_provider: BookSearchProvider | None,
        metadata_provider: BookMetadataProvider | None = None,
        fallback_metadata_provider: BookMetadataProvider | None = None,
    ) -> None:
        self.search_provider = search_provider
        self.metadata_provider = metadata_provider
        self.fallback_metadata_provider = fallback_metadata_provider

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        errors: list[ProviderError] = []
        if not self.search_provider:
            errors.append(ProviderError(provider="search", message="No search provider configured", recoverable=False))
            return SearchResponse(query=query, books=[], errors=errors)
        try:
            books = self.search_provider.search(query, limit=limit)
            return SearchResponse(query=query, books=books, errors=errors)
        except Exception as exc:
            LOGGER.warning("search failed: %s", exc)
            errors.append(ProviderError(provider=type(self.search_provider).__name__, message=str(exc)))
            return SearchResponse(query=query, books=[], errors=errors)

    def describe(self, isbn13: str) -> DescribeResponse:
        errors: list[ProviderError] = []
        for provider in [self.metadata_provider, self.fallback_metadata_provider]:
            if not provider:
                continue
            try:
                book = provider.describe(isbn13)
                if book:
                    return DescribeResponse(book=book, errors=errors)
            except Exception as exc:
                LOGGER.warning("describe failed via %s: %s", type(provider).__name__, exc)
                errors.append(ProviderError(provider=type(provider).__name__, message=str(exc)))
        return DescribeResponse(book=None, errors=errors)
