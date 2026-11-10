import json
import os
import tempfile
import sqlite3
import unittest
from urllib import error, request

from backend import _Handler, build_server, serve_in_thread
from security import RateLimiter
from test_backend import FakeAdapter


class CampaignRouteTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ["OPENCLAW_SHOPPING_API_TOKENS"] = "secret"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST_ENABLED"] = "true"
        os.environ["OPENCLAW_SHOPPING_CLIENT_ALLOWLIST"] = "insta-cardnews-book-sales-v1"
        os.environ["OPENCLAW_SHOPPING_ENABLE_BOOK_CAMPAIGN_ROUTES"] = "true"
        self.temp = tempfile.TemporaryDirectory()
        self.server = build_server(host="127.0.0.1", port=0, adapter=FakeAdapter(), db_path=f"{self.temp.name}/db.sqlite3", public_base_url="https://go.example")
        self.thread = serve_in_thread(self.server)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2); self.temp.cleanup()
        os.environ.clear(); os.environ.update(self.saved)

    def call(self, path, *, token="secret", client="insta-cardnews-book-sales-v1", body=None):
        headers = {"X-OpenClaw-Client-Id": client}
        if token is not None: headers["Authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        if data: headers["Content-Type"] = "application/json"
        try:
            with request.urlopen(request.Request(self.base + path, data=data, headers=headers, method="POST" if data else "GET")) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_feature_auth_allowlist_and_non_counting_lookup(self):
        os.environ["OPENCLAW_SHOPPING_ENABLE_BOOK_CAMPAIGN_ROUTES"] = "false"
        self.assertEqual(self.call("/internal/v1/book-campaign-links/x")[0], 404)
        os.environ["OPENCLAW_SHOPPING_ENABLE_BOOK_CAMPAIGN_ROUTES"] = "true"
        self.assertEqual(self.call("/internal/v1/book-campaign-links/x", token="wrong")[0], 401)
        self.assertEqual(self.call("/internal/v1/book-campaign-links/x", client="other")[0], 403)
        status, created = self.call("/internal/v1/book-campaign-links", body={"campaign_id": "campaign-a", "target_url": "https://www.coupang.com/vp/products/1"})
        self.assertEqual(status, 200)
        before = self.server.RequestHandlerClass.backend.shortener.get_summary()["total_short_link_clicks"]
        status, found = self.call(f"/internal/v1/book-campaign-links/{created['slug']}")
        self.assertEqual((status, found["target_url"], found["campaign_id"]), (200, created["target_url"], "campaign-a"))
        self.assertEqual(before, self.server.RequestHandlerClass.backend.shortener.get_summary()["total_short_link_clicks"])

    def test_bad_campaign_body_is_400(self):
        self.assertEqual(self.call("/internal/v1/book-campaign-links", body={"campaign_id": "", "target_url": "bad"})[0], 400)


    def test_campaign_redirect_attaches_both_event_fields_but_report_counts_once(self):
        _, created = self.call("/internal/v1/book-campaign-links", body={"campaign_id": "campaign-a", "target_url": "https://www.coupang.com/vp/products/1"})
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        with self.assertRaises(error.HTTPError) as caught:
            request.build_opener(NoRedirect()).open(self.base + f"/s/{created['slug']}")
        self.assertEqual(caught.exception.code, 302)
        with sqlite3.connect(f"{self.temp.name}/db.sqlite3") as connection:
            rows = connection.execute("SELECT event_type, campaign_id, shortlink_slug FROM events WHERE event_type IN ('book_click', 'shortlink_redirect') ORDER BY event_type").fetchall()
        self.assertEqual(rows, [("book_click", "campaign-a", created["slug"]), ("shortlink_redirect", "campaign-a", created["slug"])])

if __name__ == "__main__": unittest.main()
