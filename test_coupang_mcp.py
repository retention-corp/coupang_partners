import unittest
from unittest.mock import MagicMock, patch

from coupang_mcp_client import (
    CoupangMcpClient,
    McpError,
    _HostedAssistClient,
    extract_tool_result,
)


class FakePartnersClient:
    def search_products(self, **params):
        keyword = params["keyword"]
        return {
            "data": {
                "productData": [
                    {
                        "productId": 1,
                        "productName": f"{keyword} 일반",
                        "productPrice": 10000,
                        "isRocket": False,
                    },
                    {
                        "productId": 2,
                        "productName": f"{keyword} 로켓",
                        "productPrice": 20000,
                        "isRocket": True,
                    },
                ]
            }
        }

    def get_goldbox(self):
        return {"data": [{"productId": 99, "productName": "골드박스 상품"}]}

    def get_bestcategories(self, category_id):
        return {"data": [{"categoryId": int(category_id), "productName": "베스트 상품"}]}


class CoupangMcpTests(unittest.TestCase):
    def test_initialize_and_call_tool(self):
        client = CoupangMcpClient(partners_client=FakePartnersClient())
        init_response = client.initialize()
        self.assertTrue(init_response.session_id)

        tool_response = client.call_tool("search_coupang_products", {"keyword": "생수"})
        result = extract_tool_result(tool_response.payload)

        self.assertEqual(result[0]["productName"], "생수 일반")
        self.assertEqual(result[1]["productName"], "생수 로켓")

    def test_tools_list_after_initialize(self):
        client = CoupangMcpClient(partners_client=FakePartnersClient())
        response = client.tools_list()
        tool_names = [item["name"] for item in response.payload["result"]["tools"]]
        self.assertIn("get_coupang_best_products", tool_names)
        self.assertIn("get_coupang_goldbox", tool_names)

    def test_rocket_and_budget_tools_filter_results(self):
        client = CoupangMcpClient(partners_client=FakePartnersClient())

        rocket = extract_tool_result(client.call_tool("search_coupang_rocket", {"keyword": "생수"}).payload)
        budget = extract_tool_result(
            client.call_tool(
                "search_coupang_budget",
                {"keyword": "생수", "min_price": 15000, "max_price": 25000},
            ).payload
        )

        self.assertEqual(len(rocket), 1)
        self.assertTrue(rocket[0]["isRocket"])
        self.assertEqual(len(budget), 1)
        self.assertEqual(budget[0]["productPrice"], 20000)


def _make_hosted_response(items: list) -> MagicMock:
    """Return a fake urllib response for a hosted /v1/public/assist call.

    Mirrors the real assist response: flat JSON with top-level `best_fit`
    and `shortlist` (no `ok`/`data` envelope).
    """
    payload = {"shortlist": list(items)}
    if items:
        payload["best_fit"] = items[0]
    body = json.dumps(payload).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


_SAMPLE_ITEMS = [
    {
        "product_id": "A1",
        "title": "생수 500ml",
        "price": 5000,
        "short_deeplink": "https://a.retn.kr/s/abc",
        "deeplink": "https://link.coupang.com/abc",
        "rating": 4.5,
        "review_count": 100,
        "vendor": "쿠팡",
        "metadata": {"productImage": "https://img/a.jpg", "isRocket": False},
    },
    {
        "product_id": "A2",
        "title": "생수 2L 로켓",
        "price": 12000,
        "short_deeplink": "https://a.retn.kr/s/def",
        "deeplink": "https://link.coupang.com/def",
        "rating": 4.8,
        "review_count": 500,
        "vendor": "쿠팡",
        "metadata": {"productImage": "https://img/b.jpg", "isRocket": True},
    },
]


import json  # noqa: E402 — needed for _make_hosted_response above


class CoupangMcpHostedFallbackTests(unittest.TestCase):

    def _patch_urlopen(self, items):
        return patch(
            "coupang_mcp_client.request.urlopen",
            return_value=_make_hosted_response(items),
        )

    def test_search_falls_back_to_hosted_when_creds_missing(self):
        with self._patch_urlopen(_SAMPLE_ITEMS):
            # No partners_client injected; COUPANG_ACCESS_KEY not set in test env
            client = CoupangMcpClient()
            # Force the hosted path by clearing any cached client
            client._partners_client = _HostedAssistClient()
            result = extract_tool_result(
                client.call_tool("search_coupang_products", {"keyword": "생수"}).payload
            )
        self.assertEqual(result[0]["productName"], "생수 500ml")
        self.assertEqual(result[0]["productPrice"], 5000)
        self.assertEqual(result[0]["productUrl"], "https://a.retn.kr/s/abc")

    def test_rocket_filters_hosted_results_by_metadata_isRocket(self):
        with self._patch_urlopen(_SAMPLE_ITEMS):
            client = CoupangMcpClient()
            client._partners_client = _HostedAssistClient()
            result = extract_tool_result(
                client.call_tool("search_coupang_rocket", {"keyword": "생수"}).payload
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["productName"], "생수 2L 로켓")
        self.assertTrue(result[0]["isRocket"])

    def test_budget_filter_applied_on_hosted_results(self):
        with self._patch_urlopen(_SAMPLE_ITEMS):
            client = CoupangMcpClient()
            client._partners_client = _HostedAssistClient()
            result = extract_tool_result(
                client.call_tool(
                    "search_coupang_budget",
                    {"keyword": "생수", "min_price": 8000, "max_price": 15000},
                ).payload
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["productPrice"], 12000)

    def test_compare_splits_terms_and_queries_hosted_per_term(self):
        with self._patch_urlopen(_SAMPLE_ITEMS):
            client = CoupangMcpClient()
            client._partners_client = _HostedAssistClient()
            result = extract_tool_result(
                client.call_tool(
                    "compare_coupang_products", {"keyword": "생수 vs 탄산수"}
                ).payload
            )
        self.assertIn("terms", result)
        self.assertIn("results", result)
        self.assertEqual(len(result["terms"]), 2)
        self.assertIn("생수", result["terms"])
        self.assertIn("탄산수", result["terms"])

    def test_goldbox_without_creds_raises_clear_error(self):
        client = CoupangMcpClient()
        client._partners_client = _HostedAssistClient()
        with self.assertRaises(McpError) as ctx:
            client.call_tool("get_coupang_goldbox", {})
        self.assertIn("COUPANG_ACCESS_KEY", str(ctx.exception))

    def test_local_path_still_used_when_partners_client_injected(self):
        fake = FakePartnersClient()
        client = CoupangMcpClient(partners_client=fake)
        result = extract_tool_result(
            client.call_tool("search_coupang_products", {"keyword": "노트북"}).payload
        )
        self.assertEqual(result[0]["productName"], "노트북 일반")


if __name__ == "__main__":
    unittest.main()
