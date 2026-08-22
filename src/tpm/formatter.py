"""Centralized human-readable size formatting (binary units: KiB/MiB/GiB)."""

UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]


def format_size(nbytes, precision=1):
    """Format bytes as human-readable binary size. One formatter for the whole app."""
    if nbytes is None:
        return "?"
    size = float(nbytes)
    if size < 0:
        return "-" + format_size(-size)
    if size < 1024:
        return f"{int(size)} B"
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            break
        size /= 1024
    return f"{size:.{precision}f} {unit}"


def parse_installed_size(kib):
    """dpkg Installed-Size is in KiB; convert to bytes."""
    try:
        return int(kib) * 1024
    except (TypeError, ValueError):
        return None
