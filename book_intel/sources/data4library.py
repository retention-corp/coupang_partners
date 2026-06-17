"""Data4Library (정보나루) Open API client.

Public-library loan statistics and recommendations from the Korea National
Library. Useful signal: `loanItemSrch` (popularity by loan counts) and
`recommandList` (similar-book graph anchored on ISBN13). Both endpoints are
JSON when `format=json` is passed.

Doc: https://www.data4library.kr/openDataV

Fail-soft on missing key or network error: every public method returns an
empty list rather than raising, so the orchestrator can still compose a post
from the other sources.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from book_reco.utils import http_get_json

LOGGER = logging.getLogger("book_intel.sources.data4library")

_API_ROOT = "http://data4library.kr/api"


class Data4LibraryClient:
    """Thin read-only client for the two endpoints book_intel actually needs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        user_agent: str = "book_intel/0.1 (+https://retn.kr)",
    ) -> None:
        self.api_key = (api_key or os.getenv("DATA4LIBRARY_API_KEY") or "").strip()
        self.timeout = float(timeout_seconds)
        self.user_agent = user_agent

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # --- public API -------------------------------------------------------

    def loan_top(
        self,
        *,
        start_dt: str | None = None,
        end_dt: str | None = None,
        age_group: str = "",
        gender: str = "",
        category_code: str = "",
        page_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Top-loaned books over a time window. Returns normalized dicts."""

        if not self.configured:
            return []
        payload = self._get(
            "/loanItemSrch",
            {
                "startDt": start_dt or "",
                "endDt": end_dt or "",
                "age": age_group,
                "gender": gender,
                "kdc": category_code,
                "pageNo": 1,
                "pageSize": min(max(page_size, 1), 100),
            },
        )
        return [_normalize_loan_doc(d) for d in _extract_docs(payload)]

    def similar_books(self, isbn13: str, *, page_size: int = 10) -> list[dict[str, Any]]:
        """Graph neighbours for this ISBN (public-library borrowers' concurrent loans)."""

        isbn13 = (isbn13 or "").strip()
        if not isbn13 or not self.configured:
            return []
        payload = self._get(
            "/recommandList",
            {
                "isbn13": isbn13,
                "pageNo": 1,
                "pageSize": min(max(page_size, 1), 100),
            },
        )
        return [_normalize_recommend_doc(d) for d in _extract_docs(payload)]

    def book_exists(self, isbn13: str) -> dict[str, Any] | None:
        """Confirm the book exists in D4L and return its metadata (or None)."""

        isbn13 = (isbn13 or "").strip()
        if not isbn13 or not self.configured:
            return None
        payload = self._get("/bookExist", {"isbn13": isbn13})
        response = (payload or {}).get("response") if isinstance(payload, dict) else None
        result = (response or {}).get("result") if isinstance(response, dict) else None
        if not result:
            return None
        return result if isinstance(result, dict) else None

    # --- internals --------------------------------------------------------

    def _get(self, path: str, extra: dict[str, Any]) -> Any:
        params = {
            "authKey": self.api_key,
            "format": "json",
            **{k: v for k, v in extra.items() if v not in (None, "")},
        }
        try:
            return http_get_json(
                f"{_API_ROOT}{path}",
                params=params,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:
            LOGGER.warning("data4library %s request failed: %s", path, exc)
            return {}


def _extract_docs(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response") or payload
    if not isinstance(response, dict):
        return []
    docs = response.get("docs")
    if not isinstance(docs, list):
        return []
    out: list[dict[str, Any]] = []
    for wrapper in docs:
        if isinstance(wrapper, dict) and isinstance(wrapper.get("doc"), dict):
            out.append(wrapper["doc"])
        elif isinstance(wrapper, dict):
            out.append(wrapper)
    return out


def _normalize_loan_doc(doc: dict[str, Any]) -> dict[str, Any]:
    book = doc if "bookname" in doc else doc.get("book", doc)
    return {
        "title": (book.get("bookname") or "").strip(),
        "author": (book.get("authors") or "").strip(),
        "publisher": (book.get("publisher") or "").strip(),
        "isbn13": (book.get("isbn13") or "").strip(),
        "class_nm": (book.get("class_nm") or "").strip(),
        "loan_count": _to_int(book.get("loan_count")),
        "ranking": _to_int(book.get("ranking") or book.get("no")),
        "publication_year": (book.get("publication_year") or "").strip(),
        "bookImageURL": (book.get("bookImageURL") or "").strip(),
    }


def _normalize_recommend_doc(doc: dict[str, Any]) -> dict[str, Any]:
    book = doc if "bookname" in doc else doc.get("book", doc)
    return {
        "title": (book.get("bookname") or "").strip(),
        "author": (book.get("authors") or "").strip(),
        "isbn13": (book.get("isbn13") or "").strip(),
        "publisher": (book.get("publisher") or "").strip(),
        "class_nm": (book.get("class_nm") or "").strip(),
    }


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["Data4LibraryClient"]
