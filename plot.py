"""
plot.py
-------
Builds the interactive Plotly annotation preview figure.

Layout design:
  - Each mRNA isoform gets its own dedicated row (never packed with others).
  - All other annotation types (ncRNA, piRNA, pseudogene, etc.) are packed
    using greedy interval scheduling: non-overlapping features share rows.
  - Variants: point mutations on packed rows above, spanning bars below.
  - 3' UTR drawn as a pentagon (====>).
  - Introns drawn as optional filled blocks (off by default), toggled via
    "intron" in active_ftypes. The dotted backbone line is always shown.
  - "End of loaded region" labels tile vertically along the dashed boundary
    line rather than appearing as a single pointer at the side.
  - Y-axis fixedrange=True (horizontal pan/zoom only).
  - Position axis shown top AND bottom via xaxis2 overlay.
"""

import plotly.graph_objects as go
from config import FEATURE_COLORS, THREE_PRIME_UTR_TYPES, ALWAYS_HANDLED

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------
BLOCK_H        = 0.18
INTRON_H       = 0.10   # intron blocks are shorter than CDS
UTR_H          = 0.12
ROW_GAP        = 0.55   # mRNA CDS rows — kept spacious for quick reading
PACKED_ROW_GAP = 0.26   # noncoding, extra features, variants — tighter packing
VAR_ROW_GAP    = 0.26   # variant rows (point mutations + spans)
LABEL_SIZE     = 12
VAR_LABEL_SIZE = 10
BACKBONE_WIDTH = 2.0    # slightly thicker so dashed line is clearly visible
TOOLTIP_SPLIT_BP = 500  # short features get 1 tooltip point; long get 3


def _color(key: str, color_map: dict | None = None) -> str:
    if color_map is not None:
        return color_map.get(key, "#9E9E9E")
    return FEATURE_COLORS.get(key, "#888888")


# ---------------------------------------------------------------------------
# Row packing
# ---------------------------------------------------------------------------

def _pack_rows(intervals):
    """
    Greedy interval packing. Returns list of 0-based row indices,
    in the same order as the input intervals.
    Sorts by start coordinate for optimal (minimum-row) packing.
    """
    indexed  = sorted(enumerate(intervals), key=lambda x: x[1][0])
    row_ends = []
    result   = [None] * len(intervals)
    for orig_i, (start, end) in indexed:
        placed = False
        for r, re_ in enumerate(row_ends):
            if start > re_ + 1:
                row_ends[r] = end
                result[orig_i] = r
                placed = True
                break
        if not placed:
            row_ends.append(end)
            result[orig_i] = len(row_ends) - 1
    return result


def _pack_point_mutations(variants, strand_mode, chars_per_bp):
    """
    Label-aware packing for point mutations.

    Each variant occupies a diamond marker at `pos` plus a text label
    extending right (+ strand) or left (- strand).  The label's genomic
    footprint is estimated from the variant name length so that two
    mutations whose labels would visually overlap are placed on separate
    rows.

    chars_per_bp  -- estimated base-pairs per label character at the
                     current zoom level; derived from view width at
                     render time.
    """
    CHAR_EXTRA_BP = 6   # fixed padding added around each label (bp)

    def label_interval(v):
        name_bp = len(v.get("name", "")) / chars_per_bp + CHAR_EXTRA_BP
        pos = v["pos"]
        if strand_mode == "-":
            return (pos - name_bp, pos + 1)
        else:
            return (pos, pos + 1 + name_bp)

    intervals = [label_interval(v) for v in variants]
    return _pack_rows(intervals)


def _pack_span_variants(variants, strand_mode, chars_per_bp):
    """
    Label-aware packing for spanning variants (deletions, insertions, etc.).

    The bar occupies [pos, end].  The label is placed just outside the bar's
    3' edge (right of `end` on + strand, left of `pos` on - strand), so the
    effective footprint is extended by the label width on that side.
    """
    CHAR_EXTRA_BP = 6   # fixed padding around each label (bp)

    def label_interval(v):
        name_bp = len(v.get("name", "")) / chars_per_bp + CHAR_EXTRA_BP
        if strand_mode == "-":
            return (v["pos"] - name_bp, v["end"])
        else:
            return (v["pos"], v["end"] + name_bp)

    intervals = [label_interval(v) for v in variants]
    return _pack_rows(intervals)

