"""
setup_gui/engine.py
-------------------
Thin in-process seam between the setup GUI and the existing CLI engine in
prepare_gff.py / build_db.py.

The GUI calls these wrappers (rather than subprocessing the scripts) so it can:
  - stream structured progress via a `step(msg)` callback instead of scraping stdout,
  - branch on a chromosome-name mismatch as a return value instead of an exit code
    plus a template file written to disk,
  - reuse the scan results directly to build keep_pairs.

Nothing here re-implements engine logic — every real operation delegates to a
function already defined and tested in prepare_gff.py.
"""

import gzip
import sys
from math import exp, log, log1p
from random import random, randrange
from pathlib import Path
from typing import Callable

# Make the project-root modules importable regardless of launch directory
# (mirrors the sys.path dance in setup/s0X scripts).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import prepare_gff as P  # noqa: E402
from build_db import _fmt_time as fmt_duration  # noqa: E402  (m:ss formatter, shared)

# A progress callback: takes a single human-readable message.
StepFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


# How many example column-9 attribute strings to capture per (source, featuretype)
# pair during the scan (shown as a hover tooltip in the Step 2 selector), and the
# per-example truncation length so a long attribute field can't bloat a tooltip.
N_EXAMPLES = 3
_EX_MAXLEN = 160
_TINY = 5e-324  # smallest positive double — guards log() against a random() of 0.0


def _algo_l_state(c: int, w: float) -> list:
    """Reservoir-sampling (Algorithm L) state [W, next_index].

    Given the current 1-based feature index `c` and a running weight `w`, advance
    `w` and pick the next feature index at which the reservoir should be replaced.
    The geometric skip means we do this O(k·log n) times per pair instead of once
    per line — so the per-line scan cost stays a single integer compare. See
    https://en.wikipedia.org/wiki/Reservoir_sampling#Optimal:_Algorithm_L"""
    w = w * exp(log(random() or _TINY) / N_EXAMPLES)
    gap = int(log(random() or _TINY) / log1p(-w)) + 1
    return [w, c + gap]


# ---------------------------------------------------------------------------
# Phase 1 — validate inputs and convert any non-GFF3 to GFF3
# ---------------------------------------------------------------------------

def validate_and_convert(
    gff_paths: list[Path],
    step: StepFn = _noop,
) -> tuple[list[Path], list[Path]]:
    """
    Validate every input GFF and convert GTF/GFF2 inputs to GFF3 via AGAT.

    Returns (final_gff_paths, converted_temps) where final_gff_paths are the
    GFF3 files to scan and process, and converted_temps are AGAT outputs the
    caller should delete when done (via cleanup_temps).

    Raises RuntimeError (from prepare_gff) on an invalid file or a failed/
    missing AGAT conversion — the caller surfaces the message inline.
    """
    final_paths: list[Path] = []
    converted_temps: list[Path] = []

    for gff in gff_paths:
        step(f"Validating {gff.name}…")
        fmt = P.validate_gff_input(gff)
        if fmt == "gff3":
            step(f"  {gff.name}: GFF3 ✓")
            final_paths.append(gff)
        else:
            step(f"  {gff.name}: {fmt.upper()} — converting to GFF3 with AGAT…")
            converted = P.convert_to_gff3(gff)
            final_paths.append(converted)
            converted_temps.append(converted)
            step(f"  converted → {converted.name}")

    return final_paths, converted_temps


# ---------------------------------------------------------------------------
# Phase 2 — single-pass scan of (source, featuretype) pairs
# ---------------------------------------------------------------------------

