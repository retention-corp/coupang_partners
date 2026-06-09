"""LLM-based reviewer for a composed Content Canvas.

We re-use the OpenClaw agent CLI — same runtime as the composer — with a
separate rubric prompt that scores the draft across 10 axes (0–100) and
returns per-axis critique. This is not a ground-truth scorer (an LLM grading
another LLM has known bias), but it's the cheapest way to get fast rewrite
signal inside a self-improvement loop.

Usage:

    from book_intel.composer.evaluator import evaluate_canvas
    score, verdict = evaluate_canvas(post, raw)

`verdict` shape:

    {
        "score": 0-100,
        "axes": {"structure": 0-15, "author_methodology": 0-10, ...},
        "critique": "textual critique — 4-8 sentences; what to fix next",
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from book_intel.composer.openclaw import (
    ComposerConfig,
    _extract_assistant_text,
    _parse_post_json,
    _MAX_OUTPUT_BYTES,
)

LOGGER = logging.getLogger("book_intel.composer.evaluator")

_RUBRIC = """너는 한국 블로그/도서 리뷰 편집자이자 SEO/GEO 전략가다. 아래 Content Canvas를 10개 축으로 100점 만점 채점하고, 각 축의 점수와 짧은 이유를 돌려줘. 최종 종합 점수는 각 축 가중치 합계다.

채점 축(가중치 = 만점):
1. book_structure_fidelity (15): 책의 뼈대/원인 카탈로그를 독자가 알게 해주는가. 최소 5개 핵심 개념/원인이 본문에 구체적으로 등장했는지.
2. author_methodology (10): 저자의 취재 방식/배경/이전 작업/본인 실험이 구체적으로 드러나는가.
3. concrete_citations (10): 숫자·연구·사례가 출처(연구자/기관/책 제목)와 함께 인용되었는가. 출처 없는 숫자는 감점.
4. persona_specificity (15): 타겟 페르소나에 맞는 메타포·언어·예시가 사용됐는가(engineer = CS 메타포, operator = 손익/런웨이 등).
5. density_and_repetition (10): 같은 말을 돌려하지 않는가. 1,800자에 밀도가 있는가.
6. actionable_guide (10): 실험/읽기 방법이 구체적이고 이 책 기반인가. 템플릿형 일반론은 감점.
7. eeat_experience (10): 1인칭 dogfood 또는 검증 가설이 있는가. 제3자 훈계형이면 감점.
8. timeliness (5): timely_hook이 구체적 최근 이벤트와 연결되는가. "AI 시대"류 일반론이면 감점.
9. title_body_coherence (5): 제목이 던진 질문/약속을 본문이 충실히 해결하는가.
10. faq_independence (10): FAQ가 본문을 재진술하지 않고 다른 각도(번역/전작/한국 맥락/반론)를 다루는가.

**출력은 JSON 하나만.** no code fences, no prose outside:

```
{
  "axes": {
    "book_structure_fidelity": 0-15,
    "author_methodology": 0-10,
    "concrete_citations": 0-10,
    "persona_specificity": 0-15,
    "density_and_repetition": 0-10,
    "actionable_guide": 0-10,
    "eeat_experience": 0-10,
    "timeliness": 0-5,
    "title_body_coherence": 0-5,
    "faq_independence": 0-10
  },
  "score": 0-100,
  "critique": "4-8 sentence Korean critique — focus on top 3 fixes, not re-listing strengths",
  "top_fixes": ["구체 fix 1", "구체 fix 2", "구체 fix 3"]
}
```

Draft Canvas (JSON):

```json
{{DRAFT_JSON}}
```

Book payload the draft was built from (JSON):

```json
{{RAW_JSON}}
```
"""


def evaluate_canvas(
    post: dict[str, Any],
    raw: dict[str, Any],
    *,
    config: ComposerConfig | None = None,
    session_suffix: str = "eval",
) -> tuple[int, dict[str, Any]]:
    """Score a Canvas post. Returns (score, verdict_dict). On parse failure returns (0, {...})."""

    cfg = config or ComposerConfig()
    # We pass a slimmed-down draft so the evaluator doesn't see the HTML-baked fluff.
    draft_slim = {
        k: post.get(k)
        for k in (
            "definition_lead", "title", "subtitle", "lead", "body_markdown",
            "pros", "cons", "key_takeaways", "comparisons",
            "target_reader", "not_for", "actionable_exercise", "timely_hook",
            "faq", "newsletter_snippet", "thread_tweets", "tags",
        )
    }
    raw_slim = {
        "book": raw.get("book") or {},
        "aladin": {
            "detail": {
                "author_info": (raw.get("aladin") or {}).get("detail", {}).get("author_info", ""),
                "full_description": (raw.get("aladin") or {}).get("detail", {}).get("full_description", ""),
                "toc": (raw.get("aladin") or {}).get("detail", {}).get("toc", ""),
                "description": (raw.get("aladin") or {}).get("detail", {}).get("description", ""),
                "bestseller_rank": (raw.get("aladin") or {}).get("bestseller_rank"),
            },
        },
        "naver": raw.get("naver") or {},
        "data4library": raw.get("data4library") or {},
        "youtube": raw.get("youtube") or [],
        "target_persona": raw.get("target_persona") or {},
        "persona_hints": raw.get("persona_hints") or {},
        "context": raw.get("context") or {},
        "operator_note": raw.get("operator_note") or "",
        "known_author_facts": raw.get("known_author_facts") or "",
    }
    message = (
        _RUBRIC
        .replace("{{DRAFT_JSON}}", json.dumps(draft_slim, ensure_ascii=False, indent=2))
        .replace("{{RAW_JSON}}", json.dumps(raw_slim, ensure_ascii=False, indent=2))
    )

    session_id = f"{cfg.session_prefix}-{session_suffix}-{(raw.get('book') or {}).get('isbn13', 'unknown')}"
    argv = [
        cfg.cli_path, "agent",
        "--session-id", session_id,
        "--message", message,
        "--json",
        "--thinking", cfg.thinking,
        "--timeout", str(cfg.timeout_seconds),
    ]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_seconds + 30,
            env=dict(os.environ),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 0, {"error": "evaluator timeout"}
    if completed.returncode != 0:
        return 0, {"error": f"evaluator exit {completed.returncode}: {(completed.stderr or '')[:200]}"}

    content = _extract_assistant_text(completed.stdout[:_MAX_OUTPUT_BYTES])
    if not content:
        return 0, {"error": "evaluator returned empty text"}

    verdict = _parse_verdict_json(content)
    if verdict is None:
        return 0, {"error": "evaluator output not parseable", "raw_head": content[:300]}

    score = _as_int(verdict.get("score"))
    axes = verdict.get("axes") or {}
    if not score and isinstance(axes, dict):
        score = sum(_as_int(v) for v in axes.values())
    return max(0, min(100, score)), verdict


def _parse_verdict_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Fall back to balanced-brace extraction.
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for idx in range(start, len(cleaned)):
            ch = cleaned[idx]
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
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(cleaned[start : idx + 1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break
        start = cleaned.find("{", start + 1)
    return None


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


__all__ = ["evaluate_canvas"]
