import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from backend import build_server, serve_in_thread


class CableAdapter:
    def search_products(self, **params):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 1,
                        "productName": "AUX 케이블 3.5mm 단자 0.5m",
                        "productPrice": 4500,
                        "productUrl": "https://www.coupang.com/vp/products/1",
                    },
                    {
                        "productId": 2,
                        "productName": "AUX 케이블 10m",
                        "productPrice": 12500,
                        "productUrl": "https://www.coupang.com/vp/products/2",
                    },
                    {
                        "productId": 3,
                        "productName": "AUX 케이블 2m",
                        "productPrice": 7000,
                        "productUrl": "https://www.coupang.com/vp/products/3",
                    },
                ]
            }
        }

    def get_goldbox(self):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 901,
                        "productName": "골드박스 AUX 특가",
                        "productUrl": "https://www.coupang.com/vp/products/901",
                    }
                ]
            }
        }

    def get_bestcategories(self, category_id):
        return {
            "data": {
                "productData": [
                    {
                        "productId": 902,
                        "categoryId": int(category_id),
                        "productName": "베스트 AUX 상품",
                        "productUrl": "https://www.coupang.com/vp/products/902",
                    }
                ]
            }
        }


class OpenClawSkillTests(unittest.TestCase):
    def _load_script_globals(self):
        script_globals = {}
        with open("openclaw_skill/scripts/openclaw-shopping-skill.py", "r", encoding="utf-8") as handle:
            code = compile(handle.read(), "openclaw-shopping-skill.py", "exec")
        exec(code, script_globals)
        return script_globals

    def test_skill_defaults_to_hosted_backend(self):
        script_globals = self._load_script_globals()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(script_globals["_backend_base_url"](), "https://a.retn.kr")
            self.assertEqual(
                script_globals["_normalize_backend_base_url"]("http://127.0.0.1:9883"),
                "https://a.retn.kr",
            )

    def setUp(self):
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
            adapter=CableAdapter(),
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

    def test_skill_script_reorders_longest_cable_query(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
        env["OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND"] = "true"
        completed = subprocess.run(
            [
                sys.executable,
                "openclaw_skill/scripts/openclaw-shopping-skill.py",
                "recommend",
                "--query",
                "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘",
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        data = payload["data"]
        self.assertEqual(data["best_fit"]["product_id"], "2")
        self.assertEqual(data["best_fit"]["comparison"]["label"], "10m")
        self.assertIn("길이 표기가 가장 긴 후보", data["summary"])

    def test_skill_script_routes_unapproved_remote_backend_back_to_hosted(self):
        script_globals = self._load_script_globals()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                script_globals["_normalize_backend_base_url"]("https://evil.example.com"),
                "https://a.retn.kr",
            )

    def test_skill_script_accepts_plural_token_env(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
        env["OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND"] = "true"
        env.pop("OPENCLAW_SHOPPING_API_TOKEN", None)
        env["OPENCLAW_SHOPPING_API_TOKENS"] = "token-abc,token-def"
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "token-abc"
        completed = subprocess.run(
            [
                sys.executable,
                "openclaw_skill/scripts/openclaw-shopping-skill.py",
                "recommend",
                "--query",
                "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘",
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["best_fit"]["product_id"], "2")

    def test_skill_script_routes_localhost_override_back_to_hosted_backend(self):
        script_globals = self._load_script_globals()
        script_globals["DEFAULT_HOSTED_BACKEND"] = self.base_url

        with mock.patch.dict(os.environ, {}, clear=True):
            payload = script_globals["_post_json"](
                script_globals["_normalize_backend_base_url"]("http://127.0.0.1:9883") + "/v1/assist",
                {"query": "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘", "limit": 5},
            )

        self.assertEqual(payload["best_fit"]["product_id"], "2")

    def test_skill_script_allows_non_prod_backend_only_with_explicit_env(self):
        script_globals = self._load_script_globals()
        with mock.patch.dict(os.environ, {"OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND": "true"}, clear=True):
            self.assertEqual(
                script_globals["_normalize_backend_base_url"]("http://127.0.0.1:9883"),
                "http://127.0.0.1:9883",
            )

    def test_skill_script_fetches_goldbox_from_public_hosted_path(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
        env["OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND"] = "true"
        completed = subprocess.run(
            [sys.executable, "openclaw_skill/scripts/openclaw-shopping-skill.py", "goldbox"],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["products"][0]["productId"], 901)

    def test_skill_script_fetches_best_products_from_public_hosted_path(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
        env["OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND"] = "true"
        completed = subprocess.run(
            [
                sys.executable,
                "openclaw_skill/scripts/openclaw-shopping-skill.py",
                "best-products",
                "--category-id",
                "1039",
            ],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["category_id"], 1039)
        self.assertEqual(payload["data"]["products"][0]["categoryId"], 1039)


if __name__ == "__main__":
    unittest.main()
