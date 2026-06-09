#!/usr/bin/env python3
"""AEO (Answer Engine Optimization) pipeline: compose → score → publish for a
queue of books. Chains canvas_iterate + regenerate_book_draft so the whole
flow is one invocation per book.

Queue file: .data/aeo_queue.json
  [
    {"isbn": "9791167740984", "title": "도둑맞은 집중력", "author": "요한 하리",
     "tier": "A", "post_id": "69e3791490b39c0422d64c9a", "ghost_site": "retn.kr"},
    {"isbn": "...", "title": "...", "author": "...", "tier": "A"}   // no post_id → create new
  ]

Per-book hard requirement: `.data/author_facts/<isbn>.md` must exist. Skips
any book without it to avoid hallucinated author methodology.

Usage:
    python3 scripts/aeo_pipeline.py                 # run entire queue
    python3 scripts/aeo_pipeline.py --only <isbn>   # single book
    python3 scripts/aeo_pipeline.py --dry-run       # skip Ghost publish

Log: .data/aeo_log.jsonl  (one JSON line per book, includes best score + ghost id)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


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
]:
    _load_env_file(p)


def _log(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _run_iterate(book: dict, threshold: int, max_iters: int) -> dict:
    """Run canvas_iterate.py for one book. Returns parsed result with best score."""

    cmd = [
        sys.executable, str(ROOT / "scripts" / "canvas_iterate.py"),
        "--isbn", book["isbn"],
        "--title", book.get("title", ""),
        "--author", book.get("author", ""),
        "--tier", book.get("tier", "A"),
        "--threshold", str(threshold),
        "--max-iters", str(max_iters),
    ]
    print(f"[iterate] {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, env=dict(os.environ), capture_output=False)
    elapsed = time.time() - t0

    # Parse the best score from the iteration log.
    log_path = ROOT / ".data" / "canvas-iteration-log.jsonl"
    best = {"score": 0, "iter": 0}
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("score", 0) > best["score"]:
                best = {"score": entry["score"], "iter": entry.get("iter", 0)}
    return {"returncode": result.returncode, "elapsed_s": round(elapsed, 1), "best": best}


def _run_patch(book: dict) -> dict:
    """Run regenerate_book_draft.py --reuse to PATCH or create Ghost draft."""

    cmd = [
        sys.executable, str(ROOT / "scripts" / "regenerate_book_draft.py"),
        "--isbn", book["isbn"],
        "--title", book.get("title", ""),
        "--author", book.get("author", ""),
        "--tier", book.get("tier", "A"),
        "--ghost-site", book.get("ghost_site", "retn.kr"),
        "--reuse",
    ]
    if book.get("post_id"):
        cmd += ["--post-id", book["post_id"]]
    print(f"[publish] {' '.join(cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, env=dict(os.environ), capture_output=False)
    return {"returncode": result.returncode, "elapsed_s": round(time.time() - t0, 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default=str(ROOT / ".data" / "aeo_queue.json"))
    parser.add_argument("--only", default=None, help="process only this ISBN")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold", type=int, default=95)
    parser.add_argument("--max-iters", type=int, default=3)
    parser.add_argument("--log", default=str(ROOT / ".data" / "aeo_log.jsonl"))
    args = parser.parse_args()

    queue_path = Path(args.queue)
    if not queue_path.exists():
        print(f"queue missing: {queue_path}", file=sys.stderr)
        return 1
    books = json.loads(queue_path.read_text())
    if args.only:
        books = [b for b in books if b.get("isbn") == args.only]
    if not books:
        print("no books to process", file=sys.stderr)
        return 1

    for book in books:
        isbn = book["isbn"]
        facts = ROOT / ".data" / "author_facts" / f"{isbn}.md"
        if not facts.exists():
            print(f"[skip] {isbn}: missing .data/author_facts/{isbn}.md (required to avoid hallucinated author info)")
            _log({"ts": time.time(), "isbn": isbn, "status": "skipped_no_facts"}, Path(args.log))
            continue

        print(f"\n=== {isbn} / {book.get('title','')} ===")
        iter_result = _run_iterate(book, args.threshold, args.max_iters)
        print(f"      best_score={iter_result['best']['score']} elapsed={iter_result['elapsed_s']}s")

        if args.dry_run or iter_result["returncode"] != 0 or iter_result["best"]["score"] < args.threshold:
            _log({
                "ts": time.time(), "isbn": isbn, "title": book.get("title"),
                "status": "dry_run" if args.dry_run else ("failed" if iter_result["returncode"] else "below_threshold"),
                "best_score": iter_result["best"]["score"],
                "best_iter": iter_result["best"]["iter"],
            }, Path(args.log))
            print(f"[skip-publish] {'dry-run' if args.dry_run else 'score below threshold or iterate failed'}")
            continue

        publish_result = _run_patch(book)
        _log({
            "ts": time.time(), "isbn": isbn, "title": book.get("title"),
            "status": "published" if publish_result["returncode"] == 0 else "publish_failed",
            "best_score": iter_result["best"]["score"],
            "best_iter": iter_result["best"]["iter"],
            "post_id": book.get("post_id"),
            "publish_elapsed_s": publish_result["elapsed_s"],
        }, Path(args.log))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
