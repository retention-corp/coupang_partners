"""Shared data models (stdlib-only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass
class BookRecord:
    """Normalized book record used across all providers and interfaces."""

    title: str
    author: str = ""
    publisher: str = ""
    isbn13: str = ""
    pub_date: str = ""
    description: str = ""
    source: str = ""
    thumbnail: str = ""
    category: str = ""
    popularity_score: float | None = None
    recommendation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def copy_with(self, **updates: Any) -> "BookRecord":
        return replace(self, **updates)


@dataclass
class ProviderError:
    """Structured provider error."""

    provider: str
    message: str
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResponse:
    """Search response payload."""

    query: str
    books: list[BookRecord] = field(default_factory=list)
    errors: list[ProviderError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationResponse:
    """Recommendation response payload."""

    seed: BookRecord | None = None
    recommendations: list[BookRecord] = field(default_factory=list)
    errors: list[ProviderError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DescribeResponse:
    """Single-book description payload."""

    book: BookRecord | None = None
    errors: list[ProviderError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendingResponse:
    """Trending books payload."""

    books: list[BookRecord] = field(default_factory=list)
    errors: list[ProviderError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MCPResult:
    """Generic MCP result wrapper."""

    ok: bool
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
