import json
import unittest

from product_page_evidence import build_product_page_url, enrich_products_with_page_evidence, fetch_product_page_evidence


class _FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class _FakeResponse:
    def __init__(self, body: str, url: str):
        self._body = body.encode("utf-8")
        self._url = url
        self.headers = _FakeHeaders()

    def read(self, _limit=None):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_urlopen(_request_obj, timeout=0):
    html = """
    <html>
      <head>
        <title>특대형 빅사이즈 KF94 마스크 30매</title>
        <meta name="description" content="얼큰이와 대두도 편하게 쓸 수 있는 특대형 KF94 마스크" />
        <script type="application/ld+json">
          {"@context":"https://schema.org","@type":"Product","name":"특대형 빅사이즈 KF94 마스크","description":"큰 얼굴에 맞는 넉넉한 사이즈","brand":{"@type":"Brand","name":"이온플러스"}}
        </script>
      </head>
      <body>
        <div>대두 고객도 압박감이 덜한 넉넉한 핏</div>
        <div>미세먼지 차단용으로 많이 찾는 KF94 규격</div>
      </body>
    </html>
    """
    return _FakeResponse(html, "https://www.coupang.com/vp/products/7551562122?itemId=19874489288&vendorItemId=79840900617")


class ProductPageEvidenceTests(unittest.TestCase):
    def test_build_product_page_url_prefers_canonical_product_page(self):
        product = {
            "metadata": {
                "productId": 7551562122,
                "itemId": 19874489288,
                "vendorItemId": 79840900617,
            },
            "productUrl": "https://link.coupang.com/re/AFFSDP?...",
        }

        self.assertEqual(
            build_product_page_url(product),
            "https://www.coupang.com/vp/products/7551562122?itemId=19874489288&vendorItemId=79840900617",
        )

    def test_fetch_product_page_evidence_extracts_title_description_and_snippets(self):
        product = {
            "metadata": {
                "productId": 7551562122,
                "itemId": 19874489288,
                "vendorItemId": 79840900617,
            }
        }

        evidence = fetch_product_page_evidence(product, opener=_fake_urlopen)

        self.assertIsNotNone(evidence)
        self.assertIn("빅사이즈", evidence["page_title"])
        self.assertIn("대두", evidence["page_description"])
        self.assertGreaterEqual(len(evidence["page_snippets"]), 1)
        self.assertTrue(any("Landing page brand" in fact for fact in evidence["page_facts"]))

    def test_enrich_products_with_page_evidence_merges_description(self):
        products = [
            {
                "title": "특대형 마스크",
                "description": "KF94",
                "metadata": {
                    "productId": 7551562122,
                    "itemId": 19874489288,
                    "vendorItemId": 79840900617,
                },
            }
        ]

        enriched = enrich_products_with_page_evidence(products, opener=_fake_urlopen)

        self.assertEqual(len(enriched), 1)
        self.assertIn("얼큰이", enriched[0]["description"])
        self.assertGreaterEqual(len(enriched[0]["page_snippets"]), 1)


if __name__ == "__main__":
    unittest.main()
