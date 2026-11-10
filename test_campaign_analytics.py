import sqlite3
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from analytics import AnalyticsStore, FirestoreAnalyticsStore
from economics import compute_campaign_click_report


class CampaignAnalyticsTests(unittest.TestCase):
    def test_campaign_report_counts_only_redirect_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/analytics.sqlite3"
            store = AnalyticsStore(db_path)
            for event_type in ("book_click", "shortlink_redirect"):
                store.record_event(event_type=event_type, metadata={"slug": "abc"}, campaign_id="campaign-a", shortlink_slug="abc", client_ip="1.2.3.4")
            with sqlite3.connect(db_path) as connection:
                created = connection.execute("SELECT MIN(created_at) FROM events").fetchone()[0]
            start = datetime.fromisoformat(created) - timedelta(seconds=1)
            end = datetime.now(timezone.utc) + timedelta(seconds=1)
            report = compute_campaign_click_report(db_path, "campaign-a", start, end)
            self.assertEqual(report["raw_clicks"], 1)
            self.assertEqual(report["effective_clicks"], 1)
            self.assertEqual(report["slug_count"], 1)
            self.assertEqual(report["method_version"], "ip_slug_10s_v1")

    def test_invalid_window_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/analytics.sqlite3"
            AnalyticsStore(db_path)
            now = datetime.now(timezone.utc)
            with self.assertRaises(ValueError):
                compute_campaign_click_report(db_path, "campaign-a", now, now)


    def test_firestore_event_carries_provider_parity_fields(self):
        store = FirestoreAnalyticsStore(project_id="demo", access_token="token")
        with mock.patch.object(store, "_create_document") as create:
            store.record_event(event_type="shortlink_redirect", campaign_id="campaign-a", shortlink_slug="slug-a")
        fields = create.call_args.args[2]
        self.assertEqual(fields["campaign_id"]["stringValue"], "campaign-a")
        self.assertEqual(fields["shortlink_slug"]["stringValue"], "slug-a")

if __name__ == "__main__":
    unittest.main()
