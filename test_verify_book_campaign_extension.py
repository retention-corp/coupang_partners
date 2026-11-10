import unittest

from scripts.verify_book_campaign_extension import build_parser, validate_args


class VerifyBookCampaignExtensionTests(unittest.TestCase):
    def test_verify_only_rejects_mutating_flags(self):
        args = build_parser().parse_args(["--verify-only", "--pilot-campaign", "p", "--pilot-slug", "ps", "--canary-campaign", "c", "--canary-slug", "cs", "--base-url-public", "http://public", "--base-url-ops", "http://ops", "--create-canary"])
        with self.assertRaises(ValueError): validate_args(args)

    def test_execution_requires_exact_canary_actions(self):
        args = build_parser().parse_args(["--pilot-campaign", "p", "--pilot-slug", "ps", "--canary-campaign", "canary-smoke-abcdef123456-deadbeefcafe", "--canary-target", "https://www.coupang.com/", "--base-url-public", "http://public", "--base-url-ops", "http://ops"])
        with self.assertRaises(ValueError): validate_args(args)


if __name__ == "__main__": unittest.main()
