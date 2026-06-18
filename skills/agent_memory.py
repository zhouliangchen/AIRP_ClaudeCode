"""Safe ingestion of memory deltas produced by RP subagents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_memory_model
import agent_run


CST = timezone(timedelta(hours=8))

ACTOR_FORBIDDEN_MARKERS = agent_memory_model.ACTOR_FORBIDDEN_MARKERS
SUMMARY_ROUND_RE = re.compile(r"^round-(\d{6})$")
ACTOR_MEMORY_EVENT_TYPES = {"memory_delta", "goal_update"}
ACTOR_MEMORY_EVENT_KEYS = {"type", "target", "content", "metadata"}


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


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _delete_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _snapshot_files(paths: Iterable[Path]) -> dict[Path, bytes | None]:
    snapshots: dict[Path, bytes | None] = {}
    for path in paths:
        if path in snapshots:
            continue
        snapshots[path] = path.read_bytes() if path.exists() else None
    return snapshots


def _restore_files(snapshots: dict[Path, bytes | None]) -> None:
    for path, content in snapshots.items():
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        except OSError:
            pass


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
        text = str(item.get("text") or item.get("fact") or item.get("content") or "").strip()
    else:
        text = ""
    if not text:
        raise MemoryIngestionError(f"{path}: memory delta text is required")
    return text


def _validate_actor_delta(item: Any, path: str) -> str:
    if not isinstance(item, dict):
        raise MemoryIngestionError(f"{path}.type: actor memory event object is required")

    _validate_no_forbidden_marker(item, path)

    event_type = item.get("type")
    if event_type not in ACTOR_MEMORY_EVENT_TYPES:
        raise MemoryIngestionError(f"{path}.type: actor memory delta type must be memory_delta or goal_update")
    content = item.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MemoryIngestionError(f"{path}.content: actor memory content is required")

    for key in sorted(item):
        if key not in ACTOR_MEMORY_EVENT_KEYS:
            raise MemoryIngestionError(f"{path}.{key}: actor memory event field is not allowed")
    if "target" in item and not isinstance(item["target"], str):
        raise MemoryIngestionError(f"{path}.target: actor memory target must be a string")
    if "metadata" in item and not isinstance(item["metadata"], dict):
        raise MemoryIngestionError(f"{path}.metadata: actor memory metadata must be an object")
    return content.strip()


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


def _prepare_actor_memory_delta(
    card: Path,
    ledger: set[str],
    round_id: str,
    date_str: str,
    agent_id: str,
    items: Any,
    path: str,
) -> tuple[Path, str, list[str], str, str] | None:
    if not isinstance(items, list):
        raise MemoryIngestionError(f"{path}: actor memory deltas must be a list")

    actor_id = str(agent_id or "").strip()
    if actor_id == "player":
        normalized_id = "player"
        recent_path = card / "memory" / "player" / "recent.md"
        header = "# Player Agent Memory\n"
    elif actor_id.startswith("character:"):
        name = actor_id.split(":", 1)[1].strip()
        if not name:
            raise MemoryIngestionError(f"{path}: unsupported actor memory id {actor_id}")
        marker = _contains_forbidden_marker(name)
        if marker:
            raise MemoryIngestionError(f"{path}: forbidden actor marker {marker}")
        safe = _safe_name(name)
        normalized_id = f"character:{safe}"
        recent_path = card / "memory" / "characters" / safe / "recent.md"
        header = "# Character Recent Memory\n"
    else:
        marker = _contains_forbidden_marker(actor_id)
        if marker:
            raise MemoryIngestionError(f"{path}: forbidden actor marker {marker}")
        raise MemoryIngestionError(f"{path}: unsupported actor memory id {actor_id}")

    if not items:
        return None

    texts = [_validate_actor_delta(item, f"{path}[{index}]") for index, item in enumerate(items)]
    key = f"{round_id}:{normalized_id}"
    if key in ledger:
        return None

    return recent_path, header, _dated_lines(date_str, round_id, normalized_id, texts), key, normalized_id


def memory_summary_due(round_id: str, interval: int = 6) -> bool:
    """Return whether a run should ask actor subagents to summarize memory."""
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        return False
    if interval <= 0:
        return False

    match = SUMMARY_ROUND_RE.match(str(round_id or ""))
    if not match:
        return False
    return int(match.group(1)) % interval == 0


def _summary_output_path(agent_id: str) -> str:
    return f"memory_summaries/{_safe_name(agent_id)}.summary.json"


def _summary_prompt_path(agent_id: str) -> str:
    return f"prompts/memory/{_safe_name(agent_id)}.prompt.md"


def _actor_memory_paths(card: Path, agent_id: str) -> tuple[str, Path, Path]:
    if agent_id == "player":
        base = card / "memory" / "player"
        return "player", base, base / "recent.md"
    if agent_id.startswith("character:"):
        name = agent_id.split(":", 1)[1] or "_unknown"
        safe = _safe_name(name)
        base = card / "memory" / "characters" / safe
        return name, base, base / "recent.md"
    safe = _safe_name(agent_id)
    base = card / "memory" / "agent_summaries" / safe
    return agent_id, base, base / "recent.md"


def _read_optional_text(path: Path, limit: int = 12000) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")[:limit].strip()
    except Exception:
        return ""


def _memory_summary_prompt(card: Path, run_dir: Path, agent_id: str, output_path: str) -> str:
    actor_name, memory_dir, recent_path = _actor_memory_paths(card, agent_id)
    long_term = _read_optional_text(memory_dir / "long_term.md")
    key_memories = _read_optional_text(memory_dir / "key_memories.md")
    short_term = _read_optional_text(memory_dir / "short_term.md")
    recent_memory = _read_optional_text(recent_path)
    goals = _read_optional_text(memory_dir / "goals.json")
    contract = {
        "agent_id": agent_id,
        "character_name": actor_name if agent_id.startswith("character:") else "",
        "source": "self",
        "visibility": "actor",
        "long_term": {
            "self_understanding": [],
            "stable_beliefs": [],
            "relationship_models": [],
        },
        "key_memories": [
            {"content": "specific remembered event", "importance": "high", "details": []}
        ],
        "short_term": [
            {"content": "current-scene memory", "expires_after": "scene_end"}
        ],
        "goals": {"active": [], "paused": [], "resolved": []},
    }
    return f"""
