"""Dependency graph built from the SQLite index.

Forward edges:  A depends on B
Reverse edges:  B is required by A

Pure in-memory adjacency structures; no shell calls.
"""

from collections import defaultdict


class DependencyGraph:
    def __init__(self, packages, deps):
        """packages: {name: PackageInfo}; deps: {name: [DependencyGroup]}"""
        self.packages = packages
        self.deps = {n: list(g) for n, g in deps.items()}
        self.rdeps = defaultdict(set)
        for pkg_name, groups in self.deps.items():
            for group in groups:
                for dep in group.alternatives:
                    if dep.name in packages:
                        self.rdeps[dep.name].add(pkg_name)

    def direct_deps(self, name):
        """Flattened direct dependency names (all alternatives)."""
        seen = []
        for group in self.deps.get(name, []):
            for dep in group.alternatives:
                if dep.name not in seen:
                    seen.append(dep.name)
        return seen

    def direct_rdeps(self, name):
        return sorted(self.rdeps.get(name, set()))

    def _walk(self, start, neighbors):
        visited, order = {start}, []
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in neighbors(node):
                if nxt not in visited and nxt != start:
                    visited.add(nxt)
                    order.append(nxt)
                    stack.append(nxt)
        return sorted(order)

    def recursive_deps(self, name):
        return self._walk(name, lambda n: self.direct_deps(n))

    def recursive_rdeps(self, name):
        return self._walk(name, lambda n: self.direct_rdeps(n))

    def classify(self, name, explicit_set, essential_set):
        """Classify a package. Returns (classification, rdep_count)."""
        if name in essential_set:
            return "ESSENTIAL", len(self.rdeps.get(name, ()))
        rdep_count = len(self.rdeps.get(name, ()))
        if name in explicit_set:
            return "EXPLICIT", rdep_count
        if rdep_count == 0:
            return "ORPHAN", 0
        if rdep_count == 1:
            return "SINGLE-USE", rdep_count
        return "SHARED", rdep_count
