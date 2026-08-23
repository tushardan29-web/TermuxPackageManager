"""tpm TUI — built on curses for reliable input handling.

Design inspired by Fresh editor: clean panels, working mouse+keyboard,
no stuck screens. Every screen has a status bar and usage hints.
"""

from __future__ import annotations

import curses
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional, List

from .core import load_state
from .formatter import format_size
from .package.backend import detect_backend
from .storage.scanner import detect_caches, scan_dir_size, classify_path
from .removal.simulator import simulate_removal
from .database import Database

# ── colors ───────────────────────────────────────────────────────────────

def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)      # header
    curses.init_pair(2, curses.COLOR_WHITE, -1)      # normal
    curses.init_pair(3, curses.COLOR_YELLOW, -1)     # warn
    curses.init_pair(4, curses.COLOR_RED, -1)        # danger
    curses.init_pair(5, curses.COLOR_GREEN, -1)      # safe
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)    # shared
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)    # selected
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_WHITE)   # highlight
    curses.init_pair(9, curses.COLOR_BLUE, -1)       # info
    curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_RED)    # danger bg

C_CYAN = 1
C_WHITE = 2
C_YELLOW = 3
C_RED = 4
C_GREEN = 5
C_MAGENTA = 6
C_SELECTED = 7
C_HIGHLIGHT = 8
C_BLUE = 9
C_DANGER_BG = 10


# ── data ─────────────────────────────────────────────────────────────────

@dataclass
class Ctx:
    stdscr: Any
    state: Any = None
    db: Database = None
    caches: list = field(default_factory=list)
    status: str = ""
    h: int = 0
    w: int = 0

    def ensure_db(self):
        if self.db is None:
            self.db = Database()
        return self.db

    def ensure_state(self):
        if self.state is None:
            self.ensure_db()
            try:
                self.state = load_state(db=self.db)
            except Exception as e:
                self.status = f"error: {e}"
        return self.state

    def ensure_caches(self):
        if not self.caches:
            self.caches = detect_caches()
        return self.caches

    def refresh_size(self):
        self.h, self.w = self.stdscr.getmaxyx()


class Screen:
    title = ""
    hint = ""

    def __init__(self):
        self.sel = 0
        self.scroll = 0

    def draw(self, ctx: Ctx):
        pass

    def key(self, ctx: Ctx, k: int) -> Optional[str]:
        """Handle key. Return 'pop', 'quit', None (stay), or 'push:screen'."""
        return None

    def _clamp_sel(self, count):
        if count == 0:
            self.sel = 0
        else:
            self.sel = max(0, min(self.sel, count - 1))

    def _clamp_scroll(self, count, visible):
        if count <= visible:
            self.scroll = 0
        else:
            self.scroll = max(0, min(self.scroll, count - visible))
            if self.sel < self.scroll:
                self.scroll = self.sel
            elif self.sel >= self.scroll + visible:
                self.scroll = self.sel - visible + 1

    def _draw_bar(self, ctx: Ctx, y, text, style=curses.A_NORMAL):
        ctx.stdscr.addnstr(y, 0, text.ljust(ctx.w), ctx.w - 1, style)

    def _draw_header(self, ctx: Ctx):
        title = f" {self.title} "
        if ctx.w < len(title) + 10:
            title = title[:ctx.w - 4] + ".."
        self._draw_bar(ctx, 0, title, curses.color_pair(C_CYAN) | curses.A_BOLD)

    def _draw_footer(self, ctx: Ctx):
        hint = f" {self.hint} "
        if ctx.w < len(hint) + 4:
            hint = hint[:ctx.w - 4] + ".."
        self._draw_bar(ctx, ctx.h - 1, hint, curses.color_pair(C_BLUE))

    def _draw_status(self, ctx: Ctx):
        if ctx.status:
            self._draw_bar(ctx, ctx.h - 2, f" {ctx.status}",
                           curses.color_pair(C_YELLOW))

    def _draw_list(self, ctx: Ctx, items, start_y, height, render_fn):
        """Draw a scrollable list with overflow indicators.
        render_fn(item, selected) -> (text, attr)."""
        visible = height
        self._clamp_sel(len(items))
        self._clamp_scroll(len(items), visible)

        # scroll indicators
        has_above = self.scroll > 0
        has_below = self.scroll + visible < len(items)

        for i in range(visible):
            idx = self.scroll + i
            y = start_y + i
            if y >= ctx.h - 2:
                break
            if idx < len(items):
                text, attr = render_fn(items[idx], idx == self.sel)
                cursor = ">" if idx == self.sel else " "
                line = f"{cursor} {text}"
                ctx.stdscr.addnstr(y, 0, line, ctx.w - 1, attr)
            else:
                ctx.stdscr.addnstr(y, 0, "", ctx.w - 1)

        # overflow indicators on the right edge
        if has_above:
            ctx.stdscr.addnstr(start_y - 1 if start_y > 0 else 0,
                               ctx.w - 3, "^^", 2,
                               curses.color_pair(C_YELLOW) | curses.A_BOLD)
        if has_below:
            y_ind = start_y + min(visible, len(items) - self.scroll)
            if y_ind < ctx.h - 2:
                ctx.stdscr.addnstr(y_ind, ctx.w - 3, "vv", 2,
                                   curses.color_pair(C_YELLOW) | curses.A_BOLD)

    def _is_enter(self, k):
        return k in (curses.KEY_ENTER, 10, 13)

    def _is_up(self, k):
        return k == curses.KEY_UP or k == ord('k')

    def _is_down(self, k):
        return k == curses.KEY_DOWN or k == ord('j')

    def _is_pageup(self, k):
        return k == curses.KEY_PPAGE

    def _is_pagedown(self, k):
        return k == curses.KEY_NPAGE

    def _is_home(self, k):
        return k == curses.KEY_HOME or k == ord('g')

    def _is_end(self, k):
        return k == curses.KEY_END or k == ord('G')


