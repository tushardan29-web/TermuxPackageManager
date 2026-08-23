"""Filesystem scanner — streaming, depth-limited, symlink-safe.

Handles: permission errors, broken symlinks, symlink loops, races.
Never crashes because one entry is inaccessible.
"""

import os
from collections import deque

# Well-known cache locations relative to $HOME / $PREFIX.
CACHE_CANDIDATES = [
    ("$HOME/.cache/pip", "pip cache"),
    ("$HOME/.cache/npm", "npm cache"),
    ("$HOME/.npm", "npm cache (legacy)"),
    ("$HOME/.cargo/registry", "cargo registry"),
    ("$HOME/.gradle/caches", "gradle cache"),
    ("$HOME/.cache/uv", "uv cache"),
    ("$PREFIX/var/cache/apt", "APT cache"),
    ("$PREFIX/var/cache/apt/archives", "APT archive cache"),
    ("$PREFIX/var/cache/pacman/pkg", "pacman package cache"),
    ("/var/cache/apt/archives", "APT archive cache"),
    ("/var/cache/pacman/pkg", "pacman package cache"),
]


class ScanProgress:
    def __init__(self, callback=None):
        self.callback = callback
        self.dirs_seen = 0

    def tick(self):
        self.dirs_seen += 1
        if self.callback and self.dirs_seen % 200 == 0:
            self.callback(self.dirs_seen)


def scan_dir_size(path, max_depth=2, progress=None, _depth=0,
                  _visited=None):
    """Return {subdir_path: total_bytes} at each level up to max_depth.

    Symlinks are never followed (prevents loops and escaping $HOME).
    Returns sizes aggregated bottom-up.
    """
    if _visited is None:
        _visited = set()
    try:
        real = os.path.realpath(path)
    except OSError:
        return {}
    if real in _visited:
        return {}
    _visited.add(real)

    sizes = {}

    def measure_tree(root):
        """Total size of the tree under root, streaming."""
        total = 0
        stack = [root]
        while stack:
            d = stack.pop()
            if progress:
                progress.tick()
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(
                                    follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total

    def walk(dir_path, depth):
        try:
            entries = list(os.scandir(dir_path))
        except OSError:
            return 0
        total = 0
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    sub = walk(entry.path, depth + 1)
                    total += sub
                    if depth < max_depth:
                        sizes[entry.path] = sub
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            finally:
                if progress:
                    progress.tick()
        return total

    walk(os.path.realpath(path), 0)
    return sizes


def classify_path(path, home=None, prefix=None):
    home = home or os.path.expanduser("~")
    prefix = prefix or os.environ.get("PREFIX", "")
    p = os.path.abspath(path)
    if prefix and p.startswith(prefix + os.sep) or p == prefix:
        if "/var/cache" in p:
            return "package/cache data"
        return "$PREFIX"
    if p.startswith(home + os.sep) or p == home:
        if ".proot-distro" in p:
            return "proot-distro"
        if "/.cache" in p:
            return "caches"
        return "$HOME"
    return "other"


def detect_caches(home=None, prefix=None):
    """Return [(path, label, size_or_None)] for known caches that exist."""
    home = home or os.path.expanduser("~")
    prefix = prefix or os.environ.get("PREFIX", "")
    found = []
    for raw, label in CACHE_CANDIDATES:
        path = raw.replace("$HOME", home).replace("$PREFIX", prefix)
        if os.path.isdir(path):
            found.append((path, label))
    # proot distros
    proot_root = os.path.join(home, ".proot-distro", "installed")
    if os.path.isdir(proot_root):
        try:
            for name in os.listdir(proot_root):
                found.append((os.path.join(proot_root, name),
                              f"proot: {name}"))
        except OSError:
            pass
    results = []
    for path, label in found:
        tree = scan_dir_size(path, max_depth=1)
        size = sum(v for k, v in tree.items() if k != path)
        results.append({"path": path, "label": label,
                        "size": size})
    return results


def largest_dirs(path, limit=15, max_depth=3, min_size=1024 * 1024):
    """Top-N largest directories under path."""
    sizes = scan_dir_size(path, max_depth=max_depth)
    items = [(p, s) for p, s in sizes.items() if s >= min_size]
    items.sort(key=lambda x: -x[1])
    return items[:limit]
