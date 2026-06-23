"""
setup_gui/step_priority.py
--------------------------
Step 4 of the setup GUI: configure priority groups (full parity with the CLI
editor setup/s04_priority_groups.py).

The editable configuration is held as one reactive list `rows` of
(group, source, featuretype, status) tuples — exactly what s04 builds and
save_tsv writes — and every operation mutates it via s04's own tested helpers
(exposed as engine.pg_*). Because the pairs table and groups panel are rendered
from `rows` + classify_pair, the "who claims what" preview (precedence,
wildcards) is always live.

Capabilities: select pairs → assign (new/existing group) / exclude / unassign;
add wildcard rules (* source or featuretype); rename / reorder (precedence) /
delete groups; remove individual rules.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from shiny import module, ui, render, reactive

from .filebrowser import file_browser_ui, file_browser_server
from .logbox import log_box_ui, log_lines, spinner, bind_busy_button
from . import engine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config  # noqa: E402

DB_EXTS = (".db",)
NEW_GROUP = "➕ new group…"   # sentinel option in the assign dropdown


def _safe_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


@module.ui
def priority_ui() -> ui.Tag:
    return ui.div(
        ui.div({"class": "card"},
            ui.tags.h5("Step 4 — Priority groups"),
            ui.p("Group related (source, feature type) pairs so each gets its "
                 "own panel in the app, or exclude ones you never want to see. "
                 "Groups apply top-to-bottom — the first match wins, so order "
                 "matters. The database from Step 3 is used automatically.",
                 {"class": "upload-note"}),
            ui.output_ui("db_line"),
        ),
        ui.tags.details(
            ui.tags.summary("Use a different database"),
            file_browser_ui("priodb", "Database (.db)"),
        ),
        ui.tags.details(
            ui.tags.summary("Load rules from an existing priority_groups.tsv"),
            ui.div({"style": "padding: 0.5rem 0"},
                file_browser_ui("priotsv", "Priority groups (.tsv)"),
                ui.input_action_button("import_tsv_btn", "Import rules",
                                       class_="btn btn-secondary btn-sm",
                                       style="margin-top: 0.5rem"),
                ui.output_ui("import_tsv_status"),
            ),
        ),
        ui.hr(style="margin: 6px 0;"),
        ui.div({"class": "card"},
            ui.input_action_button("scan_db_btn", "Scan database",
                                   class_="btn btn-primary w-100"),
            ui.output_text("prio_error"),
            ui.output_ui("scan_status"),
            log_box_ui("scan_logbox"),
            ui.output_ui("prio_progress"),
        ),
        ui.div({"class": "card"},
            ui.input_text("out_tsv", "Output priority_groups.tsv path",
                          value=str(config.PRIORITY_GROUPS_PATH), width="100%"),
            ui.output_ui("out_tsv_warn"),
        ),
        ui.output_ui("pairs_card"),       # Zone A — select pairs + actions
        ui.output_ui("groups_card"),      # Zone C — groups panel (+ rename form)
        ui.output_ui("save_card"),
    )


@module.server
def priority_server(input, output, session, app_session: reactive.Value):
    priodb_sel  = file_browser_server("priodb",  start_dir=None, extensions=DB_EXTS)
    priotsv_sel = file_browser_server("priotsv", start_dir=None, extensions=(".tsv",))
    import_msg  = reactive.Value("")

    pairs         = reactive.Value(None)   # list[(source, featuretype, count)] | None
    rows          = reactive.Value([])     # list[(group, source, featuretype, status)]  SSOT
    group_colors  = reactive.Value({})    # dict[group_name, hex_color]
    prio_error_v  = reactive.Value("")
    saved_msg     = reactive.Value("")
    rename_target = reactive.Value(None)   # group name pending rename, or None
    scan_log      = reactive.Value([])     # streamed scan progress lines
    scan_running  = reactive.Value(False)  # set True synchronously so button disables instantly

    # Plain buffer the background scan appends to; the poll effect mirrors it
    # into scan_log for display (same pattern as Steps 2 & 3).
    _scan_msgs: list[str] = []
    # Tracks last-seen sel_all_{sid} values so the sync effect can detect
    # actual user toggles vs. the False that appears on every re-render.
    _sel_all_prev: dict = {}

    bind_busy_button("scan_db_btn", scan_running, "Scan database", "Scanning…")

    group_action_id   = session.ns("group_action")
    prio_col_change_id = session.ns("prio_col_change")

    # ── DB resolution + scan ────────────────────────────────────────────────
    @reactive.calc
    def resolved_db():
        sel = priodb_sel()
        if sel:
            return sel
        dbp = app_session().get("db_path")
        return Path(dbp) if dbp else None

    @render.ui
    def db_line():
        db = resolved_db()
        if not db:
            return ui.div("No database yet — finish Step 3, or pick a .db below.",
                          {"class": "upload-note"})
        origin = "selected" if priodb_sel() else "from Step 3"
        return ui.div({"class": "fb-chosen"},
            ui.tags.b("Database: "), ui.tags.code(str(db)),
            ui.tags.span(f"  ({origin})", {"class": "upload-note"}))

    # Scan runs off the UI thread (a GROUP BY over a multi-million-row features
    # table can take seconds) so its progress can stream into the log box.
    @reactive.extended_task
    async def scan_task(db_path, tsv_path):
        def step(m: str) -> None:
            _scan_msgs.append(m)
        ps = await asyncio.to_thread(engine.scan_db_pairs, db_path, step)
        existing, existing_colors = await asyncio.to_thread(engine.load_priority_groups, tsv_path)
        step(f"Loaded {len(existing)} existing rule(s) from {tsv_path.name}."
             if existing
             else f"No existing rules in {tsv_path.name} — starting fresh.")
        return {"pairs": ps, "rows": existing, "group_colors": existing_colors}

    @reactive.effect
    @reactive.event(input.scan_db_btn)
    def _start_scan():
        db = resolved_db()
        if not db or not Path(db).exists():
            prio_error_v.set("No valid database. Finish Step 3 or pick a .db file.")
            return
        prio_error_v.set("")
        saved_msg.set("")
        rename_target.set(None)
        _scan_msgs.clear()
        scan_log.set([])
        pairs.set(None)
        group_colors.set({})
        scan_running.set(True)
        scan_task(Path(db), Path(input.out_tsv()))

    @reactive.effect
    def _poll_scan():
        st = scan_task.status()
        scan_log.set(list(_scan_msgs))
        if st == "running":
            reactive.invalidate_later(0.3)
        elif st == "error":
            scan_running.set(False)
            prio_error_v.set(f"Scan failed: {scan_task.error()}")

    @reactive.effect
    def _ingest_scan():
        if scan_task.status() != "success":
            return
        scan_running.set(False)
        res = scan_task.result()
        _sel_all_prev.clear()
        pairs.set(res["pairs"])
        rows.set(res["rows"])
        group_colors.set(res.get("group_colors", {}))

    @reactive.effect
    def _sync_sel_all():
        """When a parent select-all checkbox toggles, select/deselect all children
        in that section.  Uses _sel_all_prev to distinguish real user toggles from
        the False value that appears on every pairs_card re-render."""
        ps = pairs()
        if not ps:
            return
        order, bysrc = _pair_sources(ps)
        for sid, src in enumerate(order):
            try:
                v = input[f"sel_all_{sid}"]()
            except Exception:
                continue
            if v is None:
                continue
            prev_v = _sel_all_prev.get(sid)
            if prev_v is None:
                _sel_all_prev[sid] = v          # first render — record, don't update
            elif v != prev_v:
                _sel_all_prev[sid] = v
                all_idx = [str(gi) for gi, _ in bysrc[src]]
                ui.update_checkbox_group(f"sel_pairs_{sid}",
                                         selected=all_idx if v else [])

    @render.text
    def prio_error():
        return prio_error_v()

    @render.ui
    def scan_status():
        return spinner("Scanning database…") if scan_running() else ui.div()

    @render.ui
    def scan_logbox():
        return log_lines(scan_log())

    @render.ui
    def prio_progress():
        msg = saved_msg()
        return ui.div(msg, {"class": "fb-chosen"}) if msg else ui.div()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _pair_sources(ps):
        """Group pairs by source for the hierarchical table.
        Returns (order, bysrc) where order is sources by total count desc and
        bysrc[src] = [(global_idx, (src, ft, cnt)), …].  global_idx is the index
        into ps, used as the checkbox value so selection maps back regardless of
        which section contains the row."""
        order: list[str] = []
        bysrc: dict[str, list] = {}
        for gi, (s, f, c) in enumerate(ps):
            if s not in bysrc:
                bysrc[s] = []
                order.append(s)
            bysrc[s].append((gi, (s, f, c)))
        order.sort(key=lambda s: sum(cc for _, (_, _, cc) in bysrc[s]), reverse=True)
        return order, bysrc

    def _selected() -> set[tuple[str, str]]:
        ps = pairs() or []
        order, _ = _pair_sources(ps)
        idx: set[str] = set()
        for sid in range(len(order)):
            try:
                v = input[f"sel_pairs_{sid}"]()
            except Exception:
                v = None
            if v:
                idx.update(v)
        return {(ps[int(i)][0], ps[int(i)][1]) for i in idx
                if i.isdigit() and int(i) < len(ps)}

    def _resolve_group() -> str:
        sel = (input.assign_group() or "").strip() if _has("assign_group") else ""
        if sel == NEW_GROUP or not sel:
            return (input.new_group() or "").strip() if _has("new_group") else ""
        return sel

    def _has(name: str) -> bool:
        try:
            input[name]()
            return True
        except Exception:
            return False

    # ── Zone A: pairs table + actions ───────────────────────────────────────
    def _pair_label(src, ft, cnt, current_rows):
        grp, status = engine.classify_pair(src, ft, current_rows)
        if status == "exclude":
            assign, cls = "excluded", "pa-excl"
        elif grp:
            assign, cls = grp, "pa-grp"
        else:
            assign, cls = "—", "pa-none"
        return ui.span({"class": "ft-item"},
            f"{ft}",
            ui.span(f"{cnt:,} · ", {"class": "ft-meta"}),
            ui.span(assign, {"class": cls}),
        )

    @render.ui
    def pairs_card():
        ps = pairs()
        if ps is None:
            return ui.div()
        if not ps:
            return ui.div({"class": "card"},
                ui.tags.h5("Assign pairs"),
                ui.p("No assignable pairs (only structural feature types present).",
                     {"class": "upload-note"}))
        current = rows()
        groups = engine.pg_group_order(current)
        # Build one collapsible src-section per source, each with its own
        # checkbox group keyed by global pair index.  This reuses the Prepare
        # table's proven .src-section / .ft-head / .ftgrp architecture so
        # columns align correctly.
        order, bysrc = _pair_sources(ps)
        sections = []
        for sid, src in enumerate(order):
            items = bysrc[src]
            tot_c = sum(c for _, (_, _, c) in items)
            choices = {str(gi): _pair_label(s, f, c, current)
                       for gi, (s, f, c) in items}
            sections.append(ui.div({"class": "src-section"},
                ui.div({"class": "src-head",
                        "onclick": "this.closest('.src-section').classList.toggle('open')"},
                    ui.span({"class": "src-radio", "onclick": "event.stopPropagation()"},
                        ui.input_checkbox(f"sel_all_{sid}", None, value=False)),
                    ui.span(src, {"class": "src-name"}),
                    ui.span(f"{tot_c:,} feat · {len(items)} types",
                            {"class": "src-meta"}),
                ),
                ui.div({"class": "ftgrp"},
                    ui.input_checkbox_group(f"sel_pairs_{sid}", None, choices=choices),
                ),
            ))
        return ui.div({"class": "card"},
            ui.tags.h5("Assign pairs to groups"),
            # action bar
            ui.div({"class": "prio-actions"},
                ui.input_select("assign_group", None,
                                choices=(groups + [NEW_GROUP]) if groups else [NEW_GROUP],
                                selected=NEW_GROUP),
                ui.input_text("new_group", None, placeholder="new group name"),
                ui.input_action_button("assign_btn", "Assign selected",
                                       class_="btn btn-sm btn-primary"),
                ui.input_action_button("exclude_btn", "Exclude",
                                       class_="btn btn-sm btn-outline-danger"),
                ui.input_action_button("unassign_btn", "Unassign",
                                       class_="btn btn-sm btn-outline-secondary"),
            ),
            *sections,
            # Zone B: wildcard / advanced rule
            ui.tags.details({"class": "prio-adv"},
                ui.tags.summary("Add a wildcard rule (advanced)"),
                ui.p("Use * to match any source or feature type. Added in order; "
                     "first match wins.", {"class": "upload-note"}),
                ui.div({"class": "prio-actions"},
                    ui.input_text("rule_group", None, placeholder="group name"),
                    ui.input_text("rule_source", None, value="*", placeholder="source"),
                    ui.input_text("rule_feat", None, value="*", placeholder="featuretype"),
                    ui.input_select("rule_status", None,
                                    choices=["include", "exclude"], selected="include"),
                    ui.input_action_button("add_rule_btn", "Add rule",
                                           class_="btn btn-sm btn-secondary"),
                ),
            ),
        )

    @reactive.effect
    @reactive.event(input.assign_btn)
    def _assign():
        saved_msg.set("")
        sel = _selected()
        if not sel:
            prio_error_v.set("Select one or more pairs first.")
            return
        g = _resolve_group()
        if not g:
            prio_error_v.set("Choose or name a group to assign to.")
            return
        prio_error_v.set("")
        r = engine.pg_remove_exact(rows(), sel)
        r = engine.pg_insert_for_group(r, g, [(g, s, f, "include") for s, f in sel])
        rows.set(r)

    @reactive.effect
    @reactive.event(input.exclude_btn)
    def _exclude():
        saved_msg.set("")
        sel = _selected()
        if not sel:
            prio_error_v.set("Select one or more pairs first.")
            return
        prio_error_v.set("")
        r = engine.pg_remove_exact(rows(), sel)
        r = r + [("_excluded_", s, f, "exclude") for s, f in sel]
        rows.set(r)

    @reactive.effect
    @reactive.event(input.unassign_btn)
    def _unassign():
        saved_msg.set("")
        sel = _selected()
        if not sel:
            prio_error_v.set("Select one or more pairs first.")
            return
        prio_error_v.set("")
        rows.set(engine.pg_remove_exact(rows(), sel))

    @reactive.effect
    @reactive.event(input.add_rule_btn)
    def _add_rule():
        saved_msg.set("")
        g = (input.rule_group() or "").strip()
        src = (input.rule_source() or "*").strip() or "*"
        ft = (input.rule_feat() or "*").strip() or "*"
        status = input.rule_status()
        if status == "exclude":
            rows.set(rows() + [("_excluded_", src, ft, "exclude")])
            return
        if not g:
            prio_error_v.set("Wildcard include rule needs a group name.")
            return
        prio_error_v.set("")
        rows.set(engine.pg_insert_for_group(rows(), g, [(g, src, ft, "include")]))

    # ── Zone C: groups panel ────────────────────────────────────────────────
    def _gbtn(label, payload: dict, cls="prio-gbtn", confirm_msg: str = ""):
        guard = (f"if(!confirm({json.dumps(confirm_msg)})) return false; "
                 if confirm_msg else "")
        onclick = (f"{guard}"
                   f"Shiny.setInputValue({json.dumps(group_action_id)}, "
                   f"{json.dumps(json.dumps(payload))}, {{priority:'event'}}); "
                   f"return false;")
        return ui.tags.button(label, {"onclick": onclick, "type": "button", "class": cls})

    @render.ui
    def groups_card():
        if pairs() is None:
            return ui.div()
        current = rows()
        order = engine.pg_group_order(current)
        excludes = [r for r in current if r[3] == "exclude"]
        if not order and not excludes:
            return ui.div({"class": "card"},
                ui.tags.h5("Groups"),
                ui.p("No groups yet — select pairs above and assign them.",
                     {"class": "upload-note"}))

        blocks = []
        gc = group_colors()
        for i, g in enumerate(order):
            rules = [r for r in current if r[0] == g and r[3] == "include"]
            cur_color = gc.get(g, "#9E9E9E")
            color_picker = ui.tags.div(
                {"class": "ft-item",
                 "style": "display:inline-flex;flex-direction:column;"},
                ui.tags.span({"class": "dot color-edit-btn",
                              "style": f"background:{cur_color};",
                              "title": "Group color"}),
                ui.tags.div({"class": "swatch-row"},
                    *[ui.tags.span(
                        {"class": "color-swatch" + (
                            " selected" if h.upper() == cur_color.upper() else ""),
                         "style": f"background:{h};",
                         "data-ft": prio_col_change_id,
                         "data-group": g,
                         "data-color": h},
                    ) for h in config.PALETTE],
                ),
            )
            controls = ui.span({"class": "prio-gctl"},
                color_picker,
                _gbtn("▲", {"verb": "up", "group": g}),
                _gbtn("▼", {"verb": "down", "group": g}),
                _gbtn("Rename", {"verb": "rename", "group": g}),
                _gbtn("Delete", {"verb": "delete", "group": g}, cls="prio-gbtn prio-del"),
            )
            rule_rows = [ui.div({"class": "prio-rule"},
                ui.tags.code(f"{s}  /  {f}"),
                _gbtn("✕", {"verb": "remove_rule", "group": g, "source": s, "feat": f},
                      cls="prio-x"),
            ) for (_, s, f, _st) in rules]
            block = ui.div({"class": "prio-group"},
                ui.div({"class": "prio-ghead"},
                    ui.span(f"{i+1}. {g}", {"class": "prio-gname"}), controls),
                *rule_rows)
            blocks.append(block)
            if rename_target() == g:
                blocks.append(ui.div({"class": "prio-rename"},
                    ui.input_text("rename_value", None, value=g,
                                  placeholder="new name"),
                    ui.input_action_button("rename_apply", "Apply",
                                           class_="btn btn-sm btn-primary"),
                    ui.input_action_button("rename_cancel", "Cancel",
                                           class_="btn btn-sm btn-outline-secondary"),
                ))

        excl_block = []
        if excludes:
            excl_block = [ui.div({"class": "prio-group prio-exclblock"},
                ui.div({"class": "prio-ghead"}, ui.span("Excluded", {"class": "prio-gname"})),
                *[ui.div({"class": "prio-rule"},
                    ui.tags.code(f"{s}  /  {f}"),
                    _gbtn("✕", {"verb": "remove_exclude", "source": s, "feat": f},
                          cls="prio-x"),
                  ) for (_, s, f, _st) in excludes])]

        clear_btn = _gbtn(
            "Clear all",
            {"verb": "clear_all"},
            cls="prio-gbtn prio-del",
            confirm_msg="Clear all groups and exclusions?",
        )
        return ui.div({"class": "card"},
            ui.div({"class": "prio-ghead"},
                ui.tags.h5("Groups (top = highest priority)", {"style": "margin:0"}),
                clear_btn,
            ),
            *blocks, *excl_block)

    @reactive.effect
    @reactive.event(input.group_action)
    def _group_action():
        saved_msg.set("")
        try:
            a = json.loads(input.group_action())
        except Exception:
            return
        verb = a.get("verb")
        g = a.get("group")
        current = rows()
        if verb in ("up", "down"):
            order = engine.pg_group_order(current)
            if g in order:
                i = order.index(g)
                j = i - 1 if verb == "up" else i + 1
                if 0 <= j < len(order):
                    order[i], order[j] = order[j], order[i]
                    rows.set(engine.pg_reorder(current, order))
        elif verb == "delete":
            rows.set([r for r in current if r[0] != g or r[3] == "exclude"])
            gc = dict(group_colors())
            gc.pop(g, None)
            group_colors.set(gc)
            if rename_target() == g:
                rename_target.set(None)
        elif verb == "rename":
            rename_target.set(g)
        elif verb == "remove_rule":
            s, f = a.get("source"), a.get("feat")
            rows.set([r for r in current
                      if not (r[0] == g and r[1] == s and r[2] == f and r[3] == "include")])
        elif verb == "remove_exclude":
            s, f = a.get("source"), a.get("feat")
            rows.set([r for r in current
                      if not (r[1] == s and r[2] == f and r[3] == "exclude")])
        elif verb == "clear_all":
            rows.set([])
            group_colors.set({})
            rename_target.set(None)

    @reactive.effect
    @reactive.event(input.rename_apply)
    def _rename_apply():
        saved_msg.set("")
        old = rename_target()
        new = (input.rename_value() or "").strip() if _has("rename_value") else ""
        if old and new and new != old:
            rows.set([(new if grp == old else grp, s, f, st)
                      for (grp, s, f, st) in rows()])
            gc = dict(group_colors())
            if old in gc:
                gc[new] = gc.pop(old)
                group_colors.set(gc)
        rename_target.set(None)

    @reactive.effect
    @reactive.event(input.rename_cancel)
    def _rename_cancel():
        rename_target.set(None)

    @reactive.effect
    @reactive.event(input.prio_col_change)
    def _update_group_colors():
        """Capture swatch selections dispatched via the prio_col_change input."""
        try:
            data = json.loads(input.prio_col_change())
            g     = data.get("group")
            color = data.get("color")
            if g and color:
                gc = dict(group_colors())
                gc[g] = color
                group_colors.set(gc)
        except Exception:
            pass

    # ── Import from existing TSV ────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.import_tsv_btn)
    def _import_tsv():
        import_msg.set("")
        p = priotsv_sel()
        if not p or not p.exists():
            import_msg.set("error:No file selected — use the browser above to pick a .tsv.")
            return
        try:
            loaded, loaded_colors = engine.load_priority_groups(p)
        except Exception as exc:
            import_msg.set(f"error:Could not read {p.name}: {exc}")
            return
        if not loaded:
            import_msg.set("error:File contains no valid rules.")
            return
        rows.set(loaded)
        group_colors.set(loaded_colors)
        saved_msg.set("")
        rename_target.set(None)
        n_groups = len(engine.pg_group_order(loaded))
        n_excl = sum(1 for r in loaded if r[3] == "exclude")
        import_msg.set(f"ok:Loaded {len(loaded)} rule(s) from {p.name}: "
                       f"{n_groups} group(s), {n_excl} excluded.")

    @render.ui
    def import_tsv_status():
        msg = import_msg()
        if not msg:
            return ui.div()
        kind, text = msg.split(":", 1)
        cls = "fb-chosen" if kind == "ok" else "fb-error"
        return ui.div(text, {"class": cls, "style": "margin-top: 0.4rem"})

    # ── Output path overwrite warning ───────────────────────────────────────
    @render.ui
    def out_tsv_warn():
        try:
            out = Path(input.out_tsv()).expanduser()
        except Exception:
            return ui.div()
        if out.exists():
            return ui.div(
                f"⚠ {out.name} already exists — saving will overwrite it. "
                "Change the path above to write a new file.",
                {"class": "fb-warning", "style": "margin-top:6px"},
            )
        return ui.div()

    # ── Save ────────────────────────────────────────────────────────────────
    @render.ui
    def save_card():
        if pairs() is None:
            return ui.div()
        msg = saved_msg()
        children = [ui.input_action_button("save_btn", "Save Priority Groups",
                                            class_="btn btn-success w-100")]
        if msg:
            children.append(ui.div(msg, {"class": "fb-chosen", "style": "margin-top:8px"}))
        return ui.div({"class": "card"}, *children)

    @reactive.effect
    @reactive.event(input.save_btn)
    def _save():
        out = Path(input.out_tsv()).expanduser()
        if not out.parent.exists():
            prio_error_v.set(f"Output folder does not exist: {out.parent}")
            return
        try:
            engine.save_priority_groups(out, rows(), group_colors())
        except Exception as e:
            prio_error_v.set(f"Save failed: {e}")
            return
        prio_error_v.set("")
        n_groups = len(engine.pg_group_order(rows()))
        n_excl = sum(1 for r in rows() if r[3] == "exclude")
        saved_msg.set(f"Saved {len(rows())} rule(s): {n_groups} group(s), "
                      f"{n_excl} excluded → {out}")
        with reactive.isolate():
            s = dict(app_session())
        s["priority_groups_path"] = str(out)
        app_session.set(s)
