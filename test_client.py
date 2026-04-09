import json
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib import error

from coupang_partners import CoupangApiError, CoupangPartnersClient


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class CoupangPartnersClientTests(unittest.TestCase):
    def setUp(self):
        self.client = CoupangPartnersClient(
            access_key="test-access",
            secret_key="test-secret",
        )

    def test_build_signed_date_uses_utc_format(self):
        now = datetime(2026, 4, 9, 8, 7, 6, tzinfo=timezone.utc)
        self.assertEqual(self.client.build_signed_date(now), "260409T080706Z")

    def test_sign_matches_documented_message_shape(self):
        signed = self.client.sign(
            "GET",
            "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search?keyword=good&page=1",
            signed_date="260409T080706Z",
        )

        self.assertEqual(signed.path_with_query, "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search?keyword=good&page=1")
        self.assertEqual(
            signed.authorization,
            "CEA algorithm=HmacSHA256, access-key=test-access, signed-date=260409T080706Z, "
            "signature=544e7297177cffa96a5fc4541a0c86d46f9ab26642f78972c3691c46ee04776b",
        )

    def test_request_builds_signed_json_post(self):
        captured = {}

        def opener(req, timeout):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["body"] = req.data.decode("utf-8")
            captured["timeout"] = timeout
            return DummyResponse({"rCode": "0", "data": []})

        response = self.client.request(
            "POST",
            "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink",
            json_body={"coupangUrls": ["https://www.coupang.com"]},
            signed_date="260409T080706Z",
            opener=opener,
        )

        self.assertEqual(response, {"rCode": "0", "data": []})
        self.assertEqual(
            captured["url"],
            "https://api-gateway.coupang.com/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink",
        )
        self.assertEqual(captured["headers"]["content-type"], "application/json")
        self.assertIn("authorization", captured["headers"])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(captured["body"], '{"coupangUrls": ["https://www.coupang.com"]}')

    def test_request_raises_api_error_on_http_failure(self):
        class FakeHttpError(error.HTTPError):
            code = 401
            reason = "Unauthorized"

            def __init__(self):
                super().__init__(
                    url="https://api-gateway.coupang.com/test",
                    code=401,
                    msg="Unauthorized",
                    hdrs=None,
                    fp=None,
                )

            def read(self):
                return b'{"code":"ERROR","message":"Request is not authorized."}'

        def opener(req, timeout):
            raise FakeHttpError()

        with mock.patch("client.error.HTTPError", FakeHttpError):
            with self.assertRaises(CoupangApiError) as ctx:
                self.client.request(
                    "GET",
                    "/v2/providers/affiliate_open_api/apis/openapi/v1/products/goldbox",
                    opener=opener,
                )

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(
            ctx.exception.payload,
            {"code": "ERROR", "message": "Request is not authorized."},
        )

    def test_minimal_reco_payload_builder(self):
        payload = self.client.minimal_reco_v2_payload(
            site_id="site-1",
            site_domain="example.com",
            device_id="device-1",
            image_size="200x200",
            user_puid="user-1",
        )

        self.assertEqual(
            payload,
            {
                "site": {"id": "site-1", "domain": "example.com"},
                "device": {"id": "device-1", "lmt": 0},
                "imp": {"imageSize": "200x200"},
                "user": {"puid": "user-1"},
            },
        )


if __name__ == "__main__":
    unittest.main()
