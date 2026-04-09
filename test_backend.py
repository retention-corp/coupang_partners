import json
import tempfile
import unittest
from urllib import request

from backend import build_server, serve_in_thread


class FakeAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 1,
                        "productName": "저소음 원룸 무선청소기",
                        "productPrice": 109000,
                        "productUrl": "https://example.com/1",
                        "reviewCount": 120,
                        "ratingAverage": 4.7,
                        "isRocket": True,
                        "isFreeShipping": False,
                    },
                    {
                        "productId": 2,
                        "productName": "대형 무선청소기",
                        "productPrice": 409000,
                        "productUrl": "https://example.com/2",
                        "reviewCount": 15,
                        "ratingAverage": 3.8,
                        "isRocket": False,
                        "isFreeShipping": False,
                    },
                ]
            }
        }

    def deeplink(self, urls):
        return {"data": [{"originalUrl": url, "shortUrl": "https://link.example"} for url in urls]}


class BackendTests(unittest.TestCase):
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

    def test_health_assist_events_summary_and_deeplinks(self):
        health = json.loads(request.urlopen(f"{self.base_url}/healthz", timeout=5).read().decode("utf-8"))
        self.assertTrue(health["ok"])

        assist_request = request.Request(
            f"{self.base_url}/v1/assist",
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

        event_request = request.Request(
            f"{self.base_url}/v1/events",
            data=json.dumps({"event_type": "deeplink_clicked", "query_id": assist["query_id"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        event = json.loads(request.urlopen(event_request, timeout=5).read().decode("utf-8"))
        self.assertTrue(event["ok"])

        deeplink_request = request.Request(
            f"{self.base_url}/v1/deeplinks",
            data=json.dumps({"urls": ["https://example.com/1"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        deeplinks = json.loads(request.urlopen(deeplink_request, timeout=5).read().decode("utf-8"))
        self.assertTrue(deeplinks["ok"])
        self.assertIn("data", deeplinks)

        summary = json.loads(request.urlopen(f"{self.base_url}/v1/admin/summary", timeout=5).read().decode("utf-8"))
        self.assertEqual(summary["total_queries"], 1)
        self.assertEqual(summary["total_events"], 1)


if __name__ == "__main__":
    unittest.main()
