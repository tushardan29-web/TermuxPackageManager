"""Debian-style dependency expression parser.

Supports:
    foo
    foo (>= 1.2)
    foo | bar          -> DependencyGroup with alternatives
    foo:any
    comma-separated lists

A dependency field is a list of DependencyGroups; a group is satisfied if
at least one of its alternatives is installed.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Dependency:
    name: str
    version_constraint: str = None

    def to_dict(self):
        d = {"name": self.name}
        if self.version_constraint:
            d["version_constraint"] = self.version_constraint
        return d


@dataclass
class DependencyGroup:
    """Alternatives: satisfied when at least one member is installed."""
    alternatives: list = field(default_factory=list)

    def names(self):
        return [d.name for d in self.alternatives]

    def to_dict(self):
        return {"alternatives": [d.to_dict() for d in self.alternatives]}


_CONSTRAINT_RE = None


def _split_top_level(field_str):
    """Split on commas not inside parentheses."""
    parts, depth, current = [], 0, []
    for ch in field_str:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def parse_dependency_group(expr):
    """Parse 'foo (>= 1.2) | bar' into a DependencyGroup."""
    group = DependencyGroup()
    for alt in expr.split("|"):
        alt = alt.strip()
        if not alt:
            raise ValueError(f"empty alternative in: {expr!r}")
        name, constraint = alt, None
        if "(" in alt:
            idx = alt.index("(")
            name = alt[:idx].strip()
            end = alt.find(")")
            if end == -1 or alt[end + 1:].strip():
                raise ValueError(f"malformed version constraint: {alt!r}")
            constraint = alt[idx + 1:end].strip()
        name = name.split(":")[0].strip()  # strip arch qualifier like :any
        if not name:
            raise ValueError(f"missing package name: {expr!r}")
        group.alternatives.append(Dependency(name=name, version_constraint=constraint))
    return group


def parse_depends_field(field_str):
    """Parse a full Depends field into a list of DependencyGroups."""
    if not field_str or not field_str.strip():
        return []
    return [parse_dependency_group(e) for e in _split_top_level(field_str)]


def group_satisfied(group, installed_names):
    """True if at least one alternative is in the installed-name set."""
    return any(d.name in installed_names for d in group.alternatives)
