"""Interactive TUI entry point — thin re-export of the rich-based TUI.

The full implementation lives in :mod:`tpm.tui` (so it uses ``tpm.core`` as the
single source of truth, shared with the CLI). This submodule alias keeps the
``tpm.ui.tui.run()`` path referenced by the CLI wiring.
"""

from ..tui import run, run as _run  # noqa: F401

__all__ = ["run"]
