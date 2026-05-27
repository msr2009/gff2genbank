"""
s02_prepare_gff.py
------------------
Interactive wrapper around prepare_gff.py.

Guides the user through:
  1. Specifying input GFF file(s)
  2. Specifying the genome FASTA
  3. Optionally specifying a VCF of variants
  4. Optionally specifying a chromosome name map
  5. Optionally filtering by GFF source
  6. Specifying the output path
  7. Running prepare_gff.py with the chosen arguments
  8. If a chrom_map_template.tsv is written (name mismatch detected),
     informing the user and pausing so they can edit it before re-running.

Usage (standalone):
    python setup/s02_prepare_gff.py

Usage (via orchestrator):
    from setup.s02_prepare_gff import run
    run(session)
"""

import subprocess
import sys
from pathlib import Path as _Path
_SETUP_DIR = _Path(__file__).parent
_PROJECT_ROOT = _SETUP_DIR.parent
if str(_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(_SETUP_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from pathlib import Path


STEP_ID = "s02_prepare_gff"


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _prompt_path(msg: str, must_exist: bool = True, default: str = "") -> Path | None:
    while True:
        raw = _prompt(msg, default)
        if not raw:
            return None
        p = Path(raw).expanduser()
        if must_exist and not p.exists():
            print(f"  File not found: {p}")
            continue
        return p


def _prompt_yes(msg: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = _prompt(msg + suffix).lower()
    if not raw:
        return default
    return raw.startswith("y")


# ---------------------------------------------------------------------------
# Source scanner
# ---------------------------------------------------------------------------

def _scan_sources(gff_paths: list[Path]) -> list[str]:
    """Return sorted unique source column values from one or more GFF files."""
    import gzip
    sources: set[str] = set()
    for gff in gff_paths:
        opener = gzip.open if str(gff).endswith(".gz") else open
        try:
            with opener(gff, "rt") as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        sources.add(parts[1].strip())
        except Exception as e:
            print(f"  WARNING: could not scan {gff.name}: {e}")
    return sorted(sources)


# ---------------------------------------------------------------------------
# Main interactive flow
# ---------------------------------------------------------------------------

def _run_interactive(session: dict) -> tuple[bool, list[str], dict]:
    """
    Returns (success, warnings, updated_session).
    """
    print("\n" + "=" * 60)
    print("STEP 2: PREPARE GFF FILE(S)")
    print("=" * 60)
    print("This step merges, filters, and normalises your GFF input(s)")
    print("into a single prepared file for database building.")

    warnings: list[str] = []

    # ── Input GFF(s) ──────────────────────────────────────────────────────
    print("\nInput GFF file(s) — enter one path per line, blank line when done:")
    gff_paths: list[Path] = []
    while True:
        p = _prompt_path(f"  GFF {len(gff_paths)+1} (or blank to finish)",
                         must_exist=True)
        if p is None:
            if not gff_paths:
                print("  At least one GFF file is required.")
                continue
            break
        gff_paths.append(p)

    # ── FASTA ─────────────────────────────────────────────────────────────
    fasta = _prompt_path("\nGenome FASTA file", must_exist=True)
    if fasta is None:
        print("FASTA is required.")
        return False, [], session

    # ── VCF (optional) ────────────────────────────────────────────────────
    vcf = None
    if _prompt_yes("\nDo you have a bgzipped VCF of variants to include?", default=False):
        vcf = _prompt_path("  VCF path (.vcf.gz)", must_exist=True)
        if vcf is None:
            warnings.append("VCF skipped — file not found.")

    # ── Source filtering (optional) ───────────────────────────────────────
    keep_sources: list[str] | None = None
    if _prompt_yes("\nScan GFF sources and filter to a subset?", default=False):
        print("  Scanning GFF source columns (may take a moment)...")
        all_sources = _scan_sources(gff_paths)
        if all_sources:
            print(f"  Found {len(all_sources)} unique source(s):")
            for i, s in enumerate(all_sources, 1):
                print(f"    {i:>3}. {s}")
            raw = _prompt(
                "  Enter source numbers to KEEP (e.g. 1 3 5), or blank to keep all"
            )
            if raw.strip():
                try:
                    indices = [int(x) - 1 for x in raw.split()]
                    keep_sources = [all_sources[i] for i in indices
                                    if 0 <= i < len(all_sources)]
                    print(f"  Keeping: {', '.join(keep_sources)}")
                except (ValueError, IndexError):
                    print("  Invalid selection — keeping all sources.")
                    warnings.append("Source filter selection was invalid — all sources kept.")
        else:
            print("  Could not read sources — keeping all.")

    # ── Chromosome map (optional) ─────────────────────────────────────────
    chrom_map: Path | None = None
    if _prompt_yes("\nDo you have a chromosome name map TSV?", default=False):
        chrom_map = _prompt_path("  Chrom map path", must_exist=True)

    # ── Output path ───────────────────────────────────────────────────────
    default_out = str(Path(session.get("data_dir", ".")) / "prepared.gff3.gz")
    out_raw = _prompt("\nOutput prepared GFF path", default=default_out)
    out_path = Path(out_raw).expanduser()

    # ── Build command ─────────────────────────────────────────────────────
    prepare_script = Path(__file__).parent.parent / "prepare_gff.py"
    cmd = [sys.executable, str(prepare_script)]
    for g in gff_paths:
        cmd += ["--gff", str(g)]
    cmd += ["--fasta", str(fasta)]
    if vcf:
        cmd += ["--vcf", str(vcf)]
    if chrom_map:
        cmd += ["--chrom-map", str(chrom_map)]
    if keep_sources:
        cmd += ["--keep-sources", ",".join(keep_sources)]
    cmd += ["-o", str(out_path)]

    print("\nCommand to run:")
    print("  " + " ".join(cmd))
    if not _prompt_yes("\nProceed?", default=True):
        return False, ["User cancelled."], session

    # ── Run ───────────────────────────────────────────────────────────────
    print()
    result = subprocess.run(cmd)

    if result.returncode != 0:
        # Check if a chrom_map_template.tsv was written (name mismatch)
        template = Path("chrom_map_template.tsv")
        if template.exists():
            print(f"\n  Chromosome name mismatch detected.")
            print(f"  A template has been written to: {template.resolve()}")
            print(f"  Edit the 'canonical' column to match your FASTA sequence names,")
            print(f"  then re-run this step with that file as the chromosome map.")
            warnings.append(
                f"Chromosome map template written to {template.resolve()}. "
                "Edit and re-run step 2."
            )
        return False, warnings, session

    session["prepared_gff"] = str(out_path)
    session["fasta"]        = str(fasta)
    print(f"\n  Prepared GFF written to: {out_path}")
    return True, warnings, session


def run(session: dict) -> dict:
    """Orchestrator entry point."""
    from setup_log import write_step
    data_dir = Path(session["data_dir"])
    ok, warnings, session = _run_interactive(session)
    status = "ok" if ok and not warnings else ("warning" if ok else "failed")
    write_step(
        data_dir, STEP_ID, status,
        message=f"Prepared GFF: {session.get('prepared_gff', 'n/a')}",
        warnings=warnings,
        output=session.get("prepared_gff"),
    )
    return session


def main() -> None:
    session = {"data_dir": "."}
    run(session)


if __name__ == "__main__":
    main()