def scan_pairs(gff_paths: list[Path], step: StepFn = _noop) -> tuple[list[dict], list[str]]:
    """
    ONE pass over every GFF that does double duty (so an enormous file is read
    only once, not once for pairs and again for chromosome names):
      - tally per-(source, featuretype) feature count and uncompressed bytes,
      - collect the unique chromosome names (column 1) in order of appearance.

    Returns (rows, gff_chroms):
      rows       — list of {"source", "featuretype", "count", "bytes",
                   "examples"} sorted by count desc. `bytes` (summed uncompressed
                   line length) is the honest predictor of output size;
                   `examples` holds up to N_EXAMPLES column-9 attribute strings
                   for a hover tooltip.
      gff_chroms — unique col-1 chromosome names across all inputs.

    """
    # (source, ftype) -> [count, bytes, sampler]. sampler is None while the
    # examples reservoir is still filling, then [W, next_index] (Algorithm L).
    tally: dict[tuple[str, str], list] = {}
    examples: dict[tuple[str, str], list[str]] = {}  # (source, ftype) -> [col9, …]
    chroms: list[str] = []
    chrom_seen: set[str] = set()

    for gff in gff_paths:
        step(f"Scanning {gff.name}…")
        opener = gzip.open if str(gff).endswith(".gz") else open
        seen = 0
        with opener(gff, "rt", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                parts = line.split("\t")
                c = parts[0]
                if c and c not in chrom_seen:
                    chrom_seen.add(c)
                    chroms.append(c)
                if len(parts) < 9:
                    continue
                seen += 1
                if seen % 250_000 == 0:
                    step(f"  {gff.name}: scanned {seen:,} lines…")
                key = (parts[1], parts[2])
                nbytes = len(line.encode("utf-8"))
                rec = tally.get(key)
                if rec is None:
                    # parts[8] is guaranteed present (len(parts) >= 9 above)
                    tally[key] = [1, nbytes, None]
                    examples[key] = [parts[8].strip()[:_EX_MAXLEN]]
                else:
                    rec[0] += 1
                    rec[1] += nbytes
                    st = rec[2]
                    if st is None:                  # reservoir still filling
                        ex = examples[key]
                        if len(ex) < N_EXAMPLES:
                            ex.append(parts[8].strip()[:_EX_MAXLEN])
                        if len(ex) >= N_EXAMPLES:   # just became full → start sampling
                            rec[2] = _algo_l_state(rec[0], 1.0)
                    elif rec[0] == st[1]:           # Algorithm L: this is a hit
                        examples[key][randrange(N_EXAMPLES)] = parts[8].strip()[:_EX_MAXLEN]
                        rec[2] = _algo_l_state(rec[0], st[0])

    rows = [
        {"source": s, "featuretype": f, "count": rec[0], "bytes": rec[1],
         "examples": examples.get((s, f), [])}
        for (s, f), rec in tally.items()
    ]
    rows.sort(key=lambda r: r["count"], reverse=True)
    step(f"Found {len(rows):,} distinct (source, feature type) pair(s) "
         f"across {len(chroms)} sequence(s).")
    return rows, chroms


# ---------------------------------------------------------------------------
# Phase 3 — detect chromosome-name mismatch (no GFF re-read)
# ---------------------------------------------------------------------------

def resolve_chroms(
    gff_chroms: list[str],
    fasta_path: Path,
    vcf_path: Path | None,
    step: StepFn = _noop,
) -> tuple[list[str], list[str], set[str]]:
    """
    Using the GFF chromosome names already collected by scan_pairs (so the big
    GFF is not read again), add FASTA names (from the .fai — instant) and any
    VCF names, and compute the mismatch set (source names absent from FASTA) —
    the same condition prepare_gff uses before writing a chrom-map template.

    Returns (fasta_names, source_names, mismatches).
    """
    step("Reading FASTA chromosome names…")
    fasta_names = P.fasta_chrom_names(fasta_path)  # creates .fai via samtools if absent
    fasta_set = set(fasta_names)

    source_names: list[str] = list(gff_chroms)
    if vcf_path:
        for name in P.vcf_chrom_names(vcf_path):
            if name not in source_names:
                source_names.append(name)

    mismatches = set(source_names) - fasta_set
    if mismatches:
        step(f"Chromosome-name mismatch: {sorted(mismatches)} not in FASTA.")
    else:
        step("All chromosome names already match the FASTA.")
    return fasta_names, source_names, mismatches


# ---------------------------------------------------------------------------
# Phase 4 — merge / filter / sort the prepared GFF
# ---------------------------------------------------------------------------

def run_prepare(
    gff_paths: list[Path],
    fasta_path: Path,
    vcf_path: Path | None,
    out_path: Path,
    chrom_map: dict[str, str],
    keep_pairs: set[tuple[str, str]] | None,
    step: StepFn = _noop,
) -> dict:
    """
    Append VCF alleles (if any), merge+filter the GFFs, and sort the output.
    Returns {"out_path": Path, "n_written": int}.
    """
    extra_lines: list[str] = []
    if vcf_path:
        step(f"Extracting alleles from {vcf_path.name}…")
        extra_lines = P.vcf_variants_as_gff_lines(vcf_path, chrom_map)
        step(f"  {len(extra_lines):,} VCF allele line(s).")

    step(f"Writing filtered GFF → {out_path.name}…")
    n = P.process_gff(
        gff_paths, out_path, chrom_map,
        keep_sources=None, extra_lines=extra_lines, keep_pairs=keep_pairs,
        progress=step,
    )
    step(f"  {n:,} feature line(s) written.")

    step("Sorting output (low-memory external sort)…")
    P.sort_gff_inplace(out_path, log=step)
    step("Done.")
    return {"out_path": out_path, "n_written": n}


def cleanup_temps(paths: list[Path], step: StepFn = _noop) -> None:
    """Delete AGAT-converted temp files produced by validate_and_convert."""
    if paths:
        step("Cleaning up temporary converted files…")
    P._cleanup_temps(paths)


# ---------------------------------------------------------------------------
# Step 1 — dependency check
# ---------------------------------------------------------------------------

def check_dependencies() -> list[dict]:
    """
    Probe required/optional Python packages and CLI tools, reusing the lists
    and probes already defined in setup/s01_check_deps.py.

    Returns a list of dicts:
        {"name", "category": "python"|"cli", "kind": "required"|"optional",
         "ok": bool, "version": str, "hint": str}
    """
    from setup import s01_check_deps as s01

    rows: list[dict] = []

    def _py(items, kind):
        for pkg, hint in items:
            ok, ver = s01._check_python(pkg)
            rows.append({"name": pkg, "category": "python", "kind": kind,
                         "ok": ok, "version": ver, "hint": hint})

    def _cli(items, kind):
        for tool, hint in items:
            ok, ver = s01._check_cli(tool)
            rows.append({"name": tool, "category": "cli", "kind": kind,
                         "ok": ok, "version": ver, "hint": hint})

    _py(s01.REQUIRED_PYTHON, "required")
    _cli(s01.REQUIRED_CLI, "required")
    _py(s01.OPTIONAL_PYTHON, "optional")
    _cli(s01.OPTIONAL_CLI, "optional")
    return rows


# ---------------------------------------------------------------------------
# Step 3 — build gffutils database
# ---------------------------------------------------------------------------

def count_features(gff_path: Path, step: StepFn = _noop) -> int:
    """Quick streaming count of non-comment feature lines (for build %)."""
    step(f"Counting features in {Path(gff_path).name}…")
    opener = gzip.open if str(gff_path).endswith(".gz") else open
    n = 0
    with opener(gff_path, "rt", errors="replace") as fh:
        for line in fh:
            if not line.startswith("#") and line.strip():
                n += 1
    step(f"  {n:,} features to index.")
    return n


def build_database(
    gff_path: Path,
    db_path: Path,
    force: bool = False,
    step: StepFn = _noop,
    total: int | None = None,
) -> dict:
    """
    Build the gffutils database from a prepared GFF (delegates to build_db.build,
    streaming progress via `step`), then compute an informative summary: total
    features, per-feature-type breakdown, on-disk size, and elapsed time.

    total: expected feature count for %-progress. If None, it's counted in a
    quick pass first (cheap for a prepared GFF).

    Returns {"db_path", "size", "elapsed", "total", "by_type", "skipped"}.
    """
    import build_db
    import gffutils

    if total is None:
        total = count_features(gff_path, step)

    step(f"Building gffutils database from {gff_path.name}…")
    stats = build_db.build(gff_path, db_path, force=force, log=step, total=total)

    size = db_path.stat().st_size if db_path.exists() else 0
    by_type: list[tuple[str, int]] = []
    total = stats.get("count", 0)
    try:
        db = gffutils.FeatureDB(str(db_path))
        cur = db.conn.execute(
            "SELECT featuretype, COUNT(*) AS n FROM features "
            "GROUP BY featuretype ORDER BY n DESC"
        )
        by_type = [(r[0], r[1]) for r in cur.fetchall()]
        total = sum(n for _, n in by_type)
    except Exception as e:  # pragma: no cover - summary is best-effort
        step(f"Note: could not read DB summary: {e}")

    step(f"Database is {size/1e6:.1f} MB — {total:,} features "
         f"across {len(by_type)} feature types.")
    return {
        "db_path": db_path, "size": size, "elapsed": stats.get("elapsed", 0.0),
        "total": total, "by_type": by_type, "skipped": stats.get("skipped", False),
    }


# ---------------------------------------------------------------------------
# Step 4 — priority groups (SKETCH; reuses setup/s04_priority_groups.py)
# ---------------------------------------------------------------------------

def scan_db_pairs(db_path: Path, step: StepFn = _noop) -> list[tuple[str, str, int]]:
    """All (source, featuretype, count) pairs in the DB (blank source/featuretype
    rows excluded), sorted alphabetically by (source, feature type).

    Streams rows from SQLite one at a time and reports progress every 250 k
    features — same pattern as scan_pairs (Step 2) — so the log box updates
    live rather than blocking until the GROUP BY finishes.
    """
    import time
    import gffutils
    step(f"Scanning {Path(db_path).name} for (source, feature type) pairs…")
    t0 = time.monotonic()
    db = gffutils.FeatureDB(str(db_path))
    cur = db.conn.execute("SELECT source, featuretype FROM features")
    counts: dict[tuple[str, str], int] = {}
    seen = 0
    for row in cur:
        src, ft = row[0], row[1]
        if src in ("", ".") or ft in ("", "."):
            continue
        key = (src, ft)
        counts[key] = counts.get(key, 0) + 1
        seen += 1
        if seen % 250_000 == 0:
            step(f"  scanned {seen:,} features…")
    pairs = sorted(
        [(src, ft, cnt) for (src, ft), cnt in counts.items()],
        key=lambda p: (p[0].lower(), p[1].lower()),
    )
    n_src = len({p[0] for p in pairs})
    elapsed = time.monotonic() - t0
    step(f"Found {len(pairs):,} pair(s) across {n_src} source(s) "
         f"— {seen:,} features in {elapsed:.1f}s.")
    return pairs


def load_priority_groups(tsv_path: Path) -> list[tuple[str, str, str, str]]:
    """Existing (group, source, featuretype, status) rows, or [] if absent."""
    from setup import s04_priority_groups as s04
    p = Path(tsv_path)
    if not p.exists():
        return []
    rows, _ = s04.load_tsv(p)
    return rows


def classify_pair(source: str, feat: str,
                  rows: list[tuple[str, str, str, str]]) -> tuple[str | None, str]:
    """(group_name, status) for the first row matching (source, feat)."""
    from setup import s04_priority_groups as s04
    return s04.classify_pair(source, feat, rows)


# --- row-list editing ops (thin pass-throughs to s04's tested helpers) ------

def pg_remove_exact(rows, selected):
    """Drop rows whose (source, featuretype) exactly matches a selected pair."""
    from setup import s04_priority_groups as s04
    return s04._remove_exact(list(rows), set(selected))


def pg_group_order(rows) -> list[str]:
    """Group names in current precedence order (include rows only)."""
    from setup import s04_priority_groups as s04
    return s04._group_order(list(rows))


def pg_insert_for_group(rows, group, new_rows):
    """Insert new_rows after the group's last row (or before excludes if new)."""
    from setup import s04_priority_groups as s04
    return s04._insert_for_group(list(rows), group, list(new_rows))


def pg_reorder(rows, new_order: list[str]):
    """Rebuild rows so include groups follow new_order; exclude rows go last.
    (Same logic as s04's reorder menu action.)"""
    exclude_rows = [r for r in rows if r[3] == "exclude"]
    include_rows = [r for r in rows if r[3] != "exclude"]
    reordered: list[tuple[str, str, str, str]] = []
    for g in new_order:
        reordered.extend(r for r in include_rows if r[0] == g)
    # keep any include rows whose group wasn't named in new_order (safety)
    named = set(new_order)
    reordered.extend(r for r in include_rows if r[0] not in named)
    return reordered + exclude_rows


def save_priority_groups(tsv_path: Path,
                         rows: list[tuple[str, str, str, str]]) -> None:
    """Write priority_groups.tsv from (group, source, featuretype, status) rows."""
    from setup import s04_priority_groups as s04
    s04.save_tsv(Path(tsv_path), rows)


# ---------------------------------------------------------------------------
# Step 5 — validate the finished setup (reuses setup/s05_validate.py checks)
# ---------------------------------------------------------------------------

def validate_setup(
    db_path: Path,
    fasta_path: Path,
    priority_groups_path: Path,
    default_region: str,
    step: StepFn = _noop,
) -> list[dict]:
    """
    Run the post-setup sanity checks and return structured results:
        [{"label", "ok", "message", "warnings": [str, ...]}, ...]

    Each underlying check lives in s05_validate; we just adapt their tuple
    returns into dicts the GUI can render.
    """
    from setup import s05_validate as s05

    results: list[dict] = []
    step("Running 5 setup checks…")

    def add(label, ok, message, warnings=None):
        step(f"  {'✓' if ok else '✗'} {label}: {message}")
        results.append({"label": label, "ok": ok, "message": message,
                        "warnings": warnings or []})

    db_ok, db_msg, db = s05._check_db(Path(db_path))
    add("Database opens", db_ok, db_msg)

    fa_ok, fa_msg, fa_chroms = s05._check_fasta(Path(fasta_path))
    add("FASTA + index", fa_ok, fa_msg)

    if db_ok and fa_ok:
        ov_ok, ov_msg = s05._check_chrom_overlap(db, fa_chroms)
        add("Chromosome-name overlap", ov_ok, ov_msg)

    if db_ok:
        dr_ok, dr_msg = s05._check_default_region(db, default_region)
        add("Default region resolves", dr_ok, dr_msg)

    pg_ok, pg_msg, pg_warns = s05._check_priority_groups(Path(priority_groups_path))
    add("Priority groups", pg_ok, pg_msg, pg_warns)

    return results
