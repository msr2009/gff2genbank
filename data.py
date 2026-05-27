"""
data.py
-------
All data-access functions: GFF database loading, gene/region lookup,
feature queries, variant queries, and sequence extraction.
"""

import re
import threading
from pathlib import Path

from config import (
    GFF_PATH, FASTA_PATH, DB_PATH,
)

# ---------------------------------------------------------------------------
# Server-side lazy-loaded handles (shared across all sessions)
# ---------------------------------------------------------------------------
_server_gff_db   = None
_server_fasta    = None
_server_lock     = threading.Lock()
_gene_name_index: dict[str, tuple[str, int, int, str]] | None = None

# Gene types to index for name lookups
_GENE_FEATURE_TYPES = (
    "gene", "ncRNA_gene", "pseudogene", "transposable_element_gene"
)
# Transcript types that may be top-level (no parent gene record) in some GFFs.
# These are indexed only when they have no parent, so isoforms in WormBase-
# style GFFs (where the gene record is the real searchable entity) are not
# cluttered into the index.
_TRANSCRIPT_FEATURE_TYPES = (
    "mRNA", "ncRNA", "pseudogenic_transcript",
    "piRNA", "lincRNA", "pre_miRNA", "miRNA",
    "snoRNA", "snRNA", "rRNA", "tRNA",
)
_GENE_ATTR_KEYS = ("Name", "locus", "sequence_name", "Alias")


def _build_gene_name_index(db) -> dict[str, tuple[str, int, int, str]]:
    """
    Build an in-memory dict mapping every gene name/alias/locus to
    (chrom, start, end, strand).  Built once at startup from a single
    SQL pass; subsequent lookups are O(1) dict access.

    Indexes two classes of features:
      1. Gene-level records (gene, ncRNA_gene, pseudogene, etc.).
      2. Transcript-level records (mRNA, ncRNA, etc.) — all of them,
         including isoforms that have a parent gene.  This allows searching
         by isoform ID (e.g. "M142.1c.1" or "Transcript:M142.1c.1") as
         well as by gene name/locus.  In GFFs that omit explicit gene
         records (e.g. Trinity assemblies), the mRNA is the only searchable
         entry and is indexed by its ID and Name attributes.
    """
    import json, time
    t0 = time.perf_counter()
    index: dict[str, tuple[str, int, int, str]] = {}

    cur = db.conn.cursor()
    all_types = _GENE_FEATURE_TYPES + _TRANSCRIPT_FEATURE_TYPES
    placeholders = ",".join("?" * len(all_types))
    sql = f"""
        SELECT id, seqid, start, end, strand, attributes, featuretype
        FROM   features
        WHERE  featuretype IN ({placeholders})
    """
    def _put(key: str, coords: tuple) -> None:
        """
        Insert coords into the index only if the key is new or the new
        span is longer than the existing one.  This ensures that a shared
        name (e.g. a locus tag that appears on multiple isoforms) always
        resolves to the widest coordinate range — typically the gene record
        itself or the longest isoform — rather than whichever SQL row
        happened to come last.
        """
        existing = index.get(key)
        if existing is None or (coords[2] - coords[1]) > (existing[2] - existing[1]):
            index[key] = coords

    for row in cur.execute(sql, all_types):
        fid, seqid, start, end, strand, attrs_raw, ftype = row
        coords = (seqid, start, end, strand)
        # Index by raw ID and, for WormBase-style "Gene:X" / "Transcript:X"
        # prefixed IDs, also by the unprefixed form.
        _put(fid, coords)
        for prefix in ("Gene:", "Transcript:"):
            if fid.startswith(prefix):
                _put(fid[len(prefix):], coords)
        # Index by every attribute value (Name, locus, sequence_name, Alias).
        try:
            attrs = json.loads(attrs_raw)
            for key in _GENE_ATTR_KEYS:
                for val in attrs.get(key, []):
                    if val:
                        _put(val, coords)
        except Exception:
            pass
    elapsed = time.perf_counter() - t0
    print(f"[data] Gene name index built: {len(index):,} entries in {elapsed:.2f}s")
    return index


def get_gene_name_index(db) -> dict[str, tuple[str, int, int, str]]:
    """Return the shared gene name index, building it on first call."""
    global _gene_name_index
    with _server_lock:
        if _gene_name_index is None:
            _gene_name_index = _build_gene_name_index(db)
    return _gene_name_index


