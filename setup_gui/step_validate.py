"""
setup_gui/step_validate.py
--------------------------
Step 5 of the setup GUI: validate the finished setup.

Pulls the database (Step 3), FASTA (Step 2) and priority_groups.tsv (Step 4)
from the session, falling back to config defaults, and runs the same sanity
checks as setup/s05_validate.py (via engine.validate_setup): DB opens, FASTA
indexed, DB/FASTA chromosome names overlap, the default region resolves to
features, and priority_groups.tsv parses cleanly. Runs once on load and on a
"Re-validate" button.
"""

import asyncio
import sys
from pathlib import Path

from shiny import module, ui, render, reactive

from .logbox import log_box_ui, log_lines, spinner, bind_busy_button
from . import engine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config  # noqa: E402


@module.ui
def validate_ui() -> ui.Tag:
    return ui.div(
        ui.div({"class": "card"},
            ui.tags.h5("Step 5 — Validate"),
            ui.p("Final sanity check on the database, FASTA and priority groups "
                 "produced by the earlier steps (falling back to the paths in "
                 "config.py).", {"class": "upload-note"}),
            ui.output_ui("inputs_line"),
            ui.input_text("region", "Default region to test (chrom:start-end)",
                          value=config.DEFAULT_REGION, width="100%"),
            ui.input_action_button("revalidate", "Re-validate",
                                   class_="btn btn-primary"),
            ui.output_ui("val_summary"),
            log_box_ui("val_logbox"),
        ),
        ui.output_ui("val_table"),
    )


@module.server
def validate_server(input, output, session, app_session: reactive.Value):
    results   = reactive.Value(None)    # list[dict] | None
    running   = reactive.Value(False)
    val_error = reactive.Value("")
    val_log   = reactive.Value([])
    _val_msgs: list[str] = []

    bind_busy_button("revalidate", running, "Re-validate", "Validating…")

    @reactive.calc
    def res_db():
        return app_session().get("db_path") or str(config.DB_PATH)

    @reactive.calc
    def res_fa():
        return app_session().get("fasta") or str(config.FASTA_PATH)

    @reactive.calc
    def res_pg():
        return app_session().get("priority_groups_path") or str(config.PRIORITY_GROUPS_PATH)

    @reactive.extended_task
    async def validate_task(db, fa, pg, region):
        def step(m: str) -> None:
            _val_msgs.append(m)
        return await asyncio.to_thread(
            engine.validate_setup, Path(db), Path(fa), Path(pg), region, step
        )

    def _run():
        _val_msgs.clear()
        val_log.set([])
        val_error.set("")
        running.set(True)
        validate_task(res_db(), res_fa(), res_pg(), input.region())

    @reactive.effect
    def _autostart():
        with reactive.isolate():
            _run()

    @reactive.effect
    @reactive.event(input.revalidate)
    def _rev():
        _run()

    @reactive.effect
    def _ingest():
        st = validate_task.status()
        val_log.set(list(_val_msgs))
        if st == "running":
            reactive.invalidate_later(0.3)
            return
        if st == "error":
            running.set(False)
            val_error.set(f"Validation failed unexpectedly: {validate_task.error()}")
            return
        if st == "success":
            running.set(False)
            results.set(validate_task.result())

    @render.ui
    def inputs_line():
        s = app_session()
        def line(label, val, key):
            origin = "from setup" if s.get(key) else "default (config)"
            return ui.div(ui.tags.b(label + ": "), ui.tags.code(val),
                          ui.tags.span(f"  ({origin})", {"class": "upload-note"}))
        return ui.div({"class": "val-inputs"},
            line("Database",        res_db(), "db_path"),
            line("FASTA",           res_fa(), "fasta"),
            line("Priority groups", res_pg(), "priority_groups_path"),
        )

    @render.ui
    def val_summary():
        if running():
            return spinner("Validating…")
        err = val_error()
        if err:
            return ui.div(err, {"class": "fb-error"})
        rows = results()
        if not rows:
            return ui.div()
        n_fail = sum(1 for r in rows if not r["ok"])
        n_warn = sum(len(r["warnings"]) for r in rows)
        if n_fail:
            return ui.div({"class": "fb-error"},
                ui.tags.b(f"{n_fail} check(s) failed — review below."))
        msg = "All checks passed — setup looks good."
        if n_warn:
            msg += f"  ({n_warn} warning(s).)"
        return ui.div(msg, {"class": "fb-chosen"})

    @render.ui
    def val_logbox():
        return log_lines(val_log())

    @render.ui
    def val_table():
        rows = results()
        if not rows:
            return ui.div()
        items = []
        for r in rows:
            cls = "dep-ok" if r["ok"] else "dep-bad"
            icon = "✓" if r["ok"] else "✗"
            items.append(ui.div({"class": f"dep-row {cls}"},
                ui.span(icon, {"class": "dep-icon"}),
                ui.span(r["label"], {"class": "dep-name"}),
                ui.span(r["message"], {"class": "dep-detail"}),
            ))
            for w in r["warnings"]:
                items.append(ui.div({"class": "dep-row dep-warn"},
                    ui.span("⚠", {"class": "dep-icon"}),
                    ui.span("", {"class": "dep-name"}),
                    ui.span(w, {"class": "dep-detail"}),
                ))
        return ui.div({"class": "card val-checks"}, ui.tags.h5("Checks"), *items)
