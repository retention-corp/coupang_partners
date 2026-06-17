import importlib.util
import unittest
from pathlib import Path
from unittest import mock


def load_closed_loop_module():
    script_path = Path(__file__).resolve().parent / "scripts" / "closed_loop_ops.py"
    spec = importlib.util.spec_from_file_location("closed_loop_ops", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ClosedLoopOpsTests(unittest.TestCase):
    def setUp(self):
        self.module = load_closed_loop_module()

    def test_closed_loop_flags_gateway_repair_without_running_recovery_by_default(self):
        class FakeSmokeModule:
            class AgentSmokeError(RuntimeError):
                code = "client_not_allowlisted"

            @staticmethod
            def run_agent_smoke(*args, **kwargs):
                raise FakeSmokeModule.AgentSmokeError("Client is not allowlisted")

        with mock.patch.object(self.module, "_load_agent_smoke_module", return_value=FakeSmokeModule):
            with mock.patch.object(self.module, "_admin_summary", return_value=None):
                result = self.module.run_closed_loop(
                    base_url="https://a.retn.kr",
                    timeout=5,
                    client_id="closed-loop",
                    surface="cli",
                    allow_non_prod=False,
                    auto_recover=False,
                    assist_limit_per_day=100,
                    shortlink_limit_per_day=100,
                    recovery_command="scripts/deploy_gcp_cloud_run.sh",
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "critical")
        self.assertIn("repair_agent_gateway", result["actions"])
        self.assertFalse(result["auto_recover"])

    def test_closed_loop_flags_cost_guardrail(self):
        class FakeSmokeModule:
            @staticmethod
            def run_agent_smoke(*args, **kwargs):
                return {"ok": True, "checks": []}

        with mock.patch.object(self.module, "_load_agent_smoke_module", return_value=FakeSmokeModule):
            with mock.patch.object(self.module, "_admin_summary", return_value={"total_queries": 101, "total_short_links": 10}):
                result = self.module.run_closed_loop(
                    base_url="https://a.retn.kr",
                    timeout=5,
                    client_id="closed-loop",
                    surface="cli",
                    allow_non_prod=False,
                    auto_recover=False,
                    assist_limit_per_day=100,
                    shortlink_limit_per_day=100,
                    recovery_command="scripts/deploy_gcp_cloud_run.sh",
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "critical")
        self.assertIn("tighten_public_rate_limits", result["actions"])


if __name__ == "__main__":
    unittest.main()
