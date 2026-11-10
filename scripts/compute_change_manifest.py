#!/usr/bin/env python3
"""Produce deterministic change, upload-source, or audit manifests."""
import argparse
import fnmatch
import hashlib
import subprocess
from pathlib import Path
from typing import Iterable, List, Set, Tuple

COMMON_CACHE_TOOL_EXCLUSIONS = (
    ".git", ".git/*", "__pycache__/*", ".venv/*", ".venv",
    ".pytest_cache/*", ".ruff_cache/*", ".mypy_cache/*",
)
UPLOAD_ONLY_EXCLUSIONS = COMMON_CACHE_TOOL_EXCLUSIONS + (
    ".data/*", ".omo/*", ".book-cardnews-runs/*", ".env*",
)


def _excluded(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"*/{pattern}") for pattern in patterns)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _status_paths(root: Path) -> List[Tuple[str, str]]:
    raw = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "-uall"], check=True, capture_output=True).stdout
    parts = raw.split(b"\0"); result: List[Tuple[str, str]] = []; index = 0
    while index < len(parts) and parts[index]:
        entry = parts[index].decode("utf-8", "surrogateescape"); status, path = entry[:2], entry[3:]
        if status[0] in "RC" or status[1] in "RC":
            old_path = parts[index + 1].decode("utf-8", "surrogateescape")
            result.append(("D ", old_path)); result.append(("??", path)); index += 2
        else:
            result.append((status, path)); index += 1
    return result


def _all_files(root: Path) -> Set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and ".git" not in path.relative_to(root).parts}


def manifest_lines(root: Path, mode: str = "change") -> List[str]:
    root = root.resolve(); entries: List[Tuple[str, str]]
    if mode == "upload":
        entries = [("  ", path) for path in _all_files(root) if not _excluded(path, UPLOAD_ONLY_EXCLUSIONS)]
    else:
        entries = _status_paths(root)
        if mode == "audit":
            seen = {path for _, path in entries}
            data_root = root / ".data"
            if data_root.exists():
                entries.extend(("??", str(path.relative_to(root))) for path in data_root.rglob("*") if path.is_file() and str(path.relative_to(root)) not in seen)
        patterns = COMMON_CACHE_TOOL_EXCLUSIONS if mode == "audit" else ()
        entries = [(status, path) for status, path in entries if not _excluded(path, patterns)]
    lines = []
    for status, rel in sorted(entries, key=lambda item: (item[1], item[0])):
        file_path = root / rel
        lines.append(f"{status} {rel} {_digest(file_path)}" if file_path.is_file() else f"D {rel}")
    return lines


def generate_gcloudignore() -> str:
    return "".join(f"{pattern}\n" for pattern in UPLOAD_ONLY_EXCLUSIONS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("change", "upload", "audit"), default="change")
    parser.add_argument("--generate-gcloudignore", action="store_true")
    parser.add_argument("--check-gcloudignore", action="store_true")
    parser.add_argument("--verify-staged-file-list", type=Path)
    args = parser.parse_args()
    ignore_path = args.root / ".gcloudignore"
    if args.generate_gcloudignore:
        ignore_path.write_text(generate_gcloudignore(), encoding="utf-8"); return 0
    if args.check_gcloudignore:
        return 0 if ignore_path.read_text(encoding="utf-8") == generate_gcloudignore() else 2
    if args.verify_staged_file_list:
        staged = {(line.strip()[2:] if line.strip().startswith("./") else line.strip()) for line in args.verify_staged_file_list.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("Listed")}
        expected = {path for path in _all_files(args.root.resolve()) if not _excluded(path, UPLOAD_ONLY_EXCLUSIONS)}
        return 0 if staged == expected else 3
    print("\n".join(manifest_lines(args.root, args.mode)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
