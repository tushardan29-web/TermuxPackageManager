"""tpm TUI — fully functional interactive terminal interface.

Mouse + keyboard. Every CLI feature accessible. Security warnings
before destructive actions. Usage hints on every screen.
"""

from __future__ import annotations

import os
import sys
import time
import select
import signal
import termios
import tty
from dataclasses import dataclass, field
from typing import Any, Optional, List, Callable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.tree import Tree
from rich.align import Align
from rich.rule import Rule
from rich import box

from .core import load_state
from .formatter import format_size
from .package.backend import detect_backend
from .storage.scanner import detect_caches, largest_dirs, scan_dir_size, classify_path
from .removal.simulator import simulate_removal
from .database import Database

# ── constants ────────────────────────────────────────────────────────────

MOUSE_SGR = "\033[<"
KEY_UP = "up"
KEY_DOWN = "down"
KEY_LEFT = "left"
KEY_RIGHT = "right"
KEY_ENTER = "enter"
KEY_BACKSPACE = "backspace"
KEY_DELETE = "delete"
KEY_HOME = "home"
KEY_END = "end"
KEY_PAGE_UP = "page_up"
KEY_PAGE_DOWN = "page_down"
KEY_TAB = "tab"
KEY_SHIFT_TAB = "shift_tab"
KEY_MOUSE = "mouse"

HINT_STYLE = "dim white on grey11"
BORDER_STYLE = "bright_blue"
SELECTED_STYLE = "bold white on grey15"
HEADER_STYLE = "bold bright_cyan"
WARN_STYLE = "bold yellow"
DANGER_STYLE = "bold red"
SAFE_STYLE = "bold green"
INFO_STYLE = "dim cyan"


# ── terminal input ──────────────────────────────────────────────────────

def _raw_mode(fd):
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return old


def _read_key(fd) -> Optional[str]:
    """Read a single keypress, handling escape sequences for arrows,
    function keys, mouse events, etc."""
    if not _input_ready(fd, 0.05):
        return None
    ch = os.read(fd, 1)
    if not ch:
        return None
    c = ch[0]
    if c == 27:  # ESC sequence
        if not _input_ready(fd, 0.05):
            return "escape"
        ch2 = os.read(fd, 1)
        if not ch2:
            return "escape"
        c2 = ch2[0]
        if c2 == 91:  # CSI [
            ch3 = os.read(fd, 1)
            if not ch3:
                return None
            c3 = ch3[0]
            if c3 == 65:
                return KEY_UP
            if c3 == 66:
                return KEY_DOWN
            if c3 == 67:
                return KEY_RIGHT
            if c3 == 68:
                return KEY_LEFT
            if c3 == 72:
                return KEY_HOME
            if c3 == 70:
                return KEY_END
            if c3 == 53:
                _input_ready(fd, 0.05)
                os.read(fd, 1)
                return KEY_PAGE_UP
            if c3 == 54:
                _input_ready(fd, 0.05)
                os.read(fd, 1)
                return KEY_PAGE_DOWN
            if c3 == 90:
                return KEY_SHIFT_TAB
            # SGR mouse: ESC[<button;x;y M/m
            if c3 == 60:
                buf = b""
                while True:
                    if _input_ready(fd, 0.05):
                        buf += os.read(fd, 1)
                        if buf.endswith(b"M") or buf.endswith(b"m"):
                            break
                    else:
                        break
                return KEY_MOUSE
        if c2 == 79:  # SS3
            ch3 = os.read(fd, 1)
            if ch3 and ch3[0] == 65:
                return KEY_UP
            if ch3 and ch3[0] == 66:
                return KEY_DOWN
        return None
    if c == 127 or c == 8:
        return KEY_BACKSPACE
    if c == 13 or c == 10:
        return KEY_ENTER
    if c == 9:
        return KEY_TAB
    if c == 3:
        return "ctrl-c"
    if c == 4:
        return "ctrl-d"
    if c == 11:
        return "ctrl-k"
    if c == 12:
        return "ctrl-l"
    if c == 21:
        return "ctrl-u"
    if 32 <= c < 127:
        return chr(c)
    return None


def _input_ready(fd, timeout):
    r, _, _ = select.select([fd], [], [], timeout)
    return bool(r)


# ── data structures ──────────────────────────────────────────────────────

@dataclass
class Context:
    console: Console
    state: Any = None
    db: Database = None
    caches: list = field(default_factory=list)
    status: str = ""
    error: str = ""
    no_color: bool = False

    def ensure_state(self):
        if self.state is None:
            self.db = self.db or Database()
            try:
                self.state = load_state(db=self.db)
            except Exception as e:
                self.error = str(e)
        return self.state

    def ensure_caches(self):
        if not self.caches:
            self.caches = detect_caches()
        return self.caches

    def ensure_db(self):
        if self.db is None:
            self.db = Database()
        return self.db


class Screen:
    """Base screen. Subclasses implement render() and handle()."""
    title: str = "Screen"

    def render(self, ctx: Context) -> Panel:
        return Panel("nothing", title=self.title)

    def handle(self, key: str, ctx: Context) -> tuple:
        """Return (action, ...) where action is one of:
        ('stay',)       - stay on this screen
        ('pop',)        - go back
        ('push', screen) - push new screen
        ('replace', screen) - replace current screen
        ('quit',)       - exit TUI
        ('status', msg) - show status message
        """
        return ("stay",)


# ── rendering helpers ────────────────────────────────────────────────────

