"""Safe ingestion of memory deltas produced by RP subagents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_run


CST = timezone(timedelta(hours=8))

ACTOR_ALLOWED_SOURCES = {"perceived", "observed", "self", "dialogue"}
ACTOR_FORBIDDEN_MARKERS = {"gm_only", "omniscient", "world_truth", "gm_notes"}


class MemoryIngestionError(RuntimeError):
    """Raised when a subagent memory delta would leak hidden knowledge."""


def _safe_name(name: str) -> str:
    text = "" if name is None else str(name)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", text.strip())
    return safe.strip().strip(".") or "_unknown"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_lines(path: Path, header: str, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else header.rstrip() + "\n"
    addition = "\n".join(line for line in lines if line)
    if not addition:
        return
    path.write_text(existing.rstrip() + "\n" + addition + "\n", encoding="utf-8")


def _ledger_path(card: Path) -> Path:
    return card / "memory" / ".agent_memory_ingested.json"


def _load_ledger(card: Path) -> set[str]:
    data = _read_json(_ledger_path(card), {"entries": []})
    entries = data.get("entries", []) if isinstance(data, dict) else []
    return {str(item) for item in entries}


def _save_ledger(card: Path, entries: set[str]) -> None:
    _write_json(_ledger_path(card), {"entries": sorted(entries)})


def _text_from_delta(item: Any, path: str) -> str:
    if isinstance(item, str):
        text = item.strip()
    elif isinstance(item, dict):
        text = str(item.get("text") or item.get("fact") or "").strip()
    else:
        text = ""
    if not text:
        raise MemoryIngestionError(f"{path}: memory delta text is required")
    return text


def _validate_actor_delta(item: Any, path: str) -> str:
    if isinstance(item, dict):
        for key, value in item.items():
            marker = str(key).lower()
            if marker in ACTOR_FORBIDDEN_MARKERS:
                raise MemoryIngestionError(f"{path}.{key}: forbidden actor memory field")
            if isinstance(value, str) and value.lower() in ACTOR_FORBIDDEN_MARKERS:
                raise MemoryIngestionError(f"{path}.{key}: forbidden actor memory source {value}")
        source = str(item.get("source", "perceived")).lower()
        if source not in ACTOR_ALLOWED_SOURCES:
            raise MemoryIngestionError(f"{path}.source: actor memory source {source} is not allowed")
    return _text_from_delta(item, path)


def _world_text(item: Any, path: str) -> str:
    if isinstance(item, dict):
        scope = str(item.get("scope") or "world").strip()
        fact = str(item.get("fact") or item.get("text") or "").strip()
        if not fact:
            raise MemoryIngestionError(f"{path}.fact: world memory fact is required")
        return f"{scope}: {fact}"
    return _text_from_delta(item, path)


def _dated_lines(date_str: str, round_id: str, agent_id: str, texts: list[str]) -> list[str]:
    return [f"- {date_str} [{round_id}/{agent_id}] {text}" for text in texts]


def ingest_memory_deltas(card_folder: str | Path, run_dir: str | Path, date_str: str | None = None) -> Dict[str, Any]:
    """Persist validated memory deltas from `story.input.json`."""
    card = Path(card_folder)
    root = Path(run_dir)
    story_input = agent_run.read_json(root / "story.input.json")
    if not isinstance(story_input, dict):
        raise MemoryIngestionError(f"{root / 'story.input.json'}: story input is missing")

    round_id = str(story_input.get("round_id") or root.name)
    deltas = story_input.get("memory_deltas", {})
    if not isinstance(deltas, dict):
        raise MemoryIngestionError("story_input.memory_deltas must be an object")

    now = date_str or datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    ledger = _load_ledger(card)
    ingested: list[str] = []

    player_items = deltas.get("player") or []
    if player_items:
        key = f"{round_id}:player"
        if key not in ledger:
            texts = [_validate_actor_delta(item, f"memory_deltas.player[{index}]") for index, item in enumerate(player_items)]
            _append_lines(
                card / "memory" / "player" / "recent.md",
                "# Player Agent Memory\n",
                _dated_lines(now, round_id, "player", texts),
            )
            ledger.add(key)
            ingested.append("player")

    character_deltas = deltas.get("characters") or {}
    if isinstance(character_deltas, dict):
        for name, items in character_deltas.items():
            if not items:
                continue
            safe = _safe_name(str(name))
            key = f"{round_id}:character:{safe}"
            if key in ledger:
                continue
            texts = [
                _validate_actor_delta(item, f"memory_deltas.characters.{safe}[{index}]")
                for index, item in enumerate(items)
            ]
            _append_lines(
                card / "memory" / "characters" / safe / "recent.md",
                "# Character Recent Memory\n",
                _dated_lines(now, round_id, f"character:{safe}", texts),
            )
            ledger.add(key)
            ingested.append(f"character:{safe}")

    world_items = deltas.get("world") or []
    if world_items:
        key = f"{round_id}:world"
        if key not in ledger:
            texts = [_world_text(item, f"memory_deltas.world[{index}]") for index, item in enumerate(world_items)]
            _append_lines(
                card / "memory" / "world_delta.md",
                "# World State Deltas\n",
                _dated_lines(now, round_id, "world", texts),
            )
            ledger.add(key)
            ingested.append("world")

    _save_ledger(card, ledger)
    return {
        "ok": True,
        "round_id": round_id,
        "ingested": ingested,
        "ledger": str(_ledger_path(card)),
    }
