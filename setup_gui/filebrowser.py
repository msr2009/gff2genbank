"""
setup_gui/filebrowser.py
------------------------
A reusable server-side directory-browser Shiny module for the local setup app.

Why not ui.input_file?  That widget uploads a *copy* of the file into a temp
dir and never exposes the real path — fine for a few-MB session upload, useless
for a 100 MB FASTA or a large GFF we want to read in place.  The setup app runs
locally on the same machine as the data, so we browse the real filesystem and
return real Paths.

Usage:
    # in UI:
    file_browser_ui("gff", "Input GFF")
    # in server:
    selected = file_browser_server("gff", start_dir=DATA_DIR, extensions=GFF_EXTS)
    # `selected` is a reactive.Value[Path | None] the parent can read.
"""

import json
import os
import sys
from pathlib import Path

from shiny import module, ui, render, reactive

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config  # noqa: E402


# Extension groups callers can pass to `extensions=` (lower-case, matched with
# endswith so double extensions like ".gff3.gz" work).
GFF_EXTS   = (".gff3", ".gff", ".gtf", ".gff2",
              ".gff3.gz", ".gff.gz", ".gtf.gz", ".gff2.gz")
FASTA_EXTS = (".fa", ".fasta", ".fna", ".fa.gz", ".fasta.gz")
VCF_EXTS   = (".vcf", ".vcf.gz", ".bcf")


def _match_ext(name: str, extensions: tuple[str, ...] | None) -> bool:
    if not extensions:
        return True
    low = name.lower()
    return any(low.endswith(e) for e in extensions)


def _entry_link(click_input_id: str, label: str, path: str, kind: str) -> ui.Tag:
    """A clickable directory/file row that posts '<kind>|<path>' to Shiny."""
    val = f"{kind}|{path}"
    onclick = (
        f"Shiny.setInputValue({json.dumps(click_input_id)}, "
        f"{json.dumps(val)}, {{priority:'event'}}); return false;"
    )
    icon = "📁" if kind == "D" else "📄"
    return ui.tags.a(
        ui.tags.span(icon, {"class": "fb-icon"}), f" {label}",
        href="#", onclick=onclick, class_="fb-entry",
    )


@module.ui
def file_browser_ui(label: str = "File") -> ui.Tag:
    return ui.div({"class": "card fb-card"},
        ui.tags.h5(label),
        ui.tags.div({"class": "fb-pathrow"},
            ui.input_text("path_text", None, placeholder="type or paste a path, then Go"),
            ui.input_action_button("go_path", "Go",
                                    class_="btn btn-sm btn-outline-secondary"),
        ),
        ui.output_ui("crumb"),
        ui.output_ui("listing"),
        ui.output_ui("chosen"),
    )


@module.server
def file_browser_server(
    input, output, session,
    start_dir: Path | None = None,
    extensions: tuple[str, ...] | None = None,
):
    start = Path(start_dir).expanduser() if start_dir else config.DATA_DIR
    current_dir = reactive.Value(start.resolve())
    selected    = reactive.Value(None)  # Path | None

    @reactive.effect
    @reactive.event(input.go_path)
    def _go_typed_path():
        raw = (input.path_text() or "").strip()
        if not raw:
            return
        p = Path(raw).expanduser()
        if p.is_dir():
            current_dir.set(p.resolve())
        elif p.is_file():
            selected.set(p.resolve())
            current_dir.set(p.resolve().parent)

    @reactive.effect
    @reactive.event(input.entry_click)
    def _click_entry():
        kind, _, path = (input.entry_click() or "").partition("|")
        p = Path(path)
        if kind == "D":
            current_dir.set(p.resolve())
        elif kind == "F":
            selected.set(p.resolve())

    @render.ui
    def crumb():
        return ui.div(str(current_dir()), {"class": "fb-crumb"})

    @render.ui
    def listing():
        d = current_dir()
        click_id = session.ns("entry_click")
        rows = [_entry_link(click_id, "..", str(d.parent), "D")]
        try:
            entries = sorted(
                os.scandir(d),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except OSError as exc:
            return ui.div(f"Cannot read {d}: {exc}", {"class": "fb-error"})

        shown = 0
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir:
                rows.append(_entry_link(click_id, e.name + "/", e.path, "D"))
                shown += 1
            elif _match_ext(e.name, extensions):
                rows.append(_entry_link(click_id, e.name, e.path, "F"))
                shown += 1
        if shown == 0:
            rows.append(ui.div("(no matching files in this folder)",
                               {"class": "fb-empty"}))
        return ui.div(rows, {"class": "fb-listing"})

    @render.ui
    def chosen():
        s = selected()
        if s is None:
            return ui.div()
        return ui.div(
            ui.tags.b("Selected: "), s.name,
            {"class": "fb-chosen"},
        )

    return selected