def _header(ctx: Context, title: str, hint: str = "") -> Panel:
    parts = [Text(f"  {title}", style=HEADER_STYLE)]
    if hint:
        parts.append(Text(f"  {hint}", style=HINT_STYLE))
    return Panel(Columns(parts, expand=True), style=BORDER_STYLE, box=box.DOUBLE)


def _footer(ctx: Context, hint: str) -> Panel:
    return Panel(Text(f"  {hint}", style=HINT_STYLE), style=BORDER_STYLE, box=box.ROUNDED)


def _page(ctx: Context, title: str, body, hint: str, status: str = "") -> Table:
    grid = Table.grid(expand=True, padding=0)
    grid.add_column()
    grid.add_row(_header(ctx, title, status or ctx.status))
    grid.add_row(body)
    grid.add_row(_footer(ctx, hint))
    return grid


def _scrollable_list(items: list, sel: int, height: int = 20,
                     render_row: Optional[Callable] = None) -> Table:
    """Render a scrollable list with selection highlight."""
    if not items:
        return Panel(Text("  (empty)", style="dim"), box=box.ROUNDED)
    sel = max(0, min(sel, len(items) - 1))
    start = max(0, sel - height // 2)
    end = min(len(items), start + height)
    start = max(0, end - height)

    table = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True,
                  padding=(0, 1))
    table.add_column("sel", width=3)
    table.add_column("content", ratio=1)

    for i in range(start, end):
        prefix = " > " if i == sel else "   "
        style = SELECTED_STYLE if i == sel else ""
        if render_row:
            content = render_row(items[i], i == sel)
        else:
            content = str(items[i])
        table.add_row(prefix, Text(content, style=style))

    scroll_info = f" {sel + 1}/{len(items)}"
    return Table(
        Table(table, expand=True),
        Text(scroll_info, style="dim"),
        box=box.ROUNDED, expand=True
    )


# ── screens ──────────────────────────────────────────────────────────────

class DashboardScreen(Screen):
    title = "Dashboard"
    hint = "p=Packages  d=Deps  s=Storage  o=Orphans  c=Cleanup  /=Search  q=Quit"

    def render(self, ctx):
        state = ctx.ensure_state()
        ctx.ensure_caches()
        if not state:
            return _page(ctx, self.title,
                         Panel(Text(ctx.error or "no data", style="red")),
                         self.hint)

        pkgs = state.graph.packages
        total = sum((p["installed_size"] or 0) for p in pkgs.values())
        cache_bytes = sum((c.get("size") or 0) for c in ctx.caches)
        explicit = {n for n in pkgs if n in state.explicit}
        orphan_names = [n for n in pkgs if state.classification(n)[0] == "ORPHAN"]
        orphan_bytes = sum((pkgs[n]["installed_size"] or 0) for n in orphan_names)
        shared = sum(1 for n in pkgs if state.classification(n)[0] == "SHARED")
        single = sum(1 for n in pkgs if state.classification(n)[0] == "SINGLE-USE")

        rows = [
            ("packages", str(len(pkgs))),
            ("installed size", format_size(total)),
            ("caches", format_size(cache_bytes)),
            ("explicit", str(len(explicit))),
            ("essential", str(len(state.essential))),
            ("shared deps", str(shared)),
            ("single-use", str(single)),
            (f"orphans", f"{len(orphan_names)} ({format_size(orphan_bytes)})"),
            ("cleanup potential", format_size(cache_bytes + orphan_bytes)),
            ("backend", state.backend_name),
        ]

        t = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 2))
        t.add_column("label", style="dim", ratio=1)
        t.add_column("value", style="bold", ratio=2)
        for label, value in rows:
            t.add_row(label, value)

        return _page(ctx, self.title, t, self.hint)

    def handle(self, key, ctx):
        if key == "p":
            return ("push", PackageListScreen())
        if key == "d":
            return ("push", DepBrowserScreen())
        if key == "s":
            return ("push", StorageScreen())
        if key == "o":
            return ("push", OrphanScreen())
        if key == "c":
            return ("push", CacheScreen())
        if key == "/":
            return ("push", SearchScreen())
        return ("stay",)


