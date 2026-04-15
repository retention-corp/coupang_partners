import tempfile
import unittest
from unittest import mock

from analytics import AnalyticsStore, FirestoreAnalyticsStore


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
            self.assertEqual(summary["category_breakdown"][0]["category"], "vacuum")

    def test_firestore_analytics_summary_aggregates_collections(self):
        store = FirestoreAnalyticsStore(
            project_id="demo-project",
            collection_prefix="shopping",
            access_token="test-token",
        )
        with mock.patch.object(
            store,
            "_request_json",
            side_effect=[
                [{"result": {"aggregateFields": {"total": {"integerValue": "2"}}}}],
                [{"result": {"aggregateFields": {"total": {"integerValue": "6"}}}}],
                [{"result": {"aggregateFields": {"total": {"integerValue": "1"}}}}],
                [{"result": {"aggregateFields": {"total": {"integerValue": "3"}}}}],
                [
                    {
                        "document": {
                            "fields": {
                                "query_text": {"stringValue": "저렴한 꽃병 3개"},
                                "created_at": {"timestampValue": "2026-04-13T08:49:20.138429+00:00"},
                            }
                        }
                    }
                ],
                [
                    {"document": {"fields": {"event_type": {"stringValue": "deeplink_clicked"}}}},
                    {"document": {"fields": {"event_type": {"stringValue": "deeplink_clicked"}}}},
                    {"document": {"fields": {"event_type": {"stringValue": "result_viewed"}}}},
                ],
                [],
            ],
        ):
            summary = store.get_summary()

        self.assertEqual(summary["total_queries"], 2)
        self.assertEqual(summary["total_recommendations"], 6)
        self.assertEqual(summary["total_events"], 1)
        self.assertEqual(summary["total_evidence_snippets"], 3)
        self.assertEqual(summary["latest_query"]["query_text"], "저렴한 꽃병 3개")
        self.assertEqual(summary["event_breakdown"][0], {"event_type": "deeplink_clicked", "count": 2})
        self.assertEqual(summary["category_breakdown"], [])


if __name__ == "__main__":
    unittest.main()
