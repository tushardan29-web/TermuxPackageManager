import unittest

from _path import *  # noqa: F401,F403
from tpm.formatter import format_size, parse_installed_size


class TestFormatSize(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_size(0), "0 B")

    def test_kib(self):
        self.assertEqual(format_size(1024), "1.0 KiB")
        self.assertEqual(format_size(1536), "1.5 KiB")

    def test_mib(self):
        self.assertEqual(format_size(1048576), "1.0 MiB")

    def test_bytes(self):
        self.assertEqual(format_size(512), "512 B")

    def test_none(self):
        self.assertEqual(format_size(None), "?")

    def test_precision(self):
        self.assertEqual(format_size(1536, precision=0), "2 KiB")


class TestParseInstalledSize(unittest.TestCase):
    def test_kib_to_bytes(self):
        self.assertEqual(parse_installed_size(184), 184 * 1024)

    def test_string_input(self):
        self.assertEqual(parse_installed_size("12"), 12288)

    def test_invalid(self):
        self.assertIsNone(parse_installed_size(None))
        self.assertIsNone(parse_installed_size("abc"))


if __name__ == "__main__":
    unittest.main()
