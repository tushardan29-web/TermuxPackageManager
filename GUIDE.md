# tpm User Guide

Complete reference for every command, flag, and workflow.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Package Scanning](#package-scanning)
3. [Listing Packages](#listing-packages)
4. [Package Information](#package-information)
5. [Dependency Analysis](#dependency-analysis)
6. [Removal Simulation](#removal-simulation)
7. [Safe Package Removal](#safe-package-removal)
8. [Storage Analysis](#storage-analysis)
9. [Largest Directories](#largest-directories)
10. [Cache Detection and Cleanup](#cache-detection-and-cleanup)
11. [File Listing](#file-listing)
12. [Health Diagnostics](#health-diagnostics)
13. [Interactive TUI](#interactive-tui)
14. [JSON Output](#json-output)
15. [Configuration](#configuration)
16. [Dependency Classification](#dependency-classification)
17. [Troubleshooting](#troubleshooting)

---

## Getting Started

After installation, run `tpm scan` to index your installed packages:

```sh
$ tpm scan
SCAN COMPLETE
333 packages indexed
```

This reads the real package database (dpkg or pacman) and builds a local
SQLite index at `$XDG_DATA_HOME/tpm/tpm.db`. The index is a cache -- if
deleted, `tpm rescan` rebuilds it from the system.

### First look

```sh
$ tpm list | head -5
PACKAGES
NAME                     SIZE        TYPE
rust                     547.1 MiB   EXPLICIT
openjdk-21               243.7 MiB   SHARED x3
openjdk-17               212.9 MiB   EXPLICIT
mariadb                  191.1 MiB   EXPLICIT
clang                    182.4 MiB   EXPLICIT
```

---

## Package Scanning

### `tpm scan`

Reads the system package database and builds the SQLite index. Safe to
run repeatedly -- replaces the previous index entirely.

```sh
$ tpm scan
SCAN COMPLETE
333 packages indexed
```

### `tpm rescan`

Identical to `tpm scan`. Provided for clarity.

### Automatic staleness detection

Before destructive operations (remove, clean), tpm checks whether the
system package database has changed since the last scan. If stale, it
warns:

```
warning: system package database changed since last scan (run `tpm rescan`)
```

---

## Listing Packages

### `tpm list`

Lists all installed packages sorted by size (largest first).

```sh
$ tpm list
PACKAGES
NAME                     SIZE        TYPE
rust                     547.1 MiB   EXPLICIT
openjdk-21               243.7 MiB   SHARED x3
...
```

### Filtering flags

```sh
tpm list --explicit       # Only explicitly installed packages
tpm list --dependencies   # Only dependency packages (shared + single-use)
tpm list --shared         # Only shared dependencies (used by 2+ packages)
tpm list --orphans        # Only orphan packages (nothing depends on them)
tpm list --largest        # Sort by size, largest first (default behavior)
```

### Examples

```sh
# What did I explicitly install?
$ tpm list --explicit | head
NAME                     SIZE        TYPE
rust                     547.1 MiB   EXPLICIT
python                   25.6 MiB    EXPLICIT
git                      8.9 MiB     EXPLICIT

# What packages are orphans?
$ tpm list --orphans
NAME                     SIZE        TYPE
cmake                    54.5 MiB    ORPHAN
npm                      17.8 MiB    ORPHAN
```

---

## Package Information

### `tpm info <package>`

Shows detailed information about a package: version, architecture, size,
classification, dependencies, reverse dependencies, and file count.

```sh
$ tpm info python
PYTHON
Version:             3.14.6-1
Architecture:        aarch64
Installed size:      25.6 MiB
Type:                EXPLICIT
Files indexed:       1043

Dependencies (15):
  gdbm                           SINGLE-USE
  libandroid-posix-semaphore     SHARED x5
  libandroid-support             ESSENTIAL
  libcrypt                       SHARED x2
  libffi                         EXPLICIT
  liblzma                        ESSENTIAL
  libsqlite                      SHARED x3
  ncurses                        EXPLICIT
  openssl                        EXPLICIT
  readline                       EXPLICIT
  zlib                           EXPLICIT
  zstd                           EXPLICIT

Reverse dependencies (10):
  python-pip
  python-cryptography
  python-numpy
  python-pandas
  python-pillow
  python-bcrypt
  ...
```

The file count is lazy-populated: the first time you run `tpm info` or
`tpm files` for a package, tpm queries the backend and caches the result.

---

## Dependency Analysis

### `tpm deps <package>`

Shows the direct dependencies of a package, with their classification.

```sh
$ tpm deps python
DEPENDENCIES: python
  gdbm                             [SINGLE-USE]
  libandroid-posix-semaphore       [SHARED x5]
  libandroid-support               [ESSENTIAL]
  libcrypt                         [SHARED x2]
  libffi                           [EXPLICIT]
  liblzma                          [ESSENTIAL]
  libsqlite                        [SHARED x3]
  ncurses                          [EXPLICIT]
  openssl                          [EXPLICIT]
  readline                         [EXPLICIT]
  zlib                             [EXPLICIT]
  zstd                             [EXPLICIT]
```

### `tpm deps <package> --recursive`

Shows all transitive dependencies (dependencies of dependencies, etc.).

```sh
$ tpm deps python --recursive
RECURSIVE DEPENDENCIES: python
  ca-certificates                  [EXPLICIT]
  gdbm                             [SINGLE-USE]
  libandroid-posix-semaphore       [SHARED x5]
  libandroid-support               [ESSENTIAL]
  ...
```

### `tpm rdeps <package>`

Shows what other installed packages depend on this package (reverse
dependencies). This is how you know if a package is shared.

```sh
$ tpm rdeps openssl
REQUIRED BY: openssl
  coreutils
  ffmpeg
  git
  krb5
  ldns
  libarchive
  libcrypt
  libcurl
  libngtcp2
  libsrt

count: 10
```

### `tpm rdeps <package> --recursive`

Shows all packages that transitively depend on this package.

```sh
$ tpm rdeps openssl --recursive
REQUIRED BY (RECURSIVE): openssl
  python-pip
  python-cryptography
  python-numpy
  ...

count: 58
```

---

## Removal Simulation

### `tpm remove --simulate <package>`

**This is the most important safety feature.** It shows exactly what
would happen if you removed a package, without making any changes.

```sh
$ tpm remove --simulate python
REMOVAL SIMULATION

Requested:
  python

Would become removable:
  autoconf
  automake
  bash-completion-glibc
  bc
  bison
  ca-certificates-java
  cmake
  flex
  gdbm
  gperf
  jsoncpp
  libandroid-utimes
  libtool
  libuv
  libxi
  libxtst
  lz4
  m4
  npm
  openjdk-17-x
  openjdk-21-x
  python-ensurepip-wheels

Shared dependencies retained:
  libandroid-support
  libbz2
  libcrypt
  libexpat
  libffi
  liblzma
  libssh2
  libsqlite
  ncurses
  openssl
  readline
  zlib
  zstd

Estimated package storage recovery: 216.51 MiB

No changes were made.
```

### What the simulation means

- **Requested**: the package you asked to remove
- **Would become removable**: dependencies that would become orphaned
  (no other installed package needs them)
- **Shared dependencies retained**: packages that stay because other
  packages still need them
- **Estimated recovery**: sum of sizes of requested + cascade-removable
  packages

### `tpm remove --dry-run <package>`

Alias for `--simulate`.

---

## Safe Package Removal

### `tpm remove <package>`

Shows the full removal plan, then asks for confirmation before executing.

```sh
$ tpm remove npm
REMOVAL SIMULATION

Requested:
  npm

Would become removable:
  (none)

...

Proceed with actual apt remove? Type 'yes': yes
```

### Safety guarantees

1. The simulation is always shown first
2. You must type `yes` to confirm (not y, not Y -- exactly "yes")
3. The system package database is re-scanned immediately before execution
4. Essential packages are never removed
5. The actual removal is delegated to `apt remove` or `pacman -R`

### Scripted removal

```sh
# Skip the confirmation prompt (for automation)
tpm remove --yes <package>
```

---

## Storage Analysis

### `tpm storage`

Shows a breakdown of storage usage in your home directory.

```sh
$ tpm storage
TERMUX STORAGE
20.46 GiB under /data/data/com.termux/files/home (depth 2)

  5.5 GiB   ~/.cache  [caches]
  5.2 GiB   ~/tmp  [$HOME]
  2.0 GiB   ~/.cargo  [$HOME]
  1.8 GiB   ~/.local  [$HOME]
  1.6 GiB   ~/projects  [$HOME]
  1.3 GiB   ~/.gradle  [$HOME]
  1.0 GiB   ~/.npm  [$HOME]

Cache categories:
  260.25 MiB   pip cache
  1.04 GiB     npm cache (legacy)
  1.97 GiB     cargo registry
  1.19 GiB     gradle cache
```

### `tpm storage <path>`

Analyze a specific directory.

```sh
tpm storage ~/projects
tpm storage $PREFIX
```

### `tpm storage --depth N`

Control how many directory levels to traverse.

```sh
tpm storage --depth 1    # Top-level only
tpm storage --depth 3    # Three levels deep (slower)
```

### Storage categories

| Category | Meaning |
|----------|---------|
| `$HOME` | User files under home directory |
| `$PREFIX` | Package manager files |
| `caches` | Detected cache directories |
| `proot-distro` | proot-distro root filesystems |
| `other` | Everything else |

---

## Largest Directories

### `tpm largest`

Shows the largest directories under $HOME.

```sh
$ tpm largest
LARGEST DIRECTORIES
   5.5 GiB  ~/.cache
   5.2 GiB  ~/tmp
   2.8 GiB  ~/.cache/fresh-termux
   2.4 GiB  ~/.cache/opencode
   2.0 GiB  ~/.cargo
```

### Options

```sh
tpm largest --path ~/projects    # Analyze specific directory
tpm largest --depth 4            # Deeper traversal
```

---

## Cache Detection and Cleanup

### `tpm cache`

Detects and measures all known caches without deleting anything.

```sh
$ tpm cache
CACHE STORAGE
  260.25 MiB   pip cache
  1.04 GiB     npm cache (legacy)
  1.97 GiB     cargo registry
  1.19 GiB     gradle cache
       0 B     pacman package cache

Potential cleanup: 4.45 GiB
```

### Detected cache locations

| Cache | Location |
|-------|----------|
| pip | `~/.cache/pip` |
| npm | `~/.npm`, `~/.cache/npm` |
| cargo | `~/.cargo/registry` |
| gradle | `~/.gradle/caches` |
| APT | `$PREFIX/var/cache/apt` |
| pacman | `$PREFIX/var/cache/pacman/pkg` |

### `tpm clean`

Interactive cleanup review. Shows all detected caches and orphan
packages, lets you choose what to clean.

```sh
$ tpm clean
CLEANUP
[1] pip cache                      260.25 MiB
[2] npm cache (legacy)               1.04 GiB
[3] cargo registry                   1.97 GiB
[4] gradle cache                     1.19 GiB
[5] 13 orphan packages             115.2 MiB

Total potential recovery: 4.57 GiB

Enter numbers to clean (comma-separated), 'a' for all, 'q' to quit:
> 1,2

Clean 'pip cache' (260.25 MiB)? [y/N] > y
Cleaning pip cache...
  Cleaned: 260.25 MiB

Clean 'npm cache (legacy)' (1.04 GiB)? [y/N] > y
Cleaning npm cache (legacy)...
  Cleaned: 1.04 GiB

Total cleaned: 1.30 GiB
```

### What gets cleaned

| Category | Method |
|----------|--------|
| APT cache | `apt clean` + file removal |
| pacman cache | `pacman -Sc` |
| pip cache | Deletes `~/.cache/pip` contents |
| npm cache | Deletes `~/.npm` and `~/.cache/npm` contents |
| cargo cache | Deletes `~/.cargo/registry/cache` and `registry/src` |
| gradle cache | Deletes `~/.gradle/caches` contents |
| Orphan packages | `apt autoremove` or `pacman -Rns` per orphan |

### Safety

- Every category requires individual confirmation
- Nothing is deleted without you choosing it
- Non-interactive mode: nothing is deleted
- `--yes` flag: skips confirmation (for automation)

---

## File Listing

### `tpm files <package>`

Lists all files owned by a package.

```sh
$ tpm files python | head
  5.6 MiB  /data/data/com.termux/files/usr/lib/libpython3.14.so
727.1 KiB  /data/data/com.termux/files/usr/lib/python3.14/lib-dynload/unicodedata.cpython-314-aarch64-linux-android.so
569.2 KiB  /data/data/com.termux/files/usr/lib/python3.14/pydoc_data/topics.py
...
```

### `tpm files <package> --largest`

Shows only the largest files (top 25).

```sh
$ tpm files python --largest
FILES: python (largest)
  5.6 MiB  /data/data/com.termux/files/usr/lib/libpython3.14.so
727.1 KiB  .../unicodedata.cpython-314-aarch64-linux-android.so
569.2 KiB  .../pydoc_data/topics.py
...
```

### Lazy population

File lists are not pre-scanned for all packages (too slow). On first
access, tpm queries `dpkg-query -L` or `pacman -Ql` and caches the
result in the SQLite database.

---

## Health Diagnostics

### `tpm doctor`

Checks your environment and reports issues.

```sh
$ tpm doctor
TPM DOCTOR
Termux environment detected
running unprivileged
active package manager: apt
package database detected (apt/dpkg)
dpkg status database readable
dpkg audit clean
333 packages indexed
dependency graph valid
13 orphan packages
index fresh
```

### `tpm doctor --dependencies`

Checks dependency consistency: unsatisfied dependency groups, packages
claiming uninstalled dependencies, etc.

---

## Interactive TUI

### `tpm tui`

Launches the interactive terminal UI.

```
+----------------------------------------------------------+
| Dashboard                                                |
+----------------------------------------------------------+
|                                                          |
| packages                       333                       |
| installed size             3.2 GiB                       |
| caches (detectable)        4.5 GiB                       |
| explicit                       116                       |
| essential                       45                       |
| shared deps                     70                       |
| single-use                     119                       |
| orphans             13 (115.2 MiB)                       |
| potential cleanup          4.6 GiB                       |
| backend                   apt/dpkg                       |
|                                                          |
+----------------------------------------------------------+
| p=Packages d=Shared-deps s=Storage o=Orphans c=Cleanup   |
| /=Search q=Quit                                         |
+----------------------------------------------------------+
```

### Navigation keys

| Key | Action |
|-----|--------|
| `p` | Package list |
| `d` | Shared dependencies list |
| `s` | Storage breakdown |
| `o` | Orphans list |
| `c` | Cleanup review |
| `/` | Search |
| `q` | Back / Quit |

### Package list keys

| Key | Action |
|-----|--------|
| Arrow keys | Navigate |
| `i` or Enter | Package info |
| `e` | Filter: explicit only |
| `s` | Filter: shared only |
| `o` | Filter: orphans only |
| `x` | Filter: all |
| `/` | Search |
| `q` | Back |

### Package info keys

| Key | Action |
|-----|--------|
| `r` | Toggle removal simulation |
| `d` | Dependency tree |
| `R` | Reverse dependency tree |
| `q` | Back |

---

## JSON Output

Every command supports `--json` for scripting and automation.

```sh
# List all packages as JSON
tpm list --json

# Get specific package info
tpm info openssl --json

# Simulate removal
tpm remove --simulate python --json

# Get cache sizes
tpm cache --json

# Orphan list
tpm orphans --json

# Storage breakdown
tpm storage --json

# Health check
tpm doctor --json
```

JSON goes to stdout only. Errors and warnings go to stderr.

### Example JSON output

```json
{
  "name": "python",
  "version": "3.14.6-1",
  "architecture": "aarch64",
  "installed_size_bytes": 26214400,
  "type": "EXPLICIT",
  "essential": false,
  "description": "Python interpreter",
  "dependencies": [
    {"name": "gdbm", "type": "SINGLE-USE"},
    {"name": "libffi", "type": "EXPLICIT"},
    {"name": "openssl", "type": "EXPLICIT"}
  ],
  "reverse_dependencies": ["python-pip", "python-cryptography"],
  "file_count_indexed": 1043
}
```

---

## Configuration

tpm reads configuration from `$XDG_CONFIG_HOME/tpm/config.toml`
(defaults to `~/.local/config/tpm/config.toml`).

```toml
scan_depth = 2
follow_symlinks = false
show_hidden = true
color = true
auto_rescan_packages = true
```

| Setting | Default | Meaning |
|---------|---------|---------|
| `scan_depth` | 2 | Directory traversal depth for storage |
| `follow_symlinks` | false | Follow symlinks in storage scan |
| `show_hidden` | true | Include hidden directories |
| `color` | true | Use colored output |
| `auto_rescan_packages` | true | Auto-rescan before destructive ops |

---

## Dependency Classification

Every package is classified into one of these categories:

| Classification | Meaning |
|----------------|---------|
| **EXPLICIT** | You installed this package directly |
| **SHARED** | Not explicit, required by 2+ packages |
| **SINGLE-USE** | Not explicit, required by exactly 1 package |
| **ORPHAN** | Not explicit, required by 0 packages |
| **ESSENTIAL** | Marked essential by the package system |

### How classification works

1. **ESSENTIAL** takes highest priority -- these are protected
2. **EXPLICIT** comes from `apt-mark showmanual` (apt) or `%REASON%=0` (pacman)
3. **ORPHAN** = not explicit AND no reverse dependencies
4. **SINGLE-USE** = not explicit AND exactly 1 reverse dependency
5. **SHARED** = not explicit AND 2+ reverse dependencies

### Why classification matters

- EXPLICIT packages are never auto-removed
- ESSENTIAL packages are protected from removal
- SHARED dependencies stay even if one dependent is removed
- ORPHAN packages are candidates for cleanup
- SINGLE-USE dependencies become orphaned when their sole dependent is removed

---

## Troubleshooting

### "no supported package manager found"

tpm needs `dpkg-query` (Debian/Termux) or a pacman local database
(Arch). Ensure the package manager is installed.

### "system package database changed since last scan"

Run `tpm rescan` to refresh the index.

### "package not indexed"

The package may have been installed after the last scan. Run
`tpm rescan`.

### TUI not working

The TUI requires a real terminal (tty). It will not work in pipes
or non-interactive sessions. Use the CLI commands instead.

### Slow storage scan

Large directories take time. Use `--depth 1` for a quick overview,
or specify a path: `tpm storage ~/projects --depth 2`.

### Colors not showing

tpm auto-disables colors when stdout is not a TTY. Use `--no-color`
to force plain output, or ensure you are running in a terminal.

### JSON output is empty

The `--json` flag must come after the subcommand:

```sh
# Correct
tpm list --json

# Wrong (argparse may not see it)
tpm --json list
```
