import tempfile
import unittest
from unittest import mock

from url_shortener import BuiltinShortener, FirestoreShortener


class BuiltinShortenerTests(unittest.TestCase):
    def test_builtin_shortener_is_stable_and_resolvable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            shortened = shortener.shorten("https://example.com/very/long")
            again = shortener.shorten("https://example.com/very/long")

            self.assertEqual(shortened, again)
            self.assertTrue(shortened.startswith("https://go.example.com/s/"))
            slug = shortened.rsplit("/", 1)[-1]
            self.assertEqual(shortener.resolve(slug), "https://example.com/very/long")

    def test_builtin_shortener_records_clicks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            shortened = shortener.shorten("https://example.com/a")
            slug = shortened.rsplit("/", 1)[-1]
            shortener.record_click(slug)
            shortener.record_click(slug)
            self.assertEqual(shortener.resolve(slug), "https://example.com/a")
            self.assertEqual(shortener.get_summary()["total_short_links"], 1)
            self.assertEqual(shortener.get_summary()["total_short_link_clicks"], 2)

    def test_firestore_shortener_reuses_existing_slug(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        with mock.patch.object(
            shortener,
            "_request_json",
            return_value=[
                {
                    "document": {
                        "name": "projects/demo-project/databases/(default)/documents/short_links/existingSlug"
                    }
                }
            ],
        ) as mocked_request:
            shortened = shortener.shorten("https://example.com/very/long")

        self.assertEqual(shortened, "https://go.example.com/s/existingSlug")
        self.assertEqual(mocked_request.call_count, 1)

    def test_firestore_shortener_summary_uses_aggregation_response(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        with mock.patch.object(
            shortener,
            "_request_json",
            return_value=[
                {
                    "result": {
                        "aggregateFields": {
                            "total": {"integerValue": "7"}
                        }
                    }
                }
            ],
        ):
            summary = shortener.get_summary()

        self.assertEqual(summary["total_short_links"], 7)
        self.assertEqual(summary["total_short_link_clicks"], 0)

    def test_firestore_shortener_record_click_uses_atomic_transform(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        with mock.patch.object(shortener, "_request_json", return_value={}) as mocked_request:
            shortener.record_click("slug-1")

        method, url = mocked_request.call_args.args[:2]
        body = mocked_request.call_args.kwargs["body"]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith(":commit"))
        transform = body["writes"][0]["transform"]
        self.assertEqual(
            transform["document"],
            "projects/demo-project/databases/(default)/documents/short_links/slug-1",
        )
        self.assertEqual(transform["fieldTransforms"][0]["fieldPath"], "click_count")


if __name__ == "__main__":
    unittest.main()
