#!/usr/bin/env python3
"""Self-improving Canvas loop: compose → evaluate → feedback → recompose.

Target: drive evaluator score ≥ threshold (default 95). Caps iterations to
--max-iters (default 5) to bound runtime. Each iteration's compose + score
log is written to .data/canvas-iteration-log.jsonl. The best-scoring canvas
is written to .data/canvas-preview.json so existing PATCH tooling picks it
up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


for p in [
    ROOT / ".env.local",
    Path.home() / ".env",
    Path.home() / "claude-config" / "global.env",
    Path.home() / ".claude" / "global.env",
]:
    _load_env_file(p)


def _fetch_primary_coupang(isbn: str, title: str, author: str):
    try:
        from client import CoupangPartnersClient
        from url_shortener import BuiltinShortener
        client = CoupangPartnersClient.from_env()
    except Exception as exc:
        print(f"[warn] primary coupang unavailable: {exc}")
        return None, None, None
    query = f"{title} {author}".strip() or isbn
    try:
        res = client.search_products(keyword=query, limit=5)
        items = (res or {}).get("data", {})
        if isinstance(items, dict):
            items = items.get("productData") or items.get("products") or []
        if not items:
            return None, None, None
        hit = items[0]
    except Exception as exc:
        print(f"[warn] primary coupang search: {exc}")
        return None, None, None
    deep = hit.get("productUrl") or hit.get("deeplink")
    short = None
    try:
        public_base = os.environ.get("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", "https://a.retn.kr")
        shortener = BuiltinShortener(
            db_path=str(ROOT / ".data" / "openclaw-shopping.sqlite3"),
            public_base_url=public_base,
        )
        if deep:
            short = shortener.shorten(deep)
    except Exception as exc:
        print(f"[warn] shortener: {exc}")
    return hit.get("productName") or title, deep, short


def _build_raw(isbn: str, title_hint: str, author_hint: str) -> dict:
    from book_intel.cache import EnrichmentCache
    from book_intel.sources import gather_book_intel
    from book_reco.providers.naver import NaverBookProvider

    cache = EnrichmentCache(str(ROOT / ".data" / "book-intel-cache.sqlite3"))
    title, author = title_hint, author_hint
    if not (title and author):
        try:
            naver = NaverBookProvider()
            items = naver.search(isbn, limit=3)
            if items:
                title = title or items[0].title
                author = author or items[0].author
        except Exception:
            pass
    raw = gather_book_intel(isbn13=isbn, title=title, author=author, coupang_product=None, cache=cache)
    raw["book"] = {"title": title, "author": author, "isbn13": isbn, "category": "자기계발"}
    ct, deep, short = _fetch_primary_coupang(isbn, title, author)
    if deep or short:
        raw["primary_coupang"] = {"title": ct, "deeplink": deep, "short_deeplink": short}
    raw["target_persona"] = {
        "interests": ["솔로 오퍼레이터", "수익화", "프로덕트"],
        "categories": ["자기계발"],
    }
    raw["persona_hints"] = {
        "cluster": "openclaw_operator",
        "tier": "A",
        "concept_mapping_hints": [
            "런웨이/일정 리스크",
            "단위 경제성",
            "의사결정 지연 비용",
            "혼자 일하는 사람의 기회비용",
            "스래싱(thrashing)",
            "컨텍스트 스위치 비용",
        ],
    }
    note_path = ROOT / ".data" / "operator_note.md"
    if note_path.exists():
        note = note_path.read_text().strip()
        if note:
            raw["operator_note"] = note

    # Known public-domain author facts. Operator maintains book → facts in
    # .data/author_facts/<isbn>.md. The composer treats this as authoritative.
    author_facts_path = ROOT / ".data" / "author_facts" / f"{isbn}.md"
    if author_facts_path.exists():
        facts = author_facts_path.read_text().strip()
        if facts:
            raw["known_author_facts"] = facts

    # Similar books with Coupang links (for affiliate link diversification).
    # Operator maintains book → similar in .data/similar_coupang/<isbn>.json.
    similar_path = ROOT / ".data" / "similar_coupang" / f"{isbn}.json"
    if similar_path.exists():
        import json as _json
        try:
            similar_def = _json.loads(similar_path.read_text())
            enriched = _fetch_coupang_for_similar(similar_def.get("similar_books_with_coupang", []))
            raw["similar_books_with_coupang"] = enriched
        except Exception as exc:
            print(f"[warn] similar_coupang load failed: {exc}")
    return raw


def _fetch_coupang_for_similar(candidates: list) -> list:
    """For each candidate {title, author, angle, coupang_search_query},
    run a Coupang search and attach the first matching product's short deeplink."""

    out = []
    try:
        from client import CoupangPartnersClient
        from url_shortener import BuiltinShortener
        client = CoupangPartnersClient.from_env()
    except Exception as exc:
        print(f"[warn] Coupang client unavailable: {exc}")
        for c in candidates:
            out.append({**c, "coupang_deeplink": None, "coupang_short_deeplink": None, "coupang_title": None})
        return out

    shortener = None
    try:
        public_base = os.environ.get("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", "https://a.retn.kr")
        shortener = BuiltinShortener(
            db_path=str(ROOT / ".data" / "openclaw-shopping.sqlite3"),
            public_base_url=public_base,
        )
    except Exception as exc:
        print(f"[warn] Shortener unavailable: {exc}")

    for c in candidates:
        q = c.get("coupang_search_query") or c.get("title") or ""
        hit = None
        try:
            result = client.search_products(keyword=q, limit=3)
            items = (result or {}).get("data", {}).get("productData") or (result or {}).get("data") or []
            if isinstance(items, dict):
                items = items.get("products", [])
            if isinstance(items, list) and items:
                hit = items[0]
        except Exception as exc:
            print(f"[warn] coupang search '{q}' failed: {exc}")

        deeplink = (hit or {}).get("productUrl") or (hit or {}).get("deeplink") or None
        short = None
        if deeplink and shortener is not None:
            try:
                short = shortener.shorten(deeplink)
            except Exception:
                pass
        out.append({
            **c,
            "coupang_title": (hit or {}).get("productName") or (hit or {}).get("title"),
            "coupang_deeplink": deeplink,
            "coupang_short_deeplink": short,
        })
    return out


