import os
import unittest

from economics import build_economics_summary


class EconomicsTests(unittest.TestCase):
    def test_build_economics_summary_uses_observed_ctr_when_signal_exists(self):
        summary = build_economics_summary(
            {
                "total_queries": 200,
                "total_short_link_clicks": 20,
                "event_breakdown": [{"event_type": "deeplink_clicked", "count": 20}],
                "category_breakdown": [{"category": "electronics", "count": 200}],
            }
        )

        funnel = summary["funnel"]
        self.assertEqual(funnel["observed_click_through_rate"], 0.1)
        self.assertFalse(funnel["short_link_clicks_are_attributed"])
        base = next(item for item in summary["scenarios"] if item["name"] == "base")
        self.assertAlmostEqual(base["inputs"]["click_through_rate"], 0.1)

    def test_category_override_changes_projection(self):
        original = os.environ.get("OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON")
        os.environ["OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON"] = (
            '{"electronics":{"base":{"commission_rate":0.05,"aov_krw":60000}}}'
        )
        try:
            summary = build_economics_summary(
                {
                    "total_queries": 200,
                    "total_short_link_clicks": 20,
                    "event_breakdown": [],
                    "category_breakdown": [{"category": "electronics", "count": 200}],
                }
            )
        finally:
            if original is None:
                os.environ.pop("OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON", None)
            else:
                os.environ["OPENCLAW_SHOPPING_CATEGORY_PAYOUT_OVERRIDES_JSON"] = original

        category_projection = summary["category_scenarios"][0]
        self.assertFalse(category_projection["uses_observed_ctr"])
        base = next(item for item in category_projection["scenarios"] if item["name"] == "base")
        self.assertEqual(base["inputs"]["commission_rate"], 0.05)
        self.assertEqual(base["inputs"]["aov_krw"], 60000.0)

    def test_no_attributed_click_signal_keeps_global_projection_on_assumed_ctr(self):
        summary = build_economics_summary(
            {
                "total_queries": 500,
                "total_short_link_clicks": 50,
                "event_breakdown": [],
                "category_breakdown": [],
            }
        )

        funnel = summary["funnel"]
        self.assertIsNone(funnel["observed_click_through_rate"])
        self.assertFalse(funnel["has_meaningful_click_signal"])
        base = next(item for item in summary["scenarios"] if item["name"] == "base")
        self.assertAlmostEqual(base["inputs"]["click_through_rate"], 0.15)


if __name__ == "__main__":
    unittest.main()
