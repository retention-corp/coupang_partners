"""Profitability and unit-economics helpers for the shopping backend."""

from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_QUERY_VOLUMES = (1_000, 10_000, 100_000, 1_000_000)

# Spec D10 / T5 assumptions — 1.5% initial conversion, ₩30,000 average basket. These
# live at module scope so the weekly batch, admin endpoints, and tests all agree on
# the same defaults without digging into per-call env reads.
DEFAULT_CONVERSION_RATE = 0.015
DEFAULT_AVG_BASKET_KRW = 30_000

# KST is UTC+9 with no DST — fixed offset is fine; no need for zoneinfo.
KST = timezone(timedelta(hours=9))


def build_economics_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    total_queries = int(summary.get("total_queries") or 0)
    total_short_link_clicks = int(summary.get("total_short_link_clicks") or 0)
    event_breakdown = summary.get("event_breakdown") or []
    click_events = sum(
        int(item.get("count") or 0)
        for item in event_breakdown
        if item.get("event_type") == "deeplink_clicked"
    )

    observed_ctr = _safe_ratio(click_events, total_queries)
    assumptions = _cost_assumptions_from_env()
    scenarios = _payout_scenarios_from_env()
    category_overrides = _category_overrides_from_env()
    category_breakdown = summary.get("category_breakdown") or []
    has_meaningful_click_signal = total_queries >= 100 and click_events >= 10

    return {
        "funnel": {
            "total_queries": total_queries,
            "total_short_link_clicks": total_short_link_clicks,
            "deeplink_click_events": click_events,
            "observed_click_through_rate": float(observed_ctr) if click_events > 0 else None,
            "has_meaningful_click_signal": has_meaningful_click_signal,
            "short_link_clicks_are_attributed": False,
        },
        "cost_assumptions": assumptions,
        "scenarios": [
            _scenario_projection(
                name=name,
                scenario=scenario,
                total_queries=total_queries,
                observed_ctr=observed_ctr,
                allow_observed_ctr=has_meaningful_click_signal,
                assumptions=assumptions,
            )
            for name, scenario in scenarios.items()
        ],
        "category_scenarios": [
            _category_projection(
                category=item.get("category") or "",
                query_count=int(item.get("count") or 0),
                overrides=category_overrides.get(item.get("category") or "", {}),
                base_scenarios=scenarios,
                assumptions=assumptions,
            )
            for item in category_breakdown
            if item.get("category")
        ],
    }


def _scenario_projection(
    *,
    name: str,
    scenario: Mapping[str, Any],
    total_queries: int,
    observed_ctr: Decimal,
    allow_observed_ctr: bool,
    assumptions: Mapping[str, Any],
) -> Dict[str, Any]:
    click_through_rate = _selected_ctr(
        observed_ctr=observed_ctr,
        allow_observed_ctr=allow_observed_ctr,
        assumed_ctr=_decimal(scenario.get("assumed_ctr"), Decimal("0")),
    )
    aov_krw = _decimal(scenario.get("aov_krw"), Decimal("0"))
    commission_rate = _decimal(scenario.get("commission_rate"), Decimal("0"))
    click_to_purchase_rate = _decimal(scenario.get("click_to_purchase_rate"), Decimal("0"))
    revenue_per_query_krw = aov_krw * commission_rate * click_through_rate * click_to_purchase_rate
    infra_cost_per_query_krw = _infra_cost_per_query_krw(assumptions)
    contribution_margin_per_query_krw = revenue_per_query_krw - infra_cost_per_query_krw

    return {
        "name": name,
        "inputs": {
            "aov_krw": float(aov_krw),
            "commission_rate": float(commission_rate),
            "click_to_purchase_rate": float(click_to_purchase_rate),
            "click_through_rate": float(click_through_rate),
        },
        "per_query": {
            "estimated_revenue_krw": float(revenue_per_query_krw),
            "estimated_infra_cost_krw": float(infra_cost_per_query_krw),
            "estimated_contribution_margin_krw": float(contribution_margin_per_query_krw),
        },
        "query_volume_projection": [
            {
                "queries": volume,
                "estimated_revenue_krw": float(revenue_per_query_krw * volume),
                "estimated_infra_cost_krw": float(infra_cost_per_query_krw * volume),
                "estimated_contribution_margin_krw": float(contribution_margin_per_query_krw * volume),
            }
            for volume in DEFAULT_QUERY_VOLUMES
        ],
    }


