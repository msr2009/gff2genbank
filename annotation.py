"""
annotation.py — shiny-free helpers for resolving annotation feature types and
priority groups from a loaded region.

Extracted from server.py so region2genbank.py (the batch CLI) can reuse the
exact same selection logic as the Shiny app without importing shiny/plotly.
"""

from pathlib import Path

import data as D
from config import ALWAYS_HANDLED


def build_extra_types(
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
# Validated at session startup via validate_priority_groups().
# ---------------------------------------------------------------------------

def load_priority_groups_tsv(
    tsv_path: Path,
) -> tuple[list[tuple[str, list[tuple[str, str]]]], dict[str, str]]:
    """
    Parse priority_groups.tsv into an ordered list of
    (group_name, [(source_pat, featuretype_pat), ...]) and a group_colors dict.

    Rows with status 'exclude' are collected under a special '_excluded_'
    group so the caller can filter those features from the flat list.
    Rows with any other status (or missing status column) are 'include'.
    Lines starting with '#' and the header row are ignored.
    The optional 5th column holds a hex color for each group.
    """
    if not tsv_path.exists():
        return [], {}

    raw_rows: list[tuple[str, str, str, str]] = []
    group_colors: dict[str, str] = {}
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
            color  = parts[4].strip() if len(parts) >= 5 else ""
            if group and source and feat:
                raw_rows.append((group, source, feat, status))
                if color and group not in group_colors:
                    group_colors[group] = color

    # Assemble into ordered (group, [patterns]) preserving row order.
    group_patterns: dict[str, list[tuple[str, str]]] = {}
    group_order: list[str] = []
    for group, source, feat, status in raw_rows:
        key = "_excluded_" if status == "exclude" else group
        if key not in group_patterns:
            group_patterns[key] = []
            group_order.append(key)
        group_patterns[key].append((source, feat))

    return [(g, group_patterns[g]) for g in group_order], group_colors


def validate_priority_groups(
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


def load_and_validate_priority_groups(
    tsv_path: Path,
) -> tuple[list[tuple[str, list[tuple[str, str]]]], dict[str, str]]:
    """Load priority_groups.tsv from the given path and validate it.
    Returns (groups, group_colors). If the file does not exist, returns ([], {})."""
    if not tsv_path.exists():
        print(f"[priority_groups] {tsv_path} not found — priority panel disabled.")
        return [], {}
    raw, group_colors = load_priority_groups_tsv(tsv_path)
    if not raw:
        if tsv_path.exists():
            print("[priority_groups] priority_groups.tsv is empty or has no valid rows.")
        else:
            print("[priority_groups] No priority_groups.tsv found — priority panel disabled.")
        return [], {}
    validated = validate_priority_groups(raw)
    include_groups = [(g, p) for g, p in validated if g != "_excluded_"]
    n_groups   = len(include_groups)
    n_patterns = sum(len(p) for _, p in include_groups)
    print(
        f"[priority_groups] Loaded {n_groups} group(s), "
        f"{n_patterns} pattern(s) from {tsv_path.name}."
    )
    return validated, group_colors   # keep _excluded_ entry for feature filtering


def extend_load_window(
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