def get_server_gff_db():
    global _server_gff_db
    with _server_lock:
        if _server_gff_db is None:
            import gffutils
            db_path = DB_PATH if DB_PATH.exists() else Path(str(GFF_PATH) + ".db")
            if not db_path.exists():
                raise FileNotFoundError(
                    f"GFF database not found at {db_path}. "
                    "Run build_db.py first."
                )
            _server_gff_db = gffutils.FeatureDB(str(db_path))
            print(f"[data] GFF database loaded: {db_path}")
    return _server_gff_db


def get_server_fasta():
    global _server_fasta
    with _server_lock:
        if _server_fasta is None:
            from pyfaidx import Fasta
            fai_path = Path(str(FASTA_PATH) + ".fai")
            if not FASTA_PATH.exists():
                raise FileNotFoundError(f"FASTA not found: {FASTA_PATH}")
            if not fai_path.exists():
                raise FileNotFoundError(
                    f"FASTA index not found: {fai_path}. "
                    "Run: samtools faidx " + str(FASTA_PATH)
                )
            _server_fasta = Fasta(str(FASTA_PATH))
            print(f"[data] FASTA loaded:        {FASTA_PATH}")
            print(f"[data] FASTA index loaded:  {fai_path}")
    return _server_fasta


# ---------------------------------------------------------------------------
# Database + FASTA compatibility validation
# ---------------------------------------------------------------------------

def validate_db_fasta(db, fa) -> list[str]:
    """
    Check that db and fa are compatible:
      1. DB has the required gffutils tables (catches non-gffutils SQLite files).
      2. At least one chromosome name appears in both DB and FASTA.
    Returns a list of error strings (empty = all OK).
    """
    errors = []

    # Check DB has expected gffutils schema
    try:
        cur = db.conn.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {"features", "relations", "meta"}
        missing  = required - tables
        if missing:
            errors.append(
                f"Database is missing required tables ({', '.join(sorted(missing))}). "
                "This file may not be a valid gffutils database. "
                "Re-build it with build_db.py from a GFF3 file."
            )
            return errors   # can't do further checks
    except Exception as e:
        errors.append(
            f"Cannot query database: {e}. "
            "Ensure the file is a valid gffutils database built with build_db.py."
        )
        return errors

    # Check chromosome name overlap between DB and FASTA
    try:
        db_chroms  = {r[0] for r in cur.execute(
            "SELECT DISTINCT seqid FROM features LIMIT 500"
        )}
        fa_chroms  = set(fa.keys())
        overlap    = db_chroms & fa_chroms
        if not overlap:
            db_sample  = ", ".join(sorted(db_chroms)[:5])
            fa_sample  = ", ".join(sorted(fa_chroms)[:5])
            errors.append(
                "No chromosome names match between the GFF database and FASTA. "
                f"Database has: {db_sample}… | "
                f"FASTA has: {fa_sample}… "
                "These files may be from different assemblies, or chromosome names "
                "may need to be standardised. Use prepare_gff.py with --chrom-map "
                "to remap chromosome names before rebuilding the database."
            )
    except Exception as e:
        errors.append(f"Chromosome compatibility check failed: {e}")

    return errors


# ---------------------------------------------------------------------------
# Region / gene lookup
# ---------------------------------------------------------------------------

def parse_region(region_str: str) -> tuple[str, int, int]:
    """
    Parse 'chrom:start-end' (1-based, inclusive).
    Accepts commas in numbers: IV:13,000,000-13,010,000
    """
    s = region_str.strip().replace(",", "")
    m = re.match(r'^(.+):(\d+)[-–](\d+)$', s)
    if not m:
        raise ValueError(
            f"Cannot parse region '{region_str}'. "
            "Expected format: chrom:start-end  e.g. IV:13000000-13010000"
        )
    chrom = m.group(1)
    start = int(m.group(2))
    end   = int(m.group(3))
    if start >= end:
        raise ValueError(f"Start ({start:,}) must be less than end ({end:,}).")
    return chrom, start, end


def gene_coords(db, gene_name: str) -> tuple[str, int, int, str]:
    """
    Return (chrom, start, end, strand) for a gene by name or WormBase ID.
    Uses an in-memory index built once at startup — O(1) lookup.
    """
    index = get_gene_name_index(db)
    for attempt in [gene_name, f"Gene:{gene_name}"]:
        if attempt in index:
            return index[attempt]
    raise ValueError(
        f"Gene '{gene_name}' not found in the loaded database. "
        "Check the spelling, or try searching by coordinates instead "
        "(e.g. III:10900000-10910000)."
    )


# ---------------------------------------------------------------------------
# GFF feature queries
# ---------------------------------------------------------------------------

