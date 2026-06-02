"""
setup.py
--------
Top-level orchestrator for the gff2genbank app setup pipeline.

Runs the following steps in order, with the ability to skip completed
steps or jump directly to any individual step:

  s01  Check dependencies
  s02  Prepare GFF file(s)
  s03  Build gffutils database
  s04  Configure priority feature groups
  s05  Validate setup

State is tracked in DATA_DIR/setup_log.json so re-runs know what's done.

Usage:
    python setup/setup.py
    python setup/setup.py --data-dir /path/to/data
    python setup/setup.py --step s04   # jump directly to a step
"""

import argparse
import importlib
import sys
from pathlib import Path

# Always put the setup/ directory first on sys.path so that
# `import setup_log`, `import s01_check_deps` etc. resolve correctly
# regardless of where the script is launched from.
_SETUP_DIR   = Path(__file__).parent
_PROJECT_ROOT = _SETUP_DIR.parent
sys.path.insert(0, str(_SETUP_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))


STEPS = [
    ("s01_check_deps",      "Check dependencies",        "s01_check_deps"),
    ("s02_prepare_gff",     "Prepare GFF file(s)",       "s02_prepare_gff"),
    ("s03_build_db",        "Build gffutils database",   "s03_build_db"),
    ("s04_priority_groups", "Configure priority groups", "s04_priority_groups"),
    ("s05_validate",        "Validate setup",            "s05_validate"),
]

STATUS_ICONS = {
    "ok":      "✓",
    "warning": "⚠",
    "failed":  "✗",
    "skipped": "–",
}


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print("\n\n  Interrupted. Returning to menu.")
        return default


def _prompt_yes(msg: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        raw = input(f"{msg}{suffix}: ").strip().lower()
        if not raw:
            return default
        return raw.startswith("y")
    except (EOFError, KeyboardInterrupt):
        print("\n\n  Interrupted. Returning to menu.")
        return False


# ---------------------------------------------------------------------------
# Session initialisation
# ---------------------------------------------------------------------------

def _init_session(data_dir: Path) -> dict:
    """Build starting session dict, pulling known paths from config if available."""
    session: dict = {"data_dir": str(data_dir)}
    try:
        import config
        session.setdefault("db_path",      str(config.DB_PATH))
        session.setdefault("fasta",        str(config.FASTA_PATH))
        session.setdefault("prepared_gff", str(config.GFF_PATH))
    except Exception:
        pass
    return session


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def _print_status(data_dir: Path) -> None:
    from setup_log import read_log
    log = read_log(data_dir)
    print()
    print("  Pipeline status:")
    for step_id, label, _ in STEPS:
        entry  = log.get(step_id, {})
        status = entry.get("status", "not run")
        icon   = STATUS_ICONS.get(status, "?")
        ts     = entry.get("timestamp", "")
        ts_str = f"  ({ts})" if ts else ""
        print(f"    {icon}  {step_id:<25}  {label:<32}{ts_str}")
        for w in entry.get("warnings", []):
            print(f"           ⚠ {w}")
    print()


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

def _run_step(step_id: str, module_name: str, session: dict) -> dict:
    """Import the step module and call run(session)."""
    try:
        mod = importlib.import_module(module_name)
        session = mod.run(session)
    except KeyboardInterrupt:
        print(f"\n\n  {step_id} interrupted — returning to menu.")
        from setup_log import write_step
        write_step(Path(session["data_dir"]), step_id, "failed",
                   message="Interrupted by user.")
    except Exception as e:
        print(f"\n  ERROR in {step_id}: {e}")
        from setup_log import write_step
        write_step(Path(session["data_dir"]), step_id, "failed",
                   message=str(e))
    return session


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu(data_dir: Path, start_step: str | None = None) -> None:
    session = _init_session(data_dir)

    if start_step:
        matches = [(sid, lbl, mod) for sid, lbl, mod in STEPS if sid == start_step]
        if not matches:
            print(f"Unknown step: {start_step}. Valid: {[s for s,_,_ in STEPS]}")
            sys.exit(1)
        sid, _, mod = matches[0]
        _run_step(sid, mod, session)
        return

    while True:
        try:
            print("\n" + "=" * 60)
            print("gff2genbank APP — SETUP")
            print("=" * 60)
            _print_status(data_dir)
            print("  Options:")
            for i, (step_id, label, _) in enumerate(STEPS, 1):
                print(f"    {i}. {step_id}: {label}")
            print(f"    a. Run all remaining steps in order")
            print(f"    q. Quit")

            choice = _prompt("\nChoice").strip().lower()

        except KeyboardInterrupt:
            print("\n\n  Use 'q' to quit.")
            continue

        if choice == "q":
            print("Exiting setup.")
            break

        elif choice == "a":
            from setup_log import step_status
            for step_id, label, module_name in STEPS:
                status = step_status(data_dir, step_id)
                if status in ("ok", "warning"):
                    if not _prompt_yes(
                        f"\n  {step_id} already completed ({status}). Re-run?",
                        default=False,
                    ):
                        print(f"  Skipping {step_id}.")
                        continue
                session = _run_step(step_id, module_name, session)
                from setup_log import step_status as ss
                if ss(data_dir, step_id) == "failed":
                    print(f"\n  {step_id} failed — stopping. Fix the issue and re-run.")
                    break

        elif choice.isdigit() and 1 <= int(choice) <= len(STEPS):
            idx = int(choice) - 1
            step_id, _, module_name = STEPS[idx]
            session = _run_step(step_id, module_name, session)

        else:
            print("  Invalid choice.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="gff2genbank app setup orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Path to data directory (default: read from config.py DATA_DIR)",
    )
    parser.add_argument(
        "--step", default=None,
        help="Run a single step directly (e.g. --step s04)",
    )
    args = parser.parse_args()

    # Resolve data_dir
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser()
    else:
        try:
            import config
            data_dir = config.DATA_DIR
        except Exception:
            data_dir = Path(".")
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nData directory: {data_dir.resolve()}")

    main_menu(data_dir, start_step=args.step)


if __name__ == "__main__":
    main()
