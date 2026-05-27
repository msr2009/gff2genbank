"""
s01_check_deps.py
-----------------
Check that all required and optional dependencies are available.

Required (hard failure if missing):
  - gffutils       Python package — database building
  - pyfaidx        Python package — FASTA indexing
  - samtools       CLI tool      — FASTA indexing (.fai)
  - bgzip          CLI tool      — GFF compression
  - tabix          CLI tool      — GFF indexing

Optional (warn and continue if missing):
  - cyvcf2         Python package — VCF parsing (only needed with --vcf in prepare_gff)
  - bedtools       CLI tool      — GFF sorting in prepare_gff (falls back to Python sort if missing)
  - agat           CLI tool      — GTF/GFF2 to GFF3 conversion in prepare_gff (only needed for non-GFF3 inputs)

Usage (standalone):
    python setup/s01_check_deps.py

Usage (via orchestrator):
    from setup.s01_check_deps import run
    run(session)
"""

import shutil
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


STEP_ID = "s01_check_deps"

REQUIRED_PYTHON = [
    ("gffutils", "pip install gffutils"),
    ("pyfaidx",  "pip install pyfaidx"),
]

OPTIONAL_PYTHON = [
    ("cyvcf2",   "pip install cyvcf2   # only needed if you have a VCF of variants"),
]

OPTIONAL_CLI = [
    ("bedtools",               "conda install -c bioconda bedtools  OR  brew install bedtools  # faster GFF sorting in prepare_gff"),
    ("agat_convert_sp_gxf2gxf.pl", "conda install -c bioconda agat  # only needed for GTF/GFF2 input in prepare_gff"),
]

REQUIRED_CLI = [
    ("samtools", "conda install -c bioconda samtools  OR  brew install samtools"),
    ("bgzip",    "conda install -c bioconda htslib    OR  brew install htslib"),
    ("tabix",    "conda install -c bioconda htslib    OR  brew install htslib"),
]


def _check_python(pkg: str) -> tuple[bool, str]:
    """Return (available, version_string)."""
    try:
        mod = __import__(pkg)
        ver = getattr(mod, "__version__", "unknown version")
        return True, ver
    except ImportError:
        return False, ""


def _check_cli(tool: str) -> tuple[bool, str]:
    """Return (available, version_string)."""
    if shutil.which(tool) is None:
        return False, ""
    try:
        result = subprocess.run(
            [tool, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        # samtools prints version to stdout; bgzip/tabix to stderr
        ver_line = (result.stdout or result.stderr or "").splitlines()
        ver = ver_line[0].strip() if ver_line else "unknown version"
        return True, ver
    except Exception:
        return True, "unknown version"


def run(session: dict) -> dict:
    """Orchestrator entry point."""
    from setup_log import write_step
    data_dir = Path(session["data_dir"])
    ok, warnings = _run_checks()
    status = "ok" if ok and not warnings else ("warning" if ok else "failed")
    write_step(
        data_dir, STEP_ID, status,
        message="All required dependencies found." if ok else "Missing required dependencies.",
        warnings=warnings,
    )
    session["deps_ok"] = ok
    return session


def _run_checks() -> tuple[bool, list[str]]:
    """
    Run all checks. Returns (all_required_ok, warnings_list).
    Prints results to stdout as it goes.
    """
    print("\n" + "=" * 60)
    print("STEP 1: CHECKING DEPENDENCIES")
    print("=" * 60)

    all_ok   = True
    warnings = []

    # ── Required Python packages ───────────────────────────────────────────
    print("\nRequired Python packages:")
    for pkg, install_hint in REQUIRED_PYTHON:
        ok, ver = _check_python(pkg)
        if ok:
            print(f"  ✓  {pkg:<12} {ver}")
        else:
            print(f"  ✗  {pkg:<12} NOT FOUND")
            print(f"       Install: {install_hint}")
            all_ok = False

    # ── Optional Python packages ───────────────────────────────────────────
    print("\nOptional Python packages:")
    for pkg, install_hint in OPTIONAL_PYTHON:
        ok, ver = _check_python(pkg)
        if ok:
            print(f"  ✓  {pkg:<12} {ver}")
        else:
            msg = f"{pkg} not found — VCF variant import will be unavailable"
            print(f"  ⚠  {pkg:<12} not found")
            print(f"       {install_hint}")
            warnings.append(msg)

    # ── Required CLI tools ─────────────────────────────────────────────────
    print("\nRequired CLI tools:")
    for tool, install_hint in REQUIRED_CLI:
        ok, ver = _check_cli(tool)
        if ok:
            print(f"  ✓  {tool:<12} {ver}")
        else:
            print(f"  ✗  {tool:<12} NOT FOUND")
            print(f"       Install: {install_hint}")
            all_ok = False

    # ── Optional CLI tools ─────────────────────────────────────────────────
    print("\nOptional CLI tools:")
    for tool, install_hint in OPTIONAL_CLI:
        ok, ver = _check_cli(tool)
        if ok:
            print(f"  ✓  {tool:<12} {ver}")
        else:
            msg = f"{tool} not found — prepare_gff.py will use the Python sort fallback"
            print(f"  ⚠  {tool:<12} not found")
            print(f"       {install_hint}")
            warnings.append(msg)

    print()
    if all_ok and not warnings:
        print("All dependencies satisfied.")
    elif all_ok:
        print(f"All required dependencies found. {len(warnings)} warning(s) — see above.")
    else:
        print("One or more required dependencies are missing. Install them and re-run.")

    return all_ok, warnings


def main() -> None:
    ok, _ = _run_checks()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
