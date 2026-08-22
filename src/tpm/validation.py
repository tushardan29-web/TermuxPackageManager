"""Package-name validation and shell-safety utilities.

All package names passing through tpm are validated before being handed
to any subprocess. Subprocesses are always invoked with argument arrays,
never shell strings.
"""

import re

# Debian/Termux package names: lowercase alnum + . + - +
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+~-]*(:[a-zA-Z0-9_-]+)?$")

# Characters that must never appear in a package argument.
_FORBIDDEN = set(";&|<>$`\\'\"(){}[]*?!~#\n\r\t ")


def validate_package_name(name):
    """Return normalized name or raise ValueError."""
    if not name:
        raise ValueError("empty package name")
    if _FORBIDDEN & set(name):
        raise ValueError(f"package name contains forbidden characters: {name!r}")
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid package name: {name!r}")
    return name


def validate_package_names(names):
    return [validate_package_name(n) for n in names]