def _category_projection(
    *,
    category: str,
    query_count: int,
    overrides: Mapping[str, Any],
    base_scenarios: Mapping[str, Mapping[str, Any]],
    assumptions: Mapping[str, Any],
) -> Dict[str, Any]:
    scenarios = {}
    for name, base in base_scenarios.items():
        merged = dict(base)
        merged.update((overrides.get(name) or {}))
        scenarios[name] = _scenario_projection(
            name=name,
            scenario=merged,
            total_queries=query_count,
            observed_ctr=Decimal("0"),
            allow_observed_ctr=False,
            assumptions=assumptions,
        )
    return {
        "category": category,
        "query_count": query_count,
        "uses_observed_ctr": False,
        "scenarios": list(scenarios.values()),
    }


def _cost_assumptions_from_env() -> Dict[str, Any]:
    return {
        "vcpu_count": float(_decimal(os.getenv("OPENCLAW_SHOPPING_VCPU_COUNT"), Decimal("1"))),
        "memory_gib": float(_decimal(os.getenv("OPENCLAW_SHOPPING_MEMORY_GIB"), Decimal("0.5"))),
        "avg_request_seconds": float(_decimal(os.getenv("OPENCLAW_SHOPPING_AVG_REQUEST_SECONDS"), Decimal("1.5"))),
        "usd_per_vcpu_second": float(_decimal(os.getenv("OPENCLAW_SHOPPING_USD_PER_VCPU_SECOND"), Decimal("0.000024"))),
        "usd_per_gib_second": float(_decimal(os.getenv("OPENCLAW_SHOPPING_USD_PER_GIB_SECOND"), Decimal("0.0000025"))),
        "usd_per_million_requests": float(_decimal(os.getenv("OPENCLAW_SHOPPING_USD_PER_MILLION_REQUESTS"), Decimal("0.40"))),
        "krw_per_usd": float(_decimal(os.getenv("OPENCLAW_SHOPPING_KRW_PER_USD"), Decimal("1350"))),
    }


def _payout_scenarios_from_env() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("OPENCLAW_SHOPPING_PAYOUT_SCENARIOS_JSON")
    if raw:
        parsed = json.loads(raw)
        return {str(name): dict(value) for name, value in parsed.items()}
    return {
        "bear": {
            "aov_krw": 30000,
            "commission_rate": 0.01,
            "assumed_ctr": 0.10,
            "click_to_purchase_rate": 0.015,
        },
        "base": {
            "aov_krw": 40000,
            "commission_rate": 0.02,
            "assumed_ctr": 0.15,
            "click_to_purchase_rate": 0.025,
        },
        "bull": {
            "aov_krw": 50000,
            "commission_rate": 0.03,
            "assumed_ctr": 0.20,
            "click_to_purchase_rate": 0.03,
        },
    }


def _category_overrides_from_env() -> Dict[str, Dict[str, Dict[str, Any]]]:
    raw = os.getenv("OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON")
    if not raw:
        return {}
    parsed = json.loads(raw)
    return {
        str(category): {str(name): dict(values) for name, values in (scenario_map or {}).items()}
        for category, scenario_map in parsed.items()
    }


def _selected_ctr(*, observed_ctr: Decimal, allow_observed_ctr: bool, assumed_ctr: Decimal) -> Decimal:
    if allow_observed_ctr and observed_ctr > 0:
        return observed_ctr
    return assumed_ctr


