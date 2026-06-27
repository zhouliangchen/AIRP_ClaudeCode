"""Safe ingestion of memory deltas produced by RP subagents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import agent_memory_model
import agent_run
import agent_visibility
import actor_memory_store


CST = timezone(timedelta(hours=8))

ACTOR_FORBIDDEN_MARKERS = agent_memory_model.ACTOR_FORBIDDEN_MARKERS
POST_ROUND_FORBIDDEN_MARKERS = set(agent_visibility.HIDDEN_MARKERS) | set(ACTOR_FORBIDDEN_MARKERS)
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
    existing = path.read_text(encoding="utf-8") if path.exists() else (header.rstrip() + "\n" if header else "")
    addition = "\n".join(line for line in lines if line)
    if not addition:
        return
    prefix = existing.rstrip()
    text = f"{prefix}\n{addition}\n" if prefix else f"{addition}\n"
    path.write_text(text, encoding="utf-8")


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
        memory_path = actor_memory_store.actor_paths(card, "player").short_term
    elif actor_id.startswith("character:"):
        name = actor_id.split(":", 1)[1].strip()
        if not name:
            raise MemoryIngestionError(f"{path}: unsupported actor memory id {actor_id}")
        marker = _contains_forbidden_marker(name)
        if marker:
            raise MemoryIngestionError(f"{path}: forbidden actor marker {marker}")
        paths = actor_memory_store.actor_paths(card, actor_id)
        normalized_id = f"character:{paths.name}"
        memory_path = paths.short_term
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

    lines = [f"{actor_memory_store.SELF_REPLIED_PREFIX}{text}" for text in texts]
    return memory_path, "", lines, key, normalized_id


def _post_round_job_output_path(agent_id: str) -> str:
    return f"post_round_memory_jobs/{_safe_name(agent_id)}.summary.json"


def _post_round_job_input_path(agent_id: str) -> str:
    return f"post_round_memory_jobs/{_safe_name(agent_id)}.job.json"


def _post_round_job_prompt_path(agent_id: str) -> str:
    return f"prompts/post_round_memory/{_safe_name(agent_id)}.prompt.md"


def _post_round_objective_job_output_path(agent_id: str) -> str:
    return f"post_round_objective_memory_jobs/{_safe_name(agent_id)}.summary.json"


def _post_round_objective_job_input_path(agent_id: str) -> str:
    return f"post_round_objective_memory_jobs/{_safe_name(agent_id)}.job.json"


def _post_round_objective_job_prompt_path(agent_id: str) -> str:
    return f"prompts/post_round_objective_memory/{_safe_name(agent_id)}.prompt.md"


def _read_optional_text(path: Path, limit: int = 12000) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")[:limit].strip()
    except Exception:
        return ""


def _actor_outputs_from_mapping(actor_outputs: Any, agent_id: str) -> list[Any]:
    if not isinstance(actor_outputs, dict):
        return []
    outputs = actor_outputs.get(agent_id, [])
    if not isinstance(outputs, list):
        return []
    return list(outputs)


def _participating_actors(story_input: Dict[str, Any]) -> list[str]:
    actors: set[str] = set()

    def collect(actor_outputs: Any) -> None:
        if not isinstance(actor_outputs, dict):
            return
        for actor_id, outputs in actor_outputs.items():
            actor_key = str(actor_id or "").strip()
            if isinstance(outputs, list) and outputs:
                actors.add(actor_key)

    loop_outputs = story_input.get("loop_outputs", {})
    if isinstance(loop_outputs, dict):
        collect(loop_outputs.get("actors", {}))

    side_threads = story_input.get("side_threads", {})
    threads = side_threads.get("threads", []) if isinstance(side_threads, dict) else []
    if isinstance(threads, list):
        for thread in threads:
            if isinstance(thread, dict):
                collect(thread.get("actor_outputs", {}))

    return sorted(actor for actor in actors if actor)


def _post_round_actor_outputs(story_input: Dict[str, Any], agent_id: str) -> list[Any]:
    outputs: list[Any] = []
    loop_outputs = story_input.get("loop_outputs", {})
    if isinstance(loop_outputs, dict):
        outputs.extend(_actor_outputs_from_mapping(loop_outputs.get("actors", {}), agent_id))

    side_threads = story_input.get("side_threads", {})
    threads = side_threads.get("threads", []) if isinstance(side_threads, dict) else []
    if isinstance(threads, list):
        for thread in threads:
            if isinstance(thread, dict):
                outputs.extend(_actor_outputs_from_mapping(thread.get("actor_outputs", {}), agent_id))
    return outputs


def _actor_call_dialogue_item(call: Any, speaker: str, agent_id: str) -> dict[str, str] | None:
    if not isinstance(call, dict):
        return None
    if str(call.get("actor_id") or "") != agent_id:
        return None
    content = str(call.get("prompt") or "").strip()
    if not content:
        return None
    return {
        "speaker": speaker,
        "call_id": str(call.get("call_id") or ""),
        "content": content,
    }


def _actor_response_dialogue_items(outputs: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for output in outputs if isinstance(outputs, list) else []:
        if not isinstance(output, dict):
            continue
        natural_reply = str(output.get("natural_reply") or "").strip()
        if natural_reply:
            items.append(
                {
                    "speaker": "我",
                    "source_call_id": str(output.get("source_call_id") or ""),
                    "event_type": "natural_reply",
                    "content": natural_reply,
                }
            )
            continue
        for event in output.get("events") or []:
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "").strip() != "reply":
                continue
            content = str(event.get("content") or "").strip()
            if not content:
                continue
            items.append(
                {
                    "speaker": "我",
                    "source_call_id": str(event.get("source_call_id") or ""),
                    "event_type": str(event.get("type") or ""),
                    "content": content,
                }
            )
    return items


def _round_dialogue_for_actor(story_input: Dict[str, Any], agent_id: str) -> list[dict[str, str]]:
    dialogue: list[dict[str, str]] = []
    loop_outputs = story_input.get("loop_outputs", {})
    if isinstance(loop_outputs, dict):
        gm_loop = loop_outputs.get("gm", {})
        if isinstance(gm_loop, dict):
            for output in gm_loop.get("outputs") or []:
                if not isinstance(output, dict):
                    continue
                for call in output.get("actor_calls") or []:
                    item = _actor_call_dialogue_item(call, "对我说的话", agent_id)
                    if item:
                        dialogue.append(item)
        dialogue.extend(_actor_response_dialogue_items(_actor_outputs_from_mapping(loop_outputs.get("actors", {}), agent_id)))

    side_threads = story_input.get("side_threads", {})
    threads = side_threads.get("threads", []) if isinstance(side_threads, dict) else []
    if isinstance(threads, list):
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            subgm_output = thread.get("subgm_output")
            if isinstance(subgm_output, dict):
                for call in subgm_output.get("actor_calls") or []:
                    item = _actor_call_dialogue_item(call, "对我说的话", agent_id)
                    if item:
                        dialogue.append(item)
            dialogue.extend(_actor_response_dialogue_items(_actor_outputs_from_mapping(thread.get("actor_outputs", {}), agent_id)))
    return dialogue


def _visible_events(story_input: Dict[str, Any]) -> list[Any]:
    trace = story_input.get("interaction_trace", {})
    if not isinstance(trace, dict):
        return []
    visible_events = trace.get("visible_events", [])
    if not isinstance(visible_events, list):
        return []
    return list(visible_events)


def _actor_value_matches(value: Any, agent_id: str) -> bool:
    return str(value or "").strip().casefold() == str(agent_id or "").strip().casefold()


def _public_actor_marker(value: Any) -> bool:
    marker = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return marker in agent_visibility.PUBLIC_MARKERS


def _visibility_list_grants_actor(values: Any, agent_id: str) -> bool:
    if isinstance(values, str):
        candidates = [values]
    elif isinstance(values, (list, tuple, set)):
        candidates = list(values)
    else:
        candidates = []
    return any(
        _actor_value_matches(value, agent_id) or _public_actor_marker(value)
        for value in candidates
    )


def _event_relevant_to_actor(event: Any, agent_id: str) -> bool:
    if not isinstance(event, dict):
        return False

    fields = agent_visibility.visibility_fields_from_event(event)
    for key in ("source_actor", "target_actor"):
        if _actor_value_matches(fields.get(key), agent_id):
            return True
    if _visibility_list_grants_actor(fields.get("visible_to", []), agent_id):
        return True

    basis = fields.get("visibility_basis", {})
    if isinstance(basis, dict):
        if str(basis.get("mode") or "").strip() == "public":
            return True
        if _visibility_list_grants_actor(basis.get("visible_to", []), agent_id):
            return True

    raw_basis = event.get("visibility_basis")
    if isinstance(raw_basis, dict):
        if str(raw_basis.get("mode") or "").strip().lower() == "public":
            return True
        if _visibility_list_grants_actor(raw_basis.get("visible_to", []), agent_id):
            return True

    metadata = event.get("visibility_metadata")
    if isinstance(metadata, dict) and _visibility_list_grants_actor(metadata.get("visible_to", []), agent_id):
        return True

    return False


def _actor_visible_events(story_input: Dict[str, Any], agent_id: str) -> list[Any]:
    return [
        event
        for event in _visible_events(story_input)
        if _event_relevant_to_actor(event, agent_id)
    ]


def _validate_post_round_actor_safe_payload(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_marker = agent_visibility.hidden_marker_name(key, markers=POST_ROUND_FORBIDDEN_MARKERS)
            if key_marker:
                raise MemoryIngestionError(f"{path}.{key}: forbidden post-round memory marker {key_marker}")
            _validate_post_round_actor_safe_payload(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_post_round_actor_safe_payload(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        marker = agent_visibility.hidden_marker_name(value, markers=POST_ROUND_FORBIDDEN_MARKERS)
        if marker:
            raise MemoryIngestionError(f"{path}: forbidden post-round memory marker {marker}")


def _natural_lines(value: Any, *, indent: int = 0, limit: int = 30) -> list[str]:
    prefix = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if len(lines) >= limit:
                lines.append(prefix + "- ...")
                break
            if isinstance(item, (dict, list)):
                lines.append(prefix + f"- {key}:")
                lines.extend(_natural_lines(item, indent=indent + 1, limit=max(1, limit - len(lines))))
            else:
                text = str(item).strip()
                if text:
                    lines.append(prefix + f"- {key}: {text}")
        return lines
    if isinstance(value, list):
        for item in value:
            if len(lines) >= limit:
                lines.append(prefix + "- ...")
                break
            if isinstance(item, (dict, list)):
                lines.extend(_natural_lines(item, indent=indent, limit=max(1, limit - len(lines))))
            else:
                text = str(item).strip()
                if text:
                    lines.append(prefix + f"- {text}")
        return lines
    text = str(value or "").strip()
    return [prefix + text] if text else []


def _post_round_dialogue_text(round_dialogue: Any) -> str:
    if not isinstance(round_dialogue, list) or not round_dialogue:
        return "- 本轮没有需要我整理进短期记忆的直接对话。"
    parts: list[str] = []
    for index, item in enumerate(round_dialogue, 1):
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "对话").strip()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        parts.append(f"{index}. {speaker}：{content}")
    return "\n\n".join(parts) if parts else "- 本轮没有需要我整理进短期记忆的直接对话。"


def _post_round_reference_text(job_payload: Dict[str, Any]) -> str:
    profile = str(job_payload.get("profile") or "").strip()
    long_term = str(job_payload.get("long_term_memories") or "").strip()
    short_term = str(job_payload.get("short_term_memories") or "").strip()
    key_cues = job_payload.get("key_memory_cues", [])
    key_lines = _natural_lines(key_cues, limit=20)
    sections = [
        "## 我是谁",
        profile if profile else "暂无。",
        "",
        "## 我当前已有的长期记忆",
        long_term if long_term else "暂无。",
        "",
        "## 我当前已有的重点记忆线索",
        "\n".join(key_lines) if key_lines else "暂无。",
        "",
        "## 本轮自动记录的短期记忆",
        short_term if short_term else "暂无。",
    ]
    return "\n".join(sections)


def _post_round_job_payload(
    card: Path,
    run_dir: Path,
    story_input: Dict[str, Any],
    agent_id: str,
) -> Dict[str, Any]:
    character_name = agent_id.split(":", 1)[1] if agent_id.startswith("character:") else ""
    stored = actor_memory_store.read_actor_memory(card, agent_id)
    key_cues = [
        {"tag": str(item.get("tag") or ""), "summary": str(item.get("summary") or "")}
        for item in stored.get("key_memories", [])
        if isinstance(item, dict) and (item.get("tag") or item.get("summary"))
    ]
    payload = {
        "agent_id": agent_id,
        "character_name": character_name,
        "display_name": str(stored.get("name") or "").strip(),
        "round_id": str(story_input.get("round_id") or run_dir.name),
        "profile": str(stored.get("profile") or ""),
        "round_dialogue": _round_dialogue_for_actor(story_input, agent_id),
        "short_term_memories": str(stored.get("short_term") or ""),
        "long_term_memories": str(stored.get("long_term") or ""),
        "key_memory_cues": key_cues,
    }
    _validate_post_round_actor_safe_payload(payload, "post_round_memory_job")
    return payload


def _post_round_memory_prompt(
    run_dir: Path,
    agent_id: str,
    output_path: str,
    job_payload: Dict[str, Any],
) -> str:
    contract = {
        "agent_id": agent_id,
        "character_name": job_payload.get("character_name", ""),
        "long_term_memories": "1000字以内的第一人称长期记忆",
        "key_memories": [
            {"tag": "20字内标签", "summary": "100字内摘要", "detail": "600字内详情"}
        ],
    }
    round_dialogue = job_payload.get("round_dialogue", [])
    dialogue_text = _post_round_dialogue_text(round_dialogue)
    reference_text = _post_round_reference_text(job_payload)
    display_name = str(job_payload.get("display_name") or "").strip()
    name_line = f"我是 {display_name}。" if display_name else "我是当前正在整理记忆的这个人。"
    return f"""