# ── screens ──────────────────────────────────────────────────────────────

class Dashboard(Screen):
    title = "TPM - Termux Package Manager"
    hint = "p:Packages  d:Deps  s:Storage  o:Orphans  c:Cleanup  /:Search  q:Quit"

    def draw(self, ctx: Ctx):
        self._draw_header(ctx)
        state = ctx.ensure_state()
        ctx.ensure_caches()
        y = 2
        if not state:
            ctx.stdscr.addnstr(y, 2, f"Error: {ctx.status or 'no data'}", ctx.w - 4, curses.color_pair(C_RED))
            self._draw_footer(ctx)
            return

        pkgs = state.graph.packages
        total = sum((p["installed_size"] or 0) for p in pkgs.values())
        cache_b = sum((c.get("size") or 0) for c in ctx.caches)
        explicit = sum(1 for n in pkgs if n in state.explicit)
        orphans = [n for n in pkgs if state.classification(n)[0] == "ORPHAN"]
        orphan_b = sum((pkgs[n]["installed_size"] or 0) for n in orphans)
        shared = sum(1 for n in pkgs if state.classification(n)[0] == "SHARED")
        single = sum(1 for n in pkgs if state.classification(n)[0] == "SINGLE-USE")

        rows = [
            ("Packages", str(len(pkgs))),
            ("Installed size", format_size(total)),
            ("Caches", format_size(cache_b)),
            ("Explicit", str(explicit)),
            ("Essential", str(len(state.essential))),
            ("Shared deps", str(shared)),
            ("Single-use", str(single)),
            ("Orphans", f"{len(orphans)} ({format_size(orphan_b)})"),
            ("Cleanup potential", format_size(cache_b + orphan_b)),
            ("Backend", state.backend_name),
            ("Terminal", f"{ctx.w}x{ctx.h}"),
        ]

        max_label = max(len(r[0]) for r in rows) + 2
        visible_rows = 0
        for label, val in rows:
            if y >= ctx.h - 2:
                break
            ctx.stdscr.addnstr(y, 4, label.ljust(max_label), ctx.w - 6,
                               curses.color_pair(C_WHITE))
            ctx.stdscr.addnstr(y, 4 + max_label, val, ctx.w - 4 - max_label,
                               curses.color_pair(C_CYAN) | curses.A_BOLD)
            y += 1
            visible_rows += 1

        if visible_rows < len(rows):
            ctx.stdscr.addnstr(y, 4, f"... +{len(rows) - visible_rows} more",
                               ctx.w - 6, curses.color_pair(C_YELLOW))

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if k == ord('q'):
            return "quit"
        if k == ord('p'):
            return "push:pkglist"
        if k == ord('d'):
            return "push:depbrowser"
        if k == ord('s'):
            return "push:storage"
        if k == ord('o'):
            return "push:orphans"
        if k == ord('c'):
            return "push:cache"
        if k == ord('/'):
            return "push:search"
        return None


