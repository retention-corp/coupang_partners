import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import build_server, serve_in_thread


class FakeAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 701,
                        "productName": "에이전트 스모크 USB C 케이블",
                        "productPrice": 8900,
                        "productUrl": "https://www.coupang.com/vp/products/701",
                        "reviewCount": 120,
                        "ratingAverage": 4.7,
                    }
                ]
            }
        }


def load_agent_smoke_module():
    script_path = Path(__file__).resolve().parent / "scripts" / "agent_smoke.py"
    spec = importlib.util.spec_from_file_location("agent_smoke", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AgentSmokeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_agent_smoke_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=FakeAdapter(),
            db_path=f"{self.tempdir.name}/analytics.sqlite3",
            public_base_url="https://a.retn.kr",
        )
        self.thread = serve_in_thread(self.server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_skip_assist_checks_health_and_openapi_only(self):
        result = self.module.run_agent_smoke(
            self.base_url,
            timeout=5,
            client_id="agent-smoke",
            surface="codex",
            query="USB C 케이블",
            skip_assist=True,
            allow_non_production=True,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["assist_checked"])
        self.assertEqual([check["name"] for check in result["checks"]], ["health", "openapi"])

    def test_full_smoke_requires_short_deeplink_and_redirect(self):
        with mock.patch.object(
            self.module,
            "_check_short_redirect",
            return_value={"status": 302, "location": "https://www.coupang.com/vp/products/701"},
        ):
            result = self.module.run_agent_smoke(
                self.base_url,
                timeout=5,
                client_id="agent-smoke",
                surface="codex",
                query="USB C 케이블",
                allow_non_production=True,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["assist_checked"])
        self.assertEqual([check["name"] for check in result["checks"]], ["health", "openapi", "public_assist", "shortlink_redirect"])

    def test_classifies_allowlist_errors(self):
        code = self.module._classify_http_error(403, json.dumps({"error": "Client is not allowlisted"}))
        self.assertEqual(code, "client_not_allowlisted")

    def test_rejects_non_production_base_url_by_default(self):
        with self.assertRaises(self.module.AgentSmokeError) as ctx:
            self.module.run_agent_smoke(
                self.base_url,
                timeout=5,
                client_id="agent-smoke",
                surface="codex",
                query="USB C 케이블",
            )
        self.assertEqual(ctx.exception.error_code, "invalid_base_url")


if __name__ == "__main__":
    unittest.main()
