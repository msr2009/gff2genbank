"""
setup_gui/logbox.py
-------------------
Shared progress-log widget and busy-button helpers used by every step.

Usage — log box:
    # in the @module.ui
    log_box_ui("scan_logbox")
    # in the @module.server
    @render.ui
    def scan_logbox():
        return log_lines(scan_log())

Usage — animated status spinner:
    # inside a @render.ui function
    return spinner("Scanning… (large files may take a moment)")

Usage — busy button (disables + relabels + shows spinner while task runs):
    # inside @module.server, after declaring the running reactive.Value
    bind_busy_button("scan_btn", scan_running,
                     idle_label="Scan input file(s)",
                     busy_label="Scanning…")

The container is a STABLE Shiny output styled `.progress-log` (a real capped,
scrollable block box — see ui.py). Because the element persists across updates,
its scroll position is preserved; only its inner lines are swapped.
"""

from shiny import ui, reactive


def log_box_ui(output_id: str):
    """A scrollable, height-capped progress-log output container."""
    return ui.output_ui(output_id, class_="progress-log")


def log_lines(steps, n: int = 300):
    """Render the (last n) log messages as the container's inner content."""
    if not steps:
        return ui.div()
    return ui.TagList(*[ui.div(f"• {s}", {"class": "log-line"}) for s in steps[-n:]])


def spinner(text: str) -> ui.Tag:
    """Animated inline spinner + label for step status lines.

    Renders as a small CSS-animated circle (`.fb-spin`) followed by the given
    text, all wrapped in `.fb-spinner` (blue, 0.86em).  Replace every bare
    `ui.tags.span("⏳ …", {"class":"fb-spinner"})` with `spinner("…")`.
    """
    return ui.tags.span({"class": "fb-spinner"},
        ui.tags.span({"class": "fb-spin"}),
        f" {text}",
    )


def bind_busy_button(
    button_id: str,
    is_running,
    idle_label: str,
    busy_label: str,
):
    """Register a reactive effect that disables + relabels a button while a task runs.

    Parameters
    ----------
    button_id   : the Shiny input id (already namespace-resolved by the module context).
    is_running  : zero-arg callable that returns bool (typically a reactive.Value or a
                  lambda reading one, e.g. ``lambda: task.status() == "running"``).
    idle_label  : button text when idle.
    busy_label  : button text while running (the animated `.fb-spin` icon is prepended
                  automatically).

    Call this inside a ``@module.server`` function — Shiny's module namespace is active
    so ``button_id`` resolves correctly without the module prefix.
    """
    @reactive.effect
    def _toggle():
        if is_running():
            ui.update_action_button(
                button_id,
                label=busy_label,
                icon=ui.tags.span({"class": "fb-spin"}),
                disabled=True,
            )
        else:
            ui.update_action_button(
                button_id,
                label=idle_label,
                icon=ui.tags.span(),   # clear the icon slot
                disabled=False,
            )
    return _toggle
