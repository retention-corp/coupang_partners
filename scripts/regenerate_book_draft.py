#!/usr/bin/env python3
"""One-shot utility: re-compose a Canvas for a specific ISBN and either
print a dry preview or PATCH the existing Ghost post.

Usage:
    python3 scripts/regenerate_book_draft.py --isbn 9791168473690 --dry-run
    python3 scripts/regenerate_book_draft.py --isbn 9791168473690 --post-id <ghost_post_id>

When `--post-id` is set the script PATCHes that post in place (the
existing Ghost draft gets its body + codeinjection_head replaced with the
Canvas-derived HTML). Without `--post-id`, nothing is written to Ghost.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env.local so we don't depend on shell dotenv helpers.
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


_load_env_file(ROOT / ".env.local")
_load_env_file(Path.home() / ".env")
_load_env_file(Path.home() / "claude-config" / "global.env")
_load_env_file(Path.home() / ".claude" / "global.env")


def _fetch_primary_coupang_for_book(isbn: str, title: str, author: str):
    """Run Coupang search for the main book and return (title, deeplink, short)."""
    try:
        from client import CoupangPartnersClient
        from url_shortener import BuiltinShortener
        client = CoupangPartnersClient.from_env()
    except Exception as exc:
        print(f"[warn] coupang client unavailable: {exc}")
        return None, None, None
    query = f"{title} {author}".strip() or isbn
    try:
        result = client.search_products(keyword=query, limit=5)
        items = (result or {}).get("data", {})
        if isinstance(items, dict):
            items = items.get("productData") or items.get("products") or []
        if not items:
            return None, None, None
        hit = items[0]
    except Exception as exc:
        print(f"[warn] coupang search failed: {exc}")
        return None, None, None
    deeplink = hit.get("productUrl") or hit.get("deeplink")
    short = None
    try:
        public_base = os.environ.get("OPENCLAW_SHOPPING_PUBLIC_BASE_URL", "https://a.retn.kr")
        shortener = BuiltinShortener(
            db_path=str(ROOT / ".data" / "openclaw-shopping.sqlite3"),
            public_base_url=public_base,
        )
        if deeplink:
            short = shortener.shorten(deeplink)
    except Exception as exc:
        print(f"[warn] shortener failed: {exc}")
    return hit.get("productName") or title, deeplink, short


def _build_raw_for_isbn(isbn: str, title_hint: str = "", author_hint: str = "") -> dict:
    from book_intel.cache import EnrichmentCache
    from book_intel.sources import gather_book_intel
    from book_reco.providers.naver import NaverBookProvider

    cache = EnrichmentCache(str(ROOT / ".data" / "book-intel-cache.sqlite3"))

    # Try to resolve title/author via Naver ISBN search if we don't have them.
    title, author = title_hint, author_hint
    if not (title and author):
        try:
            naver = NaverBookProvider()
            for item in naver.search(isbn, limit=3):
                if getattr(item, "isbn13", "") == isbn or isbn in (getattr(item, "isbn", "") or ""):
                    title = title or getattr(item, "title", "")
                    author = author or getattr(item, "author", "")
                    break
            if not title:
                items = naver.search(isbn, limit=3)
                if items:
                    title = items[0].title
                    author = items[0].author
        except Exception:
            pass

    raw = gather_book_intel(
        isbn13=isbn,
        title=title,
        author=author,
        coupang_product=None,
        cache=cache,
    )
    raw["book"] = {
        "title": title,
        "author": author,
        "isbn13": isbn,
        "category": "자기계발",
    }
    # Primary Coupang affiliate link for THIS book. Surfaces into ghost.py's
    # HTML renderer as a CTA + disclosure. Without it the disclosure line
    # has nothing to link to (the bug the user flagged).
    coupang_title, deeplink, short = _fetch_primary_coupang_for_book(isbn, title, author)
    if deeplink or short:
        raw["primary_coupang"] = {
            "title": coupang_title,
            "deeplink": deeplink,
            "short_deeplink": short,
        }
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
        ],
    }
    # Optional operator note for 1인칭 dogfood — pulled from .data/operator_note.md
    note_path = ROOT / ".data" / "operator_note.md"
    if note_path.exists():
        note = note_path.read_text().strip()
        if note:
            raw["operator_note"] = note
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isbn", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--tier", default="A", choices=["A", "B"])
    parser.add_argument("--post-id", default=None, help="Ghost post id — if set, PATCH the post")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--write", action="store_true", help="Shortcut for --post-id via CANVAS_POST_ID env")
    parser.add_argument("--output", default=str(ROOT / ".data" / "canvas-preview.json"))
    parser.add_argument("--reuse", action="store_true", help="skip re-compose; load canvas from --output")
    parser.add_argument("--ghost-site", default="retn.kr", choices=["retn.kr", "retn.me"], help="which Ghost instance to target")
    args = parser.parse_args()

    if args.write and not args.post_id:
        args.post_id = os.environ.get("CANVAS_POST_ID")

    if args.reuse and Path(args.output).exists():
        print(f"[1-2/4] reusing canvas from {args.output}")
        post = json.loads(Path(args.output).read_text())
    else:
        print(f"[1/4] gathering enrichment for isbn={args.isbn}")
        raw = _build_raw_for_isbn(args.isbn, title_hint=args.title, author_hint=args.author)
        print(f"      book: {raw['book']['title']} / {raw['book']['author']}")

        print(f"[2/4] composing Canvas (tier={args.tier}) via OpenClaw agent")
        from book_intel.composer import compose_post

        post = compose_post(raw, tier=args.tier)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      saved to {args.output}")
    print(f"      title: {post['title']}")
    print(f"      body_chars: {len(post['body_markdown'])}")
    print(f"      canvas fields: {[k for k, v in (post.get('canvas') or {}).items() if v]}")

    if not args.post_id:
        print("[3/4] dry-run — not touching Ghost. Pass --post-id <id> to PATCH in place.")
        return 0

    from book_intel.publisher import GhostAdminClient
    from book_intel.publisher.ghost import GhostConfig
    site_prefix = "RETN_KR_GHOST" if args.ghost_site == "retn.kr" else "RETN_ME_GHOST"
    base_url = os.environ.get(f"{site_prefix}_URL", "").strip().rstrip("/")
    admin_key = os.environ.get(f"{site_prefix}_ADMIN_KEY", "").strip()
    if not base_url or not admin_key:
        print(f"missing {site_prefix}_URL / {site_prefix}_ADMIN_KEY; falling back to RETN_ME_*", file=sys.stderr)
        client = GhostAdminClient()
    else:
        client = GhostAdminClient(config=GhostConfig(base_url=base_url, admin_key=admin_key))

    # Fetch existing post to grab `updated_at` (required for PATCH). Ghost is
    # fronted by Cloudflare which 403s the default Python urllib User-Agent, so
    # we mirror the same UA the publisher uses for POST calls.
    import urllib.request
    head_url = f"{client.config.base_url}/ghost/api/admin/posts/{args.post_id}/"
    req = urllib.request.Request(
        head_url,
        headers={
            "Authorization": f"Ghost {client._build_jwt()}",
            "Accept": "application/json",
            "Accept-Version": "v5.0",
            "User-Agent": client.config.user_agent,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        current = json.loads(resp.read().decode("utf-8"))
    existing = (current.get("posts") or [{}])[0]
    updated_at = existing.get("updated_at")
    print(f"[3/4] existing post updated_at={updated_at}")

    # retn.kr 목록 노출 조건: internal tag `#blog-col` 은 반드시 포함되어야 한다.
    # composer 가 놓치는 경우가 있어 PATCH 경로에서 방어적으로 주입한다.
    tags = list(post.get("tags") or [])
    if args.ghost_site == "retn.kr" and "#blog-col" not in tags:
        tags.insert(0, "#blog-col")

    print(f"[4/4] PATCHing Ghost post {args.post_id}")
    result = client.update_post(
        post_id=args.post_id,
        updated_at=updated_at,
        title=post["title"],
        lead=post["lead"],
        body_markdown=post["body_markdown"],
        tags=tags,
        canvas=post.get("canvas") or {},
    )
    patched = (result.get("posts") or [{}])[0]
    print(f"      url: {patched.get('url')}")
    print(f"      status: {patched.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