class PkgList(Screen):
    title = "Packages"
    hint = "j/k:move  Enter:info  d:delete  e/s/o/x:filter  /:search  r:rescan  q:back"

    def __init__(self, mode="all", search=""):
        super().__init__()
        self.mode = mode
        self.search = search
        self.items = []
        self._input_buf = ""
        self._input_mode = False

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

    def draw(self, ctx):
        self._load(ctx)
        self._draw_header(ctx)

        # status line
        filt = f" [{self.mode.upper()}]" if self.mode != "all" else ""
        srch = f" /{self.search}" if self.search else ""
        status = f" {len(self.items)} packages{filt}{srch}"
        ctx.stdscr.addnstr(1, 0, status.ljust(ctx.w), ctx.w - 1,
                           curses.color_pair(C_WHITE))

        # input line
        if self._input_mode:
            ctx.stdscr.addnstr(2, 0, f" search: {self._input_buf}_".ljust(ctx.w),
                               ctx.w - 1, curses.color_pair(C_YELLOW) | curses.A_BOLD)
            start_y = 3
        else:
            start_y = 2

        # list
        def render(item, sel):
            name, row, cls, cnt = item
            size = format_size(row.get("installed_size") or 0)
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            attr = curses.color_pair(C_WHITE)
            if cls == "EXPLICIT":
                label_attr = curses.color_pair(C_CYAN) | curses.A_BOLD
            elif cls == "SHARED":
                label_attr = curses.color_pair(C_MAGENTA)
            elif cls == "ORPHAN":
                label_attr = curses.color_pair(C_RED)
            elif cls == "ESSENTIAL":
                label_attr = curses.color_pair(C_YELLOW)
            else:
                label_attr = curses.color_pair(C_WHITE)
            if sel:
                attr = curses.color_pair(C_SELECTED) | curses.A_BOLD
            # We return raw text; color is applied via attribute
            text = f"{name:<28} {size:>10}  {label}"
            return text, attr

        self._draw_list(ctx, self.items, start_y, ctx.h - 4, render)
        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self._input_mode:
            if k == 27:  # ESC
                self._input_mode = False
                return None
            if k in (curses.KEY_ENTER, 10, 13):
                self.search = self._input_buf
                self._input_mode = False
                self.sel = 0
                return None
            if k in (curses.KEY_BACKSPACE, 127, 8):
                self._input_buf = self._input_buf[:-1]
                return None
            if 32 <= k < 127:
                self._input_buf += chr(k)
                return None
            return None

        if k == ord('/'):
            self._input_mode = True
            self._input_buf = ""
            return None
        if k == ord('q'):
            return "pop"
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if self._is_pageup(k):
            self.sel -= 20
            return None
        if self._is_pagedown(k):
            self.sel += 20
            return None
        if self._is_home(k):
            self.sel = 0
            return None
        if self._is_end(k):
            self.sel = len(self.items) - 1
            return None
        if self._is_enter(k) and self.items:
            name = self.items[self.sel][0]
            return f"push:detail:{name}"
        if k == ord('e'):
            self.mode = "explicit" if self.mode != "explicit" else "all"
            self.sel = 0
            return None
        if k == ord('s'):
            self.mode = "shared" if self.mode != "shared" else "all"
            self.sel = 0
            return None
        if k == ord('o'):
            self.mode = "orphans" if self.mode != "orphans" else "all"
            self.sel = 0
            return None
        if k == ord('x'):
            self.mode = "all"
            self.sel = 0
            return None
        if k == ord('r'):
            from .scanner import run_scan
            run_scan(db=ctx.db)
            ctx.state = None
            ctx.status = "rescan complete"
            return None
        if k == ord('d') and self.items:
            name = self.items[self.sel][0]
            return f"push:confirm:{name}"
        return None


