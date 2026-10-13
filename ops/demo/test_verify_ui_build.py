import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("verify_ui_build", Path(__file__).with_name("verify_ui_build.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "index.html").write_text("ordinary frontend")
        self.sha = "a" * 40
        self.manifest = {
            "contract": 1, "source_sha": self.sha, "source_dirty": False,
            "test_only": False, "node": "24.20.0", "npm": "11.19.0",
            "files": {"index.html": hashlib.sha256(b"ordinary frontend").hexdigest()},
        }

    def verify(self):
        (self.root / "build-manifest.json").write_text(json.dumps(self.manifest))
        MODULE.verify(self.root, self.sha)

    def test_clean_matching_build_passes(self):
        self.verify()

    def test_wrong_commit_dirty_test_or_runtime_build_fails(self):
        for field, value in (("source_sha", "b" * 40), ("source_dirty", True),
                             ("test_only", True), ("node", "24.21.0"), ("npm", "11.20.0")):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                with self.assertRaises(ValueError):
                    self.verify()
                self.manifest[field] = original

    def test_tampered_or_unlisted_files_fail(self):
        (self.root / "unexpected.json").write_text("not part of the build")
        with self.assertRaises(ValueError):
            self.verify()
        (self.root / "unexpected.json").unlink()
        (self.root / "index.html").write_text("changed")
        with self.assertRaises(ValueError):
            self.verify()

    def test_parent_escape_and_symlink_fail(self):
        self.manifest["files"] = {"../index.html": "a" * 64}
        with self.assertRaises(ValueError):
            self.verify()
        self.manifest["files"] = {"index.html": hashlib.sha256(b"ordinary frontend").hexdigest()}
        (self.root / "index.html").unlink()
        (self.root / "target.html").write_text("ordinary frontend")
        (self.root / "index.html").symlink_to("target.html")
        with self.assertRaises(ValueError):
            self.verify()


if __name__ == "__main__":
    unittest.main(verbosity=2)