def _append_log(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isbn", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--tier", default="A", choices=["A", "B"])
    parser.add_argument("--threshold", type=int, default=95)
    parser.add_argument("--max-iters", type=int, default=5)
    parser.add_argument("--output", default=str(ROOT / ".data" / "canvas-preview.json"))
    parser.add_argument("--log", default=str(ROOT / ".data" / "canvas-iteration-log.jsonl"))
    args = parser.parse_args()

    from book_intel.composer import compose_post
    from book_intel.composer.evaluator import evaluate_canvas

    print(f"[setup] isbn={args.isbn} tier={args.tier} threshold={args.threshold} max_iters={args.max_iters}")
    raw = _build_raw(args.isbn, args.title, args.author)
    print(f"        book: {raw['book']['title']} / {raw['book']['author']}")

    best = {"score": -1, "post": None, "verdict": None, "iter": 0}
    revision_hint: str | None = None

    for it in range(1, args.max_iters + 1):
        print(f"\n[iter {it}/{args.max_iters}] compose…", flush=True)
        t0 = time.time()
        run_raw = dict(raw)
        if revision_hint:
            run_raw["revision_hint"] = revision_hint
        try:
            post = compose_post(run_raw, tier=args.tier)
        except Exception as exc:
            print(f"  compose failed: {exc}")
            _append_log({"iter": it, "event": "compose_failed", "error": str(exc)}, Path(args.log))
            continue
        compose_s = time.time() - t0
        print(f"        composed in {compose_s:.0f}s; body_chars={len(post['body_markdown'])}")

        print(f"[iter {it}] evaluate…", flush=True)
        t1 = time.time()
        score, verdict = evaluate_canvas(post, raw)
        eval_s = time.time() - t1
        axes = verdict.get("axes") or {}
        print(f"        score={score} (took {eval_s:.0f}s)")
        print(f"        axes: {json.dumps(axes, ensure_ascii=False)}")
        critique = verdict.get("critique") or ""
        top_fixes = verdict.get("top_fixes") or []
        if critique:
            print(f"        critique: {critique[:300]}")
        if top_fixes:
            print(f"        top_fixes: {top_fixes}")

        _append_log({
            "iter": it,
            "score": score,
            "axes": axes,
            "critique": critique,
            "top_fixes": top_fixes,
            "body_chars": len(post.get("body_markdown", "")),
            "compose_s": round(compose_s, 1),
            "eval_s": round(eval_s, 1),
        }, Path(args.log))

        if score > best["score"]:
            best = {"score": score, "post": post, "verdict": verdict, "iter": it}
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")

        if score >= args.threshold:
            print(f"\n[done] hit threshold {args.threshold} at iter {it}. best saved to {args.output}")
            return 0

        # Build revision hint for next iteration.
        parts: list[str] = []
        if critique:
            parts.append(f"Evaluator critique: {critique}")
        if top_fixes:
            parts.append("Top fixes: " + "; ".join(top_fixes))
        # Highlight weakest axes for focus.
        if isinstance(axes, dict):
            weak = sorted(axes.items(), key=lambda kv: kv[1])[:3]
            parts.append("Weakest axes to prioritize: " + ", ".join(f"{k}({v})" for k, v in weak))
        revision_hint = " | ".join(parts)

    print(f"\n[done] max iters reached. best score {best['score']} at iter {best['iter']}. saved to {args.output}")
    return 0 if best["score"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
