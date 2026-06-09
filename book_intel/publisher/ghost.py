"""Ghost Admin API client (stdlib JWT).

Ghost authenticates admin calls with a short-lived HS256 JWT signed by the admin
key secret. The admin key env var is `RETN_ME_GHOST_ADMIN_KEY` (or the more
generic `GHOST_ADMIN_KEY` alias), formatted `<kid>:<hex-secret>`. We build the
JWT on every call (5-minute expiry) so a leaked token has minimal utility.

Only the POST /posts/ endpoint is needed for the book_intel orchestrator. The
client exposes `create_post` (live) and `preview_post` (dry-run) so the
orchestrator can gate `--live` behind explicit intent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("book_intel.publisher.ghost")

_API_VERSION = "v5.0"
_JWT_EXPIRY_SECONDS = 5 * 60  # Ghost recommends ≤5min


class GhostAuthError(RuntimeError):
    """Raised when the admin key is missing or malformed."""


class GhostPublishError(RuntimeError):
    """Raised when the Ghost API returns a non-2xx response."""


@dataclass
class GhostConfig:
    base_url: str
    admin_key: str              # "<kid>:<hex secret>"
    audience: str = "/admin/"   # the only legal value for the admin API
    timeout_seconds: float = 20.0
    user_agent: str = "book_intel/0.1 (+https://retn.kr)"

    @classmethod
    def from_env(cls) -> "GhostConfig":
        base = (
            os.getenv("RETN_ME_GHOST_URL")
            or os.getenv("GHOST_ADMIN_URL")
            or ""
        ).strip().rstrip("/")
        key = (
            os.getenv("RETN_ME_GHOST_ADMIN_KEY")
            or os.getenv("GHOST_ADMIN_KEY")
            or ""
        ).strip()
        if not base:
            raise GhostAuthError("RETN_ME_GHOST_URL / GHOST_ADMIN_URL not set")
        if not key or ":" not in key:
            raise GhostAuthError("RETN_ME_GHOST_ADMIN_KEY is missing or malformed (expected kid:secret)")
        return cls(base_url=base, admin_key=key)


class GhostAdminClient:
    """Thin admin-API client supporting the post-create flow."""

    def __init__(self, config: GhostConfig | None = None) -> None:
        self.config = config or GhostConfig.from_env()

    # --- public API -------------------------------------------------------

    def create_post(
        self,
        *,
        title: str,
        lead: str | None,
        body_markdown: str,
        tags: list[str] | None = None,
        status: str = "draft",
        feature_image: str | None = None,
        custom_excerpt: str | None = None,
        author_id: str | None = None,
        canvas: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a Ghost post. Returns the created post's public JSON payload.

        `status` defaults to "draft" so we never accidentally surface an unreviewed
        A/B post; callers flip to "published" explicitly. `lead` is mapped to
        `custom_excerpt` (Ghost's SEO-excerpt field) when provided. `canvas` is
        the Content Canvas sidecar (FAQ, JSON-LD, takeaways, comparisons…) —
        injected into the HTML below the body so the post surfaces GEO/SGE
        structured data without a separate CMS integration.
        """

        payload = self._build_payload(
            title=title,
            lead=lead,
            body_markdown=body_markdown,
            tags=tags,
            status=status,
            feature_image=feature_image,
            custom_excerpt=custom_excerpt,
            author_id=author_id,
            canvas=canvas,
        )
        url = f"{self.config.base_url}/ghost/api/admin/posts/?source=html"
        return self._request_json("POST", url, body={"posts": [payload]})

    def update_post(
        self,
        *,
        post_id: str,
        updated_at: str,
        title: str | None = None,
        lead: str | None = None,
        body_markdown: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        feature_image: str | None = None,
        custom_excerpt: str | None = None,
        canvas: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH an existing post. `updated_at` is required by Ghost for optimistic locking.

        Only keys we actually set end up in the payload; every other field Ghost
        holds stays untouched. For body edits we still regenerate HTML from the
        full markdown + canvas so the FAQ / JSON-LD stays in sync.
        """

        post: dict[str, Any] = {"updated_at": updated_at}
        if title is not None:
            post["title"] = title.strip()
        if body_markdown is not None:
            post["html"] = _compose_html(body_markdown, canvas=canvas or {})
            head = _build_jsonld_head(canvas or {})
            if head:
                post["codeinjection_head"] = head
        if tags is not None:
            post["tags"] = [{"name": str(t).strip()} for t in tags if str(t).strip()]
        if status is not None:
            post["status"] = status
        if feature_image is not None:
            post["feature_image"] = feature_image
        excerpt = custom_excerpt or lead
        if excerpt is not None:
            post["custom_excerpt"] = excerpt.strip()[:300]

        url = f"{self.config.base_url}/ghost/api/admin/posts/{post_id}/?source=html"
        return self._request_json("PUT", url, body={"posts": [post]})

    def preview_post(
        self,
        *,
        title: str,
        lead: str | None,
        body_markdown: str,
        tags: list[str] | None = None,
        feature_image: str | None = None,
        custom_excerpt: str | None = None,
        canvas: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the payload that would be POSTed without hitting the network."""

        payload = self._build_payload(
            title=title,
            lead=lead,
            body_markdown=body_markdown,
            tags=tags,
            status="draft",
            feature_image=feature_image,
            custom_excerpt=custom_excerpt,
            author_id=None,
            canvas=canvas,
        )
        return {"url": f"{self.config.base_url}/ghost/api/admin/posts/?source=html", "body": {"posts": [payload]}}

    # --- internals --------------------------------------------------------

    def _build_payload(
        self,
        *,
        title: str,
        lead: str | None,
        body_markdown: str,
        tags: list[str] | None,
        status: str,
        feature_image: str | None,
        custom_excerpt: str | None,
        author_id: str | None,
        canvas: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise GhostPublishError("title is required")
        body_markdown = (body_markdown or "").strip()
        if not body_markdown:
            raise GhostPublishError("body_markdown is required")

        tag_payload = []
        for tag in tags or []:
            cleaned = str(tag).strip()
            if cleaned:
                tag_payload.append({"name": cleaned})

        post: dict[str, Any] = {
            "title": title,
            "html": _compose_html(body_markdown, canvas=canvas or {}),
            "status": status,
            "tags": tag_payload,
        }
        head = _build_jsonld_head(canvas or {})
        if head:
            post["codeinjection_head"] = head
        if feature_image:
            post["feature_image"] = feature_image
        excerpt = custom_excerpt or lead
        if excerpt:
            post["custom_excerpt"] = excerpt.strip()[:300]
        if author_id:
            post["authors"] = [{"id": author_id}]
        return post

    def _request_json(self, method: str, url: str, *, body: dict[str, Any] | None) -> dict[str, Any]:
        payload_bytes = None
        headers = {
            "Authorization": f"Ghost {self._build_jwt()}",
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
            "Accept-Version": _API_VERSION,
        }
        if body is not None:
            payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else exc.reason
            raise GhostPublishError(f"Ghost API {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GhostPublishError(f"Ghost API transport error: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception as exc:
            raise GhostPublishError(f"Ghost returned non-JSON body: {exc}") from exc

    def _build_jwt(self) -> str:
        kid, secret_hex = self.config.admin_key.split(":", 1)
        try:
            secret_bytes = bytes.fromhex(secret_hex)
        except ValueError as exc:
            raise GhostAuthError("admin key secret is not valid hex") from exc
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": kid}
        claims = {
            "iat": now,
            "exp": now + _JWT_EXPIRY_SECONDS,
            "aud": self.config.audience,
        }
        header_seg = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        claims_seg = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_seg}.{claims_seg}".encode("ascii")
        signature = hmac.new(secret_bytes, signing_input, hashlib.sha256).digest()
        return f"{header_seg}.{claims_seg}.{_b64url(signature)}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _inline_md(text: str) -> str:
    """Convert a single string's inline Markdown (bold/italic/link) to HTML,
    while escaping HTML entities in the plain-text parts. Used for bullets
    and FAQ items that contain [text](url) but must not show the raw
    Markdown syntax in Ghost's rendered output.
    """

    import re as _re
    # Escape HTML first
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    # bold **x**
    safe = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    # italic *x* (negative lookaround so **bold** isn't double-matched)
    safe = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", safe)
    # markdown link [text](url)
    safe = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', safe)
    return safe


def _compose_html(body_markdown: str, *, canvas: dict[str, Any]) -> str:
    """Render body_markdown → HTML and append Canvas-derived structured sections.

    Canvas sections (rendered only if present on the payload):
      - pros/cons panel
      - key takeaways ul
      - comparisons ul
      - target_reader / not_for ul
      - actionable exercise blockquote
      - FAQ <details> blocks
      - JSON-LD <script type="application/ld+json"> (Review + FAQPage)

    All additions live *below* the main body so if a reader bounces at scroll,
    the core review is read first; AI Overview crawlers pick up the schema
    regardless of visual order.
    """

    html_parts: list[str] = [_markdown_to_html(body_markdown)]

    pros = canvas.get("pros") or []
    cons = canvas.get("cons") or []
    if pros or cons:
        html_parts.append('<div class="canvas-proscons">')
        if pros:
            html_parts.append("<h2>장점</h2>")
            html_parts.append("<ul>")
            for p in pros:
                html_parts.append(f"  <li>{_inline_md(p)}</li>")
            html_parts.append("</ul>")
        if cons:
            html_parts.append("<h2>단점</h2>")
            html_parts.append("<ul>")
            for c in cons:
                html_parts.append(f"  <li>{_inline_md(c)}</li>")
            html_parts.append("</ul>")
        html_parts.append("</div>")

    takeaways = canvas.get("key_takeaways") or []
    if takeaways:
        html_parts.append("<h2>핵심 요약</h2>")
        html_parts.append("<ul>")
        for t in takeaways:
            html_parts.append(f"  <li>{_inline_md(t)}</li>")
        html_parts.append("</ul>")

    comparisons = canvas.get("comparisons") or []
    if comparisons:
        # Build a title→short_deeplink lookup from similar_books_with_coupang
        # so any bare book title (that the composer forgot to link) gets wrapped.
        similar_map: dict[str, str] = {}
        for entry in canvas.get("similar_books_with_coupang") or []:
            if not isinstance(entry, dict):
                continue
            short = entry.get("coupang_short_deeplink") or entry.get("coupang_deeplink")
            if not short:
                continue
            # Strip 《 》 wrappers so the match is on the plain title.
            title = (entry.get("title") or "").strip().strip("《》").strip()
            if title:
                similar_map[title] = short
        html_parts.append("<h2>비슷한 책과의 비교</h2>")
        html_parts.append("<ul>")
        for c in comparisons:
            rendered = _inline_md(c)
            # If the bullet still mentions a bare book title 《X》 without an
            # <a> wrap, inject the short_deeplink.
            for title, short in similar_map.items():
                bare = f"《{title}》"
                if bare in rendered and f'href="{short}"' not in rendered:
                    rendered = rendered.replace(
                        bare,
                        f'《<a href="{_escape_html(short)}">{_escape_html(title)}</a>》',
                        1,
                    )
            html_parts.append(f"  <li>{rendered}</li>")
        html_parts.append("</ul>")

    target_reader = canvas.get("target_reader") or []
    not_for = canvas.get("not_for") or []
    if target_reader or not_for:
        html_parts.append("<h2>이 책, 누가 읽으면 좋을까</h2>")
        if target_reader:
            html_parts.append("<p><strong>추천 독자</strong></p><ul>")
            for t in target_reader:
                html_parts.append(f"  <li>{_inline_md(t)}</li>")
            html_parts.append("</ul>")
        if not_for:
            html_parts.append("<p><strong>추천하지 않는 독자</strong></p><ul>")
            for n in not_for:
                html_parts.append(f"  <li>{_inline_md(n)}</li>")
            html_parts.append("</ul>")

    exercise = canvas.get("actionable_exercise")
    if exercise:
        html_parts.append("<h2>이 책의 주장, 48시간으로 검증해볼 수 있다</h2>")
        html_parts.append(_markdown_to_html(exercise))

    timely = canvas.get("timely_hook")
    if timely:
        html_parts.append("<h2>지금 읽어야 하는 이유</h2>")
        html_parts.append(f"<p>{_inline_md(timely)}</p>")

    # Primary affiliate CTA. Canvas passes through `primary_coupang_deeplink`
    # (or `primary_coupang_short_deeplink`) when the orchestrator/regen script
    # attached one. If it's missing we skip silently — better than a broken CTA.
    primary_cta = canvas.get("primary_coupang_short_deeplink") or canvas.get("primary_coupang_deeplink")
    book_title = canvas.get("book_title") or "이 책"
    if primary_cta:
        html_parts.append('<p class="coupang-cta"><strong>'
                          f'<a href="{_escape_html(primary_cta)}">쿠팡에서 《{_escape_html(book_title)}》 보기</a>'
                          '</strong></p>')
        html_parts.append('<p class="coupang-disclosure"><em>파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음</em></p>')

    faq = canvas.get("faq") or []
    if faq:
        # LLM-friendly shape: explicit <h3> questions + Schema.org microdata on
        # Question / Answer nodes. AI Overview crawlers and LLM scrapers parse
        # this pattern more reliably than <details>/<summary>. JSON-LD is also
        # in codeinjection_head, so schema signal is redundant+robust.
        html_parts.append('<h2>자주 묻는 질문</h2>')
        html_parts.append('<div itemscope itemtype="https://schema.org/FAQPage">')
        for item in faq:
            if not isinstance(item, dict):
                continue
            q = item.get("q") or item.get("question")
            a = item.get("a") or item.get("answer")
            if not (q and a):
                continue
            html_parts.append('  <div itemprop="mainEntity" itemscope itemtype="https://schema.org/Question">')
            html_parts.append(f'    <h3 itemprop="name">{_inline_md(str(q))}</h3>')
            html_parts.append('    <div itemprop="acceptedAnswer" itemscope itemtype="https://schema.org/Answer">')
            html_parts.append(f'      <p itemprop="text">{_inline_md(str(a))}</p>')
            html_parts.append('    </div>')
            html_parts.append('  </div>')
        html_parts.append('</div>')

    # JSON-LD is handled via `codeinjection_head` (see _build_jsonld_head) — Ghost
    # sanitizes <script> inside body HTML, so injecting it here wouldn't survive.

    return "\n".join(html_parts)


def _build_jsonld_head(canvas: dict[str, Any]) -> str:
    """Return the `<script type=application/ld+json>` block(s) for codeinjection_head."""

    blocks: list[str] = []
    for key in ("schema_jsonld", "faq_jsonld"):
        obj = canvas.get(key)
        if isinstance(obj, dict) and obj:
            try:
                payload = json.dumps(obj, ensure_ascii=False)
            except Exception:
                continue
            # Per HTML spec, `<` inside a <script> can close the tag if followed
            # by a letter or `/`; `\u003c` is the safe escape.
            payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
            blocks.append(f'<script type="application/ld+json">{payload}</script>')
    return "\n".join(blocks)


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _markdown_to_html(markdown: str) -> str:
    """Very small Markdown → HTML adapter.

    Ghost's admin API accepts raw HTML via `source=html` (already on the URL),
    or a mobiledoc JSON payload. We go the HTML route for simplicity; the
    converter covers just the subset our composer prompts produce: headings
    (`##`, `###`), paragraphs, bullet lists, bold (`**`), italic (`*`), and
    Markdown links. Ghost's editor re-parses on view, so imperfect HTML is
    OK — as long as we don't break encoding.
    """

    import re
    lines = markdown.splitlines()
    out: list[str] = []
    list_tag: str | None = None
    # Table state — when we see a line starting with `|`, buffer the block and
    # flush as <table> when the run ends. A minimum row is `|cell|cell|` and
    # the separator line looks like `| --- | --- |`.
    table_buffer: list[str] = []
    blockquote_buffer: list[str] = []

    def _inline(text: str) -> str:
        # bold **x**
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # italic *x*
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
        # markdown link [text](url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
        return text

    def _close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def _open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag == tag:
            return
        _close_list()
        out.append(f"<{tag}>")
        list_tag = tag

    def _flush_blockquote() -> None:
        nonlocal blockquote_buffer
        if not blockquote_buffer:
            return
        inner = _markdown_to_html("\n".join(blockquote_buffer))
        out.append(f"<blockquote>{inner}</blockquote>")
        blockquote_buffer = []

    def _flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = [
            [_inline(cell.strip()) for cell in row.strip().strip("|").split("|")]
            for row in table_buffer
            if row.strip()
        ]
        table_buffer = []
        if not rows:
            return
        # If the second row is a separator (cells are all dashes), it's a proper
        # markdown table with a header row.
        header_row = None
        body_rows = rows
        if len(rows) >= 2 and all(re.fullmatch(r":?-+:?", c.strip()) for c in rows[1]):
            header_row = rows[0]
            body_rows = rows[2:]
        out.append("<table>")
        if header_row:
            out.append("  <thead><tr>")
            for cell in header_row:
                out.append(f"    <th>{cell}</th>")
            out.append("  </tr></thead>")
        out.append("  <tbody>")
        for row in body_rows:
            out.append("    <tr>")
            for cell in row:
                out.append(f"      <td>{cell}</td>")
            out.append("    </tr>")
        out.append("  </tbody>")
        out.append("</table>")

    for line in lines:
        stripped = line.rstrip()
        content = stripped.lstrip()
        if not stripped.strip():
            _flush_blockquote()
            _close_list()
            _flush_table()
            continue
        if content.startswith(">"):
            _flush_table()
            _close_list()
            blockquote_buffer.append(content[1:].lstrip())
            continue
        _flush_blockquote()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            table_buffer.append(stripped)
            continue
        else:
            _flush_table()
        if content.startswith("#### "):
            _close_list()
            out.append(f"<h4>{_inline(content[5:].strip())}</h4>")
        elif content.startswith("### "):
            _close_list()
            out.append(f"<h3>{_inline(content[4:].strip())}</h3>")
        elif content.startswith("## "):
            _close_list()
            out.append(f"<h2>{_inline(content[3:].strip())}</h2>")
        elif content.startswith("- ") or content.startswith("* "):
            _open_list("ul")
            out.append(f"  <li>{_inline(content[2:].strip())}</li>")
        elif match := re.match(r"^(\d+)\.\s+(.*)$", content):
            _open_list("ol")
            out.append(f"  <li>{_inline(match.group(2).strip())}</li>")
        else:
            _close_list()
            out.append(f"<p>{_inline(content)}</p>")
    _flush_blockquote()
    _close_list()
    _flush_table()
    return "\n".join(out)


__all__ = ["GhostAdminClient", "GhostConfig", "GhostAuthError", "GhostPublishError"]