class Detail(Screen):
    title = "Package"
    hint = "r:simulate  d:deps  R:rdeps  f:files  q:back"

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.sim = None
        self.show_files = False
        self.files = []
        self.tab = 0  # 0=info, 1=deps, 2=rdeps, 3=files

    def draw(self, ctx):
        state = ctx.ensure_state()
        self.title = self.name
        self._draw_header(ctx)

        if not state or self.name not in state.graph.packages:
            ctx.stdscr.addnstr(2, 2, f"Not found: {self.name}", ctx.w - 4,
                               curses.color_pair(C_RED))
            self._draw_footer(ctx)
            return

        row = state.graph.packages[self.name]
        cls, cnt = state.classification(self.name)
        label = f"SHARED x{cnt}" if cls == "SHARED" else cls
        y = 2

        # info block
        info_lines = [
            ("Name", self.name),
            ("Version", str(row.get("version") or "")),
            ("Arch", str(row.get("architecture") or "")),
            ("Size", format_size(row.get("installed_size") or 0)),
            ("Type", label),
            ("Essential", "yes" if row.get("essential") else "no"),
        ]
        for lbl, val in info_lines:
            if y >= ctx.h - 3:
                break
            ctx.stdscr.addnstr(y, 2, f"{lbl}:", 12, curses.color_pair(C_WHITE))
            ctx.stdscr.addnstr(y, 14, val, ctx.w - 16, curses.color_pair(C_CYAN))
            y += 1

        y += 1
        # tabs
        tabs = ["Info", "Deps", "Rdeps", "Files"]
        tab_line = " ".join(
            f"[{('>' if i == self.tab else ' ')} {t}]" for i, t in enumerate(tabs))
        ctx.stdscr.addnstr(y, 2, tab_line, ctx.w - 4,
                           curses.color_pair(C_YELLOW))
        y += 1

        # tab content
        if self.tab == 1:  # deps
            deps = state.graph.direct_deps(self.name)
            shown = 0
            for d in deps:
                if y >= ctx.h - 3:
                    break
                if d in state.graph.packages:
                    dc, dcnt = state.classification(d)
                    dl = f"SHARED x{dcnt}" if dc == "SHARED" else dc
                    ctx.stdscr.addnstr(y, 4, f"{d}", ctx.w - 20,
                                       curses.color_pair(C_WHITE))
                    ctx.stdscr.addnstr(y, ctx.w - 16, dl, 14,
                                       curses.color_pair(C_MAGENTA if dc == "SHARED" else C_WHITE))
                y += 1
                shown += 1
            if shown < len(deps):
                ctx.stdscr.addnstr(y, 4, f"... +{len(deps) - shown} more deps",
                                   ctx.w - 6, curses.color_pair(C_YELLOW))
            elif not deps:
                ctx.stdscr.addnstr(y, 4, "(none)", ctx.w - 6,
                                   curses.color_pair(C_WHITE))

        elif self.tab == 2:  # rdeps
            rdeps = state.graph.direct_rdeps(self.name)
            shown = 0
            for r in rdeps:
                if y >= ctx.h - 3:
                    break
                ctx.stdscr.addnstr(y, 4, r, ctx.w - 6,
                                   curses.color_pair(C_WHITE))
                y += 1
                shown += 1
            if shown < len(rdeps):
                ctx.stdscr.addnstr(y, 4, f"... +{len(rdeps) - shown} more rdeps",
                                   ctx.w - 6, curses.color_pair(C_YELLOW))
            elif not rdeps:
                ctx.stdscr.addnstr(y, 4, "(nothing depends on this)",
                                   ctx.w - 6, curses.color_pair(C_WHITE))

        elif self.tab == 3:  # files
            if self.files:
                shown = 0
                for fp, fs in self.files[:ctx.h - 6]:
                    if y >= ctx.h - 3:
                        break
                    sz = format_size(fs) if fs else ""
                    ctx.stdscr.addnstr(y, 4, f"{sz:>10}  {fp}", ctx.w - 6,
                                       curses.color_pair(C_WHITE))
                    y += 1
                    shown += 1
                if shown < len(self.files):
                    ctx.stdscr.addnstr(y, 4, f"... +{len(self.files) - shown} more files",
                                       ctx.w - 6, curses.color_pair(C_YELLOW))
            else:
                ctx.stdscr.addnstr(y, 4, "(press f to load files)", ctx.w - 6,
                                   curses.color_pair(C_WHITE))

        else:  # info (tab 0) - show simulation if active
            if self.sim:
                sim = self.sim
                ctx.stdscr.addnstr(y, 2, "SIMULATION:", ctx.w - 4,
                                   curses.color_pair(C_YELLOW) | curses.A_BOLD)
                y += 1
                ctx.stdscr.addnstr(y, 4, f"Remove: {', '.join(sim.requested)}",
                                   ctx.w - 6, curses.color_pair(C_RED))
                y += 1
                if sim.cascade_removable:
                    ctx.stdscr.addnstr(y, 4,
                                       f"Cascade: {len(sim.cascade_removable)} packages",
                                       ctx.w - 6, curses.color_pair(C_YELLOW))
                    y += 1
                    for n in sim.cascade_removable[:5]:
                        if y >= ctx.h - 3:
                            break
                        ctx.stdscr.addnstr(y, 6, n, ctx.w - 8,
                                           curses.color_pair(C_WHITE))
                        y += 1
                    if len(sim.cascade_removable) > 5:
                        ctx.stdscr.addnstr(y, 6,
                                           f"... and {len(sim.cascade_removable) - 5} more",
                                           ctx.w - 8, curses.color_pair(C_WHITE))
                        y += 1
                if sim.retained_shared:
                    ctx.stdscr.addnstr(y, 4,
                                       f"Retained: {len(sim.retained_shared)} shared",
                                       ctx.w - 6, curses.color_pair(C_GREEN))
                    y += 1
                if sim.broken:
                    ctx.stdscr.addnstr(y, 4, f"BROKEN: {len(sim.broken)}",
                                       ctx.w - 6, curses.color_pair(C_RED) | curses.A_BOLD)
                    y += 1
                ctx.stdscr.addnstr(y, 4,
                                   f"Recovery: {format_size(sim.total_recovery_bytes)}",
                                   ctx.w - 6, curses.color_pair(C_GREEN))
            else:
                ctx.stdscr.addnstr(y, 2, "Press 'r' to simulate removal",
                                   ctx.w - 4, curses.color_pair(C_WHITE))

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if k == ord('q'):
            return "pop"
        if k == ord('1'):
            self.tab = 0
            return None
        if k == ord('2'):
            self.tab = 1
            return None
        if k == ord('3'):
            self.tab = 2
            return None
        if k == ord('4'):
            self.tab = 3
            return None
        if k == ord('r'):
            if self.sim is None:
                state = ctx.ensure_state()
                if state and self.name in state.graph.packages:
                    self.sim = simulate_removal(
                        state.graph, [self.name],
                        explicit_set=state.explicit,
                        essential_set=state.essential)
                    self.tab = 0
            else:
                self.sim = None
            return None
        if k == ord('d'):
            self.tab = 1
            return None
        if k == ord('R'):
            self.tab = 2
            return None
        if k == ord('f'):
            if not self.files:
                db = ctx.ensure_db()
                if db:
                    if not db.files_populated(self.name):
                        backend = detect_backend()
                        if backend:
                            fl = backend.file_list(self.name)
                            db.populate_files(self.name, fl)
                    self.files = [
                        (r["path"], r["size"])
                        for r in db.conn.execute(
                            "SELECT path, size FROM files f JOIN packages p "
                            "ON p.id=f.package_id WHERE p.name=? "
                            "ORDER BY COALESCE(size,0) DESC",
                            (self.name,)).fetchall()
                    ]
            self.tab = 3
            return None
        if k == ord('c'):
            # confirm removal
            return f"push:confirm:{self.name}"
        return None


