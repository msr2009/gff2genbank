"""
s05_validate.py
---------------
Sanity checks after the full setup pipeline has run.

Checks:
  1. DB file exists and can be opened by gffutils
  2. FASTA file exists and is indexed (.fai present)
  3. DB and FASTA share at least one chromosome name
  4. Default region (from config) resolves to features in the DB
  5. priority_groups.tsv parses without warnings (if present)

Usage (standalone):
    python setup/s05_validate.py --db path/to/db --fasta path/to/genome.fa

Usage (via orchestrator):
    from setup.s05_validate import run
    run(session)
"""

import argparse
import sys
from pathlib import Path as _Path
_SETUP_DIR = _Path(__file__).parent
_PROJECT_ROOT = _SETUP_DIR.parent
if str(_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(_SETUP_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from pathlib import Path


STEP_ID = "s05_validate"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_db(db_path: Path) -> tuple[bool, str, object]:
    """Open the gffutils DB. Returns (ok, message, db_or_None)."""
    try:
        import gffutils
    except ImportError:
        return False, "gffutils not installed", None
    try:
        db = gffutils.FeatureDB(str(db_path))
        # Quick sanity — count feature types
        cur = db.conn.execute("SELECT COUNT(*) FROM features")
        n = cur.fetchone()[0]
        return True, f"{n:,} features loaded", db
    except Exception as e:
        return False, f"Could not open DB: {e}", None


def _check_fasta(fasta_path: Path) -> tuple[bool, str, set]:
    """Check FASTA exists and is indexed. Returns (ok, message, chrom_set)."""
    if not fasta_path.exists():
        return False, f"FASTA not found: {fasta_path}", set()
    fai = Path(str(fasta_path) + ".fai")
    if not fai.exists():
        return False, f".fai index not found — run: samtools faidx {fasta_path}", set()
    # Read chromosome names from .fai
    chroms = set()
    try:
        with open(fai) as fh:
            for line in fh:
                chroms.add(line.split("\t")[0].strip())
        return True, f"{len(chroms)} sequences in FASTA index", chroms
    except Exception as e:
        return False, f"Could not read .fai: {e}", set()


def _check_chrom_overlap(db, fasta_chroms: set) -> tuple[bool, str]:
    """Check DB and FASTA share chromosome names."""
    if db is None:
        return False, "DB unavailable"
    try:
        cur = db.conn.execute(
            "SELECT seqid, COUNT(*) as n FROM features "
            "GROUP BY seqid ORDER BY n DESC LIMIT 20"
        )
        db_chroms = {row[0] for row in cur.fetchall()}
        overlap = db_chroms & fasta_chroms
        if not overlap:
            db_sample   = sorted(db_chroms)[:5]
            fa_sample   = sorted(fasta_chroms)[:5]
            return False, (
                f"No chromosome names overlap.\n"
                f"    DB sample:    {db_sample}\n"
                f"    FASTA sample: {fa_sample}\n"
                f"    Run prepare_gff.py with --chrom-map to fix."
            )
        return True, f"{len(overlap)} shared chromosome name(s) (e.g. {sorted(overlap)[:3]})"
    except Exception as e:
        return False, f"Chromosome overlap check failed: {e}"


def _check_default_region(db, default_region: str) -> tuple[bool, str]:
    """Check the default region resolves to features."""
    if db is None:
        return False, "DB unavailable"
    try:
        # Parse region string — e.g. "III:10,901,491-10,910,085"
        chrom, coords = default_region.replace(",", "").split(":")
        start_s, end_s = coords.split("-")
        start, end = int(start_s), int(end_s)
        feats = list(db.region(seqid=chrom, start=start, end=end, completely_within=False))
        if not feats:
            return False, (
                f"Default region {default_region} returned no features. "
                f"Check DEFAULT_REGION in config.py."
            )
        return True, f"{len(feats)} features in default region {default_region}"
    except Exception as e:
        return False, f"Default region check failed: {e}"


def _check_priority_groups(tsv_path: Path) -> tuple[bool, str, list[str]]:
    """Parse priority_groups.tsv if present. Returns (ok, message, warnings)."""
    if not tsv_path.exists():
        return True, "No priority_groups.tsv configured — priority panel disabled", []

    # Reuse loader from s04 without importing server
    warnings_out: list[str] = []
    raw_rows = []
    try:
        with open(tsv_path) as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("group_name"):
                    continue
                parts = s.split("\t")
                if len(parts) < 3:
                    continue
                group  = parts[0].strip()
                source = parts[1].strip()
                feat   = parts[2].strip()
                status = parts[3].strip() if len(parts) >= 4 else "include"
                if group and source and feat:
                    raw_rows.append((group, source, feat, status))
    except Exception as e:
        return False, f"Could not read priority_groups.tsv: {e}", []

    # Dedup check
    seen: dict[tuple, str] = {}
    for group, source, feat, status in raw_rows:
        if status == "exclude":
            continue
        key = (source, feat)
        if key in seen and seen[key] != group:
            warnings_out.append(
                f"({source!r}, {feat!r}) claimed by both {seen[key]!r} and {group!r} "
                f"— {seen[key]!r} wins"
            )
        else:
            seen[key] = group

    n_groups = len({g for g, _, _, st in raw_rows if st != "exclude"})
    msg = f"{n_groups} group(s) defined in {tsv_path.name}"
    return True, msg, warnings_out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_checks(
    db_path: Path,
    fasta_path: Path,
    priority_groups_path: Path,
    default_region: str,
) -> tuple[bool, list[str]]:
    """Run all checks, print results. Returns (all_required_ok, warnings)."""

    print("\n" + "=" * 60)
    print("STEP 5: VALIDATE SETUP")
    print("=" * 60)

    all_ok   = True
    warnings = []

    def _report(label: str, ok: bool, msg: str) -> None:
        nonlocal all_ok
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {label:<35} {msg}")
        if not ok:
            all_ok = False

    print()

    # 1. DB
    db_ok, db_msg, db = _check_db(db_path)
    _report("Database opens", db_ok, db_msg)

    # 2. FASTA
    fa_ok, fa_msg, fa_chroms = _check_fasta(fasta_path)
    _report("FASTA + index", fa_ok, fa_msg)

    # 3. Chrom overlap
    if db_ok and fa_ok:
        ov_ok, ov_msg = _check_chrom_overlap(db, fa_chroms)
        _report("Chromosome name overlap", ov_ok, ov_msg)

    # 4. Default region
    if db_ok:
        dr_ok, dr_msg = _check_default_region(db, default_region)
        _report("Default region resolves", dr_ok, dr_msg)

    # 5. Priority groups
    pg_ok, pg_msg, pg_warns = _check_priority_groups(priority_groups_path)
    _report("Priority groups", pg_ok, pg_msg)
    for w in pg_warns:
        print(f"       ⚠ {w}")
        warnings.append(w)

    print()
    if all_ok and not warnings:
        print("All checks passed — setup is complete.")
    elif all_ok:
        print(f"Setup complete with {len(warnings)} warning(s).")
    else:
        print("One or more checks failed — review output above.")

    return all_ok, warnings


def run(session: dict) -> dict:
    """Orchestrator entry point."""
    from setup_log import write_step

    # Pull paths from session or config
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import config
        db_path       = Path(session.get("db_path",    str(config.DB_PATH)))
        fasta_path    = Path(session.get("fasta",      str(config.FASTA_PATH)))
        pg_path       = config.PRIORITY_GROUPS_PATH
        default_region = config.DEFAULT_REGION
    except Exception as e:
        print(f"  ERROR: could not load config: {e}")
        write_step(Path(session["data_dir"]), STEP_ID, "failed",
                   message=f"Config load error: {e}")
        return session

    data_dir = Path(session["data_dir"])
    ok, warnings = _run_checks(db_path, fasta_path, pg_path, default_region)
    status = "ok" if ok and not warnings else ("warning" if ok else "failed")
    write_step(
        data_dir, STEP_ID, status,
        message="Validation passed." if ok else "Validation failed.",
        warnings=warnings,
    )
    return session


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the GFF->GenBank app setup."
    )
    parser.add_argument("--db",    required=True, help="gffutils database path")
    parser.add_argument("--fasta", required=True, help="Genome FASTA path")
    parser.add_argument("--priority-groups", default=None,
                        help="priority_groups.tsv path (optional)")
    parser.add_argument("--region", default=None,
                        help="Region string to test (default: read from config.py)")
    args = parser.parse_args()

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import config
        default_region = args.region or config.DEFAULT_REGION
        pg_path = Path(args.priority_groups) if args.priority_groups \
                  else config.PRIORITY_GROUPS_PATH
    except Exception:
        default_region = args.region or "I:1-100000"
        pg_path = Path(args.priority_groups) if args.priority_groups \
                  else Path("priority_groups.tsv")

    ok, _ = _run_checks(
        db_path=Path(args.db),
        fasta_path=Path(args.fasta),
        priority_groups_path=pg_path,
        default_region=default_region,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
