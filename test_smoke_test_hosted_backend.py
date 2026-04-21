import importlib.util
import os
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
                        "productId": 501,
                        "productName": "클로즈드 베타 스모크 마스크",
                        "productPrice": 12000,
                        "productUrl": "https://www.coupang.com/vp/products/501",
                        "reviewCount": 25,
                        "ratingAverage": 4.7,
                    }
                ]
            }
        }

    def get_goldbox(self):
        return {"data": {"productData": [{"productId": 777, "productName": "골드박스 테스트 상품"}]}}

    def get_bestcategories(self, category_id):
        return {
            "data": {
                "productData": [
                    {"productId": 888, "categoryId": int(category_id), "productName": "베스트 테스트 상품"}
                ]
            }
        }


def load_smoke_module():
    script_path = Path(__file__).resolve().parent / "scripts" / "smoke_test_hosted_backend.py"
    spec = importlib.util.spec_from_file_location("smoke_test_hosted_backend", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SmokeTestHostedBackendTests(unittest.TestCase):
    def setUp(self):
        self.module = load_smoke_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self._saved_env = {
            "OPENCLAW_SHOPPING_API_TOKENS": os.environ.get("OPENCLAW_SHOPPING_API_TOKENS"),
            "OPENCLAW_SHOPPING_API_TOKEN": os.environ.get("OPENCLAW_SHOPPING_API_TOKEN"),
        }
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKENS", None)
        os.environ.pop("OPENCLAW_SHOPPING_API_TOKEN", None)
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
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_smoke_test_requires_production_base_url(self):
        with self.assertRaises(self.module.SmokeTestError):
            self.module.smoke_test(self.base_url, timeout=5, token=None, require_auth=False, query="mask")

    def test_smoke_test_checks_public_assist_when_auth_not_required(self):
        with mock.patch.object(self.module, "_normalize_base_url", return_value=self.base_url):
            result = self.module.smoke_test(self.base_url, timeout=5, token=None, require_auth=False, query="mask")

        self.assertTrue(result["ok"])
        self.assertFalse(result["auth_checked"])
        self.assertEqual(
            [check["name"] for check in result["checks"]],
            ["health", "public_assist", "public_goldbox", "public_best_products"],
        )

    def test_smoke_test_runs_authenticated_checks(self):
        with mock.patch.object(self.module, "_normalize_base_url", return_value=self.base_url):
            with mock.patch.dict(os.environ, {"OPENCLAW_SHOPPING_API_TOKEN": "smoke-token"}, clear=True):
                result = self.module.smoke_test(
                    self.base_url,
                    timeout=5,
                    token="smoke-token",
                    require_auth=True,
                    query="mask",
                )

        self.assertTrue(result["ok"])
        self.assertTrue(result["auth_checked"])
        self.assertEqual(
            [check["name"] for check in result["checks"]],
            ["health", "public_assist", "public_goldbox", "public_best_products", "admin_summary"],
        )


if __name__ == "__main__":
    unittest.main()
