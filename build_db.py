"""
build_db.py
-----------
Build a gffutils SQLite database from a prepared GFF3 file.

Run this once before deploying the app, or when you have a new GFF to use.
The output .db file can be:
  - Placed in the server data directory as the default database.
  - Uploaded via the app's "Custom Data Files" panel for a personal session.

Usage:
    python build_db.py path/to/prepared.gff3.gz [--out path/to/output.db]

Why these gffutils settings?
-----------------------------
disable_infer_genes=True, disable_infer_transcripts=True:
    Well-annotated GFF3 files already contain explicit gene and transcript
    records for every feature.  gffutils' inference scans the entire file to
    synthesise parent records that are absent — for a file that already has
    them this is pure wasted work and can cause very long build times.
    If your GFF lacks explicit gene/transcript records, remove these flags.

merge_strategy="create_unique":
    Some GFF files reuse the same ID (e.g. 'CDS:geneX') for every exon of a
    given CDS, because they are semantically the same CDS split across exons.
    With merge_strategy="merge", gffutils merges all those records into one,
    potentially losing coordinates.  "create_unique" appends a numeric suffix
    to each duplicate ID, preserving all records as distinct database entries.

Dependencies:
    conda install -c bioconda gffutils
    # or
    pip install gffutils
"""

import argparse
import sys
import time
from pathlib import Path


def _fmt_time(seconds: float) -> str:
    """Format an elapsed duration as m:ss (e.g. 0:07, 3:42, 125:09)."""
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def build(gff_path: Path, db_path: Path, force: bool = False, log=None,
          total: int | None = None) -> dict:
    """
    Create a gffutils FeatureDB from a GFF3 file.

    log:   optional callable(str) sink for progress messages. Defaults to print,
           so the CLI behaves as before; the setup GUI passes its own callback.
    total: optional expected feature count (e.g. the prepared GFF's line count)
           so progress can be reported as a percentage. Returns
           {"count", "elapsed", "skipped"}.
    """
    import gffutils

    emit = log if log is not None else (lambda m: print(m, flush=True))

    if db_path.exists() and not force:
        emit(f"Database already exists: {db_path} (enable force to rebuild).")
        return {"count": 0, "elapsed": 0.0, "skipped": True}

    emit(f"Input GFF : {gff_path}")
    emit(f"Output DB : {db_path}")

    counter = [0]
    start_t = time.time()

    def progress(f):
        counter[0] += 1
        if counter[0] % 100_000 == 0:
            el = time.time() - start_t
            rate = counter[0] / max(el, 0.001)
            if total:
                pct = min(100.0, 100.0 * counter[0] / total)
                emit(f"{counter[0]:,} / {total:,} features ({pct:.0f}%) "
                     f"({_fmt_time(el)}, {rate:,.0f}/s)")
            else:
                emit(f"{counter[0]:,} features indexed… "
                     f"({_fmt_time(el)}, {rate:,.0f}/s)")
        return f

    gffutils.create_db(
        str(gff_path),
        dbfn=str(db_path),
        force=force,
        keep_order=True,
        merge_strategy="create_unique",
        transform=progress,
        # WormBase GFF already has explicit gene and transcript records.
        # Inference is redundant and slow — disable both.
        disable_infer_genes=True,
        disable_infer_transcripts=True,
    )

    elapsed = time.time() - start_t
    emit(f"Done — {counter[0]:,} features in {_fmt_time(elapsed)}")
    return {"count": counter[0], "elapsed": elapsed, "skipped": False}


def main():
    parser = argparse.ArgumentParser(
        description="Build a gffutils database from a GFF3 file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("gff", help="GFF3 file (plain or .gz)")
    parser.add_argument("--out", default=None,
                        help="Output .db path (default: <gff>.db)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing database")
    args = parser.parse_args()

    gff_path = Path(args.gff)
    if not gff_path.exists():
        print(f"Error: file not found: {gff_path}", file=sys.stderr)
        sys.exit(1)

    db_path = Path(args.out) if args.out else Path(str(gff_path) + ".db")
    build(gff_path, db_path, force=args.force)


if __name__ == "__main__":
    main()
