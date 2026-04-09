import tempfile
import unittest

from analytics import AnalyticsStore


class AnalyticsStoreTests(unittest.TestCase):
    def test_record_assist_and_event_populate_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AnalyticsStore(f"{temp_dir}/analytics.sqlite3")
            query_id = store.record_assist(
                query_text="30만원 이하 무선청소기",
                budget=300000,
                category="vacuum",
                evidence_snippets=[{"text": "리뷰: 원룸에서 쓰기 좋음", "source": "manual"}],
                recommendations=[
                    {
                        "product_id": "11",
                        "title": "저소음 무선청소기",
                        "score": 9.5,
                        "deeplink": "https://example.com/11",
                        "rationale": "Strong fit.",
                        "risks": ["Limited review history reduces confidence."],
                    }
                ],
            )
            event_id = store.record_event(
                event_type="deeplink_clicked",
                query_id=query_id,
                recommendation_id="11",
                metadata={"channel": "cli"},
            )

            summary = store.get_summary()
            self.assertTrue(query_id)
            self.assertTrue(event_id)
            self.assertEqual(summary["total_queries"], 1)
            self.assertEqual(summary["total_recommendations"], 1)
            self.assertEqual(summary["total_events"], 1)
            self.assertEqual(summary["total_evidence_snippets"], 1)
            self.assertEqual(summary["latest_query"]["query_text"], "30만원 이하 무선청소기")
            self.assertEqual(summary["event_breakdown"][0]["event_type"], "deeplink_clicked")


if __name__ == "__main__":
    unittest.main()
