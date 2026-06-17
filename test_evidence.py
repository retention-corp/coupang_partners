import unittest

from evidence import build_evidence


class EvidenceTests(unittest.TestCase):
    def test_build_evidence_accepts_string_snippets_and_listing_signals(self):
        product = {
            "title": "저소음 초경량 원룸 무선청소기",
            "rating": 4.6,
            "review_count": 132,
            "deeplink": "https://example.com/p/1",
        }

        evidence = build_evidence(
            product,
            "30만원 이하 원룸용 저소음 무선청소기",
            snippets=["리뷰: 생각보다 조용한 편", {"text": "리뷰: 1인 가구에 잘 맞음", "source": "manual"}],
        )

        self.assertGreater(evidence["score"], 0)
        self.assertIn("저소음", evidence["matched_terms"])
        self.assertIn("Listing signals: 저소음, 원룸, 초경량", " | ".join(evidence["facts"]))
        self.assertEqual(len(evidence["snippets"]), 2)
        self.assertFalse(any("No external evidence snippet supplied" in risk for risk in evidence["risks"]))

    def test_build_evidence_uses_landing_page_snippets_and_facts(self):
        product = {
            "title": "특대형 KF94 마스크",
            "page_title": "특대형 빅사이즈 KF94 마스크 30매",
            "page_description": "대두도 편하게 쓸 수 있는 특대형 KF94 마스크",
            "page_snippets": ["얼큰이 고객도 압박감이 덜한 넉넉한 핏"],
            "page_facts": ["Landing page snippet count: 1"],
            "review_count": 5,
            "deeplink": "https://example.com/p/1",
        }

        evidence = build_evidence(product, "대두가 써도 안 아픈 KF94 마스크 추천")

        self.assertIn("kf94", evidence["matched_terms"])
        self.assertIn("마스크", evidence["matched_terms"])
        self.assertIn("Landing page snippet count: 1", evidence["facts"])
        self.assertFalse(any("rationale relies on product metadata" in risk for risk in evidence["risks"]))


if __name__ == "__main__":
    unittest.main()
