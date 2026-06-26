import os
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WINDOWS_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|]+')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
RECALL_PREFIX = "\u6211\u60f3\u56de\u5fc6"
GM_SAID_PREFIX = "\u8bb0\u5fc6\u7684\u56de\u58f0\uff1a"
SELF_REPLIED_PREFIX = "\u6211\uff1a"
PLAYER_MAPPING_FILE = "player.md"


class ActorMemoryStoreError(RuntimeError):
    """Raised when persisted actor memory files cannot be read safely."""


@dataclass(frozen=True)
class ActorMemoryPaths:
    card: Path
    actor_id: str
    name: str
    actor_dir: Path
    objective_dir: Path
    profile: Path
    long_term: Path
    key_memories: Path
    short_term: Path
    objective_profile: Path
    background: Path
    source_ledger: Path


def _safe_component(value: str, default: str) -> str:
    text = WINDOWS_FORBIDDEN_CHARS.sub("_", str(value or "").strip())
    text = text.strip().strip(".").strip()
    if not text:
        return default
    reserved_probe = text.split(".", 1)[0].upper()
    if reserved_probe in WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    return text


def _safe_character_name(value: str) -> str:
    name = _safe_component(value, "_unknown_character")
    if name.casefold() == "_self":
        return "character__self"
    return name


def _safe_actor_name(value: str) -> str:
    name = _safe_component(value, "_unknown_actor")
    if name.casefold() == "_self":
        return "actor__self"
    return name


def _player_mapping_path(card_folder: str | Path) -> Path:
    return Path(card_folder) / "characters" / PLAYER_MAPPING_FILE


def _parse_player_mapping(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key in {"name", "path"} and value:
            result[key] = value
    return result


def _player_mapping(card_folder: str | Path) -> tuple[str, Path]:
    card = Path(card_folder)
    mapping = _parse_player_mapping(_read_text(_player_mapping_path(card)))
    raw_name = mapping.get("name") or "player"
    name = _safe_component(raw_name, "player")
    if name.casefold() == "_self":
        name = "player"

    raw_path = mapping.get("path") or f"characters/{name}"
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        path = Path("characters") / name
    parts = path.parts
    if not parts or parts[0] != "characters" or len(parts) != 2:
        path = Path("characters") / name
    folder = _safe_component(path.parts[1], name)
    if folder.casefold() == "_self":
        folder = name
    return name, card / "characters" / folder


def _write_default_player_mapping(card_folder: str | Path, paths: "ActorMemoryPaths") -> None:
    mapping_path = _player_mapping_path(card_folder)
    if mapping_path.exists():
        return
    rel_actor_dir = paths.actor_dir.relative_to(paths.card).as_posix()
    _write_text_atomic(
        mapping_path,
        f"name: {paths.name}\npath: {rel_actor_dir}\n",
    )


def write_player_mapping(card_folder: str | Path, name: Any, relative_path: Any = "") -> dict[str, str]:
    card = Path(card_folder)
    safe_name = _safe_component(str(name or "").strip(), "player")
    if safe_name.casefold() == "_self":
        safe_name = "player"
    rel_text = str(relative_path or "").strip() or f"characters/{safe_name}"
    rel = Path(rel_text)
    if rel.is_absolute() or ".." in rel.parts:
        rel = Path("characters") / safe_name
    if not rel.parts or rel.parts[0] != "characters" or len(rel.parts) != 2:
        rel = Path("characters") / safe_name
    folder = _safe_component(rel.parts[1], safe_name)
    if folder.casefold() == "_self":
        folder = safe_name
    normalized_rel = f"characters/{folder}"
    _write_text_atomic(
        _player_mapping_path(card),
        f"name: {safe_name}\npath: {normalized_rel}\n",
    )
    return {"name": safe_name, "path": normalized_rel}


def _actor_name(actor_id: Any) -> str:
    text = str(actor_id or "").strip()
    if not text or text == "player":
        return "player"
    if text.startswith("character:"):
        return _safe_character_name(text.split(":", 1)[1])
    return _safe_actor_name(text)


def canonical_actor_id(actor_id: Any) -> str:
    text = str(actor_id or "").strip()
    if not text or text == "player":
        return "player"
    if text.startswith("character:"):
        return f"character:{_safe_character_name(text.split(':', 1)[1])}"
    return _safe_actor_name(text)


def actor_paths(card_folder: str | Path, actor_id: Any) -> ActorMemoryPaths:
    card = Path(card_folder)
    actor_text = str(actor_id or "").strip()
    if not actor_text or actor_text == "player":
        name, actor_dir = _player_mapping(card)
    else:
        name = _actor_name(actor_id)
        actor_dir = card / "characters" / name
    objective_dir = card / "memory" / "characters" / name
    return ActorMemoryPaths(
        card=card,
        actor_id=str(actor_id or "").strip(),
        name=name,
        actor_dir=actor_dir,
        objective_dir=objective_dir,
        profile=actor_dir / "profile.md",
        long_term=actor_dir / "long_term_memories.md",
        key_memories=actor_dir / "key_memories.json",
        short_term=actor_dir / "short_term_memories.md",
        objective_profile=objective_dir / "profile.md",
        background=objective_dir / "background.md",
        source_ledger=actor_dir / ".short_term_sources.json",
    )


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _write_json_atomic(path: Path, payload: Any) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_text_if_missing(path: Path, text: str = "") -> None:
    if path.exists():
        return
    _write_text_atomic(path, text)


def _write_json_if_missing(path: Path, payload: Any) -> None:
    if path.exists():
        return
    _write_json_atomic(path, payload)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorMemoryStoreError(f"failed to read JSON memory file {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    _write_json_atomic(path, payload)


def ensure_actor_files(card_folder: str | Path, actor_id: Any, profile: str = "") -> ActorMemoryPaths:
    paths = actor_paths(card_folder, actor_id)
    actor_text = str(actor_id or "").strip()
    if not actor_text or actor_text == "player":
        legacy_self = Path(card_folder) / "characters" / "_self"
        if legacy_self.exists():
            shutil.rmtree(legacy_self)
        _write_default_player_mapping(card_folder, paths)
    _write_text_if_missing(paths.profile, str(profile or ""))
    _write_text_if_missing(paths.long_term, "")
    _write_json_if_missing(paths.key_memories, {"memories": []})
    _write_text_if_missing(paths.short_term, "")
    _write_text_if_missing(paths.objective_profile, "")
    _write_text_if_missing(paths.background, "")
    return paths


def _key_memory_items(paths: ActorMemoryPaths) -> list[dict[str, str]]:
    payload = _read_json(paths.key_memories, {"memories": []})
    memories = payload.get("memories", []) if isinstance(payload, dict) else []
    if not isinstance(memories, list):
        return []
    normalized = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "tag": str(item.get("tag") or ""),
                "summary": str(item.get("summary") or ""),
                "detail": str(item.get("detail") or ""),
            }
        )
    return normalized


