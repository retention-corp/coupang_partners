import json
import os
import sqlite3
import tempfile
import unittest
from urllib import error, request

from analytics import AnalyticsStore
from backend import _Handler, ShoppingBackend, _extract_products, build_server, serve_in_thread
from security import RateLimiter


class FakeAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 1,
                        "productName": "저소음 원룸 무선청소기",
                        "productPrice": 109000,
                        "productUrl": "https://www.coupang.com/vp/products/1",
                        "reviewCount": 120,
                        "ratingAverage": 4.7,
                        "isRocket": True,
                        "isFreeShipping": False,
                    },
                    {
                        "productId": 2,
                        "productName": "대형 무선청소기",
                        "productPrice": 409000,
                        "productUrl": "https://www.coupang.com/vp/products/2",
                        "reviewCount": 15,
                        "ratingAverage": 3.8,
                        "isRocket": False,
                        "isFreeShipping": False,
                    },
                ]
            }
        }

    def deeplink(self, urls):
        return {"data": [{"originalUrl": url} for url in urls]}

    def get_goldbox(self):
        return {
            "data": {
                "products": [
                    {
                        "item": {
                            "productId": 101,
                            "productName": "오늘의 골드박스",
                            "productPrice": 19900,
                            "productUrl": "https://www.coupang.com/vp/products/101",
                        }
                    }
                ]
            }
        }

    def get_bestcategories(self, category_id):
        return {
            "data": {
                "bestCategories": [
                    {
                        "categoryId": int(category_id),
                        "products": [
                            {
                                "productId": 201,
                                "productName": "카테고리 베스트 상품",
                                "productPrice": 29900,
                                "productUrl": "https://www.coupang.com/vp/products/201",
                            }
                        ],
                    }
                ]
            }
        }


class CableAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 1,
                        "productName": "AUX 케이블 0.5m",
                        "productPrice": 4500,
                        "productUrl": "https://example.com/1",
                    },
                    {
                        "productId": 2,
                        "productName": "AUX 케이블 10m",
                        "productPrice": 12500,
                        "productUrl": "https://example.com/2",
                    },
                    {
                        "productId": 3,
                        "productName": "AUX 케이블 2m",
                        "productPrice": 7000,
                        "productUrl": "https://example.com/3",
                    },
                ]
            }
        }


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._saved_env = {
            "OPENCLAW_SHOPPING_API_TOKENS": os.environ.get("OPENCLAW_SHOPPING_API_TOKENS"),
            "OPENCLAW_SHOPPING_API_TOKEN": os.environ.get("OPENCLAW_SHOPPING_API_TOKEN"),
            "OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES": os.environ.get("OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES"),
            "OPENCLAW_SHOPPING_PUBLIC_BASE_URL": os.environ.get("OPENCLAW_SHOPPING_PUBLIC_BASE_URL"),
            "OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED": os.environ.get("OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"),
            "OPENCLAW_SHOPPING_CLIENT_ALLOWLIST": os.environ.get("OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_PUBLIC"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_AUTH": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_AUTH"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_ADMIN": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_REQUESTS_ADMIN"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_PUBLIC": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_PUBLIC"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_AUTH": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_AUTH"),
            "OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_ADMIN": os.environ.get("OPENCLAW_SHOPPING_RATE_LIMIT_WINDOW_SECONDS_ADMIN"),
        }
        for key in self._saved_env:
            os.environ.pop(key, None)
        os.environ["OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES"] = "true"
        _Handler.rate_limiter = RateLimiter(window_seconds=60, max_requests=30)
        _Handler.public_rate_limiter = None
        _Handler.authenticated_rate_limiter = None
        _Handler.admin_rate_limiter = None
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=FakeAdapter(),
            db_path=f"{self.tempdir.name}/analytics.sqlite3",
            public_base_url="https://go.example.com",
        )
        self.thread = serve_in_thread(self.server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_health_assist_events_summary_and_deeplinks(self):
        health = json.loads(request.urlopen(f"{self.base_url}/health", timeout=5).read().decode("utf-8"))
        self.assertTrue(health["ok"])

        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps(
                {
                    "query": "30만원 이하 무선청소기, 원룸용",
                    "constraints": {"must_have": ["저소음"], "avoid": ["대형"]},
                    "evidence_snippets": [{"text": "리뷰: 자취방에 잘 맞음", "source": "manual"}],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assist = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        self.assertEqual(assist["best_fit"]["product_id"], "1")
        self.assertTrue(assist["best_fit"]["short_deeplink"].startswith("https://go.example.com/s/"))

        event_request = request.Request(
            f"{self.base_url}/internal/v1/events",
            data=json.dumps({"event_type": "deeplink_clicked", "query_id": assist["query_id"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        event = json.loads(request.urlopen(event_request, timeout=5).read().decode("utf-8"))
        self.assertTrue(event["ok"])

        deeplink_request = request.Request(
            f"{self.base_url}/internal/v1/deeplinks",
            data=json.dumps({"urls": ["https://www.coupang.com/vp/products/1"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        deeplinks = json.loads(request.urlopen(deeplink_request, timeout=5).read().decode("utf-8"))
        self.assertTrue(deeplinks["ok"])
        self.assertIn("data", deeplinks)
        self.assertTrue(
            deeplinks["data"]["data"][0]["shortenedShareUrl"].startswith("https://go.example.com/s/")
        )

        summary = json.loads(request.urlopen(f"{self.base_url}/v1/admin/summary", timeout=5).read().decode("utf-8"))
        self.assertEqual(summary["total_queries"], 1)
        # 2 events: 1 attribution-tagged `assist` (new in spec T1), 1 deeplink_clicked.
        self.assertEqual(summary["total_events"], 2)
        self.assertEqual(summary["total_short_links"], 1)
        self.assertIn("economics", summary)
        self.assertEqual(summary["economics"]["funnel"]["total_queries"], 1)
        self.assertEqual(summary["economics"]["funnel"]["deeplink_click_events"], 1)
        self.assertIn("scenarios", summary["economics"])

    def test_short_link_redirect(self):
        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assist = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        slug = assist["best_fit"]["short_deeplink"].rsplit("/", 1)[-1]

        class NoRedirectHandler(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = request.build_opener(NoRedirectHandler)
        with self.assertRaises(error.HTTPError) as ctx:
            opener.open(f"{self.base_url}/s/{slug}", timeout=5)
        response = ctx.exception
        self.assertEqual(response.code, 302)
        self.assertEqual(response.headers["Location"], "https://www.coupang.com/vp/products/1")
        response.close()

    def test_public_goldbox_is_credentialless(self):
        response = json.loads(request.urlopen(f"{self.base_url}/v1/public/goldbox", timeout=5).read().decode("utf-8"))

        self.assertTrue(response["ok"])
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["products"][0]["productId"], 101)
        self.assertTrue(response["products"][0]["short_url"].startswith("https://go.example.com/s/"))

    def test_public_best_products_is_credentialless(self):
        response = json.loads(
            request.urlopen(f"{self.base_url}/v1/public/best-products?categoryId=1039", timeout=5).read().decode("utf-8")
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["category_id"], 1039)
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["products"][0]["categoryId"], 1039)
        self.assertTrue(response["products"][0]["short_url"].startswith("https://go.example.com/s/"))

    def test_assist_requires_bearer_token_when_configured(self):
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        response = ctx.exception
        self.assertEqual(response.code, 401)
        response.close()

    def test_assist_accepts_valid_bearer_token(self):
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token-123",
                "X-OpenClaw-Client-Id": "test-client",
            },
            method="POST",
        )
        response = json.loads(request.urlopen(request_obj, timeout=5).read().decode("utf-8"))
        self.assertIn("requestId", response)
        self.assertEqual(response["best_fit"]["product_id"], "1")

    def test_assist_accepts_singular_token_env(self):
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKENS", None)
        os.environ["OPENCLAW_SHOPPING_API_TOKEN"] = "token-123"
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token-123",
                "X-OpenClaw-Client-Id": "test-client",
            },
            method="POST",
        )
        response = json.loads(request.urlopen(request_obj, timeout=5).read().decode("utf-8"))
        self.assertEqual(response["best_fit"]["product_id"], "1")

    def test_internal_path_requires_auth_even_when_public_base_url_is_non_local(self):
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKENS", None)
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKEN", None)
        os.environ["OPENCLAW_SHOPPING_PUBLIC_BASE_URL"] = "https://a.retn.kr"

        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()

    def test_rate_limit_returns_429(self):
        _Handler.rate_limiter = RateLimiter(window_seconds=60, max_requests=1)
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "burst"},
            method="POST",
        )
        request.urlopen(request_obj, timeout=5).read()
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        response = ctx.exception
        self.assertEqual(response.code, 429)
        response.close()

    def test_allowlist_blocks_unknown_client(self):
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"] = "true"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"] = "approved-client"
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "blocked-client"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    def test_public_allowlist_blocks_unknown_client(self):
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"] = "true"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"] = "approved-client"
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "blocked-client"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    def test_public_allowlist_accepts_prefix_wildcard_client(self):
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"] = "true"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"] = "openclaw-skill-*,hermes-agent"
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Client-Id": "openclaw-skill-localhash",
            },
            method="POST",
        )
        response = json.loads(request.urlopen(request_obj, timeout=5).read().decode("utf-8"))
        self.assertIn("best_fit", response)

    def test_allowlisted_public_clients_get_separate_rate_buckets(self):
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"] = "true"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"] = "agent-a,agent-b"
        _Handler.public_rate_limiter = RateLimiter(window_seconds=60, max_requests=1)
        request_a = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "agent-a"},
            method="POST",
        )
        request_b = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "agent-b"},
            method="POST",
        )

        request.urlopen(request_a, timeout=5).read()
        request.urlopen(request_b, timeout=5).read()
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_a, timeout=5)
        self.assertEqual(ctx.exception.code, 429)
        ctx.exception.close()

    def test_public_and_authenticated_buckets_are_separate(self):
        _Handler.rate_limiter = RateLimiter(window_seconds=60, max_requests=30)
        _Handler.public_rate_limiter = RateLimiter(window_seconds=60, max_requests=1)
        _Handler.authenticated_rate_limiter = RateLimiter(window_seconds=60, max_requests=2)

        public_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "public-client"},
            method="POST",
        )
        request.urlopen(public_request, timeout=5).read()
        with self.assertRaises(error.HTTPError) as public_ctx:
            request.urlopen(public_request, timeout=5)
        self.assertEqual(public_ctx.exception.code, 429)
        public_ctx.exception.close()

        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"
        auth_request = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token-123",
                "X-OpenClaw-Client-Id": "auth-client",
            },
            method="POST",
        )
        request.urlopen(auth_request, timeout=5).read()
        request.urlopen(auth_request, timeout=5).read()
        with self.assertRaises(error.HTTPError) as auth_ctx:
            request.urlopen(auth_request, timeout=5)
        self.assertEqual(auth_ctx.exception.code, 429)
        auth_ctx.exception.close()

    def test_internal_deeplinks_reject_non_coupang_urls(self):
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/deeplinks",
            data=json.dumps({"urls": ["https://evil.example.com/phish"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        response = ctx.exception
        self.assertEqual(response.code, 400)
        response.close()

    def test_assist_rejects_invalid_constraints_type(self):
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기", "constraints": "bad"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_assist_rejects_large_request_body(self):
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=b"{" + b"x" * 70000 + b"}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 413)
        ctx.exception.close()

    def test_assist_rejects_invalid_evidence_snippet_member_type(self):
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기", "evidence_snippets": [123]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_assist_falls_back_to_original_link_when_shortener_fails(self):
        class FailingShortener:
            def shorten(self, url):
                raise RuntimeError("shortener offline")

        backend = ShoppingBackend(
            adapter=FakeAdapter(),
            analytics_store=AnalyticsStore(f"{self.tempdir.name}/fallback.sqlite3"),
            shortener=FailingShortener(),
        )
        response = backend.assist({"query": "30만원 이하 무선청소기"})
        self.assertEqual(response["best_fit"]["short_deeplink"], "https://www.coupang.com/vp/products/1")

    def test_assist_degrades_gracefully_on_coupang_api_error(self):
        from client import CoupangApiError

        class FailingAdapter:
            def search_products(self, **params):
                raise CoupangApiError(429, {"rCode": "ERROR", "rMessage": "rate limited"})

        backend = ShoppingBackend(
            adapter=FailingAdapter(),
            analytics_store=AnalyticsStore(f"{self.tempdir.name}/degraded.sqlite3"),
        )
        response = backend.assist({"query": "30만원 이하 무선청소기"})
        # Empty shortlist instead of HTTP 500 — contract k-skill relies on.
        self.assertEqual(response["shortlist"], [])
        self.assertIsNone(response["best_fit"])
        self.assertEqual(response["degraded"], "coupang_api_status_429")

    def test_assist_does_not_shorten_invalid_recommendation_targets(self):
        class InvalidUrlAdapter(FakeAdapter):
            def search_products(self, **params):
                payload = super().search_products(**params)
                payload["data"]["productData"][0]["productUrl"] = "https://evil.example.com/1"
                return payload

        backend = ShoppingBackend(
            adapter=InvalidUrlAdapter(),
            analytics_store=AnalyticsStore(f"{self.tempdir.name}/invalid-url.sqlite3"),
            shortener=self.server.RequestHandlerClass.backend.shortener,
        )
        response = backend.assist({"query": "30만원 이하 무선청소기"})
        self.assertEqual(response["best_fit"]["short_deeplink"], "https://evil.example.com/1")

    def test_assist_ranks_longest_cable_for_extremum_search(self):
        backend = ShoppingBackend(
            adapter=CableAdapter(),
            analytics_store=AnalyticsStore(f"{self.tempdir.name}/cables.sqlite3"),
        )

        response = backend.assist({"query": "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘"})

        self.assertEqual(response["normalized_intent"]["intent_type"], "extremum_search")
        self.assertEqual(response["best_fit"]["product_id"], "2")
        self.assertEqual(response["best_fit"]["comparison"]["label"], "10m")
        self.assertIn("길이 표기가 가장 긴 후보", response["summary"])

    def test_public_assist_ignores_internal_token_requirement(self):
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"
        request_obj = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-OpenClaw-Client-Id": "public-client"},
            method="POST",
        )
        response = json.loads(request.urlopen(request_obj, timeout=5).read().decode("utf-8"))
        self.assertEqual(response["best_fit"]["product_id"], "1")

    def test_public_best_products_is_tokenless_and_shortens_links(self):
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"

        response = json.loads(
            request.urlopen(f"{self.base_url}/v1/public/best-products?categoryId=1001", timeout=5).read().decode("utf-8")
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["category_id"], 1001)
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["products"][0]["categoryId"], 1001)
        self.assertTrue(response["products"][0]["short_url"].startswith("https://go.example.com/s/"))

    def test_internal_best_products_requires_bearer_token(self):
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-123"

        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(f"{self.base_url}/internal/v1/best-products?categoryId=1001", timeout=5)

        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()

    def test_best_products_rejects_non_integer_category_id(self):
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(f"{self.base_url}/v1/public/best-products?categoryId=abc", timeout=5)

        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_operator_routes_can_be_disabled(self):
        os.environ["OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES"] = "false"
        request_obj = request.Request(
            f"{self.base_url}/internal/v1/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer token-123"},
            method="POST",
        )
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(request_obj, timeout=5)
        self.assertEqual(ctx.exception.code, 404)
        ctx.exception.close()

    # ------------------------------------------------------------------ #
    # T1 attribution header parsing + analytics persistence (spec D9/R1.1/R1.5)
    # ------------------------------------------------------------------ #

    def _read_assist_events(self):
        """Return rows from `events` with `event_type='assist'`, newest first.

        The assist events are emitted by the attribution-tagging middleware added in T1 —
        one per /v1/public/assist POST — and carry the 4 attribution columns.
        """

        db_path = f"{self.tempdir.name}/analytics.sqlite3"
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT event_type, surface, surface_raw, client_id, client_version, utm_source "
                "FROM events WHERE event_type = 'assist' ORDER BY created_at DESC"
            ).fetchall()
        return rows

    def test_attribution_headers_happy_path_persists_all_four_values(self):
        """Spec R1.1: 3 headers + utm_source query must all end up on the assist event."""

        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist?utm_source=x-daily-builder",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Surface": "chatgpt-gpt",
                "X-OpenClaw-Client-Id": "uuid-123",
                "X-OpenClaw-Version": "1.2.3",
            },
            method="POST",
        )
        response = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        self.assertIn("best_fit", response)

        rows = self._read_assist_events()
        self.assertEqual(len(rows), 1)
        event_type, surface, surface_raw, client_id, client_version, utm_source = rows[0]
        self.assertEqual(event_type, "assist")
        self.assertEqual(surface, "chatgpt-gpt")
        self.assertIsNone(surface_raw)
        self.assertEqual(client_id, "uuid-123")
        self.assertEqual(client_version, "1.2.3")
        self.assertEqual(utm_source, "x-daily-builder")

    def test_attribution_unknown_surface_normalizes_and_preserves_raw(self):
        """Spec R1.5: unrecognized surface → 'unknown' + raw preserved + request succeeds."""

        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Surface": "some-weird-value",
                "X-OpenClaw-Client-Id": "uuid-456",
            },
            method="POST",
        )
        # Must still return 200 — warn, don't reject.
        response = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        self.assertIn("best_fit", response)

        rows = self._read_assist_events()
        self.assertEqual(len(rows), 1)
        _, surface, surface_raw, client_id, _, _ = rows[0]
        self.assertEqual(surface, "unknown")
        self.assertEqual(surface_raw, "some-weird-value")
        self.assertEqual(client_id, "uuid-456")

    # ------------------------------------------------------------------ #
    # T2 OpenAPI spec + /docs surface (spec R1.2)
    # ------------------------------------------------------------------ #

    def test_openapi_json_documents_public_endpoints_and_attribution_headers(self):
        """Spec R1.2: /openapi.json must be a valid OpenAPI 3.1 doc that documents
        the /v1/public/assist endpoint with all four attribution parameters (3 headers
        + utm_source query). GPT Store and Claude Code skill submissions rely on this."""

        response = request.urlopen(f"{self.base_url}/openapi.json", timeout=5)
        self.assertEqual(response.status, 200)
        content_type = response.headers.get("Content-Type", "")
        self.assertIn("application/json", content_type)
        body = json.loads(response.read().decode("utf-8"))
        response.close()

        # Top-level shape.
        self.assertTrue(body["openapi"].startswith("3.1"), f"unexpected openapi version: {body['openapi']!r}")
        self.assertIn("info", body)
        self.assertIn("title", body["info"])
        self.assertIn("version", body["info"])
        self.assertIn("servers", body)
        self.assertTrue(
            any(server.get("url") == "https://a.retn.kr" for server in body["servers"]),
            "Production server https://a.retn.kr must be in servers[]",
        )

        # Paths cover every public route exposed by the current backend.
        paths = body.get("paths", {})
        for expected in (
            "/health",
            "/v1/public/assist",
            "/v1/public/search",
            "/v1/public/goldbox",
            "/v1/public/best/{category_id}",
            "/s/{slug}",
        ):
            self.assertIn(expected, paths, f"expected path {expected} in OpenAPI doc")

        # /v1/public/assist must document the 4 attribution parameters (3 headers + utm_source).
        assist_post = paths["/v1/public/assist"]["post"]
        params = assist_post.get("parameters", [])
        param_names = {(p.get("name"), p.get("in")) for p in params}
        for expected_param in (
            ("x-openclaw-surface", "header"),
            ("x-openclaw-client-id", "header"),
            ("x-openclaw-version", "header"),
            ("utm_source", "query"),
        ):
            self.assertIn(
                expected_param,
                param_names,
                f"assist endpoint missing attribution param {expected_param}",
            )

        # 200/400/429 responses defined for the assist endpoint.
        responses_block = assist_post.get("responses", {})
        for code in ("200", "400", "429"):
            self.assertIn(code, responses_block)

    def test_docs_serves_swagger_ui_pointing_at_openapi_json(self):
        """Spec R1.2: /docs returns HTML that loads Swagger UI (or equivalent) and
        points it at /openapi.json so developers can explore the API in a browser."""

        response = request.urlopen(f"{self.base_url}/docs", timeout=5)
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers.get("Content-Type", ""))
        body = response.read().decode("utf-8")
        response.close()
        # Must reference /openapi.json and include at least one <script> or <link>.
        self.assertIn("/openapi.json", body)
        self.assertTrue(
            "<script" in body or "<link" in body,
            "docs page must include script/link tag for the UI bundle",
        )

    def test_attribution_missing_client_id_falls_back_to_anonymous_hash(self):
        """Spec D9: missing x-openclaw-client-id → deterministic 'anonymous-<ip hash>'."""

        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # Intentionally no X-OpenClaw-Client-Id.
                "X-OpenClaw-Surface": "cli",
            },
            method="POST",
        )
        response = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        self.assertIn("best_fit", response)

        rows = self._read_assist_events()
        self.assertEqual(len(rows), 1)
        _, surface, _, client_id, _, _ = rows[0]
        self.assertEqual(surface, "cli")
        self.assertTrue(client_id.startswith("anonymous-"), f"expected anonymous fallback, got {client_id!r}")
        # Hash body should be hex and nonempty (length of sha1 truncation).
        self.assertEqual(len(client_id), len("anonymous-") + 16)


class ResponseCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._saved_ttl = os.environ.get("OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS")

    def tearDown(self):
        self.tempdir.cleanup()
        if self._saved_ttl is None:
            os.environ.pop("OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS", None)
        else:
            os.environ["OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS"] = self._saved_ttl

    def _build_backend(self, adapter):
        return ShoppingBackend(
            adapter=adapter,
            analytics_store=AnalyticsStore(f"{self.tempdir.name}/a.sqlite3"),
        )

    def test_goldbox_response_cached_within_ttl(self):
        os.environ["OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS"] = "900"

        class Counter:
            calls = 0

            def get_goldbox(self_inner):
                Counter.calls += 1
                return {"data": [{"productId": 10, "productName": "딜", "productPrice": 1000, "productUrl": "https://www.coupang.com/vp/products/10"}]}

        backend = self._build_backend(Counter())
        first = backend.goldbox()
        second = backend.goldbox()
        self.assertEqual(Counter.calls, 1)
        self.assertEqual(first["data"]["deals"], second["data"]["deals"])

    def test_best_response_cached_per_category(self):
        os.environ["OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS"] = "900"

        class Counter:
            calls: dict = {}

            def get_bestcategories(self_inner, category_id):
                Counter.calls[category_id] = Counter.calls.get(category_id, 0) + 1
                return {"data": [{"productId": int(category_id), "productName": f"cat-{category_id}", "productPrice": 2000, "productUrl": f"https://www.coupang.com/vp/products/{category_id}"}]}

        backend = self._build_backend(Counter())
        backend.best("1001")
        backend.best("1001")
        backend.best("1002")
        self.assertEqual(Counter.calls, {"1001": 1, "1002": 1})

    def test_cache_disabled_when_ttl_zero(self):
        os.environ["OPENCLAW_SHOPPING_RESPONSE_CACHE_TTL_SECONDS"] = "0"

        class Counter:
            calls = 0

            def get_goldbox(self_inner):
                Counter.calls += 1
                return {"data": []}

        backend = self._build_backend(Counter())
        backend.goldbox()
        backend.goldbox()
        self.assertEqual(Counter.calls, 2)


