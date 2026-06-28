"""Backup and rollback helpers for card-scoped RP save state."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import objective_world
import actor_memory_store

EXCLUDED_BACKUP_ROOT_ITEMS = {"debug", ".agent_runs", "backup"}
BACKUP_METADATA_FILENAME = "backup.json"

BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
ROUND_DIR_RE = re.compile(r"^round-[0-9]{6}(?:-replay-[0-9]{3})?$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _backup_root(card_folder: str | Path) -> Path:
    return Path(card_folder) / "backup"


def _safe_component(value: str) -> str:
    safe = []
    for ch in value:
        if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in {"-", "_", "."}:
            safe.append(ch)
        else:
            safe.append("-")
    return "".join(safe).strip("-") or "backup"


def _new_backup_id(root: Path, round_id: str) -> str:
    safe_round = _safe_component(str(round_id))
    for _ in range(100):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_id = f"{safe_round}-{stamp}-{uuid.uuid4().hex[:12]}"
        if not (root / backup_id).exists():
            return backup_id
    raise RuntimeError("unable to allocate unique backup id")


def _copy_item(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=_copytree_ignore, dirs_exist_ok=target.exists())
    else:
        shutil.copy2(source, target)


def _remove_existing(path: Path) -> None:
    if path.is_dir():
        if path.name == "generated":
            _remove_generated_tree_for_restore(path)
        else:
            shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _copytree_ignore(directory: str, names: list[str]) -> set[str]:
    current = Path(directory)
    if current.name == "jobs" and current.parent.name == "generated":
        return {name for name in names if _is_transient_image_worker_log(current / name)}
    return set()


def _is_transient_image_worker_log(path: Path) -> bool:
    return (
        path.name.startswith("image-job-")
        and path.suffix == ".log"
        and path.parent.name == "jobs"
        and path.parent.parent.name == "generated"
    )


def _contains_only_transient_image_worker_logs(path: Path) -> bool:
    if not path.exists():
        return True
    if path.is_file():
        return _is_transient_image_worker_log(path)
    for item in path.rglob("*"):
        if item.is_file() and not _is_transient_image_worker_log(item):
            return False
    return True


def _remove_generated_tree_for_restore(path: Path) -> None:
    for item in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.is_file():
            if _is_transient_image_worker_log(item):
                continue
            item.unlink()
            continue
        if item.is_dir():
            try:
                item.rmdir()
            except OSError:
                if _contains_only_transient_image_worker_logs(item):
                    continue
                raise
    try:
        path.rmdir()
    except OSError:
        if not _contains_only_transient_image_worker_logs(path):
            raise


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _is_direct_backup_id(backup_id: str) -> bool:
    return bool(BACKUP_ID_RE.fullmatch(backup_id))


def _read_agent_runs_current(card: Path) -> str:
    current_file = card / ".agent_runs" / "current"
    if not current_file.is_file():
        return ""
    try:
        return current_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _current_raw_points_to(card: Path, raw: str, target: Path) -> bool:
    if not raw:
        return False
    current_path = Path(raw)
    if not current_path.is_absolute():
        current_path = (card / ".agent_runs" / current_path).resolve()
    else:
        current_path = current_path.resolve()
    return current_path == target


def _remove_failed_round_dir(card: Path, metadata: Dict[str, Any]) -> list[str]:
    round_id = metadata.get("round_id")
    if not isinstance(round_id, str) or not ROUND_DIR_RE.fullmatch(round_id):
        return []

    run_root = (card / ".agent_runs").resolve()
    target = (card / ".agent_runs" / round_id).resolve()
    if target == run_root or run_root not in target.parents:
        return []
    if _current_raw_points_to(card, str(metadata.get("agent_runs_current") or ""), target):
        return []
    if not target.exists():
        return []

    _remove_existing(target)
    return [f".agent_runs/{round_id}"]


def _restore_agent_runs_current(card: Path, metadata: Dict[str, Any]) -> list[str]:
    current_file = card / ".agent_runs" / "current"
    raw = str(metadata.get("agent_runs_current") or "").strip()
    if raw:
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(raw, encoding="utf-8")
        return [".agent_runs/current"]
    if current_file.exists():
        current_file.unlink()
        return []
    return []


def _is_backup_candidate(path: Path) -> bool:
    return path.name not in EXCLUDED_BACKUP_ROOT_ITEMS


def _is_safe_backup_item(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not value or value in {".", ".."}:
        return False
    if value in EXCLUDED_BACKUP_ROOT_ITEMS:
        return False
    path = Path(value)
    if path.is_absolute():
        return False
    return len(path.parts) == 1


def _backup_items(card: Path) -> list[Path]:
    return sorted(
        (path for path in card.iterdir() if _is_backup_candidate(path)),
        key=lambda item: item.name,
    )


def create_snapshot(card_folder: str | Path, round_id: str, *, reason: str) -> Dict[str, Any]:
    card = Path(card_folder)
    actor_memory_store.cleanup_stale_player_placeholder_dirs(card)
    root = _backup_root(card)
    root.mkdir(parents=True, exist_ok=True)
    backup_id = _new_backup_id(root, round_id)
    backup_dir = root / backup_id
    backup_dir.mkdir()

    copied = []
    for source in _backup_items(card):
        _copy_item(source, backup_dir / source.name)
        copied.append(source.name)

    metadata = {
        "backup_id": backup_id,
        "snapshot_id": backup_id,
        "round_id": str(round_id),
        "reason": reason,
        "created_at": _utc_now(),
        "copied": copied,
        "excluded": sorted(EXCLUDED_BACKUP_ROOT_ITEMS),
        "agent_runs_current": _read_agent_runs_current(card),
        "objective_world_included": (card / objective_world.OBJECTIVE_WORLD_REL_PATH).is_file(),
    }
    _write_json(backup_dir / BACKUP_METADATA_FILENAME, metadata)

    return {
        "ok": True,
        "backup_id": backup_id,
        "backup_dir": str(backup_dir),
        "snapshot_id": backup_id,
        "snapshot_dir": str(backup_dir),
        "round_id": str(round_id),
        "reason": reason,
        "copied": copied,
    }


def restore_snapshot(card_folder: str | Path, snapshot_id: str, *, mode: str) -> Dict[str, Any]:
    card = Path(card_folder)
    backup_id = str(snapshot_id)
    if not _is_direct_backup_id(backup_id):
        return {"ok": False, "reason": "snapshot_missing", "backup_id": backup_id, "snapshot_id": backup_id}

    backup_root = _backup_root(card).resolve()
    backup_dir = backup_root / backup_id
    resolved_backup_dir = backup_dir.resolve()
    if resolved_backup_dir == backup_root or backup_root not in resolved_backup_dir.parents:
        return {"ok": False, "reason": "snapshot_missing", "backup_id": backup_id, "snapshot_id": backup_id}

    metadata_path = backup_dir / BACKUP_METADATA_FILENAME
    if not backup_dir.is_dir() or not metadata_path.is_file():
        return {"ok": False, "reason": "snapshot_missing", "backup_id": backup_id, "snapshot_id": backup_id}

    metadata = _read_json(metadata_path)
    copied = metadata.get("copied", [])
    if not isinstance(copied, list):
        copied = []

    removed = _remove_failed_round_dir(card, metadata)
    restored = []
    copied_set = {rel for rel in copied if _is_safe_backup_item(rel)}
    current_items = [item for item in _backup_items(card)]
    for item in current_items:
        _remove_existing(item)
        if item.name not in copied_set:
            removed.append(item.name)
    for rel in sorted(copied_set):
        source = backup_dir / rel
        if not source.exists():
            continue
        target = card / rel
        _remove_existing(target)
        _copy_item(source, target)
        restored.append(rel)
    restored.extend(_restore_agent_runs_current(card, metadata))
    stale_cleanup = actor_memory_store.cleanup_stale_player_placeholder_dirs(card)
    removed.extend(stale_cleanup.get("removed", []))

    return {
        "ok": True,
        "backup_id": backup_id,
        "backup_dir": str(backup_dir),
        "snapshot_id": backup_id,
        "snapshot_dir": str(backup_dir),
        "mode": mode,
        "round_id": metadata.get("round_id"),
        "reason": metadata.get("reason"),
        "restored": restored,
        "removed": removed,
    }