class ConfirmRemoval(Screen):
    title = "Confirm Removal"
    hint = "Type 'yes' to confirm, any other key to cancel"

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.buf = ""
        self.done = False
        self.result = ""

    def draw(self, ctx):
        self._draw_header(ctx)
        state = ctx.ensure_state()
        y = 2

        sim = None
        if state and self.name in state.graph.packages:
            sim = simulate_removal(
                state.graph, [self.name],
                explicit_set=state.explicit,
                essential_set=state.essential)

        if sim:
            ctx.stdscr.addnstr(y, 2, f"Package: {self.name}", ctx.w - 4,
                               curses.color_pair(C_RED) | curses.A_BOLD)
            y += 1
            if sim.cascade_removable:
                ctx.stdscr.addnstr(y, 2,
                                   f"Will also remove {len(sim.cascade_removable)} packages:",
                                   ctx.w - 4, curses.color_pair(C_YELLOW))
                y += 1
                for n in sim.cascade_removable[:8]:
                    if y >= ctx.h - 4:
                        break
                    ctx.stdscr.addnstr(y, 4, n, ctx.w - 6,
                                       curses.color_pair(C_WHITE))
                    y += 1
            if sim.retained_shared:
                ctx.stdscr.addnstr(y, 2,
                                   f"{len(sim.retained_shared)} shared deps retained",
                                   ctx.w - 4, curses.color_pair(C_GREEN))
                y += 1
            ctx.stdscr.addnstr(y, 2,
                               f"Recovery: {format_size(sim.total_recovery_bytes)}",
                               ctx.w - 4, curses.color_pair(C_GREEN))
            y += 2

        if self.done:
            style = curses.color_pair(C_GREEN) if "ok" in self.result.lower() else curses.color_pair(C_RED)
            ctx.stdscr.addnstr(y, 2, self.result, ctx.w - 4, style)
        else:
            ctx.stdscr.addnstr(y, 2, f"Type 'yes' to confirm: {self.buf}_",
                               ctx.w - 4, curses.color_pair(C_YELLOW) | curses.A_BOLD)

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self.done:
            return "pop"
        if k == 27:  # ESC
            return "pop"
        if k in (curses.KEY_ENTER, 10, 13):
            if self.buf == "yes":
                backend = detect_backend()
                import subprocess
                if backend and "pacman" in backend.name:
                    proc = subprocess.run(["pacman", "-R", "--noconfirm", self.name],
                                          capture_output=True)
                elif backend and "uv" in backend.name:
                    proc = subprocess.run(["uv", "pip", "uninstall", self.name],
                                          capture_output=True)
                else:
                    proc = subprocess.run(["apt", "remove", "-y", self.name],
                                          capture_output=True)
                if proc.returncode == 0:
                    self.result = f"ok: {self.name} removed"
                    ctx.state = None
                else:
                    self.result = f"error: removal failed"
                self.done = True
            else:
                self.buf = ""
            return None
        if k in (curses.KEY_BACKSPACE, 127, 8):
            self.buf = self.buf[:-1]
            return None
        if 32 <= k < 127:
            self.buf += chr(k)
            return None
        return None


class Storage(Screen):
    title = "Storage"
    hint = "j/k:move  Enter:drill  d:delete  u:up  q:back"

    def __init__(self, path=None, depth=2):
        super().__init__()
        self.path = path or os.path.expanduser("~")
        self.depth = depth
        self.items = []
        self.confirming = False
        self.confirm_idx = -1

    def _load(self, ctx):
        sizes = scan_dir_size(self.path, max_depth=self.depth)
        self.items = []
        for p, s in sizes.items():
            if p == self.path:
                continue
            cat = classify_path(p)
            short = p.replace(os.path.expanduser("~"), "~")
            is_dir = os.path.isdir(p)
            self.items.append((p, short, s, cat, is_dir))
        self.items.sort(key=lambda x: -x[2])

    def draw(self, ctx):
        self._load(ctx)
        self.title = f"Storage: {self.path.replace(os.path.expanduser('~'), '~')}"
        self._draw_header(ctx)
        total = sum(i[2] for i in self.items)
        ctx.stdscr.addnstr(1, 0, f" {format_size(total, 2)} total".ljust(ctx.w),
                           ctx.w - 1, curses.color_pair(C_WHITE))

        def render(item, sel):
            full, short, size, cat, is_dir = item
            cat_s = {"caches": C_YELLOW, "proot-distro": C_RED,
                     "$PREFIX": C_GREEN}.get(cat, C_WHITE)
            icon = "/" if is_dir else " "
            text = f"{format_size(size):>10}  {icon}{short}"
            return text, curses.color_pair(cat_s)

        self._draw_list(ctx, self.items, 2, ctx.h - 4, render)

        # draw category column
        for i in range(min(len(self.items), ctx.h - 4)):
            idx = self.scroll + i
            if idx < len(self.items):
                cat = self.items[idx][3]
                cat_s = {"caches": C_YELLOW, "proot-distro": C_RED,
                         "$PREFIX": C_GREEN}.get(cat, C_WHITE)
                y = 2 + i
                ctx.stdscr.addnstr(y, ctx.w - 16, f"[{cat}]", 15,
                                   curses.color_pair(cat_s))

        if self.confirming and self.confirm_idx < len(self.items):
            item = self.items[self.confirm_idx]
            y = ctx.h - 4
            ctx.stdscr.addnstr(y, 2,
                               f"DELETE: {item[1]} ({format_size(item[2])})? Type 'yes'",
                               ctx.w - 4, curses.color_pair(C_RED) | curses.A_BOLD)

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self.confirming:
            if k in (curses.KEY_ENTER, 10, 13) or k == ord('y'):
                item = self.items[self.confirm_idx]
                self._do_delete(ctx, item)
                self.confirming = False
                ctx.status = f"deleted: {item[1]}"
                return None
            self.confirming = False
            return None

        if k == ord('q'):
            return "pop"
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if self._is_enter(k) and self.items:
            path = self.items[self.sel][0]
            if os.path.isdir(path):
                return f"push:storage:{path}"
            return None
        if k == ord('d') and self.items:
            self.confirm_idx = self.sel
            self.confirming = True
            return None
        if k == ord('u'):
            parent = os.path.dirname(self.path)
            if parent and parent != self.path:
                return f"replace:storage:{parent}"
            return None
        return None

    def _do_delete(self, ctx, item):
        import shutil
        path = item[0]
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as e:
            ctx.status = f"error: {e}"