# ---------------------------------------------------------------------------- #
# T5: shortlink redirect attribution handler tests.
# Real-world baseline (2026-04-21): 6 raw redirects, Coupang reported 1 click.
# Investigation found every remote_addr was the Google FE proxy link-local IP.
# These tests pin that the handler now captures XFF+UA on the stored event row.
# ---------------------------------------------------------------------------- #
class ShortlinkAttributionHandlerTests(unittest.TestCase):
    """Own server+DB fixture — independent from BackendTests so we don't re-run its tests."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._saved_env = {
            "OPENCLAW_SHOPPING_API_TOKENS": os.environ.get("OPENCLAW_SHOPPING_API_TOKENS"),
            "OPENCLAW_SHOPPING_API_TOKEN": os.environ.get("OPENCLAW_SHOPPING_API_TOKEN"),
            "OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES": os.environ.get("OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES"),
        }
        for key in self._saved_env:
            os.environ.pop(key, None)
        os.environ["OPENCLAW_SHOPPING_ENABLE_OPERATOR_ROUTES"] = "true"
        _Handler.rate_limiter = RateLimiter(window_seconds=60, max_requests=30)
        _Handler.public_rate_limiter = None
        _Handler.authenticated_rate_limiter = None
        _Handler.admin_rate_limiter = None
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=FakeAdapter(),
            db_path=f"{self.tempdir.name}/analytics.sqlite3",
            public_base_url="https://go.example.com",
        )
        self.thread = serve_in_thread(self.server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _read_shortlink_redirect_events(self):
        db_path = f"{self.tempdir.name}/analytics.sqlite3"
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT client_ip, user_agent, proxy_ip, metadata_json "
                "FROM events WHERE event_type = 'shortlink_redirect' "
                "ORDER BY created_at ASC"
            ).fetchall()
        return rows

    def _seed_slug(self):
        assist_request = request.Request(
            f"{self.base_url}/v1/public/assist",
            data=json.dumps({"query": "30만원 이하 무선청소기"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assist = json.loads(request.urlopen(assist_request, timeout=5).read().decode("utf-8"))
        return assist["best_fit"]["short_deeplink"].rsplit("/", 1)[-1]

    def test_shortlink_redirect_logs_xff_and_ua(self):
        slug = self._seed_slug()

        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        req_obj = request.Request(
            f"{self.base_url}/s/{slug}",
            headers={
                "X-Forwarded-For": "1.2.3.4, 5.6.7.8",
                "User-Agent": "curl/7.88.1",
            },
        )
        opener = request.build_opener(NoRedirect)
        with self.assertRaises(error.HTTPError) as ctx:
            opener.open(req_obj, timeout=5)
        ctx.exception.close()

        rows = self._read_shortlink_redirect_events()
        self.assertEqual(len(rows), 1)
        client_ip, user_agent, proxy_ip, _ = rows[0]
        self.assertEqual(client_ip, "1.2.3.4")
        self.assertEqual(user_agent, "curl/7.88.1")
        # proxy_ip is whatever the TCP peer looks like from a loopback connection —
        # we just assert it's present and distinct from the XFF client_ip.
        self.assertTrue(proxy_ip)
        self.assertNotEqual(proxy_ip, client_ip)

    def test_shortlink_redirect_fallback_without_xff(self):
        slug = self._seed_slug()

        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        req_obj = request.Request(
            f"{self.base_url}/s/{slug}",
            headers={"User-Agent": "probe/1.0"},
        )
        opener = request.build_opener(NoRedirect)
        with self.assertRaises(error.HTTPError) as ctx:
            opener.open(req_obj, timeout=5)
        ctx.exception.close()

        rows = self._read_shortlink_redirect_events()
        self.assertEqual(len(rows), 1)
        client_ip, user_agent, proxy_ip, _ = rows[0]
        # Without XFF, client_ip == TCP peer.
        self.assertEqual(client_ip, proxy_ip)
        self.assertTrue(client_ip)
        self.assertEqual(user_agent, "probe/1.0")

    def test_admin_proxy_gmv_endpoint_returns_written_rows(self):
        """Part 6: GET /v1/admin/gmv/proxy surfaces rows the weekly batch wrote."""

        # Seed one shortlink_redirect + run the batch so the admin endpoint has data.
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        from economics import KST, compute_weekly_proxy_gmv

        db_path = f"{self.tempdir.name}/analytics.sqlite3"
        week_start = _dt(2026, 4, 20, 0, 0, 0, tzinfo=KST)
        click_time_utc = _dt(2026, 4, 21, 12, 0, 0, tzinfo=KST).astimezone(_tz.utc)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "INSERT INTO events ("
                "id, event_type, metadata_json, created_at, "
                "surface, client_ip, user_agent, proxy_ip"
                ") VALUES (?, 'shortlink_redirect', ?, ?, 'cli', '1.2.3.4', 'curl', '169.254.169.126')",
                (
                    str(_uuid.uuid4()),
                    '{"slug": "xdZ9Rzh"}',
                    click_time_utc.isoformat(),
                ),
            )
        compute_weekly_proxy_gmv(week_start, db_path=db_path)

        req_obj = request.Request(
            f"{self.base_url}/v1/admin/gmv/proxy?week_start=2026-04-20"
        )
        payload = json.loads(request.urlopen(req_obj, timeout=5).read().decode("utf-8"))
        self.assertIn("rows", payload)
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["surface"], "cli")
        self.assertEqual(payload["rows"][0]["label"], "attributed_estimate")

    def test_admin_click_reconciliation_endpoint_returns_null_when_missing(self):
        """Part 6: GET /v1/admin/click-reconciliation?date=... returns 200 with null row."""

        req_obj = request.Request(
            f"{self.base_url}/v1/admin/click-reconciliation?date=2026-04-21"
        )
        payload = json.loads(request.urlopen(req_obj, timeout=5).read().decode("utf-8"))
        self.assertEqual(payload["date"], "2026-04-21")
        self.assertIsNone(payload["row"])

    def test_shortlink_redirect_truncates_long_user_agent(self):
        slug = self._seed_slug()

        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        long_ua = "A" * 500
        req_obj = request.Request(
            f"{self.base_url}/s/{slug}",
            headers={"User-Agent": long_ua},
        )
        opener = request.build_opener(NoRedirect)
        with self.assertRaises(error.HTTPError) as ctx:
            opener.open(req_obj, timeout=5)
        ctx.exception.close()

        rows = self._read_shortlink_redirect_events()
        self.assertEqual(len(rows), 1)
        _, user_agent, _, _ = rows[0]
        self.assertEqual(len(user_agent), 200)


class ExtractProductsTests(unittest.TestCase):
    """Unit tests for _extract_products covering all known Coupang response shapes."""

    def test_none_returns_empty(self):
        self.assertEqual(_extract_products(None), [])

    def test_empty_dict_returns_empty(self):
        self.assertEqual(_extract_products({}), [])

    def test_plain_list_returns_as_is(self):
        items = [{"productId": 1}, {"productId": 2}]
        self.assertEqual(_extract_products(items), items)

    def test_data_is_list(self):
        """Coupang goldbox/bestcategories often return {"data": [...]}."""
        items = [{"productId": 1, "productName": "A"}]
        self.assertEqual(_extract_products({"data": items}), items)

    def test_data_dict_with_productData(self):
        """search_products returns {"data": {"productData": [...]}}."""
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"data": {"productData": items}}), items)

    def test_data_dict_with_products(self):
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"data": {"products": items}}), items)

    def test_data_dict_with_items(self):
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"data": {"items": items}}), items)

    def test_top_level_products_key(self):
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"products": items}), items)

    def test_top_level_productData_key(self):
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"productData": items}), items)

    def test_top_level_items_key(self):
        items = [{"productId": 1}]
        self.assertEqual(_extract_products({"items": items}), items)

    def test_data_is_empty_list(self):
        self.assertEqual(_extract_products({"data": []}), [])

    def test_envelope_with_rCode_and_data_list(self):
        """Full Coupang envelope: {"rCode": "0", "rMessage": "", "data": [...]}."""
        items = [{"productId": 99, "productName": "골드박스"}]
        payload = {"rCode": "0", "rMessage": "", "data": items}
        self.assertEqual(_extract_products(payload), items)

    def test_data_is_non_list_non_dict_returns_empty(self):
        self.assertEqual(_extract_products({"data": "unexpected"}), [])

    def test_no_recognized_keys_returns_empty(self):
        self.assertEqual(_extract_products({"foo": "bar"}), [])


if __name__ == "__main__":
    unittest.main()