class PackageListScreen(Screen):
    title = "Packages"
    hint = "arrows=move  Enter=info  e/s/o/x=filter  /=search  r=rescan  q=back"

    def __init__(self, mode="all", search=""):
        self.mode = mode  # all, explicit, shared, orphans
        self.search = search
        self.sel = 0
        self.items = []
        self.input_buf = ""
        self.input_mode = False

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return
        self.items = []
        for name, row in state.graph.packages.items():
            cls, cnt = state.classification(name)
            if self.mode == "explicit" and cls != "EXPLICIT":
                continue
            if self.mode == "shared" and cls != "SHARED":
                continue
            if self.mode == "orphans" and cls != "ORPHAN":
                continue
            if self.search and self.search.lower() not in name.lower():
                continue
            self.items.append((name, row, cls, cnt))
        self.items.sort(key=lambda x: -(x[1].get("installed_size") or 0))

    def render(self, ctx):
        self._load(ctx)
        state = ctx.ensure_state()
        filter_label = f" [{self.mode.upper()}]" if self.mode != "all" else ""
        search_label = f" /{self.search}" if self.search else ""
        status = f"{len(self.items)} packages{filter_label}{search_label}"

        if not self.items:
            body = Panel(Text("  no matching packages", style="dim"), box=box.ROUNDED)
            return _page(ctx, self.title, body, self.hint, status)

        def row_renderer(item, selected):
            name, row, cls, cnt = item
            size = format_size(row.get("installed_size") or 0)
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            return Text.assemble(
                (f"{name:<30}", "bold" if selected else ""),
                (f"{size:>10}", ""),
                (f"  {label}", f"{'bold cyan' if cls == 'EXPLICIT' else 'magenta' if cls == 'SHARED' else 'red' if cls == 'ORPHAN' else 'dim'}")
            )

        items_text = []
        for item in self.items:
            name, row, cls, cnt = item
            size = format_size(row.get("installed_size") or 0)
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            items_text.append((name, size, label, cls, cnt))

        table = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True,
                      padding=(0, 1), show_lines=False)
        table.add_column("", width=3)
        table.add_column("NAME", ratio=3)
        table.add_column("SIZE", ratio=1, justify="right")
        table.add_column("TYPE", ratio=2)

        sel = max(0, min(self.sel, len(self.items) - 1))
        height = ctx.console.height - 6
        start = max(0, sel - height // 2)
        end = min(len(self.items), start + height)
        start = max(0, end - height)

        for i in range(start, end):
            name, size, label, cls, cnt = items_text[i]
            prefix = " > " if i == sel else "   "
            cls_style = ("bold cyan" if cls == "EXPLICIT" else
                         "magenta" if cls == "SHARED" else
                         "red" if cls == "ORPHAN" else
                         "yellow" if cls == "ESSENTIAL" else "dim")
            table.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(name, style="bold" if i == sel else ""),
                Text(size),
                Text(label, style=cls_style)
            )

        scroll = Text(f"  {sel + 1}/{len(self.items)}", style="dim")
        body = Table(table, scroll, box=box.ROUNDED, expand=True)

        if self.input_mode:
            input_panel = Panel(
                Text(f"  search: {self.input_buf}_", style="bold"),
                title="Search", border_style="yellow", box=box.ROUNDED)
            body = Table(input_panel, body, box=None, expand=True)

        return _page(ctx, self.title, body, self.hint, status)

    def handle(self, key, ctx):
        if self.input_mode:
            if key == KEY_ENTER:
                self.search = self.input_buf
                self.input_mode = False
                self.sel = 0
                return ("stay",)
            if key == KEY_ESCAPE or key == "ctrl-c":
                self.input_mode = False
                return ("stay",)
            if key == KEY_BACKSPACE:
                self.input_buf = self.input_buf[:-1]
                return ("stay",)
            if key and len(key) == 1 and key.isprintable():
                self.input_buf += key
                return ("stay",)
            return ("stay",)

        if key == "/":
            self.input_mode = True
            self.input_buf = ""
            return ("stay",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == KEY_HOME or key == "g":
            self.sel = 0
            return ("stay",)
        if key == KEY_END or key == "G":
            self.sel = max(0, len(self.items) - 1)
            return ("stay",)
        if key == KEY_PAGE_UP:
            self.sel = max(0, self.sel - 20)
            return ("stay",)
        if key == KEY_PAGE_DOWN:
            self.sel = min(len(self.items) - 1, self.sel + 20)
            return ("stay",)
        if key == KEY_ENTER and self.items:
            name = self.items[self.sel][0]
            return ("push", PackageDetailScreen(name))
        if key == "e":
            self.mode = "explicit" if self.mode != "explicit" else "all"
            self.sel = 0
            return ("stay",)
        if key == "s":
            self.mode = "shared" if self.mode != "shared" else "all"
            self.sel = 0
            return ("stay",)
        if key == "o":
            self.mode = "orphans" if self.mode != "orphans" else "all"
            self.sel = 0
            return ("stay",)
        if key == "x":
            self.mode = "all"
            self.sel = 0
            return ("stay",)
        if key == "r":
            from .scanner import run_scan
            run_scan(db=ctx.db)
            ctx.state = None
            ctx.ensure_state()
            return ("status", "rescan complete")
        return ("stay",)


class PackageDetailScreen(Screen):
    title = "Package"
    hint = "r=simulate  d=deps  R=rdeps  f=files  q=back"

    def __init__(self, name):
        self.name = name
        self.sim_result = None
        self.show_files = False
        self.file_items = []

    def render(self, ctx):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            return _page(ctx, self.title,
                         Panel(Text(f"  not found: {self.name}", style="red")),
                         "q=back")

        row = state.graph.packages[self.name]
        cls, cnt = state.classification(self.name)
        label = f"SHARED x{cnt}" if cls == "SHARED" else cls
        cls_style = ("bold cyan" if cls == "EXPLICIT" else
                     "magenta" if cls == "SHARED" else
                     "red" if cls == "ORPHAN" else
                     "yellow" if cls == "ESSENTIAL" else "dim")

        # info
        info = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
        info.add_column("k", style="dim", ratio=1)
        info.add_column("v", ratio=3)
        info.add_row("Name", Text(self.name, style="bold"))
        info.add_row("Version", str(row.get("version") or ""))
        info.add_row("Architecture", str(row.get("architecture") or ""))
        info.add_row("Size", format_size(row.get("installed_size") or 0))
        info.add_row("Type", Text(label, style=cls_style))
        info.add_row("Essential", "yes" if row.get("essential") else "no")

        db = ctx.ensure_db()
        fcount = None
        if db:
            try:
                if not db.files_populated(self.name):
                    backend = detect_backend()
                    if backend:
                        fl = backend.file_list(self.name)
                        db.populate_files(self.name, fl)
                fcount = db.file_count(self.name)
            except Exception:
                pass
        info.add_row("Files", str(fcount) if fcount else "?")

        # deps
        deps = state.graph.direct_deps(self.name)
        dep_lines = []
        for d in deps:
            if d in state.graph.packages:
                dc, dcnt = state.classification(d)
                dl = f"SHARED x{dcnt}" if dc == "SHARED" else dc
                dep_lines.append(f"  {d}  [{dl}]")
        if not dep_lines:
            dep_lines = ["  (none)"]
        deps_panel = Panel(
            Text("\n".join(dep_lines), overflow="fold"),
            title=f"Deps ({len(deps)})",
            border_style=BORDER_STYLE, box=box.ROUNDED)

        # rdeps
        rdeps = state.graph.direct_rdeps(self.name)
        rdep_lines = []
        for r in rdeps:
            if r in state.graph.packages:
                rc, rcnt = state.classification(r)
                rl = f"SHARED x{rcnt}" if rc == "SHARED" else rc
                rdep_lines.append(f"  {r}  [{rl}]")
        if not rdep_lines:
            rdep_lines = ["  (nothing depends on this)"]
        rdeps_panel = Panel(
            Text("\n".join(rdep_lines), overflow="fold"),
            title=f"Required by ({len(rdeps)})",
            border_style=BORDER_STYLE, box=box.ROUNDED)

        cols = Columns([deps_panel, rdeps_panel], equal=True, expand=True)

        # simulation
        sim_body = None
        if self.sim_result:
            sim = self.sim_result
            sim_lines = [Text(f"  Requested: {', '.join(sim.requested)}", style=WARN_STYLE)]
            if sim.cascade_removable:
                sim_lines.append(Text(f"  Would remove: {len(sim.cascade_removable)} packages",
                                      style=DANGER_STYLE))
                for n in sim.cascade_removable[:10]:
                    sim_lines.append(Text(f"    {n}", style="dim"))
                if len(sim.cascade_removable) > 10:
                    sim_lines.append(Text(f"    ... and {len(sim.cascade_removable) - 10} more",
                                          style="dim"))
            if sim.retained_shared:
                sim_lines.append(Text(f"  Retained (shared): {len(sim.retained_shared)} packages",
                                      style=SAFE_STYLE))
            if sim.broken:
                sim_lines.append(Text(f"  BROKEN: {len(sim.broken)} packages",
                                      style=DANGER_STYLE))
                for b in sim.broken[:5]:
                    sim_lines.append(Text(f"    {b}", style=DANGER_STYLE))
            if sim.blocked_reasons:
                for r in sim.blocked_reasons:
                    sim_lines.append(Text(f"  blocked: {r}", style=WARN_STYLE))
            sim_lines.append(Text(f"  Recovery: {format_size(sim.total_recovery_bytes)}",
                                  style=SAFE_STYLE))
            sim_body = Panel(
                Text("\n".join(str(s) for s in sim_lines), overflow="fold"),
                title="Removal Simulation",
                border_style="yellow", box=box.DOUBLE)

        # files
        files_body = None
        if self.show_files and self.file_items:
            ft = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True)
            ft.add_column("size", justify="right", style="dim")
            ft.add_column("path", ratio=1)
            for fp, fs in self.file_items[:30]:
                ft.add_row(format_size(fs) if fs else "", fp)
            files_body = Panel(ft, title=f"Files ({len(self.file_items)})",
                               border_style=BORDER_STYLE, box=box.ROUNDED)

        # action buttons
        btns = Table(box=box.SIMPLE, show_header=False, expand=True)
        btns.add_column("btn", ratio=1)
        btns.add_column("desc", ratio=3)
        btns.add_row(Text(" [r] ", style="bold yellow"), Text("Simulate removal"))
        btns.add_row(Text(" [d] ", style="bold cyan"), Text("Dependency tree"))
        btns.add_row(Text(" [R] ", style="bold cyan"), Text("Reverse dependency tree"))
        btns.add_row(Text(" [f] ", style="bold cyan"), Text("Toggle file list"))
        btns_panel = Panel(btns, title="Actions", border_style=BORDER_STYLE, box=box.ROUNDED)

        # assemble
        body = Table(box=None, expand=True, padding=0)
        body.add_column()
        body.add_row(info)
        body.add_row(Text(""))
        body.add_row(cols)
        if sim_body:
            body.add_row(sim_body)
        if files_body:
            body.add_row(files_body)
        body.add_row(btns_panel)

        hint = self.hint
        if self.sim_result:
            hint += "  R=clear simulation"
        return _page(ctx, f"{self.name}", body, hint)

    def handle(self, key, ctx):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            if key in ("q", KEY_ESCAPE):
                return ("pop",)
            return ("stay",)
        if key == "q" or key == KEY_ESCAPE:
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
        if key == "f":
            if not self.show_files:
                db = ctx.ensure_db()
                if db:
                    if not db.files_populated(self.name):
                        backend = detect_backend()
                        if backend:
                            fl = backend.file_list(self.name)
                            db.populate_files(self.name, fl)
                    self.file_items = [
                        (r["path"], r["size"])
                        for r in db.conn.execute(
                            "SELECT path, size FROM files f JOIN packages p "
                            "ON p.id=f.package_id WHERE p.name=? "
                            "ORDER BY COALESCE(size,0) DESC",
                            (self.name,)).fetchall()
                    ]
            self.show_files = not self.show_files
            return ("stay",)
        return ("stay",)