class CacheScreen(Screen):
    title = "Cache & Cleanup"
    hint = "j/k:move  Space:select  c:clean  a:all  q:back"

    def __init__(self):
        super().__init__()
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
                self.items.append({"label": c["label"], "path": c["path"],
                                   "size": c["size"], "type": "cache"})
        state = ctx.state
        if state:
            orphans = [(n, state.graph.packages[n].get("installed_size") or 0)
                       for n in state.graph.packages
                       if state.classification(n)[0] == "ORPHAN"]
            if orphans:
                total = sum(s for _, s in orphans)
                self.items.append({"label": f"{len(orphans)} orphan packages",
                                   "path": None, "size": total, "type": "orphans"})

    def draw(self, ctx):
        self._load(ctx)
        self._draw_header(ctx)
        total = sum(i["size"] for i in self.items)
        sel_bytes = sum(self.items[i]["size"] for i in self.selected
                        if i < len(self.items))
        ctx.stdscr.addnstr(1, 0,
                           f" {len(self.items)} items, {format_size(total)} total".ljust(ctx.w),
                           ctx.w - 1, curses.color_pair(C_WHITE))

        def render(item, sel):
            check = "[x]" if item.get("_sel") else "[ ]"
            text = f"{check} {item['label']:<30} {format_size(item['size']):>10}"
            attr = curses.color_pair(C_SELECTED if item.get("_sel") else C_WHITE)
            return text, attr

        # mark selected
        for i, item in enumerate(self.items):
            item["_sel"] = i in self.selected

        self._draw_list(ctx, self.items, 2, ctx.h - 4, render)

        if self.confirming:
            item = self.items[self.confirm_idx]
            y = ctx.h - 4
            ctx.stdscr.addnstr(y, 2,
                               f"WARNING: Delete {item['label']} ({format_size(item['size'])})? Type 'yes'",
                               ctx.w - 4, curses.color_pair(C_RED) | curses.A_BOLD)

        if self.selected:
            ctx.stdscr.addnstr(ctx.h - 2, 0,
                               f" {len(self.selected)} selected, {format_size(sel_bytes)}".ljust(ctx.w),
                               ctx.w - 1, curses.color_pair(C_GREEN))

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self.confirming:
            if k in (curses.KEY_ENTER, 10, 13) or k == ord('y'):
                item = self.items[self.confirm_idx]
                self._do_clean(ctx, item)
                self.confirming = False
                ctx.status = f"cleaned: {item['label']}"
                return None
            self.confirming = False
            return None

        if k == ord('q'):
            return "pop"
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if k == ord(' '):
            if self.sel < len(self.items):
                if self.sel in self.selected:
                    self.selected.discard(self.sel)
                else:
                    self.selected.add(self.sel)
            return None
        if k == ord('a'):
            if len(self.selected) == len(self.items):
                self.selected.clear()
            else:
                self.selected = set(range(len(self.items)))
            return None
        if k == ord('c') and self.selected:
            self.confirm_idx = min(self.selected)
            self.confirming = True
            return None
        if self._is_enter(k) and self.sel < len(self.items):
            self.confirm_idx = self.sel
            self.confirming = True
            return None
        return None

    def _do_clean(self, ctx, item):
        from .cleanup import (clean_apt_cache, clean_pip_cache, clean_npm_cache,
                              clean_cargo_cache, clean_gradle_cache,
                              clean_pacman_cache, clean_uv_cache, clean_orphans)
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
        elif "uv" in item["label"]:
            clean_uv_cache(dry_run=False)
        self.selected.discard(self.confirm_idx)
        ctx.caches = []


