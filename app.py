"""
app.py
------
Entry point for the GFF -> GenBank app.

This file is intentionally minimal — all logic lives in the other modules:
  config.py     — paths, colours, defaults
  data.py       — GFF/FASTA loading and querying
  plot.py       — Plotly preview figure builder
  genbank.py    — GenBank file serialiser
  ui.py         — Shiny UI layout
  server.py     — Shiny server function

Run with:
    shiny run app.py
Or with a custom data directory:
    GFF_APP_DATA_DIR=/path/to/data shiny run app.py
"""

from shiny import App
from ui import make_ui
from server import server

app = App(make_ui(), server)