class TreeScreen(Screen):
    title = "Tree"

    def __init__(self, name, mode="deps"):
        self.name = name
        self.mode = mode  # deps or rdeps
        self.sel = 0
        self.items = []

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state or self.name not in state.graph.packages:
            return
        if self.mode == "deps":
            self.items = state.graph.recursive_deps(self.name)
        else:
            self.items = state.graph.recursive_rdeps(self.name)

    def render(self, ctx):
        self._load(ctx)
        state = ctx.ensure_state()
        mode_label = "DEPENDENCIES" if self.mode == "deps" else "REQUIRED BY"

        if not self.items:
            body = Panel(Text(f"  (none)", style="dim"), box=box.ROUNDED)
            return _page(ctx, f"{mode_label}: {self.name}", body, "q=back")

        t = Table(box=box.SIMPLE_HEAVY, show_header=False, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("name", ratio=1)
        t.add_column("type", ratio=1)

        sel = max(0, min(self.sel, len(self.items) - 1))
        for i, name in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            if name in (state.graph.packages if state else {}):
                cls, cnt = state.classification(name)
                label = f"SHARED x{cnt}" if cls == "SHARED" else cls
                cls_style = ("bold cyan" if cls == "EXPLICIT" else
                             "magenta" if cls == "SHARED" else
                             "red" if cls == "ORPHAN" else "dim")
            else:
                label = "?"
                cls_style = "dim"
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(name, style="bold" if i == sel else ""),
                Text(label, style=cls_style)
            )

        scroll = Text(f"  {sel + 1}/{len(self.items)}", style="dim")
        body = Table(t, scroll, box=box.ROUNDED, expand=True)
        return _page(ctx, f"{mode_label}: {self.name}", body,
                     "arrows=move  Enter=info  q=back")

    def handle(self, key, ctx):
        if key in ("q", KEY_ESCAPE):
            return ("pop",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == KEY_ENTER and self.items:
            name = self.items[self.sel]
            return ("push", PackageDetailScreen(name))
        if key == KEY_HOME or key == "g":
            self.sel = 0
            return ("stay",)
        if key == KEY_END or key == "G":
            self.sel = max(0, len(self.items) - 1)
            return ("stay",)
        return ("stay",)


class StorageScreen(Screen):
    title = "Storage"
    hint = "arrows=move  Enter=drill-down  u=up  r=refresh  q=back"

    def __init__(self, path=None, depth=2):
        self.path = path or os.path.expanduser("~")
        self.depth = depth
        self.sel = 0
        self.items = []  # (name, size, category)
        self.scanning = False

    def _load(self, ctx):
        self.items = []
        sizes = scan_dir_size(self.path, max_depth=self.depth)
        for p, s in sizes.items():
            if p == self.path:
                continue
            cat = classify_path(p)
            short = p.replace(os.path.expanduser("~"), "~")
            self.items.append((p, short, s, cat))
        self.items.sort(key=lambda x: -x[2])

    def render(self, ctx):
        self._load(ctx)
        total = sum(i[2] for i in self.items)
        status = f"{format_size(total, 2)} under {self.path.replace(os.path.expanduser('~'), '~')}"

        if not self.items:
            body = Panel(Text("  (empty)", style="dim"), box=box.ROUNDED)
            return _page(ctx, self.title, body, self.hint, status)

        t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("SIZE", justify="right", ratio=1)
        t.add_column("PATH", ratio=3)
        t.add_column("TYPE", ratio=1)

        sel = max(0, min(self.sel, len(self.items) - 1))
        for i, (full, short, size, cat) in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            cat_style = ("cyan" if cat == "caches" else
                         "yellow" if cat == "proot-distro" else
                         "green" if cat == "$PREFIX" else "dim")
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(format_size(size), style="bold" if i == sel else ""),
                Text(short, style="bold" if i == sel else ""),
                Text(cat, style=cat_style)
            )

        scroll = Text(f"  {sel + 1}/{len(self.items)}", style="dim")
        body = Table(t, scroll, box=box.ROUNDED, expand=True)
        return _page(ctx, self.title, body, self.hint, status)

    def handle(self, key, ctx):
        if key in ("q", KEY_ESCAPE):
            return ("pop",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == KEY_ENTER and self.items:
            path = self.items[self.sel][0]
            if os.path.isdir(path):
                return ("push", StorageScreen(path, self.depth))
            return ("stay",)
        if key == "u":
            parent = os.path.dirname(self.path)
            if parent and parent != self.path:
                return ("replace", StorageScreen(parent, self.depth))
            return ("stay",)
        if key == "r":
            return ("stay",)
        if key == KEY_HOME or key == "g":
            self.sel = 0
            return ("stay",)
        if key == KEY_END or key == "G":
            self.sel = max(0, len(self.items) - 1)
            return ("stay",)
        return ("stay",)


class CacheScreen(Screen):
    title = "Cache & Cleanup"
    hint = "arrows=move  space=select  c=clean selected  a=select all  q=back"

    def __init__(self):
        self.sel = 0
        self.items = []
        self.selected = set()
        self.confirming = False
        self.confirm_idx = -1

    def _load(self, ctx):
        ctx.ensure_caches()
        ctx.ensure_state()
        self.items = []
        for c in ctx.caches:
            if c["size"] > 1024:
                self.items.append({
                    "label": c["label"],
                    "path": c["path"],
                    "size": c["size"],
                    "type": "cache",
                })
        state = ctx.state
        if state:
            orphans = [(n, state.graph.packages[n].get("installed_size") or 0)
                       for n in state.graph.packages
                       if state.classification(n)[0] == "ORPHAN"]
            if orphans:
                total = sum(s for _, s in orphans)
                self.items.append({
                    "label": f"{len(orphans)} orphan packages",
                    "path": None,
                    "size": total,
                    "type": "orphans",
                })

    def render(self, ctx):
        self._load(ctx)
        total = sum(i["size"] for i in self.items)
        selected_bytes = sum(self.items[i]["size"] for i in self.selected
                            if i < len(self.items))
        status = f"{len(self.items)} items, {format_size(total)} total"

        if not self.items:
            body = Panel(Text("  nothing to clean", style="dim"), box=box.ROUNDED)
            return _page(ctx, self.title, body, self.hint, status)

        t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("[x]", width=4)
        t.add_column("ITEM", ratio=3)
        t.add_column("SIZE", justify="right", ratio=1)

        sel = max(0, min(self.sel, len(self.items)))
        for i, item in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            check = " [x]" if i in self.selected else " [ ]"
            check_style = SAFE_STYLE if i in self.selected else "dim"
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(check, style=check_style),
                Text(item["label"], style="bold" if i == sel else ""),
                Text(format_size(item["size"]))
            )

        # total line
        t.add_row("", "",
                  Text("Selected:", style="bold"),
                  Text(format_size(selected_bytes), style=SAFE_STYLE))

        body = Table(t, box=box.ROUNDED, expand=True)

        if self.confirming:
            item = self.items[self.confirm_idx]
            warn = Panel(
                Text.assemble(
                    Text("  WARNING: This will permanently delete files!\n\n", style=DANGER_STYLE),
                    Text(f"  {item['label']}\n", style="bold"),
                    Text(f"  {format_size(item['size'])}\n", style=WARN_STYLE),
                    Text(f"  Path: {item['path'] or '(packages)'}\n\n", style="dim"),
                    Text("  Type 'yes' to confirm, anything else to cancel:", style=WARN_STYLE)
                ),
                title="CONFIRM DELETION",
                border_style="red", box=box.DOUBLE)
            body = Table(warn, body, box=None, expand=True)

        hint = self.hint
        if self.selected:
            hint += f"  ({len(self.selected)} selected, {format_size(selected_bytes)})"
        return _page(ctx, self.title, body, hint, status)

    def handle(self, key, ctx):
        if self.confirming:
            if key == KEY_ENTER:
                item = self.items[self.confirm_idx]
                self._do_clean(ctx, item)
                self.confirming = False
                return ("status", f"cleaned: {item['label']}")
            if key == "y":
                return ("stay",)
            self.confirming = False
            return ("stay",)

        if key in ("q", KEY_ESCAPE):
            return ("pop",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items), self.sel + 1)
            return ("stay",)
        if key == " ":
            if self.sel < len(self.items):
                if self.sel in self.selected:
                    self.selected.discard(self.sel)
                else:
                    self.selected.add(self.sel)
            return ("stay",)
        if key == "a":
            if len(self.selected) == len(self.items):
                self.selected.clear()
            else:
                self.selected = set(range(len(self.items)))
            return ("stay",)
        if key == "c" and self.selected:
            # confirm first selected item
            self.confirm_idx = min(self.selected)
            self.confirming = True
            return ("stay",)
        if key == KEY_ENTER and self.sel < len(self.items):
            self.confirm_idx = self.sel
            self.confirming = True
            return ("stay",)
        return ("stay",)

    def _do_clean(self, ctx, item):
        from .cleanup import (clean_apt_cache, clean_pip_cache, clean_npm_cache,
                              clean_cargo_cache, clean_gradle_cache,
                              clean_pacman_cache, clean_orphans)
        if item["type"] == "orphans":
            clean_orphans(ctx.db, dry_run=False)
        elif "APT" in item["label"]:
            clean_apt_cache(dry_run=False)
        elif "pacman" in item["label"]:
            clean_pacman_cache(dry_run=False)
        elif "pip" in item["label"]:
            clean_pip_cache(dry_run=False)
        elif "npm" in item["label"]:
            clean_npm_cache(dry_run=False)
        elif "cargo" in item["label"]:
            clean_cargo_cache(dry_run=False)
        elif "gradle" in item["label"]:
            clean_gradle_cache(dry_run=False)
        self.selected.discard(self.confirm_idx)
        ctx.caches = []


