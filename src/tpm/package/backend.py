"""PackageManagerBackend abstraction + AptBackend / PacmanBackend.

Backends read the REAL package database via machine-readable interfaces:
  apt/dpkg: dpkg-query -W -f=... (STX-separated records), apt-mark,
            dpkg-query -L
  pacman:   $db/local/*/desc metadata files, `pacman -Ql` file lists

The core dependency engine never invokes a backend directly; it works
from the SQLite index.
"""

import os
import re
import shutil
import subprocess

from ..environment import detect_environment
from ..validation import validate_package_name


class PackageManagerBackend:
    name = "abstract"

    def available(self):
        raise NotImplementedError

    def list_installed(self):
        """Yield record dicts matching the packages schema."""
        raise NotImplementedError

    def explicit_names(self):
        return set()

    def essential_names(self):
        return set()

    def file_list(self, package):
        raise NotImplementedError

    def db_mtime(self):
        return None

    def broken_packages(self):
        return None


# ------------------------------------------------------------------ apt


class AptBackend(PackageManagerBackend):
    name = "apt/dpkg"

    def __init__(self, prefix=None):
        env = detect_environment()
        self.prefix = prefix or env["prefix"]
        candidates = [
            os.path.join(self.prefix, "var", "lib", "dpkg"),
            "/var/lib/dpkg",
        ]
        self.dpkg_dir = next((c for c in candidates if
                              os.path.exists(os.path.join(c, "status"))),
                             candidates[0])
        self.status_file = os.path.join(self.dpkg_dir, "status")

    def available(self):
        return bool(shutil.which("dpkg-query")) and \
            os.path.exists(self.status_file)

    def db_mtime(self):
        try:
            return int(os.stat(self.status_file).st_mtime)
        except OSError:
            return None

    # keep old alias working
    dpkg_status_mtime = db_mtime

    def _query(self, fmt):
        """dpkg-query with STX-separated records; descriptions are
        multiline so newline-splitting is unsafe, and NUL cannot appear
        in subprocess arguments."""
        proc = subprocess.run(
            ["dpkg-query", "-W", f"-f={fmt}\x02"],
            capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                f"dpkg-query failed: "
                f"{proc.stderr.decode(errors='replace').strip()}")
        return [r for r in proc.stdout.decode(errors="replace").split("\x02")
                if r.strip()]

    def list_installed(self):
        from ..dependency.parser import parse_depends_field
        lines = self._query(
            "${binary:Package}\t${Version}\t${Architecture}\t"
            "${Installed-Size}\t${db:Status-Abbrev}\t${essential}\t"
            "${Priority}\t${Depends}\t${Description}")
        for line in lines:
            parts = line.split("\t", 8)
            while len(parts) < 9:
                parts.append("")
            pkg, version, arch, isize, status, essential, prio, deps, desc = parts[:9]
            name = pkg.split(":")[0]
            if len(status) >= 2 and status[0] != "i":
                continue
            yield {
                "name": name,
                "version": version or None,
                "architecture": arch or None,
                "status": status,
                "installed_size": int(isize) * 1024 if isize.isdigit() else None,
                "explicitly_installed": 0,
                "essential": 1 if essential == "yes" else 0,
                "priority": prio or None,
                "source": "dpkg",
                "description": desc or None,
                "depends_groups": parse_depends_field(deps),
            }

    def explicit_names(self):
        try:
            proc = subprocess.run(["apt-mark", "showmanual"],
                                  capture_output=True, text=True,
                                  timeout=120)
            if proc.returncode == 0:
                return {l.strip() for l in proc.stdout.splitlines()
                        if l.strip()}
        except (OSError, subprocess.TimeoutExpired):
            pass
        return set()

    def essential_names(self):
        return set()

    def file_list(self, package):
        validate_package_name(package)
        proc = subprocess.run(["dpkg-query", "-L", package],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return []
        paths = [l for l in proc.stdout.splitlines()
                 if l.strip() and l != "/."]
        return [(p, self._path_size(p)) for p in paths]

    @staticmethod
    def _path_size(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return None

    def broken_packages(self):
        try:
            proc = subprocess.run(["dpkg", "--audit"],
                                  capture_output=True, text=True, timeout=60)
            return proc.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None


# --------------------------------------------------------------- pacman


_PACMAN_DEP_RE = re.compile(r"^([a-zA-Z0-9@._+-]+)")


class PacmanBackend(PackageManagerBackend):
    name = "pacman"

    def __init__(self, prefix=None):
        env = detect_environment()
        self.prefix = prefix or env["prefix"]
        candidates = [
            os.path.join(self.prefix, "var", "lib", "pacman"),
            "/var/lib/pacman",
        ]
        self.db_dir = next((c for c in candidates
                            if os.path.isdir(c)), candidates[0])
        self.local_dir = os.path.join(self.db_dir, "local")

    def available(self):
        return os.path.isdir(self.local_dir) and \
            len([d for d in self._distro_dirs()]) > 0

    def _distro_dirs(self):
        try:
            return [d for d in os.listdir(self.local_dir)
                    if d != "ALPM_DB_VERSION"]
        except OSError:
            return []

    def db_mtime(self):
        try:
            return int(max(os.stat(os.path.join(self.local_dir, d)).st_mtime
                           for d in self._distro_dirs()))
        except (OSError, ValueError):
            return None

    def _parse_desc(self, desc_path):
        data = {}
        current = None
        try:
            with open(desc_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line.startswith("%") and line.endswith("%"):
                        current = line[1:-1]
                        data.setdefault(current, [])
                    elif current is not None and line:
                        data[current].append(line)
        except OSError:
            return None
        return data

    @staticmethod
    def _dep_name(expr):
        m = _PACMAN_DEP_RE.match(expr)
        return m.group(1) if m else expr

    def list_installed(self):
        from ..dependency.parser import DependencyGroup, Dependency
        for d in sorted(self._distro_dirs()):
            desc = self._parse_desc(
                os.path.join(self.local_dir, d, "desc"))
            if not desc or "NAME" not in desc:
                continue
            name = desc["NAME"][0]
            size = None
            if desc.get("SIZE"):
                try:
                    size = int(desc["SIZE"][0])
                except ValueError:
                    pass
            # %REASON%: 0 = explicitly installed, 1 = dependency
            explicit = 0
            if desc.get("REASON"):
                explicit = 1 if desc["REASON"][0] == "0" else 0
            groups = []
            for dep_expr in desc.get("DEPENDS", []):
                dep_name = self._dep_name(dep_expr)
                constraint = dep_expr[len(dep_name):] or None
                groups.append(DependencyGroup(
                    [Dependency(name=dep_name,
                                version_constraint=constraint)]))
            yield {
                "name": name,
                "version": (desc.get("VERSION") or [None])[0],
                "architecture": (desc.get("ARCH") or [None])[0],
                "status": "installed",
                "installed_size": size,
                "explicitly_installed": explicit,
                "essential": 0,
                "priority": (desc.get("GROUPS") or [None])[0],
                "source": "pacman",
                "description": (desc.get("DESC") or [None])[0],
                "depends_groups": groups,
            }

    def explicit_names(self):
        # Explicit flags come from %REASON% in list_installed; this
        # method exists for callers that need just the names.
        names = set()
        for d in self._distro_dirs():
            desc = self._parse_desc(
                os.path.join(self.local_dir, d, "desc"))
            if desc and desc.get("REASON") and \
                    desc["REASON"][0] == "0" and desc.get("NAME"):
                names.add(desc["NAME"][0])
        return names

    def essential_names(self):
        return set()

    def file_list(self, package):
        validate_package_name(package)
        if not shutil.which("pacman"):
            return []
        proc = subprocess.run(["pacman", "-Ql", package],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return []
        files = []
        for line in proc.stdout.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] and parts[1] != "/":
                path = "/" + parts[1].lstrip("/")
                files.append((path, self._path_size(
                    self.prefix + path)))
        return files

    @staticmethod
    def _path_size(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return None

    def broken_packages(self):
        if not shutil.which("pacman"):
            return None
        proc = subprocess.run(["pacman", "-Dk"],
                              capture_output=True, text=True, timeout=120)
        return None if proc.returncode == 0 else \
            proc.stdout.strip() or proc.stderr.strip()


# ------------------------------------------------------------- detection


def detect_backend(env=None):
    """Pick the backend whose database actually has content."""
    env = env or detect_environment()
    preferred = env.get("manager")
    backends = []
    if shutil.which("dpkg-query"):
        b = AptBackend(prefix=env.get("prefix"))
        if os.path.exists(b.status_file):
            backends.append((b.name in (preferred,), b))
    pb = PacmanBackend(prefix=env.get("prefix"))
    if pb.available():
        backends.append(("pacman" in (preferred,), pb))
    backends.sort(key=lambda t: -t[0])
    return backends[0][1] if backends else None
