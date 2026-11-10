import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.compute_change_manifest import COMMON_CACHE_TOOL_EXCLUSIONS, UPLOAD_ONLY_EXCLUSIONS, generate_gcloudignore, manifest_lines


class ChangeManifestTests(unittest.TestCase):
    def test_exclusion_contract_and_audit_visibility(self):
        self.assertEqual(UPLOAD_ONLY_EXCLUSIONS[:len(COMMON_CACHE_TOOL_EXCLUSIONS)], COMMON_CACHE_TOOL_EXCLUSIONS)
        self.assertEqual(generate_gcloudignore(), Path(".gcloudignore").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / ".gitignore").write_text(".data/\n.venv/\n", encoding="utf-8")
            (root / "source.py").write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
            (root / "source.py").write_text("new\n", encoding="utf-8")
            (root / ".book-cardnews-runs").mkdir(); (root / ".book-cardnews-runs" / "receipt.json").write_text("{}")
            (root / ".data").mkdir(); (root / ".data" / "db.bin").write_bytes(b"db")
            (root / ".venv").mkdir(); (root / ".venv" / "cache").write_bytes(b"x")
            lines = manifest_lines(root, "audit")
            text = "\n".join(lines)
            self.assertIn("source.py", text)
            self.assertIn(".book-cardnews-runs/receipt.json", text)
            self.assertIn(".data/db.bin", text)
            self.assertNotIn(".venv/cache", text)


if __name__ == "__main__": unittest.main()