# 回合末尾自我记忆整理规则

player 和 character 使用同一套整理规则。我的整理材料只来自 `characters/<角色名>/profile.md` 原文、`long_term_memories.md`、`key_memories.json` 的 tag/summary 线索、`short_term_memories.md`，以及本轮我和 GM/subGM 的直接对话。重点记忆 detail 不作为常驻输入；我只能在最终 JSON 中输出整理后的完整 `long_term_memories` 和完整 `key_memories` 列表。系统会在成功写回长期记忆和重点记忆后清空短期记忆。

# 我的记忆整理

{name_line}

现在我需要整理一下我的记忆，以帮助我理清思路。
我只整理自己的第一人称记忆和目标，不修改人设、背景、人格、身体事实或权威设定。
不要加入幕后事实、全知信息、隐藏备注、外部指令或别人的私密记忆。
长期记忆和重点记忆只能来自“本轮我和对我说话者的对话”、本轮自动记录的短期记忆、以及我已有的长期/重点记忆线索。
如果某件事没有出现在本轮对话或我已有记忆里，我不能把它整理成自己的记忆。
我可以小幅修补长期记忆，也可以完整重写长期记忆；无论哪种方式，最终都输出整理后的完整长期记忆。
我可以新增、更新或忘记重点记忆；无论怎么调整，最终都输出整理后的完整重点记忆列表。
我不输出短期记忆；系统会在成功写入长期/重点记忆后自动清空短期记忆。
整理完成后，我只返回一个 JSON 对象，系统会把它保存到 `{output_path}`。

