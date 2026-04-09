import json
import os
import subprocess
import sys
import tempfile
import unittest

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
                        "productUrl": "https://example.com/11",
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


if __name__ == "__main__":
    unittest.main()
