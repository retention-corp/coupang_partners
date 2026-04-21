import json
import os
import tempfile
import unittest
from urllib import error, request

from analytics import AnalyticsStore
from backend import _Handler, ShoppingBackend, build_server, serve_in_thread
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

    def get_bestcategories(self, category_id):
        return {
            "data": [
                {
                    "categoryId": int(category_id),
                    "productId": 101,
                    "productName": "카테고리 베스트 상품",
                    "productPrice": 19900,
                    "productUrl": "https://www.coupang.com/vp/products/101",
                }
            ]
        }

    def get_goldbox(self):
        return {
            "data": [
                {
                    "productId": 99,
                    "productName": "골드박스 상품",
                    "productPrice": 9900,
                    "productUrl": "https://www.coupang.com/vp/products/99",
                }
            ]
        }

    def get_goldbox(self):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 101,
                        "productName": "오늘의 골드박스",
                        "productPrice": 19900,
                        "productUrl": "https://www.coupang.com/vp/products/101",
                    }
                ]
            }
        }

    def get_bestcategories(self, category_id):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 201,
                        "categoryId": int(category_id),
                        "productName": "카테고리 베스트 상품",
                        "productPrice": 29900,
                        "productUrl": "https://www.coupang.com/vp/products/201",
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
        self.assertEqual(summary["total_events"], 1)
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


if __name__ == "__main__":
    unittest.main()
