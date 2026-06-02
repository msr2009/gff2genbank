"""
setup_gui/logbox.py
-------------------
One shared progress-log widget used by every step (scan, prepare, build, …) so
all log boxes look and behave identically and there's a single definition to
maintain.

Usage:
    # in the @module.ui
    log_box_ui("scan_logbox")
    # in the @module.server
    @render.ui
    def scan_logbox():
        return log_lines(scan_log())

The container is a STABLE Shiny output styled `.progress-log` (a real capped,
scrollable block box — see ui.py). Because the element persists across updates,
its scroll position is preserved; only its inner lines are swapped.
"""

from shiny import ui


def log_box_ui(output_id: str):
    """A scrollable, height-capped progress-log output container."""
    return ui.output_ui(output_id, class_="progress-log")


def log_lines(steps, n: int = 300):
    """Render the (last n) log messages as the container's inner content."""
    if not steps:
        return ui.div()
    return ui.TagList(*[ui.div(f"• {s}", {"class": "log-line"}) for s in steps[-n:]])
