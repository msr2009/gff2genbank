"""
setup_gui/step_deps.py
----------------------
Step 1 of the setup GUI: check that required/optional dependencies are present.

Runs automatically on load (an initial check) and offers a re-check button.
All probing is delegated to engine.check_dependencies (which reuses the lists
and probes in setup/s01_check_deps.py).
"""

import asyncio

from shiny import module, ui, render, reactive

from . import engine


@module.ui
def deps_ui() -> ui.Tag:
    return ui.div(
        ui.div({"class": "card"},
            ui.tags.h5("Step 1 — Dependencies"),
            ui.p("These tools and packages are used to prepare data and build "
                 "the database. Required items must be present; optional ones "
                 "enable extra inputs (VCF, GTF conversion, faster sorting).",
                 {"class": "upload-note"}),
            ui.input_action_button("recheck", "Re-check",
                                    class_="btn btn-sm btn-outline-secondary"),
            ui.output_ui("deps_summary"),
        ),
        ui.output_ui("deps_table"),
    )


@module.server
def deps_server(input, output, session, app_session: reactive.Value):
    deps_result  = reactive.Value(None)   # list[dict] | None
    deps_running = reactive.Value(False)

    @reactive.extended_task
    async def deps_task():
        return await asyncio.to_thread(engine.check_dependencies)

    @reactive.effect
    def _autostart():
        # Runs once on session load (no reactive dependencies).
        deps_running.set(True)
        deps_task()

    @reactive.effect
    @reactive.event(input.recheck)
    def _recheck():
        deps_running.set(True)
        deps_task()

    @reactive.effect
    def _ingest_deps():
        if deps_task.status() != "success":
            return
        rows = deps_task.result()
        deps_running.set(False)
        deps_result.set(rows)
        required_ok = all(r["ok"] for r in rows if r["kind"] == "required")
        with reactive.isolate():
            s = dict(app_session())
        s["deps_ok"] = required_ok
        app_session.set(s)

    @render.ui
    def deps_summary():
        if deps_running():
            return ui.div(ui.tags.span("⏳ Checking…", {"class": "fb-spinner"}))
        rows = deps_result()
        if not rows:
            return ui.div()
        missing_req = [r["name"] for r in rows if r["kind"] == "required" and not r["ok"]]
        missing_opt = [r["name"] for r in rows if r["kind"] == "optional" and not r["ok"]]
        if missing_req:
            return ui.div({"class": "fb-error"},
                ui.tags.b("Missing required: "), ", ".join(missing_req),
                " — install these before preparing data.")
        msg = "All required dependencies found."
        if missing_opt:
            msg += f"  ({len(missing_opt)} optional missing — see below.)"
        return ui.div(msg, {"class": "fb-chosen"})

    def _row(r: dict) -> ui.Tag:
        if r["ok"]:
            icon, cls = "✓", "dep-ok"
            detail = r["version"] or "found"
        elif r["kind"] == "required":
            icon, cls = "✗", "dep-bad"
            detail = r["hint"]
        else:
            icon, cls = "⚠", "dep-warn"
            detail = r["hint"]
        return ui.div({"class": f"dep-row {cls}"},
            ui.span(icon, {"class": "dep-icon"}),
            ui.span(r["name"], {"class": "dep-name"}),
            ui.span(detail, {"class": "dep-detail"}),
        )

    @render.ui
    def deps_table():
        rows = deps_result()
        if not rows:
            return ui.div()
        groups = [
            ("Required — Python packages", "required", "python"),
            ("Required — command-line tools", "required", "cli"),
            ("Optional", "optional", None),
        ]
        cards = []
        for title, kind, cat in groups:
            sel = [r for r in rows if r["kind"] == kind and (cat is None or r["category"] == cat)]
            if not sel:
                continue
            cards.append(ui.div({"class": "card"},
                ui.tags.h5(title),
                *[_row(r) for r in sel],
            ))
        return ui.div(*cards)
