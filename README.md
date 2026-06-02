# gff2genbank

A Shiny for Python web app for browsing genomic annotations and exporting
user-defined regions as annotated GenBank files.  Developed for *C. elegans*
WormBase data but adaptable to any organism with a GFF3 annotation file and a
genome FASTA.

---

## Overview

The app loads a GFF3 annotation database and a genome FASTA on startup, then
lets you:

1. **Browse** a region by gene name or genomic coordinates in an interactive
   Plotly genome browser.
2. **Select** which feature types and variant classes to include in the export.
3. **Download** a fully annotated GenBank file ready to open in ApE, Benchling,
   SnapGene, or any compatible sequence viewer.

Custom databases can be loaded for a single session using the sidebar, without
restarting the server.

---

## Requirements

### Python

Python 3.11 or later is recommended.

### System tools

| Tool | Purpose |
|------|---------|
| `samtools` | FASTA indexing in `prepare_gff.py` |
| `bedtools` (optional) | GFF sorting in `prepare_gff.py` (falls back to Python sort if absent) |
| `agat` (optional) | GTF/GFF2 → GFF3 conversion in `prepare_gff.py` (only needed for non-GFF3 inputs) |
| `conda` (optional) | Recommended for managing bioinformatics packages |

Install samtools via conda:
```bash
conda install -c bioconda samtools
```

### Python packages

Install all Python dependencies with pip:

```bash
pip install \
    shiny \
    shinywidgets \
    plotly \
    biopython \
    gffutils \
    pyfaidx
```

Or with conda for the bioinformatics packages and pip for the rest:

```bash
conda install -c bioconda gffutils pyfaidx
pip install shiny shinywidgets plotly biopython
```

**Optional** — only needed if you are processing a VCF to extract canonical
alleles during GFF preparation:

```bash
conda install -c bioconda cyvcf2
```

**Optional** — `bedtools` is used by `prepare_gff.py` to sort the output GFF.
If not installed, a Python fallback is used automatically:

```bash
conda install -c bioconda bedtools
```

**Optional** — `agat` is required by `prepare_gff.py` only if your input is in
GTF or GFF2 format. GFF3 inputs do not need it:

```bash
conda install -c bioconda agat
```

---

## Data files

The app expects three files in a single data directory:

| File | Description |
|------|-------------|
| `*.gff3` or `*.gff3.gz` | Prepared, merged GFF3 annotation file |
| `*.fa` | Genome FASTA (must be indexed with `samtools faidx`) |
| `*.gff3.db` | gffutils SQLite database built from the GFF3 |

