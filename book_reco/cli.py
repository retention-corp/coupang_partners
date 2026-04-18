"""CLI entrypoint (stdlib argparse)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from typing import Any

from .config import get_settings
from .models import BookRecord
from .providers.data4library import Data4LibraryProvider
from .providers.fallback import FallbackProvider
from .providers.naver import NaverBookProvider
from .providers.nlk import NLKMetadataProvider
from .services.recommendation_service import RecommendationService
from .services.search_service import SearchService
from .utils import configure_logging, dump_json


def build_services() -> tuple[SearchService, RecommendationService]:
    """Build service graph with graceful provider fallback."""

    configure_logging()

    search_provider = None
    metadata_provider = None
    recommendation_provider = None
    trending_provider = None

    try:
        search_provider = NaverBookProvider()
    except Exception:
        search_provider = None

    try:
        metadata_provider = NLKMetadataProvider()
    except Exception:
        metadata_provider = search_provider if search_provider else None

    try:
        data4 = Data4LibraryProvider()
        recommendation_provider = data4
        trending_provider = data4
    except Exception:
        recommendation_provider = None
        trending_provider = None

    # 사서추천도서 provider, wired in via services/__init__ builder when available
    try:
        from .providers.saseo import SaseoRecommendationProvider

        saseo = SaseoRecommendationProvider()
    except Exception:
        saseo = None

    fallback = FallbackProvider(search_provider=search_provider)
    search_service = SearchService(
        search_provider=search_provider,
        metadata_provider=metadata_provider,
        fallback_metadata_provider=search_provider,
    )
    recommendation_service = RecommendationService(
        search_provider=search_provider,
        recommendation_provider=recommendation_provider,
        fallback_recommendation_provider=fallback,
        trending_provider=trending_provider,
        fallback_trending_provider=fallback,
        metadata_provider=metadata_provider or search_provider,
        curated_provider=saseo,
    )
    return search_service, recommendation_service


def _print_books(books: list[BookRecord]) -> None:
    if not books:
        print("No books found.")
        return
    for index, book in enumerate(books, start=1):
        print(f"{index}. {book.title}")
        print(f"   author: {book.author}")
        print(f"   publisher: {book.publisher}")
        print(f"   isbn13: {book.isbn13}")
        if book.category:
            print(f"   category: {book.category}")
        if book.recommendation_reason:
            print(f"   reason: {book.recommendation_reason}")


def _print_errors(errors: list) -> None:
    for error in errors:
        print(f"warning: {error.provider}: {error.message}", file=sys.stderr)


def _cmd_search(args: argparse.Namespace) -> int:
    search_service, _ = build_services()
    response = search_service.search(args.query, limit=get_settings().kbook_max_results)
    if args.json:
        print(dump_json(_to_dict(response)))
    else:
        _print_books(response.books)
        _print_errors(response.errors)
    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    if not args.isbn and not args.query:
        print("error: provide --isbn or --query", file=sys.stderr)
        return 2
    _, recommendation_service = build_services()
    if args.isbn:
        response = recommendation_service.recommend_by_isbn(args.isbn, limit=get_settings().kbook_max_results)
    else:
        response = recommendation_service.recommend_by_query(args.query, limit=get_settings().kbook_max_results)
    if args.json:
        print(dump_json(_to_dict(response)))
    else:
        if response.seed:
            print(f"Seed: {response.seed.title} / {response.seed.author} / {response.seed.isbn13}")
        _print_books(response.recommendations)
        _print_errors(response.errors)
    return 0


def _cmd_trending(args: argparse.Namespace) -> int:
    _, recommendation_service = build_services()
    response = recommendation_service.trending(limit=get_settings().kbook_max_results)
    if args.json:
        print(dump_json(_to_dict(response)))
    else:
        _print_books(response.books)
        _print_errors(response.errors)
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    search_service, _ = build_services()
    response = search_service.describe(args.isbn)
    if args.json:
        print(dump_json(_to_dict(response)))
        return 0
    if response.book:
        _print_books([response.book])
        if response.book.description:
            print(f"description: {response.book.description}")
    else:
        print("Book not found.")
    _print_errors(response.errors)
    return 0


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return asdict(obj)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book-reco", description="Korean book recommender CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_p = subparsers.add_parser("search", help="Search Korean books")
    search_p.add_argument("query")
    search_p.add_argument("--json", action="store_true")
    search_p.set_defaults(func=_cmd_search)

    recommend_p = subparsers.add_parser("recommend", help="Recommend books by ISBN13 or query")
    recommend_p.add_argument("--isbn", default="")
    recommend_p.add_argument("--query", default="")
    recommend_p.add_argument("--json", action="store_true")
    recommend_p.set_defaults(func=_cmd_recommend)

    trending_p = subparsers.add_parser("trending", help="Return trending Korean books")
    trending_p.add_argument("--json", action="store_true")
    trending_p.set_defaults(func=_cmd_trending)

    describe_p = subparsers.add_parser("describe", help="Describe a book by ISBN13")
    describe_p.add_argument("--isbn", required=True)
    describe_p.add_argument("--json", action="store_true")
    describe_p.set_defaults(func=_cmd_describe)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
