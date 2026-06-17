import json
import unittest

from coupang_mcp_server import handle_message


class FakeClient:
    def call_tool(self, name, arguments):
        class Response:
            payload = {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                [{"productName": arguments.get("keyword"), "productUrl": "https://a.retn.kr/s/abc"}],
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            }

        self.last_call = (name, arguments)
        return Response()


class CoupangMcpServerTests(unittest.TestCase):
    def test_initialize_returns_server_info(self):
        response = handle_message(FakeClient(), {"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        self.assertEqual(response["result"]["serverInfo"]["name"], "retn-coupang-shopping")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tools_list_exposes_search_tool_schema(self):
        response = handle_message(FakeClient(), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("search_coupang_products", names)
        self.assertIn("search_coupang_budget", names)

    def test_tools_call_dispatches_to_client(self):
        client = FakeClient()
        response = handle_message(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_coupang_products", "arguments": {"keyword": "무선청소기"}},
            },
        )

        self.assertEqual(client.last_call, ("search_coupang_products", {"keyword": "무선청소기"}))
        text = response["result"]["content"][0]["text"]
        self.assertIn("https://a.retn.kr/s/abc", text)


if __name__ == "__main__":
    unittest.main()