The default data directory is `./data` (alongside `app.py`).  Override it
with the `GFF_APP_DATA_DIR` environment variable (see
[Configuration](#configuration)).

---

## Building the database

The database only needs to be built once per GFF file.  If you update the GFF,
rebuild the database.

### Step 1 — Prepare the GFF3 (optional but recommended)

If you have multiple GFF sources (e.g. WormBase annotations + Million Mutation
Project), merge and normalise them first:

```bash
python prepare_gff.py \
    --gff annotations.gff3.gz \
    --gff million_mutation.gff3.gz \
    --fasta genome.fa \
    --vcf variations.vcf.gz \
    -o prepared.gff3.gz
```

**Chromosome name mismatch:** If the chromosome names differ between your GFF
and FASTA files, `prepare_gff.py` will detect the mismatch, write a template
mapping file called `chrom_map_template.tsv`, and exit.  Edit the
`canonical_name` column in that file so every entry matches the names in your
FASTA, then re-run with `--chrom-map`:

```bash
python prepare_gff.py \
    --gff annotations.gff3.gz \
    --gff million_mutation.gff3.gz \
    --fasta genome.fa \
    --vcf variations.vcf.gz \
    --chrom-map chrom_map_template.tsv \
    --keep-sources WormBase,Million_mutation,Allele \
    -o prepared.gff3.gz
```

`--keep-sources` accepts a comma-separated list of GFF `source` column values
to retain.  Omit it to keep all sources.

`--vcf` is optional. When provided, canonical alleles (non-WBVar, non-gk) are
extracted from the VCF and appended to the output as `sequence_alteration`
features. Requires `cyvcf2`.

### Step 2 — Build the gffutils database

```bash
python build_db.py prepared.gff3.gz
```

This creates `prepared.gff3.gz.db` in the same directory.  To specify a
different output path:

```bash
python build_db.py prepared.gff3.gz --out /path/to/output.db
```

To rebuild an existing database:

```bash
python build_db.py prepared.gff3.gz --force
```

Build time is approximately 5 minutes for a full WormBase GFF3 and a few
seconds for a filtered/prepared file.

### Step 3 — Index the FASTA

```bash
samtools faidx genome.fa
```

This creates `genome.fa.fai` alongside the FASTA.  `pyfaidx` (used by the app)
requires this index to be present.

---

## Configuration

Edit `config.py` to change the default file paths, organism metadata, and
display behaviour.

```bash
# Override the data directory at runtime without editing config.py:
GFF_APP_DATA_DIR=/path/to/data shiny run app.py
```

Key settings in `config.py`:

| Variable | Description |
|----------|-------------|
| `DATA_DIR` | Root directory for GFF, FASTA, and DB files |
| `GFF_PATH` | Path to the prepared GFF3 file |
| `FASTA_PATH` | Path to the genome FASTA |
| `DB_PATH` | Path to the gffutils database |
| `ORGANISM_NAME` | Full organism name used in GenBank output |
| `ORGANISM_SHORT` | Abbreviated name used in the UI |
| `DEFAULT_REGION` | Genomic region loaded on startup (format: `chrom:start-end`) |
| `DEFAULT_GENE` | Default gene name shown in the gene search box |
| `LOAD_FLANK` | Flanking bases loaded on each side of the target region (default: 10,000 bp) |
| `VIEW_FLANK` | Initial Plotly x-axis window around the target (default: 1,000 bp) |

Variant/priority feature groups are configured separately in
`priority_groups.tsv` (generated by `setup/s04_priority_groups.py`).

---

## Running the app

```bash
shiny run app.py
```

Or with a custom data directory:

```bash
GFF_APP_DATA_DIR=/path/to/data shiny run app.py
```

The app will be available at `http://localhost:8000` by default.  On first
load, the database and FASTA are read into memory — this takes approximately
30 seconds.  A loading screen with progress messages is shown during this time.

---

## File structure

```
app.py                Entry point — creates the Shiny App object
config.py             Paths, colours, organism metadata, display defaults
data.py               GFF database and FASTA loading, region and feature queries
plot.py               Plotly genome browser figure builder
genbank.py            GenBank file serialiser (uses Biopython)
ui.py                 Shiny UI layout
server.py             Shiny server function and reactive logic
prepare_gff.py        GFF3 preparation and merging utility
build_db.py           gffutils database builder
diagnose_variants.py  Debug helper: inspect variant feature types in a DB
priority_groups.tsv   Variant/priority feature group definitions
requirements.txt      Python runtime dependencies (for pip / shinyapps.io)

setup/                Interactive end-to-end setup pipeline:
  setup.py            Orchestrator — run this to walk through every step
  s01_check_deps.py   Verify samtools / gffutils / biopython are available
  s02_prepare_gff.py  Wraps prepare_gff.py
  s03_build_db.py     Wraps build_db.py
  s04_priority_groups.py  Generates priority_groups.tsv from a DB
  s05_validate.py     Sanity-check the prepared DB and FASTA

data/                 Runtime data (gitignored). Place the prepared GFF/DB
                      and indexed FASTA here, or point GFF_APP_DATA_DIR at
                      a different location.
```

### Quickstart with the setup pipeline

Instead of running `prepare_gff.py` / `build_db.py` by hand, you can drive
the full pipeline interactively:

```bash
python setup/setup.py
```

It tracks progress in `data/setup_log.json` so reruns skip completed steps.

---

## Notes on gffutils settings

`build_db.py` uses specific gffutils options that matter for WormBase data:

- **`disable_infer_genes=True`, `disable_infer_transcripts=True`** — WormBase
  GFF files already contain explicit gene and transcript records.  Enabling
  inference causes gffutils to scan the entire file to synthesise redundant
  parent records, which was the main cause of 90-minute build times.  Disabling
  both drops build time to ~5 minutes.

- **`merge_strategy="create_unique"`** — WormBase reuses the same ID (e.g.
  `CDS:C48D1.2a`) for every exon of a given CDS.  The `"merge"` strategy
  collapses these into a single record and loses coordinates; `"create_unique"`
  appends a numeric suffix to each duplicate ID and preserves all records.
