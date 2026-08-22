"""tpm CLI — argparse entry point.

Presentation layer only; all logic lives in the shared core library
(tpm.core, tpm.scanner, tpm.removal, tpm.storage) which the TUI also uses.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import __version__
from .database import Database
from .dependency.parser import group_satisfied
from .formatter import format_size
from .package.backend import detect_backend
from .removal.simulator import simulate_removal
from .scanner import ensure_fresh, is_stale, run_scan
from .storage.scanner import classify_path, detect_caches, largest_dirs, scan_dir_size
from .validation import validate_package_name

# ---------------------------------------------------------------- colors


class UI:
    """Minimal output abstraction: rich tables on tty, plain otherwise,
    JSON-only mode for --json."""

    def __init__(self, json_mode=False, color=None):
        self.json_mode = json_mode
        self._rich = None
        if not json_mode and sys.stdout.isatty() and (color is not False):
            try:
                from rich.console import Console
                from rich.table import Table
                self._console = Console()
                self._Table = Table
                self._rich = True
            except ImportError:
                self._rich = False
        else:
            self._console = None
            self._rich = False

    def err(self, msg):
        print(f"tpm: error: {msg}", file=sys.stderr)

    def warn(self, msg):
        if not self.json_mode:
            print(f"warning: {msg}", file=sys.stderr)

    def emit_json(self, obj):
        json.dump(obj, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    def table(self, headers, rows):
        if self.json_mode:
            return
        if self._rich:
            t = self._Table(show_header=True, header_style="bold")
            for h in headers:
                t.add_column(h)
            for row in rows:
                t.add_row(*[str(c) for c in row])
            self._console.print(t)
        else:
            widths = [max([len(str(h))] + [len(str(r[i])) for r in rows])
                      for i, h in enumerate(headers)] if rows \
                else [len(h) for h in headers]
            print("  ".join(str(h).ljust(w)
                            for h, w in zip(headers, widths)))
            print("─" * sum(w + 2 for w in widths))
            for r in rows:
                print("  ".join(str(c).ljust(w)
                                for c, w in zip(r, widths)))

    def line(self, msg=""):
        if not self.json_mode:
            print(msg)

    def heading(self, title):
        if not self.json_mode:
            print(title)
            print("═" * max(len(title), 36))


# ---------------------------------------------------------------- helpers


def _load(db=None):
    from .core import load_state
    return load_state(db=db or Database())


def _check_backend(ui):
    backend = detect_backend()
    if backend is None:
        ui.err("no supported package manager found (need dpkg-query)")
        sys.exit(1)
    return backend


def _warn_stale(ui, db):
    stale, mtime = is_stale(db)
    if stale:
        ui.warn("system package database changed since last scan "
                "(run `tpm rescan`)")
    return stale


def _classification_rows(state, db):
    rows = []
    for name, row in sorted(state.graph.packages.items()):
        cls, count = state.classification(name)
        label = f"SHARED ×{count}" if cls == "SHARED" else cls
        rows.append((name, format_size(row["installed_size"]), label))
    return rows


# ---------------------------------------------------------------- commands


def cmd_scan(args, ui, db):
    n = run_scan(db=db)
    ui.heading("SCAN COMPLETE")
    ui.line(f"{n} packages indexed")
    return 0


def cmd_list(args, ui, db):
    state = _load(db)
    filters = [f for f in ("explicit", "dependencies", "shared", "orphans")
               if getattr(args, f)]
    by_largest = args.largest

    entries = []
    for name, row in state.graph.packages.items():
        cls, count = state.classification(name)
        entries.append((name, row["installed_size"] or 0, cls, count))

    if "explicit" in filters:
        entries = [e for e in entries if e[2] == "EXPLICIT"]
    if "dependencies" in filters:
        entries = [e for e in entries if e[2] in ("SINGLE-USE", "SHARED")]
    if "shared" in filters:
        entries = [e for e in entries if e[2] == "SHARED"]
    if "orphans" in filters:
        entries = [e for e in entries if e[2] == "ORPHAN"]
    if by_largest:
        entries.sort(key=lambda e: -(e[1] or 0))
    else:
        entries.sort(key=lambda e: -e[1])

    if args.json:
        ui.emit_json([{"name": n, "installed_size": s,
                       "type": c, "rdep_count": k}
                      for n, s, c, k in entries])
        return 0

    ui.heading("PACKAGES")
    ui.table(["NAME", "SIZE", "TYPE"],
             [(n, format_size(s), (f"SHARED ×{k}" if c == "SHARED" else c))
              for n, s, c, k in entries[:200]])
    ui.line(f"\n{len(entries)} packages")
    return 0


def cmd_search(args, ui, db):
    term = args.term.lower()
    state = _load(db)
    hits = []
    for name, row in state.graph.packages.items():
        desc = (row["description"] or "").lower()
        if term in name.lower() or term in desc:
            cls, count = state.classification(name)
            hits.append({"name": name, "description": row["description"],
                         "type": cls})
    if args.json:
        ui.emit_json(hits)
        return 0
    ui.heading(f"SEARCH: {args.term}")
    ui.table(["NAME", "TYPE", "DESCRIPTION"],
             [(h["name"], h["type"], (h["description"] or "")[:60])
              for h in hits[:50]])
    ui.line(f"\n{len(hits)} matches")
    return 0


def cmd_info(args, ui, db):
    name = validate_package_name(args.package)
    state = _load(db)
    entry = db.get_package(name)
    if entry is None:
        ui.err(f"package not indexed: {name} (try `tpm rescan`)")
        return 1
    row = entry["row"]
    g = state.graph

    def dep_lines():
        out = []
        installed_names = set(g.packages)
        for group in g.deps.get(name, []):
            for alt in group.alternatives:
                cls, cnt = g.classify(alt.name, state.explicit,
                                      state.essential) \
                    if alt.name in installed_names else ("MISSING", 0)
                label = f"SHARED ×{cnt}" if cls == "SHARED" else cls
                out.append((alt.name, label))
        return out

    data = {
        "name": name,
        "version": row["version"],
        "architecture": row["architecture"],
        "installed_size_bytes": row["installed_size"],
        "type": state.classify_label(name),
        "essential": bool(row["essential"]),
        "priority": row["priority"],
        "description": row["description"],
        "dependencies": [{"name": n, "type": t} for n, t in dep_lines()],
        "reverse_dependencies": g.direct_rdeps(name),
        "recursive_reverse_dependencies": g.recursive_rdeps(name),
        "file_count_indexed": db.file_count(name),
    }
    if args.json:
        ui.emit_json(data)
        return 0

    ui.heading(name.upper())
    ui.line(f"Version:             {row['version']}")
    ui.line(f"Architecture:        {row['architecture']}")
    ui.line(f"Installed size:      {format_size(row['installed_size'])}")
    ui.line(f"Type:                {data['type']}"
            + ("  [ESSENTIAL]" if row["essential"] else ""))
    ui.line(f"Files indexed:       {db.file_count(name)}")

    deps = data["dependencies"]
    ui.line(f"\nDependencies ({len(deps)}):")
    for d in deps:
        ui.line(f"  {d['name']:<30} {d['type']}")
    rdeps = g.direct_rdeps(name)
    ui.line(f"\nReverse dependencies ({len(rdeps)}):")
    for rd in rdeps[:40]:
        ui.line(f"  {rd}")
    if len(rdeps) > 40:
        ui.line(f"  ... and {len(rdeps) - 40} more")
    return 0


def cmd_deps(args, ui, db):
    name = validate_package_name(args.package)
    state = _load(db)
    g = state.graph
    if name not in g.packages:
        ui.err(f"not installed: {name}")
        return 1
    names = g.recursive_deps(name) if args.recursive else g.direct_deps(name)
    if args.json:
        ui.emit_json({"package": name, "recursive": bool(args.recursive),
                      "dependencies": names})
        return 0
    mode = "RECURSIVE DEPENDENCIES" if args.recursive else "DEPENDENCIES"
    ui.heading(f"{mode}: {name}")
    for n in names:
        label = state.classify_label(n)
        ui.line(f"  {n:<32} [{label}]")
    if not names:
        ui.line("  (none)")
    return 0


def cmd_rdeps(args, ui, db):
    name = validate_package_name(args.package)
    state = _load(db)
    g = state.graph
    if name not in g.packages:
        ui.err(f"not installed: {name}")
        return 1
    names = g.recursive_rdeps(name) if args.recursive else g.direct_rdeps(name)
    if args.json:
        ui.emit_json({"package": name, "recursive": bool(args.recursive),
                      "required_by": names,
                      "count": len(names)})
        return 0
    mode = "REQUIRED BY (RECURSIVE)" if args.recursive else "REQUIRED BY"
    ui.heading(f"{mode}: {name}")
    for n in names:
        ui.line(f"  {n}")
    if not names:
        ui.line("  (nothing — orphan candidate)")
    else:
        ui.line(f"\ncount: {len(names)}")
    return 0


def cmd_orphans(args, ui, db):
    state = _load(db)
    orphans = []
    for name, row in state.graph.packages.items():
        if state.classification(name)[0] == "ORPHAN":
            orphans.append((name, row["installed_size"] or 0))
    orphans.sort(key=lambda x: -x[1])
    total = sum(s for _, s in orphans)
    if args.total:
        if args.json:
            ui.emit_json({"orphan_count": len(orphans),
                          "recovery_bytes": total})
        else:
            ui.line(format_size(total))
        return 0
    if args.json:
        ui.emit_json({"orphans": [{"name": n, "installed_size": s}
                                  for n, s in orphans],
                      "total_recovery_bytes": total})
        return 0
    ui.heading("ORPHANED PACKAGES")
    ui.table(["NAME", "SIZE"], [(n, format_size(s)) for n, s in orphans[:100]])
    ui.line(f"\nPotential recovery: {format_size(total)}"
            f"  ({len(orphans)} packages)")
    return 0


def cmd_files(args, ui, db):
    name = validate_package_name(args.package)
    backend = _check_backend(ui)
    files = backend.file_list(name)
    if not files:
        ui.err(f"no file list for: {name}")
        return 1
    if args.largest:
        files.sort(key=lambda f: -(f[1] or 0))
        files = files[:25]
    if args.json:
        ui.emit_json({"package": name, "files": [
            {"path": p, "size": s} for p, s in files]})
        return 0
    ui.heading(f"FILES: {name}" + (" (largest)" if args.largest else ""))
    for p, s in files:
        size = format_size(s) if s is not None else ""
        ui.line(f"{size:>12}  {p}")
    ui.line(f"\n{len(files)} shown / total listed above")
    return 0


def cmd_storage(args, ui, db):
    path = os.path.abspath(os.path.expanduser(args.path or "~"))
    depth = args.depth
    if args.json:
        sizes = scan_dir_size(path, max_depth=depth)
        total = sum(v for k, v in sizes.items() if k != path)
        caches = detect_caches()
        ui.emit_json({
            "path": path,
            "depth": depth,
            "total_bytes": total,
            "breakdown": [{"path": p, "bytes": s}
                          for p, s in sorted(sizes.items(),
                                             key=lambda kv: -kv[1])][:50],
            "caches": [{"path": c["path"], "label": c["label"],
                        "bytes": c["size"]} for c in caches],
        })
        return 0
    ui.heading("TERMUX STORAGE")
    with_progress = sys.stdout.isatty()
    progress = None
    sizes = scan_dir_size(path, max_depth=depth)
    top = [(p, s) for p, s in sizes.items() if p != path]
    top.sort(key=lambda x: -x[1])
    total = sum(s for _, s in top)
    ui.line(f"\n{format_size(total, 2)} under {path} (depth {depth})\n")
    for p, s in top[:20]:
        cat = classify_path(p)
        short = p.replace(os.path.expanduser("~"), "~")
        ui.line(f"{format_size(s, 1):>10}   {short}  [{cat}]")
    ui.line("\nCache categories:")
    for c in detect_caches():
        ui.line(f"{format_size(c['size'], 1):>10}   {c['label']}  "
                f"({c['path'].replace(os.path.expanduser('~'), '~')})")
    return 0


def cmd_largest(args, ui, db):
    path = os.path.abspath(os.path.expanduser(args.path or "~"))
    items = largest_dirs(path, limit=15, max_depth=args.depth)
    if args.json:
        ui.emit_json({"path": path, "largest": [
            {"path": p, "bytes": s} for p, s in items]})
        return 0
    ui.heading("LARGEST DIRECTORIES")
    for p, s in items:
        ui.line(f"{format_size(s, 1):>10}  {p.replace(os.path.expanduser('~'), '~')}")
    return 0


def cmd_cache(args, ui, db):
    caches = detect_caches()
    total = sum(c["size"] for c in caches)
    if args.json:
        ui.emit_json({"caches": [{"path": c["path"], "label": c["label"],
                                  "bytes": c["size"]} for c in caches],
                      "potential_cleanup_bytes": total})
        return 0
    ui.heading("CACHE STORAGE")
    for c in caches:
        ui.line(f"{format_size(c['size'], 2):>10}   {c['label']}")
    ui.line(f"\nPotential cleanup: {format_size(total, 2)}")
    return 0


def cmd_clean(args, ui, db):
    """Interactive review screen. Never deletes without explicit confirmation."""
    caches = [c for c in detect_caches() if c["size"] > 1024]
    total = sum(c["size"] for c in caches)
    ui.heading("CLEANUP REVIEW")
    for i, c in enumerate(caches, 1):
        ui.line(f"[{i}] {c['label']:<24} {format_size(c['size'], 2):>10}   "
                f"{c['path']}")
    ui.line(f"\nPotential recovery: {format_size(total, 2)}")
    if args.yes:
        ui.warn("--yes given for clean; cleaning APT cache only (safe op)")
        targets = [c for c in caches if "APT" in c["label"]]
    elif not sys.stdin.isatty():
        ui.line("\n(non-interactive; nothing was deleted)")
        return 0
    else:
        answer = input("\nClean APT cache only? [y/N] ").strip().lower()
        targets = [c for c in caches if "APT" in c["label"]] \
            if answer == "y" else []
        if answer == "y":
            pass
        else:
            ui.line("Nothing was deleted.")
            return 0
    cleaned = 0
    for c in targets:
        archives = os.path.join(c["path"], "archives") \
            if c["path"].endswith("apt") else c["path"]
        if os.path.isdir(archives):
            for entry in os.listdir(archives):
                fp = os.path.join(archives, entry)
                try:
                    if os.path.isfile(fp) and not os.path.islink(fp) \
                            and (entry.endswith(".deb") or
                                 entry.endswith(".list") or
                                 "cache" in entry):
                        cleaned += os.path.getsize(fp)
                        os.remove(fp)
                except OSError as e:
                    ui.warn(f"could not remove {fp}: {e}")
    ui.line(f"Cleaned: {format_size(cleaned, 2)}")
    return 0


def _print_plan(ui, sim, requested_name):
    ui.heading("REMOVAL SIMULATION")
    ui.line("")
    sizes = {}
    ui.line("Requested:")
    ui.line(f"  {requested_name}")
    ui.line("")
    ui.line("Would become removable:")
    if sim.cascade_removable:
        for n in sim.cascade_removable:
            ui.line(f"  {n}")
    else:
        ui.line("  (none)")
    ui.line("")
    ui.line("Shared dependencies retained:")
    for n in sim.retained_shared or ["(none)"]:
        ui.line(f"  {n}")
    if sim.broken:
        ui.line("")
        ui.line("BROKEN packages would result:")
        for b in sim.broken:
            ui.line(f"  {b}", )
    if sim.protected_essential:
        ui.line("")
        ui.line("PROTECTED essential packages (not removed):")
        for n in sim.protected_essential:
            ui.line(f"  {n}")
    if sim.blocked_reasons:
        ui.line("")
        for r in sim.blocked_reasons:
            ui.line(f"note: {r}")
    ui.line("")
    ui.line(f"Estimated package storage recovery: "
            f"{format_size(sim.total_recovery_bytes, 2)}")
    ui.line("\nNo changes were made.")


def cmd_remove(args, ui, db):
    name = validate_package_name(args.package)
    state = _load(db)
    g = state.graph
    if name not in g.packages:
        ui.err(f"not installed: {name}")
        return 1
    if name in state.essential:
        ui.err(f"{name} is ESSENTIAL — removal refused")
        return 1

    # Fresh metadata before any destructive consideration.
    refreshed = ensure_fresh(db)

    sim = simulate_removal(g, [name], state.explicit, state.essential)

    if args.json:
        data = sim.to_dict()
        data["refreshed_before_analysis"] = refreshed
        ui.emit_json(data)
        return 0

    _print_plan(ui, sim, name)
    if args.simulate or args.dry_run:
        return 0

    # Real removal path: explicit confirmation required.
    if args.yes and not sys.stdin.isatty():
        confirmed = True
    elif sys.stdin.isatty():
        answer = input("\nProceed with actual apt remove? Type 'yes': ").strip()
        confirmed = answer == "yes"
    else:
        ui.err("refusing interactive confirmation in non-tty without --yes")
        return 1
    if not confirmed:
        ui.line("Cancelled. No changes were made.")
        return 0

    # Final freshness check immediately before execution.
    ensure_fresh(db)
    backend = detect_backend()
    if backend is not None and backend.name == "pacman":
        proc = subprocess.run(["pacman", "-R", "--noconfirm", name])
    else:
        proc = subprocess.run(["apt", "remove", "-y", name])
    return proc.returncode


def cmd_doctor(args, ui, db):
    from .environment import detect_environment, warn_if_root
    checks = []

    def check(ok, msg, warn=False):
        checks.append({"ok": ok, "msg": msg, "warn": warn})

    env = detect_environment()
    check(env["termux"], "Termux environment detected"
          if env["termux"] else f"non-Termux Linux ({env['os_name'] or 'unknown'})",
          warn=not env["termux"])
    if warn_if_root():
        check(False, "running as root — tpm is designed for unprivileged use",
              warn=True)
    else:
        check(True, "running unprivileged")
    check(bool(env["manager"]), f"active package manager: {env['manager']}"
          if env["manager"] else "no package manager detected")
    backend = detect_backend(env)
    check(backend is not None, "package database detected "
          f"({backend.name})" if backend else "no supported package database")
    if backend:
        check(os.path.exists(backend.status_file), "dpkg status database readable")
        broken = backend.broken_packages()
        check(not broken, "dpkg audit clean" if not broken else broken,
              warn=bool(broken))
    try:
        state = _load(db)
        check(True, f"{len(state.graph.packages)} packages indexed")
        # dependency consistency: unsatisfied groups
        problems = []
        installed_names = set(state.graph.packages)
        for pkg, groups in state.graph.deps.items():
            for grp in groups:
                if not group_satisfied(grp, installed_names):
                    problems.append(f"{pkg} -> {'|'.join(a.name for a in grp.alternatives)}")
        check(not problems, "dependency graph valid"
              if not problems else f"{len(problems)} unsatisfied dependency groups",
              warn=True)
        orphans = [n for n in installed_names
                   if state.classification(n)[0] == "ORPHAN"]
        check(True, f"{len(orphans)} orphan packages", warn=bool(orphans))
    except Exception as e:
        check(False, f"index problem: {e}")
    stale, _ = is_stale(db)
    check(not stale, "index fresh" if not stale else "index STALE — run tpm rescan",
          warn=stale)

    if args.dependencies:
        if args.json:
            ui.emit_json({"checks": checks})
        else:
            for c in checks:
                mark = "✓" if c["ok"] else ("⚠" if c["warn"] else "✗")
                ui.line(f"{mark} {c['msg']}")
        return 0 if all(c["ok"] or c["warn"] for c in checks) else 1

    failed = [c for c in checks if not c["ok"] and not c["warn"]]
    if args.json:
        ui.emit_json({"healthy": not failed, "checks": checks})
        return 0 if not failed else 1
    ui.heading("TPM DOCTOR")
    for c in checks:
        mark = "✓" if c["ok"] else ("⚠" if c["warn"] else "✗")
        ui.line(f"{mark} {c['msg']}")
    return 0 if not failed else 1


def cmd_version(args, ui, db):
    ui.line(f"tpm {__version__}")
    return 0


def cmd_tui(args, ui, db):
    from .tui import run
    return run()


# ---------------------------------------------------------------- parser


def build_parser():
    p = argparse.ArgumentParser(
        prog="tpm",
        description="Termux Package Manager & Storage Analyzer")
    p.add_argument("--version", action="version",
                   version=f"tpm {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="machine-readable JSON output on stdout")
    common.add_argument("--no-color", action="store_true")

    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("scan", parents=[common],
                   help="scan installed packages into index").set_defaults(func=cmd_scan)
    sub.add_parser("rescan", parents=[common],
                   help="force full rescan").set_defaults(func=cmd_scan)

    lp = sub.add_parser("list", parents=[common], help="list packages")
    for flag in ("explicit", "dependencies", "shared", "orphans"):
        lp.add_argument(f"--{flag}", action="store_true")
    lp.add_argument("--largest", action="store_true")
    lp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", parents=[common], help="search installed packages")
    sp.add_argument("term")
    sp.set_defaults(func=cmd_search)

    ip = sub.add_parser("info", parents=[common], help="package details")
    ip.add_argument("package")
    ip.set_defaults(func=cmd_info)

    dp = sub.add_parser("deps", parents=[common], help="direct/recursive dependencies")
    dp.add_argument("package")
    dp.add_argument("--recursive", action="store_true")
    dp.set_defaults(func=cmd_deps)

    rp = sub.add_parser("rdeps", parents=[common], help="what requires this package")
    rp.add_argument("package")
    rp.add_argument("--recursive", action="store_true")
    rp.set_defaults(func=cmd_rdeps)

    op = sub.add_parser("orphans", parents=[common], help="packages nothing depends on")
    op.add_argument("--total", action="store_true")
    op.set_defaults(func=cmd_orphans)

    fp = sub.add_parser("files", parents=[common], help="files owned by a package")
    fp.add_argument("package")
    fp.add_argument("--largest", action="store_true")
    fp.set_defaults(func=cmd_files)

    st = sub.add_parser("storage", parents=[common], help="storage breakdown")
    st.add_argument("path", nargs="?", default=None)
    st.add_argument("--depth", type=int, default=2)
    st.set_defaults(func=cmd_storage)

    lg = sub.add_parser("largest", parents=[common], help="largest directories")
    lg.add_argument("--path", default=None)
    lg.add_argument("--depth", type=int, default=3)
    lg.set_defaults(func=cmd_largest)

    cp = sub.add_parser("cache", parents=[common], help="detect caches")
    cp.set_defaults(func=cmd_cache)

    cl = sub.add_parser("clean", parents=[common], help="interactive cleanup review")
    cl.add_argument("--yes", action="store_true")
    cl.set_defaults(func=cmd_clean)

    rm = sub.add_parser("remove", parents=[common], help="remove a package (plan first)")
    rm.add_argument("package")
    rm.add_argument("--simulate", "--dry-run", dest="simulate",
                    action="store_true")
    rm.add_argument("--yes", action="store_true")
    rm.set_defaults(func=cmd_remove)

    doc = sub.add_parser("doctor", parents=[common], help="diagnostics")
    doc.add_argument("--dependencies", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    sub.add_parser("tui", parents=[common], help="interactive TUI").set_defaults(func=cmd_tui)
    sub.add_parser("version", parents=[common], help="print version").set_defaults(func=cmd_version)
    sub.add_parser("help", parents=[common], help="show help").set_defaults(func=lambda a,u,d: (p.print_help(), 0)[-1])
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # bare `tpm` launches TUI when tty, help otherwise
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .tui import run
            try:
                return run() or 0
            except KeyboardInterrupt:
                return 130
        parser.print_help()
        return 0
    ui = UI(json_mode=getattr(args, "json", False),
            color=(not getattr(args, "no_color", False)))
    try:
        db = Database()
        return args.func(args, ui, db) or 0
    except ValueError as e:
        ui.err(str(e))
        return 2
    except KeyboardInterrupt:
        ui.err("interrupted")
        return 130
    except Exception as e:
        ui.err(f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
