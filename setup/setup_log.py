"""
setup_log.py
------------
Shared utility for reading and writing setup_log.json in DATA_DIR.

Log file structure:
{
    "step_id": {
        "status":    "ok" | "warning" | "failed" | "skipped",
        "timestamp": "2026-05-01T14:23:11",
        "message":   "optional human-readable summary",
        "warnings":  ["list of warning strings"],  # optional
        "output":    "path/to/output/file"          # optional
    },
    ...
}
"""

import json
from datetime import datetime
from pathlib import Path

LOG_FILENAME = "setup_log.json"


def _log_path(data_dir: Path) -> Path:
    return data_dir / LOG_FILENAME


def read_log(data_dir: Path) -> dict:
    """Return the full log dict, or {} if it doesn't exist yet."""
    p = _log_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"[setup_log] WARNING: could not read {p}: {e}")
        return {}


def write_step(
    data_dir: Path,
    step_id: str,
    status: str,
    message: str = "",
    warnings: list[str] | None = None,
    output: str | Path | None = None,
) -> None:
    """
    Write or update a single step entry in setup_log.json.
    status: 'ok' | 'warning' | 'failed' | 'skipped'
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    log = read_log(data_dir)
    entry: dict = {
        "status":    status,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if message:
        entry["message"] = message
    if warnings:
        entry["warnings"] = warnings
    if output is not None:
        entry["output"] = str(output)
    log[step_id] = entry
    _log_path(data_dir).write_text(json.dumps(log, indent=2))


def print_summary(data_dir: Path) -> None:
    """Print a human-readable summary of all logged steps."""
    log = read_log(data_dir)
    if not log:
        print("  (no steps logged yet)")
        return
    status_icons = {"ok": "✓", "warning": "⚠", "failed": "✗", "skipped": "–"}
    for step_id, entry in log.items():
        icon    = status_icons.get(entry.get("status", "?"), "?")
        ts      = entry.get("timestamp", "")
        msg     = entry.get("message", "")
        out     = entry.get("output", "")
        summary = msg or out or ""
        print(f"  {icon} {step_id:<25} {ts}  {summary}")
        for w in entry.get("warnings", []):
            print(f"      ⚠ {w}")


def step_status(data_dir: Path, step_id: str) -> str | None:
    """Return the status string for a step, or None if not yet run."""
    return read_log(data_dir).get(step_id, {}).get("status")
