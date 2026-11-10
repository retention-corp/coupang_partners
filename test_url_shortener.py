import tempfile
import unittest
import sqlite3
from unittest import mock

from url_shortener import AliasConflictError, BuiltinShortener, FirestoreShortener, UrlSafetyPolicy


class FakeUrlopenResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


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

    def test_builtin_shortener_blocks_policy_unsafe_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
                safety_policy=UrlSafetyPolicy(blocked_hosts=("blocked.example",)),
            )

            self.assertIsNone(shortener.shorten("https://blocked.example/a"))
            self.assertEqual(shortener.get_summary()["total_short_links"], 0)

    def test_builtin_shortener_uses_cached_unsafe_decision_on_redirect(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
                safety_policy=UrlSafetyPolicy(checker=lambda url: "safe"),
            )
            shortened = shortener.shorten("https://example.com/a")
            slug = shortened.rsplit("/", 1)[-1]
            shortener.safety_policy = UrlSafetyPolicy(blocked_hosts=("example.com",))

            self.assertIsNone(shortener.resolve(slug))

    def test_builtin_shortener_records_review_when_checker_fails(self):
        def fail_check(url):
            raise RuntimeError("webrisk unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
                safety_policy=UrlSafetyPolicy(checker=fail_check),
            )

            shortened = shortener.shorten("https://example.com/a")
            self.assertTrue(shortened.startswith("https://go.example.com/s/"))
            decision = shortener._get_cached_safety_decision("https://example.com/a")
            self.assertEqual(decision.status, "review")
            self.assertEqual(decision.reason, "checker_error")

    def test_safety_policy_from_env_wires_web_risk_checker(self):
        with mock.patch.dict(
            "os.environ",
            {
                "OPENCLAW_SHORTENER_SAFETY_PROVIDER": "webrisk",
                "OPENCLAW_WEB_RISK_API_KEY": "test-key",
            },
            clear=False,
        ):
            policy = UrlSafetyPolicy.from_env()

        with mock.patch(
            "url_shortener.request.urlopen",
            return_value=FakeUrlopenResponse(b'{"threat": {"threatTypes": ["MALWARE"]}}'),
        ) as mocked_urlopen:
            decision = policy.decide("https://example.com/a", allow_external_check=True)

        self.assertEqual(decision.status, "unsafe")
        request_url = mocked_urlopen.call_args.args[0].full_url
        self.assertIn("https://webrisk.googleapis.com/v1/uris:search?", request_url)
        self.assertIn("key=test-key", request_url)
        self.assertIn("uri=https%3A%2F%2Fexample.com%2Fa", request_url)

    def test_builtin_shortener_rechecks_expired_cached_redirect_with_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
                safety_policy=UrlSafetyPolicy(checker=lambda url: "safe"),
            )
            shortened = shortener.shorten("https://example.com/a")
            slug = shortened.rsplit("/", 1)[-1]
            with sqlite3.connect(f"{temp_dir}/shortener.sqlite3") as connection:
                connection.execute("UPDATE url_safety_cache SET status = 'safe', expires_at = 1")
            shortener.safety_policy = UrlSafetyPolicy(checker=lambda url: "unsafe")

            self.assertIsNone(shortener.resolve(slug))
            decision = shortener._get_cached_safety_decision("https://example.com/a")
            self.assertEqual(decision.status, "unsafe")

    def test_admin_create_link_stores_alias_and_short_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )

            record = shortener.create_link(
                "https://Example.com/path?b=1#ignored",
                alias="Launch-Deal",
                owner_username="Member@Example.com",
            )

            self.assertEqual(record.slug, "launch-deal")
            self.assertEqual(record.alias, "launch-deal")
            self.assertEqual(record.short_url, "https://go.example.com/s/launch-deal")
            self.assertEqual(record.target_url, "https://example.com/path?b=1")
            self.assertEqual(record.owner_username, "member@example.com")
            self.assertEqual(record.owner_status, "assigned")
            self.assertEqual(shortener.resolve("launch-deal"), "https://example.com/path?b=1")

    def test_admin_create_link_allows_duplicate_landing_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )

            first = shortener.create_link("https://example.com/same")
            second = shortener.create_link("https://example.com/same")

            self.assertNotEqual(first.slug, second.slug)
            self.assertEqual(shortener.resolve(first.slug), "https://example.com/same")
            self.assertEqual(shortener.resolve(second.slug), "https://example.com/same")

    def test_admin_create_link_rejects_global_alias_conflict_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            original = shortener.create_link("https://example.com/original", alias="deal-001")

            with self.assertRaises(AliasConflictError):
                shortener.create_link("https://example.com/replacement", alias="deal-001")

            self.assertEqual(shortener.resolve("deal-001"), "https://example.com/original")
            self.assertEqual(shortener.list_links()[0].slug, original.slug)

    def test_admin_batch_creation_returns_row_level_success_and_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            shortener.create_link("https://example.com/existing", alias="taken")

            response = shortener.create_links(
                [
                    {"url": "https://example.com/one", "alias": "one"},
                    {"url": "https://example.com/two", "alias": "taken"},
                    {"url": "not-a-url", "alias": "bad-url"},
                ],
                owner_username="member@example.com",
            )

            self.assertFalse(response["ok"])
            self.assertEqual([item["ok"] for item in response["results"]], [True, False, False])
            self.assertEqual(response["results"][0]["link"]["short_url"], "https://go.example.com/s/one")
            self.assertEqual(response["results"][1]["error"], "alias_conflict")
            self.assertEqual(response["results"][2]["error"], "validation_error")

    def test_admin_create_link_rejects_malformed_and_reserved_alias_before_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )

            response = shortener.create_links(
                [
                    {"url": "https://example.com/a", "alias": "ab"},
                    {"url": "https://example.com/b", "alias": "api"},
                    {"url": "https://example.com/c", "alias": "bad alias"},
                    {"url": "https://example.com/d", "alias": "bad/alias"},
                ]
            )

            self.assertFalse(response["ok"])
            self.assertEqual([item["error"] for item in response["results"]], ["validation_error"] * 4)
            self.assertEqual(shortener.get_summary()["total_short_links"], 0)

    def test_admin_deactivate_returns_not_found_but_preserves_admin_record_and_click_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            record = shortener.create_link("https://example.com/live", alias="live-link")
            shortener.record_click(record.slug)

            updated = shortener.update_link(record.slug, active=False)

            self.assertFalse(updated.active)
            self.assertIsNone(shortener.resolve(record.slug))
            listed = shortener.list_links()[0]
            self.assertEqual(listed.slug, record.slug)
            self.assertFalse(listed.active)
            self.assertEqual(listed.click_count, 1)

    def test_existing_shorten_records_are_listed_as_legacy_unassigned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shortener = BuiltinShortener(
                db_path=f"{temp_dir}/shortener.sqlite3",
                public_base_url="https://go.example.com",
            )
            short_url = shortener.shorten("https://example.com/legacy")
            slug = short_url.rsplit("/", 1)[-1]

            listed = shortener.list_links()[0]

            self.assertEqual(listed.slug, slug)
            self.assertEqual(listed.owner_username, None)
            self.assertEqual(listed.owner_status, "legacy")
            self.assertTrue(listed.to_dict()["legacy"])
            self.assertEqual(shortener.resolve(slug), "https://example.com/legacy")

    def test_existing_sqlite_unique_target_schema_is_migrated_for_duplicate_admin_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/shortener.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE short_links (
                        slug TEXT PRIMARY KEY,
                        target_url TEXT NOT NULL UNIQUE,
                        click_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_clicked_at TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO short_links (slug, target_url) VALUES ('legacySlug', 'https://example.com/same')"
                )
            shortener = BuiltinShortener(db_path=db_path, public_base_url="https://go.example.com")

            duplicate = shortener.create_link("https://example.com/same")

            self.assertNotEqual(duplicate.slug, "legacySlug")
            self.assertEqual(len(shortener.list_links()), 2)

    def test_firestore_shortener_reuses_existing_slug(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        with mock.patch.object(
            shortener,
            "_request_json",
            side_effect=[
                None,
                {},
                [
                    {
                        "document": {
                            "name": "projects/demo-project/databases/(default)/documents/short_links/existingSlug"
                        }
                    }
                ],
            ],
        ) as mocked_request:
            shortened = shortener.shorten("https://example.com/very/long")

        self.assertEqual(shortened, "https://go.example.com/s/existingSlug")
        self.assertEqual(mocked_request.call_count, 3)

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

    def test_firestore_shortener_writes_safety_cache_before_creating_slug(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
            safety_policy=UrlSafetyPolicy(blocked_hosts=("blocked.example",)),
        )
        with mock.patch.object(shortener, "_request_json", return_value={}) as mocked_request:
            shortened = shortener.shorten("https://blocked.example/a")

        self.assertIsNone(shortened)
        self.assertEqual(mocked_request.call_count, 2)
        self.assertFalse(any("/short_links/" in call.args[1] for call in mocked_request.call_args_list))
        method, url = mocked_request.call_args_list[-1].args[:2]
        body = mocked_request.call_args_list[-1].kwargs["body"]
        self.assertEqual(method, "PATCH")
        self.assertIn("/url_safety_cache/", url)
        self.assertEqual(body["fields"]["status"]["stringValue"], "unsafe")

    def test_firestore_shortener_resolve_blocks_embedded_unsafe_status_without_lookup(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        document = {
            "fields": {
                "target_url": {"stringValue": "https://example.com/a"},
                "safety_status": {"stringValue": "unsafe"},
            }
        }
        with mock.patch.object(shortener, "_request_json", return_value=document) as mocked_request:
            self.assertIsNone(shortener.resolve("slug-1"))

        self.assertEqual(mocked_request.call_count, 1)

    def test_firestore_shortener_rechecks_expired_cached_redirect_with_provider(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
            safety_policy=UrlSafetyPolicy(checker=lambda url: "unsafe"),
        )
        document = {
            "fields": {
                "target_url": {"stringValue": "https://example.com/a"},
                "active": {"booleanValue": True},
                "safety_status": {"stringValue": "safe"},
            }
        }
        expired_cache = {
            "fields": {
                "status": {"stringValue": "safe"},
                "reason": {"stringValue": "checker_result"},
                "source": {"stringValue": "checker"},
                "host": {"stringValue": "example.com"},
                "checked_at": {"stringValue": "2026-09-03T00:00:00+00:00"},
                "expires_at_epoch": {"integerValue": "1"},
            }
        }
        with mock.patch.object(
            shortener,
            "_request_json",
            side_effect=[document, expired_cache, {}],
        ) as mocked_request:
            self.assertIsNone(shortener.resolve("slug-1"))

        self.assertEqual(mocked_request.call_count, 3)
        method, url = mocked_request.call_args_list[-1].args[:2]
        body = mocked_request.call_args_list[-1].kwargs["body"]
        self.assertEqual(method, "PATCH")
        self.assertIn("/url_safety_cache/", url)
        self.assertEqual(body["fields"]["status"]["stringValue"], "unsafe")

    def test_firestore_admin_create_link_uses_alias_as_global_document_id(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        created_document = {
            "name": "projects/demo-project/databases/(default)/documents/short_links/deal-002",
            "fields": {
                "target_url": {"stringValue": "https://example.com/a"},
                "alias": {"stringValue": "deal-002"},
                "active": {"booleanValue": True},
                "owner_username": {"stringValue": "member@example.com"},
                "owner_status": {"stringValue": "assigned"},
                "click_count": {"integerValue": "0"},
                "created_at": {"timestampValue": "2026-09-03T10:00:00Z"},
            },
        }
        with mock.patch.object(
            shortener,
            "_request_json",
            side_effect=[None, {}, None, {}, created_document],
        ) as mocked_request:
            record = shortener.create_link(
                "https://example.com/a",
                alias="Deal-002",
                owner_username="Member@Example.com",
            )

        self.assertEqual(record.short_url, "https://go.example.com/s/deal-002")
        method, url = mocked_request.call_args_list[3].args[:2]
        body = mocked_request.call_args_list[3].kwargs["body"]
        self.assertEqual(method, "PATCH")
        self.assertTrue(url.endswith("/short_links/deal-002?currentDocument.exists=false"))
        self.assertEqual(body["fields"]["alias"]["stringValue"], "deal-002")
        self.assertEqual(body["fields"]["owner_username"]["stringValue"], "member@example.com")

    def test_firestore_deactivated_document_does_not_resolve(self):
        shortener = FirestoreShortener(
            project_id="demo-project",
            public_base_url="https://go.example.com",
            access_token="test-token",
        )
        document = {
            "fields": {
                "target_url": {"stringValue": "https://example.com/a"},
                "active": {"booleanValue": False},
            }
        }
        with mock.patch.object(shortener, "_request_json", return_value=document) as mocked_request:
            self.assertIsNone(shortener.resolve("slug-1"))

        self.assertEqual(mocked_request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
