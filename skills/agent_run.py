"""Helpers for multi-agent run directory and report persistence."""

from __future__ import annotations

import json
import re
from pathlib import Path


def safe_name(name):
    """Return a filesystem-safe file name."""
    text = "" if name is None else str(name)
    safe = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_", ".", " "}:
            safe.append(ch)
        else:
            safe.append("_")
    text = "".join(safe).strip().strip(".")
    return text or "run"


def run_root(card_folder):
    """Return the .agent_runs directory under the card folder."""
    return Path(card_folder) / ".agent_runs"


def _existing_round_numbers(run_dir):
    pattern = re.compile(r"^round-(\d{6})$")
    numbers = []
    for child in run_dir.iterdir() if run_dir.exists() else []:
        match = pattern.match(child.name)
        if match and child.is_dir():
            try:
                numbers.append(int(match.group(1)))
            except ValueError:
                continue
    return sorted(numbers)


def create_run_dir(card_folder, turn_index=None):
    """Create and return a run directory for the given turn index."""
    root = run_root(card_folder)
    root.mkdir(parents=True, exist_ok=True)

    if turn_index is None:
        if root.joinpath("current").exists():
            current = current_run_dir(card_folder)
            if current is not None:
                name = current.name
                if name.startswith("round-"):
                    try:
                        turn_index = int(name.replace("round-", ""), 10)
                    except ValueError:
                        turn_index = None
        if turn_index is None:
            numbers = _existing_round_numbers(root)
            turn_index = numbers[-1] if numbers else 0

    turn_index = int(turn_index or 0)
    run_name = f"round-{turn_index + 1:06d}"
    run_dir = root / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    run_root(card_folder).joinpath("current").write_text(str(run_dir.resolve()), encoding="utf-8")
    return run_dir


def current_run_dir(card_folder):
    """Return the current run directory recorded by .agent_runs/current."""
    root = run_root(card_folder)
    current_file = root / "current"
    if not current_file.exists():
        return None

    raw = current_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if path.exists():
        return path
    return None


def write_json(path, data):
    """Write JSON data as UTF-8 with stable formatting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path, default=None):
    """Read JSON data from a path; return default on missing or invalid files."""
    path = Path(path)
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_text(path, text):
    """Write plain text using UTF-8."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text), encoding="utf-8")


def read_current_critic_report(card_folder):
    """Read critic.report.json from the current run directory."""
    run_dir = current_run_dir(card_folder)
    if run_dir is None:
        return {}
    return read_json(run_dir / "critic.report.json", {})
