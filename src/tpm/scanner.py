"""Scan orchestration: dpkg metadata + apt-mark -> SQLite index."""

import os
import time

from .database import Database
from .package.backend import detect_backend
from .validation import validate_package_names


def run_scan(db=None, progress=None):
    """Full rescan of package metadata. Returns count of packages indexed."""
    db = db or Database()
    backend = detect_backend()
    if backend is None:
        raise RuntimeError("no supported package backend found")
    explicit = backend.explicit_names()
    records = list(backend.list_installed())
    names = {r["name"] for r in records}
    # apt-mark may contain stale names; only mark what's actually installed.
    for rec in records:
        rec["explicitly_installed"] = 1 if rec["name"] in explicit else 0
    validate_package_names(names)
    db.replace_packages(records)
    mtime = backend.dpkg_status_mtime()
    if mtime:
        db.set_meta("dpkg_status_mtime", int(mtime))
    db.set_meta("last_scan", int(time.time()))
    db.set_meta("backend", backend.name)
    db.set_meta("package_count", len(records))
    return len(records)


def is_stale(db=None, backend=None):
    """Cheap staleness check — compare dpkg status mtime."""
    db = db or Database()
    backend = backend or detect_backend()
    if backend is None:
        return False, None
    current = backend.dpkg_status_mtime()
    stored = db.get_meta("dpkg_status_mtime")
    if stored is None or current is None:
        return True, current
    return int(stored) != int(current), current


def ensure_fresh(db=None, force=False):
    """Auto-rescan packages if the system changed (spec §37)."""
    db = db or Database()
    stale, _ = is_stale(db)
    if stale or force:
        run_scan(db=db)
        return True
    return False
