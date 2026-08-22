import unittest

from _path import *  # noqa: F401,F403
from tpm.dependency.parser import (
    Dependency,
    DependencyGroup,
    group_satisfied,
    parse_dependency_group,
    parse_depends_field,
)


class TestParseDependsField(unittest.TestCase):
    def test_plain_name(self):
        groups = parse_depends_field("foo")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].names(), ["foo"])
        self.assertIsNone(groups[0].alternatives[0].version_constraint)

    def test_version_constraint(self):
        groups = parse_depends_field("foo (>= 1.2)")
        self.assertEqual(groups[0].names(), ["foo"])
        self.assertEqual(groups[0].alternatives[0].version_constraint, ">= 1.2")

    def test_alternatives(self):
        groups = parse_depends_field("foo | bar")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].names(), ["foo", "bar"])

    def test_arch_qualifier_stripped(self):
        groups = parse_depends_field("qux:any")
        self.assertEqual(groups[0].names(), ["qux"])

    def test_full_mixed_field(self):
        groups = parse_depends_field("a (>= 1.0), b | c, d:any")
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].names(), ["a"])
        self.assertEqual(groups[0].alternatives[0].version_constraint, ">= 1.0")
        self.assertEqual(groups[1].names(), ["b", "c"])
        self.assertEqual(groups[2].names(), ["d"])

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            parse_depends_field("b |")

    def test_empty_field(self):
        self.assertEqual(parse_depends_field(""), [])
        self.assertEqual(parse_depends_field("   "), [])
        self.assertEqual(parse_depends_field(None), [])


class TestParseDependencyGroup(unittest.TestCase):
    def test_single(self):
        g = parse_dependency_group("vim")
        self.assertIsInstance(g, DependencyGroup)
        self.assertEqual(g.alternatives, [Dependency(name="vim", version_constraint=None)])

    def test_unbalanced_parens_raises(self):
        with self.assertRaises(ValueError):
            parse_dependency_group("foo (>= 1.0")

    def test_group_satisfied(self):
        g = parse_dependency_group("foo | bar")
        self.assertTrue(group_satisfied(g, {"bar"}))
        self.assertFalse(group_satisfied(g, {"baz"}))


if __name__ == "__main__":
    unittest.main()
