"""
test_priority_groups.py
-----------------------
Offline unit tests for the priority_groups TSV loader and validator.
Exercises _load_priority_groups_tsv and _validate_priority_groups directly
without importing server.py (which requires a full Shiny environment).

Run from the project root:
    python test_data/test_priority_groups.py

Expected output:
    [TEST 1] Normal load (cel config) ............. all PASS
    [TEST 2] Conflict deduplication ............... all PASS
    [TEST 3] Missing file ......................... all PASS
    [TEST 4] Exclude rows separated correctly ..... all PASS
"""

from pathlib import Path

TEST_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Paste the two functions under test directly — keeps this script self-contained
# and avoids importing server.py (which needs a full Shiny+config environment).
# If you change these functions in server.py, update them here too.
# ---------------------------------------------------------------------------

def _load_priority_groups_tsv(tsv_path):
    if not tsv_path.exists():
        return []
    raw_rows = []
    with open(tsv_path) as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("group_name"):
                continue
            parts = stripped.split("\t")
            if len(parts) < 3:
                continue
            group  = parts[0].strip()
            source = parts[1].strip()
            feat   = parts[2].strip()
            status = parts[3].strip() if len(parts) >= 4 else "include"
            if group and source and feat:
                raw_rows.append((group, source, feat, status))
    group_patterns = {}
    group_order = []
    for group, source, feat, status in raw_rows:
        key = "_excluded_" if status == "exclude" else group
        if key not in group_patterns:
            group_patterns[key] = []
            group_order.append(key)
        group_patterns[key].append((source, feat))
    return [(g, group_patterns[g]) for g in group_order]


def _validate_priority_groups(groups):
    seen = {}
    cleaned = []
    for group, patterns in groups:
        clean_patterns = []
        for pat in patterns:
            if pat in seen:
                print(
                    f"  [WARNING] ({pat[0]!r}, {pat[1]!r}) in both "
                    f"{seen[pat]!r} and {group!r} — keeping {seen[pat]!r}"
                )
            else:
                seen[pat] = group
                clean_patterns.append(pat)
        if not clean_patterns:
            print(f"  [WARNING] group {group!r} empty after dedup — skipped")
            continue
        cleaned.append((group, clean_patterns))
    return cleaned


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

failures = []

def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    msg = f"  [{status}] {label}" + (f"  ({detail})" if detail else "")
    print(msg)
    if not condition:
        failures.append(label)
    return condition


# ---------------------------------------------------------------------------
# TEST 1: Normal load — cel config
# ---------------------------------------------------------------------------
print("\n[TEST 1] Normal load — priority_groups_cel.tsv")
raw = _load_priority_groups_tsv(TEST_DIR / "priority_groups_cel.tsv")
group_names = [g for g, _ in raw if g != "_excluded_"]

check("Classical allele present",  "Classical allele" in group_names)
check("MMP allele present",        "MMP allele"       in group_names)
check("HTP allele present",        "HTP allele"       in group_names)
check("Polymorphism present",      "Polymorphism"     in group_names)
check("RNAi reagent present",      "RNAi reagent"     in group_names)
check("_excluded_ group present",  "_excluded_" in [g for g, _ in raw])

htp_patterns = next((p for g, p in raw if g == "HTP allele"), [])
check("HTP allele has 2 patterns", len(htp_patterns) == 2, str(htp_patterns))

validated = _validate_priority_groups(raw)
validated_names = [g for g, _ in validated if g != "_excluded_"]
check("Validation preserves all groups",
      set(validated_names) == set(group_names), str(validated_names))


# ---------------------------------------------------------------------------
# TEST 2: Conflict deduplication — conflict test TSV
# ---------------------------------------------------------------------------
print("\n[TEST 2] Conflict deduplication — priority_groups_conflict_test.tsv")
raw2       = _load_priority_groups_tsv(TEST_DIR / "priority_groups_conflict_test.tsv")
validated2 = _validate_priority_groups(raw2)
names2     = [g for g, _ in validated2]

check("Classical allele survives",   "Classical allele" in names2)
check("MMP allele survives",         "MMP allele"       in names2)
check("All alleles survives",        "All alleles"      in names2)
check("Empty group dropped",         "Empty group"      not in names2, str(names2))

all_patterns = next((p for g, p in validated2 if g == "All alleles"), [])
check("All alleles kept only NBP_knockout",
      len(all_patterns) == 1 and all_patterns[0] == ("NBP_knockout", "*"),
      str(all_patterns))


# ---------------------------------------------------------------------------
# TEST 3: Missing file
# ---------------------------------------------------------------------------
print("\n[TEST 3] Missing file")
raw3 = _load_priority_groups_tsv(TEST_DIR / "nonexistent.tsv")
check("Returns empty list", raw3 == [], str(raw3))


# ---------------------------------------------------------------------------
# TEST 4: Exclude rows separated correctly
# ---------------------------------------------------------------------------
print("\n[TEST 4] Exclude rows — cel config")
raw4       = _load_priority_groups_tsv(TEST_DIR / "priority_groups_cel.tsv")
validated4 = _validate_priority_groups(raw4)

include4 = [(g, p) for g, p in validated4 if g != "_excluded_"]
exclude4 = [(g, p) for g, p in validated4 if g == "_excluded_"]

check("Include groups present",      len(include4) > 0, str(len(include4)))
check("Exactly one _excluded_ group", len(exclude4) == 1, str(len(exclude4)))
excl_patterns = exclude4[0][1] if exclude4 else []
check("Exclude has 2 patterns",      len(excl_patterns) == 2, str(excl_patterns))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
if failures:
    print(f"FAILED: {len(failures)} test(s): {failures}")
    raise SystemExit(1)
else:
    print("All tests passed.")
