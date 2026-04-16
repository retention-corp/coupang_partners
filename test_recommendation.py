import unittest

from recommendation import (
    build_assist_response,
    build_search_queries,
    infer_exclusion_terms,
    normalize_request,
    recommend_products,
)


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
        self.assertEqual(normalized["intent_type"], "recommendation")

    def test_infer_exclusion_terms_for_vase_query(self):
        normalized = normalize_request({"query": "저렴한 꽃병"})
        self.assertEqual(
            infer_exclusion_terms(normalized),
            ["식물", "화초", "스투키", "몬스테라", "공기정화"],
        )

    def test_infer_exclusion_terms_for_vacuum_category(self):
        normalized = normalize_request(
            {"query": "무선청소기 추천", "constraints": {"category": "무선청소기"}}
        )
        self.assertEqual(
            infer_exclusion_terms(normalized),
            ["업소용", "산업용", "대형"],
        )

    def test_normalize_request_infers_oat_milk_must_have_terms(self):
        normalized = normalize_request({"query": "브레빌 870으로 라떼 만들 오트밀크 추천"})
        self.assertIn("오트", normalized["must_have"])
        self.assertIn("밀크", normalized["must_have"])

    def test_infer_exclusion_terms_for_oat_milk_query(self):
        normalized = normalize_request({"query": "오트밀크 추천"})
        self.assertEqual(
            infer_exclusion_terms(normalized),
            ["물티슈", "양말", "제로사이다", "사이다", "키친타월", "휴지"],
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
        self.assertEqual(recommendations[0]["evidence"]["confidence"], "high")

    def test_recommend_products_raises_confidence_with_landing_page_evidence(self):
        products = [
            {
                "productId": 1,
                "productName": "특대형 KF94 마스크",
                "productPrice": 7900,
                "productUrl": "https://example.com/1",
                "page_title": "특대형 빅사이즈 KF94 마스크 30매",
                "page_description": "대두도 편하게 쓸 수 있는 특대형 KF94 마스크",
                "page_snippets": ["얼큰이 고객도 압박감이 덜한 넉넉한 핏"],
                "page_facts": ["Landing page snippet count: 1"],
            }
        ]

        recommendations = recommend_products(
            query="대두가 써도 안 아픈 KF94 마스크 추천",
            products=products,
        )

        self.assertEqual(recommendations[0]["evidence"]["confidence"], "high")
        self.assertIn("Landing page snippet count: 1", recommendations[0]["evidence"]["facts"])

    def test_recommend_products_filters_generic_irrelevant_results(self):
        products = [
            {
                "productId": 1,
                "productName": "물티슈 캡형 100매",
                "productPrice": 3000,
                "productUrl": "https://example.com/1",
            },
            {
                "productId": 2,
                "productName": "바리스타 오트 밀크 1L",
                "productPrice": 4500,
                "productUrl": "https://example.com/2",
            },
            {
                "productId": 3,
                "productName": "무지 양말 10켤레",
                "productPrice": 9900,
                "productUrl": "https://example.com/3",
            },
        ]

        recommendations = recommend_products(
            query="브레빌 870으로 라떼 만들 오트밀크 추천",
            products=products,
        )

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["product_id"], "2")

    def test_normalize_request_detects_extremum_search_and_cleans_query(self):
        normalized = normalize_request({"query": "쿠팡에서 AUX 선 제일 긴거 제품 찾아줘"})

        self.assertEqual(normalized["intent_type"], "extremum_search")
        self.assertEqual(normalized["sort_key"], "length_m")
        self.assertEqual(normalized["sort_direction"], "desc")
        self.assertEqual(normalized["query_core"], "AUX 선")
        self.assertEqual(build_search_queries(normalized), ["AUX 선"])

    def test_recommend_products_prefers_longest_explicit_length_for_cable_query(self):
        products = [
            {
                "productId": 1,
                "productName": "AUX 케이블 3.5mm 단자 0.5m",
                "productPrice": 4500,
                "productUrl": "https://example.com/1",
            },
            {
                "productId": 2,
                "productName": "AUX 케이블 10m",
                "productPrice": 12500,
                "productUrl": "https://example.com/2",
            },
            {
                "productId": 3,
                "productName": "AUX 케이블 2m",
                "productPrice": 7000,
                "productUrl": "https://example.com/3",
            },
        ]

        recommendations = recommend_products(
            query="AUX 선 제일 긴거",
            products=products,
            intent_type="extremum_search",
            sort_key="length_m",
            sort_direction="desc",
        )

        self.assertEqual(recommendations[0]["product_id"], "2")
        self.assertEqual(recommendations[0]["comparison"]["label"], "10m")
        self.assertIn("longest among the returned listings", recommendations[0]["rationale"])
        self.assertEqual(recommendations[1]["comparison"]["label"], "2m")

    def test_build_assist_response_uses_conservative_summary_when_length_missing(self):
        normalized = normalize_request({"query": "AUX 선 제일 긴거"})
        recommendations = recommend_products(
            query="AUX 선 제일 긴거",
            products=[
                {
                    "productId": 1,
                    "productName": "AUX 케이블",
                    "productPrice": 5000,
                    "productUrl": "https://example.com/1",
                }
            ],
            intent_type=normalized["intent_type"],
            sort_key=normalized["sort_key"],
            sort_direction=normalized["sort_direction"],
        )

        response = build_assist_response(
            normalized=normalized,
            search_plan=build_search_queries(normalized),
            recommendations=recommendations,
            query_id="qid-1",
        )

        self.assertIn("불확실성", response["summary"])
        self.assertIn("길이 표기가 없어", response["summary"])
        self.assertIn("uncertain", " ".join(response["risks"]))

    def test_build_assist_response_adds_low_confidence_warning(self):
        normalized = normalize_request({"query": "무선청소기 추천"})
        recommendations = recommend_products(
            query="무선청소기 추천",
            products=[
                {
                    "productId": 1,
                    "productName": "무선청소기",
                    "productPrice": 5000,
                    "productUrl": "https://example.com/1",
                }
            ],
            intent_type=normalized["intent_type"],
            sort_key=normalized["sort_key"],
            sort_direction=normalized["sort_direction"],
        )

        response = build_assist_response(
            normalized=normalized,
            search_plan=build_search_queries(normalized),
            recommendations=recommendations,
            query_id="qid-2",
        )

        self.assertIn("메타데이터 중심 매칭", response["summary"])
        self.assertEqual(recommendations[0]["evidence"]["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
