import unittest
from types import SimpleNamespace

from _path import *  # noqa: F401,F403
from tpm.dependency.graph import DependencyGraph
from tpm.dependency.parser import parse_depends_field


def make_graph(edges):
    """edges: {pkg: depends_field_str}. Builds packages + deps maps."""
    deps = {name: parse_depends_field(field) for name, field in edges.items()}
    names = set(edges)
    for groups in deps.values():
        for g in groups:
            names.update(g.names())
    packages = {n: SimpleNamespace(name=n, installed_size=1024) for n in names}
    return DependencyGraph(packages=packages, deps=deps)


class TestClassifySpec53A(unittest.TestCase):
    """Fixture A: A->B, A->C, D->C."""

    def setUp(self):
        self.graph = make_graph({"A": "B, C", "D": "C"})

    def test_single_use(self):
        cls, n = self.graph.classify("B", explicit_set=set(), essential_set=set())
        self.assertEqual(cls, "SINGLE-USE")
        self.assertEqual(n, 1)

    def test_shared(self):
        cls, n = self.graph.classify("C", explicit_set=set(), essential_set=set())
        self.assertEqual(cls, "SHARED")
        self.assertEqual(n, 2)

    def test_explicit_and_essential(self):
        cls, _ = self.graph.classify("B", explicit_set={"B"}, essential_set=set())
        self.assertEqual(cls, "EXPLICIT")
        cls, _ = self.graph.classify("B", explicit_set=set(), essential_set={"B"})
        self.assertEqual(cls, "ESSENTIAL")


class TestOrphanAfterRemovalSpec53B(unittest.TestCase):
    """Fixture B: A->B, B->C, D->C; removing A orphans B but not C."""

    def setUp(self):
        self.graph = make_graph({"A": "B", "B": "C", "D": "C"})

    def test_direct_deps(self):
        self.assertEqual(self.graph.direct_deps("B"), ["C"])
        self.assertEqual(self.graph.direct_rdeps("C"), ["B", "D"])

    def test_recursive(self):
        self.assertEqual(self.graph.recursive_deps("A"), ["B", "C"])
        self.assertEqual(self.graph.recursive_rdeps("C"), ["A", "B", "D"])


class TestSharedRetentionSpec53C(unittest.TestCase):
    """Fixture C: A->B, A->C, D->C, E->C; C is shared by D and E."""

    def setUp(self):
        self.graph = make_graph(
            {"A": "B, C", "D": "C", "E": "C"})

    def test_c_rdeps(self):
        self.assertEqual(self.graph.direct_rdeps("C"), ["A", "D", "E"])

    def test_b_is_single_use_of_a(self):
        self.assertEqual(self.graph.direct_rdeps("B"), ["A"])


class TestCycleSafety(unittest.TestCase):
    """X->Y, Y->X plus Z->X: walks must terminate and be correct."""

    def setUp(self):
        self.graph = make_graph({"X": "Y", "Y": "X", "Z": "X"})

    def test_recursive_deps_terminates(self):
        self.assertEqual(self.graph.recursive_deps("X"), ["Y"])
        self.assertEqual(self.graph.recursive_deps("Z"), ["X", "Y"])

    def test_recursive_rdeps_terminates(self):
        self.assertEqual(sorted(self.graph.recursive_rdeps("X")), ["Y", "Z"])
        self.assertEqual(sorted(self.graph.recursive_rdeps("Y")), ["X", "Z"])


if __name__ == "__main__":
    unittest.main()
