"""Profitability and unit-economics helpers for the shopping backend."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

DEFAULT_QUERY_VOLUMES = (1_000, 10_000, 100_000, 1_000_000)


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
