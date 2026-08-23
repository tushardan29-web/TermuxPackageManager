"""Environment detection — Termux vs regular Linux, active package manager.

Nothing here is hardcoded to /data/data/com.termux; $PREFIX and standard
Linux paths are discovered dynamically.
"""

import os
import platform
import shutil


def detect_environment():
    prefix = os.environ.get("PREFIX")
    termux = bool(prefix) or os.path.isdir(
        "/data/data/com.termux/files/usr")
    if not prefix:
        if termux:
            prefix = "/data/data/com.termux/files/usr"
        else:
            prefix = "/usr"
    info = {
        "termux": termux,
        "prefix": prefix,
        "platform": platform.system(),
        "machine": platform.machine(),
        "os_name": None,
        "manager": None,
    }
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("NAME="):
                    info["os_name"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass

    # Active manager: Termux exposes it explicitly.
    mgr = os.environ.get("TERMUX_APP_PACKAGE_MANAGER")
    if mgr in ("apt", "pacman", "pkg"):
        info["manager"] = mgr
        return info
    # Else infer from available databases/binaries (Debian vs Arch etc).
    if shutil.which("dpkg-query") and _has_dpkg_db(prefix):
        info["manager"] = "apt"
    elif _has_pacman_db(prefix):
        info["manager"] = "pacman"
    elif shutil.which("dpkg-query"):
        info["manager"] = "apt"
    return info


def _has_dpkg_db(prefix):
    return os.path.exists(os.path.join(prefix, "var/lib/dpkg/status")) or \
        os.path.exists("/var/lib/dpkg/status")


def _has_pacman_db(prefix):
    local = os.path.join(prefix, "var/lib/pacman/local")
    try:
        return os.path.isdir(local) and \
            len([d for d in os.listdir(local)
                 if d != "ALPM_DB_VERSION"]) > 0
    except OSError:
        return False


def warn_if_root():
    """tpm targets normal user contexts; root is unexpected."""
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False
