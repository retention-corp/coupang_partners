import importlib.util
import json
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "make_my_deeplink",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "make_my_deeplink.py"),
)
make_my_deeplink = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_my_deeplink)

DeeplinkError = make_my_deeplink.DeeplinkError
PRODUCT_URL = "https://www.coupang.com/vp/products/1234567890?itemId=999&vendorItemId=888"


class FakeResponse:
    def __init__(self, url, body=""):
        self._url = url
        self._body = body.encode("utf-8")

    def geturl(self):
        return self._url

    def read(self, size=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def opener_for(pages):
    """pages: {requested_url: (landed_url, body)}"""

    def _opener(http_request, timeout=None):
        landed, body = pages[http_request.full_url]
        return FakeResponse(landed, body)

    return _opener


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def deeplink(self, coupang_urls, *, sub_id=None):
        self.calls.append((list(coupang_urls), sub_id))
        return self.response


class NormalizeProductUrlTests(unittest.TestCase):
    def test_drops_other_partners_tracking_params(self):
        tracked = (
            "https://www.coupang.com/vp/products/1234567890"
            "?itemId=999&vendorItemId=888&lptag=AF1234567&itime=20260907&traceid=V0-abc&pageKey=1234567890"
        )
        normalized = make_my_deeplink.normalize_product_url(tracked)
        self.assertEqual(normalized, PRODUCT_URL)
        self.assertNotIn("lptag", normalized)

    def test_upgrades_mobile_host_and_strips_fragment(self):
        normalized = make_my_deeplink.normalize_product_url(
            "https://m.coupang.com/vp/products/1234567890?itemId=999#reviews"
        )
        self.assertEqual(normalized, "https://www.coupang.com/vp/products/1234567890?itemId=999")

    def test_product_id_extraction(self):
        self.assertEqual(make_my_deeplink.product_id_of(PRODUCT_URL), "1234567890")
        self.assertIsNone(make_my_deeplink.product_id_of("https://www.coupang.com/np/campaigns/82"))


class ResolveUrlTests(unittest.TestCase):
    def test_passes_through_product_url_without_network(self):
        def explode(*_args, **_kwargs):
            raise AssertionError("should not hit the network")

        self.assertEqual(make_my_deeplink.resolve_url(PRODUCT_URL, opener=explode), PRODUCT_URL)

    def test_follows_http_redirect_to_product_page(self):
        short = "https://link.coupang.com/a/gQxaDKRrXw"
        opener = opener_for({short: (PRODUCT_URL, "<html>product</html>")})
        self.assertEqual(make_my_deeplink.resolve_url(short, opener=opener), PRODUCT_URL)

    def test_follows_meta_refresh_interstitial(self):
        short = "https://link.coupang.com/a/gQxaDKRrXw"
        interstitial = "https://link.coupang.com/re/AFFSDP?lptag=AF1234567"
        opener = opener_for(
            {
                short: (
                    short,
                    f'<html><head><meta http-equiv="refresh" content="0;url={interstitial}"></head></html>',
                ),
                interstitial: (PRODUCT_URL, "<html>product</html>"),
            }
        )
        self.assertEqual(make_my_deeplink.resolve_url(short, opener=opener), PRODUCT_URL)

    def test_rejects_non_coupang_input(self):
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.resolve_url("https://example.com/vp/products/1")

    def test_raises_when_short_link_never_lands(self):
        short = "https://link.coupang.com/a/gQxaDKRrXw"
        opener = opener_for({short: (short, "<html>no redirect here</html>")})
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.resolve_url(short, opener=opener)


class FakeShortener:
    def __init__(self, base="https://a.retn.kr"):
        self.base = base
        self.calls = []

    def shorten(self, url):
        self.calls.append(url)
        return f"{self.base}/s/abc1234"


class DirectModeTests(unittest.TestCase):
    def test_mints_deeplink_and_own_short_link_from_short_link(self):
        short = "https://link.coupang.com/a/gQxaDKRrXw"
        tracked_landing = PRODUCT_URL + "&lptag=AF1234567&itime=20260907"
        opener = opener_for({short: (tracked_landing, "<html>product</html>")})
        client = FakeClient(
            {
                "data": [
                    {
                        "originalUrl": PRODUCT_URL,
                        "shortenUrl": "https://link.coupang.com/a/MINE01",
                        "landingUrl": "https://link.coupang.com/re/AFFSDP?lptag=AF9999999",
                    }
                ]
            }
        )
        shortener = FakeShortener()

        results = make_my_deeplink.convert(
            [short], mode="direct", client=client, shortener=shortener, opener=opener
        )

        self.assertEqual(client.calls, [([PRODUCT_URL], None)])
        self.assertEqual(shortener.calls, ["https://link.coupang.com/a/MINE01"])
        self.assertEqual(results[0]["coupang_deeplink"], "https://link.coupang.com/a/MINE01")
        self.assertEqual(results[0]["share_url"], "https://a.retn.kr/s/abc1234")
        self.assertEqual(results[0]["normalized_url"], PRODUCT_URL)
        self.assertEqual(results[0]["product_id"], "1234567890")
        self.assertEqual(results[0]["mode"], "direct")
        self.assertEqual(results[0]["disclosure"], make_my_deeplink.DISCLOSURE_TEXT)

    def test_sub_id_is_forwarded_to_the_partners_api(self):
        client = FakeClient({"data": [{"shortenUrl": "https://link.coupang.com/a/MINE02"}]})
        make_my_deeplink.convert(
            [PRODUCT_URL],
            mode="direct",
            sub_id="blog-2026",
            resolve=False,
            client=client,
            shortener=FakeShortener(),
        )
        self.assertEqual(client.calls, [([PRODUCT_URL], "blog-2026")])

    def test_invalid_sub_id_is_rejected_before_any_call(self):
        client = FakeClient({"data": []})
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.convert([PRODUCT_URL], mode="direct", sub_id="bad id!", resolve=False, client=client)
        self.assertEqual(client.calls, [])

    def test_falls_back_to_coupang_url_when_shortener_fails(self):
        class BrokenShortener:
            def shorten(self, url):
                raise RuntimeError("firestore down")

        client = FakeClient({"data": [{"shortenUrl": "https://link.coupang.com/a/MINE03"}]})
        results = make_my_deeplink.convert(
            [PRODUCT_URL], mode="direct", resolve=False, client=client, shortener=BrokenShortener()
        )
        self.assertEqual(results[0]["share_url"], "https://link.coupang.com/a/MINE03")

    def test_rejects_unexpected_api_shape(self):
        client = FakeClient({"rCode": "ERROR"})
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.convert([PRODUCT_URL], mode="direct", resolve=False, client=client)


class BackendModeTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("OPENCLAW_SHOPPING_API_TOKEN", "OPENCLAW_SHOPPING_API_TOKENS", "OPENCLAW_SHOPPING_OPS_BASE_URL")
        }
        os.environ["OPENCLAW_SHOPPING_API_TOKEN"] = "operator-token"
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKENS", None)
        os.environ["OPENCLAW_SHOPPING_OPS_BASE_URL"] = "https://ops.example"

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_posts_to_operator_backend_and_returns_own_short_link(self):
        captured = {}

        def opener(http_request, timeout=None):
            captured["url"] = http_request.full_url
            captured["auth"] = http_request.get_header("Authorization")
            captured["payload"] = json.loads(http_request.data.decode("utf-8"))
            body = {
                "ok": True,
                "data": {
                    "data": [
                        {
                            "originalUrl": PRODUCT_URL,
                            "shortenUrl": "https://link.coupang.com/a/MINE04",
                            "shortenedShareUrl": "https://a.retn.kr/s/xyz9876",
                        }
                    ]
                },
            }
            return FakeResponse("https://ops.example/v1/deeplinks", json.dumps(body))

        results = make_my_deeplink.convert(
            [PRODUCT_URL], sub_id="newsletter", resolve=False, opener=opener
        )

        self.assertEqual(captured["url"], "https://ops.example/v1/deeplinks")
        self.assertEqual(captured["auth"], "Bearer operator-token")
        self.assertEqual(captured["payload"], {"urls": [PRODUCT_URL], "subId": "newsletter"})
        self.assertEqual(results[0]["mode"], "backend")
        self.assertEqual(results[0]["share_url"], "https://a.retn.kr/s/xyz9876")

    def test_backend_mode_requires_a_token(self):
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKEN", None)
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.convert([PRODUCT_URL], mode="backend", resolve=False)

    def test_non_https_ops_base_url_is_rejected(self):
        os.environ["OPENCLAW_SHOPPING_OPS_BASE_URL"] = "http://ops.example"
        with self.assertRaises(DeeplinkError):
            make_my_deeplink.convert([PRODUCT_URL], mode="backend", resolve=False)


if __name__ == "__main__":
    unittest.main()