class Orphans(Screen):
    title = "Orphan Packages"
    hint = "j/k:move  Enter:info  Space:select  d:delete  c:cleanup  q:back"

    def __init__(self):
        super().__init__()
        self.items = []
        self.selected = set()
        self.confirming = False
        self.confirm_idx = -1

    def _load(self, ctx):
        state = ctx.ensure_state()
        if not state:
            return
        self.items = []
        for name, row in state.graph.packages.items():
            if state.classification(name)[0] == "ORPHAN":
                self.items.append({"name": name, "size": row.get("installed_size") or 0})
        self.items.sort(key=lambda x: -x["size"])

    def draw(self, ctx):
        self._load(ctx)
        self._draw_header(ctx)
        total = sum(i["size"] for i in self.items)
        ctx.stdscr.addnstr(1, 0,
                           f" {len(self.items)} orphans, {format_size(total)}".ljust(ctx.w),
                           ctx.w - 1, curses.color_pair(C_WHITE))

        def render(item, sel):
            check = "[x]" if item.get("_sel") else "[ ]"
            text = f"{check} {item['name']:<30} {format_size(item['size']):>10}"
            attr = curses.color_pair(C_SELECTED if sel else C_WHITE)
            return text, attr

        # mark selected state on items for render
        for i, item in enumerate(self.items):
            item["_sel"] = i in self.selected

        self._draw_list(ctx, self.items, 2, ctx.h - 4, render)

        if self.confirming and self.confirm_idx < len(self.items):
            item = self.items[self.confirm_idx]
            y = ctx.h - 4
            ctx.stdscr.addnstr(y, 2,
                               f"DELETE: {item['name']} ({format_size(item['size'])})? Type 'yes'",
                               ctx.w - 4, curses.color_pair(C_RED) | curses.A_BOLD)

        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self.confirming:
            if k in (curses.KEY_ENTER, 10, 13) or k == ord('y'):
                item = self.items[self.confirm_idx]
                self._do_delete(ctx, item)
                self.confirming = False
                ctx.status = f"deleted: {item['name']}"
                return None
            self.confirming = False
            return None

        if k == ord('q'):
            return "pop"
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if k == ord(' ') and self.sel < len(self.items):
            if self.sel in self.selected:
                self.selected.discard(self.sel)
            else:
                self.selected.add(self.sel)
            return None
        if k == ord('a'):
            if len(self.selected) == len(self.items):
                self.selected.clear()
            else:
                self.selected = set(range(len(self.items)))
            return None
        if self._is_enter(k) and self.items:
            name = self.items[self.sel]["name"]
            return f"push:detail:{name}"
        if k == ord('d') and self.items:
            self.confirm_idx = self.sel
            self.confirming = True
            return None
        if k == ord('c') and self.selected:
            from .cleanup import clean_orphans
            clean_orphans(ctx.db, dry_run=False)
            self.selected.clear()
            ctx.state = None
            ctx.status = "orphans cleaned"
            return None
        return None

    def _do_delete(self, ctx, item):
        backend = detect_backend()
        import subprocess
        if backend and "pacman" in backend.name:
            subprocess.run(["pacman", "-R", "--noconfirm", item["name"]],
                           capture_output=True)
        elif backend and "uv" in backend.name:
            subprocess.run(["uv", "pip", "uninstall", item["name"]],
                           capture_output=True)
        else:
            subprocess.run(["apt", "remove", "-y", item["name"]],
                           capture_output=True)
        self.items.remove(item)
        ctx.state = None


class DepBrowser(Screen):
    title = "Dependency Browser"
    hint = "j/k:move  Enter:info  d:delete  /:search  q:back"

    def __init__(self):
        super().__init__()
        self.items = []
        self.search = ""
        self._input_buf = ""
        self._input_mode = False

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
            self.items.append((name, cls, cnt, rdeps, deps))
        self.items.sort(key=lambda x: -x[3])

    def draw(self, ctx):
        self._load(ctx)
        self._draw_header(ctx)
        ctx.stdscr.addnstr(1, 0,
                           f" {len(self.items)} packages".ljust(ctx.w),
                           ctx.w - 1, curses.color_pair(C_WHITE))

        if self._input_mode:
            ctx.stdscr.addnstr(2, 0, f" search: {self._input_buf}_".ljust(ctx.w),
                               ctx.w - 1, curses.color_pair(C_YELLOW))
            start_y = 3
        else:
            start_y = 2

        def render(item, sel):
            name, cls, cnt, rdeps, deps = item
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            text = f"{name:<28} {label:<16} rdeps:{rdeps:<4} deps:{deps}"
            return text, curses.color_pair(C_WHITE)

        self._draw_list(ctx, self.items, start_y, ctx.h - 4, render)
        self._draw_footer(ctx)

    def key(self, ctx, k):
        if self._input_mode:
            if k == 27:
                self._input_mode = False
                return None
            if k in (curses.KEY_ENTER, 10, 13):
                self.search = self._input_buf
                self._input_mode = False
                self.sel = 0
                return None
            if k in (curses.KEY_BACKSPACE, 127, 8):
                self._input_buf = self._input_buf[:-1]
                return None
            if 32 <= k < 127:
                self._input_buf += chr(k)
                return None
            return None

        if k == ord('q'):
            return "pop"
        if k == ord('/'):
            self._input_mode = True
            self._input_buf = ""
            return None
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if self._is_enter(k) and self.items:
            name = self.items[self.sel][0]
            return f"push:detail:{name}"
        if k == ord('d') and self.items:
            name = self.items[self.sel][0]
            return f"push:confirm:{name}"
        return None


