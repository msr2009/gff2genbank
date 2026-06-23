# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Main genome browser
shiny run app.py
GFF_APP_DATA_DIR=/path/to/data shiny run app.py   # custom data dir

# Setup GUI (data preparation pipeline, tabbed)
shiny run setup_app.py

# Setup CLI (same pipeline, menu-driven)
python setup/setup.py
python setup/setup.py --step s04   # jump to a step

# Run tests
python test_data/test_priority_groups.py

# Index a FASTA (required before running main app)
samtools faidx genome.fa
```

No linting or formatting toolchain is configured.

## What this is

A Shiny for Python app to browse genomic annotations and export user-defined regions as annotated GenBank files. There are two separate Shiny apps:

- **`app.py`** — the runtime genome browser (users search genes/regions, toggle feature types, download GenBank)
- **`setup_app.py`** — a local setup wizard for preparing the data files the browser needs

## Architecture

### Main app (`app.py`)

Five cooperating modules:

| Module | Role |
|--------|------|
| `config.py` | All paths, colors, organism metadata, feature-type constants |
| `data.py` | Lazy-loaded GFF database and FASTA; shared singletons across all sessions |
| `plot.py` | Builds the Plotly genome browser figure |
| `genbank.py` | Serializes active features to a Biopython SeqRecord / .gb file |
| `server.py` | All Shiny reactive logic |

**Key reactive flow in `server.py`:**
1. User enters gene/coordinates → `active_region` (`reactive.Value`) set
2. `active_region` change → DB queried, `active_ftypes` reset to defaults
3. User pans/zooms Plotly → `active_window` captured and clamped to loaded range
4. User toggles a feature type → `active_ftypes` updated, plot rebuilt (`active_window` preserved)
5. User downloads → `genbank.serialize()` called with current `active_ftypes`

`data.py` uses a `threading.Lock`-guarded singleton (`_server_gff_db`, `_server_fasta`, `_gene_name_index`). `init_db()` is called once at startup; subsequent calls are no-ops.

### Setup pipeline (5 steps, two front-ends)

Each step has three implementations that share underlying logic:

```
setup/s0X_*.py          ← CLI version (standalone scripts)
setup_gui/step_*.py     ← GUI version (Shiny modules)
prepare_gff.py          ← engine for step 2 (GFF merge/filter/sort)
build_db.py             ← engine for step 3 (gffutils DB creation)
setup/s04_priority_groups.py  ← engine for step 4 (TSV helpers)
setup_gui/engine.py     ← thin pass-throughs from GUI modules to the engines above
```

**Cross-step state** is carried in an `app_session` reactive dict (GUI) or a plain `session` dict (CLI):

| Step | Reads | Writes |
|------|-------|--------|
| 2 — Prepare GFF | — | `session["prepared_gff"]` |
| 3 — Build DB | `prepared_gff` | `session["db_path"]` |
| 4 — Priority groups | `db_path` | `priority_groups.tsv` file |
| 5 — Validate | `db_path` | — |

### Shiny module pattern (setup GUI)

`setup_gui/server.py` mounts each step as a namespaced module:
```python
app_session = reactive.Value({"data_dir": str(config.DATA_DIR)})
priority_server("prio", app_session)   # each step reads/writes app_session
```

Each `step_*.py` exports `@module.ui` and `@module.server` functions. Reusable components live in `setup_gui/filebrowser.py` and `setup_gui/logbox.py`.

### Priority groups

`priority_groups.tsv` maps `(source, featuretype)` pairs to named groups or `_excluded_`. The browser loads it at startup and creates a dedicated UI panel per group. Pairs not in the file fall through to a flat annotation list.

The reactive SSOT in `step_priority.py` is `rows: reactive.Value(list[(group, source, featuretype, status)])` — all assign/exclude/reorder operations mutate it via helpers in `setup/s04_priority_groups.py` (exposed through `setup_gui/engine.py`).

### Off-thread scan pattern

Steps 2, 3, and 4 run long operations (file scans, DB builds, SQL queries) off the UI thread using `@reactive.extended_task`. A `_scan_msgs: list[str]` buffer is appended to from the background thread; a `_poll_scan` effect copies it into a `scan_log: reactive.Value` every 0.3 s via `reactive.invalidate_later(0.3)`. `log_box_ui` / `log_lines()` from `logbox.py` render the streamed lines.

## Data files required

Three files must exist before `app.py` starts (paths in `config.py`, override with `GFF_APP_DATA_DIR`):

- Prepared GFF3 (`.gff3` or `.gff3.gz`)
- Genome FASTA indexed with `samtools faidx`
- gffutils SQLite database (`.gff3.db`)

`priority_groups.tsv` is optional but expected at `config.PRIORITY_GROUPS_PATH`.