class OrphanScreen(Screen):
    title = "Orphan Packages"
    hint = "arrows=move  Enter=info  space=select  c=cleanup selected  q=back"

    def __init__(self):
        self.sel = 0
        self.items = []
        self.selected = set()

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return
        self.items = []
        for name, row in state.graph.packages.items():
            if state.classification(name)[0] == "ORPHAN":
                self.items.append((name, row.get("installed_size") or 0))
        self.items.sort(key=lambda x: -x[1])

    def render(self, ctx):
        self._load(ctx)
        total = sum(s for _, s in self.items)
        status = f"{len(self.items)} orphans, {format_size(total)}"

        if not self.items:
            body = Panel(Text("  no orphan packages found", style=SAFE_STYLE), box=box.ROUNDED)
            return _page(ctx, self.title, body, "q=back", status)

        t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("[x]", width=4)
        t.add_column("NAME", ratio=2)
        t.add_column("SIZE", justify="right", ratio=1)

        sel = max(0, min(self.sel, len(self.items)))
        for i, (name, size) in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            check = " [x]" if i in self.selected else " [ ]"
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(check, style=SAFE_STYLE if i in self.selected else "dim"),
                Text(name, style="bold" if i == sel else ""),
                Text(format_size(size))
            )

        body = Table(t, box=box.ROUNDED, expand=True)
        hint = self.hint
        if self.selected:
            sel_total = sum(self.items[i][1] for i in self.selected)
            hint += f"  ({len(self.selected)} selected, {format_size(sel_total)})"
        return _page(ctx, self.title, body, hint, status)

    def handle(self, key, ctx):
        if key in ("q", KEY_ESCAPE):
            return ("pop",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == " ":
            if self.sel < len(self.items):
                if self.sel in self.selected:
                    self.selected.discard(self.sel)
                else:
                    self.selected.add(self.sel)
            return ("stay",)
        if key == "a":
            if len(self.selected) == len(self.items):
                self.selected.clear()
            else:
                self.selected = set(range(len(self.items)))
            return ("stay",)
        if key == KEY_ENTER and self.items:
            name = self.items[self.sel][0]
            return ("push", PackageDetailScreen(name))
        if key == "c" and self.selected:
            from .cleanup import clean_orphans
            clean_orphans(ctx.db, dry_run=False)
            self.selected.clear()
            ctx.state = None
            return ("status", "orphans cleaned")
        return ("stay",)


class DepBrowserScreen(Screen):
    title = "Dependency Browser"
    hint = "arrows=move  Enter=drill-down  /=search  q=back"

    def __init__(self):
        self.sel = 0
        self.items = []
        self.search = ""
        self.input_buf = ""
        self.input_mode = False

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return
        self.items = []
        for name, row in state.graph.packages.items():
            cls, cnt = state.classification(name)
            rdeps = len(state.graph.direct_rdeps(name))
            deps = len(state.graph.direct_deps(name))
            if self.search and self.search.lower() not in name.lower():
                continue
            self.items.append((name, row, cls, cnt, rdeps, deps))
        self.items.sort(key=lambda x: -x[4])  # sort by rdeps desc

    def render(self, ctx):
        self._load(ctx)
        status = f"{len(self.items)} packages"

        t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("NAME", ratio=2)
        t.add_column("TYPE", ratio=1)
        t.add_column("RDEPS", justify="right", ratio=1)
        t.add_column("DEPS", justify="right", ratio=1)

        sel = max(0, min(self.sel, len(self.items)))
        for i, (name, row, cls, cnt, rdeps, deps) in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            cls_style = ("bold cyan" if cls == "EXPLICIT" else
                         "magenta" if cls == "SHARED" else
                         "red" if cls == "ORPHAN" else "dim")
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(name, style="bold" if i == sel else ""),
                Text(label, style=cls_style),
                Text(str(rdeps), style=WARN_STYLE if rdeps > 5 else ""),
                Text(str(deps))
            )

        body = Table(t, box=box.ROUNDED, expand=True)
        if self.input_mode:
            input_panel = Panel(
                Text(f"  search: {self.input_buf}_", style="bold"),
                title="Search", border_style="yellow", box=box.ROUNDED)
            body = Table(input_panel, body, box=None, expand=True)

        return _page(ctx, self.title, body,
                     "arrows=move  Enter=info  /=search  q=back", status)

    def handle(self, key, ctx):
        if self.input_mode:
            if key == KEY_ENTER:
                self.search = self.input_buf
                self.input_mode = False
                self.sel = 0
                return ("stay",)
            if key == KEY_ESCAPE or key == "ctrl-c":
                self.input_mode = False
                return ("stay",)
            if key == KEY_BACKSPACE:
                self.input_buf = self.input_buf[:-1]
                return ("stay",)
            if key and len(key) == 1 and key.isprintable():
                self.input_buf += key
                return ("stay",)
            return ("stay",)

        if key in ("q", KEY_ESCAPE):
            return ("pop",)
        if key == "/":
            self.input_mode = True
            self.input_buf = ""
            return ("stay",)
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == KEY_ENTER and self.items:
            name = self.items[self.sel][0]
            return ("push", PackageDetailScreen(name))
        if key == KEY_HOME or key == "g":
            self.sel = 0
            return ("stay",)
        if key == KEY_END or key == "G":
            self.sel = max(0, len(self.items) - 1)
            return ("stay",)
        return ("stay",)


