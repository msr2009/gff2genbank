"""
genbank.py
----------
Serialises the current region and annotations as a GenBank flat file.

Key rules:
  - No mRNA features in output (they cause downstream tools to duplicate CDS).
  - Each unique CDS is written as a single feature with a CompoundLocation
    (join) across all its exons, in genomic coordinate order (low to high).
  - UTRs are written as individual features (one per exon interval).
  - Colors use ApEinfo qualifiers (/ApEinfo_fwdcolor, /ApEinfo_revcolor)
    which are read by ApE, Benchling, and SnapGene.
  - Each CDS feature carries a /translation qualifier with the amino acid
    sequence derived from the joined CDS nucleotides.
  - Output file always named chrom_start_end.gb.
"""

import io
from collections import defaultdict

from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqFeature import SeqFeature, FeatureLocation, CompoundLocation

from config import FEATURE_COLORS, ALWAYS_HANDLED, ORGANISM_NAME, ORGANISM_SHORT


def _color(ftype: str, color_map: dict | None = None) -> str:
    if color_map is not None:
        return color_map.get(ftype, "#9E9E9E")
    return FEATURE_COLORS.get(ftype, "#888888")


def _ape_color_quals(hex_color: str) -> dict:
    """
    Return ApEinfo color qualifiers used by ApE, Benchling, and SnapGene.
    Using /ApEinfo_fwdcolor and /ApEinfo_revcolor instead of the non-standard
    /color qualifier ensures colours are read by all major plasmid editors.
    """
    return {
        "ApEinfo_fwdcolor": [hex_color],
        "ApEinfo_revcolor": [hex_color],
    }


def _translate_cds(seq_obj: Seq, locs: list[FeatureLocation],
                   strand_val: int) -> str:
    """
    Extract and translate the CDS nucleotide sequence from a list of
    FeatureLocation objects (already in local coordinates).

    Exons are sorted by start position. For minus-strand features the
    reverse complement is taken before translation.

    Returns the amino acid string, stopping at the first stop codon
    (stop codon not included in output, matching ApE convention).
    Returns empty string on any error.
    """
    try:
        # Sort exons by genomic position (ascending)
        sorted_locs = sorted(locs, key=lambda l: l.start)
        # Extract exons and uppercase for translation — case of seq_obj is irrelevant here
        nuc = Seq("".join(str(seq_obj[l.start:l.end]).upper() for l in sorted_locs))
        if strand_val == -1:
            nuc = nuc.reverse_complement()
        protein = nuc.translate(to_stop=True)
        return str(protein)
    except Exception as e:
        print(f"[translate_cds] {e}")
        return ""


