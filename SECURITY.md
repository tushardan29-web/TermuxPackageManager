# Security Model

## Core Principle

tpm **never** deletes package files directly. All destructive operations
are delegated to the system package manager via subprocess argument
arrays (never shell strings).

## Safety Guarantees

### 1. No direct file deletion

Package files are only removed by `apt remove` or `pacman -R`. tpm
never runs `rm` on package-owned files.

### 2. Simulation before action

Every removal shows a complete plan before execution. The simulation
uses graph reachability to determine:
- Which packages become removable (cascade)
- Which shared dependencies are retained
- Which packages would become broken
- Estimated storage recovery

### 3. Explicit confirmation

Actual removal requires typing `yes` after seeing the plan. The `--yes`
flag skips this for automation, but only after the simulation has been
displayed.

### 4. Staleness detection

Before destructive operations, tpm checks if the system package database
has changed since the last scan. If stale, it warns or re-scans
automatically.

### 5. Package name validation

All package names are validated by `validate_package_name()` before any
subprocess call. Names containing shell metacharacters, spaces, or
path traversal are rejected.

### 6. Subprocess controls

- All subprocess calls use `capture_output=True` and `timeout` (60-120s)
- Package names are passed as separate arguments, never interpolated
  into shell strings
- No `os.system()` or `shell=True` usage

### 7. Essential protection

Packages marked essential by the system are never removed, even if
simulation shows them as removable.

### 8. Root detection

`warn_if_root()` alerts when running as root. tpm is designed for
normal user contexts.

### 9. Database is not authoritative

The SQLite database is a cache. Deleting it is always safe --
`tpm rescan` rebuilds it from the real system. tpm never trusts stale
dependency information for destructive operations.

## What tpm does NOT do

- Does not modify the dpkg/pacman database directly
- Does not run `rm -rf` on any directory
- Does not assume a package is orphaned without checking reverse dependencies
- Does not remove shared dependencies merely because one dependent is removed
- Does not automatically remove packages without confirmation
- Does not request or require root access

## Subprocess Audit

All subprocess calls in the codebase:

| Location | Command | Purpose |
|----------|---------|---------|
| AptBackend.list_installed | `dpkg-query -W -f=...` | Read package metadata |
| AptBackend.file_list | `dpkg-query -L <pkg>` | List package files |
| AptBackend.explicit_names | `apt-mark showmanual` | Get explicit packages |
| AptBackend.broken_packages | `dpkg --audit` | Check for broken packages |
| PacmanBackend.file_list | `pacman -Ql <pkg>` | List package files |
| PacmanBackend.broken_packages | `pacman -Dk` | Check dependencies |
| cmd_remove | `apt remove -y <pkg>` | Remove package (apt) |
| cmd_remove | `pacman -R --noconfirm <pkg>` | Remove package (pacman) |
| cleanup.clean_apt_cache | `apt clean` | Clean APT cache |
| cleanup.clean_pacman_cache | `pacman -Sc --noconfirm` | Clean pacman cache |
| cleanup.clean_orphans | `apt autoremove -y` | Remove orphan packages |
| cleanup.clean_orphans | `pacman -Rns --noconfirm <pkg>` | Remove orphan packages |

All commands use argument arrays, never shell strings.
