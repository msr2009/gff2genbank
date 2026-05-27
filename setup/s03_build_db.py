"""
s03_build_db.py
---------------
Interactive wrapper around build_db.py.

Guides the user through:
  1. Confirming the prepared GFF path (from session or prompt)
  2. Confirming the output DB path
  3. Running build_db.py
  4. Reporting build time

Usage (standalone):
    python setup/s03_build_db.py

Usage (via orchestrator):
    from setup.s03_build_db import run
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
import time
from pathlib import Path


STEP_ID = "s03_build_db"


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _prompt_yes(msg: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = _prompt(msg + suffix).lower()
    if not raw:
        return default
    return raw.startswith("y")


def _run_interactive(session: dict) -> tuple[bool, list[str], dict]:
    print("\n" + "=" * 60)
    print("STEP 3: BUILD GFFUTILS DATABASE")
    print("=" * 60)

    warnings: list[str] = []

    # ── GFF input ─────────────────────────────────────────────────────────
    default_gff = session.get("prepared_gff", "")
    gff_raw = _prompt("\nPrepared GFF file path", default=default_gff)
    gff = Path(gff_raw).expanduser()
    if not gff.exists():
        print(f"  File not found: {gff}")
        return False, ["GFF file not found."], session

    # ── Output DB path ─────────────────────────────────────────────────────
    default_db = str(Path(session.get("data_dir", ".")) / (gff.name + ".db"))
    db_raw  = _prompt("Output database path", default=default_db)
    db_path = Path(db_raw).expanduser()

    if db_path.exists():
        if not _prompt_yes(f"\n  {db_path.name} already exists. Overwrite?", default=False):
            print("  Skipping database build.")
            session["db_path"] = str(db_path)
            return True, ["Database already existed — not rebuilt."], session

    # ── Run ───────────────────────────────────────────────────────────────
    build_script = Path(__file__).parent.parent / "build_db.py"
    cmd = [sys.executable, str(build_script), str(gff), "--out", str(db_path)]

    print("\nCommand:")
    print("  " + " ".join(cmd))
    if not _prompt_yes("\nProceed?", default=True):
        return False, ["User cancelled."], session

    print("\n  Building database — this may take several minutes for large GFFs...")
    t0 = time.perf_counter()
    result = subprocess.run(cmd)
    elapsed = time.perf_counter() - t0

    if result.returncode != 0:
        return False, [f"build_db.py exited with code {result.returncode}."], session

    mins, secs = divmod(int(elapsed), 60)
    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    print(f"\n  Database built in {time_str}: {db_path}")
    session["db_path"] = str(db_path)
    return True, warnings, session


def run(session: dict) -> dict:
    """Orchestrator entry point."""
    from setup_log import write_step
    data_dir = Path(session["data_dir"])
    ok, warnings, session = _run_interactive(session)
    status = "ok" if ok and not warnings else ("warning" if ok else "failed")
    write_step(
        data_dir, STEP_ID, status,
        message=f"Database: {session.get('db_path', 'n/a')}",
        warnings=warnings,
        output=session.get("db_path"),
    )
    return session


def main() -> None:
    session = {"data_dir": "."}
    run(session)


if __name__ == "__main__":
    main()
