"""Top-level content pipeline: pick books → enrich → compose → publish.

Data flow:

    1. Read the 7-day category_demand_heatmap from SignalStore.
    2. Allocate a post quota per persona cluster: A-tier × 1.5 multiplier,
       every cluster has a floor of 1 post / 7 days so non-target
       categories never go dark.
    3. For each quota slot, call book_assist with a cluster-appropriate
       persona to pick one Coupang-matched book.
    4. gather_book_intel collects enrichment (Aladin / D4L / Naver /
       Coupang reviews / YouTube) around that book, cached 7 days.
    5. compose_post runs the raw material through an OpenClaw agent
       turn with the cluster's tier prompt.
    6. The Ghost publisher creates a DRAFT post (--live flips status
       to "published"); a post_publish event is emitted so the
       feedback loop can later attribute blog traffic to the prompt
       variant.

CLI:

    python3 -m book_intel.orchestrator --dry-run --limit 3
    python3 -m book_intel.orchestrator --live --limit 3

Dry-run skips Ghost POST (uses preview_post) and prints the composed
posts to stdout so the operator can manually inspect before going live.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from book_intel.cache import EnrichmentCache
from book_intel.composer import CompositionError, compose_post
from book_intel.feedback import POST_PUBLISH, emit
from book_intel.feedback.signal_store import SignalStore
from book_intel.publisher import GhostAdminClient, GhostAuthError, GhostPublishError
from book_intel.sources import gather_book_intel
from book_reco.backend_integration import book_assist
from book_reco.persona import A_TIER_CLUSTERS, PersonaProfile

LOGGER = logging.getLogger("book_intel.orchestrator")

# Hand-coded persona seeds per cluster. The orchestrator uses these to nudge
# book_assist toward cluster-relevant picks even before the feedback loop has
# data. Fields follow the PersonaProfile "persona" payload schema.
CLUSTER_PERSONAS: dict[str, dict[str, Any]] = {
    "openclaw_engineer": {
        "interests": ["엔지니어링", "백엔드", "아키텍처"],
        "categories": ["자연과학", "자기계발"],
        "engineering_weight": 1.0,
    },
    "openclaw_operator": {
        "interests": ["솔로 오퍼레이터", "수익화", "스타트업", "프로덕트"],
        "categories": ["자기계발", "자연과학"],
        "engineering_weight": 0.5,
    },
    "general_self_dev": {
        "interests": ["자기계발", "경영", "재무"],
        "categories": ["자기계발"],
    },
    "literature": {
        "interests": ["문학"],
        "categories": ["한국소설", "어문학"],
        "korean_preference": 0.8,
    },
    "parent_lifestyle": {
        "interests": ["육아", "교육"],
        "categories": ["육아"],
    },
}

DEFAULT_QUOTA_ORDER = [
    "openclaw_engineer",
    "openclaw_operator",
    "general_self_dev",
    "literature",
    "parent_lifestyle",
]


@dataclass
class OrchestratorConfig:
    limit: int = 3
    dry_run: bool = True
    cache_db_path: str = ".data/book-intel-cache.sqlite3"
    signal_db_path: str = ".data/openclaw-shopping.sqlite3"
    publisher_status: str = "draft"  # "draft" for live review queue, "published" for immediate
    window_days: int = 7
    a_tier_multiplier: float = 1.5
    floor_per_cluster: float = 1.0 / 7.0  # one post per 7 days minimum
    coupang_search_fn: Optional[Callable[..., Any]] = None
    shorten_fn: Optional[Callable[[str], str]] = None
    analytics_store: Any = None
    ghost_client: Optional[GhostAdminClient] = None


def run(config: OrchestratorConfig) -> list[dict[str, Any]]:
    """Execute one orchestrator pass. Returns a list of per-slot result dicts."""

    signal_store = _load_signal_store(config)
    quota = pick_next_posts(
        signal_store=signal_store,
        limit=config.limit,
        window_days=config.window_days,
        a_tier_multiplier=config.a_tier_multiplier,
        floor_per_cluster=config.floor_per_cluster,
    )
    LOGGER.info("orchestrator quota: %s", quota)

    cache = EnrichmentCache(config.cache_db_path)
    ghost = config.ghost_client if not config.dry_run else None
    if not config.dry_run and ghost is None:
        try:
            ghost = GhostAdminClient()
        except GhostAuthError as exc:
            raise RuntimeError(f"Ghost auth missing for --live: {exc}")

    results: list[dict[str, Any]] = []
    for cluster, count in quota.items():
        for _ in range(count):
            slot = _run_slot(
                cluster=cluster,
                config=config,
                signal_store=signal_store,
                cache=cache,
                ghost=ghost,
            )
            results.append(slot)
    return results


def pick_next_posts(
    *,
    signal_store: SignalStore | None,
    limit: int,
    window_days: int = 7,
    a_tier_multiplier: float = 1.5,
    floor_per_cluster: float = 1.0 / 7.0,
) -> dict[str, int]:
    """Return {cluster: count} summing to `limit`. Honors A-tier multiplier + floor."""

    limit = max(1, int(limit))
    heatmap = signal_store.category_demand_heatmap(window_days=window_days) if signal_store else {}
    # Compute raw weight per cluster (demand) with A-tier multiplier applied.
    raw: dict[str, float] = {}
    for cluster in DEFAULT_QUOTA_ORDER:
        demand = float(heatmap.get(cluster, 0.0))
        multiplier = a_tier_multiplier if cluster in A_TIER_CLUSTERS else 1.0
        # Floor ensures cold-start allocations never go to zero.
        raw[cluster] = max(demand * multiplier, floor_per_cluster)

    total_weight = sum(raw.values()) or 1.0
    # Proportional allocation rounded down, then distribute remainder by largest fractional part.
    ideal: dict[str, float] = {c: (w / total_weight) * limit for c, w in raw.items()}
    assigned: dict[str, int] = {c: int(v) for c, v in ideal.items()}
    remainder = limit - sum(assigned.values())
    if remainder > 0:
        fractional = sorted(
            ((c, ideal[c] - assigned[c]) for c in raw),
            key=lambda pair: (-pair[1], DEFAULT_QUOTA_ORDER.index(pair[0])),
        )
        for cluster, _frac in fractional[:remainder]:
            assigned[cluster] += 1

    # Trim clusters that got zero assignments for brevity.
    return {cluster: count for cluster, count in assigned.items() if count > 0}


def _run_slot(
    *,
    cluster: str,
    config: OrchestratorConfig,
    signal_store: SignalStore | None,
    cache: EnrichmentCache,
    ghost: Optional[GhostAdminClient],
) -> dict[str, Any]:
    tier = "A" if cluster in A_TIER_CLUSTERS else "B"
    persona_dict = CLUSTER_PERSONAS.get(cluster, CLUSTER_PERSONAS["general_self_dev"])
    slot: dict[str, Any] = {"cluster": cluster, "tier": tier, "persona": persona_dict}

    payload = {"vertical": "book", "limit": 1, "persona": persona_dict}
    try:
        assist_response = book_assist(
            payload,
            search_products_fn=config.coupang_search_fn or _missing_coupang,
            shorten_fn=config.shorten_fn,
            analytics_store=config.analytics_store,
            signal_store=signal_store,
            client_id=f"orchestrator:{cluster}",
        )
    except Exception as exc:
        LOGGER.warning("book_assist failed for cluster %s: %s", cluster, exc)
        slot["status"] = "book_assist_failed"
        slot["error"] = str(exc)
        return slot

    recs = assist_response.get("recommendations") or []
    if not recs:
        slot["status"] = "no_match"
        return slot

    rec = recs[0]
    book = rec.get("book") or {}
    product = rec.get("product") or {}
    slot["book"] = {
        "title": book.get("title"),
        "isbn13": book.get("isbn13"),
        "author": book.get("author"),
    }

    raw = gather_book_intel(
        isbn13=book.get("isbn13", ""),
        title=book.get("title", ""),
        author=book.get("author", ""),
        coupang_product=product,
        cache=cache,
    )
    raw["book"] = book
    raw["target_persona"] = persona_dict
    raw["coupang"]["affiliate_product"] = {
        "title": product.get("title"),
        "price": product.get("price"),
        "deeplink": product.get("deeplink"),
        "short_deeplink": product.get("short_deeplink"),
    }

    try:
        post = compose_post(raw, tier=tier)
    except CompositionError as exc:
        LOGGER.warning("compose_post failed for cluster %s (isbn=%s): %s", cluster, book.get("isbn13"), exc)
        slot["status"] = "compose_failed"
        slot["error"] = str(exc)
        return slot

    # Ensure the affiliate deeplink is present in the body, appended if missing.
    deeplink = product.get("short_deeplink") or product.get("deeplink") or ""
    body_markdown = post["body_markdown"]
    if deeplink and deeplink not in body_markdown:
        body_markdown = body_markdown.rstrip() + f"\n\n[쿠팡에서 보기]({deeplink})\n"
    post["body_markdown"] = body_markdown

    slot["post"] = {
        "title": post["title"],
        "lead": post["lead"],
        "body_chars": len(post["body_markdown"]),
        "tags": post["tags"],
        "prompt_variant": post["prompt_variant"],
    }

    if config.dry_run or ghost is None:
        slot["status"] = "dry_run"
        slot["preview"] = _ghost_preview(post, ghost_base=_guess_base())
        return slot

    try:
        response = ghost.create_post(
            title=post["title"],
            lead=post["lead"],
            body_markdown=post["body_markdown"],
            tags=post["tags"],
            status=config.publisher_status,
        )
    except GhostPublishError as exc:
        LOGGER.warning("Ghost publish failed: %s", exc)
        slot["status"] = "publish_failed"
        slot["error"] = str(exc)
        return slot

    created = (response.get("posts") or [{}])[0]
    slot["status"] = "published"
    slot["ghost_post"] = {
        "id": created.get("id"),
        "url": created.get("url"),
        "status": created.get("status"),
        "slug": created.get("slug"),
    }
    emit(
        config.analytics_store,
        POST_PUBLISH,
        isbn13=book.get("isbn13"),
        cluster=cluster,
        tier=tier,
        prompt_variant=post["prompt_variant"],
        slug=created.get("slug"),
    )
    return slot


def _ghost_preview(post: dict[str, Any], *, ghost_base: str) -> dict[str, Any]:
    preview_client = GhostAdminClient.__new__(GhostAdminClient)  # bypass env requirements
    preview_client.config = type("_Cfg", (), {
        "base_url": ghost_base,
        "admin_key": "preview:0",
        "audience": "/admin/",
        "timeout_seconds": 20.0,
        "user_agent": "book_intel/0.1 preview",
    })
    return preview_client.preview_post(
        title=post["title"],
        lead=post["lead"],
        body_markdown=post["body_markdown"],
        tags=post["tags"],
    )


def _guess_base() -> str:
    return (os.getenv("RETN_ME_GHOST_URL") or os.getenv("GHOST_ADMIN_URL") or "https://retn.kr").rstrip("/")


def _missing_coupang(**_kwargs: Any) -> Any:
    raise RuntimeError("coupang_search_fn not configured — orchestrator needs a CoupangPartnersClient adapter")


def _load_signal_store(config: OrchestratorConfig) -> SignalStore | None:
    try:
        return SignalStore(config.signal_db_path)
    except Exception as exc:
        LOGGER.warning("signal store unavailable, proceeding cold-start: %s", exc)
        return None


# --- CLI ---------------------------------------------------------------


def _cli_default_config(args: argparse.Namespace) -> OrchestratorConfig:
    from client import CoupangPartnersClient
    from url_shortener import BuiltinShortener
    from analytics import AnalyticsStore

    cfg = OrchestratorConfig(
        limit=args.limit,
        dry_run=args.dry_run,
        publisher_status=args.status,
    )
    if args.signal_db:
        cfg.signal_db_path = args.signal_db
    if args.cache_db:
        cfg.cache_db_path = args.cache_db

    try:
        coupang_client = CoupangPartnersClient.from_env()
        cfg.coupang_search_fn = lambda **kw: coupang_client.search_products(
            keyword=kw.get("keyword", ""),
            limit=kw.get("limit", 5),
        )
    except Exception as exc:
        LOGGER.warning("Coupang client unavailable: %s", exc)

    public_base = os.getenv("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", "https://a.retn.kr")
    try:
        shortener = BuiltinShortener(db_path=cfg.signal_db_path, public_base_url=public_base)
        cfg.shorten_fn = shortener.shorten
    except Exception as exc:
        LOGGER.warning("Shortener unavailable: %s", exc)

    try:
        cfg.analytics_store = AnalyticsStore(cfg.signal_db_path)
    except Exception as exc:
        LOGGER.warning("AnalyticsStore unavailable: %s", exc)

    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="book_intel.orchestrator")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="compose + preview without hitting Ghost (default)")
    mode.add_argument("--live", action="store_true", help="publish to Ghost (creates drafts by default)")
    parser.add_argument("--status", default="draft", choices=["draft", "published"], help="Ghost post status when --live (default: draft)")
    parser.add_argument("--limit", type=int, default=3, help="total posts to produce this run")
    parser.add_argument("--signal-db", default=None, help="override signal store sqlite path")
    parser.add_argument("--cache-db", default=None, help="override enrichment cache sqlite path")
    parser.add_argument("--verbose", action="store_true", help="DEBUG-level logging")
    args = parser.parse_args(argv)

    if args.live:
        args.dry_run = False

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cfg = _cli_default_config(args)
    results = run(cfg)

    summary = {
        "limit": cfg.limit,
        "dry_run": cfg.dry_run,
        "slots": [{
            "cluster": r["cluster"],
            "tier": r["tier"],
            "status": r.get("status"),
            "book": r.get("book"),
            "post": r.get("post"),
            "ghost_post": r.get("ghost_post"),
            "error": r.get("error"),
        } for r in results],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Exit nonzero if everything failed.
    any_ok = any(r.get("status") in ("dry_run", "published") for r in results)
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
