#!/usr/bin/env python3
"""Flip a Ghost draft to status=scheduled with a future published_at.

Usage:
    python3 scripts/schedule_ghost_post.py \
        --post-id <id> --ghost-site retn.kr \
        --publish-at "2026-04-22T00:00:00.000Z"

Time format: ISO 8601 UTC. For KST morning scheduling, 09:00 KST = 00:00:00Z
the same date (KST = UTC+9). Example: 2026-04-22 09:00 KST → 2026-04-22T00:00:00.000Z.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
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
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


for p in [
    ROOT / ".env.local",
    Path.home() / ".env",
    Path.home() / "claude-config" / "global.env",
]:
    _load_env_file(p)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-id", required=True)
    parser.add_argument("--ghost-site", default="retn.kr", choices=["retn.kr", "retn.me"])
    parser.add_argument("--publish-at", required=True, help='ISO UTC, e.g. "2026-04-22T00:00:00.000Z"')
    parser.add_argument("--status", default="scheduled", choices=["scheduled", "published", "draft"])
    args = parser.parse_args()

    from book_intel.publisher.ghost import GhostAdminClient, GhostConfig
    prefix = "RETN_KR_GHOST" if args.ghost_site == "retn.kr" else "RETN_ME_GHOST"
    base_url = os.environ.get(f"{prefix}_URL", "").strip().rstrip("/")
    admin_key = os.environ.get(f"{prefix}_ADMIN_KEY", "").strip()
    if not base_url or not admin_key:
        print(f"missing {prefix}_URL / {prefix}_ADMIN_KEY", file=sys.stderr)
        return 1
    client = GhostAdminClient(config=GhostConfig(base_url=base_url, admin_key=admin_key))

    # Grab current updated_at for optimistic lock.
    get_url = f"{base_url}/ghost/api/admin/posts/{args.post_id}/"
    req = urllib.request.Request(
        get_url,
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
    post = (current.get("posts") or [{}])[0]
    updated_at = post.get("updated_at")
    if not updated_at:
        print("post not found or has no updated_at", file=sys.stderr)
        return 1

    payload = {
        "posts": [{
            "updated_at": updated_at,
            "status": args.status,
            "published_at": args.publish_at,
        }],
    }
    put_url = f"{base_url}/ghost/api/admin/posts/{args.post_id}/"
    put_req = urllib.request.Request(
        put_url,
        headers={
            "Authorization": f"Ghost {client._build_jwt()}",
            "Accept": "application/json",
            "Accept-Version": "v5.0",
            "User-Agent": client.config.user_agent,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="PUT",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    with urllib.request.urlopen(put_req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    updated = (result.get("posts") or [{}])[0]
    print(f"status       = {updated.get('status')}")
    print(f"published_at = {updated.get('published_at')}")
    print(f"url          = {updated.get('url')}")
    print(f"updated_at   = {updated.get('updated_at')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
