"""
setup_gui/ui.py
---------------
Top-level UI for the standalone local setup app (setup_app.py).

Reuses the main app's CSS so the two look identical, and lays the five
pipeline steps out as tabs.  Only Step 2 (Prepare GFF) is implemented today;
the others are placeholders that later modules slot into.
"""

import sys
from pathlib import Path

from shiny import ui

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ui import CSS as APP_CSS  # reuse the main app's look  # noqa: E402
from .step_deps import deps_ui  # noqa: E402
from .step_prepare import prepare_gff_ui  # noqa: E402
from .step_build_db import build_db_ui  # noqa: E402
from .step_priority import priority_ui  # noqa: E402
from .step_validate import validate_ui  # noqa: E402

SETUP_VERSION = "v0.1"

# File-browser / setup-specific styling layered on top of the shared app CSS.
SETUP_CSS = APP_CSS + """
.fb-card { }
.fb-pathrow { display:flex; gap:6px; align-items:center; margin-bottom:6px; }
.fb-pathrow .shiny-input-container { flex:1 1 auto; margin-bottom:0; }
.fb-crumb {
    font-family: monospace; font-size:0.78em; color:#2980b9;
    background:#eef4fb; border-radius:5px; padding:4px 8px; margin-bottom:6px;
    word-break: break-all;
}
.fb-listing {
    border:1px solid #e0e0e0; border-radius:6px; background:#fafbfc;
    max-height:220px; overflow-y:auto; padding:4px 0;
}
.fb-entry {
    display:block; padding:3px 12px; font-size:0.85em;
    color:#34495e; text-decoration:none; cursor:pointer;
}
.fb-entry:hover { background:#e8f0fe; color:#1a5fa8; text-decoration:none; }
.fb-icon { display:inline-block; width:1.2em; }
.fb-empty { padding:6px 12px; font-size:0.82em; color:#999; font-style:italic; }
.fb-chosen {
    font-size:0.85em; color:#1a8a4a; margin-top:6px;
    background:#eafaf0; border-radius:5px; padding:5px 8px;
}
.fb-error { color:#c0392b; font-size:0.85em; margin-top:6px; white-space:pre-wrap; }
.fb-warning {
    color:#8a6d00; font-size:0.85em; margin-top:8px;
    background:#fff8e1; border:1px solid #f0d98c; border-radius:5px; padding:6px 9px;
}
.fb-warning ul { margin:4px 0 4px 18px; padding:0; }
/* .fb-spinner wraps the animated circle (.fb-spin) + label text */
.fb-spinner { display:inline-flex; align-items:center; gap:6px; color:#2980b9; font-size:0.86em; margin:4px 0; }
/* CSS-animated circle used inside buttons (via update_action_button icon=) and status lines */
.fb-spin {
    display:inline-block; width:0.85em; height:0.85em; vertical-align:-0.1em; flex-shrink:0;
    border:2px solid #cfe2f3; border-top-color:#2980b9; border-radius:50%;
    animation:fb-spin 0.7s linear infinite;
}
@keyframes fb-spin { to { transform:rotate(360deg); } }
/* Also animate the icon slot inside a disabled action button */
.btn .fb-spin { border-color:#cfe2f3; border-top-color:currentColor; }
.fb-filelist { margin:6px 0 0; padding-left:18px; font-size:0.82em; }
.fb-filelist code { font-size:0.95em; }
.fb-arrow { color:#888; margin:0 4px; }
.fb-total {
    font-family: monospace; font-size:0.9em; color:#2c3e50; font-weight:600;
    margin-top:8px; padding:6px 8px; background:#f4f8fb; border-radius:5px;
}
/* ── Step 1 dependency rows ─────────────────────────────────────────────── */
.dep-row { display:flex; align-items:center; gap:8px; padding:2px 0; font-size:0.86em; }
.dep-icon { flex:0 0 1.3em; text-align:center; font-weight:700; }
.dep-name { flex:0 0 30%; font-family:monospace; }
.dep-detail { flex:1 1 auto; color:#777; font-size:0.92em;
              overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dep-ok   .dep-icon { color:#1a8a4a; }
.dep-bad  .dep-icon { color:#c0392b; }
.dep-warn .dep-icon { color:#e67e22; }
.dep-bad  .dep-detail { color:#c0392b; }
/* ── Step 3 build summary table ─────────────────────────────────────────── */
.bd-summary { width:100%; font-size:0.85em; border-collapse:collapse; }
.bd-summary td { padding:2px 8px; border-bottom:1px solid #f0f0f0; }
.bd-summary td.bd-num { text-align:right; font-family:monospace; color:#555; }
/* ── Step 4 priority groups ─────────────────────────────────────────────── */
.pa-assign { flex:0 0 22%; font-size:0.9em; }
.pa-grp  { color:#1a5fa8; font-weight:600; }
.pa-excl { color:#c0392b; font-style:italic; }
.pa-none { color:#bbb; }
.prio-actions {
    display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:8px;
}
.prio-actions .shiny-input-container { margin-bottom:0; }
.prio-actions input[type=text], .prio-actions select { height:30px; padding:2px 6px; }
.prio-adv { margin-top:8px; }
.prio-adv summary { cursor:pointer; font-size:0.84em; color:#3498db; font-weight:600; }
.prio-group { border:1px solid #e6e6e6; border-radius:6px; padding:6px 8px;
              margin-bottom:6px; background:#fafbfc; }
.prio-ghead { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.prio-gname { font-weight:600; color:#2c3e50; }
.prio-gctl { display:flex; gap:4px; }
.prio-gbtn { font-size:0.76em; padding:2px 7px; border:1px solid #ccc;
             border-radius:4px; background:#fff; cursor:pointer; color:#333; }
.prio-gbtn:hover { background:#e8f0fe; border-color:#4a90d9; }
.prio-del:hover { background:#fdeaea; border-color:#c0392b; color:#c0392b; }
.prio-rule { display:flex; align-items:center; gap:8px; padding:2px 0 2px 14px;
             font-size:0.82em; }
.prio-x { font-size:0.72em; padding:0 6px; border:none; background:none;
          color:#c0392b; cursor:pointer; }
.prio-rename { display:flex; gap:6px; align-items:center; margin:4px 0 8px 14px; }
.prio-rename .shiny-input-container { margin-bottom:0; }
.prio-exclblock { background:#fdf3f2; }
/* ── Step 5 validate ────────────────────────────────────────────────────── */
.val-inputs { font-size:0.84em; margin:6px 0; }
.val-inputs code { font-size:0.95em; }
.val-checks .dep-detail { white-space:normal; overflow:visible; }
.val-checks .dep-name { flex:0 0 32%; }
#error_out, [id$='_error'] { color:#c0392b; font-size:0.86em; }
/* ── Step 2 hierarchical feature selector ───────────────────────────────── */
.src-section { border:1px solid #e6e6e6; border-radius:6px; margin-bottom:5px; background:#fff; }
.src-head { cursor:pointer; padding:6px 10px; display:flex; align-items:center; gap:10px;
            list-style:none; }
.src-head::-webkit-details-marker { display:none; }
.src-head::before { content:"▸"; color:#888; font-size:0.8em; flex:0 0 auto; }
.src-section.open > .src-head::before { content:"▾"; }
.src-name { font-weight:600; color:#2c3e50; flex:0 0 auto; }
.src-meta { color:#888; font-size:0.8em; flex:1 1 auto; }
.src-btns { display:flex; gap:4px; flex:0 0 auto; }
.src-btn { font-size:0.72em; padding:1px 7px; border:1px solid #ccc; border-radius:4px;
           background:#fff; cursor:pointer; color:#444; }
.src-btn:hover { background:#e8f0fe; border-color:#4a90d9; }
.ftgrp { padding:2px 10px 6px; margin-left:28px; font-size:0.84em; }
.ftgrp .shiny-input-checkboxgroup, .ftgrp .form-group { margin:0; }
/* Flex layout: natural checkbox size, gap controls space to label.
   Bootstrap 5 uses float+negative-margin; override for both markup forms. */
.ftgrp .form-check { display:flex; align-items:center; gap:10px;
                     margin:0; padding:2px 0; min-height:0; }
.ftgrp .form-check-input { float:none; position:static; margin:0; flex-shrink:0; }
.ftgrp .form-check-label { margin:0; }
.ftgrp .checkbox { margin:0; padding:2px 0; min-height:0; }
.ftgrp .checkbox label { display:flex; align-items:center; gap:10px;
                         margin:0; padding:0; cursor:pointer; }
.ftgrp .checkbox label input[type="checkbox"] { position:static; margin:0; flex-shrink:0; }
.ft-item { color:#34495e; }
/* rows with examples show a help cursor + dotted name */
.ft-item[title] { cursor:help; }
.ft-item[title] > :first-child { text-decoration:underline dotted #bbb; text-underline-offset:2px; }
.ft-meta { margin-left:4ch; color:#888; font-family:monospace; font-size:0.95em; white-space:nowrap; }
.pa-grp, .pa-excl, .pa-none { white-space:nowrap; }
/* scrollable log lines (container is a stable output → scroll is preserved).
   Shiny's output_ui container defaults to display:contents (no box), which
   ignores max-height/overflow — force a real block box so it caps + scrolls. */
.progress-log { display:block !important; max-height:170px; overflow-y:auto;
                white-space:normal; }
.progress-log .log-line { white-space:pre-wrap; line-height:1.35; }
/* per-source keep/drop radio sits compactly in the section header */
.src-radio { flex:0 0 auto; }
.src-radio .shiny-input-container, .src-radio .form-group { margin:0; }
.src-radio .radio-inline, .src-radio .form-check-inline { margin-right:8px; }
.src-radio label { font-size:0.78em; margin:0; }
/* collapse via .open class (not <details>, so the radio stays clickable) */
.src-section > .ftgrp { display:none; }
.src-section.open > .ftgrp { display:block; }
"""


def _placeholder(step: str) -> ui.Tag:
    return ui.div({"class": "card"},
        ui.tags.h5(step),
        ui.p("Not yet implemented in the GUI. Use the terminal pipeline "
             "(setup/setup.py) for this step for now.",
             {"class": "upload-note"}),
    )


def make_setup_ui() -> ui.Tag:
    return ui.page_fluid(
        ui.tags.head(ui.tags.style(SETUP_CSS)),
        ui.tags.h2(
            f"gff2genbank — Setup  {SETUP_VERSION}",
            style="padding:12px 0 5px; color:#1a252f; font-weight:700;",
        ),
        ui.p("Local data-preparation GUI. Runs on your machine so it can read "
             "large files in place.", {"class": "upload-note"}),
        ui.navset_tab(
            ui.nav_panel("1 · Dependencies", deps_ui("deps")),
            ui.nav_panel("2 · Prepare GFF", prepare_gff_ui("prep")),
            ui.nav_panel("3 · Build database", build_db_ui("build")),
            ui.nav_panel("4 · Priority groups", priority_ui("prio")),
            ui.nav_panel("5 · Validate", validate_ui("validate")),
            selected="1 · Dependencies",
        ),
    )
