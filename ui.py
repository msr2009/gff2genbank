"""
ui.py — Shiny UI layout for the GFF -> GenBank app.
"""

from shiny import ui
from shinywidgets import output_widget
from config import FEATURE_COLORS, DEFAULT_REGION, DEFAULT_GENE, DB_PATH, FASTA_PATH

APP_VERSION = "v1.48"

CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; }
.card {
    background: #fff; border-radius: 10px; padding: 14px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 10px;
}
/* Force shinywidgets / ipywidgets plot to fill the card width */
/* Force the ipywidget iframe and plotly svg to fill the card */
.widget-subarea, .widget-output { width: 100% !important; height: auto !important; }
.plotly-graph-div, .js-plotly-plot { width: 100% !important; }
.plotly-graph-div svg, .js-plotly-plot svg { width: 100% !important; }
/* Ensure the shinywidgets iframe expands to the plot's natural height */
.widget-output iframe, .shiny-bound-output iframe { width: 100% !important; height: auto !important; min-height: 380px; }
h5 { color: #2c3e50; font-weight: 600; margin: 10px 0 4px; font-size: 0.92rem; }

/* ── Plot toolbar ────────────────────────────────────────────────── */
#plot-toolbar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 5px 8px 3px;
    background: #f8f9fa;
    border-bottom: 1px solid #e2e2e2;
    flex-wrap: wrap;
}
#plot-toolbar .tb-sep {
    width: 1px; height: 22px;
    background: #ccc; margin: 0 4px;
}
.tb-btn {
    display: inline-flex; align-items: center; justify-content: center;
    gap: 5px;
    padding: 5px 10px;
    border: 1px solid #ccc;
    border-radius: 5px;
    background: #fff;
    font-size: 0.82rem;
    color: #333;
    cursor: pointer;
    transition: background 0.12s, border-color 0.12s;
    user-select: none;
    white-space: nowrap;
}
.tb-btn:hover  { background: #e8f0fe; border-color: #4a90d9; color: #1a5fa8; }
.tb-btn.active { background: #ddeeff; border-color: #2176c7; color: #1a5fa8; font-weight: 600; }
.tb-btn svg    { flex-shrink: 0; }
.dot {
    display: inline-block; width: 10px; height: 10px;
    border-radius: 2px; margin-right: 4px; vertical-align: middle;
}
#error_out  { color:#c0392b; font-size:0.86em; min-height:1.1em; }
#status_out { color:#555;    font-size:0.86em; min-height:1.1em; }
#db_ready_flag { position: absolute; opacity: 0; pointer-events: none; font-size: 0; }
.progress-log {
    font-family: monospace; font-size:0.79em; color:#555;
    background:#f8f8f8; border:1px solid #e0e0e0; border-radius:5px;
    padding:6px 8px; margin-top:4px;
    max-height:100px; overflow-y:auto; white-space:pre-wrap;
}
.upload-section details { margin-top:4px; }
.upload-section summary { cursor:pointer; font-size:0.86em; color:#3498db; font-weight:600; }
.upload-note { font-size:0.78em; color:#888; margin:3px 0 6px; }
#window_display { font-family:monospace; font-size:0.83em; color:#2980b9; min-height:1.2em; }
.ft-row {
    display: flex; align-items: center; gap: 6px;
    margin-bottom: 2px; flex-wrap: nowrap;
}
/* input_switch renders a .shiny-input-container block — shrink it to
   just the switch widget width so the label sits beside it. */
.ft-row > .shiny-input-container {
    width: auto !important; flex-shrink: 0;
    margin-bottom: 0 !important;
}
.ft-row > .shiny-input-container .form-check {
    margin-bottom: 0; padding-left: 2.5em;
}
.ft-row > .shiny-input-container .form-check-label { display: none; }
.ft-row-label {
    display: flex; align-items: center; gap: 4px;
    font-size: 0.9em; flex-shrink: 1; flex-wrap: nowrap;
}
.dot.color-edit-btn {
    cursor: pointer;
    outline: 2px solid transparent;
    transition: outline-color 0.1s;
    flex-shrink: 0;
}
.dot.color-edit-btn:hover { outline-color: #555; }
.swatch-row {
    display: none; flex-wrap: wrap; gap: 3px;
    margin: 3px 0 4px auto;
    /* 10 swatches × 14px + 9 gaps × 3px = 167px — forces even 10×2 grid */
    width: 167px;
}
.swatch-row.open { display: flex; }
.color-swatch {
    display: inline-block; width: 14px; height: 14px;
    border-radius: 3px; cursor: pointer;
    border: 2px solid transparent;
    box-sizing: border-box;
    flex-shrink: 0;
    transition: border-color 0.1s;
}
.color-swatch:hover { border-color: #333; }
.color-swatch.selected { border-color: #111; box-shadow: 0 0 0 1px #fff inset; }

.loading-page {
    min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
    background: #f0f2f5;
    font-family: 'Segoe UI', Arial, sans-serif;
}
.loading-card {
    background: #fff; border-radius: 14px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.10);
    padding: 44px 52px; max-width: 480px; width: 90%;
    text-align: center;
}
.loading-logo { font-size: 2.8rem; margin-bottom: 6px; line-height: 1; }
.loading-title { font-size: 1.6rem; font-weight: 700; color: #1a252f; margin-bottom: 4px; }
.loading-version {
    display: inline-block; font-size: 0.82rem; font-weight: 600;
    color: #3498db; background: #eaf4fb;
    padding: 2px 10px; border-radius: 20px; margin-bottom: 8px;
}
.loading-subtitle { font-size: 0.95rem; color: #7f8c8d; margin-bottom: 24px; }
.loading-files {
    background: #f8f9fa; border: 1px solid #e8e8e8; border-radius: 8px;
    padding: 12px 18px; font-size: 0.85em; color: #555;
    line-height: 2.2; text-align: left; margin-bottom: 24px;
}
.loading-files code {
    background: #e8f4fd; padding: 1px 6px; border-radius: 3px;
    font-size: 0.9em; color: #1a6fa0; font-weight: 500;
}
.loading-status {
    display: flex; align-items: center; justify-content: center;
    gap: 10px; min-height: 40px; margin-bottom: 10px;
    font-size: 0.95rem; font-weight: 600; color: #2c3e50;
}
.loading-progress {
    height: 5px; background: #e8e8e8; border-radius: 3px;
    overflow: hidden; margin-bottom: 20px;
}
.loading-bar {
    height: 100%; width: 35%; background: #3498db; border-radius: 3px;
    animation: loading-slide 1.5s cubic-bezier(0.4,0,0.2,1) infinite;
}
.loading-bar.error { background: #e74c3c; animation: none; width: 100%; }
@keyframes loading-slide {
    0%   { margin-left: -35%; }
    100% { margin-left: 100%; }
}
"""

# JavaScript that captures Plotly zoom/pan and sends range to Shiny.
#
# Strategy: listen for the "plotly_relayout" custom event at the document
# level. Plotly dispatches this as a native DOM CustomEvent that bubbles,
# so we don't need a reference to the specific graph element.
# This works regardless of how shinywidgets wraps the figure.
#
# Also registers a Shiny message handler "get_plot_range" that Python can
# call to request the current range on demand (e.g. at download time).
JS = """
<script>
(function() {
  // --- Capture zoom/pan via bubbled plotly_relayout DOM event ---
  // Plotly fires this as a native CustomEvent on the graph div.
  // We catch it at document level so element identity doesn't matter.
  document.addEventListener("plotly_relayout", function(e) {
    var d = e.detail;
    if (!d) return;
    var x0 = d["xaxis.range[0]"];
    var x1 = d["xaxis.range[1]"];
    // Also handle autorange reset (double-click resets to full range)
    if (d["xaxis.autorange"] === true) {
      if (window.Shiny) {
        Shiny.setInputValue("plotly_autoreset", Date.now(), {priority: "event"});
        console.log("[plotly] autorange reset");
      }
      return;
    }
    if (x0 !== undefined && x1 !== undefined && window.Shiny) {
      Shiny.setInputValue(
        "plotly_xrange",
        {x0: Math.round(x0), x1: Math.round(x1)},
        {priority: "event"}
      );
      console.log("[plotly] relayout x0=" + Math.round(x0) + " x1=" + Math.round(x1));
    }
    // After a zoom-box interaction, switch dragmode back to pan.
    var graphs = document.querySelectorAll(".plotly-graph-div, .js-plotly-plot");
    if (graphs.length && window.Plotly) {
      var gd2 = graphs[0];
      var currentMode = gd2._fullLayout && gd2._fullLayout.dragmode;
      if (currentMode === "zoom") {
        Plotly.relayout(gd2, {dragmode: "pan"});
        document.querySelectorAll(".tb-btn[data-plotly-action='pan'], .tb-btn[data-plotly-action='zoom']")
          .forEach(function(b) {
            b.classList.toggle("active", b.dataset.plotlyAction === "pan");
          });
      }
    }
  });

  // --- Fallback: poll the Plotly figure's current range ---
  // Used when the DOM event approach doesn't fire (e.g. initial render).
  // Polls every 2s, only sends if range has changed.
  var lastX0 = null, lastX1 = null;
  function pollRange() {
    try {
      var graphs = document.querySelectorAll(".plotly-graph-div, .js-plotly-plot");
      for (var i = 0; i < graphs.length; i++) {
        var g = graphs[i];
        var layout = g.layout || (g._fullData && g._fullLayout);
        if (!layout) continue;
        var xrange = g._fullLayout &&
                     g._fullLayout.xaxis &&
                     g._fullLayout.xaxis.range;
        if (!xrange || xrange.length < 2) continue;
        var x0 = Math.round(xrange[0]);
        var x1 = Math.round(xrange[1]);
        if (x0 !== lastX0 || x1 !== lastX1) {
          lastX0 = x0; lastX1 = x1;
          if (window.Shiny) {
            Shiny.setInputValue(
              "plotly_xrange",
              {x0: x0, x1: x1},
              {priority: "event"}
            );
            console.log("[poll] range updated x0=" + x0 + " x1=" + x1);
          }
        }
        break;
      }
    } catch(e) {
      // silent
    }
  }
  setInterval(pollRange, 1000);

  // --- Color swatch interactions ---
  document.addEventListener("click", function(e) {

    // Dot click: toggle the swatch row open/closed.
    var dot = e.target.closest(".dot.color-edit-btn");
    if (dot) {
      var row = dot.closest(".ft-item").querySelector(".swatch-row");
      if (row) row.classList.toggle("open");
      return;
    }

    // Swatch click: send color to Shiny via setInputValue, update visuals,
    // close the palette row.
    var swatch = e.target.closest(".color-swatch");
    if (!swatch) return;
    var inputId = swatch.dataset.ft;   // e.g. "col_CDS"
    var color   = swatch.dataset.color;
    if (window.Shiny) {
      Shiny.setInputValue(inputId, color, {priority: "event"});
    }
    // Update selected ring visually
    var row = swatch.closest(".swatch-row");
    if (row) {
      row.querySelectorAll(".color-swatch").forEach(function(s) {
        s.classList.toggle("selected",
          s.dataset.color.toUpperCase() === color.toUpperCase());
      });
      // Close the palette after picking
      row.classList.remove("open");
      // Update the dot in the sibling ft-row
      var ftItem = row.closest(".ft-item");
      if (ftItem) {
        var dot = ftItem.querySelector(".dot");
        if (dot) dot.style.background = color;
      }
    }
  });


  // --- Plot toolbar buttons ---
  document.addEventListener("click", function(e) {
    var btn = e.target.closest(".tb-btn[data-plotly-action]");
    if (!btn) return;
    var action = btn.dataset.plotlyAction;
    var graphs = document.querySelectorAll(".plotly-graph-div, .js-plotly-plot");
    if (!graphs.length) return;
    var gd = graphs[0];

    if (action === "pan" || action === "zoom") {
      if (window.Plotly) Plotly.relayout(gd, {dragmode: action});
      // Update active state
      document.querySelectorAll(".tb-btn[data-plotly-action='pan'], .tb-btn[data-plotly-action='zoom']")
        .forEach(function(b) { b.classList.toggle("active", b === btn); });

    } else if (action === "zoomIn") {
      var xr = gd._fullLayout && gd._fullLayout.xaxis && gd._fullLayout.xaxis.range;
      if (!xr) return;
      var mid = (xr[0] + xr[1]) / 2, half = (xr[1] - xr[0]) / 4;
      if (window.Plotly) Plotly.relayout(gd, {"xaxis.range": [mid - half, mid + half]});

    } else if (action === "zoomOut") {
      var xr = gd._fullLayout && gd._fullLayout.xaxis && gd._fullLayout.xaxis.range;
      if (!xr) return;
      var mid = (xr[0] + xr[1]) / 2, half = (xr[1] - xr[0]);
      if (window.Plotly) Plotly.relayout(gd, {"xaxis.range": [mid - half, mid + half]});

    } else if (action === "resetAxes") {
      // Tell Shiny to re-render the plot at the initial view range.
      // This is more reliable than Plotly.relayout because it goes through
      // the server-side render path, avoiding client-side uirevision conflicts.
      if (window.Shiny) {
        Shiny.setInputValue("toolbar_reset", Date.now(), {priority: "event"});
      }
      // Update toolbar active state to Pan
      document.querySelectorAll(".tb-btn[data-plotly-action='pan'], .tb-btn[data-plotly-action='zoom']")
        .forEach(function(b) {
          b.classList.toggle("active", b.dataset.plotlyAction === "pan");
        });

    } else if (action === "autoscale") {
      if (window.Plotly) Plotly.relayout(gd, {"xaxis.autorange": true, "yaxis.autorange": true});
    }
  });

  // Set Pan as active by default once the plot renders
  var _tbInit = false;
  function _initToolbar() {
    if (_tbInit) return;
    var pan = document.querySelector(".tb-btn[data-plotly-action='pan']");
    if (pan) { pan.classList.add("active"); _tbInit = true; }
  }
  document.addEventListener("plotly_afterplot", function() { _initToolbar(); });
  setInterval(_initToolbar, 800);

  // --- Download SVG button ---
  document.addEventListener("click", function(e) {
    var btn = e.target.closest("#download_svg_btn");
    if (!btn) return;
    try {
      var graphs = document.querySelectorAll(".plotly-graph-div, .js-plotly-plot");
      if (!graphs.length) { alert("No plot found."); return; }
      var gd = graphs[0];
      if (window.Plotly) {
        var fnSpan = document.getElementById("svg_filename_value");
        var filename = fnSpan ? fnSpan.textContent.trim() : "gff2genbank_plot";
        Plotly.downloadImage(gd, {
          format: "svg",
          filename: filename,
          width: gd.offsetWidth || 1200,
          height: gd.offsetHeight || 500
        });
      }
    } catch(err) {
      console.error("SVG download error:", err);
    }
  });

  // --- Region input: disabled until DB ready, Enter key triggers load ---
  (function() {
    function setup() {
      var input = document.getElementById("region_input");
      if (!input) { setTimeout(setup, 100); return; }

      // Disable until db_ready_flag becomes 'true'
      input.disabled = true;
      var observer = new MutationObserver(function() {
        var flag = document.getElementById("db_ready_flag");
        if (flag && flag.textContent.trim() === "true") {
          input.disabled = false;
          observer.disconnect();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });

      // Enter key triggers Load Region button
      input.addEventListener("keydown", function(e) {
        if (e.key === "Enter") {
          e.preventDefault();
          var btn = document.getElementById("load_btn");
          if (btn && !btn.disabled) {
            // Force Shiny to register the current value before clicking,
            // since Shiny debounces text input and may not have synced yet.
            Shiny.setInputValue("region_input", input.value, {priority: "event"});
            setTimeout(function() { btn.click(); }, 50);
          }
        }
      });
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", setup);
    } else {
      setup();
    }
  })();

  // --- Force Plotly to fill its container on first render ---
  // shinywidgets locks the graph to the width measured at insert time,
  // which is often too narrow. We watch for the graph div, then call
  // Plotly.relayout with the container's actual pixel width while
  // preserving the explicit height computed by build_preview.
  (function() {
    function fixPlotWidth(graphDiv) {
      var parent = graphDiv.parentElement;
      if (!parent) return;
      var w = parent.getBoundingClientRect().width;
      if (w < 100) {
        setTimeout(function() { fixPlotWidth(graphDiv); }, 100);
        return;
      }
      if (window.Plotly) {
        // Preserve the existing height — only override width.
        var currentH = graphDiv._fullLayout ? graphDiv._fullLayout.height : null;
        var update = { width: w, autosize: false };
        if (currentH) update.height = currentH;
        Plotly.relayout(graphDiv, update);
      }
    }

    var observer = new MutationObserver(function(mutations) {
      mutations.forEach(function(m) {
        m.addedNodes.forEach(function(n) {
          if (n.nodeType !== 1) return;
          // Direct match
          if (n.classList &&
              (n.classList.contains("plotly-graph-div") ||
               n.classList.contains("js-plotly-plot"))) {
            setTimeout(function() { fixPlotWidth(n); }, 50);
          }
          // Descendant match
          var found = n.querySelectorAll ? 
              n.querySelectorAll(".plotly-graph-div, .js-plotly-plot") : [];
          found.forEach(function(el) {
            setTimeout(function() { fixPlotWidth(el); }, 50);
          });
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // Also refit width on window resize
    var resizeTimer;
    window.addEventListener("resize", function() {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        document.querySelectorAll(".plotly-graph-div, .js-plotly-plot").forEach(function(gd) {
          fixPlotWidth(gd);
        });
      }, 150);
    });
  })();

  // ── Debounce toggle switches ──────────────────────────────────────────
  // Patch Shiny.setInputValue so ft_* and var_* toggle updates are
  // held for 800ms after the last click before being sent to the server.
  (function() {
    var timers = {};
    var _origSetInputValue = null;

    function installPatch() {
      if (!window.Shiny || !Shiny.setInputValue) return;
      if (_origSetInputValue) return;  // already patched
      _origSetInputValue = Shiny.setInputValue.bind(Shiny);
      Shiny.setInputValue = function(name, value, opts) {
        if (/^(ft_|var_)/.test(name)) {
          if (timers[name]) clearTimeout(timers[name]);
          timers[name] = setTimeout(function() {
            _origSetInputValue(name, value, opts);
            delete timers[name];
          }, 800);
        } else {
          _origSetInputValue(name, value, opts);
        }
      };
    }

    // Shiny may not exist yet at script parse time — wait for it.
    if (window.Shiny && Shiny.setInputValue) {
      installPatch();
    } else {
      document.addEventListener("shiny:connected", installPatch);
    }
  })();

})();
</script>
"""



def make_ui() -> ui.Tag:
    return ui.page_fluid(
        ui.tags.head(ui.tags.style(CSS), ui.HTML(JS)),
        _app_ui(),
    )


def _app_ui() -> ui.Tag:
    return ui.div(
        ui.tags.h2(
            f"gff2genbank  {APP_VERSION}",
            style="padding:12px 0 5px; color:#1a252f; font-weight:700;",
        ),

        ui.layout_sidebar(
            ui.sidebar(

                # ── Download (top of sidebar for easy access) ───────────────
                ui.div({"class": "card"},
                    ui.tags.h5("Download"),
                    ui.tags.small("Window:", style="color:#888;"),
                    ui.output_text("window_display"),
                    ui.tags.div({"style": "display:none;", "id": "initial-view-range-out"}, ui.output_text("initial_view_range", inline=True)),
                    ui.tags.br(),
                    ui.output_ui("download_btn_ui"),
                    ui.output_ui("svg_filename_ui"),
                    ui.output_ui("download_svg_btn_ui"),
                ),

                # ── Region selection ────────────────────────────────────────
                ui.div({"class": "card"},
                    ui.tags.h5("Region"),
                    ui.input_text(
                        "region_input", None,
                        value="unc-119",
                        placeholder="gene name or chrom:start-end",
                    ),
                    ui.tags.small(
                        "Enter a gene name or coordinates (e.g. III:10900000-10910000)",
                        style="color:#888; display:block; margin-bottom:6px;",
                    ),
                    ui.input_action_button(
                        "load_btn", "Load Region",
                        class_="btn btn-primary w-100 mt-2",
                    ),
                    ui.output_ui("load_progress"),
                    ui.output_text("status_out"),
                    ui.output_text("error_out"),
                ),

                # ── Gene model toggles (CDS/UTR/intron) — always shown ───────
                ui.output_ui("gene_model_card"),

                # ── Priority group controls (hidden when no groups configured) ─
                ui.output_ui("priority_card"),

                # ── Other annotation toggles (hidden when empty) ─────────────
                ui.output_ui("other_annotations_card"),

                # ── Strand orientation ──────────────────────────────────────
                ui.div({"class": "card"},
                    ui.tags.h5("Strand"),
                    ui.tags.div({"class": "btn-group w-100", "role": "group"},
                        ui.tags.input(
                            type="radio", class_="btn-check",
                            name="strand_mode_btn", id="strand_plus",
                            autocomplete="off",
                        ),
                        ui.tags.label(
                            "+ Top", {"for": "strand_plus",
                            "class": "btn btn-outline-secondary btn-sm w-50"},
                        ),
                        ui.tags.input(
                            type="radio", class_="btn-check",
                            name="strand_mode_btn", id="strand_minus",
                            autocomplete="off",
                        ),
                        ui.tags.label(
                            "− Bottom", {"for": "strand_minus",
                            "class": "btn btn-outline-secondary btn-sm w-50"},
                        ),
                    ),
                    ui.tags.script("""
(function() {
  function syncStrand() {
    var plusBtn  = document.getElementById('strand_plus');
    var minusBtn = document.getElementById('strand_minus');
    if (!plusBtn || !minusBtn) return;

    // Init: reflect Shiny's current value
    var shinyVal = $('input[type=radio][name=strand_mode]').filter(':checked').val() || '+';
    if (shinyVal === '-') { minusBtn.checked = true; } else { plusBtn.checked = true; }

    // On click: push value back to Shiny's hidden radio input
    [plusBtn, minusBtn].forEach(function(btn) {
      btn.addEventListener('change', function() {
        var val = btn.id === 'strand_plus' ? '+' : '-';
        Shiny.setInputValue('strand_mode', val, {priority: 'event'});
      });
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncStrand);
  } else {
    syncStrand();
  }
})();
"""),
                    # Keep Shiny's real radio input but hide it
                    ui.div({"style": "display:none;"},
                        ui.input_radio_buttons(
                            "strand_mode", None,
                            choices={"+": "+", "-": "-"},
                            selected="+",
                        ),
                    ),
                ),

                # ── Custom data files ───────────────────────────────────────
                ui.div({"class": "card upload-section"},
                    ui.tags.h5("Custom Data Files"),
                    ui.tags.details(
                        ui.tags.summary("Upload your own files (optional)"),
                        ui.p("Session-only; not stored.", {"class": "upload-note"}),
                        ui.tags.b("GFF database (.db)", style="font-size:0.86em;"),
                        ui.p("Build with build_db.py", {"class": "upload-note"}),
                        ui.input_file("upload_db", None, accept=[".db"]),
                        ui.tags.b("FASTA + index (.fa and .fa.fai)",
                                  style="font-size:0.86em;"),
                        ui.p("samtools faidx your.fa", {"class": "upload-note"}),
                        ui.input_file("upload_fa",  None, accept=[".fa",".fasta"]),
                        ui.input_file("upload_fai", None, accept=[".fai"]),
                        ui.input_action_button(
                            "apply_uploads", "Apply uploaded files",
                            class_="btn btn-secondary w-100 mt-2",
                        ),
                        ui.output_text("upload_status"),
                    ),
                    ui.tags.hr(style="margin:8px 0;"),
                    ui.download_button(
                        "download_build_script", "Download build_db.py",
                        class_="btn btn-outline-secondary w-100",
                    ),
                    ui.download_button(
                        "download_prepare_script", "Download prepare_gff.py",
                        class_="btn btn-outline-secondary w-100 mt-1",
                    ),
                ),

                width=290,
            ),

            # ── Main panel ──────────────────────────────────────────────────
            # Loading info card — visible while DB loads, gone once first
            # region renders. Plain output_ui, no async tricks needed.
            # db_ready_flag is a hidden text output ("true"/"false") that
            # drives panel_conditional — standard Shiny pattern for a loading screen.
            ui.output_text("db_ready_flag", inline=True),

            ui.panel_conditional(
                "output.db_ready_flag !== 'true'",
                ui.div(
                    {"class": "card", "style": "padding: 40px 36px; text-align: center;"},
                    ui.tags.div(
                        {"class": "spinner-border text-primary",
                         "role": "status",
                         "style": "width: 2.5rem; height: 2.5rem; margin-bottom: 18px;"},
                        ui.tags.span({"class": "visually-hidden"}, "Loading..."),
                    ),
                    ui.tags.h4(
                        "Loading database…",
                        style="color: #1a252f; font-weight: 700; margin-bottom: 8px;",
                    ),
                    ui.output_text("startup_status"),
                    ui.tags.p(
                        ui.tags.b("Database: "),
                        ui.tags.code(DB_PATH.name),
                        ui.tags.br(),
                        ui.tags.b("FASTA: "),
                        ui.tags.code(FASTA_PATH.name),
                        style="color: #555; font-size: 0.85em; margin-top: 14px; margin-bottom: 4px;",
                    ),
                    ui.tags.p(
                        "Custom databases can be loaded using the sidebar.",
                        style="color: #aaa; font-size: 0.82em; margin-top: 4px;",
                    ),
                    ui.tags.p(
                        "This takes about 30 seconds on first load. The app will appear automatically.",
                        style="color: #aaa; font-size: 0.82em; margin-top: 12px;",
                    ),
                ),
            ),

            ui.panel_conditional(
                "output.db_ready_flag === 'true'",
                ui.output_ui("loading_card"),
                ui.div({"class": "card"},
                    ui.div(
                        {"id": "plot-toolbar"},
                        # Pan / Zoom mode toggle
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2v-4M9 21H5a2 2 0 0 1-2-2v-4m0 0h18"/></svg>'),
                            " Pan",
                            **{"class": "tb-btn", "data-plotly-action": "pan", "title": "Pan (drag to scroll)"},
                        ),
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35M11 8v6M8 11h6"/></svg>'),
                            " Set Region",
                            **{"class": "tb-btn", "data-plotly-action": "zoom", "title": "Drag to set visible region"},
                        ),
                        ui.div({"class": "tb-sep"}),
                        # Zoom in / out
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35M11 8v6M8 11h6"/></svg>'),
                            " Zoom In",
                            **{"class": "tb-btn", "data-plotly-action": "zoomIn", "title": "Zoom in 2×"},
                        ),
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35M8 11h6"/></svg>'),
                            " Zoom Out",
                            **{"class": "tb-btn", "data-plotly-action": "zoomOut", "title": "Zoom out 2×"},
                        ),
                        ui.div({"class": "tb-sep"}),
                        # Reset / Autoscale
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>'),
                            " Reset",
                            **{"class": "tb-btn", "data-plotly-action": "resetAxes", "title": "Reset to full loaded region"},
                        ),
                        ui.tags.button(
                            ui.HTML('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v5h5M21 3v5h-5M3 21v-5h5M21 21v-5h-5"/></svg>'),
                            " Full Region",
                            **{"class": "tb-btn", "data-plotly-action": "autoscale", "title": "Show full loaded region"},
                        ),
                    ),
                    output_widget("preview_plot"),
                ),
                ui.tags.style(".modebar { display: none !important; }"),

            ),
        ),
    )


def _safe_id(name: str) -> str:
    """Convert a group name to a safe Shiny input ID (alphanumeric + underscores)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


import re
