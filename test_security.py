import unittest

from security import is_client_allowlisted


class SecurityTests(unittest.TestCase):
    def test_allowlist_exact_match(self):
        self.assertTrue(is_client_allowlisted("hermes-agent", ("hermes-agent",), True))

    def test_allowlist_prefix_wildcard(self):
        self.assertTrue(is_client_allowlisted("claw-shopping-beta", ("claw-*",), True))

    def test_allowlist_rejects_unknown_when_enforced(self):
        self.assertFalse(is_client_allowlisted("unknown-agent", ("claw-*", "codex"), True))

    def test_allowlist_disabled_allows_missing_client(self):
        self.assertTrue(is_client_allowlisted(None, (), False))


if __name__ == "__main__":
    unittest.main()
