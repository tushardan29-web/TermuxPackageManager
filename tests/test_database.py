import shutil
import tempfile
import unittest

from _path import *  # noqa: F401,F403
from tpm.database import Database
from tpm.dependency.parser import parse_depends_field


class TestDatabaseRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tpm-test-")
        self.db = Database(f"{self.tmpdir}/tpm.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _records(self):
        return [
            {
                "name": "vim",
                "version": "9.0",
                "architecture": "aarch64",
                "status": "install ok installed",
                "installed_size": 4096,
                "explicitly_installed": 1,
                "essential": 0,
                "priority": "optional",
                "source": "dpkg",
                "description": "editor",
                "depends_groups": parse_depends_field("libcli (>= 1.0)"),
            },
            {
                "name": "viewer",
                "version": "2.1",
                "architecture": "aarch64",
                "status": "install ok installed",
                "installed_size": 2048,
                "explicitly_installed": 0,
                "essential": 0,
                "priority": "optional",
                "source": "dpkg",
                "description": "viewer",
                "depends_groups": parse_depends_field("libcli | libcli-lite, zlib:any"),
            },
        ]

    def test_roundtrip_preserves_groups(self):
        self.db.replace_packages([dict(r) for r in self._records()])
        entries = {e["row"]["name"]: e for e in self.db.all_packages()}

        self.assertEqual(set(entries), {"vim", "viewer"})

        vim = entries["vim"]
        self.assertEqual(vim["row"]["installed_size"], 4096)
        self.assertEqual(len(vim["depends_groups"]), 1)
        self.assertEqual(
            [a["name"] for a in vim["depends_groups"][0]["alternatives"]],
            ["libcli"])
        self.assertEqual(
            vim["depends_groups"][0]["alternatives"][0]["version_constraint"],
            ">= 1.0")

        viewer = entries["viewer"]
        groups = viewer["depends_groups"]
        self.assertEqual(len(groups), 2)
        alt_names = [a["name"] for a in groups[0]["alternatives"]]
        self.assertEqual(alt_names, ["libcli", "libcli-lite"])
        self.assertEqual([a["name"] for a in groups[1]["alternatives"]],
                         ["zlib"])

    def test_get_package(self):
        self.db.replace_packages([dict(r) for r in self._records()])
        entry = self.db.get_package("vim")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["row"]["version"], "9.0")
        self.assertIsNone(self.db.get_package("nonexistent"))

    def test_replace_clears_old_rows(self):
        self.db.replace_packages([dict(r) for r in self._records()])
        self.db.replace_packages([
            dict(self._records()[0], name="only")])
        names = [e["row"]["name"] for e in self.db.all_packages()]
        self.assertEqual(names, ["only"])
        self.assertIsNone(self.db.get_package("vim"))


if __name__ == "__main__":
    unittest.main()