class Search(Screen):
    title = "Search"
    hint = "type to search  Enter:select  d:delete  q:back"

    def __init__(self):
        super().__init__()
        self.query = ""
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

    def draw(self, ctx):
        self._load(ctx)
        self._draw_header(ctx)
        ctx.stdscr.addnstr(1, 0,
                           f" search: {self.query}_".ljust(ctx.w),
                           ctx.w - 1, curses.color_pair(C_YELLOW) | curses.A_BOLD)

        def render(item, sel):
            name, row, cls, cnt = item
            size = format_size(row.get("installed_size") or 0)
            label = f"SHARED x{cnt}" if cls == "SHARED" else cls
            text = f"{name:<28} {size:>10}  {label}"
            return text, curses.color_pair(C_WHITE)

        self._draw_list(ctx, self.items, 2, ctx.h - 4, render)
        self._draw_footer(ctx)

    def key(self, ctx, k):
        if k == 27:  # ESC
            if self.query:
                self.query = ""
                self.sel = 0
                return None
            return "pop"
        if k in (curses.KEY_ENTER, 10, 13) and self.items:
            name = self.items[self.sel][0]
            return f"push:detail:{name}"
        if k == ord('d') and self.items:
            name = self.items[self.sel][0]
            return f"push:confirm:{name}"
        if self._is_up(k):
            self.sel -= 1
            return None
        if self._is_down(k):
            self.sel += 1
            return None
        if k in (curses.KEY_BACKSPACE, 127, 8):
            self.query = self.query[:-1]
            self.sel = 0
            return None
        if 32 <= k < 127:
            self.query += chr(k)
            self.sel = 0
            return None
        return None


# ── app ──────────────────────────────────────────────────────────────────

def _make_screen(spec):
    """Create a screen from a 'type:args' spec string."""
    parts = spec.split(":", 2)
    kind = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    if kind == "pkglist":
        return PkgList()
    if kind == "detail":
        return Detail(arg)
    if kind == "confirm":
        return ConfirmRemoval(arg)
    if kind == "storage":
        return Storage(arg if arg else None)
    if kind == "cache":
        return CacheScreen()
    if kind == "orphans":
        return Orphans()
    if kind == "depbrowser":
        return DepBrowser()
    if kind == "search":
        return Search()
    return Dashboard()


class App:
    def __init__(self, stdscr):
        self.ctx = Ctx(stdscr=stdscr)
        self.ctx.refresh_size()
        self.stack: List[Screen] = [Dashboard()]

    def run(self):
        stdscr = self.ctx.stdscr
        curses.curs_set(0)
        stdscr.timeout(50)
        _init_colors()

        # enable mouse
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        while True:
            stdscr.erase()
            self.ctx.refresh_size()

            screen = self.stack[-1]
            screen.draw(self.ctx)

            # status bar
            if self.ctx.status:
                stdscr.addnstr(self.ctx.h - 2, 0,
                               f" {self.ctx.status}".ljust(self.ctx.w),
                               self.ctx.w - 1, curses.color_pair(C_YELLOW))

            stdscr.refresh()

            k = stdscr.getch()
            if k == -1:
                continue

            # terminal resize — redraw on next loop iteration
            if k == curses.KEY_RESIZE:
                stdscr.clear()
                continue

            # mouse events - treat as click
            if k == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & curses.BUTTON1_CLICKED:
                        # find item at my position
                        if my == 0:
                            continue  # header
                        if my == self.ctx.h - 1:
                            continue  # footer
                        # map to list index
                        item_idx = screen.scroll + (my - 2)
                        if hasattr(screen, 'items') and item_idx < len(screen.items):
                            screen.sel = item_idx
                            # simulate enter
                            result = screen.key(self.ctx, curses.KEY_ENTER)
                            self._handle_result(result)
                            continue
                except Exception:
                    pass
                continue

            result = screen.key(self.ctx, k)
            self._handle_result(result)

    def _handle_result(self, result):
        if result is None:
            return
        if result == "quit":
            raise KeyboardInterrupt
        if result == "pop":
            if len(self.stack) > 1:
                self.stack.pop()
            return
        if result.startswith("push:"):
            self.stack.append(_make_screen(result))
        if result.startswith("replace:"):
            if len(self.stack) > 1:
                self.stack.pop()
            self.stack.append(_make_screen(result))


def _smoke(stdscr):
    """Non-interactive: just show dashboard."""
    _init_colors()
    ctx = Ctx(stdscr=stdscr)
    ctx.ensure_state()
    ctx.ensure_caches()
    ctx.refresh_size()
    screen = Dashboard()
    screen.draw(ctx)
    stdscr.refresh()
    stdscr.getch()


def run(no_color=False, db=None, console=None):
    """Entry point."""
    def _main(stdscr):
        app = App(stdscr)
        try:
            app.run()
        except KeyboardInterrupt:
            pass
        return 0

    return curses.wrapper(_main)
