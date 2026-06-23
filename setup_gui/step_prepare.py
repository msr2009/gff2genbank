"""
setup_gui/step_prepare.py
-------------------------
Step 2 of the setup GUI: prepare a GFF for database building.

The friendly version of `prepare_gff.py`:
  - pick input GFF(s), FASTA, optional VCF with a local file browser,
  - SCAN the inputs to show every (source, feature type) pair with its feature
    count and estimated output size, then select which to KEEP (trim to a
    manageable size — the original reason this step existed),
  - resolve any chromosome-name mismatch inline via dropdowns (instead of the
    CLI's write-a-template-and-rerun round trip),
  - run the merge/filter/sort off the UI thread with streamed progress.

All real work is delegated to setup_gui/engine.py (which wraps prepare_gff.py).
"""

import asyncio
import sys
from pathlib import Path

from shiny import module, ui, render, reactive

from .logbox import log_box_ui, log_lines, spinner, bind_busy_button
from .filebrowser import (
    file_browser_ui, file_browser_server,
    GFF_EXTS, FASTA_EXTS, VCF_EXTS,
)
from . import engine

# project-root config (DATA_DIR)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config  # noqa: E402


# Feature types the browse app requires to be present in the prepared GFF.
# Mirrors data.py's transcript_types and genbank.py's rendering logic.
_TRANSCRIPT_TYPES = frozenset({
    "mRNA", "ncRNA", "pseudogenic_transcript",
    "piRNA", "lincRNA", "pre_miRNA", "miRNA",
    "snoRNA", "snRNA", "rRNA", "tRNA",
})
_BLOCK_TYPES = frozenset({"CDS", "exon"})


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@module.ui
def prepare_gff_ui() -> ui.Tag:
    return ui.div(
        ui.div({"class": "card"},
            ui.tags.h5("Step 2 — Prepare GFF"),
            ui.p(
                "Merge, filter, and normalise your annotation file(s) into a "
                "single prepared GFF3 ready for database building. Scan the "
                "inputs to see what's inside, keep only the feature types you "
                "need (to trim the file to a manageable size), and fix any "
                "chromosome-name mismatch without leaving this page.",
                {"class": "upload-note"},
            ),
        ),

        # ── Inputs ──────────────────────────────────────────────────────────
        file_browser_ui("gff", "Input GFF / GTF  (add one or more)"),
        ui.div({"class": "card"},
            ui.div({"class": "fb-pathrow"},
                ui.input_action_button("add_gff", "➕ Add selected GFF",
                                        class_="btn btn-sm btn-primary"),
                ui.input_action_button("clear_gff", "Clear list",
                                        class_="btn btn-sm btn-outline-secondary"),
            ),
            ui.output_ui("gff_list"),
        ),
        file_browser_ui("fasta", "Genome FASTA"),
        file_browser_ui("vcf", "VCF of variants  (optional)"),

        # ── Scan ────────────────────────────────────────────────────────────
        ui.div({"class": "card"},
            ui.tags.h5("Analyse inputs"),
            ui.input_action_button("scan_btn", "Scan input file(s)",
                                    class_="btn btn-primary w-100"),
            ui.output_text("scan_error"),
            ui.output_ui("scan_status"),
            log_box_ui("scan_logbox"),
        ),

        # ── Filter table ────────────────────────────────────────────────────
        ui.output_ui("filter_card"),

        # ── Chromosome mapping (only when mismatch) ─────────────────────────
        ui.output_ui("chrom_map_editor"),

        # ── Output + run ────────────────────────────────────────────────────
        ui.div({"class": "card"},
            ui.tags.h5("Output & run"),
            ui.input_text("out_path", "Output prepared GFF path",
                          value=str(config.DATA_DIR / "prepared.gff3.gz"),
                          width="100%"),
            ui.input_action_button("prepare_btn", "Prepare GFF",
                                    class_="btn btn-success w-100 mt-2"),
            ui.output_text("prepare_error"),
            ui.output_ui("prepare_status"),
            log_box_ui("prepare_logbox"),
            ui.output_ui("prepare_result_ui"),
        ),
    )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

