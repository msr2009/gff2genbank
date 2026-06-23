"""
server.py — Shiny server function for the gff2genbank app.

Key fixes in this version:
  - avail_fts now includes transcript types (ncRNA, piRNA, etc.) as well as
    extra GFF feature types, so ALL annotation types get toggle switches.
  - active_ftypes reads all avail_fts switches plus core structural ones.
    The default is ON for everything except non-coding exons (hidden by default).
  - active_window is NOT reset when toggles fire — only reset on Load.
    This preserves zoom/pan position when the user turns annotations on/off.
  - Pan clamping: _capture_zoom clamps active_window to [load_start, load_end].
  - Toggle switches default to True (ON) for all types except exon.
    The Shiny input_switch initialises with value=True on first render;
    subsequent renders use the persisted switch state.
"""

import asyncio
import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import plotly.graph_objects as go
from shiny import reactive, render, req, ui
from shinywidgets import render_widget

import data as D
import plot as P
import genbank as G
from config import (
    LOAD_FLANK, VIEW_FLANK,
    FEATURE_COLORS,
    ALWAYS_HANDLED, DEFAULT_REGION, ORGANISM_SHORT,
    PALETTE, DEFAULT_COLORS, PRIORITY_GROUPS_PATH,
)


def _safe_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _build_extra_types(
    feats: dict,
    tx_types: set,
    priority_groups: list,
    excluded_patterns: list,
) -> set:
    """
    Return the set of featuretypes that should appear in the flat annotation list.

    A featuretype is excluded if:
      1. It is in ALWAYS_HANDLED (structural/container types), or
      2. It is a tx_type (rendered in the transcript track), or
      3. Every feature of that type in the region is claimed by a priority group, or
      4. Every feature of that type in the region matches an excluded pattern.

    This means variation-type features with no priority group configured will
    appear in the flat list — the user is never locked out of an annotation.
    """
    def _is_claimed(source: str, ft: str) -> bool:
        """Return True if (source, ft) is claimed by any priority group."""
        for _, patterns in priority_groups:
            for pat_src, pat_ft in patterns:
                src_ok = pat_src == "*" or pat_src == source
                ft_ok  = pat_ft  == "*" or pat_ft  == ft
                if src_ok and ft_ok:
                    return True
        return False

    def _is_excluded(source: str, ft: str) -> bool:
        """Return True if (source, ft) matches an explicit exclude pattern."""
        for pat_src, pat_ft in excluded_patterns:
            src_ok = pat_src == "*" or pat_src == source
            ft_ok  = pat_ft  == "*" or pat_ft  == ft
            if src_ok and ft_ok:
                return True
        return False

    result = set()
    for ft, feat_list in feats.items():
        if ft in ALWAYS_HANDLED or ft in tx_types:
            continue
        # Include this featuretype if at least one feature is neither
        # claimed by a priority group nor explicitly excluded.
        if any(
            not _is_claimed(f["source"], ft) and not _is_excluded(f["source"], ft)
            for f in feat_list
        ):
            result.add(ft)
    return result


# ---------------------------------------------------------------------------
# Priority groups — loaded once per session from priority_groups.tsv.
# Structure: list of (group_name, [(source_pat, featuretype_pat), ...])
# Validated at session startup via _validate_priority_groups().
# ---------------------------------------------------------------------------

