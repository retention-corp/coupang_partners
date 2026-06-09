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
    tags_prefix: list[str] = field(default_factory=lambda: ["#blog-col", "도서추천", "쿠팡파트너스"])
    prompt_variant: str = "canvas"       # "canvas" (new, Content Canvas) | "blog" (legacy narrative)


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
    prompt_text = _render_prompt(raw, tier=tier_norm, prompt_dir=cfg.prompt_dir, variant=cfg.prompt_variant)
    session_id = _session_id(raw, tier_norm, prefix=cfg.session_prefix)

    base_prompt = prompt_text
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

        if post.get("error") == "insufficient_signal":
            raise CompositionError("composer refused: insufficient_signal")

        repetition = _detect_repetition(post.get("body", ""))
        if repetition and attempt < _MAX_RETRIES:
            last_error = f"repeated n-gram: {repetition[:3]}"
            LOGGER.warning("%s (attempt %d); nudging rewrite", last_error, attempt)
            prompt_text = base_prompt + (
                "\n\nREVISION: your previous draft repeated these phrases too many times: "
                + json.dumps(repetition, ensure_ascii=False)
                + ". Rewrite the body so no 3-gram noun phrase appears more than 3 times. "
                "Vary vocabulary, don't just cut — re-angle the argument."
            )
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
        # The model sometimes wraps JSON inside prose. Greedy-match the outermost
        # braces; plain `\{...\}` non-greedy fails on nested canvas objects (FAQ,
        # JSON-LD, youtube_short) so we walk until braces balance.
        obj = _extract_balanced_json(cleaned)
        if obj is None:
            return None
    if not isinstance(obj, dict):
        return None
    if obj.get("error") == "insufficient_signal":
        return obj  # let caller handle the refusal
    if not isinstance(obj.get("title"), str) or not isinstance(obj.get("body"), str):
        return None
    return obj


def _extract_balanced_json(text: str) -> dict[str, Any] | None:
    """Find the first `{...}` block with balanced braces and try to parse it."""

    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```) and closing fence
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _render_prompt(raw: dict[str, Any], *, tier: str, prompt_dir: Path, variant: str = "canvas") -> str:
    prefix = "canvas" if variant == "canvas" else "blog"
    template = _load_template(prompt_dir / f"{prefix}_{tier.lower()}_tier.md")
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
    tags_raw = post.get("tags") or []
    tags = [str(t).strip() for t in (tags_raw if isinstance(tags_raw, list) else []) if str(t).strip()]
    for pre in reversed(cfg.tags_prefix):
        if pre not in tags:
            tags.insert(0, pre)

    variant_slug = cfg.prompt_variant  # "canvas" or "blog"
    finalized: dict[str, Any] = {
        "title": title,
        "lead": lead,
        "body_markdown": body,
        "tags": tags,
        "prompt_variant": f"{variant_slug}_{tier.lower()}_tier",
        "tier": tier,
        "book": book,
    }

    # Preserve every Canvas field the prompt may have emitted. Downstream (Ghost
    # publisher, newsletter, YouTube) picks what it needs; missing keys stay
    # `None`/`[]` so templates don't blow up on B-tier / sparse payloads.
    canvas_fields = {
        "definition_lead": _as_text(post.get("definition_lead")),
        "subtitle": _as_text(post.get("subtitle")),
        "pros": _as_list(post.get("pros")),
        "cons": _as_list(post.get("cons")),
        "key_takeaways": _as_list(post.get("key_takeaways")),
        "comparisons": _as_list(post.get("comparisons")),
        "target_reader": _as_list(post.get("target_reader")),
        "not_for": _as_list(post.get("not_for")),
        "actionable_exercise": _as_text(post.get("actionable_exercise")),
        "timely_hook": _as_text(post.get("timely_hook")),
        "faq": _as_faq(post.get("faq")),
        "youtube_short": _as_dict(post.get("youtube_short")),
        "newsletter_snippet": _as_text(post.get("newsletter_snippet")),
        "thread_tweets": _as_list(post.get("thread_tweets")),
        "schema_jsonld": _as_dict(post.get("schema_jsonld")),
        "faq_jsonld": _as_dict(post.get("faq_jsonld")),
        "internal_link_seeds": _as_list(post.get("internal_link_seeds")),
    }
    finalized.update({k: v for k, v in canvas_fields.items() if v not in (None, [], {})})
    # Book metadata + primary Coupang CTA info for the publisher.
    primary = (raw or {}).get("primary_coupang") or {}
    canvas_fields["book_title"] = book.get("title") or ""
    canvas_fields["primary_coupang_deeplink"] = primary.get("deeplink") or ""
    canvas_fields["primary_coupang_short_deeplink"] = primary.get("short_deeplink") or ""
    # Similar books with Coupang links — publisher post-processes comparisons
    # so every mentioned book title gets wrapped in its short deeplink even
    # when the composer forgot.
    canvas_fields["similar_books_with_coupang"] = (raw or {}).get("similar_books_with_coupang") or []
    finalized["canvas"] = canvas_fields
    return finalized


def _as_text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _detect_repetition(text: str, *, max_repeats: int = 3, ngram: int = 3) -> list[str]:
    """Return noun 3-grams that appear more than `max_repeats` times in `text`.

    Rough heuristic: split on whitespace/punctuation, drop tokens < 2 chars, slide
    a 3-gram window, count. Korean postpositions leak in but frequency is still
    a good proxy for "same phrase over and over".
    """

    if not text:
        return []
    tokens = [
        tok for tok in re.split(r"[\s\.,\!\?\"'()\[\]《》\-\:\;·/\n\t]+", text) if len(tok) >= 2
    ]
    from collections import Counter
    grams: Counter[str] = Counter()
    for i in range(len(tokens) - ngram + 1):
        grams[" ".join(tokens[i : i + ngram])] += 1
    return [gram for gram, count in grams.most_common(8) if count > max_repeats]


def _as_faq(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        q = _as_text(item.get("q") or item.get("question"))
        a = _as_text(item.get("a") or item.get("answer"))
        if q and a:
            out.append({"q": q, "a": a})
    return out


__all__ = ["CompositionError", "ComposerConfig", "compose_post"]
