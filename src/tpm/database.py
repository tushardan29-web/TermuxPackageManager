"""SQLite index — cached representation, never authoritative.

Source of truth is always dpkg + the filesystem. Deleting this file
is always safe; `tpm rescan` rebuilds it.
"""

import os
import sqlite3
import time

from .dependency.parser import parse_depends_field

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    version TEXT,
    architecture TEXT,
    status TEXT,
    installed_size INTEGER,
    explicitly_installed INTEGER DEFAULT 0,
    essential INTEGER DEFAULT 0,
    priority TEXT,
    source TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS dependencies (
    package_id INTEGER NOT NULL,
    dependency_name TEXT NOT NULL,
    dependency_type TEXT,
    version_constraint TEXT,
    alternative_index INTEGER DEFAULT 0,
    group_index INTEGER DEFAULT 0,
    PRIMARY KEY(package_id, dependency_name, group_index, alternative_index)
);
CREATE TABLE IF NOT EXISTS files (
    package_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    size INTEGER,
    PRIMARY KEY(package_id, path)
);
CREATE TABLE IF NOT EXISTS storage_paths (
    path TEXT PRIMARY KEY,
    size INTEGER,
    category TEXT,
    scanned_at INTEGER
);
CREATE TABLE IF NOT EXISTS scan_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_packages_name ON packages(name);
CREATE INDEX IF NOT EXISTS idx_deps_depname ON dependencies(dependency_name);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
"""


def default_db_path():
    xdg = os.environ.get("XDG_DATA_HOME") or \
        os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, "tpm", "tpm.db")


class Database:
    def __init__(self, path=None):
        self.path = path or default_db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---- metadata -------------------------------------------------
    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO scan_metadata(key, value) VALUES(?,?)",
            (key, str(value)))
        self.conn.commit()

    def get_meta(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM scan_metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ---- write: full rescan ---------------------------------------
    def replace_packages(self, records):
        """records: iterable of dicts with keys matching packages columns
        plus 'depends_groups' (list of DependencyGroup)."""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM dependencies")
        cur.execute("DELETE FROM packages")
        for rec in records:
            groups = rec.pop("depends_groups", [])
            cols = ", ".join(rec.keys())
            ph = ", ".join("?" for _ in rec)
            cur.execute(f"INSERT INTO packages({cols}) VALUES({ph})",
                        tuple(rec.values()))
            pid = cur.lastrowid
            for gi, group in enumerate(groups):
                for ai, dep in enumerate(group.alternatives):
                    cur.execute(
                        "INSERT OR IGNORE INTO dependencies(package_id, "
                        "dependency_name, dependency_type, "
                        "version_constraint, alternative_index, group_index) "
                        "VALUES(?,?,?,?,?,?)",
                        (pid, dep.name, "depends", dep.version_constraint,
                         ai, gi))
        self.conn.commit()

    def replace_files(self, package_name, paths):
        """paths: list of (path, size_or_None)."""
        cur = self.conn.cursor()
        row = cur.execute("SELECT id FROM packages WHERE name=?",
                          (package_name,)).fetchone()
        if not row:
            return 0
        pid = row["id"]
        cur.execute("DELETE FROM files WHERE package_id=?", (pid,))
        cur.executemany(
            "INSERT OR IGNORE INTO files(package_id, path, size) "
            "VALUES(?,?,?)", [(pid, p, s) for p, s in paths])
        self.conn.commit()
        return len(paths)

    def set_storage(self, entries):
        cur = self.conn.cursor()
        now = int(time.time())
        cur.executemany(
            "INSERT OR REPLACE INTO storage_paths(path, size, category, "
            "scanned_at) VALUES(?,?,?,?)",
            [(p, s, c, now) for p, s, c in entries])
        self.conn.commit()

    # ---- read ------------------------------------------------------
    def all_packages(self):
        rows = self.conn.execute(
            "SELECT * FROM packages ORDER BY name").fetchall()
        deps = {}
        for row in self.conn.execute(
                "SELECT d.package_id, d.dependency_name, d.version_constraint,"
                " d.group_index, d.alternative_index FROM dependencies d"):
            pass
        from collections import defaultdict
        by_pid = defaultdict(dict)
        for r in self.conn.execute(
                "SELECT * FROM dependencies ORDER BY group_index, "
                "alternative_index"):
            by_pid[r["package_id"]].setdefault(r["group_index"], []).append(r)
        out = []
        for row in rows:
            groups = []
            for gi in sorted(by_pid.get(row["id"], {})):
                members = by_pid[row["id"]][gi]
                groups.append({
                    "alternatives": [
                        {"name": m["dependency_name"],
                         "version_constraint": m["version_constraint"]}
                        for m in members]})
            out.append({"row": row, "depends_groups": groups})
        return out

    def get_package(self, name):
        for entry in self.all_packages():
            if entry["row"]["name"] == name:
                return entry
        return None

    def file_count(self, package_name):
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM files f JOIN packages p "
            "ON p.id=f.package_id WHERE p.name=?",
            (package_name,)).fetchone()
        return row["n"]

    def files_populated(self, package_name):
        """True if files table has entries for this package."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM files f JOIN packages p "
            "ON p.id=f.package_id WHERE p.name=?",
            (package_name,)).fetchone()
        return row["n"] > 0

    def populate_files(self, package_name, file_list):
        """file_list: [(path, size_or_None)] from backend."""
        cur = self.conn.cursor()
        row = cur.execute("SELECT id FROM packages WHERE name=?",
                          (package_name,)).fetchone()
        if not row:
            return 0
        pid = row["id"]
        cur.execute("DELETE FROM files WHERE package_id=?", (pid,))
        cur.executemany(
            "INSERT OR IGNORE INTO files(package_id, path, size) "
            "VALUES(?,?,?)", [(pid, p, s) for p, s in file_list])
        self.conn.commit()
        return len(file_list)

    def largest_files(self, package_name, limit=20):
        return self.conn.execute(
            "SELECT path, size FROM files f JOIN packages p "
            "ON p.id=f.package_id WHERE p.name=? "
            "ORDER BY COALESCE(size,0) DESC LIMIT ?",
            (package_name, limit)).fetchall()

    def storage_entries(self):
        return self.conn.execute(
            "SELECT * FROM storage_paths ORDER BY size DESC").fetchall()

    def is_stale(self, dpkg_mtime):
        stored = self.get_meta("dpkg_status_mtime")
        return stored is None or int(stored) != int(dpkg_mtime)

    def wipe(self):
        self.conn.close()
        if os.path.exists(self.path):
            os.remove(self.path)
        self.__init__(self.path)
