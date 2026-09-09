"""
region2genbank.py
------------------
Batch CLI: export an annotated GenBank flat-file for a genomic region without
launching the Shiny app. Reuses the same query pipeline (data.py) and
serializer (genbank.py) as app.py. Priority groups / variant tracks are not
supported here — only plain GFF featuretypes, selected one at a time.

Usage:
    python region2genbank.py --region III:10,901,491-10,910,085 \\
        --db c_elegans.merged.db --fasta genome.fa \\
        --feature CDS --feature exon

    # See every annotation type and its count in the whole database, to know
    # what names are valid for --feature (no --region/--fasta needed):
    python region2genbank.py --db c_elegans.merged.db --list-db-features

    # Same, but scoped to one region instead of the whole database:
    python region2genbank.py --db c_elegans.merged.db --region unc-119 \\
        --list-db-features
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import gffutils
from pyfaidx import Fasta

import annotation as A
import data as D
import genbank as G
from config import DEFAULT_COLORS, LOAD_FLANK

REGION_RE = re.compile(r"^\S+:\d[\d,]*[-–]\d[\d,]*$")


def open_handles(db_path, fasta_path):
    """Build a gffutils/pyfaidx handle pair directly, bypassing data.py's
    config-bound singletons so an arbitrary --db/--fasta pair can be used."""
    db = gffutils.FeatureDB(str(db_path))
    fasta = Fasta(str(fasta_path))
    return db, fasta


def resolve_region(db, query):
    """Resolve a 'chrom:start-end' string or a gene name into (chrom, start, end)."""
    if REGION_RE.match(query.strip()):
        return D.parse_region(query)
    # Not coordinate-shaped: treat as a gene name (strand is discarded, as
    # the app does — build_genbank's strand_mode is set independently).
    chrom, start, end, _strand = D.gene_coords(db, query)
    return chrom, start, end


def compute_window(db, chrom, start, end, flank, expand, max_bp):
    """Pad the requested region by flank, then optionally snap outward to
    fully contain any overhanging mRNA (the app's default Load behavior)."""
    load_start = max(1, start - flank)
    load_end = end + flank
    if expand:
        load_start, load_end, _ = A.extend_load_window(
            db, chrom, load_start, load_end, max_load_bp=max_bp
        )
    if load_end - load_start > max_bp:
        print(
            f"Warning: window {chrom}:{load_start:,}-{load_end:,} "
            f"({load_end - load_start:,}bp) exceeds --max-bp ({max_bp:,}); "
            "proceeding anyway.",
            file=sys.stderr,
        )
    return load_start, load_end


def load_annotations(db, chrom, load_start, load_end):
    """Query the window once and derive transcripts, extra features, and the
    set of feature-type names available for selection in this region.
    Priority groups are ignored, so build_extra_types sees no claimed/excluded
    patterns and variant_features is never queried."""
    raw = D.query_region(db, chrom, load_start, load_end)
    txs = D.transcript_structures(db, chrom, load_start, load_end, _raw=raw)
    feats = D.features_in_region(db, chrom, load_start, load_end, _raw=raw)
    tx_types = {tx["tx_type"] for tx in txs if tx["tx_type"] != "mRNA"}
    extra_types = A.build_extra_types(feats, tx_types, [], [])
    avail_fts = sorted(tx_types | extra_types)
    return txs, feats, avail_fts


def db_feature_counts(db):
    """Return a sorted list of (featuretype, count) for every type in the whole db."""
    return sorted((ft, db.count_features_of_type(ft)) for ft in db.featuretypes())


def region_feature_counts(db, chrom, start, end):
    """Return a sorted list of (featuretype, count) for raw GFF records
    overlapping [start, end] on chrom — the same window used for export."""
    raw = D.query_region(db, chrom, start, end)
    return sorted(Counter(f.featuretype for f in raw).items())


def select_active(avail_fts, requested):
    """Resolve --feature names into the active_ftypes set. Each name must be a
    GFF featuretype available in the region; unknown names are a hard error."""
    valid_ftypes = set(avail_fts) | {"CDS", "exon", "five_prime_UTR", "three_prime_UTR", "intron"}
    unknown = [n for n in requested if n not in valid_ftypes]
    if unknown:
        raise ValueError(
            f"Unknown --feature name(s): {', '.join(unknown)}.\n"
            f"Valid feature types: {', '.join(sorted(valid_ftypes)) or '(none)'}"
        )
    return set(requested)


def export_region(db, fasta, query, feature_names, expand, flank, max_bp, strand):
    """Run the full region -> GenBank pipeline and return (gb_text, chrom, start, end)."""
    chrom, start, end = resolve_region(db, query)
    if chrom not in D.db_chroms(db):
        raise ValueError(
            f"Chromosome '{chrom}' not found in database. "
            f"Available: {', '.join(D.db_chroms(db))}"
        )

    load_start, load_end = compute_window(db, chrom, start, end, flank, expand, max_bp)
    txs, feats, avail_fts = load_annotations(db, chrom, load_start, load_end)
    active_ftypes = select_active(avail_fts, feature_names)

    seq = D.extract_sequence(fasta, chrom, load_start, load_end)
    gb_text = G.build_genbank(
        seq, chrom, load_start, load_end,
        txs, feats, {},   # no variant tracks — priority groups are ignored
        active_ftypes=active_ftypes,
        variant_group_visibility={},
        strand_mode=strand,
        color_map=dict(DEFAULT_COLORS),
    )
    return gb_text, chrom, load_start, load_end, avail_fts, len(txs)


def main():
    parser = argparse.ArgumentParser(
        description="Export an annotated GenBank file for a region, without the Shiny app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--region", default=None,
        help="'chrom:start-end' (1-based) or a gene name. Required for export; optional "
             "with --list-db-features to scope the listing to one region.",
    )
    parser.add_argument("--db", required=True, help="gffutils .db path")
    parser.add_argument("--fasta", default=None, help="Indexed genome FASTA (.fai required)")
    parser.add_argument(
        "--feature", dest="features", action="append", default=None, metavar="NAME",
        help="GFF featuretype to include (e.g. CDS). May be repeated; at least one "
             "is required for export. Use --list-db-features to see valid names.",
    )
    parser.add_argument(
        "--no-expand", dest="expand", action="store_false",
        help="Do not snap the window outward to fully contain overhanging mRNAs.",
    )
    parser.add_argument(
        "--flank", type=int, default=LOAD_FLANK,
        help=f"Padding added to each side of the region before loading (default: {LOAD_FLANK:,}).",
    )
    parser.add_argument(
        "--max-bp", type=int, default=200_000,
        help="Cap on the loaded window size in bp (default: 200,000).",
    )
    parser.add_argument("--strand", choices=["+", "-"], default="+", help="Display strand.")
    parser.add_argument(
        "--list-db-features", action="store_true",
        help="Print annotation featuretypes with their counts, then exit. Only --db is "
             "required. Scoped to --region if given, otherwise the whole database. Use "
             "this to find valid --feature names.",
    )
    parser.add_argument("-o", "--out", default=None, help="Output .gb path.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: --db file not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    if args.list_db_features:
        try:
            db = gffutils.FeatureDB(str(db_path))
        except Exception as e:
            print(f"Error: could not open database: {e}", file=sys.stderr)
            sys.exit(1)
        try:
            if args.region:
                chrom, start, end = resolve_region(db, args.region)
                load_start, load_end = compute_window(
                    db, chrom, start, end, args.flank, args.expand, args.max_bp
                )
                counts = region_feature_counts(db, chrom, load_start, load_end)
                print(f"Annotation types in {chrom}:{load_start:,}-{load_end:,}:")
            else:
                counts = db_feature_counts(db)
                print(f"Annotation types in {db_path.name}:")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        for ft, count in counts:
            print(f"  {ft:<30} {count:>10,}")
        return

    if args.region is None:
        print("Error: --region is required (unless using --list-db-features).", file=sys.stderr)
        sys.exit(1)
    if args.fasta is None:
        print("Error: --fasta is required (unless using --list-db-features).", file=sys.stderr)
        sys.exit(1)
    if not args.features:
        print("Error: at least one --feature is required (e.g. --feature CDS).", file=sys.stderr)
        sys.exit(1)

    fasta_path = Path(args.fasta)
    if not fasta_path.exists():
        print(f"Error: --fasta file not found: {fasta_path}", file=sys.stderr)
        sys.exit(1)

    fai_path = fasta_path.with_suffix(fasta_path.suffix + ".fai")
    if not fai_path.exists():
        print(
            f"Error: no .fai index found for {fasta_path}. "
            f"Run: samtools faidx {fasta_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        db, fasta = open_handles(db_path, fasta_path)
    except Exception as e:
        print(f"Error: could not open database or FASTA: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        gb_text, chrom, load_start, load_end, avail_fts, n_txs = export_region(
            db, fasta, args.region, args.features, args.expand, args.flank, args.max_bp, args.strand,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not avail_fts and n_txs == 0:
        print("Warning: no annotations found in this region.", file=sys.stderr)

    out_path = Path(args.out) if args.out else Path(G.make_filename(chrom, load_start, load_end, args.strand))
    out_path.write_text(gb_text)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
