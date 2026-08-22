"""Removal simulation — graph reachability, no shell calls.

Algorithm (spec §54):
  remaining = installed - requested
  Compute requirements of all remaining packages.
  Iteratively mark non-explicit, non-essential packages with zero
  remaining reverse-dependencies as removable (cascade).
  Detect unsatisfied dependency groups among remaining packages.
"""

from dataclasses import dataclass, field


@dataclass
class SimulationResult:
    requested: list = field(default_factory=list)
    cascade_removable: list = field(default_factory=list)   # deps becoming unnecessary
    retained_shared: list = field(default_factory=list)     # shared deps that stay
    broken: list = field(default_factory=list)              # packages w/ unsatisfied deps
    protected_essential: list = field(default_factory=list)
    package_recovery_bytes: int = 0
    total_recovery_bytes: int = 0
    blocked_reasons: list = field(default_factory=list)     # why requested can't be removed

    def to_dict(self):
        return {
            "requested": self.requested,
            "cascade_removable": self.cascade_removable,
            "retained_shared": self.retained_shared,
            "broken": self.broken,
            "protected_essential": self.protected_essential,
            "package_recovery_bytes": self.package_recovery_bytes,
            "total_recovery_bytes": self.total_recovery_bytes,
            "blocked_reasons": self.blocked_reasons,
        }


def simulate_removal(graph, requested_names, explicit_set=None, essential_set=None):
    """Simulate removing requested_names from the system described by graph.

    graph: DependencyGraph over currently-installed packages.
    Returns SimulationResult; never mutates the system.
    """
    explicit_set = explicit_set or set()
    essential_set = essential_set or set()
    result = SimulationResult()

    installed = set(graph.packages)
    for name in requested_names:
        if name not in installed:
            result.blocked_reasons.append(f"not installed: {name}")
        elif name in essential_set:
            result.protected_essential.append(name)
            result.blocked_reasons.append(
                f"{name} is ESSENTIAL and will not be removed")
    if result.blocked_reasons and not any(n in installed and n not in essential_set
                                          for n in requested_names):
        return result

    requested_ok = [n for n in requested_names
                    if n in installed and n not in essential_set]
    result.requested = sorted(requested_ok)
    remaining = installed - set(requested_ok)

    removable = []
    removed_so_far = set(requested_ok)
    changed = True
    while changed:
        changed = False
        active = remaining - set(removable)
        # requirements imposed by packages that remain installed
        reqs = set()
        for pkg in active:
            for group in graph.deps.get(pkg, []):
                satisfied_by = [d.name for d in group.alternatives
                                if d.name in active]
                reqs.update(satisfied_by)
        for cand in sorted(active):
            if cand in explicit_set or cand in essential_set:
                continue
            if cand not in reqs:
                removable.append(cand)
                removed_so_far.add(cand)
                changed = True

    result.cascade_removable = sorted(set(removable))

    # Broken-package detection: a remaining package with an unsatisfiable group.
    final_active = remaining - set(result.cascade_removable)
    for pkg in sorted(final_active):
        for group in graph.deps.get(pkg, []):
            if not any(d.name in final_active for d in group.alternatives):
                alt_str = "|".join(d.name for d in group.alternatives)
                entry = f"{pkg} needs {alt_str}"
                if entry not in result.broken:
                    result.broken.append(entry)

    # Shared dependencies retained: deps of requested that stay because
    # other remaining packages require them.
    sizes = {n: (p.installed_size or 0) for n, p in graph.packages.items()}
    retained = set()
    for name in requested_ok:
        for dep_name in graph.direct_deps(name):
            if dep_name in final_active and dep_name not in explicit_set \
                    and dep_name not in essential_set:
                retained.add(dep_name)
    result.retained_shared = sorted(retained)

    recovery_pkgs = requested_ok + result.cascade_removable
    result.package_recovery_bytes = sum(sizes.get(n, 0) for n in recovery_pkgs)
    result.total_recovery_bytes = result.package_recovery_bytes
    return result
