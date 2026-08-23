"""Cleanup backends — actual cache/package cleanup operations.

All operations require explicit confirmation. Nothing is deleted
without the user choosing it interactively or passing --yes.
"""

import os
import shutil
import subprocess


class CleanupResult:
    def __init__(self):
        self.cleaned_bytes = 0
        self.items_cleaned = 0
        self.errors = []

    def to_dict(self):
        return {
            "cleaned_bytes": self.cleaned_bytes,
            "items_cleaned": self.items_cleaned,
            "errors": self.errors,
        }


def _run(cmd, timeout=120):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def clean_apt_cache(prefix=None, dry_run=False):
    """Remove downloaded .deb files via apt clean."""
    prefix = prefix or os.environ.get("PREFIX", "")
    result = CleanupResult()
    # apt clean removes everything in /var/cache/apt/archives/
    archive_dir = os.path.join(prefix, "var", "cache", "apt", "archives")
    if not os.path.isdir(archive_dir):
        return result
    # Calculate size first
    for entry in os.scandir(archive_dir):
        try:
            if entry.is_file(follow_symlinks=False) and entry.name.endswith(".deb"):
                size = entry.stat(follow_symlinks=False).st_size
                result.cleaned_bytes += size
                result.items_cleaned += 1
                if not dry_run:
                    os.remove(entry.path)
        except OSError as e:
            result.errors.append(f"{entry.path}: {e}")
    # Also run apt clean to handle any internal state
    if not dry_run:
        _run(["apt", "clean"])
    return result


def clean_pacman_cache(prefix=None, dry_run=False):
    """Remove old pacman packages via pacman -Sc."""
    prefix = prefix or os.environ.get("PREFIX", "")
    cache_dir = os.path.join(prefix, "var", "cache", "pacman", "pkg")
    result = CleanupResult()
    if not os.path.isdir(cache_dir):
        return result
    for entry in os.scandir(cache_dir):
        try:
            if entry.is_file(follow_symlinks=False):
                size = entry.stat(follow_symlinks=False).st_size
                result.cleaned_bytes += size
                result.items_cleaned += 1
                if not dry_run:
                    os.remove(entry.path)
        except OSError as e:
            result.errors.append(f"{entry.path}: {e}")
    if not dry_run:
        _run(["pacman", "-Sc", "--noconfirm"])
    return result


def clean_pip_cache(home=None, dry_run=False):
    """Remove pip download cache."""
    home = home or os.path.expanduser("~")
    cache_dir = os.path.join(home, ".cache", "pip")
    return _clean_tree(cache_dir, dry_run, label="pip")


def clean_npm_cache(home=None, dry_run=False):
    """Remove npm cache (both legacy and current locations)."""
    home = home or os.path.expanduser("~")
    result = CleanupResult()
    for path in [os.path.join(home, ".npm"),
                 os.path.join(home, ".cache", "npm")]:
        sub = _clean_tree(path, dry_run, label="npm")
        result.cleaned_bytes += sub.cleaned_bytes
        result.items_cleaned += sub.items_cleaned
        result.errors.extend(sub.errors)
    return result


def clean_cargo_cache(home=None, dry_run=False):
    """Remove cargo registry cache."""
    home = home or os.path.expanduser("~")
    result = CleanupResult()
    for sub in ["registry/cache", "registry/src"]:
        path = os.path.join(home, ".cargo", sub)
        sub_result = _clean_tree(path, dry_run, label="cargo")
        result.cleaned_bytes += sub_result.cleaned_bytes
        result.items_cleaned += sub_result.items_cleaned
        result.errors.extend(sub_result.errors)
    return result


def clean_gradle_cache(home=None, dry_run=False):
    """Remove gradle caches."""
    home = home or os.path.expanduser("~")
    path = os.path.join(home, ".gradle", "caches")
    return _clean_tree(path, dry_run, label="gradle")


def clean_uv_cache(home=None, dry_run=False):
    """Remove uv cache (wheel, http, git fallback)."""
    home = home or os.path.expanduser("~")
    result = CleanupResult()
    cache_dir = os.path.join(home, ".cache", "uv")
    if not os.path.isdir(cache_dir):
        return result
    # uv cache clean removes everything; we calculate size first
    for entry in os.scandir(cache_dir):
        try:
            if entry.is_dir(follow_symlinks=False):
                sub = _clean_tree(entry.path, dry_run, label="uv")
                result.cleaned_bytes += sub.cleaned_bytes
                result.items_cleaned += sub.items_cleaned
                result.errors.extend(sub.errors)
            elif entry.is_file(follow_symlinks=False):
                size = entry.stat(follow_symlinks=False).st_size
                result.cleaned_bytes += size
                result.items_cleaned += 1
                if not dry_run:
                    os.remove(entry.path)
        except OSError as e:
            result.errors.append(f"{entry.path}: {e}")
    if not dry_run:
        _run(["uv", "cache", "clean"])
    return result


def clean_orphans(db, dry_run=False):
    """Run apt autoremove / pacman -Rns for orphan packages."""
    from .core import load_state
    from .scanner import ensure_fresh
    state = load_state(db)
    ensure_fresh(db)
    orphans = [n for n, row in state.graph.packages.items()
               if state.classification(n)[0] == "ORPHAN"]
    if not orphans:
        return CleanupResult()
    result = CleanupResult()
    result.items_cleaned = len(orphans)
    # Calculate approximate size
    for name in orphans:
        row = state.graph.packages.get(name, {})
        result.cleaned_bytes += row.get("installed_size") or 0
    if not dry_run:
        backend_name = state.backend_name
        if "pacman" in backend_name:
            for name in orphans:
                _run(["pacman", "-Rns", "--noconfirm", name])
        else:
            _run(["apt", "autoremove", "-y"])
    return result


def _clean_tree(path, dry_run=False, label=""):
    """Delete files under path, keeping the directory itself."""
    result = CleanupResult()
    if not os.path.isdir(path):
        return result
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    sub = _clean_tree(entry.path, dry_run, label)
                    result.cleaned_bytes += sub.cleaned_bytes
                    result.items_cleaned += sub.items_cleaned
                    result.errors.extend(sub.errors)
                    # Remove empty directory after cleaning contents
                    if not dry_run:
                        try:
                            os.rmdir(entry.path)
                        except OSError:
                            pass
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    result.cleaned_bytes += size
                    result.items_cleaned += 1
                    if not dry_run:
                        os.remove(entry.path)
            except OSError as e:
                result.errors.append(f"{entry.path}: {e}")
    except OSError as e:
        result.errors.append(f"{path}: {e}")
    return result
