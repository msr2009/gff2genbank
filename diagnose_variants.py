#!/usr/bin/env python3
"""
diagnose_variants.py
--------------------
Inspect the GFF database to find exactly which feature types are used
by each variant source group, and how many records exist per type.

Also checks a specific region (default: ced-3) to show what's actually
in the database vs what the app currently queries for.

Usage:
    python diagnose_variants.py path/to/prepared.gff3.gz.db
    python diagnose_variants.py path/to/prepared.gff3.gz.db --region IV:13190000-13210000
"""

import argparse
import sys
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db", help="Path to gffutils .db file")
    parser.add_argument("--region", default="IV:13190000-13210000",
                        help="Region to inspect (chrom:start-end)")
    args = parser.parse_args()

    import gffutils
    db = gffutils.FeatureDB(args.db)

    # Current VARIATION_TYPES from config
    current_variation_types = {
        "point_mutation", "deletion", "insertion",
        "substitution", "tandem_duplication",
        "complex_change_in_nucleotide_sequence",
        "sequence_alteration",
    }

    # Current VARIANT_GROUPS sources from config
    variant_sources = {"Allele", "Million_mutation", "NBP_knockout", "KO_consortium"}

    # ------------------------------------------------------------------
    # 1. Scan all feature types that appear with variant sources
    # ------------------------------------------------------------------
    print("=" * 70)
    print("FEATURE TYPES USED BY VARIANT SOURCES (entire database)")
    print("=" * 70)

    type_source_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # We need to scan all features — gffutils doesn't index by source easily,
    # so we iterate all features (this may be slow for large DBs)
    print("Scanning database (may take a moment)...")
    for feat in db.all_features():
        if feat.source in variant_sources:
            type_source_counts[feat.featuretype][feat.source] += 1

    if not type_source_counts:
        print("  No features found with variant sources!")
        print(f"  Variant sources searched: {variant_sources}")
    else:
        for ftype in sorted(type_source_counts):
            sources = dict(type_source_counts[ftype])
            in_config = "YES" if ftype in current_variation_types else "MISSING FROM CONFIG"
            print(f"  {ftype:<45} {in_config}")
            for src, count in sorted(sources.items()):
                print(f"      {src}: {count:,} records")

    # ------------------------------------------------------------------
    # 2. Check the specific region
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print(f"REGION CHECK: {args.region}")
    print("=" * 70)

    try:
        chrom_str, coords = args.region.split(":")
        start_str, end_str = coords.replace(",", "").split("-")
        chrom = chrom_str
        start = int(start_str)
        end   = int(end_str)
    except Exception as e:
        print(f"Bad region format: {e}")
        sys.exit(1)

    print(f"\nAll features with variant sources in {chrom}:{start:,}-{end:,}:")
    print(f"{'Feature type':<35} {'Source':<20} {'Name':<20} {'Pos'}")
    print("-" * 85)

    found = 0
    has_public_name = 0
    for feat in db.region(seqid=chrom, start=start, end=end,
                           completely_within=False):
        if feat.source not in variant_sources:
            continue
        found += 1
        pub = feat.attributes.get("public_name", ["(no public_name)"])[0]
        if "public_name" in feat.attributes:
            has_public_name += 1
        in_config = "" if feat.featuretype in current_variation_types else " <-- MISSING"
        print(f"  {feat.featuretype:<33} {feat.source:<20} {pub:<20} "
              f"{feat.start:,}-{feat.end:,}{in_config}")

    print(f"\nTotal variant-source features in region: {found}")
    print(f"With public_name attribute: {has_public_name}")

    # ------------------------------------------------------------------
    # 3. Show what the current code would actually return
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("WHAT CURRENT CODE RETURNS (querying only VARIATION_TYPES)")
    print("=" * 70)
    print(f"VARIATION_TYPES queried: {sorted(current_variation_types)}")
    print()

    returned = 0
    for feat in db.region(seqid=chrom, start=start, end=end,
                           featuretype=list(current_variation_types),
                           completely_within=False):
        if feat.source not in variant_sources:
            continue
        pub = feat.attributes.get("public_name", ["(no public_name)"])[0]
        print(f"  {feat.featuretype:<33} {feat.source:<20} {pub}")
        returned += 1

    print(f"\nTotal returned by current code: {returned}")
    missing = found - returned
    if missing > 0:
        print(f"MISSING: {missing} variant features not returned due to "
              f"feature type not being in VARIATION_TYPES")

    # ------------------------------------------------------------------
    # 4. Recommended fix
    # ------------------------------------------------------------------
    missing_types = {ftype for ftype in type_source_counts
                     if ftype not in current_variation_types}
    if missing_types:
        print()
        print("=" * 70)
        print("RECOMMENDED FIX: Add these types to VARIATION_TYPES in config.py:")
        print("=" * 70)
        for t in sorted(missing_types):
            print(f"  \"{t}\",")


if __name__ == "__main__":
    main()
