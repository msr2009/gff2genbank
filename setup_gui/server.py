"""
setup_gui/server.py
-------------------
Top-level server for the standalone setup app.

Holds a single `app_session` reactive dict (mirroring the CLI orchestrator's
`session` dict — see setup/setup.py) that each step module reads/writes, so
later steps can consume what earlier ones produced (e.g. Step 3 reads the
`prepared_gff` Step 2 publishes).
"""

import sys
from pathlib import Path

from shiny import reactive

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config  # noqa: E402
from .step_deps import deps_server  # noqa: E402
from .step_prepare import prepare_gff_server  # noqa: E402
from .step_build_db import build_db_server  # noqa: E402
from .step_priority import priority_server  # noqa: E402
from .step_validate import validate_server  # noqa: E402


def setup_server(input, output, session):
    app_session = reactive.Value({"data_dir": str(config.DATA_DIR)})

    deps_server("deps", app_session)            # Step 1 — Dependencies
    prepare_gff_server("prep", app_session)     # Step 2 — Prepare GFF
    build_db_server("build", app_session)       # Step 3 — Build database
    priority_server("prio", app_session)        # Step 4 — Priority groups (sketch)
    validate_server("validate", app_session)    # Step 5 — Validate