## 输出 JSON 契约

```json
{json.dumps(contract, ensure_ascii=False, indent=2)}
```

## 本轮我和对我说话者的对话

{dialogue_text}

{reference_text}

请只根据这些自然语言材料整理记忆，并按“输出 JSON 契约”输出。
"""


def _post_round_objective_job_payload(
    card: Path,
    run_dir: Path,
    story_input: Dict[str, Any],
    agent_id: str,
) -> Dict[str, Any]:
    paths = actor_memory_store.ensure_actor_files(card, agent_id)
    payload = {
        "agent_id": "gm",
        "target_actor_id": agent_id,
        "character_name": paths.name,
        "round_id": str(story_input.get("round_id") or run_dir.name),
        "current_recent": _read_optional_text(paths.objective_recent, limit=20000),
        "objective_profile": _read_optional_text(paths.objective_profile, limit=12000),
        "actor_profile": _read_optional_text(paths.profile, limit=12000),
        "round_events": _actor_visible_events(story_input, agent_id),
    }
    return payload


def _post_round_objective_memory_prompt(
    run_dir: Path,
    agent_id: str,
    output_path: str,
    job_payload: Dict[str, Any],
) -> str:
    character_name = str(job_payload.get("character_name") or "").strip()
    contract = {
        "agent_id": "gm",
        "updates": [
            {
                "character_name": character_name,
                "recent": "完整的 memory/characters/<角色>/recent.md 内容",
                "objective_profile": "可选；完整的 memory/characters/<角色>/profile.md 内容",
                "actor_profile": "可选；完整的 characters/<角色>/profile.md 第一人称内容",
            }
        ],
    }
    return f"""