def _load_priority_groups_tsv(
    tsv_path: Path,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """
    Parse priority_groups.tsv into an ordered list of
    (group_name, [(source_pat, featuretype_pat), ...]).

    Rows with status 'exclude' are collected under a special '_excluded_'
    group so the server can filter those features from the flat list.
    Rows with any other status (or missing status column) are 'include'.
    Lines starting with '#' and the header row are ignored.
    """
    if not tsv_path.exists():
        return []

    raw_rows: list[tuple[str, str, str, str]] = []
    with open(tsv_path) as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("group_name"):
                continue   # header
            parts = stripped.split("\t")
            if len(parts) < 3:
                continue
            group  = parts[0].strip()
            source = parts[1].strip()
            feat   = parts[2].strip()
            status = parts[3].strip() if len(parts) >= 4 else "include"
            if group and source and feat:
                raw_rows.append((group, source, feat, status))

    # Assemble into ordered (group, [patterns]) preserving row order.
    group_patterns: dict[str, list[tuple[str, str]]] = {}
    group_order: list[str] = []
    for group, source, feat, status in raw_rows:
        key = "_excluded_" if status == "exclude" else group
        if key not in group_patterns:
            group_patterns[key] = []
            group_order.append(key)
        group_patterns[key].append((source, feat))

    return [(g, group_patterns[g]) for g in group_order]


def _validate_priority_groups(
    groups: list[tuple[str, list[tuple[str, str]]]],
) -> list[tuple[str, list[tuple[str, str]]]]:
    """
    Validate priority groups, deduplicating (source, featuretype) pairs.

    A pair that appears in more than one group is assigned to the first group
    that claimed it.  A WARNING is printed for each conflict; the duplicate
    entry is silently dropped from later groups.  A group left with no
    patterns after deduplication is dropped entirely with a WARNING.

    Returns the cleaned, deduplicated group list.
    """
    seen: dict[tuple[str, str], str] = {}   # pattern -> first group name
    cleaned: list[tuple[str, list[tuple[str, str]]]] = []

    for group, patterns in groups:
        clean_patterns: list[tuple[str, str]] = []
        for pat in patterns:
            if pat in seen:
                print(
                    f"[priority_groups] WARNING: ({pat[0]!r}, {pat[1]!r}) defined in "
                    f"both {seen[pat]!r} and {group!r}. "
                    f"Assigned to {seen[pat]!r}. "
                    f"Remove from {group!r} to silence this warning."
                )
            else:
                seen[pat] = group
                clean_patterns.append(pat)
        if not clean_patterns:
            print(
                f"[priority_groups] WARNING: group {group!r} has no patterns "
                f"after deduplication and will be skipped."
            )
            continue
        cleaned.append((group, clean_patterns))

    return cleaned


def _load_and_validate_priority_groups(
    tsv_path: Path,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Load priority_groups.tsv from the configured path and validate it.
    If the file does not exist, priority groups are disabled (returns [])."""
    if not tsv_path.exists():
        print(f"[priority_groups] {tsv_path} not found — priority panel disabled.")
        return []
    raw = _load_priority_groups_tsv(tsv_path)
    if not raw:
        if tsv_path.exists():
            print("[priority_groups] priority_groups.tsv is empty or has no valid rows.")
        else:
            print("[priority_groups] No priority_groups.tsv found — priority panel disabled.")
        return []
    validated = _validate_priority_groups(raw)
    include_groups = [(g, p) for g, p in validated if g != "_excluded_"]
    n_groups   = len(include_groups)
    n_patterns = sum(len(p) for _, p in include_groups)
    print(
        f"[priority_groups] Loaded {n_groups} group(s), "
        f"{n_patterns} pattern(s) from {tsv_path.name}."
    )
    return validated   # keep _excluded_ entry for feature filtering



def _extend_load_window(
    db, chrom: str, load_start: int, load_end: int,
    max_load_bp: int,
) -> tuple[int, int, bool]:
    """
    Check whether any mRNA in the initial load window extends beyond its
    boundaries. If so, expand the window to fully contain those transcripts,
    provided the result stays within max_load_bp.

    Returns (new_load_start, new_load_end, was_extended).
    """
    raw  = D.query_region(db, chrom, load_start, load_end)
    txs  = D.transcript_structures(db, chrom, load_start, load_end, _raw=raw)
    ext_start, ext_end = load_start, load_end
    for tx in txs:
        if tx["tx_type"] != "mRNA":
            continue
        if tx["start"] < ext_start:
            ext_start = max(1, tx["start"])
            print(f"[load] mRNA '{tx['name']}' extends left  to {tx['start']:,}")
        if tx["end"] > ext_end:
            ext_end = tx["end"]
            print(f"[load] mRNA '{tx['name']}' extends right to {tx['end']:,}")
    if ext_start < load_start or ext_end > load_end:
        ext_bp = ext_end - ext_start
        if ext_bp <= max_load_bp:
            print(f"[load] Window extended: {chrom}:{ext_start:,}-{ext_end:,} ({ext_bp:,}bp)")
            return ext_start, ext_end, True
        else:
            print(f"[load] CDS extension ({ext_bp:,}bp) exceeds MAX_LOAD_BP — keeping original")
    return load_start, load_end, False


def server(input, output, session):

    # Per-session reactive state ─────────────────────────────────────────
    region        = reactive.Value(None)   # (chrom, load_start, load_end)
    tx_data       = reactive.Value([])
    extra_data    = reactive.Value({})
    variants_data = reactive.Value({})
    # All annotation types found in region that get toggle switches.
    # Includes transcript types AND extra GFF feature types.
    avail_fts     = reactive.Value([])
    err_msg       = reactive.Value("")
    loading       = reactive.Value(False)
    load_steps    = reactive.Value([])

    # Priority groups for this session — loaded from priority_groups.tsv
    # alongside the active DB.  List of (group_name, [(src_pat, ft_pat)...]).
    # The '_excluded_' pseudo-group (if present) is kept here for feature
    # filtering but never shown in the UI.
    from config import PRIORITY_GROUPS_PATH
    _pg_all      = _load_and_validate_priority_groups(PRIORITY_GROUPS_PATH)
    # Separate include groups (shown in UI) from excluded patterns
    priority_groups    = reactive.Value(
        [(g, p) for g, p in _pg_all if g != "_excluded_"]
    )
    excluded_patterns  = reactive.Value(
        next((p for g, p in _pg_all if g == "_excluded_"), [])
    )

    # Note: download window is read directly from the FigureWidget at click
    # time via _current_window(), so no active_window reactive Value is needed.
    # We do track gene_bounds for the initial plot view window.
    gene_bounds     = reactive.Value(None)   # (g_s, g_e) of target gene/region
    initial_view    = reactive.Value(None)   # (view_start, view_end) set on each Load
    reset_view      = reactive.Value(0)        # incremented by Reset button to trigger re-render
    # Last known view range from JS — persisted across Load calls so the plot
    # stays at the same position when re-loading within the same region.
    last_view_range = reactive.Value(None)   # (start, end) or None
    # Increments on each Load; passed as numeric uirevision to Plotly so axes
    # fully reset between regions (avoids Plotly string-length init bug).
    load_count      = reactive.Value(0)

    # Per-session file overrides
    session_tmpdir   = reactive.Value(None)
    download_error   = reactive.Value(None)   # set when download window is outside loaded region
    oor_modal_data         = reactive.Value(None)
    large_region_data      = reactive.Value(None)
    large_region_confirmed = reactive.Value(False)
    session_db      = reactive.Value(None)   # path string
    session_db_conn = reactive.Value(None)   # cached FeatureDB handle
    session_fa      = reactive.Value(None)
    upload_msg     = reactive.Value("")

    # Per-session color overrides — starts as a copy of DEFAULT_COLORS.
    # Updated whenever the user clicks a color swatch.
    session_colors = reactive.Value(dict(DEFAULT_COLORS))

    # Tracks whether the default DB+FASTA are loaded and the app is ready.
    db_ready    = reactive.Value(False)
    startup_msg = reactive.Value("Loading default database and FASTA...")

    # ── Startup: pre-load default DB and FASTA at session start ───────────
    # Runs immediately when the session opens (no event dependency).
    # Shows a loading overlay in the UI until done.
    @reactive.effect
    async def _startup_load():
        import asyncio
        await asyncio.sleep(0.1)
        try:
            from config import DB_PATH, FASTA_PATH
            startup_msg.set(f"Loading GFF database: {DB_PATH.name}")
            db = D.get_server_gff_db()
            startup_msg.set("Warming up database index...")
            try:
                row = next(db.execute("SELECT COUNT(*) FROM features WHERE featuretype='gene'"))
                n = row[0]
                print(f"[startup] Database index warmed: {n:,} gene records cached.")
            except Exception as warm_ex:
                print(f"[startup] Warmup error (non-fatal): {warm_ex}")
            startup_msg.set("Building gene name index...")
            await asyncio.sleep(0)   # yield to event loop so message reaches browser
            try:
                D.get_gene_name_index(db)
                print("[startup] Gene name index ready.")
            except Exception as idx_ex:
                print(f"[startup] Gene name index error (non-fatal): {idx_ex}")
            startup_msg.set(f"Loading genome FASTA: {FASTA_PATH.name}")
            fa = D.get_server_fasta()
            startup_msg.set("Validating database and FASTA compatibility...")
            await asyncio.sleep(0)
            errs = D.validate_db_fasta(db, fa)
            if errs:
                for e in errs:
                    print(f"[startup] VALIDATION ERROR: {e}")
                startup_msg.set(f"⚠ {errs[0]}")
                db_ready.set(True)
                return
            print("[startup] Default database and FASTA ready.")
            startup_msg.set("")
            db_ready.set(True)
        except Exception as ex:
            startup_msg.set(f"Startup error: {ex}")
            print(f"[startup] ERROR: {ex}")

    # ── Auto-load default region once db_ready flips True ─────────────────
    # Runs in the normal reactive graph (not inside the async startup effect)
    # so all output renderers are fully wired before reactive state is set.
    # @reactive.event ensures this fires exactly once when db_ready() → True,
    # and never again (ignore_none=True skips the initial False value).
    def _load_region(chrom, g_s, g_e, db, *,
                     load_start=None, load_end=None,
                     view_start=None, view_end=None,
                     step=None):
        """
        Shared core for all region-load paths.

        Given a resolved (chrom, g_s, g_e) target and a db handle, this
        function:
          1. Computes the load window (target ± LOAD_FLANK, extended to
             fully contain any overlapping transcripts).
          2. Determines the initial view window (gene ± VIEW_FLANK, or the
             caller-supplied view_start/view_end for the out-of-range case).
          3. Queries the GFF database for transcripts, features, and variants.
          4. Updates all reactive state so the plot and sidebar re-render.

        Parameters
        ----------
        chrom, g_s, g_e : str, int, int
            Resolved chromosome and target span (e.g. from parse_region or
            gene_coords).
        db : gffutils.FeatureDB
            Open database handle (server or session).
        view_start, view_end : int | None
            If supplied, used as the initial view window instead of the
            default gene ± VIEW_FLANK calculation.  Used by the out-of-range
            reload path.
        step : callable | None
            Optional progress callback step(msg) for the load spinner.
        """
        def _step(msg):
            if step:
                step(msg)

        if load_start is None:
            load_start = max(1, g_s - LOAD_FLANK)
        if load_end is None:
            load_end = g_e + LOAD_FLANK

        # Determine initial view window.
        if view_start is None or view_end is None:
            # Preserve the current JS view range if it falls within the new
            # loaded region (so re-loading the same area keeps the zoom level).
            with reactive.isolate():
                lvr = last_view_range()
            if lvr is not None and lvr[0] >= load_start and lvr[1] <= load_end:
                view_start, view_end = lvr
            else:
                view_start = max(load_start, g_s - VIEW_FLANK)
                view_end   = min(load_end,   g_e + VIEW_FLANK)

        _step(f"Querying GFF: {chrom}:{load_start:,}-{load_end:,}...")
        raw   = D.query_region(db, chrom, load_start, load_end)
        txs   = D.transcript_structures(db, chrom, load_start, load_end, _raw=raw)
        feats = D.features_in_region(db, chrom, load_start, load_end, _raw=raw)
        vars_ = D.variant_features(db, chrom, load_start, load_end,
                           priority_groups=priority_groups(),
                           _raw=raw)
        tx_types    = {tx["tx_type"] for tx in txs if tx["tx_type"] != "mRNA"}
        extra_types = _build_extra_types(feats, tx_types, priority_groups(), excluded_patterns())

        # Reset toggle debounce so the post-load render is immediate.
        with reactive.isolate():
            _toggle_state.set(None)
            _pending_toggle.set(None)
            lc = load_count()

        # Update all reactive state in one block.
        region.set((chrom, load_start, load_end))
        gene_bounds.set((g_s, g_e))
        initial_view.set((view_start, view_end))
        load_count.set(lc + 1)
        tx_data.set(txs)
        extra_data.set(feats)
        variants_data.set(vars_)
        avail_fts.set(sorted(tx_types | extra_types))
        last_view_range.set((view_start, view_end))

        n_cv = sum(len(v) for v in vars_.values())
        _step(f"Done — {len(txs)} transcript(s), {n_cv} variant(s).")
        print(f"[load] {chrom}:{load_start:,}-{load_end:,} "
              f"view={view_start:,}-{view_end:,} "
              f"txs={len(txs)} vars={n_cv}")

    @reactive.effect
    @reactive.event(db_ready)
    def _auto_load_default_region():
        """Load DEFAULT_REGION from config.py when the database first becomes ready."""
        if not db_ready():
            return
        try:
            chrom, g_s, g_e = D.parse_region(DEFAULT_REGION)
            db = D.get_server_gff_db()
            # Extend window to fully contain any overlapping transcripts.
            load_start = max(1, g_s - LOAD_FLANK)
            load_end   = g_e + LOAD_FLANK
            load_start, load_end, _ = _extend_load_window(
                db, chrom, load_start, load_end, max_load_bp=200_000)
            _load_region(chrom, g_s, g_e, db,
                         load_start=load_start, load_end=load_end)
            print(f"[startup] Default region loaded: {DEFAULT_REGION}")
        except Exception as ex:
            print(f"[startup] Auto-load of default region failed (non-fatal): {ex}")

    # ── Session cleanup ────────────────────────────────────────────────────
    @reactive.effect
    def _register_cleanup():
        # Resolve the reactive value NOW (inside a reactive context)
        # so the lambda captures the plain string, not the reactive Value.
        tmpdir = session_tmpdir()
        session.on_ended(lambda: _cleanup(tmpdir))

    def _cleanup(tmpdir):
        if tmpdir and Path(tmpdir).exists():
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _ensure_tmpdir():
        d = session_tmpdir()
        if d is None:
            d = tempfile.mkdtemp(prefix="gff_app_")
            session_tmpdir.set(d)
        return Path(d)

    def _gff_db():
        p = session_db()
        if p and Path(p).exists():
            import gffutils
            conn = session_db_conn()
            if conn is None:
                try:
                    conn = gffutils.FeatureDB(str(p))
                except Exception:
                    raise ValueError(
                        "The uploaded file does not appear to be a valid gffutils "
                        "database. Use build_db.py to create a database from a GFF3 file."
                    )
                try:
                    tables = {r[0] for r in conn.conn.cursor().execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    if not {"features", "relations"}.issubset(tables):
                        raise ValueError(
                            "Database is missing required tables. "
                            "Re-build it with build_db.py from a GFF3 file."
                        )
                except ValueError:
                    raise
                except Exception as e:
                    raise ValueError(f"Database validation failed: {e}")
                session_db_conn.set(conn)
            return conn
        try:
            server_db = D.get_server_gff_db()
            server_db.conn.cursor().execute("SELECT 1").fetchone()
            return server_db
        except Exception:
            raise ValueError(
                "The database connection was lost (the DB file may have been "
                "moved or deleted). Restart the app to reconnect."
            )

    def _fasta():
        p = session_fa()
        if p and Path(p).exists():
            from pyfaidx import Fasta
            return Fasta(str(p))
        return D.get_server_fasta()

    # ── File uploads ───────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.apply_uploads)
    def _apply_uploads():
        upload_msg.set("")
        tmpdir = _ensure_tmpdir()
        msgs   = []
        db_info = input.upload_db()
        if db_info:
            dest = tmpdir / "custom.db"
            shutil.copy(db_info[0]["datapath"], dest)
            session_db.set(str(dest))
            session_db_conn.set(None)   # invalidate cached connection
            msgs.append("GFF database loaded — will validate on next region load")
        fa_info  = input.upload_fa()
        fai_info = input.upload_fai()
        if fa_info and fai_info:
            shutil.copy(fa_info[0]["datapath"],  tmpdir / "custom.fa")
            shutil.copy(fai_info[0]["datapath"], tmpdir / "custom.fa.fai")
            session_fa.set(str(tmpdir / "custom.fa"))
            msgs.append("FASTA + index loaded")
        elif fa_info or fai_info:
            msgs.append("Warning: upload both .fa and .fai together")
        pg_info = input.upload_pg()
        if pg_info:
            dest = tmpdir / "custom_priority_groups.tsv"
            shutil.copy(pg_info[0]["datapath"], dest)
            _pg_all = _load_and_validate_priority_groups(dest)
            priority_groups.set([(g, p) for g, p in _pg_all if g != "_excluded_"])
            excluded_patterns.set(next((p for g, p in _pg_all if g == "_excluded_"), []))
            msgs.append("Priority groups loaded")
        upload_msg.set("\n".join(msgs) if msgs else "No files uploaded.")

    @output
    @render.text
    def upload_status():
        return upload_msg()

    # ── Region load ────────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.load_btn)
    def _load_user_region():
        """Load a region from the search box, triggered by the Load button."""
        if not db_ready():
            err_msg.set("Database is still loading. Please wait.")
            return

        err_msg.set("")
        load_steps.set([])
        loading.set(True)

        def step(msg):
            load_steps.set(load_steps() + [msg])

        import time
        t0 = time.perf_counter()
        def elapsed():
            return f"{time.perf_counter() - t0:.2f}s"

        try:
            db  = _gff_db()
            raw = input.region_input().strip()

            # ── Parse input: coordinate string or gene/isoform name ───────
            if re.match(r"^\S+:\d[\d,]*-\d[\d,]*$", raw):
                chrom, g_s, g_e = D.parse_region(raw)
            else:
                step(f"Looking up '{raw}'...")
                chrom, g_s, g_e, _ = D.gene_coords(db, raw)

            # ── Validate chromosome ───────────────────────────────────────
            all_chroms = D.db_chroms(db)
            if all_chroms and chrom not in all_chroms:
                raise ValueError(
                    f"Chromosome '{chrom}' was not found in the database. "
                    f"Available chromosomes: {', '.join(all_chroms)}"
                )

            # ── Large-region guard ────────────────────────────────────────
            load_start = max(1, g_s - LOAD_FLANK)
            load_end   = g_e + LOAD_FLANK
            load_start, load_end, _ = _extend_load_window(
                db, chrom, load_start, load_end, max_load_bp=200_000)
            with reactive.isolate():
                confirmed = large_region_confirmed()
            if (load_end - load_start) > 200_000 and not confirmed:
                large_region_data.set((chrom, g_s, g_e, load_start, load_end))
                loading.set(False)
                return
            large_region_confirmed.set(False)

            # ── Session db validation ─────────────────────────────────────
            if session_db():
                step("Validating database compatibility...")
                errs = D.validate_db_fasta(db, _fasta())
                if errs:
                    raise ValueError("\n".join(errs))

            # ── Reset strand orientation on every new Load ────────────────
            ui.update_radio_buttons("strand_mode", selected="+")

            # ── Load ──────────────────────────────────────────────────────
            _load_region(chrom, g_s, g_e, db,
                         load_start=load_start, load_end=load_end,
                         step=step)
            print(f"[load] Total load time: {elapsed()}")

        except Exception as exc:
            err_msg.set(f"Error: {exc}")
            print(f"[load] ERROR after {elapsed()}: {exc}")
        finally:
            loading.set(False)

    # _capture_zoom removed: shinywidgets does not expose relayout_data
    # or layout as reactive widget traits. Zoom/pan is now captured via
    # on_relayout callback registered directly on the FigureWidget inside
    # the preview_plot render function below.

    # ── Active feature types ───────────────────────────────────────────────
    @reactive.calc
    def active_ftypes():
        """
        Returns the set of currently-enabled feature types.

        Core structural types (CDS, UTRs): ON by default, toggle to hide.
        All types in avail_fts: ON by default, toggle to hide.
        Non-coding exons: removed entirely (point 8).

        The Shiny switch sends True when ON, False when OFF.
        If a switch doesn't exist yet (before region loads), default to ON.
        """
        enabled = set()

        for ft in ("CDS", "five_prime_UTR", "three_prime_UTR", "exon"):
            try:
                if input[f"ft_{ft}"]():
                    enabled.add(ft)
            except Exception:
                enabled.add(ft)   # default ON

        # Introns: off by default
        try:
            if input["ft_intron"]():
                enabled.add("intron")
        except Exception:
            pass

        for ft in avail_fts():
            try:
                if input[f"ft_{ft}"]():
                    enabled.add(ft)
            except Exception:
                enabled.add(ft)   # default ON

        return enabled

    # ── Debounced render inputs ────────────────────────────────────────────
    # ── Debounced toggle state (R-shiny debounce pattern) ─────────────────
    # _toggle_state holds the last *committed* snapshot of toggle inputs.
    # The effect below reads raw inputs; when they change it schedules
    # reactive.invalidate_later(0.5). On the next run (500ms later), if
    # inputs are still the same it commits — otherwise it schedules again.
    # This replicates R Shiny's debounce() behaviour exactly.
    _toggle_state = reactive.Value(None)

    # _pending holds the snapshot we want to commit after the quiet period.
    _pending_toggle = reactive.Value(None)

    @reactive.effect
    def _debounce_toggles():
        # Read raw inputs — registers dependencies so this re-runs on any change.
        current = (
            frozenset(active_ftypes()),
            tuple(sorted(priority_group_visibility().items())),
            input.strand_mode(),
            tuple(sorted(session_colors().items())),
        )
        with reactive.isolate():
            last_committed = _toggle_state()
            pending        = _pending_toggle()

        if last_committed is None:
            # First run — commit immediately, no debounce needed.
            _toggle_state.set(current)
            return

        if current == last_committed:
            # Inputs are back to the committed state (or timer fired and
            # nothing changed) — nothing to do.
            return

        if current == pending:
            # Timer fired and inputs are still the same as when we last
            # saw a change — quiet period has elapsed, safe to commit.
            _toggle_state.set(current)
            _pending_toggle.set(None)
            return

        # Inputs changed — record what we saw and wait 800ms.
        _pending_toggle.set(current)
        reactive.invalidate_later(0.5)

    @reactive.calc
    def priority_group_visibility():
        result = {}
        for g, _ in priority_groups():
            key = f"pg_{_safe_id(g)}"
            try:
                result[g] = bool(input[key]())
            except Exception:
                result[g] = True
        return result


    # ── Download button (disabled until a region is loaded) ────────────────
    @output
    @render.ui
    def download_btn_ui():
        """
        Renders the Download GenBank button.
        Disabled (greyed out, not clickable) until a region has been loaded.
        When a region is loaded, renders as a proper download_button.
        """
        if region() is None:
            # Disabled placeholder — looks like the button but does nothing
            return ui.tags.button(
                "Download GenBank (.gb)",
                class_="btn btn-success w-100 mt-1",
                disabled=True,
                style="opacity:0.5; cursor:not-allowed; padding:4px 8px; font-size:0.875rem; line-height:1.5;",
            )
        return ui.download_button(
            "download_gb", "Download GenBank (.gb)",
            class_="btn btn-success w-100 mt-1",
            style="padding:4px 8px; font-size:0.875rem; line-height:1.5;",
        )

    # ── SVG download button (disabled until a region is loaded) ─────────────
    @output
    @render.ui
    def download_svg_btn_ui():
        disabled = region() is None
        style = (
            "background-color:#d4edda; color:#155724;"
            "border:1px solid #c3e6cb; padding:4px 8px;"
            "font-size:0.875rem; line-height:1.5;"
        )
        if disabled:
            style += "opacity:0.5; cursor:not-allowed;"
        return ui.tags.button(
            ui.tags.i({"class": "bi bi-filetype-svg"}, ""),
            " Download SVG",
            id="download_svg_btn",
            class_="btn w-100 mt-1",
            disabled=disabled,
            style=style,
        )

    # ── Hidden span carrying the current SVG filename ──────────────────────
    @output
    @render.ui
    def svg_filename_ui():
        """
        Renders a hidden span with the SVG filename matching the GenBank filename
        (same chrom/coords/strand, but .svg extension). Read by the JS SVG handler.
        """
        if region() is None:
            stem = "gff2genbank_plot"
        else:
            stem = G.make_filename(*_current_window(),
                                   strand=input.strand_mode()).replace(".gb", "")
        return ui.tags.span(
            stem,
            id="svg_filename_value",
            style="display:none;",
        )

    # ── Text outputs ───────────────────────────────────────────────────────
    @output
    @render.text
    def status_out():
        if not db_ready():
            return "Database loading, please wait..."
        r = region()
        if r is None:
            return "Enter a gene name or coordinates and click Load."
        chrom, s, e = r
        src = "custom" if session_db() else "default"
        return f"Loaded {chrom}:{s:,}-{e:,} [{src}]"

    @output
    @render.text
    def error_out():
        return err_msg()

    @reactive.effect
    @reactive.event(input.toolbar_reset)
    def _on_toolbar_reset():
        """Triggered by the Reset toolbar button via Shiny.setInputValue."""
        reset_view.set(reset_view() + 1)

    @output
    @render.text
    def initial_view_range():
        """
        Exposes the initial view range as a hidden text output so JS can read
        it for the Reset toolbar button.  Format: "start,end" or empty string.
        """
        iv = initial_view()
        if iv is None:
            return ""
        return f"{iv[0]},{iv[1]}"

    @output
    @render.text
    def window_display():
        """
        Shows the current visible window.
        Updated by JS via two strategies:
          1. DOM-level plotly_relayout event (bubbled CustomEvent)
          2. 1-second polling of _fullLayout.xaxis.range
        Both set input.plotly_xrange via Shiny.setInputValue.
        Console output helps diagnose whether JS is firing.
        """
        r = region()
        if r is None:
            return "-"
        chrom, load_start, load_end = r

        # Check if user double-clicked to reset (autorange)
        try:
            _ = input.plotly_autoreset()
            # If we get here, an autoreset fired — show full loaded region
            strand = input.strand_mode()
            if strand == "-":
                return f"{chrom}:{load_end:,}-{load_start:,}"
            return f"{chrom}:{load_start:,}-{load_end:,}"
        except Exception:
            pass

        # Try the JS-injected range
        try:
            rng = input.plotly_xrange()
            if rng and "x0" in rng and "x1" in rng:
                raw_s = int(round(min(rng["x0"], rng["x1"])))
                raw_e = int(round(max(rng["x0"], rng["x1"])))
                if raw_s >= 0 and raw_s < raw_e:
                    s = max(load_start, raw_s)
                    e = min(load_end,   raw_e)
                    strand = input.strand_mode()
                    if strand == "-":
                        result = f"{chrom}:{e:,}-{s:,}"
                    else:
                        result = f"{chrom}:{s:,}-{e:,}"
                    print(f"[window_display] JS range active: {result}")
                    return result
            else:
                print(f"[window_display] plotly_xrange exists but has no data: {rng!r}")
        except Exception as ex:
            print(f"[window_display] plotly_xrange not yet set (normal before first zoom): {type(ex).__name__}: {ex!r}")

        # Fallback: show full loaded region
        strand = input.strand_mode()
        if strand == "-":
            fallback = f"{chrom}:{load_end:,}-{load_start:,}"
        else:
            fallback = f"{chrom}:{load_start:,}-{load_end:,}"
        print(f"[window_display] showing fallback (full loaded region): {fallback}")
        return fallback

    @reactive.effect
    def _persist_view_range():
        """Store the current JS view range so it can be preserved on next Load.
        Uses a 5 bp deadband: ignore updates that differ by <= 5 bp from the
        current stored value.  This prevents Plotly's sub-pixel axis rounding
        from producing an endless stream of micro-updates.
        """
        try:
            rng = input.plotly_xrange()
            if rng and "x0" in rng and "x1" in rng:
                s = int(round(min(rng["x0"], rng["x1"])))
                e = int(round(max(rng["x0"], rng["x1"])))
                if s >= 0 and s < e:
                    with reactive.isolate():
                        cur = last_view_range()
                    if cur is None or abs(s - cur[0]) > 5 or abs(e - cur[1]) > 5:
                        last_view_range.set((s, e))
        except Exception:
            pass

    # ── Per-feature color updates ──────────────────────────────────────────
    @reactive.effect
    def _update_colors():
        """Watch all color swatch inputs and update session_colors.
        Color input IDs are col_<safe_id>.
        """
        colors = dict(DEFAULT_COLORS)  # start fresh from defaults
        # Core structural types
        for ft in ("CDS", "five_prime_UTR", "three_prime_UTR", "intron"):
            try:
                val = input[f"col_{_safe_id(ft)}"]()
                if val:
                    colors[ft] = val
            except Exception:
                pass
        # Dynamically loaded types
        for ft in avail_fts():
            try:
                val = input[f"col_{_safe_id(ft)}"]()
                if val:
                    colors[ft] = val
            except Exception:
                pass
        # Priority groups
        for g, _ in priority_groups():
            try:
                val = input[f"col_{_safe_id(g)}"]()
                if val:
                    colors[g] = val
            except Exception:
                pass
        with reactive.isolate():
            cur = session_colors()
        if colors != cur:
            session_colors.set(colors)

    # ── Loading progress ───────────────────────────────────────────────────
    @output
    @render.ui
    def load_progress():
        steps = load_steps()
        if not steps:
            return ui.div()
        log = "\n".join(f"* {s}" for s in steps)
        spinner = (
            ui.tags.span(
                ui.tags.span({
                    "class": "spinner-border spinner-border-sm me-1",
                    "role": "status", "aria-hidden": "true",
                }),
                " Loading...",
                style="font-size:0.86em; color:#3498db;",
            ) if loading() else ui.div()
        )
        return ui.div(spinner, ui.div(log, {"class": "progress-log"}))

    # ── Feature toggle panel ───────────────────────────────────────────────
    def _color_swatches(ft_key: str, current_color: str):
        """Render a clickable current-color dot + hidden palette row.
        Clicking the dot toggles the palette open/closed.
        Swatch clicks send the color directly via Shiny.setInputValue().
        """
        input_id = f"col_{_safe_id(ft_key)}"
        swatches = []
        for hex_color in PALETTE:
            selected = hex_color.upper() == current_color.upper()
            swatches.append(
                ui.tags.span(
                    {"class": "color-swatch" + (" selected" if selected else ""),
                     "style": f"background:{hex_color};",
                     "data-ft": input_id,
                     "data-color": hex_color,
                     "title": hex_color},
                )
            )
        return ui.div(
            {"class": "swatch-row"},
            *swatches,
        )

    def _switch_val(input_id, default):
        """Return current toggle state without taking a reactive dependency.
        Using isolate() prevents toggle cards from re-rendering every time a
        switch is clicked — which was causing the plot bounce loop."""
        try:
            with reactive.isolate():
                return bool(input[input_id]())
        except Exception:
            return default

    @output
    @render.ui
    def gene_model_card():
        """
        Card 1: CDS / exon / UTR / intron toggles.
        Always shown once a region is loaded; hidden before first load.
        No header — the toggle types are self-explanatory.
        """
        colors   = session_colors()
        txs      = tx_data()
        has_mrna = any(tx["tx_type"] == "mRNA" for tx in txs)

        coding_block_types = {
            ft for tx in txs if tx["tx_type"] == "mRNA"
            for (_, _, ft) in tx["blocks"]
        }
        has_cds     = "CDS"             in coding_block_types
        has_exon    = "exon"            in coding_block_types
        has_5utr    = "five_prime_UTR"  in coding_block_types
        has_3utr    = "three_prime_UTR" in coding_block_types
        has_introns = "intron"          in coding_block_types

        core = []
        if has_mrna:
            if has_cds:
                core.append(("CDS",            "CDS",    True))
            if has_exon:
                core.append(("exon",           "Exons",  True))
            if has_5utr:
                core.append(("five_prime_UTR",  "5' UTR", True))
            if has_3utr:
                core.append(("three_prime_UTR", "3' UTR", True))
            if has_introns:
                core.append(("intron",          "Introns", False))

        if not core:
            return ui.div()

        items = []
        for key, label, default in core:
            color = colors.get(key, "#9E9E9E")
            items.append(ui.div(
                {"class": "ft-item"},
                ui.div(
                    {"class": "ft-row"},
                    ui.input_switch(f"ft_{key}", "", value=_switch_val(f"ft_{key}", default)),
                    ui.tags.span(
                        {"class": "ft-row-label"},
                        ui.tags.span({"class": "dot color-edit-btn",
                                     "style": f"background:{color};",
                                     "title": "Change color"}),
                        label,
                    ),
                ),
                _color_swatches(key, color),
            ))
        return ui.div({"class": "card"}, *items)

    @output
    @render.ui
    def other_annotations_card():
        """
        Card 3: everything in avail_fts() — non-coding tx types, extra
        feature types not claimed by a priority group.
        Hidden when empty.
        """
        fts = avail_fts()
        if not fts:
            return ui.div()

        colors = session_colors()
        items  = []
        for ft in fts:
            color = colors.get(ft, "#9E9E9E")
            items.append(ui.div(
                {"class": "ft-item"},
                ui.div(
                    {"class": "ft-row"},
                    ui.input_switch(f"ft_{ft}", "", value=_switch_val(f"ft_{ft}", True)),
                    ui.tags.span(
                        {"class": "ft-row-label"},
                        ui.tags.span({"class": "dot color-edit-btn",
                                     "style": f"background:{color};",
                                     "title": "Change color"}),
                        ft.replace("_", " "),
                    ),
                ),
                _color_swatches(ft, color),
            ))
        return ui.div({"class": "card"}, *items)

    # ── Priority group card (hidden when no groups configured or none in region) ─
    @output
    @render.ui
    def priority_card():
        groups  = priority_groups()
        pg_data = variants_data()
        # Hide entirely if no groups configured
        if not groups:
            return ui.div()
        # Hide if groups are configured but none have features in this region
        has_features = any(pg_data.get(g) for g, _ in groups)
        if not has_features and region() is not None:
            return ui.div()
        return ui.div(
            {"class": "card"},
            ui.output_ui("priority_toggles"),
        )

    # ── Priority group toggle panel ────────────────────────────────────────
    @output
    @render.ui
    def priority_toggles():
        def _switch_val(input_id, default):
            try:
                with reactive.isolate():
                    return bool(input[input_id]())
            except Exception:
                return default

        colors   = session_colors()
        pg_data  = variants_data()   # {group_name: [list of features]}
        groups   = priority_groups()

        if not groups:
            return ui.p("No priority groups configured.",
                        style="color:#aaa; font-size:0.85em;")

        items = []
        for g, _ in groups:
            # Only show the toggle if this group has features in the loaded region
            if not pg_data.get(g):
                continue
            color = colors.get(g, FEATURE_COLORS.get(g, "#9E9E9E"))
            switch_id = f"pg_{_safe_id(g)}"
            items.append(ui.div(
                {"class": "ft-item"},
                ui.div(
                    {"class": "ft-row"},
                    ui.input_switch(switch_id, "", value=_switch_val(switch_id, True)),
                    ui.tags.span(
                        {"class": "ft-row-label"},
                        ui.tags.span({"class": "dot color-edit-btn",
                                     "style": f"background:{color};",
                                     "title": "Change color"}),
                        g,
                    ),
                ),
                _color_swatches(g, color),
            ))
        if not items:
            return ui.p("No priority features in this region.",
                        style="color:#aaa; font-size:0.85em;")
        return ui.div(*items)

    # ── Loading info card ──────────────────────────────────────────────────
    # Shown above the plot while the DB is loading (region is None).
    # Disappears automatically when the first region loads.
    @output
    @render.text
    def db_ready_flag():
        return "true" if db_ready() else "false"

    @output
    @render.text
    def startup_status():
        return startup_msg()

    # Only depends on region() so it re-renders exactly once.
    @output
    @render.ui
    def loading_card():
        if not startup_msg():
            return ui.div()
        from config import DB_PATH, FASTA_PATH
        from ui import APP_VERSION
        return ui.div(
            {"class": "card", "style": "padding: 24px 28px;"},
            ui.div(
                {"style": "display:flex; align-items:center; gap:14px;"},
                ui.tags.div(
                    {"class": "spinner-border text-primary", "role": "status"},
                    ui.tags.span({"class": "visually-hidden"}, "Loading..."),
                ),
                ui.div(
                    ui.tags.h5(
                        f"GFF \u2192 GenBank \u00a0 {APP_VERSION}",
                        style="margin:0 0 4px; color:#1a252f;",
                    ),
                    ui.tags.p(
                        "Loading database and FASTA files — the app will be ready shortly.",
                        style="margin:0 0 8px; color:#555; font-size:0.9em;",
                    ),
                    ui.tags.p(
                        ui.tags.b("Database: "),
                        ui.tags.code(DB_PATH.name),
                        ui.tags.br(),
                        ui.tags.b("FASTA:    "),
                        ui.tags.code(FASTA_PATH.name),
                        style="margin:0; font-size:0.85em; color:#555;",
                    ),
                    ui.tags.p(
                        "If the database fails to load, use ",
                        ui.tags.b("Custom Data Files"),
                        " in the sidebar to upload your own.",
                        style="margin:8px 0 0; font-size:0.8em; color:#aaa;",
                    ),
                ),
            ),
        )

    # ── Preview plot ───────────────────────────────────────────────────────
    @render_widget
    def preview_plot():
        # Read region WITHOUT isolate so we depend on it (re-render when it changes).
        # All other startup-changing reactives (db_ready, startup_msg) are NOT
        # read here at all, so they cannot cause a re-render or spinner.
        r = region()
        if r is None:
            from config import DB_PATH, FASTA_PATH
            from ui import APP_VERSION
            # Loading info placeholder — shown until the first region loads.
            # Replaced automatically when real data arrives.
            fig = go.FigureWidget()
            fig.update_layout(
                xaxis=dict(visible=False), yaxis=dict(visible=False),
                plot_bgcolor="#fafafa", paper_bgcolor="#fafafa",
                height=300,
                margin=dict(l=20, r=20, t=20, b=20),
                annotations=[
                    dict(
                        text=f"<b>GFF \u2192 GenBank   {APP_VERSION}</b>",
                        showarrow=False,
                        xref="paper", yref="paper", x=0.5, y=0.80,
                        font=dict(size=20, color="#1a252f"),
                    ),
                    dict(
                        text="Loading database and FASTA files...",
                        showarrow=False,
                        xref="paper", yref="paper", x=0.5, y=0.62,
                        font=dict(size=13, color="#7f8c8d"),
                    ),
                    dict(
                        text=f"<b>Database:</b> {DB_PATH.name}",
                        showarrow=False,
                        xref="paper", yref="paper", x=0.5, y=0.46,
                        font=dict(size=11, color="#555"),
                        bgcolor="#eaf4fb", borderpad=4,
                    ),
                    dict(
                        text=f"<b>FASTA:</b>       {FASTA_PATH.name}",
                        showarrow=False,
                        xref="paper", yref="paper", x=0.5, y=0.30,
                        font=dict(size=11, color="#555"),
                        bgcolor="#eaf4fb", borderpad=4,
                    ),
                    dict(
                        text="If the database fails to load, use <b>Custom Data Files</b> in the sidebar.",
                        showarrow=False,
                        xref="paper", yref="paper", x=0.5, y=0.10,
                        font=dict(size=10, color="#aaa"),
                    ),
                ],
            )
            return fig

        chrom, load_start, load_end = r
        from config import VIEW_FLANK

        # Read reset_view to take a reactive dependency on it — when the
        # Reset button fires, this increments and triggers a re-render.
        rv = reset_view()

        # Read last_view_range under isolate() so that JS polling
        # updating it every ~1s does NOT trigger a re-render of this
        # plot.  We only need it as a one-shot seed value: use it when
        # it exists and is within the loaded region, otherwise fall back
        # to gene +/- VIEW_FLANK.  Without isolate() every poll update
        # causes a re-render which nudges Plotly's axis, which causes
        # another poll update — an infinite oscillation loop.
        with reactive.isolate():
            lvr = last_view_range()
            iv  = initial_view()

        # If a reset was requested, use the stored initial view range.
        if rv > 0 and iv is not None:
            view_start, view_end = iv
        elif lvr is not None and lvr[0] >= load_start and lvr[1] <= load_end:
            view_start, view_end = lvr
        else:
            gb = gene_bounds()
            if gb:
                g_s, g_e = gb
                view_start = max(load_start, g_s - VIEW_FLANK)
                view_end   = min(load_end,   g_e + VIEW_FLANK)
            else:
                view_start, view_end = load_start, load_end

        # Build as FigureWidget so we can register the on_relayout callback.
        # _toggle_state is the debounced gate — reading it here means the
        # plot only re-renders after 800ms of toggle inactivity.
        # active_ftypes / priority_group_visibility are then read under isolate()
        # so they don't add extra reactive dependencies of their own.
        _toggle_state()   # depend on debounced state, not raw inputs
        with reactive.isolate():
            af  = active_ftypes()
            vv  = priority_group_visibility()
            sm  = input.strand_mode()
            col = session_colors()

        # build_preview returns a go.Figure; convert it here.
        plain_fig = P.build_preview(
            tx_data(), extra_data(), variants_data(),
            region_start=load_start, region_end=load_end,
            load_start=load_start, load_end=load_end,
            view_start=view_start, view_end=view_end,
            active_ftypes=af,
            variant_group_visibility=vv,
            strand_mode=sm,
            uirevision=load_count(),
            color_map=col,
        )
        fw = go.FigureWidget(plain_fig)
        fw.layout.width = None   # let CSS/container control width
        # Do NOT set autosize=True — that would override the explicit height
        # computed by build_preview to fit all annotation rows.  We only want
        # autosize on the width axis, which is handled by the CSS rules in ui.py.
        fw.update_layout(autosize=False)

        # No reactive zoom capture — the current x-axis range is read
        # directly from fw.layout.xaxis.range at download time, so panning
        # and zooming never trigger a re-render.
        return fw

    # ── GenBank download ───────────────────────────────────────────────────
    def _current_window():
        """
        Return the current visible x-axis window as (chrom, start, end),
        with start < end regardless of strand.
        """
        r = region()
        if r is None:
            return ("region", 0, 0)
        chrom, load_start, load_end = r

        # Strategy 1: JS input — with sanity check on span
        try:
            rng = input.plotly_xrange()
            if rng and "x0" in rng and "x1" in rng:
                raw_s = int(round(min(rng["x0"], rng["x1"])))
                raw_e = int(round(max(rng["x0"], rng["x1"])))
                if raw_s >= 0 and raw_s < raw_e:
                    s = max(load_start, raw_s)
                    e = min(load_end,   raw_e)
                    if s < e:
                        return (chrom, s, e)
        except Exception:
            pass

        # Strategy 2: FigureWidget layout
        try:
            rng = preview_plot.widget.layout.xaxis.range
            if rng and len(rng) == 2:
                s = max(load_start, int(round(min(rng[0], rng[1]))))
                e = min(load_end,   int(round(max(rng[0], rng[1]))))
                if s < e:
                    return (chrom, s, e)
        except Exception as ex:
            print(f"[download] Could not read widget range: {ex}")

        return r

    def _window_in_loaded_region(w):
        """Return True if window w is fully within the loaded region."""
        r = region()
        if r is None or w is None:
            return False
        _, load_start, load_end = r
        _, w_start, w_end = w
        return w_start >= load_start and w_end <= load_end

    @render.download(
        filename=lambda: G.make_filename(*_current_window(),
                                        strand=input.strand_mode())
    )
    def download_gb():
        r = region()
        req(r)
        chrom, load_start, load_end = r
        w = _current_window()

        # Check if the current view window is within the loaded region.
        if not _window_in_loaded_region(w):
            _, w_start, w_end = w
            print(f"[download_gb] WARNING: view {w_start:,}-{w_end:,} "
                  f"outside loaded region {load_start:,}-{load_end:,}")
            # Can't call ui.modal_show() from inside a download generator.
            # Signal to the _oor_modal_watcher effect to show the modal.
            oor_modal_data.set((chrom, load_start, load_end, w_start, w_end))
            return   # abort download (yields nothing = empty file)

        print(f"[download_gb] Window: {w[0]}:{w[1]:,}-{w[2]:,}")
        _, dl_start, dl_end = w

        # ── Sequence: extract from FASTA at download time ────────────────
        # Sequence is not cached during region load (it's not needed for the
        # plot), so we read it here on demand from the indexed FASTA.
        # pyfaidx random-access reads are fast for any window size.
        fa = _fasta()
        try:
            window_seq = D.extract_sequence(fa, chrom, dl_start, dl_end)
        except Exception as e:
            raise ValueError(
                f"Could not extract sequence for {chrom}:{dl_start:,}-{dl_end:,}. "
                f"The FASTA may not contain chromosome '{chrom}', or the FASTA "
                "and GFF database may be from different assemblies. "
                f"(Detail: {e})"
            )

        # ── Annotations: filter already-loaded cache — no DB round-trip ──
        # tx_data / extra_data / variants_data were loaded for the full
        # load region at Load time.  The download window is always a
        # sub-window of that, so we just filter by overlap.  This avoids
        # 3 redundant full region scans of the gffutils SQLite database
        # (each of which was taking ~10s on a large DB).
        def _overlaps(s, e):
            return s <= dl_end and e >= dl_start

        cached_txs = [
            tx for tx in tx_data()
            if _overlaps(tx["start"], tx["end"])
        ]

        cached_feats = {}
        for ftype, feats_list in extra_data().items():
            filtered = [f for f in feats_list if _overlaps(f["start"], f["end"])]
            if filtered:
                cached_feats[ftype] = filtered

        cached_vars = {}
        for group, vlist in variants_data().items():
            filtered = [v for v in vlist if _overlaps(v["pos"], v["end"])]
            if filtered:
                cached_vars[group] = filtered

        print(f"[download_gb] Using cached data — "
              f"{len(cached_txs)} tx, "
              + ", ".join(f"{g}:{len(v)}" for g, v in cached_vars.items()))

        gb = G.build_genbank(
            window_seq, chrom, dl_start, dl_end,
            cached_txs, cached_feats, cached_vars,
            active_ftypes=active_ftypes(),
            variant_group_visibility=priority_group_visibility(),
            strand_mode=input.strand_mode(),
            color_map=session_colors(),
        )
        yield gb.encode("utf-8")

    # ── Watch for out-of-range modal requests ─────────────────────────────
    # ── Large region confirmation modal ────────────────────────────────────
    @reactive.effect
    def _large_region_modal_watcher():
        data = large_region_data()
        if data is None:
            return
        chrom, g_s, g_e, load_start, load_end = data
        bp = load_end - load_start
        m = ui.modal(
            ui.p(f"The requested region is {bp:,} bp, which may be slow to load."),
            ui.p(f"Region: {chrom}:{load_start:,}–{load_end:,}"),
            ui.p("Regions larger than 200,000 bp can take a long time and may "
                 "cause the app to become unresponsive."),
            ui.p("Would you like to load it anyway?"),
            footer=ui.div(
                ui.input_action_button(
                    "confirm_large_region_btn", "Yes — load anyway",
                    class_="btn btn-warning me-2",
                ),
                ui.modal_button("No — cancel", class_="btn btn-secondary"),
            ),
            title="Large region warning",
            easy_close=True,
        )
        ui.modal_show(m)
        large_region_data.set(None)

    @reactive.effect
    @reactive.event(input.confirm_large_region_btn)
    def _confirm_large_region():
        ui.modal_remove()
        large_region_confirmed.set(True)
        ui.update_action_button("load_btn")

    # ui.modal_show() cannot be called from inside a @render.download generator
    # (different execution context). Instead, download_gb() sets oor_modal_data
    # and this effect, running in the normal reactive context, shows the modal.
    @reactive.effect
    def _oor_modal_watcher():
        data = oor_modal_data()
        if data is None:
            return
        chrom, load_start, load_end, w_start, w_end = data
        m = ui.modal(
            ui.p("The current view extends outside the loaded data region."),
            ui.p(f"Loaded:       {chrom}:{load_start:,}–{load_end:,}"),
            ui.p(f"Current view: {chrom}:{w_start:,}–{w_end:,}"),
            ui.p("Would you like to load data for the current view?"),
            footer=ui.div(
                ui.input_action_button(
                    "load_view_btn", "Yes — load this region",
                    class_="btn btn-primary me-2",
                ),
                ui.modal_button("No — cancel", class_="btn btn-secondary"),
            ),
            title="Data not loaded for current view",
            easy_close=True,
        )
        ui.modal_show(m)
        oor_modal_data.set(None)   # reset so it doesn't re-fire

    # ── "Yes — load this region" button from the out-of-range modal ────────
    @reactive.effect
    @reactive.event(input.load_view_btn)
    def _load_view_region():
        """
        Triggered by the modal's Yes button: loads the current view window
        as a new region, then closes the modal.
        """
        ui.modal_remove()
        w = _current_window()
        if w[0] == "region":
            return
        chrom, w_start, w_end = w
        # Set the coordinate inputs and trigger a load
        ui.update_text("region_input",
                       value=f"{chrom}:{w_start}-{w_end}")
        # Programmatically trigger Load by clicking the button
        # (Shiny for Python doesn't have a direct trigger_input API,
        # so we set a flag that _load watches)
        load_view_pending.set(True)

    load_view_pending = reactive.Value(False)

    @reactive.effect
    def _check_load_view_pending():
        """Out-of-range reload: load the current view window as a new region."""
        if not load_view_pending():
            return
        load_view_pending.set(False)
        w = _current_window()
        if w[0] == "region":
            return
        chrom, w_start, w_end = w
        err_msg.set("")
        load_steps.set([])
        loading.set(True)
        def step(msg): load_steps.set(load_steps() + [msg])
        try:
            db = _gff_db()
            step(f"Loading view region {chrom}:{w_start:,}-{w_end:,}...")
            _load_region(chrom, w_start, w_end, db,
                         view_start=w_start, view_end=w_end,
                         step=step)
        except Exception as exc:
            err_msg.set(f"Error: {exc}")
        finally:
            loading.set(False)

    # ── Setup app download ─────────────────────────────────────────────────
    @render.download(filename="gff2genbank_setup.zip")
    def download_setup_app():
        root = Path(__file__).parent
        # Top-level files needed to run setup_app.py
        top_level = [
            "setup_app.py",
            "prepare_gff.py",
            "build_db.py",
            "config.py",
            "ui.py",
        ]
        # setup_gui/ package files (skip __pycache__)
        setup_gui_files = [
            p for p in (root / "setup_gui").iterdir()
            if p.is_file() and p.suffix != ".pyc"
        ]
        # setup/ package files needed by engine.py (skip __pycache__)
        setup_files = [
            p for p in (root / "setup").iterdir()
            if p.is_file() and p.suffix != ".pyc"
        ]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in top_level:
                p = root / name
                if p.exists():
                    zf.write(p, name)
            for p in setup_gui_files:
                zf.write(p, f"setup_gui/{p.name}")
            for p in setup_files:
                zf.write(p, f"setup/{p.name}")
        yield buf.getvalue()
