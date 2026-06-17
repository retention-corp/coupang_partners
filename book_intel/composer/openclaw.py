"""OpenClaw CLI wrapper for book-review composition.

Calls `openclaw agent --json --thinking medium` as a subprocess. Each book×tier
pair uses a deterministic `--session-id` so repeat runs reuse the same session
context (lets the operator inspect/resume via `openclaw sessions` if needed).

Response shape from `openclaw agent --json`:

    {
      "ok": true,
      "data": {
        "session_id": "...",
        "turn": {
          "finalAssistantVisibleText": "<string — what the model said>",
          "finalAssistantRawText": "<string — same without delivery-channel shaping>",
          ...
        }
      }
    }

The model returns a JSON object string inside `finalAssistantVisibleText`. We parse
that second layer to extract the post. On parse failure we retry once with a
stricter "JSON ONLY" nudge; persistent failure raises `CompositionError` and the
orchestrator drops that post.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("book_intel.composer.openclaw")

_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_OUTPUT_BYTES = 200_000
_MAX_RETRIES = 2


class CompositionError(RuntimeError):
    """Raised when OpenClaw returns unusable output after all retries."""


@dataclass
class ComposerConfig:
    thinking: str = "medium"            # off | minimal | low | medium | high | xhigh
    timeout_seconds: int = 240
    cli_path: str = "openclaw"
    prompt_dir: Path = _PROMPT_DIR
    session_prefix: str = "book-intel"
    tags_prefix: list[str] = field(default_factory=lambda: ["도서추천", "쿠팡파트너스"])


def compose_post(
    raw: dict[str, Any],
    *,
    tier: str,
    config: ComposerConfig | None = None,
) -> dict[str, Any]:
    """Compose a blog-ready post from the gathered raw material.

    `raw` is the output of `book_intel.sources.gather_book_intel` augmented with a
    `book` dict (title/author/publisher/isbn13/category) and an optional
    `target_persona` describing who the post is for. `tier` is "A" or "B".
    """

    cfg = config or ComposerConfig()
    tier_norm = _normalize_tier(tier)
    prompt_text = _render_prompt(raw, tier=tier_norm, prompt_dir=cfg.prompt_dir)
    session_id = _session_id(raw, tier_norm, prefix=cfg.session_prefix)

    last_error: str = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            raw_stdout = _run_openclaw(prompt_text, session_id=f"{session_id}-{attempt}", config=cfg)
        except subprocess.TimeoutExpired:
            last_error = f"openclaw timeout after {cfg.timeout_seconds}s"
            LOGGER.warning("%s (attempt %d)", last_error, attempt)
            continue
        except subprocess.CalledProcessError as exc:
            last_error = f"openclaw exited {exc.returncode}: {(exc.stderr or '')[:200]}"
            LOGGER.warning("%s (attempt %d)", last_error, attempt)
            continue

        content = _extract_assistant_text(raw_stdout)
        if not content:
            last_error = "empty assistant text"
            LOGGER.warning("%s (attempt %d)", last_error, attempt)
            continue

        post = _parse_post_json(content)
        if post is None:
            last_error = "assistant output not parseable as post JSON"
            LOGGER.warning("%s (attempt %d)", last_error, attempt)
            # Append a stricter nudge for the next attempt.
            prompt_text = prompt_text + "\n\nREMINDER: Output ONLY the JSON object, no code fences, no prose before/after."
            continue

        return _finalize(post, raw, tier=tier_norm, cfg=cfg)

    raise CompositionError(f"compose_post failed after {_MAX_RETRIES} attempts: {last_error}")


# --- internals ---------------------------------------------------------


def _run_openclaw(message: str, *, session_id: str, config: ComposerConfig) -> str:
    argv = [
        config.cli_path, "agent",
        "--session-id", session_id,
        "--message", message,
        "--json",
        "--thinking", config.thinking,
        "--timeout", str(config.timeout_seconds),
    ]
    env = dict(os.environ)
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds + 30,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, argv, output=completed.stdout, stderr=completed.stderr
        )
    return completed.stdout[:_MAX_OUTPUT_BYTES]


def _extract_assistant_text(stdout: str) -> str:
    """Pull the assistant's text out of an `openclaw agent --json` stdout payload.

    OpenClaw's response shape varies by runner/route: the gateway path exposes a
    `{data: {turn: {finalAssistantVisibleText}}}` envelope, while embedded runs
    with delivery payloads surface as `{result: {payloads: [{text}]}}`. We try
    both defensively and return whichever yields a non-empty string.
    """

    try:
        wrapper = json.loads(stdout)
    except Exception as exc:
        LOGGER.debug("openclaw stdout not json: %s; raw head: %s", exc, stdout[:200])
        return ""
    if not isinstance(wrapper, dict):
        return ""

    # Shape A: gateway / default deliver path.
    data_block = wrapper.get("data")
    if isinstance(data_block, dict):
        turn = data_block.get("turn")
        if isinstance(turn, dict):
            candidate = (
                turn.get("finalAssistantVisibleText")
                or turn.get("finalAssistantRawText")
                or ""
            )
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

    # Shape B: embedded runner delivery payloads.
    result = wrapper.get("result")
    if isinstance(result, dict):
        payloads = result.get("payloads")
        if isinstance(payloads, list):
            for payload in payloads:
                if isinstance(payload, dict):
                    text = payload.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

    # Shape C: flat `text` key (agent run with no delivery shaping).
    flat_text = wrapper.get("text")
    if isinstance(flat_text, str) and flat_text.strip():
        return flat_text.strip()

    return ""


def _parse_post_json(text: str) -> dict[str, Any] | None:
    cleaned = _strip_code_fences(text)
    try:
        obj = json.loads(cleaned)
    except Exception:
        # Try to locate a JSON object within prose.
        match = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("title"), str) or not isinstance(obj.get("body"), str):
        return None
    return obj


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```) and closing fence
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _render_prompt(raw: dict[str, Any], *, tier: str, prompt_dir: Path) -> str:
    template = _load_template(prompt_dir / f"blog_{tier.lower()}_tier.md")
    return template.replace("{{PAYLOAD_JSON}}", json.dumps(raw, ensure_ascii=False, indent=2))


def _load_template(path: Path) -> str:
    if not path.exists():
        raise CompositionError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _session_id(raw: dict[str, Any], tier: str, *, prefix: str) -> str:
    book = (raw or {}).get("book") or {}
    isbn = (book.get("isbn13") or "").strip()
    if isbn:
        return f"{prefix}-{tier.lower()}-{isbn}"
    title = str(book.get("title") or "unknown")
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{tier.lower()}-{digest}"


def _normalize_tier(tier: str) -> str:
    value = (tier or "").strip().upper()
    return "A" if value == "A" else "B"


def _finalize(post: dict[str, Any], raw: dict[str, Any], *, tier: str, cfg: ComposerConfig) -> dict[str, Any]:
    book = (raw or {}).get("book") or {}
    title = (post.get("title") or "").strip()
    lead = (post.get("lead") or "").strip()
    body = (post.get("body") or "").strip()
    tags = post.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    # Always prepend platform tags so posts are discoverable by category.
    for pre in reversed(cfg.tags_prefix):
        if pre not in tags:
            tags.insert(0, pre)
    return {
        "title": title,
        "lead": lead,
        "body_markdown": body,
        "tags": tags,
        "prompt_variant": f"blog_{tier.lower()}_tier",
        "tier": tier,
        "book": book,
    }


__all__ = ["CompositionError", "ComposerConfig", "compose_post"]
