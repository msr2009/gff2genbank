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


def build(gff_path: Path, db_path: Path, force: bool = False) -> None:
    """Create a gffutils FeatureDB from a GFF3 file."""
    import gffutils

    if db_path.exists() and not force:
        print(f"Database already exists: {db_path}")
        print("Use --force to rebuild.")
        return

    print(f"Input GFF : {gff_path}")
    print(f"Output DB : {db_path}")
    print()

    counter = [0]
    start_t = time.time()

    def progress(f):
        counter[0] += 1
        if counter[0] % 50_000 == 0:
            print(f"  {counter[0]:>8,} features  ({time.time() - start_t:.0f}s)",
                  flush=True)
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
    print(f"\nDone — {counter[0]:,} features in {elapsed:.1f}s")
    print(f"Database: {db_path}")


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
