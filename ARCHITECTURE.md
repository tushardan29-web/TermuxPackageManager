# Architecture

## Overview

tpm is a presentation-layer CLI/TUI over a shared core library. The CLI
(`src/tpm/cli.py`) and TUI (`src/tpm/tui.py`) both use the same backend
modules. No logic is duplicated.

```
                 REAL SYSTEM
                     |
          +----------+----------+
          |                     |
        dpkg/pacman          filesystem
          |                     |
          +----------+----------+
                     |
               tpm scanner
                     |
                 SQLite index
                     |
              dependency engine
                     |
              +------+------+
              |             |
            CLI           TUI
```

## Module Map

| Module | Purpose |
|--------|---------|
| `cli.py` | Argparse entry point, 18 commands, JSON output |
| `tui.py` | Interactive TUI (rich-based), 8 screen types |
| `core.py` | SystemState loading, freshness checks |
| `scanner.py` | Scan orchestration, staleness detection |
| `database.py` | SQLite index with schema, indexes, read/write |
| `config.py` | TOML-like config reader |
| `cleanup.py` | Cache cleanup backends (APT/pip/npm/cargo/gradle/pacman) |
| `environment.py` | Detects Termux vs Linux, active package manager |
| `formatter.py` | Centralized binary size formatting (KiB/MiB/GiB) |
| `validation.py` | Package name validation (shell-safe) |
| `dependency/parser.py` | Debian dependency expression parser |
| `dependency/graph.py` | Adjacency-based dependency graph |
| `removal/simulator.py` | Graph reachability removal simulation |
| `package/backend.py` | Backend abstraction + AptBackend + PacmanBackend |
| `storage/scanner.py` | Filesystem scanner, cache detection |

## Backend Abstraction

`PackageManagerBackend` is the interface. Two implementations:

### AptBackend (dpkg/apt)

- `dpkg-query -W -f=...` with STX separator (multiline-safe)
- `apt-mark showmanual` for explicitly installed packages
- `dpkg-query -L` for file lists
- `dpkg --audit` for broken package detection
- Status file: `$PREFIX/var/lib/dpkg/status`

### PacmanBackend

- Reads `desc` files from `$PREFIX/var/lib/pacman/local/*/desc`
- No subprocess for metadata -- just file parsing
- `pacman -Ql` for file lists
- `%REASON%` field: 0 = explicit, 1 = dependency
- No essential concept (always returns empty set)

### Backend Selection: `detect_backend()`

1. Checks `TERMUX_APP_PACKAGE_MANAGER` env var
2. Falls back to checking dpkg DB presence
3. Checks for pacman local DB with content
4. Prefers the active manager when both databases exist

Both backends produce identical record dicts:

```python
{
    "name": "python",
    "version": "3.14.6-1",
    "architecture": "aarch64",
    "status": "ii",
    "installed_size": 26214400,
    "explicitly_installed": 1,
    "essential": 0,
    "priority": None,
    "source": "dpkg",
    "description": "Python interpreter",
    "depends_groups": [DependencyGroup(...)],
}
```

## Environment Detection

`detect_environment()` returns:

```python
{
    "termux": True,
    "prefix": "/data/data/com.termux/files/usr",
    "platform": "Android",
    "machine": "aarch64",
    "os_name": None,
    "manager": "apt",
}
```

## Dependency Graph

Built in-memory from the SQLite index:

- **Forward edges**: A depends on B (from Depends field)
- **Reverse edges**: B is required by A (computed at load time)
- **Alternatives**: `foo | bar` modeled as DependencyGroup
- **Classification**: ESSENTIAL > EXPLICIT > SHARED(>1) > SINGLE-USE(=1) > ORPHAN(=0)

## Removal Simulation Algorithm

Given installed set P and requested removal set R:

1. remaining = P - R
2. Compute requirements of all remaining packages
3. Iteratively find non-explicit, non-essential packages with zero
   remaining reverse-dependencies -- mark as removable (cascade)
4. Repeat until no additional packages become removable
5. Detect unsatisfied dependency groups among remaining packages
6. Calculate estimated recovery = sum of sizes of R + cascade-removable

This is a graph reachability problem, not ad-hoc recursion.

## SQLite Schema

```sql
CREATE TABLE packages (
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

CREATE TABLE dependencies (
    package_id INTEGER NOT NULL,
    dependency_name TEXT NOT NULL,
    dependency_type TEXT,
    version_constraint TEXT,
    alternative_index INTEGER DEFAULT 0,
    group_index INTEGER DEFAULT 0,
    PRIMARY KEY(package_id, dependency_name, group_index, alternative_index)
);

CREATE TABLE files (
    package_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    size INTEGER,
    PRIMARY KEY(package_id, path)
);

CREATE TABLE storage_paths (
    path TEXT PRIMARY KEY,
    size INTEGER,
    category TEXT,
    scanned_at INTEGER
);

CREATE TABLE scan_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

Indexes: package name, dependency name, file paths.

## Data Flow

```
scan:
  backend.list_installed() -> records
  db.replace_packages(records)
  db.set_meta("dpkg_status_mtime", mtime)

analyze:
  db.all_packages() -> entries
  build DependencyGraph(packages, deps)
  classify each package -> EXPLICIT/SHARED/SINGLE-USE/ORPHAN/ESSENTIAL

simulate removal:
  remaining = installed - requested
  iterate: find non-explicit, non-essential, zero-rdep packages -> cascade
  detect broken: remaining pkg with unsatisfied dependency group
  recovery = sum(sizes of requested + cascade)

cleanup:
  detect_caches() -> [{path, label, size}]
  user selects categories
  per-category: clean_apt_cache, clean_pip_cache, etc.
  report cleaned bytes
```
