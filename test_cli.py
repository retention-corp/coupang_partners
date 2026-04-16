import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import bin.openclaw_shopping as openclaw_cli

from backend import build_server, serve_in_thread


class FakeAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 11,
                        "productName": "저소음 초경량 무선청소기",
                        "productPrice": 129000,
                        "productUrl": "https://www.coupang.com/vp/products/11",
                        "reviewCount": 87,
                        "ratingAverage": 4.5,
                        "isRocket": True,
                        "isFreeShipping": False,
                    }
                ]
            }
        }

    def deeplink(self, urls):
        return {"data": [{"originalUrl": url, "shortUrl": "https://link.example"} for url in urls]}


class CliTests(unittest.TestCase):
    def test_cli_defaults_to_hosted_backend(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(openclaw_cli._base_url_from_env(), "https://a.retn.kr")
            self.assertEqual(
                openclaw_cli._normalize_backend_base_url("http://127.0.0.1:9883"),
                "https://a.retn.kr",
            )

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=FakeAdapter(),
            db_path=f"{self.tempdir.name}/analytics.sqlite3",
        )
        self.thread = serve_in_thread(self.server)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tempdir.cleanup()

    def test_bin_cli_returns_json(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
        env["OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND"] = "true"
        completed = subprocess.run(
            [
                sys.executable,
                "bin/openclaw_shopping.py",
                "30만원 이하 무선청소기",
                "--must-have",
                "저소음",
                "--evidence-snippet",
                "리뷰: 조용한 편",
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["best_fit"]["product_id"], "11")

    def test_bin_cli_routes_localhost_override_back_to_hosted_backend(self):
        with mock.patch.object(openclaw_cli, "DEFAULT_HOSTED_BACKEND", self.base_url):
            with mock.patch.dict(os.environ, {}, clear=True):
                payload = openclaw_cli.request_assist(
                    "http://127.0.0.1:9883",
                    {"query": "30만원 이하 무선청소기"},
                    30,
                )

        self.assertEqual(payload["best_fit"]["product_id"], "11")

    def test_bin_cli_allows_non_prod_backend_only_with_explicit_env(self):
        with mock.patch.dict(os.environ, {"OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND": "true"}, clear=True):
            self.assertEqual(
                openclaw_cli._normalize_backend_base_url("http://127.0.0.1:9883"),
                "http://127.0.0.1:9883",
            )


if __name__ == "__main__":
    unittest.main()
