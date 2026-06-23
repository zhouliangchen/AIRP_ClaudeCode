"""Snapshot and rollback helpers for card-scoped RP runtime state."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import objective_world

SNAPSHOT_ITEMS = [
    "chat_log.json",
    ".card_data.json",
    ".initvar.json",
    "ui_manifest.json",
    ".beautify_template.html",
    ".beautify.json",
    ".regex_scripts.json",
    "memory",
    ".agent_runs/current",
]

SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
ROUND_DIR_RE = re.compile(r"^round-[0-9]{6}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_root(card_folder: str | Path) -> Path:
    return Path(card_folder) / ".agent_runs" / "snapshots"


def _safe_component(value: str) -> str:
    safe = []
    for ch in value:
        if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in {"-", "_", "."}:
            safe.append(ch)
        else:
            safe.append("-")
    return "".join(safe).strip("-") or "snapshot"


def _new_snapshot_id(root: Path, round_id: str) -> str:
    safe_round = _safe_component(str(round_id))
    for _ in range(100):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_id = f"{safe_round}-{stamp}-{uuid.uuid4().hex[:12]}"
        if not (root / snapshot_id).exists():
            return snapshot_id
    raise RuntimeError("unable to allocate unique snapshot id")


def _copy_item(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _remove_existing(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _is_direct_snapshot_id(snapshot_id: str) -> bool:
    return bool(SNAPSHOT_ID_RE.fullmatch(snapshot_id))


def _snapshot_current_points_to(snapshot_dir: Path, target: Path) -> bool:
    current_file = snapshot_dir / ".agent_runs" / "current"
    if not current_file.is_file():
        return False
    raw = current_file.read_text(encoding="utf-8").strip()
    if not raw:
        return False
    current_path = Path(raw)
    if not current_path.is_absolute():
        current_path = (snapshot_dir / ".agent_runs" / current_path).resolve()
    else:
        current_path = current_path.resolve()
    return current_path == target


def _remove_failed_round_dir(card: Path, snapshot_dir: Path, metadata: Dict[str, Any]) -> list[str]:
    round_id = metadata.get("round_id")
    if not isinstance(round_id, str) or not ROUND_DIR_RE.fullmatch(round_id):
        return []

    run_root = (card / ".agent_runs").resolve()
    target = (card / ".agent_runs" / round_id).resolve()
    if target == run_root or run_root not in target.parents:
        return []
    if _snapshot_current_points_to(snapshot_dir, target):
        return []
    if not target.exists():
        return []

    _remove_existing(target)
    return [f".agent_runs/{round_id}"]


def create_snapshot(card_folder: str | Path, round_id: str, *, reason: str) -> Dict[str, Any]:
    card = Path(card_folder)
    root = _snapshot_root(card)
    root.mkdir(parents=True, exist_ok=True)
    snapshot_id = _new_snapshot_id(root, round_id)
    snapshot_dir = root / snapshot_id
    snapshot_dir.mkdir()

    copied = []
    for rel in SNAPSHOT_ITEMS:
        source = card / rel
        if not source.exists():
            continue
        _copy_item(source, snapshot_dir / rel)
        copied.append(rel)

    metadata = {
        "snapshot_id": snapshot_id,
        "round_id": str(round_id),
        "reason": reason,
        "created_at": _utc_now(),
        "copied": copied,
        "objective_world_included": (card / objective_world.OBJECTIVE_WORLD_REL_PATH).is_file(),
    }
    _write_json(snapshot_dir / "snapshot.json", metadata)

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "round_id": str(round_id),
        "reason": reason,
        "copied": copied,
    }


def restore_snapshot(card_folder: str | Path, snapshot_id: str, *, mode: str) -> Dict[str, Any]:
    card = Path(card_folder)
    if not _is_direct_snapshot_id(str(snapshot_id)):
        return {"ok": False, "reason": "snapshot_missing", "snapshot_id": str(snapshot_id)}

    snapshot_root = _snapshot_root(card).resolve()
    snapshot_dir = snapshot_root / str(snapshot_id)
    resolved_snapshot_dir = snapshot_dir.resolve()
    if resolved_snapshot_dir == snapshot_root or snapshot_root not in resolved_snapshot_dir.parents:
        return {"ok": False, "reason": "snapshot_missing", "snapshot_id": str(snapshot_id)}

    metadata_path = snapshot_dir / "snapshot.json"
    if not snapshot_dir.is_dir() or not metadata_path.is_file():
        return {"ok": False, "reason": "snapshot_missing", "snapshot_id": str(snapshot_id)}

    metadata = _read_json(metadata_path)
    copied = metadata.get("copied", [])
    if not isinstance(copied, list):
        copied = []

    removed = _remove_failed_round_dir(card, snapshot_dir, metadata)
    restored = []
    for rel in copied:
        if rel not in SNAPSHOT_ITEMS:
            continue
        source = snapshot_dir / rel
        if not source.exists():
            continue
        target = card / rel
        _remove_existing(target)
        _copy_item(source, target)
        restored.append(rel)

    return {
        "ok": True,
        "snapshot_id": str(snapshot_id),
        "mode": mode,
        "round_id": metadata.get("round_id"),
        "reason": metadata.get("reason"),
        "restored": restored,
        "removed": removed,
    }
