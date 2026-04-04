import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
if str(DEPLOY) not in sys.path:
    sys.path.insert(0, str(DEPLOY))

from analyze_account import normalize_taxonomy_result


class TestNormalizeTaxonomyResult(unittest.TestCase):
    def test_falls_back_to_heuristics_when_llm_payload_missing(self):
        result = normalize_taxonomy_result(
            {},
            biography="B2B consulting for founders. Book a call and get the free guide.",
            external_url="https://growth.example.com/apply",
            other_socials={"youtube": "https://youtube.com/@growth"},
            captions_text="DM me consulting if you want help scaling.",
        )

        self.assertTrue(result["has_signals"])
        self.assertEqual(result["offer_type"], "consulting")
        self.assertEqual(result["funnel_type"], "book_call")
        self.assertEqual(result["platform_mix"], "multi_channel")
        self.assertEqual(result["audience_type"], "b2b")
        self.assertIn(result["icp"], {"ICP1", "ICP2", "ICP3", "ICP4", "ICP5"})

    def test_keeps_valid_llm_categories_and_normalizes_platform_fields(self):
        result = normalize_taxonomy_result(
            {
                "has_signals": True,
                "signals_found": ["newsletter", "join community"],
                "reasoning": "Strong audience monetization signals.",
                "offer_type": "newsletter",
                "funnel_type": "subscribe_join",
                "business_model": "community_business",
                "audience_type": "creator_economy",
                "monetization_strength": "strong",
                "cta_keywords": ["join community"],
                "bio_keywords": ["creator economy"],
                "confidence": 0.88,
                "icp": "ICP3",
            },
            biography="Creator economy newsletter for operators.",
            external_url="https://newsletter.example.com",
            other_socials={"twitter": {"url": "https://twitter.com/example", "followers": 1000}},
            captions_text="Join our community this week.",
        )

        self.assertEqual(result["offer_type"], "newsletter")
        self.assertEqual(result["platform_mix"], "multi_channel")
        self.assertEqual(result["primary_domain"], "newsletter.example.com")
        self.assertEqual(result["icp"], "ICP3")


if __name__ == "__main__":
    unittest.main()
