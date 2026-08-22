# Security

## Package Removal Safety

tpm **never** deletes package files directly. All removal is delegated to
the system package manager:

- **Debian/Ubuntu**: `apt remove -y <package>`
- **Arch Linux**: `pacman -R --noconfirm <package>`

The correct command is selected automatically based on the detected backend.

## Validation

Package names are validated via `validate_package_name()` before any
subprocess invocation. Invalid names are rejected immediately.

## Interactive Confirmation

Actual removal requires explicit confirmation:
- In interactive mode: the user must type `yes` after seeing the simulation plan
- `--simulate` / `--dry-run` shows what would happen without making changes
- `--yes` skips the confirmation prompt (for scripted use)

## No Direct File Deletion

Package files are never removed by hand — only the system package manager
handles file removal, ensuring correct dependency tracking and rollback.

## Root Detection

`warn_if_root()` alerts when running as root. tpm is designed for normal
user contexts; root usage is flagged as unexpected.

## Subprocess Controls

- All subprocess calls use `capture_output=True` and `timeout` (60–120s)
- Package names are validated before shell invocation
- No user input is interpolated into shell commands unsafely