def read_actor_memory(card_folder: str | Path, actor_id: Any) -> dict[str, Any]:
    paths = actor_paths(card_folder, actor_id)
    return {
        "name": paths.name,
        "profile": _read_text(paths.profile),
        "objective_profile": _read_text(paths.objective_profile),
        "background": _read_text(paths.background),
        "long_term": _read_text(paths.long_term),
        "key_memories": _key_memory_items(paths),
        "short_term": _read_text(paths.short_term),
    }


def _load_source_ids(path: Path) -> set[str]:
    payload = _read_json(path, {"source_ids": []})
    values = payload.get("source_ids", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value)}


def append_short_term_dialogue(
    card_folder: str | Path,
    actor_id: Any,
    speaker: Any,
    content: Any,
    source_id: Any = "",
) -> bool:
    text = str(content or "").strip()
    if not text:
        return False

    paths = ensure_actor_files(card_folder, actor_id)
    source_key = str(source_id or "").strip()
    source_ids = _load_source_ids(paths.source_ledger)
    if source_key and source_key in source_ids:
        return False

    speaker_key = str(speaker or "").strip().casefold()
    prefix = GM_SAID_PREFIX if speaker_key in {"gm", "subgm"} else SELF_REPLIED_PREFIX
    existing = _read_text(paths.short_term)
    next_text = existing.rstrip()
    if next_text:
        next_text += "\n\n"
    next_text += f"{prefix}{text}\n\n"
    _write_text_atomic(paths.short_term, next_text)

    if source_key:
        source_ids.add(source_key)
        _write_json(paths.source_ledger, {"source_ids": sorted(source_ids)})
    return True


def _empty_memory() -> dict[str, str]:
    return {"tag": "", "summary": "", "detail": ""}


def _normalize_recall_query(text: Any) -> str:
    # Callers decide semantically whether recall is intended; this only strips
    # the legacy natural-language protocol prefix when the tool is already invoked.
    query = str(text or "").strip()
    if not query.startswith(RECALL_PREFIX):
        return query
    rest = query[len(RECALL_PREFIX):].lstrip()
    if rest.startswith(("\uff1a", ":")):
        return rest[1:].strip()
    return query


def _match_memory(query: str, item: dict[str, str]) -> bool:
    needle = query.casefold()
    tag = item["tag"].casefold()
    summary = item["summary"].casefold()
    return bool(
        needle
        and (
            needle in tag
            or needle in summary
            or tag in needle
            or summary in needle
        )
    )


def recall_key_memory(card_folder: str | Path, actor_id: Any, natural_text: Any) -> dict[str, str]:
    query = _normalize_recall_query(natural_text)
    if not query:
        return _empty_memory()

    paths = ensure_actor_files(card_folder, actor_id)
    for item in _key_memory_items(paths):
        if _match_memory(query, item):
            return item
    return _empty_memory()


def _text_field(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def validate_memory_update(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("memory update payload must be an object")

    long_term = _text_field(payload.get("long_term_memories", ""), "long_term_memories", 1000)
    key_memories = payload.get("key_memories", [])
    if not isinstance(key_memories, list):
        raise ValueError("key_memories must be a list")
    if len(key_memories) > 16:
        raise ValueError("key_memories must contain at most 16 items")

    normalized = []
    for index, item in enumerate(key_memories):
        if not isinstance(item, dict):
            raise ValueError(f"key_memories[{index}] must be an object")
        normalized.append(
            {
                "tag": _text_field(item.get("tag", ""), f"key_memories[{index}].tag", 20, required=True),
                "summary": _text_field(
                    item.get("summary", ""),
                    f"key_memories[{index}].summary",
                    100,
                    required=True,
                ),
                "detail": _text_field(item.get("detail", ""), f"key_memories[{index}].detail", 600),
            }
        )
    return {"long_term_memories": long_term, "key_memories": normalized}


def apply_memory_update(card_folder: str | Path, actor_id: Any, payload: Any) -> dict[str, Any]:
    update = validate_memory_update(payload)
    paths = ensure_actor_files(card_folder, actor_id)
    _write_text_atomic(
        paths.long_term,
        update["long_term_memories"].rstrip() + ("\n" if update["long_term_memories"] else ""),
    )
    _write_json_atomic(paths.key_memories, {"memories": update["key_memories"]})
    _write_text_atomic(paths.short_term, "")
    _write_json_atomic(paths.source_ledger, {"source_ids": []})
    return {"name": paths.name, **update}
