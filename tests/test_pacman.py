"""Tests for PacmanBackend with mock desc files."""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

# Ensure src/ is on path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_desc(directory, name, version="1.0-1", arch="aarch64",
               size="1048576", reason="0", deps=None, desc="A package"):
    """Create a pacman desc file in the given directory."""
    pkg_dir = os.path.join(directory, name)
    os.makedirs(pkg_dir, exist_ok=True)
    lines = [
        f"%NAME%",
        name,
        f"%VERSION%",
        version,
        f"%ARCH%",
        arch,
        f"%SIZE%",
        size,
        f"%REASON%",
        reason,
        f"%DESC%",
        desc,
    ]
    if deps:
        lines.append("%DEPENDS%")
        lines.extend(deps)
    with open(os.path.join(pkg_dir, "desc"), "w") as f:
        f.write("\n".join(lines) + "\n")


class TestPacmanBackend(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.local_dir = os.path.join(self.tmpdir, "var", "lib", "pacman", "local")
        os.makedirs(self.local_dir)
        # Remove the ALPM_DB_VERSION marker so _distro_dirs works
        # (it's a directory, not a file)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_list_installed_basic(self):
        from tpm.package.backend import PacmanBackend
        _make_desc(self.local_dir, "python", version="3.14.1-1",
                   size="26843545", reason="0", desc="Python interpreter")
        _make_desc(self.local_dir, "openssl", version="3.2.1-1",
                   size="1572864", reason="1",
                   deps=["glibc>=2.17"], desc="SSL library")
        _make_desc(self.local_dir, "bash", version="5.2.26-1",
                   size="7340032", reason="0",
                   deps=["glibc", "ncurses>=6.4"], desc="Bash shell")

        backend = PacmanBackend(prefix=self.tmpdir)
        records = list(backend.list_installed())
        self.assertEqual(len(records), 3)
        names = {r["name"] for r in records}
        self.assertEqual(names, {"bash", "openssl", "python"})

    def test_explicit_vs_dependency(self):
        from tpm.package.backend import PacmanBackend
        _make_desc(self.local_dir, "git", reason="0")      # explicit
        _make_desc(self.local_dir, "perl", reason="1")      # dependency

        backend = PacmanBackend(prefix=self.tmpdir)
        records = list(backend.list_installed())
        by_name = {r["name"]: r for r in records}
        self.assertEqual(by_name["git"]["explicitly_installed"], 1)
        self.assertEqual(by_name["perl"]["explicitly_installed"], 0)

    def test_dependency_parsing(self):
        from tpm.package.backend import PacmanBackend
        _make_desc(self.local_dir, "curl", deps=[
            "openssl>=3.0", "zlib", "nghttp2"
        ])
        backend = PacmanBackend(prefix=self.tmpdir)
        records = list(backend.list_installed())
        self.assertEqual(len(records), 1)
        groups = records[0]["depends_groups"]
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].alternatives[0].name, "openssl")
        self.assertEqual(groups[0].alternatives[0].version_constraint, ">=3.0")
        self.assertEqual(groups[1].alternatives[0].name, "zlib")
        self.assertEqual(groups[2].alternatives[0].name, "nghttp2")

    def test_empty_db(self):
        from tpm.package.backend import PacmanBackend
        backend = PacmanBackend(prefix=self.tmpdir)
        records = list(backend.list_installed())
        self.assertEqual(records, [])

    def test_explicit_names(self):
        from tpm.package.backend import PacmanBackend
        _make_desc(self.local_dir, "python", reason="0")
        _make_desc(self.local_dir, "openssl", reason="1")
        _make_desc(self.local_dir, "git", reason="0")

        backend = PacmanBackend(prefix=self.tmpdir)
        names = backend.explicit_names()
        self.assertEqual(names, {"python", "git"})

    def test_installed_size(self):
        from tpm.package.backend import PacmanBackend
        _make_desc(self.local_dir, "rust", size="572662272")
        backend = PacmanBackend(prefix=self.tmpdir)
        records = list(backend.list_installed())
        self.assertEqual(records[0]["installed_size"], 572662272)


class TestDetectBackend(unittest.TestCase):
    def test_apt_preferred_when_available(self):
        """On this system, apt is the active manager."""
        from tpm.environment import detect_environment
        env = detect_environment()
        if env["manager"] == "apt":
            from tpm.package.backend import detect_backend
            b = detect_backend(env)
            self.assertIsNotNone(b)
            self.assertEqual(b.name, "apt/dpkg")


class TestEnvironmentDetection(unittest.TestCase):
    def test_detect_returns_required_keys(self):
        from tpm.environment import detect_environment
        env = detect_environment()
        self.assertIn("termux", env)
        self.assertIn("prefix", env)
        self.assertIn("manager", env)
        self.assertIn("platform", env)
        self.assertIsInstance(env["prefix"], str)


if __name__ == "__main__":
    unittest.main()