def _tips(fig, x0, x1, y_mid, text):
    """
    Place invisible scatter points for hover tooltips.
    - Short features (<= TOOLTIP_SPLIT_BP): one point at centre.
    - Long features (> TOOLTIP_SPLIT_BP): points at x0, centre, x1.
    Tooltip background is light grey for readability.
    """
    mid = (x0 + x1) / 2
    xs  = [mid] if (x1 - x0) <= TOOLTIP_SPLIT_BP else [x0, mid, x1]
    fig.add_trace(go.Scatter(
        x=xs, y=[y_mid] * len(xs),
        mode="markers",
        marker=dict(size=6, opacity=0),
        hovertext=text, hoverinfo="text",
        hoverlabel=dict(
            bgcolor="#f0f0f0",
            font=dict(color="#333333", size=11),
            bordercolor="#cccccc",
        ),
        showlegend=False,
    ))


def _rect(shapes, x0, x1, y_mid, h, color, opacity=0.88):
    shapes.append(dict(
        type="rect", x0=x0, x1=x1,
        y0=y_mid - h / 2, y1=y_mid + h / 2,
        fillcolor=color, opacity=opacity,
        line=dict(color=color, width=0.5),
    ))


def _backbone(shapes, x0, x1, y_mid):
    """Dotted backbone line — slightly thicker so it reads clearly as dashed."""
    shapes.append(dict(
        type="line", x0=x0, x1=x1, y0=y_mid, y1=y_mid,
        line=dict(color="#bbbbbb", width=BACKBONE_WIDTH, dash="dot"),
    ))


# Fixed arrowhead width in genomic coordinates (bp).
ARROW_BP = 20


def _pentagon_utr(shapes, fig, x0, x1, y_mid, color, tip_at_high_coord, hover):
    """
    Draw a 3' UTR as a pentagon (====>) with a fixed 20 bp arrowhead.

    tip_at_high_coord=True  -> tip at x1 (+ strand gene)
    tip_at_high_coord=False -> tip at x0 (- strand gene)
    """
    h     = UTR_H
    tip_w = ARROW_BP

    if tip_at_high_coord:
        body_end = x1 - tip_w
        if body_end > x0:
            _rect(shapes, x0, body_end, y_mid, h, color)
        path = (f"M {body_end},{y_mid + h/2} "
                f"L {x1},{y_mid} "
                f"L {body_end},{y_mid - h/2} Z")
    else:
        body_start = x0 + tip_w
        if body_start < x1:
            _rect(shapes, body_start, x1, y_mid, h, color)
        path = (f"M {body_start},{y_mid + h/2} "
                f"L {x0},{y_mid} "
                f"L {body_start},{y_mid - h/2} Z")

    shapes.append(dict(
        type="path", path=path,
        fillcolor=color, opacity=0.88,
        line=dict(color=color, width=0),
    ))
    _tips(fig, x0, x1, y_mid, hover)


def _label(fig, x, y_mid, text, strand_mode="+"):
    # On + strand: label sits at the genomic start (visually left), anchor left.
    # On - strand: x-axis is flipped, so tx["end"] is visually on the left;
    #              anchor left so the label extends rightward (visually) from
    #              the left edge of the feature.
    xanchor = "left"
    fig.add_annotation(
        x=x, y=y_mid + BLOCK_H / 2 + 0.07,
        text=text, showarrow=False,
        xanchor=xanchor,
        font=dict(size=LABEL_SIZE, color="#333"),
    )


