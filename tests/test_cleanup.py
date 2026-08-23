"""Tests for cleanup backends and config module."""

import os
import shutil
import tempfile
import unittest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestCleanupResult(unittest.TestCase):
    def test_to_dict(self):
        from tpm.cleanup import CleanupResult
        r = CleanupResult()
        r.cleaned_bytes = 1024
        r.items_cleaned = 5
        r.errors = ["bad file"]
        d = r.to_dict()
        self.assertEqual(d["cleaned_bytes"], 1024)
        self.assertEqual(d["items_cleaned"], 5)
        self.assertEqual(d["errors"], ["bad file"])


class TestCleanTree(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_clean_empty_dir(self):
        from tpm.cleanup import _clean_tree
        d = os.path.join(self.tmpdir, "empty")
        os.makedirs(d)
        r = _clean_tree(d, dry_run=True)
        self.assertEqual(r.cleaned_bytes, 0)

    def test_clean_files_dry_run(self):
        from tpm.cleanup import _clean_tree
        d = os.path.join(self.tmpdir, "stuff")
        os.makedirs(d)
        for i in range(3):
            with open(os.path.join(d, f"f{i}.txt"), "w") as f:
                f.write("x" * (100 * (i + 1)))
        r = _clean_tree(d, dry_run=True)
        self.assertEqual(r.items_cleaned, 3)
        self.assertGreater(r.cleaned_bytes, 0)
        # Files still exist in dry run
        self.assertEqual(len(os.listdir(d)), 3)

    def test_clean_files_real(self):
        from tpm.cleanup import _clean_tree
        d = os.path.join(self.tmpdir, "stuff")
        os.makedirs(d)
        for i in range(3):
            with open(os.path.join(d, f"f{i}.txt"), "w") as f:
                f.write("x" * 100)
        r = _clean_tree(d, dry_run=False)
        self.assertEqual(r.items_cleaned, 3)
        self.assertEqual(len(os.listdir(d)), 0)

    def test_clean_nested(self):
        from tpm.cleanup import _clean_tree
        d = os.path.join(self.tmpdir, "nested")
        sub = os.path.join(d, "a", "b")
        os.makedirs(sub)
        with open(os.path.join(sub, "file.txt"), "w") as f:
            f.write("x" * 500)
        r = _clean_tree(d, dry_run=True)
        self.assertEqual(r.items_cleaned, 1)
        self.assertEqual(r.cleaned_bytes, 500)


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        from tpm.config import load_config
        cfg = load_config(path="/nonexistent/path")
        self.assertEqual(cfg["scan_depth"], 2)
        self.assertFalse(cfg["follow_symlinks"])

    def test_load_custom(self):
        from tpm.config import load_config
        tmpdir = tempfile.mkdtemp()
        cfg_path = os.path.join(tmpdir, "config.toml")
        with open(cfg_path, "w") as f:
            f.write('scan_depth = 5\nfollow_symlinks = true\ncolor = false\n')
        cfg = load_config(cfg_path)
        self.assertEqual(cfg["scan_depth"], 5)
        self.assertTrue(cfg["follow_symlinks"])
        self.assertFalse(cfg["color"])
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