# GM 角色客观记忆整理

你是 GM agent。你需要在每轮剧情输出后，读取并整理本轮参与剧情角色的上帝视角近期经历与可选设定更新。

这不是 player/character 的个人记忆整理。不要使用第一人称角色自述来代替 GM 判断；也不要把控制面、文件路径、调试信息或前端信息写入任何存档文件。

## 当前角色

- actor id: {agent_id}
- 角色名: {character_name or "未命名"}
- round: {job_payload.get("round_id") or run_dir.name}

## 已有 memory/characters/<角色>/recent.md

{job_payload.get("current_recent") or "暂无。"}

## 已有 memory/characters/<角色>/profile.md

{job_payload.get("objective_profile") or "暂无。"}

## 已有 characters/<角色>/profile.md

{job_payload.get("actor_profile") or "暂无。"}

## 本轮与该角色相关的剧情材料

```json
{json.dumps(job_payload.get("round_events") or [], ensure_ascii=False, indent=2)}
```

## 写回规则

- `recent` 必须是整理后的完整 `memory/characters/<角色>/recent.md` 内容，而不是增量片段。
- 只有角色客观设定确实需要变化时，才输出 `objective_profile`。
- 只有角色第一人称自我介绍确实需要变化时，才输出 `actor_profile`；该字段会写入 `characters/<角色>/profile.md`，必须保持角色第一人称沉浸口吻，不得包含幕后控制面信息。
- 没有变化的 profile 字段可以省略或为空。
- 最终只返回 JSON 对象，系统会保存到 `{output_path}`。

