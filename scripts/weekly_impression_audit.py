#!/usr/bin/env python3
"""Weekly impression-fire-rate audit (P2.2).

Compares our recommendation count (from the local sqlite analytics DB) against
the Coupang Partners dashboard impression count for the same KST week. Emits
a ratio and flags it when below the configured threshold (default 0.8) so the
operator can investigate whether legitimately-served recommendations are
failing to register as approved impressions on Coupang's side.

Coupang Partners has no public API for impression counts. The expected flow:
1. Operator pulls the weekly impression number from the Partners dashboard.
2. Either pass it in via `--coupang-impressions N`, or insert a row into the
   sqlite `economics` table with columns `(week_start, reported_impressions)`
   so future runs can read it without manual entry.

Output: single JSON line on stdout. Exit code 0 always so a weekly cron does
not fail-loud on a low ratio — the flag field carries the signal.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Allow running both as `python3 scripts/weekly_impression_audit.py` and via
# `python3 -m scripts.weekly_impression_audit` without forcing PYTHONPATH on
# the operator.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

KST = timezone(timedelta(hours=9))
DEFAULT_THRESHOLD = 0.8


def _previous_monday_kst(today_kst: date) -> date:
    """Return the Monday of the most-recently-completed KST week."""

    # weekday(): Monday=0 ... Sunday=6. Subtract weekday()+7 so the result is
    # always a Monday at least one week before today (the *completed* week).
    days_since_last_monday = today_kst.weekday() + 7
    return today_kst - timedelta(days=days_since_last_monday)


def _week_window_utc(week_start_kst: date) -> tuple[datetime, datetime]:
    start_kst = datetime(week_start_kst.year, week_start_kst.month, week_start_kst.day, tzinfo=KST)
    end_kst = start_kst + timedelta(days=7)
    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


def count_recommendations_in_window(
    db_path: str,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> int:
    """Count rows in the `recommendations` table whose created_at falls inside
    [start_utc, end_utc). Returns 0 when the DB or table is missing — so a
    fresh deploy emits a usable ratio of None instead of crashing."""

    path = Path(db_path)
    if not path.exists():
        return 0
    with sqlite3.connect(str(path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "recommendations" not in tables:
            return 0
        row = connection.execute(
            "SELECT COUNT(*) FROM recommendations WHERE created_at >= ? AND created_at < ?",
            (start_utc.isoformat(), end_utc.isoformat()),
        ).fetchone()
    return int(row[0] or 0)


def fetch_reported_impressions(db_path: str, week_start_iso: str) -> Optional[int]:
    """Best-effort lookup against an `economics` table holding manually-entered
    Coupang Partners dashboard numbers. Mirrors the lenient column probing used
    by `economics._fetch_coupang_reported_clicks` so this script can survive
    schema drift without code changes — operators can use whichever column
    name their tooling already produces."""

    path = Path(db_path)
    if not path.exists():
        return None
    try:
        with sqlite3.connect(str(path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "economics" not in tables:
                return None
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(economics)").fetchall()
            }
            week_col = next(
                (col for col in ("week_start", "week_start_kst", "report_week", "date_kst") if col in columns),
                None,
            )
            impression_col = next(
                (col for col in ("coupang_impressions", "reported_impressions", "impressions") if col in columns),
                None,
            )
            if not week_col or not impression_col:
                return None
            row = connection.execute(
                f"SELECT {impression_col} FROM economics WHERE {week_col} = ? LIMIT 1",
                (week_start_iso,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])
    except sqlite3.Error:
        return None


def compute_ratio(coupang_impressions: Optional[int], our_recommendations: int) -> Optional[float]:
    if coupang_impressions is None or our_recommendations <= 0:
        return None
    return float(coupang_impressions) / float(our_recommendations)


def run_audit(
    *,
    db_path: str,
    week_start_kst: date,
    coupang_impressions_override: Optional[int] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    start_utc, end_utc = _week_window_utc(week_start_kst)
    our_recommendations = count_recommendations_in_window(
        db_path, start_utc=start_utc, end_utc=end_utc
    )
    week_start_iso = week_start_kst.isoformat()
    if coupang_impressions_override is not None:
        coupang_impressions = int(coupang_impressions_override)
    else:
        coupang_impressions = fetch_reported_impressions(db_path, week_start_iso)
    ratio = compute_ratio(coupang_impressions, our_recommendations)
    flag = ratio is not None and ratio < threshold
    return {
        "week_start_kst": week_start_iso,
        "window_utc": {
            "start": start_utc.isoformat(),
            "end": end_utc.isoformat(),
        },
        "our_recommendations": our_recommendations,
        "coupang_impressions": coupang_impressions,
        "ratio": ratio,
        "threshold": threshold,
        "flag": flag,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--db",
        default=os.getenv("OPENCLAW_SHOPPING_DB_PATH", ".data/openclaw-shopping.sqlite3"),
        help="Path to the sqlite analytics DB (default: $OPENCLAW_SHOPPING_DB_PATH or .data/openclaw-shopping.sqlite3).",
    )
    parser.add_argument(
        "--week-start",
        default=None,
        help="KST Monday (YYYY-MM-DD) of the week to audit. Default: previous completed week.",
    )
    parser.add_argument(
        "--coupang-impressions",
        type=int,
        default=None,
        help="Override the dashboard impression count instead of reading the economics table.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Flag the ratio when below this value (default: {DEFAULT_THRESHOLD}).",
    )
    return parser


def _parse_week_start(value: Optional[str]) -> date:
    if value is None or not value.strip():
        return _previous_monday_kst(datetime.now(KST).date())
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    if parsed.weekday() != 0:
        raise SystemExit(f"--week-start must be a Monday (got {value} = weekday {parsed.weekday()})")
    return parsed


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    week_start = _parse_week_start(args.week_start)
    result = run_audit(
        db_path=args.db,
        week_start_kst=week_start,
        coupang_impressions_override=args.coupang_impressions,
        threshold=args.threshold,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