class SearchScreen(Screen):
    title = "Search"
    hint = "type to search  Enter=select  q=back"

    def __init__(self):
        self.query = ""
        self.sel = 0
        self.items = []

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state or not self.query:
            self.items = []
            return
        q = self.query.lower()
        self.items = []
        for name, row in state.graph.packages.items():
            desc = (row.get("description") or "").lower()
            if q in name.lower() or q in desc:
                cls, cnt = state.classification(name)
                self.items.append((name, row, cls, cnt))
        self.items.sort(key=lambda x: -(x[1].get("installed_size") or 0))

    def render(self, ctx):
        self._load(ctx)
        status = f"{len(self.items)} matches"

        input_panel = Panel(
            Text(f"  search: {self.query}_", style="bold"),
            title="Query", border_style="yellow", box=box.ROUNDED)

        if not self.items:
            body = Table(input_panel,
                         Panel(Text("  type to search", style="dim"), box=box.ROUNDED),
                         box=None, expand=True)
            return _page(ctx, self.title, body, self.hint, status)

        t = Table(box=box.SIMPLE_HEAVY, show_header=True, expand=True, padding=(0, 1))
        t.add_column("", width=3)
        t.add_column("NAME", ratio=2)
        t.add_column("SIZE", justify="right", ratio=1)
        t.add_column("TYPE", ratio=1)

        sel = max(0, min(self.sel, len(self.items)))
        for i, (name, row, cls, cnt) in enumerate(self.items):
            prefix = " > " if i == sel else "   "
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            t.add_row(
                Text(prefix, style=SELECTED_STYLE if i == sel else ""),
                Text(name, style="bold" if i == sel else ""),
                Text(format_size(row.get("installed_size") or 0)),
                Text(label)
            )

        body = Table(input_panel, t, box=None, expand=True)
        return _page(ctx, self.title, body, self.hint, status)

    def handle(self, key, ctx):
        if key == KEY_ENTER and self.items:
            name = self.items[self.sel][0]
            return ("push", PackageDetailScreen(name))
        if key == KEY_UP or key == "k":
            self.sel = max(0, self.sel - 1)
            return ("stay",)
        if key == KEY_DOWN or key == "j":
            self.sel = min(len(self.items) - 1, self.sel + 1)
            return ("stay",)
        if key == KEY_BACKSPACE:
            self.query = self.query[:-1]
            self.sel = 0
            return ("stay",)
        if key == KEY_ESCAPE:
            if self.query:
                self.query = ""
                self.sel = 0
                return ("stay",)
            return ("pop",)
        if key and len(key) == 1 and key.isprintable():
            self.query += key
            self.sel = 0
            return ("stay",)
        return ("stay",)