def _infra_cost_per_query_krw(assumptions: Mapping[str, Any]) -> Decimal:
    cpu_cost = (
        _decimal(assumptions["usd_per_vcpu_second"], Decimal("0"))
        * _decimal(assumptions["vcpu_count"], Decimal("0"))
        * _decimal(assumptions["avg_request_seconds"], Decimal("0"))
    )
    memory_cost = (
        _decimal(assumptions["usd_per_gib_second"], Decimal("0"))
        * _decimal(assumptions["memory_gib"], Decimal("0"))
        * _decimal(assumptions["avg_request_seconds"], Decimal("0"))
    )
    request_cost = _decimal(assumptions["usd_per_million_requests"], Decimal("0")) / Decimal("1000000")
    return (cpu_cost + memory_cost + request_cost) * _decimal(assumptions["krw_per_usd"], Decimal("0"))


def _safe_ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(str(numerator)) / Decimal(str(denominator))


def _decimal(value: Any, fallback: Decimal) -> Decimal:
    if value in (None, ""):
        return fallback
    return Decimal(str(value))


# --------------------------------------------------------------------------- #
# Shortlink proxy-GMV pipeline (spec acquihire-sprint-1mo T5, R3.3/R3.5-8)
# --------------------------------------------------------------------------- #
# These helpers run against the same sqlite database AnalyticsStore owns. They
# are intentionally read-only on the analytics side (dedup is a query-time op)
# and write into their own tables (proxy_gmv, click_reconciliation) so the
# underlying event stream stays untouched — a reconciliation re-run should
# always be able to re-derive effective_clicks from raw events.


def is_internal_ip(ip: Optional[str]) -> bool:
    """True when `ip` is loopback or link-local (169.254.0.0/16).

    Used by the weekly proxy-GMV batch to optionally exclude Google front-end
    proxy TCP peers that leaked into `client_ip` before T5 part 1 landed
    (`169.254.169.126` was the concrete 2026-04-21 incident). Loopback is
    included so local dev traffic can be filtered symmetrically. Anything
    unparseable is treated as external — conservative bias so we never silently
    drop real clicks when the IP column is dirty.
    """

    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    # is_link_local covers 169.254.0.0/16 for IPv4 and fe80::/10 for IPv6.
    return bool(addr.is_link_local)


def _parse_event_timestamp(raw: Any) -> Optional[datetime]:
    """Parse a stored `created_at` string into an aware datetime.

    AnalyticsStore writes ISO-8601 with a timezone suffix, but external loaders
    (tests, historical fixtures) occasionally drop the `Z`/`+00:00`. We assume
    UTC when a naive timestamp lands here — matches `_utc_now()` producer.
    """

    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_effective_clicks(
    rows: Sequence[Mapping[str, Any]],
    window_seconds: int = 10,
) -> Tuple[int, int]:
    """Return (raw_clicks, effective_clicks) after collapsing near-duplicates.

    A Coupang-equivalent effective click is defined as a click on a given
    `(client_ip, slug)` pair that is at least `window_seconds` away from the
    previous click in the same pair. The raw count mirrors the input length —
    callers pre-filter (e.g., by surface) before handing rows off.

    Rows may be dicts or any mapping; we read `timestamp`, `slug`, `client_ip`.
    Missing `client_ip` is bucketed under the literal empty string so rows still
    get grouped together (matches the 2026-04-21 incident where every redirect
    came from the same Google proxy and Coupang still deduped them).
    """

    raw_count = len(rows)
    if raw_count == 0:
        return 0, 0

    window = max(int(window_seconds), 0)

    # Stable sort by (client_ip, slug, timestamp). We extract the timestamp
    # once so the comparator does not re-parse per swap.
    normalized: List[Tuple[str, str, Optional[datetime]]] = []
    for row in rows:
        client_ip = (row.get("client_ip") or "").strip()
        slug = (row.get("slug") or "").strip()
        ts = _parse_event_timestamp(row.get("timestamp"))
        normalized.append((client_ip, slug, ts))

    normalized.sort(
        key=lambda item: (item[0], item[1], item[2] or datetime.min.replace(tzinfo=timezone.utc))
    )

    effective = 0
    last_key: Optional[Tuple[str, str]] = None
    last_ts: Optional[datetime] = None
    for client_ip, slug, ts in normalized:
        key = (client_ip, slug)
        if last_key != key or ts is None or last_ts is None:
            effective += 1
            last_key = key
            last_ts = ts
            continue
        # Rows missing a timestamp can't be deduped — fall through already handled.
        gap = (ts - last_ts).total_seconds()
        if gap >= window:
            effective += 1
            last_ts = ts
        # else: collapse into the previous effective click; keep last_ts as-is
        # so a string of rapid clicks all fold into one.
    return raw_count, effective


