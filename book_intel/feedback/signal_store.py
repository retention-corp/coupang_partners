"""Daily-aggregated (book, cluster) signal store.

The store owns one table, `book_signal_daily`, keyed on (isbn, cluster, date). Reads
return Wilson-safe CTR/CVR lower bounds that `book_reco.utils.learned_boost` can
consume; the schema deliberately keeps aggregate counters (not raw events) so the
analytics.events table stays the single source of truth and this table can be
rebuilt from it any time via `rollup.py`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from book_reco.utils import _wilson_lower_bound


@dataclass
class DailyRow:
    isbn: str
    cluster: str
    date: str  # ISO date string (YYYY-MM-DD)
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    post_reads: int = 0


class SignalStore:
    """SQLite-backed store for per-(book, cluster, day) signal aggregates."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path))
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as cx:
            cx.execute("PRAGMA journal_mode=WAL")
            cx.executescript(
                """
                CREATE TABLE IF NOT EXISTS book_signal_daily (
                    isbn        TEXT    NOT NULL,
                    cluster     TEXT    NOT NULL,
                    date        TEXT    NOT NULL,
                    impressions INTEGER NOT NULL DEFAULT 0,
                    clicks      INTEGER NOT NULL DEFAULT 0,
                    conversions INTEGER NOT NULL DEFAULT 0,
                    post_reads  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (isbn, cluster, date)
                );

                CREATE INDEX IF NOT EXISTS idx_signal_cluster_date
                    ON book_signal_daily (cluster, date DESC);

                CREATE INDEX IF NOT EXISTS idx_signal_isbn_cluster
                    ON book_signal_daily (isbn, cluster, date DESC);
                """
            )

    def upsert(self, row: DailyRow) -> None:
        with sqlite3.connect(self.db_path) as cx:
            cx.execute(
                """
                INSERT INTO book_signal_daily
                    (isbn, cluster, date, impressions, clicks, conversions, post_reads)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(isbn, cluster, date) DO UPDATE SET
                    impressions = excluded.impressions,
                    clicks      = excluded.clicks,
                    conversions = excluded.conversions,
                    post_reads  = excluded.post_reads
                """,
                (
                    row.isbn,
                    row.cluster,
                    row.date,
                    int(row.impressions),
                    int(row.clicks),
                    int(row.conversions),
                    int(row.post_reads),
                ),
            )

    def recent_rates(
        self,
        isbn: str,
        cluster: str,
        *,
        window_days: int = 28,
    ) -> tuple[float, float]:
        """Return (ctr_lb, cvr_lb) Wilson 95% lower bounds over the window.

        CTR = clicks / impressions. CVR = conversions / clicks. Both return 0.0 when
        the denominator is 0 or the book has no rows — `learned_boost` treats those
        as cold-start and contributes nothing.
        """

        if not isbn or not cluster:
            return 0.0, 0.0
        cutoff = (date.today() - timedelta(days=max(1, window_days))).isoformat()
        with sqlite3.connect(self.db_path) as cx:
            row = cx.execute(
                """
                SELECT COALESCE(SUM(impressions), 0),
                       COALESCE(SUM(clicks), 0),
                       COALESCE(SUM(conversions), 0)
                FROM book_signal_daily
                WHERE isbn = ? AND cluster = ? AND date >= ?
                """,
                (isbn, cluster, cutoff),
            ).fetchone()
        impressions, clicks, conversions = int(row[0]), int(row[1]), int(row[2])
        ctr_lb = _wilson_lower_bound(clicks, impressions) if impressions else 0.0
        cvr_lb = _wilson_lower_bound(conversions, clicks) if clicks else 0.0
        return ctr_lb, cvr_lb

    def category_demand_heatmap(self, *, window_days: int = 7) -> dict[str, float]:
        """Return {cluster: clicks_per_day} over the window, for orchestrator quota."""

        cutoff = (date.today() - timedelta(days=max(1, window_days))).isoformat()
        with sqlite3.connect(self.db_path) as cx:
            rows = cx.execute(
                """
                SELECT cluster,
                       COALESCE(SUM(impressions), 0),
                       COALESCE(SUM(clicks), 0)
                FROM book_signal_daily
                WHERE date >= ?
                GROUP BY cluster
                """,
                (cutoff,),
            ).fetchall()
        heatmap: dict[str, float] = {}
        for cluster, _impressions, clicks in rows:
            heatmap[cluster] = float(clicks or 0) / float(window_days)
        return heatmap

    def debug_dump(self) -> list[dict[str, Any]]:
        """Diagnostic: return all rows as dicts (for tests / ad-hoc inspection)."""

        with sqlite3.connect(self.db_path) as cx:
            rows = cx.execute(
                "SELECT isbn, cluster, date, impressions, clicks, conversions, post_reads "
                "FROM book_signal_daily ORDER BY date DESC, isbn"
            ).fetchall()
        return [
            {
                "isbn": r[0],
                "cluster": r[1],
                "date": r[2],
                "impressions": r[3],
                "clicks": r[4],
                "conversions": r[5],
                "post_reads": r[6],
            }
            for r in rows
        ]


__all__ = ["DailyRow", "SignalStore"]