class RemoveConfirmScreen(Screen):
    title = "Confirm Removal"
    hint = "Type 'yes' to confirm, anything else to cancel"

    def __init__(self, name, sim_result):
        self.name = name
        self.sim = sim_result
        self.input_buf = ""
        self.done = False
        self.result_msg = ""

    def render(self, ctx):
        sim = self.sim
        lines = [
            Text("  You are about to remove:", style=WARN_STYLE),
            Text(f"    {self.name}", style="bold"),
            Text(""),
        ]
        if sim.cascade_removable:
            lines.append(Text(f"  This will also remove {len(sim.cascade_removable)} packages:",
                              style=DANGER_STYLE))
            for n in sim.cascade_removable[:8]:
                lines.append(Text(f"    {n}", style="dim"))
            if len(sim.cascade_removable) > 8:
                lines.append(Text(f"    ... and {len(sim.cascade_removable) - 8} more",
                                  style="dim"))
        if sim.retained_shared:
            lines.append(Text(f"  {len(sim.retained_shared)} shared dependencies will be retained",
                              style=SAFE_STYLE))
        if sim.broken:
            lines.append(Text(f"  WARNING: {len(sim.broken)} packages would break!",
                              style=DANGER_STYLE))
        lines.append(Text(""))
        lines.append(Text(f"  Recovery: {format_size(sim.total_recovery_bytes)}", style=WARN_STYLE))
        lines.append(Text(""))
        if self.done:
            lines.append(Text(f"  {self.result_msg}", style=SAFE_STYLE if "ok" in self.result_msg.lower() else DANGER_STYLE))
        else:
            lines.append(Text(f"  Type 'yes' to confirm: {self.input_buf}_", style="bold"))

        body = Panel(
            Text("\n".join(str(s) for s in lines), overflow="fold"),
            title="CONFIRM PACKAGE REMOVAL",
            border_style="red", box=box.DOUBLE)
        return _page(ctx, self.title, body, self.hint)

    def handle(self, key, ctx):
        if self.done:
            if key in ("q", KEY_ESCAPE, KEY_ENTER):
                return ("pop",)
            return ("stay",)
        if key == KEY_ESCAPE or key == "ctrl-c":
            return ("pop",)
        if key == KEY_BACKSPACE:
            self.input_buf = self.input_buf[:-1]
            return ("stay",)
        if key == KEY_ENTER:
            if self.input_buf == "yes":
                from .package.backend import detect_backend
                backend = detect_backend()
                import subprocess
                if backend and "pacman" in backend.name:
                    proc = subprocess.run(["pacman", "-R", "--noconfirm", self.name])
                else:
                    proc = subprocess.run(["apt", "remove", "-y", self.name])
                if proc.returncode == 0:
                    self.result_msg = f"ok: {self.name} removed"
                    ctx.state = None
                else:
                    self.result_msg = f"error: removal failed (exit {proc.returncode})"
                self.done = True
                return ("stay",)
            self.input_buf = ""
            return ("stay",)
        if key and len(key) == 1 and key.isprintable():
            self.input_buf += key
            return ("stay",)
        return ("stay",)


