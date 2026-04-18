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
    ) -> dict[str, Any]:
        """Create a Ghost post. Returns the created post's public JSON payload.

        `status` defaults to "draft" so we never accidentally surface an unreviewed
        A/B post; callers flip to "published" explicitly. `lead` is mapped to
        `custom_excerpt` (Ghost's SEO-excerpt field) when provided.
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
        )
        url = f"{self.config.base_url}/ghost/api/admin/posts/?source=html"
        return self._request_json("POST", url, body={"posts": [payload]})

    def preview_post(
        self,
        *,
        title: str,
        lead: str | None,
        body_markdown: str,
        tags: list[str] | None = None,
        feature_image: str | None = None,
        custom_excerpt: str | None = None,
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
            "html": _markdown_to_html(body_markdown),
            "status": status,
            "tags": tag_payload,
        }
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
    in_list = False

    def _inline(text: str) -> str:
        # bold **x**
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # italic *x*
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
        # markdown link [text](url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
        return text

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.rstrip()
        if not stripped.strip():
            _close_list()
            continue
        if stripped.startswith("### "):
            _close_list()
            out.append(f"<h3>{_inline(stripped[4:].strip())}</h3>")
        elif stripped.startswith("## "):
            _close_list()
            out.append(f"<h2>{_inline(stripped[3:].strip())}</h2>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"  <li>{_inline(stripped[2:].strip())}</li>")
        else:
            _close_list()
            out.append(f"<p>{_inline(stripped)}</p>")
    _close_list()
    return "\n".join(out)


__all__ = ["GhostAdminClient", "GhostConfig", "GhostAuthError", "GhostPublishError"]
