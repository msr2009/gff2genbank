"""
setup_gui/step_build_db.py
--------------------------
Step 3 of the setup GUI: build the gffutils database from the prepared GFF.

Auto-fills the input from Step 2's output (app_session["prepared_gff"]) and a
sensible output DB path, but lets the user pick a different GFF. Runs the build
off the UI thread with streamed progress and a richer completion summary than
the bare CLI (feature total, per-type breakdown, on-disk size, elapsed time).
"""

import asyncio
from pathlib import Path

from shiny import module, ui, render, reactive

from .filebrowser import file_browser_ui, file_browser_server, GFF_EXTS
from .logbox import log_box_ui, log_lines, spinner, bind_busy_button
from . import engine


@module.ui
def build_db_ui() -> ui.Tag:
    return ui.div(
        ui.div({"class": "card"},
            ui.tags.h5("Step 3 — Build database"),
            ui.p("Builds the gffutils SQLite database the app queries. The "
                 "prepared GFF and output path from Step 2 are filled in "
                 "automatically; override the input below if you want to build "
                 "from a different file.",
                 {"class": "upload-note"}),
            ui.output_ui("source_line"),
        ),
        ui.tags.details(
            ui.tags.summary("Choose a different GFF"),
            file_browser_ui("buildgff", "Input GFF"),
        ),
        ui.div({"class": "card"},
            ui.input_text("out_db", "Output database path", value="", width="100%"),
            ui.input_checkbox("force", "Rebuild if the database already exists",
                              value=False),
            ui.input_action_button("build_btn", "Build database",
                                   class_="btn btn-success w-100 mt-2"),
            ui.output_text("build_error"),
            ui.output_ui("build_status"),
            log_box_ui("build_logbox"),
        ),
        ui.output_ui("build_summary"),
    )


@module.server
def build_db_server(input, output, session, app_session: reactive.Value):
    buildgff_sel = file_browser_server("buildgff", start_dir=None, extensions=GFF_EXTS)

    build_running = reactive.Value(False)
    build_result  = reactive.Value(None)   # summary dict | None
    build_error_v = reactive.Value("")
    build_log     = reactive.Value([])
    _build_msgs: list[str] = []

    bind_busy_button("build_btn", build_running, "Build database", "Building…")

    @reactive.calc
    def resolved_gff():
        """The GFF to build from: an explicit picker choice wins, else the
        prepared GFF published by Step 2."""
        sel = buildgff_sel()
        if sel:
            return sel
        prepared = app_session().get("prepared_gff")
        return Path(prepared) if prepared else None

    @reactive.calc
    def source_origin():
        if buildgff_sel():
            return "selected"
        return "step2" if app_session().get("prepared_gff") else None

    @reactive.effect
    @reactive.event(resolved_gff)
    def _autofill_out():
        gff = resolved_gff()
        if gff:
            ui.update_text("out_db", value=str(gff) + ".db")

    @render.ui
    def source_line():
        gff = resolved_gff()
        if not gff:
            return ui.div("No prepared GFF yet — finish Step 2, or pick a GFF "
                          "below.", {"class": "upload-note"})
        origin = ("from Step 2" if source_origin() == "step2" else "selected")
        return ui.div({"class": "fb-chosen"},
            ui.tags.b("Input GFF: "), ui.tags.code(str(gff)),
            ui.tags.span(f"  ({origin})", {"class": "upload-note"}))

    # ── Build task ──────────────────────────────────────────────────────────
    @reactive.extended_task
    async def build_task(gff, db, force, total):
        def step(m: str) -> None:
            _build_msgs.append(m)
        return await asyncio.to_thread(engine.build_database, gff, db, force, step, total)

    @reactive.effect
    @reactive.event(input.build_btn)
    def _start_build():
        gff = resolved_gff()
        if not gff or not Path(gff).exists():
            build_error_v.set("No valid input GFF. Finish Step 2 or pick a file.")
            return
        out_raw = (input.out_db() or "").strip()
        if not out_raw:
            build_error_v.set("Enter an output database path.")
            return
        db = Path(out_raw).expanduser()
        if not db.parent.exists():
            build_error_v.set(f"Output folder does not exist: {db.parent}")
            return
        if db.exists() and not input.force():
            build_error_v.set("Database already exists — tick 'Rebuild' to overwrite.")
            return
        build_error_v.set("")
        # Reuse the exact feature count from Step 2 when building its output, so
        # the build can report % without an extra counting pass.
        total = None
        sess = app_session()
        if sess.get("prepared_gff") and str(gff) == str(sess["prepared_gff"]):
            total = sess.get("prepared_features")
        _build_msgs.clear()
        build_log.set([])
        build_result.set(None)
        build_running.set(True)
        build_task(Path(gff), db, bool(input.force()), total)

    @reactive.effect
    def _ingest_build():
        st = build_task.status()
        build_log.set(list(_build_msgs))
        if st == "running":
            reactive.invalidate_later(0.3)
            return
        if st == "error":
            build_running.set(False)
            build_error_v.set(str(build_task.error()))
            return
        if st == "success":
            build_running.set(False)
            res = build_task.result()
            build_result.set(res)
            with reactive.isolate():
                s = dict(app_session())
            s["db_path"] = str(res["db_path"])
            app_session.set(s)

    @render.text
    def build_error():
        return build_error_v()

    @render.ui
    def build_status():
        return spinner("Building database… (large GFFs take minutes)") if build_running() else ui.div()

    @render.ui
    def build_logbox():
        return log_lines(build_log())

    @render.ui
    def build_summary():
        res = build_result()
        if not res:
            return ui.div()
        rows = res["by_type"][:15]
        table = ui.tags.table({"class": "bd-summary"},
            ui.tags.tbody(
                *[ui.tags.tr(ui.tags.td(ft), ui.tags.td(f"{n:,}", {"class": "bd-num"}))
                  for ft, n in rows]
            ),
        )
        more = (ui.tags.div(f"…and {len(res['by_type']) - 15} more types",
                            {"class": "upload-note"})
                if len(res["by_type"]) > 15 else ui.div())
        return ui.div({"class": "card"},
            ui.tags.h5("✓ Database built"),
            ui.div({"class": "fb-chosen"},
                ui.tags.code(str(res["db_path"])), ui.tags.br(),
                f"{res['total']:,} features  •  {res['size']/1e6:.1f} MB  •  "
                f"{engine.fmt_duration(res['elapsed'])}"),
            ui.tags.h5("Feature types", style="margin-top:10px;"),
            table, more,
            ui.tags.small("Next: configure priority groups (Step 4), or point "
                          "the app at this database.", {"class": "upload-note"}),
        )
