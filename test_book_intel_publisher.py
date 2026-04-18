"""Tests for book_intel.publisher.ghost — JWT signing, payload shape, HTML conversion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest import mock

from book_intel.publisher.ghost import (
    GhostAdminClient,
    GhostAuthError,
    GhostConfig,
    GhostPublishError,
    _markdown_to_html,
)


def _make_config(kid: str = "abc123", secret_hex: str = "deadbeef" * 8) -> GhostConfig:
    return GhostConfig(base_url="https://example.com", admin_key=f"{kid}:{secret_hex}")


class GhostConfigFromEnvTests(unittest.TestCase):
    def tearDown(self) -> None:
        for k in ("RETN_ME_GHOST_URL", "RETN_ME_GHOST_ADMIN_KEY", "GHOST_ADMIN_URL", "GHOST_ADMIN_KEY"):
            os.environ.pop(k, None)

    def test_missing_url_raises(self) -> None:
        os.environ["RETN_ME_GHOST_ADMIN_KEY"] = "kid:hex"
        with self.assertRaises(GhostAuthError):
            GhostConfig.from_env()

    def test_malformed_key_raises(self) -> None:
        os.environ["RETN_ME_GHOST_URL"] = "https://x"
        os.environ["RETN_ME_GHOST_ADMIN_KEY"] = "no-colon"
        with self.assertRaises(GhostAuthError):
            GhostConfig.from_env()

    def test_well_formed(self) -> None:
        os.environ["RETN_ME_GHOST_URL"] = "https://retn.kr"
        os.environ["RETN_ME_GHOST_ADMIN_KEY"] = "kid:aabbcc"
        config = GhostConfig.from_env()
        self.assertEqual(config.base_url, "https://retn.kr")
        self.assertEqual(config.admin_key, "kid:aabbcc")


class JWTTests(unittest.TestCase):
    def test_jwt_parts_and_signature(self) -> None:
        config = _make_config()
        client = GhostAdminClient(config=config)
        token = client._build_jwt()
        header_seg, claims_seg, sig_seg = token.split(".")

        def _b64url_decode(seg: str) -> bytes:
            padding = 4 - (len(seg) % 4)
            if padding != 4:
                seg = seg + "=" * padding
            return base64.urlsafe_b64decode(seg)

        header = json.loads(_b64url_decode(header_seg))
        claims = json.loads(_b64url_decode(claims_seg))
        self.assertEqual(header["alg"], "HS256")
        self.assertEqual(header["kid"], "abc123")
        self.assertEqual(claims["aud"], "/admin/")
        self.assertGreater(claims["exp"], claims["iat"])
        # Verify signature
        secret = bytes.fromhex("deadbeef" * 8)
        expected = hmac.new(secret, f"{header_seg}.{claims_seg}".encode(), hashlib.sha256).digest()
        expected_seg = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        self.assertEqual(sig_seg, expected_seg)

    def test_hex_parse_failure_raises_auth(self) -> None:
        config = GhostConfig(base_url="https://x", admin_key="kid:NOT-HEX!!")
        client = GhostAdminClient(config=config)
        with self.assertRaises(GhostAuthError):
            client._build_jwt()


class PayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GhostAdminClient(config=_make_config())

    def test_preview_payload_shape(self) -> None:
        preview = self.client.preview_post(
            title="제목",
            lead="리드 요약",
            body_markdown="## 헤딩\n\n본문 단락입니다.\n\n- 포인트 A\n- 포인트 B",
            tags=["자기계발", "세이노"],
        )
        body = preview["body"]["posts"][0]
        self.assertEqual(body["title"], "제목")
        self.assertEqual(body["status"], "draft")
        self.assertEqual([t["name"] for t in body["tags"]], ["자기계발", "세이노"])
        self.assertIn("<h2>헤딩</h2>", body["html"])
        self.assertIn("<li>포인트 A</li>", body["html"])
        self.assertEqual(body["custom_excerpt"], "리드 요약")

    def test_missing_title_raises(self) -> None:
        with self.assertRaises(GhostPublishError):
            self.client.preview_post(title="", lead="x", body_markdown="body")

    def test_missing_body_raises(self) -> None:
        with self.assertRaises(GhostPublishError):
            self.client.preview_post(title="t", lead=None, body_markdown="")


class MarkdownConversionTests(unittest.TestCase):
    def test_paragraph(self) -> None:
        self.assertEqual(_markdown_to_html("hello"), "<p>hello</p>")

    def test_h2_and_h3(self) -> None:
        self.assertIn("<h2>Title</h2>", _markdown_to_html("## Title"))
        self.assertIn("<h3>Sub</h3>", _markdown_to_html("### Sub"))

    def test_bullet_list(self) -> None:
        html = _markdown_to_html("- one\n- two\n\nafter")
        self.assertIn("<ul>", html)
        self.assertIn("<li>one</li>", html)
        self.assertIn("</ul>", html)
        self.assertIn("<p>after</p>", html)

    def test_bold_italic_link(self) -> None:
        html = _markdown_to_html("한국어 **강조** 와 *이탤릭* 그리고 [링크](https://x)")
        self.assertIn("<strong>강조</strong>", html)
        self.assertIn("<em>이탤릭</em>", html)
        self.assertIn('<a href="https://x">링크</a>', html)


class TransportErrorTests(unittest.TestCase):
    def test_http_error_bubbles_as_publish_error(self) -> None:
        client = GhostAdminClient(config=_make_config())
        import urllib.error as _err, io as _io
        err = _err.HTTPError("u", 401, "Unauthorized", {}, _io.BytesIO(b'{"errors":[{"message":"bad key"}]}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(GhostPublishError):
                client.create_post(title="t", lead="l", body_markdown="body")


if __name__ == "__main__":
    unittest.main()
