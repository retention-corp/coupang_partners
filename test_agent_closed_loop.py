import importlib.util
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
                        "productName": "클로즈드 루프 카나리 상품",
                        "productPrice": 9900,
                        "productUrl": "https://www.coupang.com/vp/products/701",
                        "reviewCount": 10,
                        "ratingAverage": 4.2,
                    }
                ]
            }
        }


def load_closed_loop_module():
    script_path = Path(__file__).resolve().parent / "scripts" / "agent_closed_loop.py"
    spec = importlib.util.spec_from_file_location("agent_closed_loop", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AgentClosedLoopTests(unittest.TestCase):
    def setUp(self):
        self.module = load_closed_loop_module()
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

    def test_shallow_closed_loop_checks_health_and_openapi(self):
        result = self.module.run_closed_loop(
            self.base_url,
            timeout=5,
            client_id="smoke-test",
            surface="cli",
            deep=False,
            allow_non_prod=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual([check["name"] for check in result["checks"]], ["health", "openapi"])
        self.assertEqual(result["cost_guard"]["default_mode"], "shallow")
        self.assertEqual(result["cost_guard"]["cloud_run_defaults"]["min_instances"], 0)
        self.assertIn("hermes-agent", result["cost_guard"]["expected_agent_allowlist"])

    def test_deep_closed_loop_requires_assist_and_shortlink_redirect(self):
        with mock.patch.object(
            self.module,
            "_request_redirect_status",
            return_value={"status": 302, "location": "https://link.coupang.com/a/example"},
        ):
            result = self.module.run_closed_loop(
                self.base_url,
                timeout=5,
                client_id="smoke-test",
                surface="cli",
                deep=True,
                allow_non_prod=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [check["name"] for check in result["checks"]],
            ["health", "openapi", "public_assist", "shortlink_redirect"],
        )
        self.assertIn("short_deeplink", result["context"])
        self.assertEqual(result["cost_guard"]["next_check_after_seconds"], 3600)

    def test_state_file_throttles_repeated_deep_loop(self):
        state_file = f"{self.tempdir.name}/closed-loop-state.json"
        with mock.patch.object(
            self.module,
            "_request_redirect_status",
            return_value={"status": 302, "location": "https://link.coupang.com/a/example"},
        ):
            first = self.module.run_closed_loop(
                self.base_url,
                timeout=5,
                client_id="smoke-test",
                surface="cli",
                deep=True,
                allow_non_prod=True,
                state_file=state_file,
                min_deep_interval_seconds=3600,
            )
            second = self.module.run_closed_loop(
                self.base_url,
                timeout=5,
                client_id="smoke-test",
                surface="cli",
                deep=True,
                allow_non_prod=True,
                state_file=state_file,
                min_deep_interval_seconds=3600,
            )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["context"]["mode"], "shallow")
        self.assertEqual(second["context"]["requested_mode"], "deep")
        self.assertTrue(second["context"]["deep_skipped"])
        self.assertEqual([check["name"] for check in second["checks"]], ["health", "openapi"])

    def test_openapi_contract_mismatch_classifies_recovery(self):
        with mock.patch.object(self.module, "_request_json", return_value={"paths": {"/health": {}}}):
            with self.assertRaises(self.module.ClosedLoopError) as ctx:
                self.module._check_openapi(
                    self.base_url,
                    timeout=5,
                    client_id="smoke-test",
                    surface="cli",
                )

        self.assertEqual(ctx.exception.code, "openapi_contract_mismatch")
        failed = [self.module.CheckResult(name="openapi", ok=False, code=ctx.exception.code)]
        recovery = self.module.build_recovery_plan(failed, deep=False)
        self.assertEqual(recovery[0]["priority"], "P0")
        self.assertIn("Roll back", recovery[0]["action"])

    def test_openapi_accepts_deployed_best_category_alias(self):
        paths = {
            "/health": {},
            "/v1/public/assist": {},
            "/v1/public/search": {},
            "/v1/public/goldbox": {},
            "/v1/public/best/{category}": {},
            "/s/{slug}": {},
        }
        with mock.patch.object(self.module, "_request_json", return_value={"paths": paths}):
            result = self.module._check_openapi(
                self.base_url,
                timeout=5,
                client_id="smoke-test",
                surface="cli",
            )

        self.assertEqual(result, {})

    def test_allowlist_recovery_names_sa_agent_defaults(self):
        failed = [self.module.CheckResult(name="assist", ok=False, code="client_not_allowlisted")]
        recovery = self.module.build_recovery_plan(failed, deep=True)
        action_text = " ".join(item["action"] for item in recovery)
        self.assertIn("OPENCLAW_SHOPPING_CLIENT_ALLOWLIST", action_text)
        self.assertIn("hermes-agent", action_text)
        self.assertIn("claw-*", action_text)

    def test_http_error_classifier_maps_operational_failures(self):
        cases = [
            (403, "Cloudflare Error code: 1010", "cloudflare_blocked"),
            (403, '{"error":"Client is not allowlisted"}', "client_not_allowlisted"),
            (429, '{"error":"Rate limit exceeded"}', "rate_limited"),
            (404, '{"error":"Not found"}', "route_missing"),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected):
                error = self.module._classify_http_error("GET", "https://a.retn.kr/test", status, body)
                self.assertEqual(error.code, expected)


if __name__ == "__main__":
    unittest.main()
