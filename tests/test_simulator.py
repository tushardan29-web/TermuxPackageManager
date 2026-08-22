import unittest
from types import SimpleNamespace

from _path import *  # noqa: F401,F403
from tpm.dependency.graph import DependencyGraph
from tpm.dependency.parser import parse_depends_field
from tpm.removal.simulator import SimulationResult, simulate_removal


def build(packages_sizes, edges):
    deps = {name: parse_depends_field(field) for name, field in edges.items()}
    names = set(edges)
    for groups in deps.values():
        for g in groups:
            names.update(g.names())
    packages = {
        n: SimpleNamespace(name=n, installed_size=packages_sizes.get(n, 100))
        for n in names
    }
    return DependencyGraph(packages=packages, deps=deps)


class TestRequestedListed(unittest.TestCase):
    def test_requested_in_result(self):
        graph = build({}, {"A": "", "B": ""})
        res = simulate_removal(graph, ["A"])
        self.assertIn("A", res.requested)


class TestCascade(unittest.TestCase):
    """Fixture B: A->B, B->C, D->C."""

    def setUp(self):
        self.graph = build(
            {"A": 500, "B": 300, "C": 200, "D": 400},
            {"A": "B", "B": "C", "D": "C"})

    def test_cascade_removes_orphaned_chain(self):
        res = simulate_removal(self.graph, ["A"], explicit_set={"D"},
                               essential_set=set())
        self.assertIn("A", res.requested)
        self.assertIn("B", res.cascade_removable)
        self.assertNotIn("C", res.cascade_removable)
        self.assertNotIn("D", res.cascade_removable)

    def test_explicit_never_cascaded(self):
        res = simulate_removal(self.graph, ["A"], explicit_set={"B", "D"},
                               essential_set=set())
        self.assertNotIn("B", res.cascade_removable)

    def test_recovery_bytes_requested_plus_cascade(self):
        res = simulate_removal(self.graph, ["A"], explicit_set={"D"},
                               essential_set=set())
        self.assertEqual(res.package_recovery_bytes, 500 + 300)
        self.assertEqual(res.total_recovery_bytes, res.package_recovery_bytes)


class TestEssentialProtection(unittest.TestCase):
    def test_essential_blocked(self):
        graph = build({}, {"base": "", "app": ""})
        res = simulate_removal(graph, ["base"], explicit_set=set(),
                               essential_set={"base"})
        self.assertIn("base", res.protected_essential)
        self.assertTrue(any("base" in r for r in res.blocked_reasons))
        self.assertNotIn("base", res.requested)


class TestSharedRetained(unittest.TestCase):
    """Fixture C: A->B, A->C, D->C, E->C."""

    def setUp(self):
        self.graph = build({}, {"A": "B, C", "D": "C", "E": "C"})

    def test_shared_dep_retained(self):
        res = simulate_removal(self.graph, ["A"], explicit_set={"D", "E"},
                               essential_set=set())
        self.assertIn("C", res.retained_shared)
        self.assertIn("B", res.cascade_removable)


class TestBrokenDetection(unittest.TestCase):
    def test_remaining_package_left_broken(self):
        graph = build({}, {"A": "libdep", "libdep": ""})
        res = simulate_removal(graph, ["libdep"], explicit_set={"A"},
                               essential_set=set())
        self.assertTrue(any(e.startswith("A needs libdep") for e in res.broken))

    def test_no_break_when_dependent_also_removed(self):
        graph = build({}, {"A": "libdep", "libdep": ""})
        res = simulate_removal(graph, ["A", "libdep"], explicit_set=set(),
                               essential_set=set())
        self.assertEqual(res.broken, [])


class TestJsonShape(unittest.TestCase):
    def test_to_dict_keys(self):
        res = SimulationResult()
        d = res.to_dict()
        expected = {
            "requested", "cascade_removable", "retained_shared", "broken",
            "protected_essential", "package_recovery_bytes",
            "total_recovery_bytes", "blocked_reasons",
        }
        self.assertEqual(set(d.keys()), expected)

    def test_not_installed_blocked(self):
        graph = build({}, {"A": ""})
        res = simulate_removal(graph, ["ghost"], explicit_set=set(),
                               essential_set=set())
        self.assertTrue(any("not installed" in r for r in res.blocked_reasons))


if __name__ == "__main__":
    unittest.main()
