"""Daily rollup: analytics.events → book_signal_daily.

Idempotent — re-running the same date re-computes aggregates and upserts. The join
between `book_click` (which only knows slug) and `book_impression` (which knows isbn
+ cluster + slug) happens in Python: for every click, we find the most recent prior
impression with the same slug and inherit its isbn + cluster. That keeps the hot
redirect path free of DB reads while still producing per-(isbn, cluster) counters.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from .events import (
    BOOK_CLICK,
    BOOK_CONVERSION,
    BOOK_IMPRESSION,
    POST_READ,
)
from .signal_store import DailyRow, SignalStore


def rollup_day(
    analytics_db_path: str,
    signal_store: SignalStore,
    target_date: date | None = None,
    *,
    slug_lookback_days: int = 30,
) -> int:
    """Aggregate one day's events into book_signal_daily. Returns rows written."""

    if target_date is None:
        target_date = date.today() - timedelta(days=1)
    date_str = target_date.isoformat()
    day_start = datetime.combine(target_date, datetime.min.time()).isoformat()
    day_end = (datetime.combine(target_date, datetime.min.time()) + timedelta(days=1)).isoformat()

    # Pre-seed slug → (isbn, cluster) from impressions in a wider window so clicks
    # without an impression on the same day still resolve correctly (mobile users
    # who click the next day after reading the newsletter, etc.).
    slug_start = (datetime.combine(target_date, datetime.min.time()) - timedelta(days=slug_lookback_days)).isoformat()
    slug_map: dict[str, tuple[str, str]] = {}
    with sqlite3.connect(analytics_db_path) as cx:
        impression_rows = cx.execute(
            """
            SELECT metadata_json, created_at FROM events
            WHERE event_type = ?
              AND created_at >= ?
              AND created_at < ?
            ORDER BY created_at ASC
            """,
            (BOOK_IMPRESSION, slug_start, day_end),
        ).fetchall()
        day_rows = cx.execute(
            """
            SELECT event_type, metadata_json FROM events
            WHERE created_at >= ? AND created_at < ?
            """,
            (day_start, day_end),
        ).fetchall()

    for meta_json, _created in impression_rows:
        meta = _parse(meta_json)
        slug = _clean(meta.get("slug"))
        isbn = _clean(meta.get("isbn13") or meta.get("isbn"))
        cluster = _clean(meta.get("persona_cluster") or meta.get("cluster")) or "other"
        if slug and isbn:
            slug_map[slug] = (isbn, cluster)

    agg: dict[tuple[str, str], dict[str, int]] = {}

    def _bump(isbn: str, cluster: str, field: str) -> None:
        key = (isbn, cluster)
        bucket = agg.setdefault(
            key,
            {"impressions": 0, "clicks": 0, "conversions": 0, "post_reads": 0},
        )
        bucket[field] += 1

    for event_type, meta_json in day_rows:
        meta = _parse(meta_json)
        if event_type == BOOK_IMPRESSION:
            isbn = _clean(meta.get("isbn13") or meta.get("isbn"))
            cluster = _clean(meta.get("persona_cluster") or meta.get("cluster")) or "other"
            if isbn:
                _bump(isbn, cluster, "impressions")
        elif event_type == BOOK_CLICK:
            isbn = _clean(meta.get("isbn13") or meta.get("isbn"))
            cluster = _clean(meta.get("persona_cluster") or meta.get("cluster"))
            if not (isbn and cluster):
                slug = _clean(meta.get("slug"))
                if slug and slug in slug_map:
                    isbn, cluster = slug_map[slug]
            if isbn:
                _bump(isbn, cluster or "other", "clicks")
        elif event_type == BOOK_CONVERSION:
            isbn = _clean(meta.get("isbn13") or meta.get("isbn"))
            cluster = _clean(meta.get("persona_cluster") or meta.get("cluster")) or "other"
            if isbn:
                _bump(isbn, cluster, "conversions")
        elif event_type == POST_READ:
            isbn = _clean(meta.get("isbn13") or meta.get("isbn"))
            cluster = _clean(meta.get("persona_cluster") or meta.get("cluster")) or "other"
            if isbn:
                _bump(isbn, cluster, "post_reads")

    for (isbn, cluster), counts in agg.items():
        signal_store.upsert(
            DailyRow(
                isbn=isbn,
                cluster=cluster,
                date=date_str,
                impressions=counts["impressions"],
                clicks=counts["clicks"],
                conversions=counts["conversions"],
                post_reads=counts["post_reads"],
            )
        )
    return len(agg)


def _parse(meta_json: Any) -> dict[str, Any]:
    if not meta_json:
        return {}
    if isinstance(meta_json, dict):
        return meta_json
    try:
        return json.loads(meta_json)
    except Exception:
        return {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


__all__ = ["rollup_day"]
