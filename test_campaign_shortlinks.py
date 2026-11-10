import tempfile
import unittest
from unittest import mock

from url_shortener import BuiltinShortener, FirestoreShortener


class CampaignShortlinkTests(unittest.TestCase):
    def test_same_campaign_target_is_idempotent_and_campaigns_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(f"{temp_dir}/links.sqlite3", "https://go.example")
            first = shortener.create_campaign_link("campaign-a", "https://www.coupang.com/vp/products/1")
            repeated = shortener.create_campaign_link("campaign-a", "https://www.coupang.com/vp/products/1")
            other = shortener.create_campaign_link("campaign-b", "https://www.coupang.com/vp/products/1")
            self.assertEqual(first, repeated)
            self.assertNotEqual(first["slug"], other["slug"])
            self.assertEqual(shortener.resolve(first["slug"]), first["target_url"])

    def test_lookup_is_non_counting_and_legacy_is_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(f"{temp_dir}/links.sqlite3", "https://go.example")
            legacy_url = shortener.shorten("https://www.coupang.com/vp/products/legacy")
            legacy_slug = legacy_url.rsplit("/", 1)[-1]
            campaign = shortener.create_campaign_link("campaign-a", "https://www.coupang.com/vp/products/1")
            before = shortener.get_summary()["total_short_link_clicks"]
            self.assertEqual(shortener.lookup_link(legacy_slug)["record_type"], "legacy")
            self.assertIsNone(shortener.lookup_link(legacy_slug)["campaign_id"])
            self.assertEqual(shortener.lookup_link(campaign["slug"]), campaign)
            self.assertEqual(before, shortener.get_summary()["total_short_link_clicks"])


    def test_firestore_campaign_record_matches_sqlite_contract(self):
        shortener = FirestoreShortener(project_id="demo", public_base_url="https://go.example", access_token="token")
        document = {"document": {"name": "projects/demo/databases/(default)/documents/short_links/campaignSlug", "fields": {"target_url": {"stringValue": "https://www.coupang.com/1"}, "campaign_id": {"stringValue": "campaign-a"}}}}
        with mock.patch.object(shortener, "_request_json", return_value=[document]):
            record = shortener.create_campaign_link("campaign-a", "https://www.coupang.com/1")
        self.assertEqual(record, {"slug": "campaignSlug", "target_url": "https://www.coupang.com/1", "campaign_id": "campaign-a", "record_type": "campaign"})

if __name__ == "__main__":
    unittest.main()