## 输出 JSON 契约

```json
{json.dumps(contract, ensure_ascii=False, indent=2)}
```
"""


def schedule_post_round_memory_jobs(card_folder: str | Path, run_dir: str | Path) -> Dict[str, Any]:
    """Materialize actor-safe post-round memory jobs for actors used this round."""
    card = Path(card_folder)
    root = Path(run_dir)
    story_input = agent_run.read_json(root / "story.input.json")
    if not isinstance(story_input, dict):
        return {"ok": True, "scheduled": []}

    scheduled_agents = _participating_actors(story_input)
    scheduled_entries: Dict[str, Dict[str, str]] = {}
    objective_entries: Dict[str, Dict[str, str]] = {}
    payloads: Dict[str, Dict[str, Any]] = {}
    prompt_texts: Dict[str, str] = {}
    objective_payloads: Dict[str, Dict[str, Any]] = {}
    objective_prompt_texts: Dict[str, str] = {}
    for agent_id in scheduled_agents:
        job_rel = _post_round_job_input_path(agent_id)
        prompt_rel = _post_round_job_prompt_path(agent_id)
        output_rel = _post_round_job_output_path(agent_id)
        payload = _post_round_job_payload(card, root, story_input, agent_id)
        payloads[agent_id] = payload
        prompt_texts[agent_id] = _post_round_memory_prompt(root, agent_id, output_rel, payload)
        scheduled_entries[agent_id] = {
            "job": job_rel,
            "prompt": prompt_rel,
            "output": output_rel,
        }
        objective_job_rel = _post_round_objective_job_input_path(agent_id)
        objective_prompt_rel = _post_round_objective_job_prompt_path(agent_id)
        objective_output_rel = _post_round_objective_job_output_path(agent_id)
        objective_payload = _post_round_objective_job_payload(card, root, story_input, agent_id)
        objective_payloads[agent_id] = objective_payload
        objective_prompt_texts[agent_id] = _post_round_objective_memory_prompt(
            root,
            agent_id,
            objective_output_rel,
            objective_payload,
        )
        objective_entries[agent_id] = {
            "job": objective_job_rel,
            "prompt": objective_prompt_rel,
            "output": objective_output_rel,
        }

    manifest = _read_json(root / "manifest.json", {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("round_id", str(story_input.get("round_id") or root.name))
    manifest["post_round_memory_jobs"] = {
        "status": "pending" if scheduled_entries else "not_required",
        "scheduled": scheduled_entries,
        "failed": {},
    }
    manifest["post_round_objective_memory_jobs"] = {
        "status": "pending" if objective_entries else "not_required",
        "scheduled": objective_entries,
        "failed": {},
    }

    manifest_path = root / "manifest.json"
    artifact_paths: list[Path] = [manifest_path]
    for entry in scheduled_entries.values():
        artifact_paths.append(root / entry["job"])
        artifact_paths.append(root / entry["prompt"])
    for entry in objective_entries.values():
        artifact_paths.append(root / entry["job"])
        artifact_paths.append(root / entry["prompt"])
    snapshots = _snapshot_files(artifact_paths)
    try:
        for agent_id in scheduled_agents:
            job_rel = scheduled_entries[agent_id]["job"]
            prompt_rel = scheduled_entries[agent_id]["prompt"]
            _write_json(root / job_rel, payloads[agent_id])
            _write_text(root / prompt_rel, prompt_texts[agent_id])
            objective_job_rel = objective_entries[agent_id]["job"]
            objective_prompt_rel = objective_entries[agent_id]["prompt"]
            _write_json(root / objective_job_rel, objective_payloads[agent_id])
            _write_text(root / objective_prompt_rel, objective_prompt_texts[agent_id])
        _write_json(manifest_path, manifest)
    except Exception:
        _restore_files(snapshots)
        raise
    return {"ok": True, "scheduled": scheduled_agents}


def _update_post_round_job_status(root: str | Path, status: str, failed: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Update the post-round memory job manifest status without touching prose artifacts."""
    run_dir = Path(root)
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        manifest = {}
    jobs = manifest.get("post_round_memory_jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
    jobs["status"] = status
    if failed is not None:
        jobs["failed"] = dict(failed)
    else:
        jobs.setdefault("failed", {})
    manifest["post_round_memory_jobs"] = jobs
    _write_json(manifest_path, manifest)
    return jobs


def _update_post_round_objective_job_status(root: str | Path, status: str, failed: Dict[str, str] | None = None) -> Dict[str, Any]:
    run_dir = Path(root)
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        manifest = {}
    jobs = manifest.get("post_round_objective_memory_jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
    jobs["status"] = status
    if failed is not None:
        jobs["failed"] = dict(failed)
    else:
        jobs.setdefault("failed", {})
    manifest["post_round_objective_memory_jobs"] = jobs
    _write_json(manifest_path, manifest)
    return jobs


def _post_round_output_path(root: Path, relative_path: Any) -> Path:
    path = Path(str(relative_path or ""))
    return path if path.is_absolute() else root / path


def _validate_post_round_memory_update(payload: Any, expected_agent_id: str, path: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MemoryIngestionError(f"{path.name}: summary payload must be an object")
    declared_agent = str(payload.get("agent_id") or expected_agent_id).strip()
    if declared_agent != expected_agent_id:
        raise MemoryIngestionError(
            f"{path.name}: agent_id mismatch, expected {expected_agent_id}, got {declared_agent}"
        )
    if expected_agent_id.startswith("character:"):
        declared_name = str(payload.get("character_name") or "").strip()
        expected_name = expected_agent_id.split(":", 1)[1]
        if declared_name and declared_name != expected_name:
            raise MemoryIngestionError(
                f"{path.name}: character_name mismatch, expected {expected_name}, got {declared_name}"
            )
    _validate_post_round_actor_safe_payload(payload, path.name)
    try:
        return actor_memory_store.validate_memory_update(payload)
    except Exception as exc:
        raise MemoryIngestionError(f"{path.name}: {exc}") from exc


def _objective_text_field(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise MemoryIngestionError(f"{field} is required")
    if len(text) > limit:
        raise MemoryIngestionError(f"{field} exceeds {limit} characters")
    if actor_memory_store._contains_control_plane_memory_text(text):
        raise MemoryIngestionError(f"{field} contains control-plane text")
    return text


def _validate_post_round_objective_memory_update(
    payload: Any,
    expected_agent_id: str,
    expected_name: str,
    path: Path,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MemoryIngestionError(f"{path.name}: objective summary payload must be an object")
    declared_agent = str(payload.get("agent_id") or "").strip()
    if declared_agent != "gm":
        raise MemoryIngestionError(f"{path.name}: agent_id must be gm")
    updates = payload.get("updates")
    if not isinstance(updates, list) or not updates:
        raise MemoryIngestionError(f"{path.name}: updates must be a non-empty list")
    normalized: dict[str, str] | None = None
    for index, item in enumerate(updates):
        if not isinstance(item, dict):
            raise MemoryIngestionError(f"{path.name}: updates[{index}] must be an object")
        character_name = str(item.get("character_name") or "").strip()
        if character_name and character_name != expected_name:
            continue
        recent = _objective_text_field(item.get("recent"), "recent", 20000, required=True)
        objective_profile = _objective_text_field(item.get("objective_profile"), "objective_profile", 12000)
        actor_profile = _objective_text_field(item.get("actor_profile"), "actor_profile", 12000)
        if actor_profile:
            _validate_post_round_actor_safe_payload(actor_profile, f"{path.name}.updates[{index}].actor_profile")
        normalized = {
            "character_name": character_name or expected_name,
            "recent": recent,
            "objective_profile": objective_profile,
            "actor_profile": actor_profile,
        }
        break
    if normalized is None:
        raise MemoryIngestionError(f"{path.name}: no update matched {expected_agent_id}")
    return normalized


def _validate_post_round_objective_memory_update_for_card(
    card: Path,
    payload: Any,
    expected_agent_id: str,
    path: Path,
) -> dict[str, Any]:
    expected_name = actor_memory_store.actor_paths(card, expected_agent_id).name
    update = _validate_post_round_objective_memory_update(payload, expected_agent_id, expected_name, path)
    if update["character_name"] != expected_name:
        raise MemoryIngestionError(f"{path.name}: character_name mismatch, expected {expected_name}")
    return update


def _apply_objective_memory_update(card: Path, expected_agent_id: str, update: dict[str, str]) -> None:
    paths = actor_memory_store.ensure_actor_files(card, expected_agent_id)
    _write_text(paths.objective_recent, update["recent"])
    if update.get("objective_profile"):
        _write_text(paths.objective_profile, update["objective_profile"])
    if update.get("actor_profile"):
        _write_text(paths.profile, update["actor_profile"])


def ingest_post_round_memory_jobs(card_folder: str | Path, run_dir: str | Path) -> Dict[str, Any]:
    """Persist completed `post_round_memory_jobs/*.summary.json` outputs into actor memory."""
    card = Path(card_folder)
    root = Path(run_dir)
    manifest = _read_json(root / "manifest.json", {})
    if not isinstance(manifest, dict):
        _update_post_round_job_status(root, "not_required", failed={})
        _update_post_round_objective_job_status(root, "not_required", failed={})
        return {
            "ok": True,
            "status": "not_required",
            "round_id": root.name,
            "ingested": [],
            "missing": {},
            "failed": {},
        }

    jobs = manifest.get("post_round_memory_jobs")
    if not isinstance(jobs, dict):
        _update_post_round_job_status(root, "not_required", failed={})
        _update_post_round_objective_job_status(root, "not_required", failed={})
        return {
            "ok": True,
            "status": "not_required",
            "round_id": root.name,
            "ingested": [],
            "missing": {},
            "failed": {},
        }

    scheduled_raw = jobs.get("scheduled")
    if not isinstance(scheduled_raw, dict) or not scheduled_raw:
        _update_post_round_job_status(root, "not_required", failed={})
        _update_post_round_objective_job_status(root, "not_required", failed={})
        return {
            "ok": True,
            "status": "not_required",
            "round_id": root.name,
            "ingested": [],
            "missing": {},
            "failed": {},
        }

    scheduled: Dict[str, tuple[Path, str]] = {}
    objective_scheduled: Dict[str, tuple[Path, str]] = {}
    failed: Dict[str, str] = {}
    for raw_agent_id, entry in scheduled_raw.items():
        agent_id = str(raw_agent_id or "").strip()
        if not agent_id:
            continue
        if not isinstance(entry, dict):
            failed[agent_id] = "post_round_memory_jobs scheduled entry must be an object"
            continue
        output_rel = entry.get("output")
        if not isinstance(output_rel, str) or not output_rel.strip():
            failed[agent_id] = "post_round_memory_jobs scheduled output path is required"
            continue
        scheduled[agent_id] = (_post_round_output_path(root, output_rel), output_rel)

    objective_jobs = manifest.get("post_round_objective_memory_jobs")
    objective_scheduled_raw = objective_jobs.get("scheduled") if isinstance(objective_jobs, dict) else {}
    if isinstance(objective_scheduled_raw, dict):
        for raw_agent_id, entry in objective_scheduled_raw.items():
            agent_id = str(raw_agent_id or "").strip()
            if not agent_id:
                continue
            if not isinstance(entry, dict):
                failed[f"objective:{agent_id}"] = "post_round_objective_memory_jobs scheduled entry must be an object"
                continue
            output_rel = entry.get("output")
            if not isinstance(output_rel, str) or not output_rel.strip():
                failed[f"objective:{agent_id}"] = "post_round_objective_memory_jobs scheduled output path is required"
                continue
            objective_scheduled[agent_id] = (_post_round_output_path(root, output_rel), output_rel)

    ingested: list[str] = []
    missing: Dict[str, str] = {}
    for expected_agent_id, (path, output_rel) in sorted(scheduled.items(), key=lambda item: item[0]):
        if not path.exists():
            missing[expected_agent_id] = output_rel
            continue
        try:
            payload = _read_json(path, {})
            update = _validate_post_round_memory_update(payload, expected_agent_id, path)
            actor_memory_store.apply_memory_update(card, expected_agent_id, update)
            ingested.append(expected_agent_id)
        except Exception as exc:
            failed[expected_agent_id] = str(exc)

    for expected_agent_id, (path, output_rel) in sorted(objective_scheduled.items(), key=lambda item: item[0]):
        if not path.exists():
            missing[f"objective:{expected_agent_id}"] = output_rel
            continue
        try:
            payload = _read_json(path, {})
            update = _validate_post_round_objective_memory_update_for_card(
                card,
                payload,
                expected_agent_id,
                path,
            )
            _apply_objective_memory_update(card, expected_agent_id, update)
        except Exception as exc:
            failed[f"objective:{expected_agent_id}"] = str(exc)

    if failed:
        _update_post_round_job_status(root, "degraded_memory_state", failed=failed)
        _update_post_round_objective_job_status(root, "degraded_memory_state", failed=failed)
        return {
            "ok": False,
            "status": "degraded_memory_state",
            "round_id": root.name,
            "ingested": ingested,
            "missing": missing,
            "failed": failed,
        }
    if missing:
        _update_post_round_job_status(root, "pending", failed={})
        _update_post_round_objective_job_status(root, "pending", failed={})
        return {
            "ok": False,
            "status": "pending",
            "round_id": root.name,
            "ingested": ingested,
            "missing": missing,
            "failed": {},
        }

    _update_post_round_job_status(root, "complete", failed={})
    _update_post_round_objective_job_status(root, "complete", failed={})
    return {
        "ok": True,
        "status": "complete",
        "round_id": root.name,
        "ingested": ingested,
        "missing": {},
        "failed": {},
    }


def previous_post_round_memory_state(card_folder: str | Path) -> Dict[str, Any]:
    """Return the latest pending/degraded post-round memory state from previous runs."""
    runs_root = Path(card_folder) / ".agent_runs"
    if not runs_root.exists():
        return {}

    for run_dir in sorted(runs_root.glob("round-*"), key=lambda path: path.name, reverse=True):
        if not SUMMARY_ROUND_RE.match(run_dir.name):
            continue
        manifest = _read_json(run_dir / "manifest.json", {})
        if not isinstance(manifest, dict):
            continue
        jobs = manifest.get("post_round_memory_jobs", {})
        if not isinstance(jobs, dict):
            continue
        status = jobs.get("status")
        if status not in {"pending", "degraded_memory_state"}:
            continue
        scheduled = jobs.get("scheduled", {})
        failed = jobs.get("failed", {})
        return {
            "previous_round_id": str(manifest.get("round_id") or run_dir.name),
            "status": status,
            "scheduled": scheduled if isinstance(scheduled, dict) else {},
            "failed": failed if isinstance(failed, dict) else {},
        }
    return {}


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