def _load_shortlink_events(
    db_path: str,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, Any]]:
    """Return shortlink_redirect events in [start_utc, end_utc).

    Reads directly from AnalyticsStore's sqlite backing file. We go through
    a fresh connection rather than sharing the writer's — keeps this module
    dependency-light (no AnalyticsStore import) and SQLite's MVCC is fine for
    read-while-write.
    """

    path = Path(db_path)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with sqlite3.connect(str(path)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            "SELECT created_at, metadata_json, client_ip, user_agent, proxy_ip, "
            "surface, surface_raw, client_id, client_version, utm_source "
            "FROM events "
            "WHERE event_type = 'shortlink_redirect' "
            "AND created_at >= ? AND created_at < ? "
            "ORDER BY created_at ASC",
            (start_utc.isoformat(), end_utc.isoformat()),
        )
        for row in cursor.fetchall():
            metadata: Dict[str, Any] = {}
            if row["metadata_json"]:
                try:
                    metadata = json.loads(row["metadata_json"])
                except (TypeError, ValueError):
                    metadata = {}
            rows.append(
                {
                    "timestamp": row["created_at"],
                    "slug": metadata.get("slug", ""),
                    "client_ip": row["client_ip"] or "",
                    "user_agent": row["user_agent"] or "",
                    "proxy_ip": row["proxy_ip"] or "",
                    "surface": row["surface"] or "unknown",
                    "surface_raw": row["surface_raw"],
                    "client_id": row["client_id"],
                    "client_version": row["client_version"],
                    "utm_source": row["utm_source"],
                }
            )
    return rows


def _week_start_kst_to_utc_range(week_start_kst: datetime) -> Tuple[datetime, datetime]:
    """Map a KST week-start datetime to the half-open UTC range [start, start+7d)."""

    if week_start_kst.tzinfo is None:
        week_start_kst = week_start_kst.replace(tzinfo=KST)
    start_utc = week_start_kst.astimezone(timezone.utc)
    end_utc = (week_start_kst + timedelta(days=7)).astimezone(timezone.utc)
    return start_utc, end_utc


def _day_kst_to_utc_range(date_kst: date) -> Tuple[datetime, datetime]:
    day_start_kst = datetime(date_kst.year, date_kst.month, date_kst.day, tzinfo=KST)
    start_utc = day_start_kst.astimezone(timezone.utc)
    end_utc = (day_start_kst + timedelta(days=1)).astimezone(timezone.utc)
    return start_utc, end_utc


def _ensure_proxy_gmv_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS proxy_gmv (
            id TEXT PRIMARY KEY,
            surface TEXT NOT NULL,
            week_start TEXT NOT NULL,
            raw_clicks INTEGER NOT NULL,
            effective_clicks INTEGER NOT NULL,
            estimated_basket_krw INTEGER NOT NULL,
            proxy_gmv_krw INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT 'attributed_estimate',
            created_at TEXT NOT NULL,
            UNIQUE (surface, week_start)
        )
        """
    )


def _ensure_click_reconciliation_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS click_reconciliation (
            date_kst TEXT PRIMARY KEY,
            our_raw_clicks INTEGER NOT NULL,
            our_effective_clicks INTEGER NOT NULL,
            coupang_reported_clicks INTEGER,
            gap_ratio REAL,
            created_at TEXT NOT NULL
        )
        """
    )


