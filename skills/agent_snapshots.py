"""Snapshot and rollback helpers for card-scoped RP runtime state."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_root(card_folder: str | Path) -> Path:
    return Path(card_folder) / ".agent_runs" / "snapshots"


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip("-") or "snapshot"


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
    path = Path(snapshot_id)
    return path.name == snapshot_id and not path.is_absolute()


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

    snapshot_dir = _snapshot_root(card) / str(snapshot_id)
    metadata_path = snapshot_dir / "snapshot.json"
    if not snapshot_dir.is_dir() or not metadata_path.is_file():
        return {"ok": False, "reason": "snapshot_missing", "snapshot_id": str(snapshot_id)}

    metadata = _read_json(metadata_path)
    copied = metadata.get("copied", [])
    if not isinstance(copied, list):
        copied = []

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
    }