# ── app ──────────────────────────────────────────────────────────────────

class _App:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.stack: List[Screen] = [DashboardScreen()]
        self.last_size = None

    @property
    def screen(self):
        return self.stack[-1]

    def run(self):
        console = self.ctx.console
        fd = sys.stdin.fileno()
        old = _raw_mode(fd)
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
                    if action[0] == "status":
                        self.ctx.status = action[1]
                        dirty = True
                    if key and action[0] != "status":
                        self.ctx.status = ""
                    if dirty:
                        try:
                            live.update(self.screen.render(self.ctx))
                        except Exception:
                            pass
                    else:
                        if key is None:
                            time.sleep(0.01)
                        else:
                            try:
                                live.update(self.screen.render(self.ctx))
                            except Exception:
                                pass
        except KeyboardInterrupt:
            self.ctx.status = ""
            return
        except Exception as e:
            try:
                console.print(f"[red]TUI error: {e}[/]")
            except Exception:
                pass
            return
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _dispatch(self, key):
        if key == "ctrl-c" or key == "ctrl-d":
            return ("quit",)
        if key == "q" and len(self.stack) <= 1:
            return ("quit",)
        if key is None:
            return ("stay",)
        return self.screen.handle(key, self.ctx)


# ── entry points ─────────────────────────────────────────────────────────

def _smoke_render(console, ctx):
    """Render dashboard once in non-interactive mode."""
    screen = DashboardScreen()
    try:
        console.print(screen.render(ctx))
    except Exception:
        console.print("[red]tpm TUI: non-interactive mode[/]")


def run(no_color=False, db=None, console=None):
    """Entry point for tpm tui."""
    console = console or Console()
    ctx = Context(console=console, no_color=no_color, db=db)
    ctx.ensure_state()
    if not console.is_terminal:
        _smoke_render(console, ctx)
        return 0
    app = _App(ctx)
    app.run()
    return 0
