"""tpm configuration — reads $XDG_CONFIG_HOME/tpm/config.toml.

Falls back to defaults if file is missing or malformed.
Pure-Python TOML-like parsing (no tomllib dependency for 3.10 compat).
"""

import os


DEFAULTS = {
    "scan_depth": 2,
    "follow_symlinks": False,
    "show_hidden": True,
    "color": True,
    "auto_rescan_packages": True,
}


def _config_path():
    xdg = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.join(os.path.expanduser("~"), ".local", "config")
    return os.path.join(xdg, "tpm", "config.toml")


def load_config(path=None):
    """Load config with defaults fallback. TOML-like syntax only."""
    cfg = dict(DEFAULTS)
    path = path or _config_path()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("["):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key not in DEFAULTS:
                        continue
                    default = DEFAULTS[key]
                    if isinstance(default, bool):
                        cfg[key] = val.lower() in ("true", "1", "yes")
                    elif isinstance(default, int):
                        try:
                            cfg[key] = int(val)
                        except ValueError:
                            pass
    except (OSError, IOError):
        pass
    return cfg
