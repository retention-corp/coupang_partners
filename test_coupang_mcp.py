import unittest

from coupang_mcp_client import CoupangMcpClient, extract_tool_result


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


if __name__ == "__main__":
    unittest.main()
