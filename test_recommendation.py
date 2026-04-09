import unittest

from recommendation import build_search_queries, normalize_request, recommend_products


class RecommendationTests(unittest.TestCase):
    def test_normalize_request_and_build_search_queries(self):
        normalized = normalize_request(
            {
                "query": "30만원 이하 무선청소기",
                "constraints": {
                    "category": "무선청소기",
                    "must_have": ["저소음", "원룸"],
                    "avoid": ["대형"],
                },
            }
        )

        self.assertEqual(normalized["budget"], 300000)
        self.assertEqual(normalized["avoid"], ["대형"])
        self.assertEqual(
            build_search_queries(normalized),
            ["30만원 이하 무선청소기", "저소음 30만원 이하 무선청소기", "원룸 30만원 이하 무선청소기"],
        )

    def test_recommend_products_prefers_budget_and_evidence(self):
        products = [
            {
                "productId": 1,
                "productName": "저소음 원룸 무선청소기",
                "productPrice": 109000,
                "productUrl": "https://example.com/1",
                "reviewCount": 120,
                "ratingAverage": 4.7,
            },
            {
                "productId": 2,
                "productName": "대형 무선청소기",
                "productPrice": 409000,
                "productUrl": "https://example.com/2",
                "reviewCount": 15,
                "ratingAverage": 3.8,
            },
        ]

        recommendations = recommend_products(
            query="30만원 이하 무선청소기, 원룸용",
            products=products,
            budget=300000,
            evidence_snippets=[{"text": "리뷰: 원룸 자취방에 좋음"}],
        )

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["product_id"], "1")
        self.assertIn("원룸", recommendations[0]["rationale"])


if __name__ == "__main__":
    unittest.main()
