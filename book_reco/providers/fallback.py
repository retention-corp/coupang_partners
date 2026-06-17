"""Fallback provider logic."""

from __future__ import annotations

from ..models import BookRecord
from ..utils import score_candidate
from .base import (
    BookRecommendationProvider,
    BookSearchProvider,
    TrendingBooksProvider,
)


class FallbackProvider(BookRecommendationProvider, TrendingBooksProvider):
    """Deterministic fallback provider using search results and curated trending data."""

    def __init__(self, search_provider: BookSearchProvider | None = None) -> None:
        self.search_provider = search_provider

    def recommend(self, seed: BookRecord, limit: int = 5) -> list[BookRecord]:
        if not self.search_provider:
            return []
        query_terms = " ".join(filter(None, [seed.title, seed.author, seed.category])).strip()
        if not query_terms:
            query_terms = seed.title or seed.isbn13
        candidates = self.search_provider.search(query_terms, limit=max(limit * 3, 15))
        ranked: list[tuple[float, BookRecord]] = []
        for candidate in candidates:
            if candidate.isbn13 and candidate.isbn13 == seed.isbn13:
                continue
            score = score_candidate(seed, candidate)
            reason = self._reason(seed, candidate)
            ranked.append((score, candidate.copy_with(recommendation_reason=reason)))
        ranked.sort(key=lambda item: (-item[0], item[1].title))
        return [book for _, book in ranked[:limit]]

    def trending(self, limit: int = 10) -> list[BookRecord]:
        curated = [
            BookRecord(title="불편한 편의점", author="김호연", publisher="나무옆의자", isbn13="9791161571188", source="fallback", category="한국소설", popularity_score=95, recommendation_reason="widely-read Korean fiction fallback"),
            BookRecord(title="아주 희미한 빛으로도", author="최은영", publisher="문학동네", isbn13="9788954699916", source="fallback", category="한국소설", popularity_score=92, recommendation_reason="recent Korean literary popularity fallback"),
            BookRecord(title="세이노의 가르침", author="세이노", publisher="데이원", isbn13="9791168473690", source="fallback", category="자기계발", popularity_score=90, recommendation_reason="popular self-development fallback"),
            BookRecord(title="역행자", author="자청", publisher="웅진지식하우스", isbn13="9788901260718", source="fallback", category="자기계발", popularity_score=88, recommendation_reason="popular Korean non-fiction fallback"),
            BookRecord(title="작별인사", author="김영하", publisher="복복서가", isbn13="9791191114225", source="fallback", category="한국소설", popularity_score=87, recommendation_reason="popular Korean speculative fiction fallback"),
            BookRecord(title="시대예보: 핵개인의 시대", author="송길영", publisher="교보문고", isbn13="9791193453674", source="fallback", category="인문학", popularity_score=86, recommendation_reason="recent trend non-fiction fallback"),
            BookRecord(title="아버지의 해방일지", author="정지아", publisher="창비", isbn13="9788936434595", source="fallback", category="한국소설", popularity_score=85, recommendation_reason="recent Korean literary fiction fallback"),
            BookRecord(title="달러구트 꿈 백화점", author="이미예", publisher="팩토리나인", isbn13="9791165681036", source="fallback", category="한국소설", popularity_score=84, recommendation_reason="popular Korean fantasy fallback"),
            BookRecord(title="도둑맞은 집중력", author="요한 하리", publisher="어크로스", isbn13="9791167741288", source="fallback", category="자기계발", popularity_score=83, recommendation_reason="popular translated non-fiction fallback"),
            BookRecord(title="더 시스템", author="스콧 애덤스", publisher="베리북", isbn13="9791168411401", source="fallback", category="자기계발", popularity_score=82, recommendation_reason="popular translated non-fiction fallback"),
        ]
        return curated[:limit]

    def _reason(self, seed: BookRecord, candidate: BookRecord) -> str:
        reasons: list[str] = []
        if seed.category and candidate.category and seed.category == candidate.category:
            reasons.append("same category")
        if seed.author and candidate.author and seed.author == candidate.author:
            reasons.append("same author")
        if seed.publisher and candidate.publisher and seed.publisher == candidate.publisher:
            reasons.append("same publisher")
        if not reasons:
            reasons.append("title and metadata similarity")
        return ", ".join(reasons)
