"""
prepare_gff.py
--------------
Prepare one or more GFF3 files for use with the gff2genbank app.

This script:
  1. Validates each input file (not binary/archive, correct format).
  2. Converts any GTF or GFF2 inputs to GFF3 using AGAT.
  3. Collects all chromosome/contig names from every input GFF and VCF.
  4. If a --chrom-map file is provided, renames chromosomes consistently
     across all sources.  If not, writes a template mapping file and exits
     with an error — this ensures the user explicitly confirms name mappings
     rather than having them guessed automatically.
  5. Merges all input GFF files into one output, with optional source filtering.
  6. Sorts the output by chromosome then start position.
  7. Optionally appends alleles from a VCF as sequence_alteration features.

Usage
-----
First run (generates template if chromosome names differ across sources):

    python prepare_gff.py \\
        --gff annotations.gff3.gz \\
        --fasta genome.fa \\
        --vcf variations.vcf.gz \\
        -o prepared.gff3.gz

If a mismatch is detected, a file called chrom_map_template.tsv is written.
Edit it so the 'canonical' column contains the names you want in the output
(typically matching the FASTA), then re-run with --chrom-map:

    python prepare_gff.py \\
        --gff annotations.gff3.gz \\
        --fasta genome.fa \\
        --vcf variations.vcf.gz \\
        --chrom-map chrom_map_template.tsv \\
        --keep-sources MySource,AnotherSource \\
        -o prepared.gff3.gz

Format support
--------------
GFF3 input is processed directly.  GTF / GFF2 input is automatically
converted to GFF3 using AGAT (agat_convert_sp_gxf2gxf.pl) before
processing.  AGAT must be installed if non-GFF3 input is provided:

    conda install -c bioconda agat

Dependencies
------------
    conda install -c bioconda samtools bedtools  # required / optional CLI tools
    conda install -c bioconda cyvcf2             # optional: only needed with --vcf
    conda install -c bioconda agat               # optional: only needed for GTF/GFF2 input

Debug mode
----------
Run with --debug to:
  - Keep AGAT-converted temp files in ./prepare_gff_debug/
  - Print full stdout/stderr from all subprocess calls
"""

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


WBVAR_RE = re.compile(r"^WBVar\d+$")
GK_RE    = re.compile(r"^gk", re.IGNORECASE)

# Magic bytes for common archive/binary formats
_MAGIC = {
    b"\x1f\x8b":             "gzip archive",
    b"BZh":                   "bzip2 archive",
    b"PK\x03\x04":           "zip archive",
    b"\xfd7zXZ\x00":         "xz archive",
    b"\x04\x22\x4d\x18":   "LZ4 archive",
    b"\x28\xb5\x2f\xfd":   "zstd archive",
}
_TAR_OFFSET = 257
_TAR_MAGIC  = b"ustar"


# ---------------------------------------------------------------------------
# Input file validation
# ---------------------------------------------------------------------------

def _read_raw_bytes(path: Path, n: int = 512) -> bytes:
    """Read the first n bytes of a file regardless of extension."""
    with open(path, "rb") as fh:
        return fh.read(n)


def _check_bytes_for_archive(path: Path, raw: bytes, is_inner: bool = False) -> None:
    """
    Raise RuntimeError if raw bytes look like a binary archive.
    is_inner=True tweaks the error message for nested-inside-gz cases.
    """
    loc = "inside the gzip" if is_inner else "file"

    if len(raw) > _TAR_OFFSET + 5 and raw[_TAR_OFFSET:_TAR_OFFSET + 5] == _TAR_MAGIC:
        if is_inner:
            raise RuntimeError(
                f"\'{path.name}\' is a gzip-compressed tar archive (.tar.gz), not a GFF.\n"
                f"  Extract the archive and pass the GFF file inside it."
            )
        raise RuntimeError(
            f"\'{path.name}\' appears to be a tar archive, not a GFF file.\n"
            f"  Extract the archive first and pass the GFF file inside it."
        )

    for magic, fmt in _MAGIC.items():
        if raw.startswith(magic):
            if fmt == "gzip archive" and not is_inner and str(path).endswith(".gz"):
                return   # legitimate outer gzip wrapper — ok
            raise RuntimeError(
                f"\'{path.name}\' ({loc}) appears to be a {fmt}, not a GFF file.\n"
                + (
                    f"  If this is a compressed GFF, ensure it is gzip-compressed\n"
                    f"  and rename it with a .gz extension."
                    if not is_inner else
                    f"  This is not a valid GFF file."
                )
            )

    # Generic binary: >10% non-printable bytes
    if not is_inner:
        non_text = sum(1 for b in raw if b < 9 or (13 < b < 32) or b == 127)
        if non_text / max(len(raw), 1) > 0.10:
            raise RuntimeError(
                f"\'{path.name}\' appears to contain binary data and is not a valid GFF.\n"
                f"  ({non_text}/{len(raw)} bytes are non-printable)"
            )


