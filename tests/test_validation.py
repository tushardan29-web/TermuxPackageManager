import unittest

from _path import *  # noqa: F401,F403
from tpm.validation import validate_package_name


class TestValidatePackageName(unittest.TestCase):
    def test_accepts_valid(self):
        for name in ("python", "libfoo2", "g++", "foo-bar.baz", "a"):
            self.assertEqual(validate_package_name(name), name)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_package_name("")

    def test_rejects_shell_metachars(self):
        for name in ("foo;rm", "$(x)", "foo|bar", "`id`", "a\tb", "\n"):
            with self.assertRaises(ValueError):
                validate_package_name(name)

    def test_rejects_spaces(self):
        with self.assertRaises(ValueError):
            validate_package_name("foo bar")

    def test_rejects_traversal(self):
        for name in ("../etc", "..", "/etc/passwd", "./x"):
            with self.assertRaises(ValueError):
                validate_package_name(name)

    def test_rejects_uppercase(self):
        with self.assertRaises(ValueError):
            validate_package_name("Foo")

    def test_rejects_leading_non_alnum(self):
        with self.assertRaises(ValueError):
            validate_package_name("-foo")


if __name__ == "__main__":
    unittest.main()