# Approximate fraction of the view range that one character occupies at
# LABEL_SIZE=12px in a typical plot.  Used to estimate label width in bp
# so we can suppress labels that would overflow their gene span.
# Calibrated conservatively (slightly wide) to avoid false "fits" calls.
_CHAR_BP_FRACTION = 0.008


def _label_fits(text: str, span_bp: int, view_bp: int) -> bool:
    """
    Return True if a label of len(text) characters is likely to fit within
    span_bp without overflowing into neighbouring features.

    The estimate is:  label_width_bp ≈ len(text) * _CHAR_BP_FRACTION * view_bp
    If the label would be wider than the gene span it is suppressed; the name
    is still accessible via the hover tooltip on every block.
    """
    if view_bp <= 0:
        return True
    estimated_label_bp = len(text) * _CHAR_BP_FRACTION * view_bp
    return estimated_label_bp <= span_bp


# ---------------------------------------------------------------------------
# Main figure builder
# ---------------------------------------------------------------------------

def build_preview(
    transcripts,
    extra_features,
    variants_by_group,
    region_start,
    region_end,
    load_start,
    load_end,
    view_start,
    view_end,
    active_ftypes,
    variant_group_visibility,
    strand_mode,
    uirevision=1,
    color_map=None,
):
    fig    = go.Figure()
    shapes = []
    y      = 0.0

    # ------------------------------------------------------------------
    # Empty region notice
    # ------------------------------------------------------------------
    has_any = (
        any(tx["tx_type"] in active_ftypes or tx["tx_type"] == "mRNA"
            for tx in transcripts)
        or any(ft in active_ftypes for ft in extra_features)
        or any(v for v in variants_by_group.values())
    )
    if not has_any:
        fig.add_annotation(
            x=(view_start + view_end) / 2,
            y=0,
            text="No annotated features in this region",
            showarrow=False,
            font=dict(size=14, color="#aaa"),
            xanchor="center",
            yanchor="middle",
        )

    # ------------------------------------------------------------------
    # Transcript isoform rows — mRNA only, packed by gene group.
    #
    # Algorithm:
    #   1. Group isoforms by gene_id.
    #   2. Compute each gene's envelope (min start, max end of its isoforms).
    #   3. Merge overlapping/contained gene envelopes into "super-groups"
    #      so that genes which overlap are never interleaved.
    #   4. Pack super-group envelopes with _pack_rows() so non-overlapping
    #      groups share row bands.
    #   5. Within each super-group, assign contiguous rows: one row per
    #      isoform, all isoforms of the same gene together.
    # ------------------------------------------------------------------

    # Collect visible mRNA transcripts (those with at least one drawn block).
    mrna_txs = []
    for tx in transcripts:
        if tx["tx_type"] != "mRNA":
            continue
        visible_blocks = [
            (s, e, ft) for s, e, ft in tx["blocks"]
            if ft in active_ftypes or ft == tx["tx_type"]
        ]
        if visible_blocks:
            mrna_txs.append((tx, visible_blocks))

    if mrna_txs:
        # ── Step 1: group isoforms by gene_id ─────────────────────────
        from collections import defaultdict
        gene_isoforms: dict[str, list] = defaultdict(list)
        for tx, vblocks in mrna_txs:
            gene_isoforms[tx["gene_id"]].append((tx, vblocks))

        # ── Step 2: gene envelopes ────────────────────────────────────
        # gene_id -> (env_start, env_end)
        gene_envelope: dict[str, tuple[int, int]] = {}
        for gid, items in gene_isoforms.items():
            env_s = min(tx["start"] for tx, _ in items)
            env_e = max(tx["end"]   for tx, _ in items)
            gene_envelope[gid] = (env_s, env_e)

        # ── Step 3: merge overlapping gene envelopes into super-groups ─
        # Sort genes by envelope start; greedily merge if they overlap or
        # one contains the other.
        sorted_genes = sorted(gene_envelope.items(), key=lambda kv: kv[1][0])
        super_groups: list[list[str]] = []   # each entry is a list of gene_ids
        sg_envelopes: list[tuple[int, int]] = []

        for gid, (gs, ge) in sorted_genes:
            if sg_envelopes and gs <= sg_envelopes[-1][1]:
                # Overlaps or is contained within the current super-group
                super_groups[-1].append(gid)
                sg_envelopes[-1] = (
                    sg_envelopes[-1][0],
                    max(sg_envelopes[-1][1], ge),
                )
            else:
                super_groups.append([gid])
                sg_envelopes.append((gs, ge))

        # ── Step 4: pack super-groups into row bands ──────────────────
        # Each super-group occupies N contiguous rows (one per isoform,
        # summed across all genes in the group).  _pack_rows treats each
        # super-group as an atomic interval; groups that don't overlap
        # share the same band (same set of row slots).
        sg_row_indices = _pack_rows(sg_envelopes)

        # ── Step 5: assign y positions and draw ───────────────────────
        # Band layout:
        #   - band_height[b] = max isoform-rows of any super-group in band b.
        #     Super-groups in the same band share the same row slots, so the
        #     band must be tall enough for the tallest group in it.
        #   - y_band_top[b]  = absolute y of the first row in band b.
        #   - Each super-group draws its isoforms from y_band_top[its band]
        #     downward, one ROW_GAP per isoform.

        band_height: dict[int, int] = {}  # band -> max isoform rows in band
        for sg_idx, band in enumerate(sg_row_indices):
            n = sum(len(gene_isoforms[gid]) for gid in super_groups[sg_idx])
            band_height[band] = max(band_height.get(band, 0), n)

        max_band = max(sg_row_indices) if sg_row_indices else 0
        y_band_top: dict[int, float] = {}
        cursor = y
        for band in range(max_band + 1):
            y_band_top[band] = cursor
            cursor -= band_height.get(band, 1) * ROW_GAP

        # Draw each super-group starting from its band's top y.
        for sg_idx, gene_ids in enumerate(super_groups):
            band  = sg_row_indices[sg_idx]
            row_y = y_band_top[band]
            # Sort genes within the super-group by envelope start.
            gene_ids_sorted = sorted(gene_ids, key=lambda g: gene_envelope[g][0])
            for gid in gene_ids_sorted:
                for tx, visible_blocks in gene_isoforms[gid]:
                    y_mid = row_y
                    _backbone(shapes, tx["start"], tx["end"], y_mid)

                    tip_at_high_coord = (tx["strand"] == "+")
                    for (bs, be, ftype) in visible_blocks:
                        color = _color(ftype, color_map)
                        hover = f"<b>{ftype}</b> {bs:,}-{be:,}<br>{tx['name']}"
                        if ftype in THREE_PRIME_UTR_TYPES:
                            _pentagon_utr(shapes, fig, bs, be, y_mid, color,
                                          tip_at_high_coord, hover)
                        elif ftype == "intron":
                            _rect(shapes, bs, be, y_mid, INTRON_H, color, opacity=0.55)
                            _tips(fig, bs, be, y_mid, hover)
                        else:
                            h = UTR_H if "UTR" in ftype else BLOCK_H
                            _rect(shapes, bs, be, y_mid, h, color)
                            _tips(fig, bs, be, y_mid, hover)

                    label_x = tx["end"] if strand_mode == "-" else tx["start"]
                    tx_span  = tx["end"] - tx["start"]
                    view_bp  = view_end - view_start
                    if _label_fits(tx["name"], tx_span, view_bp):
                        _label(fig, label_x, y_mid, tx["name"], strand_mode)
                    row_y -= ROW_GAP

        # Advance y past all bands.
        total_mrna_rows = sum(band_height.values())
        y -= total_mrna_rows * ROW_GAP

    # ------------------------------------------------------------------
    # Non-coding transcript types — one row per tx_type, all features of
    # that type share a single y level (no interval packing).
    # Labels are suppressed for features narrower than LABEL_MIN_BP since
    # the user can hover to identify them.
    # (ncRNA, piRNA, lincRNA, snoRNA, snRNA, rRNA, tRNA, etc.)
    # ------------------------------------------------------------------
    LABEL_MIN_BP = 500   # features shorter than this get no text label

    # Collect active non-coding types in a stable order
    noncoding_types = []
    for tx in transcripts:
        if tx["tx_type"] == "mRNA":
            continue
        if tx["tx_type"] not in active_ftypes:
            continue
        if tx["tx_type"] not in noncoding_types:
            noncoding_types.append(tx["tx_type"])

    noncoding_items = [tx for tx in transcripts
                       if tx["tx_type"] in noncoding_types]

    if noncoding_items:
        y -= PACKED_ROW_GAP * 0.2

        # Pack all non-coding features together across types using interval
        # scheduling — features that don't overlap share a row regardless of
        # type, just like the extra-features section below.
        nc_rows    = _pack_rows([(tx["start"], tx["end"]) for tx in noncoding_items])
        nc_max_row = max(nc_rows)

        for tx, row_idx in zip(noncoding_items, nc_rows):
            tx_type   = tx["tx_type"]
            y_mid     = y - row_idx * PACKED_ROW_GAP
            tip_right = (tx["strand"] == "+")   # genomic strand, not view mode

            _backbone(shapes, tx["start"], tx["end"], y_mid)

            # Identify the terminal (3'-most) block so we only draw the
            # directional arrow on that one block, not every exon.
            non_utr_blocks = [(bs, be) for bs, be, ft in tx["blocks"]
                              if ft not in THREE_PRIME_UTR_TYPES]
            if non_utr_blocks:
                if tip_right:
                    terminal_block = max(non_utr_blocks, key=lambda b: b[1])
                else:
                    terminal_block = min(non_utr_blocks, key=lambda b: b[0])
            else:
                terminal_block = None

            for (bs, be, ftype) in tx["blocks"]:
                block_color = _color(ftype, color_map)
                hover = f"<b>{tx_type}</b>: {tx['name']} {bs:,}-{be:,}"
                if ftype in THREE_PRIME_UTR_TYPES:
                    _pentagon_utr(shapes, fig, bs, be, y_mid, block_color,
                                  tip_right, hover)
                elif (bs, be) == terminal_block:
                    # Terminal exon only — draw body + directional arrow.
                    h     = UTR_H if "UTR" in ftype else BLOCK_H
                    tip_w = min(ARROW_BP, max(1, be - bs - 1))
                    if tip_right:
                        body_end = be - tip_w
                        if body_end > bs:
                            _rect(shapes, bs, body_end, y_mid, h, block_color)
                        path = (f"M {body_end},{y_mid + h/2} "
                                f"L {be},{y_mid} "
                                f"L {body_end},{y_mid - h/2} Z")
                    else:
                        body_start = bs + tip_w
                        if body_start < be:
                            _rect(shapes, body_start, be, y_mid, h, block_color)
                        path = (f"M {body_start},{y_mid + h/2} "
                                f"L {bs},{y_mid} "
                                f"L {body_start},{y_mid - h/2} Z")
                    shapes.append(dict(
                        type="path", path=path,
                        fillcolor=block_color, opacity=0.88,
                        line=dict(color=block_color, width=0),
                    ))
                    _tips(fig, bs, be, y_mid, hover)
                else:
                    # Non-terminal exon — plain rectangle, no arrow.
                    h = UTR_H if "UTR" in ftype else BLOCK_H
                    _rect(shapes, bs, be, y_mid, h, block_color)
                    _tips(fig, bs, be, y_mid, hover)

            if (tx["end"] - tx["start"]) >= LABEL_MIN_BP:
                label_x = tx["end"] if strand_mode == "-" else tx["start"]
                _label(fig, label_x, y_mid, tx["name"], strand_mode)

        y -= (nc_max_row + 1) * PACKED_ROW_GAP

    # ------------------------------------------------------------------
    # Other GFF annotation types — packed into shared rows
    # ------------------------------------------------------------------
    # Non-coding tx types are already rendered above, so exclude them here
    # to avoid double-drawing anything that also appears in extra_features.
    rendered_tx_types = {tx["tx_type"] for tx in transcripts}

    # ALWAYS_HANDLED comes from config — single canonical source of truth.
    # Also skip types already rendered as transcript rows (rendered_tx_types).
    plot_skip = ALWAYS_HANDLED | rendered_tx_types

    extra_items = []
    for ftype, feats in extra_features.items():
        if ftype in plot_skip or ftype not in active_ftypes:
            continue
        for feat in feats:
            extra_items.append((feat["start"], feat["end"], feat, ftype))

    extra_max_row = 0
    if extra_items:
        y -= PACKED_ROW_GAP * 0.2
        rows         = _pack_rows([(s, e) for s, e, _, _ in extra_items])
        max_row      = max(rows)
        extra_max_row = max_row
        for (s, e, feat, ftype), row in zip(extra_items, rows):
            y_mid = y - row * PACKED_ROW_GAP
            color = _color(ftype, color_map)
            _rect(shapes, s, e, y_mid, BLOCK_H, color)
            _tips(fig, s, e, y_mid,
                  f"<b>{ftype}</b>: {feat['name']} {s:,}-{e:,}")
        y -= (max_row + 1) * PACKED_ROW_GAP

    # ------------------------------------------------------------------
    # Variant tracks — one band per priority group so groups never share rows
    # ------------------------------------------------------------------
    view_bp      = max(view_end - view_start, 1)
    chars_per_bp = 800 / (7 * view_bp)
    bar_h        = BLOCK_H * 0.65

    first_group = True
    for group_name, visible in variant_group_visibility.items():
        if not visible:
            continue
        color = _color(group_name, color_map)
        grp_point = []
        grp_span  = []
        for v in variants_by_group.get(group_name, []):
            entry = {**v, "color": color}
            if v["is_point_mutation"]:
                grp_point.append(entry)
            else:
                grp_span.append(entry)

        if not grp_point and not grp_span:
            continue

        # Small gap before the first group; tighter between groups
        y -= PACKED_ROW_GAP * (0.5 if first_group else 0.25)
        first_group = False

        if grp_point:
            pm_rows = _pack_point_mutations(grp_point, strand_mode, chars_per_bp)
            for v, row in zip(grp_point, pm_rows):
                y_mid = y - row * VAR_ROW_GAP
                sub   = v.get("substitution", "")
                hover = (
                    f"<b>{v['name']}</b> ({v['group']})<br>"
                    f"Type: {v['var_type']}<br>"
                    f"Pos: {v['pos']:,}"
                    + (f"<br>{sub}" if sub else "")
                )
                fig.add_trace(go.Scatter(
                    x=[v["pos"]], y=[y_mid],
                    mode="markers+text",
                    marker=dict(symbol="diamond", size=8, color=v["color"],
                                line=dict(color="white", width=0.5)),
                    text=[v["name"]],
                    textposition="middle right",
                    textfont=dict(size=VAR_LABEL_SIZE, color="#333"),
                    hovertext=hover, hoverinfo="text",
                    hoverlabel=dict(
                        bgcolor="#f0f0f0",
                        font=dict(color="#333333", size=11),
                        bordercolor="#cccccc",
                    ),
                    showlegend=False,
                ))
            y -= (max(pm_rows) + 1) * VAR_ROW_GAP

        if grp_span:
            y -= VAR_ROW_GAP * 0.4
            span_rows = _pack_span_variants(grp_span, strand_mode, chars_per_bp)
            for v, row in zip(grp_span, span_rows):
                y_mid = y - row * VAR_ROW_GAP
                _rect(shapes, v["pos"], v["end"], y_mid, bar_h, v["color"], opacity=0.75)
                _tips(fig, v["pos"], v["end"], y_mid,
                      f"<b>{v['name']}</b> ({v['group']})<br>"
                      f"{v['var_type']} {v['pos']:,}-{v['end']:,}")
                span_label_x      = v["pos"] if strand_mode == "-" else v["end"]
                fig.add_annotation(
                    x=span_label_x, y=y_mid,
                    text=v["name"], showarrow=False,
                    xanchor="left", yanchor="middle",
                    font=dict(size=VAR_LABEL_SIZE, color="#333"),
                )
            y -= (max(span_rows) + 1) * VAR_ROW_GAP

    # ------------------------------------------------------------------
    # "End of loaded region" markers
    # Dashed vertical line spanning the full plot height.
    # Label text tiles vertically at regular intervals so it's visible
    # at any zoom level, rather than being a single pointer at one side.
    # ------------------------------------------------------------------
    y_range_top = BLOCK_H + 0.3
    y_range_bot = y - PACKED_ROW_GAP * 0.5

    # Tile interval in y-axis units: one label every ~6 rows (wider spacing
    # prevents the rotated text from overlapping itself at typical zoom levels)
    tile_step = PACKED_ROW_GAP * 6
    # Labels sit just outside the dashed line so they don't overlap features.
    # left boundary  -> label is to the LEFT  (xanchor="right", small negative offset)
    # right boundary -> label is to the RIGHT (xanchor="left",  small positive offset)
    # A small genomic offset (1 bp) keeps the text clear of the line itself.
    for pos, xanchor, x_offset in [
        (load_start, "right", -1),
        (load_end,   "left",   1),
    ]:
        # Vertical dashed line
        shapes.append(dict(
            type="line", x0=pos, x1=pos,
            y0=0, y1=1, yref="paper",
            line=dict(color="#dddddd", width=1.5, dash="dash"),
        ))
        # Tile labels from top to bottom of the content area
        label_text = "end of loaded region"
        tile_y = y_range_top - tile_step / 2
        while tile_y > y_range_bot:
            fig.add_annotation(
                x=pos + x_offset, y=tile_y,
                text=label_text,
                showarrow=False,
                xanchor=xanchor,
                textangle=-90,
                font=dict(size=11, color="#cccccc"),
            )
            tile_y -= tile_step

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    LABEL_PAD = 150
    x_range = [view_start - LABEL_PAD, view_end + LABEL_PAD]
    if strand_mode == "-":
        x_range = x_range[::-1]

    # Derive figure height directly from the actual y-coordinate span.
    # `y` has been decremented by every rendered row throughout the function,
    # so (y_range_top - y_range_bot) is the true content height in y-units.
    # Convert to pixels: 52px per ROW_GAP unit is the calibrated baseline.
    px_per_unit  = 52 / ROW_GAP
    y_span       = y_range_top - y_range_bot   # always positive
    fig_height   = max(380, 80 + int(y_span * px_per_unit))

    fig.update_layout(
        shapes=shapes,
        xaxis=dict(
            range=x_range,
            title="Genomic position",
            tickformat=",d",
            showgrid=True,
            gridcolor="#eeeeee",
            fixedrange=False,
        ),
        xaxis2=dict(
            overlaying="x", side="top",
            range=x_range, tickformat=",d",
            showgrid=False, fixedrange=False, matches="x",
        ),
        yaxis=dict(
            visible=False,
            range=[y_range_bot, y_range_top],
            fixedrange=True,
        ),
        plot_bgcolor="#fafafa",
        paper_bgcolor="#ffffff",
        margin=dict(l=10, r=40, t=35, b=45),
        height=fig_height,
        autosize=False,   # explicit height must not be overridden by autosize
        dragmode="pan",
        hovermode="closest",
        # uirevision: constant within a region (so toggle changes don't reset
        # zoom), but changes between regions (so Plotly fully resets the axes).
        # Must be a number, NOT a string — Plotly uses len(string) to initialise
        # xaxis.range[1] before the layout applies, causing off-by-one bugs.
        uirevision=uirevision,
    )
    return fig