def check_not_binary_or_archive(path: Path) -> None:
    """Full binary/archive check for an input file, including inside .gz."""
    raw = _read_raw_bytes(path)
    if not raw:
        raise RuntimeError(f"File is empty: {path}")

    _check_bytes_for_archive(path, raw)

    # For .gz files, also peek inside
    if str(path).endswith(".gz"):
        try:
            with gzip.open(path, "rb") as fh:
                inner = fh.read(512)
            _check_bytes_for_archive(path, inner, is_inner=True)
        except (OSError, EOFError) as e:
            raise RuntimeError(
                f"Could not decompress \'{path.name}\': {e}\n"
                f"  Ensure the file is a valid gzip-compressed GFF3."
            )


def _sniff_format(path: Path) -> str:
    """
    Detect the GFF format by inspecting the header and attribute style.
    Returns one of: 'gff3', 'gtf', 'gff2', 'unknown'.

    Priority:
      1. ##gff-version header line
      2. Attribute style in first 50 data lines:
           gene_id "x"; transcript_id "y"  ->  gtf
           ID=x;Parent=y                   ->  gff3
      3. Neither found                     ->  unknown
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    gff3_score = 0
    gtf_score  = 0
    lines_read = 0

    with opener(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.startswith("##gff-version"):
                ver = line.split()[-1].strip()
                return "gff3" if ver.startswith("3") else "gff2"
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            col9 = parts[8]
            if re.search(r'gene_id\s+"[^"]*"', col9):
                gtf_score += 1
            if re.search(r'\b(ID|Parent)=', col9):
                gff3_score += 1
            lines_read += 1
            if lines_read >= 50:
                break

    if gff3_score > gtf_score:
        return "gff3"
    if gtf_score > gff3_score:
        return "gtf"
    return "unknown"


def validate_gff_input(path: Path) -> str:
    """
    Validate a single GFF input file.  Returns the detected format string.
    Raises RuntimeError with a clear message on any problem.
    """
    check_not_binary_or_archive(path)
    fmt = _sniff_format(path)

    # Cross-check extension vs content (warn only)
    name_no_gz = path.name
    if name_no_gz.endswith(".gz"):
        name_no_gz = name_no_gz[:-3]
    ext = Path(name_no_gz).suffix.lower()

    if fmt == "gff3" and ext == ".gtf":
        print(f"  Warning: \'{path.name}\' has a .gtf extension but looks like GFF3.")
    elif fmt == "gtf" and ext in (".gff3", ".gff"):
        print(f"  Warning: \'{path.name}\' has a {ext} extension but looks like GTF.")

    if fmt in ("gtf", "gff2"):
        if shutil.which("agat_convert_sp_gxf2gxf.pl") is None:
            raise RuntimeError(
                f"\'{path.name}\' appears to be in {fmt.upper()} format, not GFF3.\n"
                f"  Automatic conversion requires AGAT, which is not installed.\n"
                f"  Install it with:  conda install -c bioconda agat\n"
                f"  Then re-run prepare_gff.py."
            )

    if fmt == "unknown":
        raise RuntimeError(
            f"Could not determine the format of \'{path.name}\'.\n"
            f"  Expected GFF3 (ID=/Parent= attributes) or GTF (gene_id/transcript_id).\n"
            f"  Check that the file is a valid annotation file and not corrupted."
        )

    return fmt


# ---------------------------------------------------------------------------
# GTF / GFF2 -> GFF3 conversion via AGAT
# ---------------------------------------------------------------------------

def convert_to_gff3(
    path: Path,
    debug: bool = False,
    debug_dir: Path | None = None,
) -> Path:
    """
    Convert a GTF or GFF2 file to GFF3 using agat_convert_sp_gxf2gxf.pl.
    Returns the path to the converted GFF3 file.
    In debug mode the file is kept in debug_dir; otherwise it is a temp file
    the caller should delete after use.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if debug and debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        out_path = debug_dir / f"{path.name}.agat_converted_{ts}.gff3"
        log_path = debug_dir / f"{path.name}.agat_log_{ts}.txt"
    else:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".agat_converted.gff3", delete=False
        )
        out_path = Path(tmp.name)
        tmp.close()
        log_path = None

    print(f"  Converting \'{path.name}\' to GFF3 with AGAT...")
    cmd = [
        "agat_convert_sp_gxf2gxf.pl",
        "--gff", str(path),
        "--output", str(out_path),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=not debug, text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "agat_convert_sp_gxf2gxf.pl not found.\n"
            "  Install AGAT with:  conda install -c bioconda agat"
        )

    if debug and log_path:
        with open(log_path, "w") as fh:
            fh.write(result.stdout or "")
            fh.write(result.stderr or "")
        print(f"  AGAT log written to: {log_path}")

    if result.returncode != 0:
        msg = (result.stderr or "").strip().splitlines()
        snippet = "\n  ".join(msg[-5:]) if msg else ""
        raise RuntimeError(
            f"AGAT conversion failed for \'{path.name}\' (exit {result.returncode}).\n"
            + (f"  {snippet}" if snippet else
               "  Run with --debug to see full AGAT output.")
        )

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(
            f"AGAT produced an empty output for \'{path.name}\'.\n"
            f"  Run with --debug to see full AGAT output."
        )

    print(f"  Converted successfully -> {out_path.name}")
    return out_path