# Memory Summary Prompt

Agent id: `{agent_id}`
Round id: `{run_dir.name}`
Required output: `{output_path}`

You are organizing only this actor's first-person memory. organization is not compression:
do not replace specific remembered events with vague summaries. Key memories should
preserve enough details for the actor to recall what happened, who was involved, and
why it mattered. You may organize memory and goals only. Do not edit profile,
background, personality, body_facts, authoritative_setting, or character_sheet data.
Do not add GM-only, omniscient, world-truth, hidden-note, or out-of-character knowledge.

## Required JSON Contract

```json
{json.dumps(contract, ensure_ascii=False, indent=2)}
```

## Current Long-Term Memory

```markdown
{long_term or "(none)"}
```

## Current Key Memories

```markdown
{key_memories or "(none)"}
```

## Current Short-Term Memory

```markdown
{short_term or "(none)"}
```

## Recent First-Person Memory

```markdown
{recent_memory or "(none)"}
```

## Current Goals

```json
{goals or "{}"}
```
"""


def write_memory_summary_prompts(
    card_folder: str | Path,
    run_dir: str | Path,
    manifest: Dict[str, Any],
    agents: Iterable[str],
) -> Dict[str, Any]:
    """Materialize actor memory-summary prompts and update the manifest in place."""
    card = Path(card_folder)
    root = Path(run_dir)
    scheduled: list[str] = []

    prompts = manifest.setdefault("prompts", {})
    expected_outputs = manifest.setdefault("expected_outputs", {})
    memory_prompts = prompts.setdefault("memory_summaries", {})
    memory_outputs = expected_outputs.setdefault("memory_summaries", {})

    for raw_agent_id in agents:
        agent_id = str(raw_agent_id or "").strip()
        if not agent_id:
            continue
        output_rel = _summary_output_path(agent_id)
        prompt_rel = _summary_prompt_path(agent_id)
        _write_text(root / prompt_rel, _memory_summary_prompt(card, root, agent_id, output_rel))
        memory_prompts[agent_id] = prompt_rel
        memory_outputs[agent_id] = output_rel
        scheduled.append(agent_id)

    return {"ok": True, "scheduled": scheduled}


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


ACTOR_FORBIDDEN_MARKER_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in ACTOR_FORBIDDEN_MARKERS
}


def _contains_forbidden_marker(text: str) -> str:
    tokens = _canonical_tokens(text)
    if not tokens:
        return ""
    for marker, marker_tokens in ACTOR_FORBIDDEN_MARKER_TOKENS.items():
        if not marker_tokens or len(marker_tokens) > len(tokens):
            continue
        for index in range(0, len(tokens) - len(marker_tokens) + 1):
            if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
                return marker
    return ""


def _validate_no_forbidden_marker(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_marker = _contains_forbidden_marker(str(key))
            if key_marker:
                raise MemoryIngestionError(f"{path}.{key}: forbidden summary marker {key_marker}")
            _validate_no_forbidden_marker(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_no_forbidden_marker(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        marker = _contains_forbidden_marker(value)
        if marker:
            raise MemoryIngestionError(f"{path}: forbidden summary marker {marker}")


def _validate_memory_summary(payload: Dict[str, Any], path: Path) -> Dict[str, Any]:
    try:
        return agent_memory_model.validate_memory_update(payload)
    except agent_memory_model.AgentMemoryModelError as exc:
        raise MemoryIngestionError(f"{path.name}: {exc}") from exc


def _validate_character_name_matches_agent_id(agent_id: str, payload: Dict[str, Any], path: Path) -> None:
    if not agent_id.startswith("character:"):
        return
    declared = str(payload.get("character_name") or "").strip()
    if declared and declared != agent_id.split(":", 1)[1]:
        raise MemoryIngestionError(
            f"{path.name}: character_name mismatch, expected {agent_id.split(':', 1)[1]}, got {declared}"
        )


def _scheduled_memory_summaries(root: Path) -> Dict[str, Path]:
    manifest = _read_json(root / "manifest.json", {})
    if not isinstance(manifest, dict):
        return {}
    expected_outputs = manifest.get("expected_outputs", {})
    if not isinstance(expected_outputs, dict):
        return {}
    summaries = expected_outputs.get("memory_summaries", {})
    if not isinstance(summaries, dict):
        return {}

    scheduled: Dict[str, Path] = {}
    for agent_id, relative_path in summaries.items():
        agent_key = str(agent_id or "").strip()
        if not agent_key:
            continue
        scheduled[agent_key] = root / str(relative_path)
    return scheduled


def _summary_sort_key(item: tuple[str, Path]) -> tuple[int, str]:
    agent_id, _path = item
    return (0 if agent_id == "player" else 1, agent_id)


def ingest_memory_summaries(card_folder: str | Path, run_dir: str | Path) -> Dict[str, Any]:
    """Persist actor self-summary artifacts from `memory_summaries/*.summary.json`."""
    card = Path(card_folder)
    root = Path(run_dir)
    summary_root = root / "memory_summaries"
    scheduled = _scheduled_memory_summaries(root)
    if not summary_root.exists() and not scheduled:
        return {"ok": True, "round_id": root.name, "ingested": []}
    if scheduled and not summary_root.exists():
        missing = ", ".join(str(path.relative_to(root).as_posix()) for path in scheduled.values())
        raise MemoryIngestionError(f"missing scheduled memory summaries: {missing}")

    actual_paths = {path.resolve() for path in summary_root.glob("*.summary.json")} if summary_root.exists() else set()
    scheduled_paths = {path.resolve() for path in scheduled.values()}
    extra_paths = sorted(actual_paths - scheduled_paths)
    if extra_paths:
        extra = ", ".join(path.name for path in extra_paths)
        raise MemoryIngestionError(f"unscheduled memory summary files: {extra}")

    missing_paths = [(agent_id, path) for agent_id, path in scheduled.items() if not path.exists()]
    if missing_paths:
        missing = ", ".join(f"{agent_id}:{path.relative_to(root).as_posix()}" for agent_id, path in missing_paths)
        raise MemoryIngestionError(f"missing scheduled memory summaries: {missing}")

    records: list[Dict[str, Any]] = []
    for expected_agent_id, path in sorted(scheduled.items(), key=_summary_sort_key):
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            raise MemoryIngestionError(f"{path.name}: summary payload must be an object")
        update = _validate_memory_summary(payload, path)
        agent_id = update["agent_id"]
        if agent_id != expected_agent_id:
            raise MemoryIngestionError(
                f"{path.name}: agent_id mismatch, expected {expected_agent_id}, got {agent_id}"
            )
        _validate_character_name_matches_agent_id(agent_id, update, path)
        records.append(update)

    pending_writes: list[tuple[Path, str, Any]] = []
    pending_deletes: list[Path] = []
    ingested: list[str] = []
    for update in records:
        agent_id = update["agent_id"]
        _actor_name, memory_dir, recent_path = _actor_memory_paths(card, agent_id)
        pending_writes.extend(
            [
                (memory_dir / "long_term.md", "text", agent_memory_model.render_long_term_markdown(update)),
                (memory_dir / "key_memories.md", "text", agent_memory_model.render_key_memories_markdown(update)),
                (memory_dir / "short_term.md", "text", agent_memory_model.render_short_term_markdown(update)),
                (memory_dir / "goals.json", "json", agent_memory_model.render_goals_json(update)),
            ]
        )
        pending_deletes.append(recent_path)
        ingested.append(agent_id)

    snapshot_paths = [path for path, _kind, _content in pending_writes]
    snapshot_paths.extend(pending_deletes)
    snapshots = _snapshot_files(snapshot_paths)
    try:
        for path, kind, content in pending_writes:
            if kind == "json":
                _write_json(path, content)
            else:
                _write_text(path, content)
        for path in pending_deletes:
            _delete_file(path)
    except Exception:
        _restore_files(snapshots)
        raise

    return {"ok": True, "round_id": root.name, "ingested": ingested}


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
    legacy_keys = sorted(key for key in ("characters", "player") if key in deltas)
    if legacy_keys:
        raise MemoryIngestionError(
            f"legacy memory_deltas branches are not supported: {', '.join(legacy_keys)}"
        )

    now = date_str or datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    ledger = _load_ledger(card)
    next_ledger = set(ledger)
    pending_writes: list[tuple[Path, str, list[str]]] = []
    ingested: list[str] = []

    if "actors" in deltas:
        actor_deltas = deltas["actors"]
        if not isinstance(actor_deltas, dict):
            raise MemoryIngestionError("memory_deltas.actors must be an object")
        for actor_id, items in actor_deltas.items():
            prepared = _prepare_actor_memory_delta(
                card,
                next_ledger,
                round_id,
                now,
                str(actor_id),
                items,
                f"memory_deltas.actors.{actor_id}",
            )
            if prepared:
                recent_path, header, lines, key, ingested_id = prepared
                pending_writes.append((recent_path, header, lines))
                next_ledger.add(key)
                ingested.append(ingested_id)
    if "world" in deltas:
        world_items = deltas["world"]
        if not isinstance(world_items, list):
            raise MemoryIngestionError("memory_deltas.world must be a list")
    else:
        world_items = []
    if world_items:
        key = f"{round_id}:world"
        texts = [_world_text(item, f"memory_deltas.world[{index}]") for index, item in enumerate(world_items)]
        if key not in next_ledger:
            pending_writes.append((
                card / "memory" / "world_delta.md",
                "# World State Deltas\n",
                _dated_lines(now, round_id, "world", texts),
            ))
            next_ledger.add(key)
            ingested.append("world")

    snapshot_paths = [path for path, _header, _lines in pending_writes]
    snapshot_paths.append(_ledger_path(card))
    snapshots = _snapshot_files(snapshot_paths)
    try:
        for path, header, lines in pending_writes:
            _append_lines(path, header, lines)
        _save_ledger(card, next_ledger)
    except Exception:
        _restore_files(snapshots)
        raise
    return {
        "ok": True,
        "round_id": round_id,
        "ingested": ingested,
        "ledger": str(_ledger_path(card)),
    }