def compute_weekly_proxy_gmv(
    week_start_kst: datetime,
    avg_basket_krw: int = DEFAULT_AVG_BASKET_KRW,
    conv_rate: float = DEFAULT_CONVERSION_RATE,
    *,
    db_path: str,
    exclude_internal_ips: bool = False,
) -> List[Dict[str, Any]]:
    """Aggregate shortlink redirects into per-surface proxy GMV for the week.

    Upserts the result into the `proxy_gmv` table and returns the rows. The
    label is hard-coded to `attributed_estimate` per spec D10 so the pitch
    dashboard can always render Source-A vs Source-B separately even when a
    future code path wants to attach a confidence score.
    """

    start_utc, end_utc = _week_start_kst_to_utc_range(week_start_kst)
    events = _load_shortlink_events(db_path, start_utc=start_utc, end_utc=end_utc)
    if exclude_internal_ips:
        events = [event for event in events if not is_internal_ip(event.get("client_ip"))]

    by_surface: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        surface = (event.get("surface") or "unknown").strip() or "unknown"
        by_surface.setdefault(surface, []).append(event)

    week_start_iso = week_start_kst.date().isoformat()
    created_at = datetime.now(timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        _ensure_proxy_gmv_table(connection)
        for surface, surface_events in by_surface.items():
            raw_clicks, effective_clicks = compute_effective_clicks(surface_events)
            proxy_gmv_krw = int(round(effective_clicks * avg_basket_krw * conv_rate))
            row = {
                "surface": surface,
                "week_start": week_start_iso,
                "raw_clicks": raw_clicks,
                "effective_clicks": effective_clicks,
                "estimated_basket_krw": int(avg_basket_krw),
                "proxy_gmv_krw": proxy_gmv_krw,
                "label": "attributed_estimate",
                "created_at": created_at,
            }
            connection.execute(
                """
                INSERT INTO proxy_gmv (
                    id, surface, week_start, raw_clicks, effective_clicks,
                    estimated_basket_krw, proxy_gmv_krw, label, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(surface, week_start) DO UPDATE SET
                    raw_clicks = excluded.raw_clicks,
                    effective_clicks = excluded.effective_clicks,
                    estimated_basket_krw = excluded.estimated_basket_krw,
                    proxy_gmv_krw = excluded.proxy_gmv_krw,
                    label = excluded.label,
                    created_at = excluded.created_at
                """,
                (
                    str(uuid.uuid4()),
                    surface,
                    week_start_iso,
                    raw_clicks,
                    effective_clicks,
                    int(avg_basket_krw),
                    proxy_gmv_krw,
                    "attributed_estimate",
                    created_at,
                ),
            )
            rows.append(row)
    return rows


def _fetch_coupang_reported_clicks(db_path: str, date_iso: str) -> Optional[int]:
    """Best-effort lookup against T4's `economics` table.

    T4 has not landed yet in T5's branch — so the table may not exist. We treat
    every failure mode (missing table, missing column, missing row) the same:
    return None, which the reconciliation row stores as NULL and surfaces to the
    admin endpoint as `null`. When T4 ships with its own schema, swap the query
    shape here; the reconciliation contract stays stable.
    """

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
            date_col = next(
                (col for col in ("date_kst", "report_date", "date", "created_at") if col in columns),
                None,
            )
            click_col = next(
                (col for col in ("coupang_clicks", "reported_clicks", "clicks") if col in columns),
                None,
            )
            if not date_col or not click_col:
                return None
            row = connection.execute(
                f"SELECT {click_col} FROM economics WHERE {date_col} = ? LIMIT 1",
                (date_iso,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])
    except sqlite3.Error:
        return None


def compute_daily_click_reconciliation(
    date_kst: date,
    *,
    db_path: str,
    analytics_store: Any = None,
) -> Dict[str, Any]:
    """Produce one reconciliation row comparing our clicks to Coupang's report.

    `analytics_store` is only used to emit the `click_reconciliation_warning`
    event when gap > 50% — the computation itself is pure read-then-upsert so
    the job stays idempotent. Passing `None` silently skips the warning emit
    (useful for tests that don't want a sibling AnalyticsStore instance).
    """

    start_utc, end_utc = _day_kst_to_utc_range(date_kst)
    events = _load_shortlink_events(db_path, start_utc=start_utc, end_utc=end_utc)
    our_raw, our_effective = compute_effective_clicks(events)

    date_iso = date_kst.isoformat()
    coupang_reported = _fetch_coupang_reported_clicks(db_path, date_iso)

    gap_ratio: Optional[float]
    if coupang_reported is None or our_effective <= 0:
        gap_ratio = None
    else:
        gap_ratio = 1.0 - (float(coupang_reported) / float(our_effective))

    created_at = datetime.now(timezone.utc).isoformat()
    result = {
        "date_kst": date_iso,
        "our_raw_clicks": our_raw,
        "our_effective_clicks": our_effective,
        "coupang_reported_clicks": coupang_reported,
        "gap_ratio": gap_ratio,
        "created_at": created_at,
    }

    with sqlite3.connect(db_path) as connection:
        _ensure_click_reconciliation_table(connection)
        connection.execute(
            """
            INSERT INTO click_reconciliation (
                date_kst, our_raw_clicks, our_effective_clicks,
                coupang_reported_clicks, gap_ratio, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_kst) DO UPDATE SET
                our_raw_clicks = excluded.our_raw_clicks,
                our_effective_clicks = excluded.our_effective_clicks,
                coupang_reported_clicks = excluded.coupang_reported_clicks,
                gap_ratio = excluded.gap_ratio,
                created_at = excluded.created_at
            """,
            (
                date_iso,
                our_raw,
                our_effective,
                coupang_reported,
                gap_ratio,
                created_at,
            ),
        )

    # Warn only when gap is meaningfully large AND we actually have a Coupang
    # baseline — None gap_ratio means "can't compare yet", which should not
    # trigger the warning channel.
    if gap_ratio is not None and gap_ratio > 0.5 and analytics_store is not None:
        try:
            analytics_store.record_event(
                event_type="click_reconciliation_warning",
                metadata={
                    "date_kst": date_iso,
                    "our_raw_clicks": our_raw,
                    "our_effective_clicks": our_effective,
                    "coupang_reported_clicks": coupang_reported,
                    "gap_ratio": gap_ratio,
                },
            )
        except Exception:
            # Never let analytics emission fail the reconciliation row write.
            pass

    return result


def read_proxy_gmv_rows(
    db_path: str,
    *,
    week_start: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read proxy_gmv rows for admin endpoint consumption.

    Returns the rows for `week_start` (YYYY-MM-DD) when provided, otherwise the
    most recent week present in the table. Empty list when the table does not
    exist yet — keeps the admin endpoint cheap on a fresh DB.
    """

    path = Path(db_path)
    if not path.exists():
        return []
    with sqlite3.connect(str(path)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "proxy_gmv" not in tables:
            return []
        if week_start is None:
            latest = connection.execute(
                "SELECT week_start FROM proxy_gmv ORDER BY week_start DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return []
            week_start = latest["week_start"]
        cursor = connection.execute(
            "SELECT surface, week_start, raw_clicks, effective_clicks, "
            "estimated_basket_krw, proxy_gmv_krw, label, created_at "
            "FROM proxy_gmv WHERE week_start = ? "
            "ORDER BY proxy_gmv_krw DESC, surface ASC",
            (week_start,),
        )
        return [dict(row) for row in cursor.fetchall()]


def read_click_reconciliation_row(
    db_path: str,
    *,
    date_kst: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single click_reconciliation row by date, newest if omitted."""

    path = Path(db_path)
    if not path.exists():
        return None
    with sqlite3.connect(str(path)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "click_reconciliation" not in tables:
            return None
        if date_kst is None:
            row = connection.execute(
                "SELECT date_kst, our_raw_clicks, our_effective_clicks, "
                "coupang_reported_clicks, gap_ratio, created_at "
                "FROM click_reconciliation ORDER BY date_kst DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT date_kst, our_raw_clicks, our_effective_clicks, "
                "coupang_reported_clicks, gap_ratio, created_at "
                "FROM click_reconciliation WHERE date_kst = ?",
                (date_kst,),
            ).fetchone()
    return dict(row) if row else None