# ---------------------------------------------------------------------------
# Chromosome name collection
# ---------------------------------------------------------------------------

def fasta_chrom_names(fasta_path: Path) -> list[str]:
    """Return chromosome names from a samtools .fai index, creating it if needed."""
    fai_path = Path(str(fasta_path) + ".fai")
    if not fai_path.exists():
        print(f"  Indexing FASTA: {fasta_path}", flush=True)
        subprocess.run(["samtools", "faidx", str(fasta_path)], check=True)
    names = []
    with open(fai_path) as fh:
        for line in fh:
            parts = line.strip().split("\t")
            if parts:
                names.append(parts[0])
    return names


def gff_chrom_names(gff_path: Path) -> list[str]:
    """Return unique chromosome names from a GFF3 file (column 1)."""
    seen: list[str] = []
    opener = gzip.open if str(gff_path).endswith(".gz") else open
    with opener(gff_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split("\t", 2)
            if parts:
                chrom = parts[0].strip()
                if chrom and chrom not in seen:
                    seen.append(chrom)
    return seen


def vcf_chrom_names(vcf_path: Path) -> list[str]:
    """Return unique chromosome names from a VCF file (CHROM column)."""
    seen: list[str] = []
    opener = gzip.open if str(vcf_path).endswith(".gz") else open
    with opener(vcf_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split("\t", 2)
            if parts:
                chrom = parts[0].strip()
                if chrom and chrom not in seen:
                    seen.append(chrom)
    return seen


# ---------------------------------------------------------------------------
# Chromosome map: load or generate template
# ---------------------------------------------------------------------------

def load_chrom_map(map_path: Path) -> dict[str, str]:
    """Load a tab-delimited {source_name: canonical_name} mapping file."""
    mapping: dict[str, str] = {}
    with open(map_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            src, canon = parts[0].strip(), parts[1].strip()
            if src == "source_name":
                continue
            mapping[src] = canon
    return mapping


def write_chrom_map_template(
    all_names: list[str],
    fasta_names: list[str],
    out_path: Path,
) -> None:
    """Write a template mapping file pre-filled where names already match FASTA."""
    fasta_set = set(fasta_names)
    with open(out_path, "w") as fh:
        fh.write("# Edit the 'canonical_name' column so every name maps to\n")
        fh.write("# the chromosome name used in your FASTA file.\n")
        fh.write("# Lines starting with # are ignored.\n")
        fh.write("source_name\tcanonical_name\n")
        for name in sorted(set(all_names)):
            canon = name if name in fasta_set else ""
            fh.write(f"{name}\t{canon}\n")


def check_or_build_chrom_map(
    gff_paths: list[Path],
    fasta_path: Path,
    vcf_path: Path | None,
    map_path: Path | None,
    template_out: Path,
) -> dict[str, str] | None:
    """
    Validate chromosome name consistency.  Returns a mapping dict, or None
    if a template was written and the user must edit and re-run.
    """
    print("Collecting chromosome names from all sources...")
    fasta_names = fasta_chrom_names(fasta_path)
    fasta_set   = set(fasta_names)
    print(f"  FASTA: {sorted(fasta_set)}")

    all_source_names: list[str] = []
    for gff_path in gff_paths:
        names = gff_chrom_names(gff_path)
        print(f"  GFF {gff_path.name}: {names[:8]}{"..." if len(names) > 8 else ""}")
        all_source_names.extend(names)
    if vcf_path:
        names = vcf_chrom_names(vcf_path)
        print(f"  VCF {vcf_path.name}: {names[:8]}{"..." if len(names) > 8 else ""}")
        all_source_names.extend(names)

    if map_path:
        mapping = load_chrom_map(map_path)
        print(f"\nLoaded chromosome map from {map_path} ({len(mapping)} entries).")
        return mapping

    unique_source = set(all_source_names)
    mismatches = unique_source - fasta_set
    if not mismatches:
        print("\nAll chromosome names already match the FASTA. No mapping needed.")
        return {n: n for n in unique_source}

    print(f"\nChromosome name mismatch detected!")
    print(f"  Names in sources but not in FASTA: {sorted(mismatches)}")
    write_chrom_map_template(list(unique_source), fasta_names, template_out)
    print(f"\nA template mapping file has been written to: {template_out}")
    print("Edit the 'canonical_name' column so every name matches your FASTA,")
    print("then re-run with:  --chrom-map " + str(template_out))
    return None


# ---------------------------------------------------------------------------
# VCF variant extraction
# ---------------------------------------------------------------------------

def vcf_variants_as_gff_lines(
    vcf_path: Path,
    chrom_map: dict[str, str],
) -> list[str]:
    """
    Extract canonical alleles from a bgzipped VCF and return them as GFF3 lines.
    Skips WBVar-only records and gk alleles.
    """
    try:
        import cyvcf2
    except ImportError:
        print("Warning: cyvcf2 not installed; skipping VCF allele extraction.")
        return []

    lines: list[str] = []
    vcf = cyvcf2.VCF(str(vcf_path))
    for v in vcf:
        pn = str(v.INFO.get("PN", "") or "").strip()
        if not pn or WBVAR_RE.match(pn) or GK_RE.match(pn):
            continue
        chrom = chrom_map.get(v.CHROM, v.CHROM)
        start = v.POS
        end   = v.POS + max(len(v.REF) - 1, 0)
        sub   = f"{v.REF}/{chr(44).join(str(a) for a in v.ALT)}"
        attrs = f"variation=VCF_{pn};public_name={pn};substitution={sub}"
        lines.append(
            f"{chrom}\tAllele\tsequence_alteration\t"
            f"{start}\t{end}\t.\t+\t.\t{attrs}\n"
        )
    print(f"  Extracted {len(lines):,} canonical VCF alleles.")
    return lines


# ---------------------------------------------------------------------------
# GFF processing
# ---------------------------------------------------------------------------

def process_gff(
    gff_paths: list[Path],
    out_path: Path,
    chrom_map: dict[str, str],
    keep_sources: set[str] | None,
    extra_lines: list[str],
    keep_pairs: set[tuple[str, str]] | None = None,
    progress=None,
) -> int:
    """
    Merge all input GFF files into one output, applying chromosome renaming
    and optional filtering.  Streams line-by-line (bounded memory).  Returns
    total feature lines written.

    Filtering (both optional, AND-combined when both are given):
      keep_sources  — keep only lines whose source column is in this set.
      keep_pairs    — keep only lines whose (source, featuretype) pair is in
                      this set.  Used by the setup GUI for fine-grained
                      size-trimming; the CLI passes only keep_sources.
    progress: optional callable(str) called every ~1M input lines so a GUI can
              show live progress on enormous files.
    """
    out_opener = gzip.open if str(out_path).endswith(".gz") else open
    written = 0
    seen = 0
    header_done = False

    with out_opener(out_path, "wt") as fh_out:
        for gff_path in gff_paths:
            opener = gzip.open if str(gff_path).endswith(".gz") else open
            with opener(gff_path, "rt") as fh_in:
                for line in fh_in:
                    if line.startswith("#"):
                        if not header_done:
                            fh_out.write(line)
                        continue
                    header_done = True
                    seen += 1
                    if progress and seen % 250_000 == 0:
                        progress(f"  read {seen:,} lines, kept {written:,}…")
                    parts = line.split("\t")
                    if len(parts) < 9:
                        continue
                    if keep_sources and parts[1] not in keep_sources:
                        continue
                    if keep_pairs is not None and (parts[1], parts[2]) not in keep_pairs:
                        continue
                    parts[0] = chrom_map.get(parts[0], parts[0])
                    fh_out.write("\t".join(parts))
                    written += 1
        for line in extra_lines:
            fh_out.write(line)
            written += 1

    return written


# ---------------------------------------------------------------------------
# GFF sorting
# ---------------------------------------------------------------------------

def sort_gff_inplace(gff_path: Path, debug: bool = False, log=None) -> None:
    """
    Sort a GFF3 file by chromosome then start position, in-place.

    Memory-bounded for very large GFFs: the file is never held in memory.
    Comment/header lines (few) are kept in memory; data lines are streamed to a
    temp file and sorted by an *external* sorter that spills to disk —
    GNU/BSD ``sort`` (always present) preferred, then ``bedtools``, then a
    pure-Python in-memory fallback only if neither CLI exists. The sorted data
    is then streamed back into the output (gzip-aware), so peak RSS stays at a
    few tens of MB regardless of input size.

    log: optional callable(str) for progress (defaults to print).
    """
    emit = log if log is not None else (lambda m: print(m, flush=True))
    compressed = str(gff_path).endswith(".gz")
    in_opener  = gzip.open if compressed else open

    data_tmp   = Path(str(gff_path) + ".data_tmp")
    sorted_tmp = Path(str(gff_path) + ".sorted_tmp")
    out_tmp    = Path(str(gff_path) + ".out_tmp")

    # ── Pass 1: split header (small, in memory) from data (streamed to disk) ─
    header_lines: list[str] = []
    n = 0
    with in_opener(gff_path, "rt") as fh, open(data_tmp, "w") as dt:
        for line in fh:
            if line.startswith("#"):
                header_lines.append(line)
            else:
                dt.write(line)
                n += 1
                if n % 250_000 == 0:
                    emit(f"  prepared {n:,} lines for sorting…")

    # ── Sort the data temp with the lowest-memory tool available ────────────
    used = None
    if shutil.which("sort"):
        try:
            emit("  sorting (external sort, low memory)…")
            subprocess.run(
                ["sort", "-t", "\t", "-k1,1", "-k4,4n",
                 "-o", str(sorted_tmp), str(data_tmp)],
                check=True, env={**os.environ, "LC_ALL": "C"},
            )
            used = "sort"
        except Exception as e:
            emit(f"  external sort failed ({e}); trying next option…")
    if used is None and shutil.which("bedtools"):
        try:
            emit("  sorting (bedtools)…")
            with open(sorted_tmp, "w") as out:
                subprocess.run(["bedtools", "sort", "-i", str(data_tmp)],
                               stdout=out, check=True)
            used = "bedtools"
        except Exception as e:
            emit(f"  bedtools failed ({e}); falling back to in-memory sort…")
    if used is None:
        emit("  sorting (in-memory Python fallback)…")
        def _key(line: str):
            parts = line.split("\t")
            try:
                return (parts[0], int(parts[3]))
            except (IndexError, ValueError):
                return (parts[0] if parts else "", 0)
        with open(data_tmp) as dt:
            lines = dt.readlines()
        lines.sort(key=_key)
        with open(sorted_tmp, "w") as st:
            st.writelines(lines)
        used = "python"

    # ── Pass 2: stream header + sorted data into the output (gzip-aware) ─────
    out_opener = gzip.open if compressed else open
    with out_opener(out_tmp, "wt") as fh_out, open(sorted_tmp) as st:
        fh_out.writelines(header_lines)
        for line in st:
            fh_out.write(line)
    out_tmp.replace(gff_path)

    for t in (data_tmp, sorted_tmp):
        t.unlink(missing_ok=True)
    emit(f"  Sorted with {used}.")


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------

def _cleanup_temps(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            print(f"  Warning: could not remove temp file {p}: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare GFF3/GTF file(s) for the gff2genbank app.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--gff", dest="gff_files", action="append", required=True, metavar="GFF",
        help="Input GFF/GTF file (plain or .gz). May be specified multiple times. "
             "GTF and GFF2 are automatically converted to GFF3 using AGAT.",
    )
    parser.add_argument(
        "--fasta", required=True,
        help="Genome FASTA file. A .fai index will be created with samtools if absent.",
    )
    parser.add_argument(
        "--vcf", default=None,
        help="Optional bgzipped VCF. Variants with a public name (PN INFO field) "
             "are appended to the output as sequence_alteration features.",
    )
    parser.add_argument(
        "--chrom-map", default=None, metavar="TSV",
        help="Tab-delimited file mapping source chromosome names to canonical names.",
    )
    parser.add_argument(
        "--keep-sources", default=None,
        help="Comma-separated GFF source column values to retain.",
    )
    parser.add_argument(
        "-o", "--out", required=True,
        help="Output GFF3 path. Use a .gz suffix for compressed output.",
    )
    parser.add_argument(
        "--chrom-map-template", default="chrom_map_template.tsv",
        help="Path for the generated template mapping file (default: chrom_map_template.tsv).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Keep AGAT-converted temp files in ./prepare_gff_debug/ and print "
             "full subprocess output.",
    )
    args = parser.parse_args()

    gff_paths    = [Path(g) for g in args.gff_files]
    fasta_path   = Path(args.fasta)
    vcf_path     = Path(args.vcf)        if args.vcf       else None
    map_path     = Path(args.chrom_map)  if args.chrom_map else None
    out_path     = Path(args.out)
    template_out = Path(args.chrom_map_template)
    debug        = args.debug
    debug_dir    = Path("prepare_gff_debug") if debug else None

    if debug:
        print(f"[debug] Debug mode enabled. Temp files -> {debug_dir}/")

    keep_sources: set[str] | None = None
    if args.keep_sources:
        keep_sources = {s.strip() for s in args.keep_sources.split(",")}
        print(f"Keeping GFF sources: {sorted(keep_sources)}")

    # ── Validate paths exist ───────────────────────────────────────────────
    for p in gff_paths:
        if not p.exists():
            print(f"Error: GFF not found: {p}", file=sys.stderr); sys.exit(1)
    if not fasta_path.exists():
        print(f"Error: FASTA not found: {fasta_path}", file=sys.stderr); sys.exit(1)
    if vcf_path and not vcf_path.exists():
        print(f"Error: VCF not found: {vcf_path}", file=sys.stderr); sys.exit(1)

    # ── Step 0: validate format and convert non-GFF3 inputs ───────────────
    print("\nValidating input file(s)...")
    converted_temps: list[Path] = []
    final_gff_paths: list[Path] = []

    for gff_path in gff_paths:
        try:
            fmt = validate_gff_input(gff_path)
        except RuntimeError as e:
            print(f"\nError: {e}", file=sys.stderr); sys.exit(1)

        if fmt == "gff3":
            print(f"  {gff_path.name}: GFF3 ✓")
            final_gff_paths.append(gff_path)
        else:
            print(f"  {gff_path.name}: {fmt.upper()} — converting to GFF3...")
            try:
                converted = convert_to_gff3(gff_path, debug=debug, debug_dir=debug_dir)
            except RuntimeError as e:
                print(f"\nError: {e}", file=sys.stderr); sys.exit(1)
            final_gff_paths.append(converted)
            if not debug:
                converted_temps.append(converted)

    # ── Step 1: chromosome map ─────────────────────────────────────────────
    chrom_map = check_or_build_chrom_map(
        final_gff_paths, fasta_path, vcf_path, map_path, template_out
    )
    if chrom_map is None:
        _cleanup_temps(converted_temps); sys.exit(1)

    # ── Step 2: VCF alleles ────────────────────────────────────────────────
    extra_lines: list[str] = []
    if vcf_path:
        print(f"\nExtracting canonical alleles from VCF: {vcf_path.name}")
        extra_lines = vcf_variants_as_gff_lines(vcf_path, chrom_map)

    # ── Step 3: merge and write GFF ───────────────────────────────────────
    print(f"\nMerging {len(final_gff_paths)} GFF file(s) -> {out_path}")
    n = process_gff(final_gff_paths, out_path, chrom_map, keep_sources, extra_lines)
    print(f"  {n:,} feature lines written.")

    # ── Step 4: sort ──────────────────────────────────────────────────────
    print(f"\nSorting {out_path}...")
    sort_gff_inplace(out_path, debug=debug)
    print("Done.")

    # ── Cleanup ────────────────────────────────────────────────────────────
    _cleanup_temps(converted_temps)

    print(f"\nNext step:")
    print(f"  python build_db.py {out_path}")


if __name__ == "__main__":
    main()