@module.server
def prepare_gff_server(input, output, session, app_session: reactive.Value):
    # File browsers (each its own namespace) → reactive selected Path
    gff_sel   = file_browser_server("gff",   start_dir=config.DATA_DIR, extensions=GFF_EXTS)
    fasta_sel = file_browser_server("fasta", start_dir=config.DATA_DIR, extensions=FASTA_EXTS)
    vcf_sel   = file_browser_server("vcf",   start_dir=config.DATA_DIR, extensions=VCF_EXTS)

    # State
    gff_paths    = reactive.Value([])      # list[Path] — confirmed inputs
    pair_stats   = reactive.Value(None)    # list[dict] | None — scan result
    fasta_names  = reactive.Value([])      # list[str]
    source_names = reactive.Value([])      # list[str]
    mismatches   = reactive.Value(set())   # set[str]
    final_gffs   = reactive.Value([])      # list[Path] — validated/converted
    temps        = reactive.Value([])      # list[Path] — AGAT temp files
    scan_err     = reactive.Value("")
    prep_error   = reactive.Value("")
    scan_log     = reactive.Value([])
    prep_log     = reactive.Value([])
    scan_running = reactive.Value(False)   # set True synchronously so button disables instantly
    prep_running = reactive.Value(False)   # spinner / disable flag for prepare
    prep_result  = reactive.Value(None)    # {"out_path", "n_written"} | None

    # Plain (non-reactive) message buffers the background tasks append to;
    # the polling effects copy them into the reactive logs for display.
    _scan_msgs: list[str] = []
    _prep_msgs: list[str] = []

    # Busy-button wiring — disables + animates + relabels each trigger while running.
    bind_busy_button("scan_btn",    scan_running, "Scan input file(s)", "Scanning…")
    bind_busy_button("prepare_btn", prep_running, "Prepare GFF",        "Preparing…")

    # Auto-clear scan_err when the user changes inputs so stale errors don't linger.
    @reactive.effect
    def _clear_scan_err():
        _ = gff_paths()   # subscribe to GFF list
        _ = fasta_sel()   # subscribe to FASTA selection
        scan_err.set("")

    # Auto-clear prep_error when a new scan lands (new scan = new filter options).
    @reactive.effect
    def _clear_prep_err_on_scan():
        _ = pair_stats()
        prep_error.set("")

    # Authoritative selection state — single source of truth for both the
    # selection summary and the keep-set passed to prepare.
    # Shape: {"modes": {sid: bool}, "keep": {sid: set[str_global_idx]}}
    # None until first scan lands; seeded wholesale on every re-scan so stale
    # sids (which remap when scan results change) never survive.
    sel_state: reactive.Value = reactive.Value(None)

    # ── GFF list management ─────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.add_gff)
    def _add_gff():
        p = gff_sel()
        if p and p not in gff_paths():
            gff_paths.set(gff_paths() + [p])

    @reactive.effect
    @reactive.event(input.clear_gff)
    def _clear_gff():
        gff_paths.set([])

    @render.ui
    def gff_list():
        ps = gff_paths()
        if not ps:
            return ui.div("No GFF files added yet.", {"class": "upload-note"})
        return ui.tags.ul(
            [ui.tags.li(ui.tags.code(str(p))) for p in ps],
            {"class": "fb-filelist"},
        )

    # ── Scan task (validate/convert → scan pairs → chrom info) ──────────────
    @reactive.extended_task
    async def scan_task(gffs, fasta, vcf):
        def step(m: str) -> None:
            _scan_msgs.append(m)
        final, tmp = await asyncio.to_thread(engine.validate_and_convert, gffs, step)
        stats, gff_chroms = await asyncio.to_thread(engine.scan_pairs, final, step)
        fnames, snames, mis = await asyncio.to_thread(
            engine.resolve_chroms, gff_chroms, fasta, vcf, step
        )
        return {
            "pair_stats": stats, "fasta_names": fnames, "source_names": snames,
            "mismatches": mis, "final_gffs": final, "temps": tmp,
        }

    @reactive.effect
    @reactive.event(input.scan_btn)
    def _start_scan():
        if not gff_paths():
            scan_err.set("Add at least one GFF file first.")
            return
        if not fasta_sel():
            scan_err.set("Select a genome FASTA first.")
            return
        scan_err.set("")
        _scan_msgs.clear()
        scan_log.set([])
        pair_stats.set(None)
        scan_running.set(True)
        scan_task(gff_paths(), fasta_sel(), vcf_sel())

    @reactive.effect
    def _poll_scan():
        st = scan_task.status()
        scan_log.set(list(_scan_msgs))
        if st == "running":
            reactive.invalidate_later(0.3)
        elif st == "error":
            scan_running.set(False)

    @reactive.effect
    def _ingest_scan():
        if scan_task.status() != "success":
            return
        scan_running.set(False)
        res = scan_task.result()
        # Seed authoritative selection state wholesale so stale sids never survive
        # a re-scan (sids remap whenever the source count / order changes).
        stats = res["pair_stats"]
        order, bysrc = _sources(stats)
        sel_state.set({
            "modes": {sid: True for sid in range(len(order))},
            "keep":  {sid: {str(gi) for gi, _ in bysrc[src]}
                      for sid, src in enumerate(order)},
        })
        pair_stats.set(stats)
        fasta_names.set(res["fasta_names"])
        source_names.set(res["source_names"])
        mismatches.set(res["mismatches"])
        final_gffs.set(res["final_gffs"])
        temps.set(res["temps"])

    @render.text
    def scan_error():
        return scan_err()

    @render.ui
    def scan_status():
        if scan_running():
            return spinner("Scanning… (large files may take a moment)")
        if scan_task.status() == "error":
            return ui.div(str(scan_task.error()), {"class": "fb-error"})
        return ui.div()

    @render.ui
    def scan_logbox():
        return log_lines(scan_log())

    # ── Filter selector (hierarchical: source ▸ feature types) ──────────────
    # 200+ (source, featuretype) pairs are unusable as a flat list, so we group
    # by source: each source is a collapsible section with its feature types inside.
    def _sources(stats):
        """Canonical (stable) source order = count desc. The position index is a
        STABLE source id (sid) used for input names, so changing the *display*
        sort never remaps which source an input belongs to.
        Returns (sources_by_sid, {source: [(global_idx, row), …]})."""
        order: list[str] = []
        bysrc: dict[str, list] = {}
        for gi, r in enumerate(stats):
            s = r["source"]
            if s not in bysrc:
                bysrc[s] = []
                order.append(s)
            bysrc[s].append((gi, r))
        order.sort(key=lambda s: sum(rr["count"] for _, rr in bysrc[s]),
                   reverse=True)
        return order, bysrc

    def _ex_title(r: dict) -> str:
        """Hover-tooltip text: a few example column-9 attribute strings collected
        during the scan, so the user can see what a feature type actually is."""
        ex = r.get("examples") or []
        return ("Examples (col 9):\n" + "\n".join(f"• {e}" for e in ex)) if ex else ""

    def _ft_label(r: dict) -> ui.Tag:
        attrs = {"class": "ft-row"}
        t = _ex_title(r)
        if t:
            attrs["title"] = t
        return ui.span(attrs,
            ui.span(r["featuretype"], {"class": "ft-name"}),
            ui.span(f'{r["count"]:,}', {"class": "ft-cnt"}),
            ui.span(f'{r["bytes"]/1e6:.2f} MB', {"class": "ft-mb"}),
        )

    def _get(name, default):
        try:
            v = input[name]()
            return v if v is not None else default
        except Exception:
            return default

    def _keep_pairs() -> set[tuple[str, str]]:
        """Keep set derived from the authoritative sel_state — never from raw widget reads.
        Both selection_summary and _start_prepare call this, so they always agree."""
        stats = pair_stats() or []
        st = sel_state()
        if not stats or st is None:
            return set()
        keep: set[tuple[str, str]] = set()
        for sid, kept_idx in st["keep"].items():
            if not st["modes"].get(sid, True):
                continue
            for i in kept_idx:
                if i.isdigit() and int(i) < len(stats):
                    keep.add((stats[int(i)]["source"], stats[int(i)]["featuretype"]))
        return keep

    def _selection_issues() -> list[str]:
        """Return a list of problems with the current selection that would
        cause the browse app to silently lose annotations."""
        ftypes = {ft for _, ft in _keep_pairs()}
        issues = []
        if not (ftypes & _TRANSCRIPT_TYPES):
            issues.append(
                "No transcript type selected (mRNA, ncRNA, …). "
                "The transcript track will be empty and any selected "
                "CDS/exon features will be invisible in the browse app."
            )
        if not (ftypes & _BLOCK_TYPES):
            issues.append(
                "No CDS or exon type selected. "
                "Transcripts will render as featureless spans."
            )
        return issues

    @render.text
    def selection_summary():
        stats = pair_stats()
        if not stats:
            return ""
        keep = _keep_pairs()
        kept = [r for r in stats if (r["source"], r["featuretype"]) in keep]
        kc = sum(r["count"] for r in kept)
        kmb = sum(r["bytes"] for r in kept) / 1e6
        tc = sum(r["count"] for r in stats)
        tmb = sum(r["bytes"] for r in stats) / 1e6
        return (f"Keeping {kc:,} of {tc:,} features   •   "
                f"~{kmb:.1f} of {tmb:.1f} MB   "
                f"({len(kept)} of {len(stats)} type pairs)")

    @render.ui
    def filter_card():
        stats = pair_stats()
        if not stats:
            return ui.div()
        order, bysrc = _sources(stats)          # sid = index in this list
        try:
            sort_mode = input.sort_mode() or "name"
        except Exception:
            sort_mode = "name"

        disp = list(enumerate(order))           # [(sid, src), …]
        if sort_mode == "name":
            disp = sorted(disp, key=lambda t: t[1].lower())

        st = sel_state() or {"modes": {}, "keep": {}}
        sections = []
        with reactive.isolate():                # insulate from transient widget values
            for sid, src in disp:
                items = bysrc[src]
                if sort_mode == "name":
                    items = sorted(items, key=lambda gr: gr[1]["featuretype"].lower())
                tot_c = sum(r["count"] for _, r in items)
                tot_mb = sum(r["bytes"] for _, r in items) / 1e6
                choices = {str(gi): _ft_label(r) for gi, r in items}
                all_idx = [str(gi) for gi, _ in items]
                cur_sel  = sorted(st["keep"].get(sid, set(all_idx)))
                cur_mode = bool(st["modes"].get(sid, True))

                head_children = [
                    ui.span({"class": "src-radio", "onclick": "event.stopPropagation()"},
                        ui.input_checkbox(f"mode_{sid}", "keep", value=cur_mode)),
                    ui.span(src, {"class": "src-name"}),
                    ui.span(f"{tot_c:,} feat · {tot_mb:.1f} MB · {len(items)} types",
                            {"class": "src-meta"}),
                ]

                body = [
                    ui.div({"class": "ft-head"},
                        ui.span("feature type", {"class": "ft-name"}),
                        ui.span("count", {"class": "ft-cnt"}),
                        ui.span("size", {"class": "ft-mb"})),
                    ui.div({"class": "ftgrp"},
                        ui.input_checkbox_group(f"keep_{sid}", None,
                                                choices=choices, selected=cur_sel)),
                ]

                sections.append(ui.div({"class": "src-section"},
                    ui.div({"class": "src-head",
                            "onclick": "this.closest('.src-section').classList.toggle('open')"},
                        *head_children),
                    *body,
                ))

        return ui.div({"class": "card"},
            ui.tags.h5("Choose feature types to keep"),
            ui.p("Tick a source's checkbox to keep it, untick to drop it. "
                 "Expand a source to refine which feature types within it to keep.",
                 {"class": "upload-note"}),
            ui.div({"class": "prio-actions"},
                ui.span("Sort:", {"class": "upload-note"}),
                ui.input_radio_buttons("sort_mode", None,
                                       {"name": "name", "count": "count"},
                                       selected=sort_mode, inline=True),
                ui.input_action_button("keep_all_btn", "Keep all",
                                       class_="btn btn-sm btn-outline-secondary"),
                ui.input_action_button("drop_all_btn", "Drop all",
                                       class_="btn btn-sm btn-outline-secondary"),
                ui.span(f"{len(order)} sources · {len(stats)} type pairs",
                        {"class": "upload-note"}),
            ),
            *sections,
            ui.div(ui.output_text("selection_summary"), {"class": "fb-total"}),
            ui.output_ui("selection_warning"),
        )

    @render.ui
    def selection_warning():
        if not pair_stats():
            return ui.div()
        issues = _selection_issues()
        if not issues:
            return ui.div()
        return ui.div({"class": "fb-warning"},
            ui.tags.b("⚠ Missing required annotation types:"),
            ui.tags.ul(*[ui.tags.li(i) for i in issues]),
        )

    @reactive.effect
    def _reconcile_selection():
        """Single reconcile effect: reads all dynamic mode_{sid}/keep_{sid} widgets
        and writes changes into sel_state (the authoritative kept-set).

        sel_state is read under isolate() so this effect depends only on widget
        changes — not on its own writes — avoiding the read+write self-loop freeze.

        Key invariant: an untouched widget (input returns None/raises) keeps the
        previous state value; an explicitly-emptied widget (returns ()) is honored.
        This kills the None-vs-() asymmetry that caused summary/prepare disagreement.
        """
        stats = pair_stats()
        if not stats:
            return
        order, bysrc = _sources(stats)
        with reactive.isolate():
            st = sel_state() or {"modes": {}, "keep": {}}
        new_modes: dict[int, bool]     = {}
        new_keep:  dict[int, set[str]] = {}
        changed = False
        for sid, src in enumerate(order):
            all_idx   = {str(gi) for gi, _ in bysrc[src]}
            prev_mode = st["modes"].get(sid, True)
            prev_keep = st["keep"].get(sid, set(all_idx))
            mode = bool(_get(f"mode_{sid}", prev_mode))
            raw  = _get(f"keep_{sid}", None)   # None → untouched; () → explicitly empty
            widget_keep = (set(raw) & all_idx) if raw is not None else set(prev_keep)
            choices = {str(gi): _ft_label(r) for gi, r in bysrc[src]}
            if mode and not prev_mode:          # source re-added → tick all
                widget_keep = set(all_idx)
                ui.update_checkbox_group(f"keep_{sid}", choices=choices,
                                         selected=list(all_idx))
            elif (not mode) and prev_mode:      # source dropped → untick all
                widget_keep = set()
                ui.update_checkbox_group(f"keep_{sid}", choices=choices, selected=[])
            new_modes[sid] = mode
            new_keep[sid]  = widget_keep
            if mode != prev_mode or widget_keep != prev_keep:
                changed = True
        if changed:
            sel_state.set({"modes": new_modes, "keep": new_keep})

    @reactive.effect
    @reactive.event(input.keep_all_btn)
    def _keep_all():
        stats = pair_stats()
        if not stats:
            return
        order, bysrc = _sources(stats)
        sel_state.set({
            "modes": {sid: True for sid in range(len(order))},
            "keep":  {sid: {str(gi) for gi, _ in bysrc[src]}
                      for sid, src in enumerate(order)},
        })
        for sid, src in enumerate(order):
            ui.update_checkbox(f"mode_{sid}", value=True)
            ui.update_checkbox_group(f"keep_{sid}",
                choices={str(gi): _ft_label(r) for gi, r in bysrc[src]},
                selected=[str(gi) for gi, _ in bysrc[src]])

    @reactive.effect
    @reactive.event(input.drop_all_btn)
    def _drop_all():
        stats = pair_stats()
        if not stats:
            return
        order, bysrc = _sources(stats)
        sel_state.set({
            "modes": {sid: False for sid in range(len(order))},
            "keep":  {sid: set() for sid in range(len(order))},
        })
        for sid, src in enumerate(order):
            ui.update_checkbox(f"mode_{sid}", value=False)
            ui.update_checkbox_group(f"keep_{sid}",
                choices={str(gi): _ft_label(r) for gi, r in bysrc[src]},
                selected=[])

    # ── Chromosome mapping editor (inline; no template file) ────────────────
    @render.ui
    def chrom_map_editor():
        mis = sorted(mismatches())
        if not mis:
            return ui.div()
        fnames = fasta_names()
        rows = [ui.p(
            ui.tags.b("Chromosome names need mapping. "),
            "These names appear in your GFF/VCF but not in the FASTA. Map each "
            "to a FASTA sequence so coordinates line up.",
            {"class": "upload-note"},
        )]
        for i, name in enumerate(mis):
            rows.append(ui.div({"class": "ft-row"},
                ui.tags.code(name),
                ui.tags.span(" → ", {"class": "fb-arrow"}),
                ui.input_select(f"cmap_{i}", None,
                                choices=["(choose)"] + list(fnames),
                                selected="(choose)"),
            ))
        return ui.div({"class": "card"},
            ui.tags.h5("Chromosome mapping"), *rows,
            ui.output_ui("chrom_dup_warning"))

    def _dup_targets() -> dict[str, list[str]]:
        """FASTA sequence -> [source names] for any FASTA sequence chosen by more
        than one dropdown. Two source names mapping to the same sequence may be
        legitimate (aliases), so we warn rather than forbid the choice — but the
        run is blocked until it's resolved (see _start_prepare)."""
        picks: dict[str, list[str]] = {}
        for i, name in enumerate(sorted(mismatches())):
            try:
                v = input[f"cmap_{i}"]()
            except Exception:
                v = "(choose)"
            if v and v != "(choose)":
                picks.setdefault(v, []).append(name)
        return {fa: names for fa, names in picks.items() if len(names) > 1}

    @render.ui
    def chrom_dup_warning():
        dups = _dup_targets()
        if not dups:
            return ui.div()
        return ui.div({"class": "fb-warning"},
            ui.tags.b("⚠ The same FASTA sequence is mapped more than once:"),
            ui.tags.ul([ui.tags.li(f"{fa} ← {', '.join(names)}")
                        for fa, names in dups.items()]),
            ui.tags.small("Each FASTA sequence may be the target of only one "
                          "mapping. Resolve this before preparing."),
        )

    def _build_chrom_map() -> tuple[dict[str, str], list[str]]:
        """Return (chrom_map, unmapped_names). Identity for matches; user
        selection for mismatches."""
        cmap = {n: n for n in source_names()}
        unmapped: list[str] = []
        for i, name in enumerate(sorted(mismatches())):
            try:
                val = input[f"cmap_{i}"]()
            except Exception:
                val = "(choose)"
            if val and val != "(choose)":
                cmap[name] = val
            else:
                unmapped.append(name)
        return cmap, unmapped

    # ── Prepare task ────────────────────────────────────────────────────────
    # NOTE: outputs are driven by plain reactive.Values that the _ingest effect
    # sets from the task's status — NOT by reading prepare_task.result()/status()
    # directly inside the renderers. Reading an extended_task's result straight
    # from a render function did not reliably flush the completed state to the
    # browser here; mirroring it into reactive.Values (as the scan step does)
    # makes the success/error state propagate to the client correctly.
    @reactive.extended_task
    async def prepare_task(gffs, fasta, vcf, out, cmap, keep, tmps):
        def step(m: str) -> None:
            _prep_msgs.append(m)
        res = await asyncio.to_thread(
            engine.run_prepare, gffs, fasta, vcf, out, cmap, keep, step
        )
        await asyncio.to_thread(engine.cleanup_temps, tmps, step)
        return res

    @reactive.effect
    @reactive.event(input.prepare_btn)
    def _start_prepare():
        if not final_gffs():
            prep_error.set("Scan your input file(s) first.")
            return
        keep = _keep_pairs()
        if not keep:
            prep_error.set("Select at least one feature type to keep.")
            return
        issues = _selection_issues()
        if issues:
            prep_error.set("Cannot prepare: " + " ".join(issues))
            return
        cmap, unmapped = _build_chrom_map()
        if unmapped:
            prep_error.set(
                "Map every flagged chromosome name first: "
                + ", ".join(unmapped)
            )
            return
        dups = _dup_targets()
        if dups:
            prep_error.set(
                "Each FASTA sequence may be mapped only once. Duplicated: "
                + "; ".join(f"{fa} ← {', '.join(ns)}" for fa, ns in dups.items())
            )
            return
        out = Path(input.out_path()).expanduser()
        if not out.parent.exists():
            prep_error.set(f"Output folder does not exist: {out.parent}")
            return
        prep_error.set("")
        _prep_msgs.clear()
        prep_result.set(None)
        prep_log.set([])
        prep_running.set(True)
        prepare_task(final_gffs(), fasta_sel(), vcf_sel(), out, cmap, keep, temps())

    @reactive.effect
    def _ingest_prepare():
        st = prepare_task.status()
        prep_log.set(list(_prep_msgs))
        if st == "running":
            reactive.invalidate_later(0.3)   # stream the log while it runs
            return
        if st == "error":
            prep_running.set(False)
            prep_error.set(str(prepare_task.error()))
            return
        if st == "success":
            prep_running.set(False)
            res = prepare_task.result()
            prep_result.set(res)
            # Publish for later steps. Read app_session under isolate() so this
            # effect does not depend on (and thus re-trigger itself via) the very
            # value it writes — that read/write cycle otherwise spins forever.
            with reactive.isolate():
                s = dict(app_session())
            s["prepared_gff"] = str(res["out_path"])
            s["prepared_features"] = res["n_written"]   # exact build %-denominator
            if fasta_sel():
                s["fasta"] = str(fasta_sel())
            app_session.set(s)

    @render.text
    def prepare_error():
        return prep_error()

    @render.ui
    def prepare_status():
        return spinner("Preparing…") if prep_running() else ui.div()

    @render.ui
    def prepare_logbox():
        return log_lines(prep_log())

    @render.ui
    def prepare_result_ui():
        res = prep_result()
        if not res:
            return ui.div()
        # Self-contained nav button: clicks the Step 3 tab and scrolls to top, so
        # the user needn't scroll all the way back up this long page.
        goto_js = (
            "var ls=document.querySelectorAll('.nav-tabs .nav-link');"
            "for(var i=0;i<ls.length;i++){"
            "if(ls[i].textContent.indexOf('Build database')>-1){ls[i].click();break;}}"
            "window.scrollTo({top:0,behavior:'smooth'});"
        )
        return ui.div({"class": "fb-chosen"},
            ui.tags.b("✓ Prepared: "), ui.tags.code(str(res["out_path"])),
            ui.tags.br(),
            f"{res['n_written']:,} feature lines written.",
            ui.tags.br(),
            ui.tags.button("Continue to Step 3 — Build database →",
                           {"type": "button", "onclick": goto_js,
                            "class": "btn btn-success mt-2"}),
        )
