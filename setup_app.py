"""
setup_app.py
------------
Entry point for the LOCAL gff2genbank setup GUI — a friendly front-end over the
data-preparation pipeline (prepare_gff.py / build_db.py / setup/).

Run locally (it reads large data files in place, so it is not deployed):
    pip install -r requirements.txt -r requirements-setup.txt
    shiny run setup_app.py

Optionally point it at a different data directory:
    GFF_APP_DATA_DIR=/path/to/data shiny run setup_app.py

This is separate from the browsing app (app.py) on purpose: setup is a local,
occasional task; browsing is the deployed runtime app.
"""

from shiny import App
from setup_gui.ui import make_setup_ui
from setup_gui.server import setup_server

app = App(make_setup_ui(), setup_server)