def build_genbank(
    seq_str: str,
    chrom: str,
    region_start: int,
    region_end: int,
    transcripts: list[dict],
    extra_features: dict[str, list],
    variants_by_group: dict[str, list],
    active_ftypes: set[str],
    variant_group_visibility: dict[str, bool],
    strand_mode: str,
    color_map: dict | None = None,
) -> str:
    """
    Build a GenBank flat-file string.

    Args:
        seq_str       — raw + strand genomic sequence
        chrom         — chromosome name
        region_start  — 1-based start of window
        region_end    — 1-based end of window
        transcripts   — from data.transcript_structures()
        extra_features— from data.features_in_region()
        variants_by_group — from data.variant_features()
        active_ftypes — currently toggled-on feature types
        variant_group_visibility — {group_name: bool}
        strand_mode   — '+' or '-'

    Returns:
        GenBank flat-file string.
    """
    from Bio import SeqIO

    # ------------------------------------------------------------------
    # Coordinate system
    # ------------------------------------------------------------------
    # GFF:      1-based, fully closed  [start, end]  (both inclusive)
    # Biopython FeatureLocation: 0-based, half-open  [s, e)
    #
    # + strand:
    #   s = start_g - region_start          (1-based -> 0-based offset)
    #   e = end_g   - region_start + 1      (+1: inclusive -> exclusive)
    #
    # - strand (sequence is reverse-complemented):
    #   s = local_start(end_g)              (high genomic -> low local)
    #   e = local_end(start_g)              (low genomic  -> high local)

    if strand_mode == "-":
        raw_seq = Seq(seq_str).reverse_complement()
        def local_start(pos: int) -> int:
            return region_end - pos
        def local_end(pos: int) -> int:
            return region_end - pos + 1
    else:
        raw_seq = Seq(seq_str)
        def local_start(pos: int) -> int:
            return pos - region_start
        def local_end(pos: int) -> int:
            return pos - region_start + 1

    def feature_strand(gff_strand: str) -> int:
        """
        Convert a GFF strand character to a Biopython strand integer,
        accounting for the display orientation (strand_mode).

        When displaying in minus-strand mode the sequence is reverse-
        complemented, so all genomic strands flip:
          '+' feature in '-' view  ->  strand -1 in local coords
          '-' feature in '-' view  ->  strand +1 in local coords
        """
        if gff_strand == "+":
            return 1 if strand_mode == "+" else -1
        elif gff_strand == "-":
            return -1 if strand_mode == "+" else 1
        return 0  # unstranded

    # Collect all CDS intervals across all mRNA transcripts so we can
    # uppercase just those positions in the final output.
    # local_start() converts genomic coords to 0-based local offsets.
    cds_intervals: list[tuple[int, int]] = []
    for tx in transcripts:
        if tx["tx_type"] == "mRNA":
            for (s, e, ftype) in tx["blocks"]:
                if ftype == "CDS":
                    lo = local_start(s)
                    hi = local_start(e)
                    cds_intervals.append((min(lo, hi), max(lo, hi)))

    # Use the raw sequence as-is for the SeqRecord — Biopython normalises
    # case to lowercase in genbank output regardless, so case is applied
    # only in the post-processing step below.
    final_seq = raw_seq

    record = SeqRecord(
        final_seq,
        id=f"{chrom}:{region_start}-{region_end}",
        name=f"{chrom}_{region_start}",
        description=(
            f"{ORGANISM_SHORT} {chrom}:{region_start}-{region_end} "
            f"({'minus' if strand_mode == '-' else 'plus'} strand)"
        ),
    )
    record.annotations["molecule_type"] = "DNA"
    record.annotations["organism"]      = ORGANISM_NAME

    def clamp(n: int) -> int:
        return max(0, min(n, len(final_seq)))

    def make_location(start_g: int, end_g: int,
                      gff_strand: str = "+") -> FeatureLocation | None:
        """
        Convert a GFF 1-based closed interval to a Biopython 0-based
        half-open FeatureLocation with the correct strand.

        start_g / end_g are always low/high genomic coords (GFF convention).
        gff_strand is the feature's own strand character ("+", "-", or ".").
        """
        if strand_mode == "-":
            s = local_start(end_g)
            e = local_end(start_g)
        else:
            s = local_start(start_g)
            e = local_end(end_g)
        s, e = clamp(s), clamp(e)
        if s >= e:
            return None
        return FeatureLocation(s, e, strand=feature_strand(gff_strand))

    def add_simple(start_g, end_g, ftype, quals, gff_strand="+"):
        loc = make_location(start_g, end_g, gff_strand)
        if loc:
            record.features.append(SeqFeature(loc, type=ftype, qualifiers=quals))

    # ------------------------------------------------------------------
    # Transcripts: CDS with join() + translation, UTRs individually
    # No mRNA features written (causes duplication in downstream tools).
    # ------------------------------------------------------------------
    gb_type_map = {
        "five_prime_UTR":  "5'UTR",
        "three_prime_UTR": "3'UTR",
    }

    for tx in transcripts:
        cds_by_name: dict[str, list[tuple[int, int]]] = defaultdict(list)
        utr_blocks:  list[tuple[int, int, str]] = []

        for (s, e, ftype) in tx["blocks"]:
            if ftype == "CDS":
                cds_by_name[tx["name"]].append((s, e))
            elif ftype in ("five_prime_UTR", "three_prime_UTR"):
                if ftype in active_ftypes:
                    utr_blocks.append((s, e, ftype))
            elif ftype not in active_ftypes and ftype != tx["tx_type"]:
                continue
            else:
                # Non-coding transcript exon blocks — label is just the
                # transcript name; ftype goes in /note to avoid redundancy.
                add_simple(s, e, "misc_feature", {
                    "label": [tx["name"]],
                    "note":  [ftype],
                    **_ape_color_quals(_color(ftype, color_map)),
                }, gff_strand=tx.get("strand", "+"))

        # CDS: one joined feature per transcript with translation
        if "CDS" in active_ftypes:
            tx_strand     = tx.get("strand", "+")
            tx_strand_val = feature_strand(tx_strand)
            for cds_name, intervals in cds_by_name.items():
                # GenBank join() lists exons in biological order:
                #   + strand: low coord first
                #   - strand: high coord first (complement(join(...)))
                reverse = (tx_strand_val == -1)
                intervals_sorted = sorted(
                    intervals, key=lambda iv: iv[0], reverse=reverse
                )
                locs = []
                for s, e in intervals_sorted:
                    loc = make_location(s, e, tx_strand)
                    if loc:
                        locs.append(loc)
                if not locs:
                    continue

                final_loc = locs[0] if len(locs) == 1 else CompoundLocation(locs)

                # Translate using the feature's own strand value
                protein = _translate_cds(final_seq, locs, tx_strand_val)

                quals = {
                    "label":       [cds_name],
                    **_ape_color_quals(_color("CDS", color_map)),
                }
                if protein:
                    quals["translation"] = [protein]

                record.features.append(
                    SeqFeature(final_loc, type="CDS", qualifiers=quals)
                )

        # UTRs
        for (s, e, ftype) in utr_blocks:
            add_simple(s, e, gb_type_map[ftype], {
                "label": [f"{tx['name']} {ftype}"],
                **_ape_color_quals(_color(ftype, color_map)),
            }, gff_strand=tx.get("strand", "+"))

    # ------------------------------------------------------------------
    # Other GFF features (misc_feature)
    # ------------------------------------------------------------------
    # Build the set of (source, featuretype) pairs already claimed by priority
    # groups — features matching these are drawn via the variants section below
    # and must be skipped here to avoid double-drawing.
    priority_sources: set[str] = set()
    for v_list in variants_by_group.values():
        for v in v_list:
            priority_sources.add(v.get("source", ""))

    for ftype, feats in extra_features.items():
        if ftype in ALWAYS_HANDLED or ftype not in active_ftypes:
            continue
        for feat in feats:
            if feat.get("source") in priority_sources:
                continue
            add_simple(feat["start"], feat["end"], "misc_feature", {
                "label": [feat["name"]],
                "note":  [ftype],
                **_ape_color_quals(_color(ftype, color_map)),
            }, gff_strand=feat.get("strand", "+"))

    # ------------------------------------------------------------------
    # Variants
    # ------------------------------------------------------------------
    total_vars_written = 0
    for group_name, visible in variant_group_visibility.items():
        if not visible:
            continue
        color         = _color(group_name, color_map)
        group_variants = variants_by_group.get(group_name, [])
        for v in group_variants:
            add_simple(v["pos"], v["end"], "variation", {
                "label": [v["name"]],
                "note":  [v.get("substitution", v["var_type"])
                          + f" ({group_name})"],
                **_ape_color_quals(color),
            })
            total_vars_written += 1
        print(f"[build_genbank] {group_name}: {len(group_variants)} variants "
              f"({'visible' if visible else 'hidden'})")
    print(f"[build_genbank] Total variation features written: {total_vars_written}")

    buf = io.StringIO()
    SeqIO.write(record, buf, "genbank")
    gb_str = buf.getvalue()

    # Biopython normalises the ORIGIN sequence to lowercase regardless of
    # the Seq object's case.  Post-process: find the ORIGIN block and
    # uppercase just the CDS positions.
    origin_idx = gb_str.find("\nORIGIN")
    if origin_idx != -1 and cds_intervals:
        header   = gb_str[:origin_idx + 1]   # everything up to and incl. newline
        origin   = gb_str[origin_idx + 1:]   # "ORIGIN\n        1 atcg..."

        # Strip the ORIGIN block down to just sequence characters
        # (no spaces, digits, newlines) so we can index by position,
        # then rebuild it with uppercase CDS positions.
        seq_chars = list(origin[origin.index("\n") + 1:])  # after "ORIGIN\n"

        # seq_chars contains spaces, digits, newlines interspersed.
        # Build a mapping from sequence-position -> index in seq_chars.
        pos = 0
        pos_to_idx = {}
        for i, ch in enumerate(seq_chars):
            if ch.isalpha():
                pos_to_idx[pos] = i
                pos += 1

        # Uppercase CDS positions
        for lo, hi in cds_intervals:
            lo = max(0, lo)
            hi = min(pos, hi)
            for p in range(lo, hi):
                if p in pos_to_idx:
                    seq_chars[pos_to_idx[p]] = seq_chars[pos_to_idx[p]].upper()

        origin_rebuilt = origin[:origin.index("\n") + 1] + "".join(seq_chars)
        return header + origin_rebuilt

    return gb_str


def make_filename(chrom: str, start: int, end: int, strand: str = "+") -> str:
    """
    Returns a filename encoding chromosome, coordinates, and strandedness.
    - Plus strand:  IV_13000000-13010000.gb   (low-to-high)
    - Minus strand: IV_13010000-13000000.gb   (high-to-low, matches display)
    """
    if strand == "-":
        return f"{chrom}_{end}-{start}.gb"
    return f"{chrom}_{start}-{end}.gb"
