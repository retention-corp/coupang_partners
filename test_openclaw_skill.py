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


class OpenClawSkillTests(unittest.TestCase):
    def test_skill_defaults_to_hosted_backend(self):
        script_globals = {}
        with open("openclaw_skill/scripts/openclaw-shopping-skill.py", "r", encoding="utf-8") as handle:
            code = compile(handle.read(), "openclaw-shopping-skill.py", "exec")
        with mock.patch.dict(os.environ, {}, clear=True):
            exec(code, script_globals)
            self.assertEqual(script_globals["_backend_base_url"](), "https://a.retn.kr")

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

    def test_skill_script_rejects_unapproved_remote_backend_when_token_is_present(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = "https://a.retn.kr"
        env["OPENCLAW_SHOPPING_API_TOKEN"] = "token-123"
        completed = subprocess.run(
            [
                sys.executable,
                "openclaw_skill/scripts/openclaw-shopping-skill.py",
                "recommend",
                "--backend",
                "https://evil.example.com",
                "--query",
                "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘",
            ],
            cwd=os.getcwd(),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("backend host is not approved", completed.stderr)

    def test_skill_script_accepts_plural_token_env(self):
        env = dict(os.environ)
        env["OPENCLAW_SHOPPING_BASE_URL"] = self.base_url
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


if __name__ == "__main__":
    unittest.main()