def db_chroms(db) -> list[str]:
    """
    Return a sorted list of all chromosome/contig names present in the
    gffutils database.  Used to produce helpful error messages when the
    user supplies a chromosome name that does not exist in the DB.
    """
    try:
        cur = db.conn.cursor()
        rows = cur.execute("SELECT DISTINCT seqid FROM features").fetchall()
        return sorted(r[0] for r in rows)
    except Exception:
        return []


def query_region(db, chrom: str, start: int, end: int):
    """
    Single db.region() pass returning all features overlapping the region.
    Callers split the results rather than each making their own query.
    Returns a list of raw gffutils Feature objects.
    """
    return list(db.region(seqid=chrom, start=start, end=end,
                           completely_within=False))


def features_in_region(db, chrom: str, start: int, end: int,
                        _raw: list | None = None) -> dict[str, list]:
    """
    Return all GFF features overlapping the region, grouped by feature type.
    Each value is a list of feature dicts: {id, name, start, end, strand, attrs}.
    Pass _raw to reuse an already-fetched feature list (avoids a second query).
    """
    raw = _raw if _raw is not None else query_region(db, chrom, start, end)
    by_type: dict[str, list] = {}
    for feat in raw:
        ft = feat.featuretype
        by_type.setdefault(ft, []).append({
            "id":     feat.id,
            "name":   (feat.attributes.get("Name", [feat.id]) or [feat.id])[0],
            "start":  feat.start,
            "end":    feat.end,
            "strand": feat.strand,
            "source": feat.source,
            "attrs":  dict(feat.attributes),
        })
    return by_type


def transcript_structures(db, chrom: str, start: int, end: int,
                           _raw: list | None = None) -> list[dict]:
    """
    Return per-transcript drawing structures for the preview plot.

    For protein-coding transcripts: draw CDS + UTR blocks.
    For non-coding transcripts: draw exon blocks coloured by transcript type.

    Uses a single batch SQL query for all children instead of one query
    per transcript (#4 fix). Pass _raw to reuse an already-fetched feature
    list and avoid a second db.region() call (#3 fix).

    Each returned dict:
    {
        id, name, start, end, strand, tx_type, coding,
        blocks: [(start, end, feature_type), ...]
    }
    """
    transcript_types = {
        "mRNA", "ncRNA", "pseudogenic_transcript",
        "piRNA", "lincRNA", "pre_miRNA", "miRNA",
        "snoRNA", "snRNA", "rRNA", "tRNA",
    }

    raw = _raw if _raw is not None else query_region(db, chrom, start, end)
    txs = [f for f in raw if f.featuretype in transcript_types]

    if not txs:
        return []

    # ── Batch children query (#4) ─────────────────────────────────────────
    # One SQL query for all transcripts instead of one per transcript.
    tx_ids       = [tx.id for tx in txs]
    placeholders = ",".join("?" * len(tx_ids))
    sql = f"""
        SELECT r.parent, f.start, f.end, f.featuretype
        FROM   relations r
        JOIN   features  f ON f.id = r.child
        WHERE  r.parent IN ({placeholders})
        AND    r.level  = 1
    """
    children_by_parent: dict[str, dict[str, list[tuple[int, int]]]] = {
        tx_id: {} for tx_id in tx_ids
    }
    try:
        cur = db.conn.cursor()
        for parent, cs, ce, ctype in cur.execute(sql, tx_ids):
            children_by_parent[parent].setdefault(ctype, []).append((cs, ce))
    except Exception as exc:
        print(f"[transcript_structures] Batch children query failed: {exc}")

    # ── Batch parent-gene query ───────────────────────────────────────────
    # Find the level-2 ancestor (gene record) for each transcript.
    # For GFFs without an explicit gene record the transcript itself is used
    # as its own gene group, which keeps the stacking logic consistent.
    gene_id_by_tx: dict[str, str] = {}
    try:
        gene_types   = set(_GENE_FEATURE_TYPES)
        parent_sql   = f"""
            SELECT r.child, r.parent, f.featuretype
            FROM   relations r
            JOIN   features  f ON f.id = r.parent
            WHERE  r.child IN ({placeholders})
            AND    r.level = 1
        """
        for child, parent, ptype in cur.execute(parent_sql, tx_ids):
            if ptype in gene_types:
                gene_id_by_tx[child] = parent
    except Exception as exc:
        print(f"[transcript_structures] Parent-gene query failed: {exc}")

    # ── Build result dicts ────────────────────────────────────────────────
    results = []
    for tx in txs:
        tx_type = tx.featuretype
        name    = (tx.attributes.get("Name", [tx.id]) or [tx.id])[0]
        locus   = tx.attributes.get("locus", [])
        display_name = f"{locus[0]} ({name})" if locus else name

        child_by_type = children_by_parent.get(tx.id, {})

        # A transcript is "coding" (use UTR/CDS colour scheme) if it has
        # explicit CDS children OR if it has UTR children (mRNA-only GFFs
        # that carry exons + UTRs but no separate CDS records).
        has_cds  = bool(child_by_type.get("CDS"))
        has_utrs = bool(
            child_by_type.get("five_prime_UTR")
            or child_by_type.get("three_prime_UTR")
        )
        coding = has_cds or has_utrs

        if coding:
            blocks = []
            if has_cds:
                # Classic layout: CDS blocks + UTRs + introns.
                for ct in ("CDS", "five_prime_UTR", "three_prime_UTR", "intron"):
                    for s, e in child_by_type.get(ct, []):
                        blocks.append((s, e, ct))
            else:
                # mRNA-only layout: exon blocks carry the coding body,
                # UTRs are drawn on top with their proper colours.
                for s, e in child_by_type.get("exon", [(tx.start, tx.end)]):
                    blocks.append((s, e, "exon"))
                for ct in ("five_prime_UTR", "three_prime_UTR", "intron"):
                    for s, e in child_by_type.get(ct, []):
                        blocks.append((s, e, ct))
        else:
            exons = child_by_type.get("exon", [(tx.start, tx.end)])
            blocks = [(s, e, tx_type) for s, e in exons]
            for s, e in child_by_type.get("intron", []):
                blocks.append((s, e, "intron"))

        results.append({
            "id":      tx.id,
            "name":    display_name,
            "start":   tx.start,
            "end":     tx.end,
            "strand":  tx.strand,
            "tx_type": tx_type,
            "coding":  coding,
            "blocks":  sorted(blocks, key=lambda b: b[0]),
            # gene_id: ID of the parent gene record, or tx.id if none exists.
            # Used by plot.py to group isoforms together during row packing.
            "gene_id": gene_id_by_tx.get(tx.id, tx.id),
        })

    return results


