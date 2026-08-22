"""Core service layer — the single library both CLI and TUI use.

Builds PackageInfo objects + DependencyGraph from the Database,
with staleness detection against dpkg's status file mtime.
"""

import os
from dataclasses import dataclass, field

from .database import Database
from .dependency.graph import DependencyGraph
from .dependency.parser import DependencyGroup, Dependency, group_satisfied
from .package.backend import detect_backend


class PackageInfo(dict):
    """Package record supporting both mapping (``row["name"]``) and attribute
    (``row.name``) access, so the dependency/removal engines can use either
    style. Backed by the database row's columns."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


@dataclass
class SystemState:
    graph: DependencyGraph
    explicit: set
    essential: set
    backend_name: str = "apt/dpkg"

    def classification(self, name):
        return self.graph.classify(name, self.explicit, self.essential)

    def classify_label(self, name):
        cls, count = self.classification(name)
        if cls == "SHARED":
            return f"SHARED ×{count}"
        return cls


def load_state(db=None, backend=None):
    """Load full system state from the SQLite index."""
    db = db or Database()
    backend = backend or detect_backend()
    if backend is None:
        raise RuntimeError("no supported package manager found (dpkg/apt)")

    packages = {}
    deps = {}
    explicit = set()
    essential = set()

    for entry in db.all_packages():
        row = entry["row"]
        name = row["name"]
        groups = [
            DependencyGroup([
                Dependency(a["name"], a["version_constraint"])
                for a in g["alternatives"]])
            for g in entry["depends_groups"]]
        packages[name] = PackageInfo(dict(row))
        deps[name] = groups
        if row["explicitly_installed"]:
            explicit.add(name)
        if row["essential"]:
            essential.add(name)

    graph = DependencyGraph(packages, deps)
    return SystemState(graph=graph, explicit=explicit,
                       essential=essential, backend_name=backend.name)


def check_stale(backend):
    """Return dpkg status mtime for staleness comparison."""
    return backend.dpkg_status_mtime() if backend else None
