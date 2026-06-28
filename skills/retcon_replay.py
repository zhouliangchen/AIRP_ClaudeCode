"""Deprecated compatibility shim for legacy retcon replay helpers.

Replay execution now enters through capability-driven ``replay.plan`` sessions.
These legacy helpers intentionally do not inspect or mutate card state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_replay_from_current_run(
    card_folder: str | Path,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    return {"ok": True, "action": "not_required", "deprecated": True}


def active_constraint_for_pending(
    card_folder: str | Path,
    pending: dict[str, Any] | None,
) -> dict[str, Any]:
    return {}


def advance_after_delivery(card_folder: str | Path) -> dict[str, Any]:
    return {"ok": True, "action": "not_required", "deprecated": True}