# ---------------------------------------------------------------------------
# Variant queries
# ---------------------------------------------------------------------------

def variant_features(
    db,
    chrom: str,
    start: int,
    end: int,
    priority_groups: list[tuple[str, list[tuple[str, str]]]] | None = None,
    _raw: list | None = None,
) -> dict[str, list]:
    """
    Return priority features from the GFF, organised by caller-supplied groups.

    Parameters
    ----------
    priority_groups:
        Ordered list of (group_name, [(source_pat, featuretype_pat), ...]).
        Patterns may be '*' to match any value in that column.
        First matching group wins (no feature appears in two groups).
        If None or empty, returns an empty dict.

    _raw:
        Pre-fetched list of gffutils Feature objects.  If omitted, the region
        is queried from the database.

    Returns a dict keyed by group display name, each value a list of dicts:
    {
        pos, end, name, var_type, source,
        substitution,
        is_point_mutation,   # True for features spanning <= 2 bp
        group,               # display group name
    }
    """
    if not priority_groups:
        return {}

    group_names = [g for g, _ in priority_groups]
    results: dict[str, list] = {g: [] for g in group_names}
    raw = _raw if _raw is not None else query_region(db, chrom, start, end)

    def _first_group(source: str, featuretype: str) -> str | None:
        for gname, patterns in priority_groups:
            for pat_src, pat_ft in patterns:
                src_ok = pat_src == "*" or pat_src == source
                ft_ok  = pat_ft  == "*" or pat_ft  == featuretype
                if src_ok and ft_ok:
                    return gname
        return None

    try:
        for feat in raw:
            group = _first_group(feat.source, feat.featuretype)
            if group is None:
                continue
            attrs     = feat.attributes
            # Use public_name if present (WormBase), fall back to Name then ID
            pub_names = (
                attrs.get("public_name")
                or attrs.get("Name")
                or [feat.id]
            )
            name     = pub_names[0] if pub_names else feat.id
            is_point = (feat.end - feat.start) <= 2
            results[group].append({
                "pos":               feat.start,
                "end":               feat.end,
                "name":              name,
                "var_type":          feat.featuretype,
                "source":            feat.source,
                "substitution":      attrs.get("substitution", [""])[0],
                "is_point_mutation": is_point,
                "group":             group,
            })
    except Exception as exc:
        print(f"[variant_features] Query error: {exc}")

    return results


# ---------------------------------------------------------------------------
# Sequence extraction
# ---------------------------------------------------------------------------

def extract_sequence(fasta_handle, chrom: str, start: int, end: int) -> str:
    """Extract genomic sequence (1-based, inclusive)."""
    return str(fasta_handle[chrom][start - 1:end])
