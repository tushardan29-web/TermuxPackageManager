"""Interactive TUI for tpm — built on rich only (no textual).

Uses the same core library as the CLI (tpm.core.load_state, tpm.formatter,
tpm.storage.scanner, tpm.removal.simulator, tpm.database). No logic is
duplicated here; this module is purely presentation + an event loop.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import shutil
import select
import signal
import termios
import tty
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

from rich.console import Console, ConsoleOptions
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.tree import Tree
from rich.align import Align
from rich.theme import Theme

from .core import load_state
from .formatter import format_size
from .package.backend import detect_backend
from .storage.scanner import (
    detect_caches,
    largest_dirs,
    scan_dir_size,
    ScanProgress,
)
from .removal.simulator import simulate_removal
from .database import Database


THEME_STYLES = {
    "essential": "bold yellow",
    "explicit": "bold cyan",
    "shared": "magenta",
    "orphan": "red",
    "single": "dim",
    "selected": "bold on blue",
    "border": "blue",
    "help": "dim",
    "stat": "bold",
}


def _make_console(no_color: bool, file=None) -> Console:
    theme = Theme(THEME_STYLES)
    return Console(no_color=no_color, theme=theme,
                   highlight=not no_color, soft_wrap=False, file=file)


@dataclass
class Context:
    console: Console
    state: Any = None
    error: str = ""
    status: str = ""
    no_color: bool = False
    caches: list = None
    home_largest: list = None
    prefix_largest: list = None
    db: Any = None

    def ensure_state(self) -> Any:
        if self.state is None and not self.error:
            try:
                self.state = load_state(db=self.db)
            except Exception as e:
                self.error = f"failed to load state: {e}"
                self.state = None
        return self.state

    def ensure_caches(self) -> list:
        if self.caches is None:
            try:
                self.caches = detect_caches() or []
            except Exception as e:
                self.caches = []
                self.error = f"cache scan failed: {e}"
        return self.caches

    def ensure_db(self) -> Optional[Database]:
        if self.db is None:
            try:
                self.db = Database()
            except Exception:
                self.db = None
        return self.db


def _cls_style(cls: str) -> str:
    m = {
        "ESSENTIAL": "essential",
        "EXPLICIT": "explicit",
        "SHARED": "shared",
        "ORPHAN": "orphan",
        "SINGLE-USE": "single",
    }
    return m.get(cls, "")


def _pkg_record(state, name):
    row = state.graph.packages[name]
    cls, cnt = state.classification(name)
    return {
        "name": name,
        "size": row["installed_size"] or 0,
        "cls": cls,
        "cnt": cnt,
        "version": row["version"],
        "arch": row["architecture"],
        "desc": row["description"],
        "priority": row["priority"],
    }


def _all_pkg_records(state):
    out = []
    for name in sorted(state.graph.packages):
        out.append(_pkg_record(state, name))
    return out


def _orphan_names(state):
    out = []
    for name in sorted(state.graph.packages):
        cls, _ = state.classification(name)
        if cls == "ORPHAN":
            out.append(name)
    return out


# --------------------------------------------------------------------------
# Low-level terminal input
# --------------------------------------------------------------------------
def _input_ready(fd, timeout):
    try:
        r, _, _ = select.select([fd], [], [], timeout)
        return len(r) > 0
    except (OSError, ValueError):
        return False


_ESCAPE_MAP = {
    b"\x1b[A": "up",
    b"\x1b[B": "down",
    b"\x1b[C": "right",
    b"\x1b[D": "left",
    b"\x1b[2~": "insert",
    b"\x1b[3~": "delete",
    b"\x1b[5~": "pageup",
    b"\x1b[6~": "pagedown",
    b"\x1b[1~": "home",
    b"\x1b[4~": "end",
    b"\x1bOH": "home",
    b"\x1bOF": "end",
    b"\x1b[H": "home",
    b"\x1b[F": "end",
}


def _decode_seq(seq: bytes):
    if seq in _ESCAPE_MAP:
        return _ESCAPE_MAP[seq]
    if seq == b"\x1b":
        return "esc"
    return "esc"


def _read_key(fd) -> Optional[str]:
    if not _input_ready(fd, 0.1):
        return None
    try:
        b = os.read(fd, 1)
    except OSError:
        return None
    if not b:
        return None
    if b == b"\x1b":
        seq = b"\x1b"
        for _ in range(8):
            if _input_ready(fd, 0.02):
                try:
                    nxt = os.read(fd, 1)
                except OSError:
                    break
                if not nxt:
                    break
                seq += nxt
            else:
                break
        return _decode_seq(seq)
    if b == b"\r" or b == b"\n":
        return "enter"
    if b in (b"\x7f", b"\x08"):
        return "backspace"
    if b == b"\x03":
        return "ctrl-c"
    if b == b"\x04":
        return "ctrl-d"
    if b == b"\x15":
        return "ctrl-u"
    if b == b"\x01":
        return "ctrl-a"
    if b == b"\x18":
        return "ctrl-x"
    if b == b"\t":
        return "tab"
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return None


@contextmanager
def _raw_terminal():
    fd = sys.stdin.fileno()
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSAFLUSH)
        yield fd
    except (termios.error, AttributeError, OSError):
        yield sys.stdin.fileno()
    finally:
        if old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except termios.error:
                pass


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------
def _header(ctx, title: str, help_text: str = "") -> Panel:
    w, h = ctx.console.size
    status_line = ""
    msgs = []
    if ctx.status:
        msgs.append(ctx.status)
    if ctx.error:
        msgs.append(ctx.error)
    status_line = " | ".join(msgs) if msgs else ""
    footer = help_text
    if status_line:
        footer = f"{help_text}\n[help]{status_line}[/]"
    t = Text()
    t.append(title, style="bold")
    t.append("  ")
    t.append(Text("← q back · ?:help", style="help"))
    body = t
    return Panel(body, title=title, border_style="border")


def _stat_table(ctx, rows):
    t = Table.grid(padding=(0, 1, 0, 1))
    t.add_column(justify="left")
    t.add_column(justify="right")
    for label, val in rows:
        t.add_row(Text(str(label), style="stat"),
                  Text(str(val), style="help"))
    return t


def _footer(ctx, help_text: str) -> Panel:
    msgs = []
    if ctx.status:
        msgs.append(ctx.status)
    if ctx.error:
        msgs.append(ctx.error)
    base = help_text
    if msgs:
        base = base + "\n" + "[help]" + " | ".join(msgs) + "[/]"
    return Panel(Text(base), border_style="help", title_align="left")


def _screen(ctx, title, body, help_text=""):
    header = Panel(Text(title, style="bold"), border_style="border",
                   title_align="left")
    foot = _footer(ctx, help_text)
    outer = Table.grid(expand=True)
    outer.add_column()
    outer.add_row(header)
    outer.add_row(body)
    outer.add_row(foot)
    return outer


def _paged(items, sel, console, render_row=None, height_pad=6):
    h = console.height or 24
    limit = max(4, h - height_pad)
    total = len(items)
    start = max(0, sel - limit // 2)
    if total:
        start = min(start, max(0, total - limit))
    end = min(total, start + limit) if total else 0
    win = items[start:end]
    return win, start, end


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------
class Screen:
    title = "tpm"

    def render(self, ctx: Context):
        raise NotImplementedError

    def handle(self, key: str, ctx: Context):
        return ("stay",)


class DashboardScreen(Screen):
    title = "Dashboard"

    def render(self, ctx: Context):
        ctx.ensure_state()
        ctx.ensure_caches()
        state = ctx.state
        if state is None:
            body = Panel(Text(ctx.error or "no system state available",
                              style="red"), border_style="border",
                         title="Dashboard")
            return _screen(ctx, "Dashboard", body,
                           "p=Packages s=Storage o=Orphans c=Cleanup q=Quit")

        pkgs = state.graph.packages
        total_bytes = sum((p["installed_size"] or 0) for p in pkgs.values())
        cache_bytes = sum((c.get("size") or 0) for c in ctx.caches)
        explicit = {n for n in pkgs if n in state.explicit}
        essential = state.essential
        orphan_names = _orphan_names(state)
        orphan_bytes = sum((state.graph.packages[n]["installed_size"] or 0)
                           for n in orphan_names if n in state.graph.packages)
        shared_count = sum(
            1 for n in pkgs
            if state.classification(n)[0] == "SHARED")
        single_count = sum(
            1 for n in pkgs
            if state.classification(n)[0] == "SINGLE-USE")
        cleanup_bytes = cache_bytes + orphan_bytes

        rows = [
            ("packages", len(pkgs)),
            ("installed size", format_size(total_bytes)),
            ("caches (detectable)", format_size(cache_bytes)),
            ("explicit", len(explicit)),
            ("essential", len(essential)),
            ("shared deps", shared_count),
            ("single-use", single_count),
            ("orphans", f"{len(orphan_names)} ({format_size(orphan_bytes)})"),
            ("potential cleanup", format_size(cleanup_bytes)),
            ("backend", state.backend_name),
        ]
        table = _stat_table(ctx, rows)
        return _screen(ctx, self.title, table,
                       "p=Packages d=Shared-deps s=Storage o=Orphans "
                       "c=Cleanup /=Search q=Quit")

    def handle(self, key, ctx):
        if key == "p":
            return ("push", PackageListScreen(mode="all"))
        if key == "d":
            return ("push", PackageListScreen(mode="shared"))
        if key == "s":
            return ("push", StorageScreen())
        if key == "o":
            return ("push", OrphansScreen())
        if key == "c":
            return ("push", CleanupScreen())
        if key == "/":
            return ("push", PackageListScreen(mode="all", search_prompt=True))
        return ("stay",)


class PackageListScreen(Screen):
    title = "Packages"

    def __init__(self, mode="all", search_prompt=False):
        self.mode = mode
        self.sel = 0
        self.search = ""
        self.searching = bool(search_prompt)
        self.cls_filter = None  # None=all, "candidates", or class string
        self.items = []

    def _build(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return []
        recs = _all_pkg_records(state)
        if self.mode == "shared":
            recs = [r for r in recs if r["cls"] == "SHARED"]
            recs.sort(key=lambda r: (-r["cnt"], -(r["size"] or 0)))
        else:
            recs.sort(key=lambda r: -(r["size"] or 0))
        if self.cls_filter == "candidates":
            recs = [r for r in recs if r["cls"] in ("ORPHAN", "SINGLE-USE")]
        elif self.cls_filter:
            recs = [r for r in recs if r["cls"] == self.cls_filter]
        if self.search:
            s = self.search.lower()
            recs = [r for r in recs if s in r["name"].lower()]
        self.items = recs
        return recs

    def render(self, ctx: Context):
        recs = self._build(ctx)
        body = self._render_table(ctx, recs)
        help_line = (
            "ARROWS move  +/- filter: all/candidates  "
            "e/s/o/1/x class  / search  Enter info  i info  q back"
        )
        if self.searching:
            help_line = (
                f"search: {self.search!r}  "
                "backspace to edit, enter to apply, esc to cancel"
            )
        return _screen(ctx, self.title, body, help_line)

    def _render_table(self, ctx, recs):
        t = Table.grid(expand=True)
        t.add_column(justify="left", width=2)
        t.add_column(justify="left", width=24)
        t.add_column(justify="left", width=18)
        t.add_column(justify="left", width=16)
        t.add_column(justify="right", width=11)
        t.add_column(justify="right", width=5)
        win, start, end = _paged(recs, self.sel, ctx.console)
        if not recs:
            return Text("(no packages match)")
        t.add_row(Text(""), Text("Package", style="help"),
                  Text("Version", style="help"),
                  Text("Type", style="help"),
                  Text("Size", style="help"), Text("Rd", style="help"))
        for i, r in enumerate(win):
            idx = start + i
            mark = "▶" if idx == self.sel else " "
            style = "selected" if idx == self.sel else _cls_style(r["cls"])
            t.add_row(
                Text(mark, style=style),
                Text(r["name"], style=style),
                Text(str(r["version"] or ""), style="help"),
                Text(_cls_short(r["cls"], r["cnt"]), style=style),
                Text(format_size(r["size"] or 0), style="help"),
                Text(str(r["cnt"]), style="help"),
            )
        return t

    def handle(self, key, ctx):
        recs = self._build(ctx)
        if not recs:
            if key == "q":
                return ("pop",)
            return ("stay",)
        if self.searching:
            if key in ("esc", "esc"):
                self.searching = False
                return ("stay",)
            if key == "enter":
                self.searching = False
                self.sel = 0
                return ("stay",)
            if key == "backspace":
                self.search = self.search[:-1]
                return ("stay",)
            if len(key) == 1 and key.isprintable():
                self.search += key
                return ("stay",)
            return ("stay",)
        if key == "down" or key == "j":
            self.sel = min(len(recs) - 1, self.sel + 1)
            return ("stay",)
        if key == "up" or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == "pageup":
            self.sel = max(0, self.sel - 10)
            return ("stay",)
        if key == "pagedown":
            self.sel = min(len(recs) - 1, self.sel + 10)
            return ("stay",)
        if key == "enter":
            return ("push", PackageInfoScreen(recs[self.sel]["name"]))
        if key == "i":
            return ("push", PackageInfoScreen(recs[self.sel]["name"]))
        if key == "/":
            self.searching = True
            self.search = ""
            return ("stay",)
        if key == "+":
            self.cls_filter = None
            return ("stay",)
        if key == "-":
            self.cls_filter = "candidates"
            return ("stay",)
        for letter, cls in (("e", "EXPLICIT"), ("s", "SHARED"),
                            ("o", "ORPHAN"), ("1", "SINGLE-USE"),
                            ("x", None)):
            if key == letter:
                self.cls_filter = cls
                return ("stay",)
        if key == "q":
            return ("pop",)
        return ("stay",)


def _cls_short(cls, cnt):
    if cls == "SHARED":
        return f"SHARED×{cnt}"
    if cls == "ESSENTIAL":
        return "E"
    if cls == "EXPLICIT":
        return "E"
    if cls == "ORPHAN":
        return "O"
    if cls == "SINGLE-USE":
        return "S"
    return cls or ""


def _cls_long(cls, cnt):
    label = {
        "ESSENTIAL": "ESSENTIAL",
        "EXPLICIT": "EXPLICIT",
        "SHARED": f"SHARED ×{cnt}",
        "ORPHAN": "ORPHAN",
        "SINGLE-USE": "SINGLE-USE",
    }.get(cls, cls or "")
    return label


class PackageInfoScreen(Screen):
    title = "Package"

    def __init__(self, name):
        self.name = name
        self.sim_result = None
        self.largest = None

    def render(self, ctx: Context):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            body = Panel(Text(f"no info for {self.name}", style="red"),
                         border_style="border")
            return _screen(ctx, f"{self.title}: {self.name}", body, "q back")
        row = state.graph.packages[self.name]
        cls, cnt = state.classification(self.name)
        label = _cls_long(cls, cnt)
        deps = state.graph.direct_deps(self.name)
        rdeps = state.graph.direct_rdeps(self.name)
        dep_lines = []
        shared_deps = 0
        for d in deps:
            if d in state.graph.packages:
                dcls, dcnt = state.classification(d)
                dep_lines.append(f"  {d} — {_cls_short(dcls, dcnt)}")
                if dcls == "SHARED":
                    shared_deps += 1
        if not dep_lines:
            dep_lines = ["  (none)"]
        rdep_lines = []
        for r in rdeps:
            if r in state.graph.packages:
                rcls, rcnt = state.classification(r)
                rdep_lines.append(f"  {r} — {_cls_short(rcls, rcnt)}")
        if not rdep_lines:
            rdep_lines = ["  (none)"]

        db = ctx.ensure_db()
        fcount = None
        if db is not None:
            try:
                if not db.files_populated(self.name):
                    backend = detect_backend()
                    if backend:
                        file_list = backend.file_list(self.name)
                        db.populate_files(self.name, file_list)
                fcount = db.file_count(self.name)
            except Exception:
                fcount = None

        info_text = Text()
        info_text.append(f"Name: {self.name}", style="bold")
        info_text.append("\nVersion: " + str(row['version'] or ""))
        info_text.append("\nArchitecture: " + str(row['architecture'] or ""))
        info_text.append("\nSize: " + format_size(row['installed_size'] or 0))
        info_text.append("\nType: " + label)
        if fcount is not None:
            info_text.append("\nFiles: " + str(fcount))
        else:
            info_text.append("\nFiles: ?")
        info = Panel(info_text, title="Info", border_style="border")

        deps_panel = Panel(Text("\n".join(dep_lines)),
                           title=f"Deps ({len(deps)}, shared {shared_deps})",
                           border_style="border")
        rdeps_panel = Panel(Text("\n".join(rdep_lines)),
                            title=f"Reverse deps ({len(rdeps)})",
                            border_style="border")
        cols = Columns([deps_panel, rdeps_panel], equal=True)

        action_line = Text("  [Simulate removal] → press r")
        btns = Panel(action_line, title="Actions", border_style="border")

        if self.sim_result is not None:
            sim = self.sim_result
            sim_lines = [
                f"Requested: {', '.join(sim.requested) or '(none)'}",
                f"Cascade-removable: {len(sim.cascade_removable)} "
                f"({format_size(sim.package_recovery_bytes)})",
            ]
            if sim.broken:
                sim_lines.append("Broken:")
                sim_lines += [f"  {b}" for b in sim.broken]
            if sim.blocked_reasons:
                sim_lines += [f"blocked: {b}" for b in sim.blocked_reasons]
            sim_panel = Panel(Text("\n".join(sim_lines)), title="Simulation",
                              border_style="border")
            cols = Columns([sim_panel, rdeps_panel], equal=True)

        body = Table.grid(expand=True)
        body.add_column()
        body.add_row(info)
        body.add_row(Text(""))
        body.add_row(cols)
        body.add_row(btns)

        help_line = ("r=simulate removal  d=dep tree  R=rdep tree  "
                     "q=back")
        return _screen(ctx, f"{self.title}: {self.name}", body, help_line)

    def handle(self, key, ctx):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            if key == "q":
                return ("pop",)
            return ("stay",)
        if key == "q":
            return ("pop",)
        if key == "r":
            if self.sim_result is None:
                self.sim_result = simulate_removal(
                    state.graph, [self.name],
                    explicit_set=state.explicit,
                    essential_set=state.essential)
            else:
                self.sim_result = None
            return ("stay",)
        if key == "d":
            return ("push", TreeScreen(self.name, "deps"))
        if key == "R":
            return ("push", TreeScreen(self.name, "rdeps"))
        return ("stay",)


class TreeScreen(Screen):
    title = "Tree"

    def __init__(self, name, direction="deps", recursive=True):
        self.name = name
        self.direction = direction
        self.recursive = recursive
        self.depth = 0

    def render(self, ctx: Context):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            body = Panel(Text(f"no tree for {self.name}", style="red"),
                         border_style="border")
            return _screen(ctx, self.title, body, "q back r=recursive-toggle")
        tree = Tree(f"{self.name} [{_cls_short(state.classification(self.name)[0], 0)}]")
        visited = {self.name}

        def builder(node, pkg, depth):
            row = state.graph.packages.get(pkg)
            if not row:
                return
            cls, cnt = state.classification(pkg)
            children = (state.graph.direct_deps(pkg) if self.direction == "deps"
                        else state.graph.direct_rdeps(pkg))
            for c in children:
                if not self.recursive and depth >= 1:
                    continue
                if c not in state.graph.packages:
                    continue
                if c in visited:
                    node.add(Text(f"{c} (cycle)", style="dim"))
                    continue
                visited.add(c)
                label = (f"{c} {_cls_short(state.classification(c)[0], 0)} "
                         f"{format_size(state.graph.packages[c]['installed_size'] or 0)}")
                child_node = node.add(Text(label))
                if depth < 6:
                    builder(child_node, c, depth + 1)
                visited.discard(c)

        builder(tree, self.name, 0)
        body = Panel(tree, title=f"{'Dependencies' if self.direction=='deps' else 'Reverse deps'} of {self.name}",
                     border_style="border")
        help_line = ("q back  r=toggle recursive")
        return _screen(ctx, self.title, body, help_line)

    def handle(self, key, ctx):
        if key == "q":
            return ("pop",)
        if key == "r":
            self.recursive = not self.recursive
            return ("stay",)
        return ("stay",)


class StorageScreen(Screen):
    title = "Storage"

    def render(self, ctx: Context):
        state = ctx.ensure_state()
        caches = ctx.ensure_caches()
        body = Table.grid(expand=True)
        body.add_column()
        body.add_row(Text("Storage analysis", style="stat"))
        body.add_row(Text(""))
        rows = []
        if state:
            total = sum((p["installed_size"] or 0)
                        for p in state.graph.packages.values())
            rows.append(("installed packages", format_size(total)))
        else:
            rows.append(("installed packages", "unavailable"))
        cache_total = sum((c.get("size") or 0) for c in caches)
        rows.append(("detected caches", format_size(cache_total)))
        body.add_row(_stat_table(ctx, rows))
        body.add_row(Text(""))
        body.add_row(Text("Known caches:", style="help"))
        ct = Table.grid(expand=True)
        ct.add_column(justify="left", width=30)
        ct.add_column(justify="right", width=11)
        for c in caches[:15]:
            ct.add_row(Text(c["label"], style="orphan"),
                       Text(format_size(c.get("size") or 0), style="help"))
        if not caches:
            ct.add_row(Text("(none)"))
        body.add_row(Panel(ct, title="Caches", border_style="border"))
        home = os.path.expanduser("~")
        prefix = os.environ.get("PREFIX", "/usr")
        body.add_row(Text(f"$HOME = {home}", style="help"))
        body.add_row(Text(f"$PREFIX = {prefix}", style="help"))
        if ctx.home_largest is None:
            try:
                ctx.home_largest = largest_dirs(home, limit=15) or []
            except Exception as e:
                ctx.home_largest = []
                ctx.error = f"largest_dirs failed: {e}"
        ld = Panel(self._dir_table(ctx.home_largest), title="Largest dirs under $HOME",
                   border_style="border")
        body.add_row(ld)
        help_line = "q back  r=refresh"
        return _screen(ctx, self.title, body, help_line)

    def _dir_table(self, items):
        t = Table.grid(expand=True)
        t.add_column(justify="left", width=40)
        t.add_column(justify="right", width=11)
        for path, size in items[:15]:
            t.add_row(Text(path, style="dim"), Text(format_size(size), style="help"))
        if not items:
            t.add_row(Text("(none)"))
        return t

    def handle(self, key, ctx):
        if key == "q":
            return ("pop",)
        if key == "r":
            ctx.caches = None
            ctx.home_largest = None
            return ("stay",)
        return ("stay",)


class OrphansScreen(Screen):
    title = "Orphans"

    def __init__(self):
        self.sel = 0
        self.items = []

    def _build(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return []
        out = []
        for name in _orphan_names(state):
            row = state.graph.packages[name]
            out.append({"name": name,
                        "size": row["installed_size"] or 0})
        out.sort(key=lambda r: -(r["size"] or 0))
        self.items = out
        return out

    def render(self, ctx: Context):
        items = self._build(ctx)
        total = sum((i["size"] or 0) for i in items)
        t = Table.grid(expand=True)
        t.add_column(justify="left", width=26)
        t.add_column(justify="right", width=11)
        win, start, end = _paged(items, self.sel, ctx.console, _noop)
        for i, it in enumerate(win):
            idx = start + i
            sel = idx == self.sel
            style = "selected" if sel else "orphan"
            mark = "▶" if sel else " "
            t.add_row(Text(f"{mark} {it['name']}", style=style),
                      Text(format_size(it["size"] or 0), style=style))
        if not items:
            t.add_row(Text("(no orphans)"))
        body = t
        foot = Panel(Text(f"Total orphans: {len(items)}  "
                          f"size: {format_size(total)}"),
                     border_style="border")
        help_line = "ARROWS move  Enter=info  q back"
        content = Table.grid(expand=True)
        content.add_column()
        content.add_row(Panel(body, title="Orphan packages",
                              border_style="border"))
        content.add_row(foot)
        return _screen(ctx, self.title, content, help_line)

    def handle(self, key, ctx):
        items = self._build(ctx)
        if not items:
            if key == "q":
                return ("pop",)
            return ("stay",)
        if key == "down" or key == "j":
            self.sel = min(len(items) - 1, self.sel + 1)
        elif key == "up" or key == "k":
            self.sel = max(0, self.sel - 1)
        elif key == "enter":
            return ("push", PackageInfoScreen(items[self.sel]["name"]))
        elif key == "q":
            return ("pop",)
        return ("stay",)


class CleanupScreen(Screen):
    title = "Cleanup review"

    def __init__(self):
        self.sel = 0
        self.caches = []
        self.orphans = []
        self.selected = set()
        self.confirmed = False

    def _build(self, ctx):
        ctx.ensure_caches()
        self.caches = list(ctx.caches or [])
        state = ctx.ensure_state()
        if state:
            for name in _orphan_names(state):
                row = state.graph.packages[name]
                self.orphans.append({"name": name,
                                     "size": row["installed_size"] or 0})
        self.orphans.sort(key=lambda r: -(r["size"] or 0))

    def render(self, ctx: Context):
        self._build(ctx)
        rows = []
        for c in self.caches:
            rows.append({"kind": "cache", "label": c["label"],
                         "path": c["path"], "size": c.get("size") or 0})
        for o in self.orphans:
            rows.append({"kind": "orphan", "label": o["name"],
                         "path": o["name"], "size": o["size"]})
        rows.sort(key=lambda r: -(r["size"]))
        self._rows = rows
        t = Table.grid(expand=True)
        t.add_column(justify="left", width=3)
        t.add_column(justify="left", width=24)
        t.add_column(justify="left", width=30)
        t.add_column(justify="right", width=11)
        for i, r in enumerate(rows):
            sel = i == self.sel
            mark = "[▶]" if sel else "   "
            chk = "x" if i in self.selected else " "
            style = "selected" if sel else _cls_style(
                "orphan" if r["kind"] == "orphan" else "SHARED")
            t.add_row(Text(mark, style=style),
                      Text(f"[{chk}]", style=style),
                      Text(r["label"], style=style),
                      Text(format_size(r["size"]), style="help"))
        if not rows:
            t.add_row(Text(""), Text("(nothing to clean)"))
        total = sum(r["size"] for r in rows)
        sel_total = sum(r["size"] for i, r in enumerate(rows)
                        if i in self.selected)
        body = Panel(t, title="Cleanup candidates (space=select)",
                     border_style="border")
        summary = Panel(Text(
            f"all: {len(rows)}  selected: {len(self.selected)}  "
            f"selected bytes: {format_size(sel_total)}  "
            f"total bytes: {format_size(total)}"),
            title="Summary", border_style="border")
        content = Table.grid(expand=True)
        content.add_column()
        content.add_row(body)
        content.add_row(Text(""))
        content.add_row(summary)
        action = ("clean selected → press y to confirm, n to cancel"
                  if self.selected else "(select rows with space)")
        help_line = (f"{action}  ARROWS  space=toggle  "
                     "y=run cleanup  n=cancel  q=back")
        return _screen(ctx, self.title, content, help_line)

    def handle(self, key, ctx):
        rows = self._rows
        if not rows:
            if key == "q":
                return ("pop",)
            return ("stay",)
        if key == "down" or key == "j":
            self.sel = min(len(rows) - 1, self.sel + 1)
            return ("stay",)
        if key == "up" or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == " ":
            if self.sel in self.selected:
                self.selected.discard(self.sel)
            else:
                self.selected.add(self.sel)
            return ("stay",)
        if key == "y":
            self._do_cleanup(ctx, rows)
            return ("stay",)
        if key == "n":
            self.selected = set()
            return ("stay",)
        if key == "q":
            return ("pop",)
        return ("stay",)

    def _do_cleanup(self, ctx, rows):
        removed = 0
        errors = []
        for i in self.selected:
            r = rows[i]
            if r["kind"] == "cache":
                p = r["path"]
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=False)
                        removed += 1
                    elif os.path.isfile(p):
                        os.remove(p)
                        removed += 1
                except OSError as e:
                    errors.append(f"{p}: {e}")
        try:
            import subprocess
            subprocess.run(["apt-get", "clean"], capture_output=True,
                           timeout=120)
        except (OSError, Exception) as e:
            errors.append(f"apt-get clean: {e}")
        ctx.caches = None
        if errors:
            ctx.status = f"cleanup: {removed} removed; errors: {errors[:3]}"
        else:
            ctx.status = f"cleanup: {removed} caches removed (apt clean ran)"
        self.selected = set()


def _noop(*a, **k):
    pass


# --------------------------------------------------------------------------
# App / event loop
# --------------------------------------------------------------------------
class _App:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.stack = [DashboardScreen()]
        self.last_size = None

    @property
    def screen(self):
        return self.stack[-1]

    def run(self):
        console = self.ctx.console
        fd = sys.stdin.fileno()
        with _raw_terminal():
            try:
                with Live(self.screen.render(self.ctx), console=console,
                          screen=True, refresh_per_second=8,
                          transient=False, auto_refresh=False) as live:
                    while True:
                        size = (console.width, console.height)
                        dirty = self.last_size != size
                        self.last_size = size
                        key = _read_key(fd)
                        if key:
                            dirty = True
                        action = self._dispatch(key)
                        if action[0] == "quit":
                            self.ctx.status = ""
                            return
                        if key:
                            self.ctx.status = ""
                        if dirty:
                            live.update(self.screen.render(self.ctx))
                        else:
                            if key is None:
                                time.sleep(0.01)
                            else:
                                live.update(self.screen.render(self.ctx))
            except KeyboardInterrupt:
                self.ctx.status = ""
                return
            except Exception as e:
                try:
                    console.print(f"[red]TUI error: {e}[/]")
                except Exception:
                    pass
                return

    def _dispatch(self, key):
        if key == "ctrl-c" or key == "ctrl-d":
            return ("quit",)
        if key == "q":
            if len(self.stack) > 1:
                self.stack.pop()
                return ("stay",)
            return ("quit",)
        if key is None:
            w, h = self.ctx.console.size
            if self.last_size != (w, h):
                self.last_size = (w, h)
                return ("stay",)
            return ("stay",)
        return self.screen.handle(key, self.ctx)


def _smoke_render(console: Console, ctx: Context):
    """Render the dashboard once when there is no TTY (headless / test)."""
    screen = DashboardScreen()
    try:
        console.print(screen.render(ctx))
    except Exception:
        console.print("[red]tpm TUI: unable to render in non-interactive mode[/]")


def run(no_color: bool = False, db=None, console: Console = None):
    """Entry point: build and run the interactive TUI."""
    console = console or _make_console(no_color)
    ctx = Context(console=console, no_color=no_color, db=db)
    ctx.ensure_state()
    if not console.is_terminal:
        _smoke_render(console, ctx)
        return
    _App(ctx).run()


if __name__ == "__main__":
    run()
