#!/usr/bin/env python3
"""Deterministically drive one prepared RP round through Claude Code subagents."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict

import agent_outputs
import agent_prompts
import agent_run
import actor_memory_store
import agent_memory
import agent_schemas
import input_analysis_apply
import llm_runner
import model_debug
import projection_agent

try:
    from handler import write_progress
except Exception:
    def write_progress(stage, label, percent=None, detail=None):
        return {"stage": stage, "label": label, "percent": percent, "detail": detail or {}}


def _write_progress_safe(stage, label, percent=None, detail=None):
    try:
        return write_progress(stage, label, percent=percent, detail=detail)
    except Exception:
        return None


class AgentExecutionError(RuntimeError):
    """Raised when a Claude Code subagent run is missing, invalid, or unusable."""


_INPUT_ANALYSIS_APPLY_ALLOWED_STAGES = {
    "",
    "prepared",
    "prompts_ready",
    "awaiting_agent_outputs",
    "analysis_applied",
}


def _text_from_blocks(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(part for part in parts if part).strip()


def extract_agent_text(stream_text: str) -> str:
    """Extract the completed local-agent text from Claude Code stream-json output."""
    saw_local_agent = False
    tool_result_text = ""
    final_result_text = ""

    for raw_line in str(stream_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue

        if item.get("type") == "system" and item.get("subtype") == "task_started":
            if item.get("task_type") == "local_agent":
                saw_local_agent = True

        tool_use_result = item.get("tool_use_result")
        if isinstance(tool_use_result, dict) and tool_use_result.get("status") == "completed":
            content_text = _text_from_blocks(tool_use_result.get("content"))
            if content_text:
                tool_result_text = content_text

        if item.get("type") == "result" and item.get("subtype") == "success":
            result_text = item.get("result")
            if isinstance(result_text, str):
                final_result_text = result_text.strip()

    if not saw_local_agent:
        raise AgentExecutionError("Claude Code stream did not include a local_agent task.")
    result = tool_result_text or final_result_text
    if not result:
        raise AgentExecutionError("Claude Code local_agent task returned no text.")
    return result


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        repaired = _repair_unescaped_string_quotes(raw)
        if repaired != raw:
            try:
                payload = json.loads(repaired)
            except json.JSONDecodeError:
                payload = None
            else:
                if isinstance(payload, dict):
                    return payload
        if raw.lstrip().startswith("{") and exc.msg != "Extra data":
            raise AgentExecutionError(f"Agent returned invalid JSON: {exc}") from exc
        decoder = json.JSONDecoder()
        payload = None
        for match in re.finditer(r"\{", raw):
            try:
                candidate, _ = decoder.raw_decode(raw[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            if "{" not in raw:
                raise AgentExecutionError(f"Agent returned non-JSON text: {raw[:200]}") from exc
            raise AgentExecutionError(f"Agent returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentExecutionError("Agent JSON result must be an object.")
    return payload


def _repair_unescaped_string_quotes(raw: str) -> str:
    """Escape ASCII quotes that appear inside JSON string values.

    This deliberately does not repair structural JSON errors.  A quote inside a
    string is considered structural only when the next non-space token can end a
    JSON string/key (`:`, `,`, `}`, `]`, or EOF).  Otherwise it is preserved as
    string content by escaping it.
    """

    text = str(raw or "")
    if not text:
        return text
    result: list[str] = []
    in_string = False
    escaped = False
    length = len(text)
    for index, char in enumerate(text):
        if escaped:
            result.append(char)
            escaped = False
            continue
        if in_string and char == "\\":
            result.append(char)
            escaped = True
            continue
        if char != '"':
            result.append(char)
            continue
        if not in_string:
            result.append(char)
            in_string = True
            continue

        lookahead = index + 1
        while lookahead < length and text[lookahead].isspace():
            lookahead += 1
        next_char = text[lookahead] if lookahead < length else ""
        if next_char in {"", ":", ",", "}", "]"}:
            result.append(char)
            in_string = False
        else:
            result.append('\\"')
    return "".join(result)


def _repair_json_string_controls(raw: str) -> str:
    text = str(raw or "")
    if not text:
        return text
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if in_string and char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            result.append(char)
            in_string = not in_string
            continue
        if in_string and char == "\n":
            result.append("\\n")
            continue
        if in_string and char == "\r":
            result.append("\\r")
            continue
        if in_string and char == "\t":
            result.append("\\t")
            continue
        result.append(char)
    return "".join(result)


def _loads_json_relaxed(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return None
    attempts = [text]
    repaired_controls = _repair_json_string_controls(text)
    if repaired_controls != text:
        attempts.append(repaired_controls)
    for item in list(attempts):
        repaired_quotes = _repair_unescaped_string_quotes(item)
        if repaired_quotes != item:
            attempts.append(repaired_quotes)
    seen = set()
    for item in attempts:
        if item in seen:
            continue
        seen.add(item)
        try:
            return json.loads(item)
        except json.JSONDecodeError:
            continue
    return None


def _with_attempt_rejection_feedback(prompt: str, error: AgentExecutionError | None) -> str:
    if error is None:
        return prompt
    return (
        prompt.rstrip()
        + "\n\n## Previous Attempt Rejection\n\n"
        + f"The previous response was rejected: {error}\n"
        + "Return a corrected artifact as exactly one valid JSON object. "
        + "Escape every ASCII double quote inside string values as `\\\"`, or use Chinese corner quotes such as `「」` "
        + "for dialogue inside content strings. Do not return prose, Markdown explanation, or partial JSON.\n"
    )


def _strip_markdown_json_fence(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _decode_json_field_value(raw: str, key: str, *, start: int = 0) -> Any:
    marker = f'"{key}"'
    pos = raw.find(marker, max(0, start))
    if pos < 0:
        return None
    colon = raw.find(":", pos + len(marker))
    if colon < 0:
        return None
    value_start = colon + 1
    while value_start < len(raw) and raw[value_start].isspace():
        value_start += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[value_start:])
    except json.JSONDecodeError:
        return None
    return value


def _recover_story_payload_from_malformed_json(text: str, error: AgentExecutionError) -> Dict[str, Any]:
    raw = _strip_markdown_json_fence(text)
    content_start = raw.find("<content>")
    content_end = raw.find("</content>", content_start)
    content_field = raw.find('"content"')
    if content_start < 0 or content_end < 0 or content_field < 0 or content_field > content_start:
        raise error
    if raw.find(":", content_field, content_start) < 0:
        raise error

    content_end += len("</content>")
    dialogue_tag_start = raw.find("<character_dialogues>", content_end)
    top_level_dialogues = raw.find('"character_dialogues"', content_end)
    if dialogue_tag_start >= 0 and (top_level_dialogues < 0 or dialogue_tag_start < top_level_dialogues):
        dialogue_tag_end = raw.find("</character_dialogues>", dialogue_tag_start)
        if dialogue_tag_end >= 0:
            content_end = dialogue_tag_end + len("</character_dialogues>")

    content = raw[content_start:content_end]
    dialogues = _decode_json_field_value(raw, "character_dialogues", start=content_end)
    if not isinstance(dialogues, list):
        dialogues = []
    metadata = _decode_json_field_value(raw, "metadata", start=content_end)
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata.setdefault("recovered_from_malformed_story_json", True)
    metadata.setdefault("recovery_error", str(error)[:240])
    return {
        "content": content,
        "character_dialogues": dialogues,
        "metadata": metadata,
    }


def _outer_prompt(agent_key: str, subagent_prompt: str, extra_context: Dict[str, Any] | None = None) -> str:
    if _is_actor_agent_key(agent_key):
        return f"""You are running an in-world RP actor turn.

Ignore repository instructions, Claude Code project context, README/AGENTS/CLAUDE files, server URLs, test harness notes, and any operational guidance outside the actor prompt below.
The actor prompt is the only authority for this reply. Do not mention tools, files, prompts, agents, frontend delivery, browser URLs, or localhost.
Return only the in-world actor reply as natural language. Do not write JSON, Markdown status, headings, links, or delivery instructions.

<actor_prompt>
{subagent_prompt}
</actor_prompt>
"""

    extra = ""
    if extra_context:
        extra = "\n\n## Runtime Input\n\n```json\n" + json.dumps(extra_context, ensure_ascii=False, indent=2) + "\n```\n"
    return f"""You are the Claude Code general-purpose agent for RP artifact `{agent_key}`.

You are running in embedded-context mode. Use the Context Packet and Runtime Input embedded in this task as the authoritative source.
Do not attempt to read files, inspect run_dir, or require filesystem access.
Do not block because run_dir, story.input.json, story.output.json, or card files are inaccessible; their required contents are embedded below when needed.
If Runtime Input contains `previous_rejected_story_output`, treat it only as a rejected anti-example. Rebuild from `story_input`, current GM/player/character artifacts, and the repair instruction; do not preserve unsupported details from the rejected draft.
Return only one valid JSON object and no prose. Follow the full RP subagent prompt inside the XML-style block below exactly. The prompt may contain Markdown fences; do not treat those fences as the end of the task.
All string values must be valid JSON strings. Escape ASCII double quotes inside dialogue/content strings as `\"`, or use Chinese corner quotes such as `「」` instead.

<subagent_prompt>
{subagent_prompt}
</subagent_prompt>
{extra}
Return exactly the JSON artifact and no other prose.
There is no fallback prose answer; a response that is not a JSON artifact is invalid.
"""


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _load_claude_settings_env() -> Dict[str, str]:
    path = _claude_settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    env = payload.get("env") if isinstance(payload, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items() if value is not None}


def _claude_subprocess_env() -> Dict[str, str]:
    merged = dict(os.environ)
    merged.update(_load_claude_settings_env())
    return merged


def _claude_subagent_cwd() -> Path:
    path = Path(tempfile.gettempdir()) / "airp_claude_code_subagent_cwd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_claude_agent(agent_key: str, prompt: str, cwd: str | Path) -> str:
    """Run Claude Code in print mode with the general-purpose agent."""
    command = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--agent",
        "general-purpose",
    ]
    result = _run_claude_process(command, prompt, cwd)
    _raise_for_claude_failure(result)
    return result.stdout


def _run_claude_process(command: list[str], prompt: str, cwd: str | Path) -> Any:
    return subprocess.run(
        command,
        input=prompt,
        cwd=str(_claude_subagent_cwd()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_claude_subprocess_env(),
        timeout=600,
    )


def _raise_for_claude_failure(result: Any) -> None:
    if result.returncode != 0:
        stderr_tail = str(result.stderr or "").strip()[-500:]
        stdout_tail = str(result.stdout or "").strip()[-500:]
        if stderr_tail:
            output_label = "stderr tail"
            output_tail = stderr_tail
        elif stdout_tail:
            output_label = "stdout tail"
            output_tail = stdout_tail
        else:
            output_label = "output"
            output_tail = "no output"
        raise AgentExecutionError(f"claude exited with {result.returncode} ({output_label}): {output_tail}")


def _run_claude_with_debug(
    logger: model_debug.ModelDebugLogger | None,
    run_claude: Callable[[str, str, str | Path], str],
    agent_key: str,
    prompt: str,
    cwd: str | Path,
) -> str:
    if logger is None:
        return run_claude(agent_key, prompt, cwd)

    started = model_debug.utc_now()
    stdout = ""
    stderr = ""
    returncode: int | None = None
    error = ""
    exception_type = ""
    api_metadata: Dict[str, Any] = {}
    try:
        if run_claude is run_claude_agent:
            command = [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--agent",
                "general-purpose",
            ]
            result = _run_claude_process(command, prompt, cwd)
            stdout = str(getattr(result, "stdout", "") or "")
            stderr = str(getattr(result, "stderr", "") or "")
            returncode = int(getattr(result, "returncode", 1))
            _raise_for_claude_failure(result)
            return stdout

        stdout = str(run_claude(agent_key, prompt, cwd) or "")
        returncode = 0
        return stdout
    except Exception as exc:
        error = str(exc)
        exception_type = exc.__class__.__name__
        raise
    finally:
        ended = model_debug.utc_now()
        if run_claude is llm_runner.run_llm_agent:
            try:
                last_result = llm_runner.get_last_result()
            except Exception:
                last_result = None
            if isinstance(last_result, dict):
                api_metadata = {
                    key: last_result[key]
                    for key in ("provider", "model", "status", "usage", "raw_response")
                    if key in last_result
                }
                preview = str(last_result.get("text") or stdout or "").strip()
                if preview:
                    api_metadata["response_preview"] = preview[:500]
        try:
            logger.write_call(
                agent_key=agent_key,
                cwd=str(cwd),
                prompt=prompt,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
                started_at=model_debug.isoformat(started),
                ended_at=model_debug.isoformat(ended),
                duration_ms=model_debug.duration_ms(started, ended),
                error=error,
                exception_type=exception_type,
                api_metadata=api_metadata,
            )
        except Exception:
            if not exception_type:
                raise


def _extract_agent_or_direct_text(output: str) -> str:
    try:
        return extract_agent_text(output)
    except AgentExecutionError:
        text = str(output or "").strip()
        if not text:
            raise
        return text


def _load_manifest(run_dir: Path) -> Dict[str, Any]:
    manifest = agent_run.read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise AgentExecutionError(f"{run_dir / 'manifest.json'} is missing or invalid.")
    return manifest


def _relative_path(run_dir: Path, value: str, default: str) -> Path:
    relative = value or default
    return run_dir / relative


def _artifact_output_path(run_dir: Path, value: str, default: str) -> Path:
    relative = Path(str(value or default))
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise AgentExecutionError(f"{value or default}: output artifact path must be run-relative")
    if relative.parts and relative.parts[0] == "artifacts":
        return run_dir / relative
    return run_dir / "artifacts" / relative


def _read_prompt(run_dir: Path, manifest: Dict[str, Any], key: str) -> str:
    prompts = manifest.get("prompts") or {}
    if not isinstance(prompts, dict):
        raise AgentExecutionError("manifest.prompts is required.")
    prompt_path = _relative_path(run_dir, str(prompts.get(key) or f"prompts/{key}.prompt.md"), f"prompts/{key}.prompt.md")
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentExecutionError(f"{prompt_path}: prompt is missing.") from exc


def _is_actor_agent_key(agent_key: str) -> bool:
    return agent_key == "player" or agent_key.startswith("character:")


def _is_post_round_memory_agent(agent_key: str) -> bool:
    return agent_key == "post_round_memory" or agent_key.startswith("post_round_memory:")


def _is_post_round_objective_memory_agent(agent_key: str) -> bool:
    return agent_key == "post_round_objective_memory" or agent_key.startswith("post_round_objective_memory:")


def _actor_protocol_identity(agent_key: str, extra_context: Dict[str, Any] | None) -> tuple[str, Path | None]:
    context = extra_context if isinstance(extra_context, dict) else {}
    if _is_actor_agent_key(agent_key):
        packet = context.get("loop_packet")
        packet = packet if isinstance(packet, dict) else {}
        actor_id = str(packet.get("actor_id") or agent_key).strip() or agent_key
        card_folder = str(packet.get("card_folder") or context.get("card_folder") or "").strip()
        return actor_id, Path(card_folder) if card_folder else None
    if _is_post_round_memory_agent(agent_key):
        job = context.get("post_round_memory_job")
        job = job if isinstance(job, dict) else {}
        actor_id = str(job.get("agent_id") or context.get("actor_id") or "").strip()
        card_folder = str(context.get("card_folder") or "").strip()
        return actor_id, Path(card_folder) if card_folder else None
    return "", None


def _recall_protocol_query(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(actor_memory_store.RECALL_PREFIX):
            return actor_memory_store._normalize_recall_query(stripped)
        return ""
    return ""


def _actor_protocol_query_from_payload(payload: Dict[str, Any]) -> str:
    return _recall_protocol_query(payload.get("natural_reply"))


def _format_recalled_memory(query: str, memory: Dict[str, str]) -> str:
    tag = str(memory.get("tag") or "").strip()
    summary = str(memory.get("summary") or "").strip()
    detail = str(memory.get("detail") or "").strip()
    if not (tag or summary or detail):
        return f"我试着回忆“{query}”，但没有想起更清晰的内容。"
    lines = [f"我刚刚回忆起：{tag or query}"]
    if summary:
        lines.append(f"摘要：{summary}")
    if detail:
        lines.append(f"详情：{detail}")
    return "\n".join(lines)


def _run_actor_protocol_tool(
    agent_key: str,
    query: str,
    extra_context: Dict[str, Any] | None,
) -> str:
    actor_id, card_folder = _actor_protocol_identity(agent_key, extra_context)
    if not actor_id or card_folder is None:
        return f"我试着回忆“{query}”，但这里没有可用的记忆档案。"
    memory = actor_memory_store.recall_key_memory(card_folder, actor_id, query)
    return _format_recalled_memory(query, memory)


def _inject_actor_protocol_results(prompt_text: str, tool_results: list[str]) -> str:
    if not tool_results:
        return prompt_text
    sections = [
        "## 我刚刚想起的重点记忆",
        "",
        "\n\n".join(item for item in tool_results if item).strip() or "暂无。",
        "",
        "请把这些刚刚想起的内容当作我现在已经回忆起来的第一人称记忆，然后继续完成当前任务。",
    ]
    return prompt_text.rstrip() + "\n\n" + "\n".join(sections).strip() + "\n"


def _unwrap_payload(agent_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    wrapper = ""
    if agent_key == "gm":
        wrapper = "gm_output"
    elif _is_actor_agent_key(agent_key):
        wrapper = "actor_output"
    elif agent_key.startswith("subGM:"):
        wrapper = "subgm_output"
    elif agent_key == "story":
        wrapper = "story_output"
    elif agent_key == "critic":
        wrapper = "critic_report"
    elif agent_key == "postprocess":
        wrapper = "postprocess_output"
    elif agent_key == "projection":
        wrapper = "projection_output"
    elif _is_post_round_memory_agent(agent_key):
        wrapper = "post_round_memory"
    elif _is_post_round_objective_memory_agent(agent_key):
        wrapper = "post_round_objective_memory"

    nested = payload.get(wrapper) if wrapper else None
    if isinstance(nested, dict):
        return nested
    return payload


def _normalize_world_state_delta(items: list[Any]) -> list[Any]:
    normalized = []
    for item in items:
        if isinstance(item, dict):
            if item.get("fact"):
                normalized.append(item)
                continue
            fact = item.get("value") or item.get("description") or item.get("event") or item.get("text") or ""
            scope = item.get("scope") or item.get("path") or item.get("target") or "world"
            if str(fact).strip():
                normalized.append({"scope": str(scope), "fact": str(fact)})
            continue
        if str(item).strip():
            normalized.append({"scope": "world", "fact": str(item).strip()})
    return normalized


def _projection_validation_identity(validation_context: Dict[str, Any] | None) -> tuple[str, str]:
    context = validation_context if isinstance(validation_context, dict) else {}
    packet = context.get("projection_packet")
    source = packet if isinstance(packet, dict) else context
    actor_id = str(source.get("target_actor_id") or "").strip()
    source_call_id = str(source.get("source_call_id") or "").strip()
    if not actor_id or not source_call_id:
        raise AgentExecutionError("projection validation context requires target_actor_id and source_call_id")
    return actor_id, source_call_id


def _validate(
    agent_key: str,
    payload: Any,
    validation_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if _is_actor_agent_key(agent_key) and isinstance(payload, str):
        context = validation_context if isinstance(validation_context, dict) else {}
        packet = context.get("loop_packet")
        packet = packet if isinstance(packet, dict) else {}
        character_name = str(packet.get("character_name") or "").strip()
        if not character_name and agent_key.startswith("character:"):
            character_name = agent_key.split(":", 1)[1]
        try:
            return agent_schemas.natural_actor_output(agent_key, payload, character_name)
        except agent_schemas.ValidationError as exc:
            raise AgentExecutionError(f"{agent_key} returned invalid artifact: {exc}") from exc

    payload = _unwrap_payload(agent_key, payload)
    try:
        if agent_key == "gm":
            normalized = agent_schemas.validate_gm_output(payload)
            normalized["world_state_delta"] = _normalize_world_state_delta(normalized.get("world_state_delta", []))
            return normalized
        if _is_actor_agent_key(agent_key):
            normalized = agent_schemas.validate_actor_output(payload)
            if agent_key == "player" and normalized.get("agent_id") != "player":
                raise AgentExecutionError(f"{agent_key} returned wrong agent_id: {normalized.get('agent_id')!r}")
            if agent_key.startswith("character:"):
                if normalized.get("agent") != "character":
                    raise AgentExecutionError(f"{agent_key} returned wrong agent: {normalized.get('agent')!r}")
                if normalized.get("agent_id") != agent_key:
                    raise AgentExecutionError(f"{agent_key} returned wrong agent_id: {normalized.get('agent_id')!r}")
            return normalized
        if agent_key.startswith("subGM:"):
            normalized = agent_schemas.validate_subgm_output(payload)
            expected_thread_id = agent_key.split(":", 1)[1]
            if normalized.get("thread_id") != expected_thread_id:
                raise AgentExecutionError(
                    f"{agent_key} returned wrong thread_id: {normalized.get('thread_id')!r}"
                )
            return normalized
        if agent_key == "story":
            source_metadata = dict(payload.get("metadata")) if isinstance(payload.get("metadata"), dict) else {}
            normalized = agent_schemas.validate_story_output(payload)
            recovered = {
                key: source_metadata[key]
                for key in ("recovered_from_malformed_story_json", "recovery_error")
                if key in source_metadata
            }
            if recovered:
                metadata = normalized.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata.update(recovered)
                normalized["metadata"] = metadata
            return normalized
        if agent_key == "critic":
            return agent_schemas.validate_critic_report(payload)
        if agent_key == "postprocess":
            return payload
        if agent_key == "input_analyst":
            return payload
        if agent_key == "projection":
            actor_id, source_call_id = _projection_validation_identity(validation_context)
            return projection_agent.validate_projection_output(
                payload,
                actor_id=actor_id,
                source_call_id=source_call_id,
            )
        if _is_post_round_memory_agent(agent_key):
            context = validation_context if isinstance(validation_context, dict) else {}
            job = context.get("post_round_memory_job")
            job = job if isinstance(job, dict) else {}
            expected_agent_id = str(job.get("agent_id") or context.get("actor_id") or "").strip()
            if not expected_agent_id:
                raise AgentExecutionError("post_round_memory validation requires agent_id")
            path = Path(str(context.get("post_round_output_path") or "post_round_memory.summary.json"))
            update = agent_memory._validate_post_round_memory_update(payload, expected_agent_id, path)
            normalized = {"agent_id": expected_agent_id, **update}
            character_name = str(payload.get("character_name") or job.get("character_name") or "").strip()
            if character_name:
                normalized["character_name"] = character_name
            return normalized
        if _is_post_round_objective_memory_agent(agent_key):
            context = validation_context if isinstance(validation_context, dict) else {}
            job = context.get("post_round_objective_memory_job")
            job = job if isinstance(job, dict) else {}
            expected_agent_id = str(job.get("target_actor_id") or context.get("actor_id") or "").strip()
            if not expected_agent_id:
                raise AgentExecutionError("post_round_objective_memory validation requires target_actor_id")
            card_folder = Path(str(context.get("card_folder") or "."))
            path = Path(str(context.get("post_round_output_path") or "post_round_objective_memory.summary.json"))
            update = agent_memory._validate_post_round_objective_memory_update_for_card(
                card_folder,
                payload,
                expected_agent_id,
                path,
            )
            return {"agent_id": "gm", "updates": [update]}
    except agent_schemas.ValidationError as exc:
        raise AgentExecutionError(f"{agent_key} returned invalid artifact: {exc}") from exc
    except projection_agent.ProjectionValidationError as exc:
        raise AgentExecutionError(f"{agent_key} returned invalid artifact: {exc}") from exc
    except agent_memory.MemoryIngestionError as exc:
        raise AgentExecutionError(f"{agent_key} returned invalid artifact: {exc}") from exc
    raise AgentExecutionError(f"Unknown agent key: {agent_key}")


def _dispatch_agent_payload(
    agent_key: str,
    prompt_text: str,
    cwd: Path,
    run_claude: Callable[[str, str, str | Path], str],
    extra_context: Dict[str, Any] | None = None,
    attempts: int = 2,
    initial_error: AgentExecutionError | None = None,
) -> Dict[str, Any]:
    last_error: AgentExecutionError | None = initial_error
    attempts = max(1, int(attempts or 1))
    protocol_enabled = _is_actor_agent_key(agent_key) or _is_post_round_memory_agent(agent_key)
    max_protocol_iterations = 4
    for attempt in range(attempts):
        tool_results: list[str] = []
        try:
            for protocol_iteration in range(max_protocol_iterations + 1):
                prompt_body = _inject_actor_protocol_results(prompt_text, tool_results)
                prompt = _with_attempt_rejection_feedback(
                    _outer_prompt(agent_key, prompt_body, extra_context),
                    last_error,
                )
                stream = run_claude(agent_key, prompt, cwd)
                text = _extract_agent_or_direct_text(stream)
                try:
                    payload = _extract_json_object(text)
                except AgentExecutionError as exc:
                    protocol_query = _recall_protocol_query(text) if protocol_enabled else ""
                    if protocol_query:
                        if protocol_iteration >= max_protocol_iterations:
                            raise AgentExecutionError(
                                f"{agent_key} kept invoking actor protocol after {max_protocol_iterations} iterations"
                            ) from exc
                        tool_results.append(_run_actor_protocol_tool(agent_key, protocol_query, extra_context))
                        continue
                    if _is_actor_agent_key(agent_key):
                        return _validate(agent_key, text, extra_context)
                    if agent_key != "story":
                        raise
                    payload = _recover_story_payload_from_malformed_json(text, exc)
                normalized = _validate(agent_key, payload, extra_context)
                protocol_query = (
                    _actor_protocol_query_from_payload(normalized)
                    if protocol_enabled and _is_actor_agent_key(agent_key)
                    else ""
                )
                if protocol_query:
                    if protocol_iteration >= max_protocol_iterations:
                        raise AgentExecutionError(
                            f"{agent_key} kept invoking actor protocol after {max_protocol_iterations} iterations"
                        )
                    tool_results.append(_run_actor_protocol_tool(agent_key, protocol_query, extra_context))
                    continue
                return normalized
        except AgentExecutionError as exc:
            last_error = exc
            if attempt == attempts - 1:
                raise
    raise last_error or AgentExecutionError("Claude Code local_agent task did not complete.")


def _dispatch_and_write(
    agent_key: str,
    output_path: Path,
    prompt_text: str,
    cwd: Path,
    run_claude: Callable[[str, str, str | Path], str],
    extra_context: Dict[str, Any] | None = None,
    attempts: int = 2,
    initial_error: AgentExecutionError | None = None,
) -> Dict[str, Any]:
    normalized = _dispatch_agent_payload(
        agent_key,
        prompt_text,
        cwd,
        run_claude,
        extra_context=extra_context,
        attempts=attempts,
        initial_error=initial_error,
    )
    agent_run.write_json(output_path, normalized)
    return normalized


def _read_loop_prompt(
    run_dir: Path,
    manifest: Dict[str, Any],
    agent_key: str,
    packet: Dict[str, Any] | None = None,
) -> str:
    if agent_key == "gm":
        return _read_prompt(run_dir, manifest, agent_key)
    if agent_key == "player":
        if packet is not None:
            return agent_prompts.player_prompt_text(packet)
        return _read_prompt(run_dir, manifest, agent_key)
    if agent_key == "projection":
        return agent_prompts.projection_prompt_text(packet or {})
    if agent_key.startswith("subGM:"):
        return agent_prompts.subgm_prompt_text(packet or {})
    if not agent_key.startswith("character:"):
        raise AgentExecutionError(f"Unknown loop agent key: {agent_key}")

    if packet is not None:
        return agent_prompts.character_prompt_text(packet)

    prompts = manifest.get("prompts") or {}
    character_prompts = prompts.get("characters") if isinstance(prompts, dict) else None
    if not isinstance(character_prompts, dict):
        raise AgentExecutionError("manifest.prompts.characters is required.")
    actor_name = agent_key.split(":", 1)[1]
    prompt_rel = character_prompts.get(actor_name) or character_prompts.get(agent_run.safe_name(actor_name))
    if not isinstance(prompt_rel, str) or not prompt_rel:
        raise AgentExecutionError(f"Missing prompt path for {agent_key}.")
    prompt_path = run_dir / prompt_rel
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentExecutionError(f"{prompt_path}: prompt is missing.") from exc


def _run_delivery(card_folder: Path, root: Path, run_command: Callable[..., Any]) -> Dict[str, Any]:
    command = [sys.executable, str(root / "skills" / "round_deliver.py"), str(card_folder), str(root)]
    result = run_command(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    stdout = str(getattr(result, "stdout", "") or "").strip()
    parsed: Dict[str, Any] = {}
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            parsed = item
            break
    return {
        "ok": getattr(result, "returncode", 1) == 0,
        "returncode": getattr(result, "returncode", 1),
        "stdout": stdout[-1000:],
        "stderr": str(getattr(result, "stderr", "") or "")[-1000:],
        "result": parsed,
    }


def _delivery_retry_decision(delivery_result: Dict[str, Any]) -> str:
    detail = delivery_result.get("detail")
    if isinstance(detail, dict):
        decision = str(detail.get("decision") or "")
        if decision in {"revise", "block"}:
            return decision
    reason = str(delivery_result.get("reason") or "")
    return "block" if "block" in reason else "revise"


def _reset_round_progression_outputs(run_dir: Path) -> None:
    artifact_names = [
        "gm.output.json",
        "actor.outputs.json",
        "interaction.trace.json",
        "story.input.json",
        "story.output.json",
        "critic.report.json",
    ]
    for base_dir in (run_dir, run_dir / "artifacts"):
        for name in artifact_names:
            try:
                (base_dir / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    for name in ["side_threads"]:
        path = run_dir / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    manifest_path = run_dir / "manifest.json"
    manifest = agent_run.read_json(manifest_path, {}) or {}
    agent_run.append_manifest_stage(
        manifest,
        "awaiting_agent_outputs",
        "Rolled back current round progression artifacts for self-repair.",
    )
    agent_run.write_json(manifest_path, manifest)


def _stdout_json(payload: Dict[str, Any], indent: int | None = None) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=indent)


def _delivery_complete(delivery: Dict[str, Any]) -> bool:
    delivery_result = delivery.get("result") if isinstance(delivery.get("result"), dict) else {}
    delivery_action = delivery_result.get("action")
    complete = bool(delivery.get("ok")) and delivery_action not in {"retry", "blocked"}
    if delivery_result.get("ok") is False:
        complete = False
    return complete


import round_runtime


def _extract_tag(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", str(text or ""), re.DOTALL)
    return match.group(1).strip() if match else ""


def _strip_tag(text: str, tag: str) -> str:
    return re.sub(rf"\s*<{tag}>.*?</{tag}>\s*", "\n", str(text or ""), flags=re.DOTALL)


def _strip_tag_ci(text: str, tag: str) -> str:
    return re.sub(rf"\s*<{tag}>.*?</{tag}>\s*", "\n", str(text or ""), flags=re.DOTALL | re.IGNORECASE)


def _count_chinese_chars(text: str) -> int:
    clean = re.sub(r"<[^>]+>", "", str(text or ""))
    return sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")


def _normalize_update_variable_analysis(content: str) -> str:
    fallback = (
        "Time advances through the current player action. Dramatic updates are permitted. "
        "Variables track location, player state, active character state, and immediate scene consequences "
        "while preserving hidden truths."
    )

    def replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        word_count = len(re.findall(r"\b[\w'-]+\b", body))
        has_cjk = any("\u3400" <= ch <= "\u9fff" for ch in body)
        if body and not has_cjk and word_count <= 80:
            return match.group(0)
        return f"<Analysis>{fallback}</Analysis>"

    return re.sub(r"<Analysis>(.*?)</Analysis>", replace, str(content or ""), flags=re.DOTALL | re.IGNORECASE)


def _dialogues_from_story_input(story_input: Dict[str, Any] | None) -> list[Dict[str, str]]:
    if not isinstance(story_input, dict):
        return []
    loop_outputs = story_input.get("loop_outputs")
    if not isinstance(loop_outputs, dict):
        return []
    actors = loop_outputs.get("actors")
    if not isinstance(actors, dict):
        return []

    dialogues: list[Dict[str, str]] = []
    for actor_id, outputs in actors.items():
        actor_key = str(actor_id)
        if actor_key == "player" or not actor_key.startswith("character:") or not isinstance(outputs, list):
            continue
        name = actor_key.split(":", 1)[1]
        line = ""
        for output in outputs:
            if not isinstance(output, dict):
                continue
            character_name = str(output.get("character_name") or "").strip()
            if character_name:
                name = character_name
            events = output.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                content = str(event.get("content") or "").strip()
                if not content:
                    continue
                if event_type == "reply" and not line:
                    line = content
            if line:
                break
        if not line:
            continue
        entry = {"name": str(name), "source": "subagent", "line": line[:1000]}
        dialogues.append(entry)
        if len(dialogues) >= 6:
            break
    return dialogues


def _normalize_story_output(story: Dict[str, Any], story_input: Dict[str, Any] | None = None) -> Dict[str, Any]:
    normalized = dict(story)
    normalized.pop("tokens", None)
    normalized.pop("token_usage", None)
    normalized.pop("metadata_tokens", None)
    content = str(normalized.get("content") or "")
    derived_edits = _story_output_derived_content_edits(normalized)
    content = _strip_tag_ci(content, "polished_input")
    content = _strip_tag_ci(content, "tokens")
    content = _strip_tag_ci(content, "metadata")
    content = _strip_tag_ci(content, "character_dialogues")
    content = _strip_tag_ci(content, "derived_content_edits")
    content = _normalize_update_variable_analysis(content)

    dialogues = normalized.get("character_dialogues")
    if not isinstance(dialogues, list):
        dialogues = []
    if not dialogues:
        dialogues = _dialogues_from_story_input(story_input)
    normalized["character_dialogues"] = dialogues
    dialogue_block = "<character_dialogues>" + json.dumps(dialogues, ensure_ascii=False) + "</character_dialogues>"
    if derived_edits:
        normalized["derived_content_edits"] = derived_edits
        edit_block = "<derived_content_edits>" + json.dumps(derived_edits, ensure_ascii=False) + "</derived_content_edits>"
        content = edit_block + "\n" + content.lstrip()

    if "<summary>" in content:
        content = content.replace("<summary>", dialogue_block + "\n<summary>", 1)
    elif "<options>" in content:
        content = content.replace("<options>", dialogue_block + "\n<options>", 1)
    else:
        content = content.rstrip() + "\n" + dialogue_block
    normalized["content"] = content.strip()
    return normalized


_TOKEN_COUNT_KEYS = {
    "in",
    "input",
    "input_tokens",
    "prompt",
    "prompt_tokens",
    "out",
    "output",
    "output_tokens",
    "completion",
    "completion_tokens",
    "total",
    "total_tokens",
}


def _whole_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _token_count_numbers(value: Any) -> list[int]:
    numbers: list[int] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if isinstance(raw_value, (dict, list)):
                numbers.extend(_token_count_numbers(raw_value))
                continue
            if key in _TOKEN_COUNT_KEYS:
                number = _whole_number(raw_value)
                if number is not None:
                    numbers.append(number)
        return numbers
    if isinstance(value, list):
        for item in value:
            numbers.extend(_token_count_numbers(item))
        return numbers

    text = str(value)
    for match in re.finditer(
        r"\b(?:in|input|input_tokens|prompt|prompt_tokens|out|output|output_tokens|completion|completion_tokens|total|total_tokens)\b\s*[:=]\s*(\d+)\b",
        text,
        flags=re.IGNORECASE,
    ):
        numbers.append(int(match.group(1)))
    return numbers


def _token_value_has_placeholder(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower() if isinstance(value, (dict, list)) else str(value).lower()
    if "nnnn" in text or "fake" in text or "placeholder" in text or re.search(r"\ball[-_ ]?zero\b", text):
        return True
    numbers = _token_count_numbers(value)
    return bool(numbers) and all(number == 0 for number in numbers)


def _story_has_token_placeholder(story: Dict[str, Any]) -> bool:
    content = str(story.get("content") or "")
    token_block = _extract_tag(content, "tokens")
    if token_block is not None and _token_value_has_placeholder(token_block):
        return True
    for key in ("tokens", "token_usage", "metadata_tokens"):
        if key in story and _token_value_has_placeholder(story.get(key)):
            return True
    return False


def _is_token_only_placeholder_failure(item: Any) -> bool:
    if not isinstance(item, str):
        return False
    text = item.strip().lower()
    if re.search(r"\b(?:and|also)\b", text) or any(marker in text for marker in ("并", "且", ",", "，", ";", "；")):
        return False
    token_target = r"(?:<tokens>|tokens?|token(?:\s+(?:block|data|values))?)"
    return bool(
        re.fullmatch(
            rf"(?:story\.output\.json\s+)?(?:contains|has|includes)\s+(?:a\s+)?"
            rf"(?:placeholder|fake|all[-_ ]?zero)\s+{token_target}(?:\s+values)?"
            rf"(?:\s*\('nnnn'\))?\.?",
            text,
        )
    )


def _story_output_looks_readable(story: Dict[str, Any]) -> bool:
    content = str(story.get("content") or "")
    body = _extract_tag(content, "content") or content
    cjk_count = _count_chinese_chars(body)
    question_count = body.count("?") + body.count("？")
    placeholder_runs = len(re.findall(r"\?{3,}|？{3,}", body))
    return cjk_count >= 80 and question_count <= max(20, cjk_count // 8) and placeholder_runs == 0


def _is_unsupported_placeholder_corruption_failure(item: Any, story: Dict[str, Any]) -> bool:
    if not isinstance(item, str) or not _story_output_looks_readable(story):
        return False
    text = item.strip().lower()
    corruption_terms = (
        "placeholder",
        "question-mark",
        "question mark",
        "mojibake",
        "unreadable",
        "non-semantic",
        "nonsemantic",
        "obfuscated",
        "non-decodable",
        "encoding",
        "replacement glyph",
        "intelligible",
    )
    if any(term in text for term in corruption_terms):
        return True
    story_text = json.dumps(story, ensure_ascii=False)
    if "/??" in text and "/??" not in story_text:
        return True
    return False


_DERIVED_CONTENT_EDIT_FIELDS = (
    "ai",
    "content",
    "new_ai",
    "summary",
    "first_paragraph",
    "new_first_paragraph",
)
_DERIVED_CONTENT_FULL_EDIT_FIELDS = ("ai", "content", "new_ai")


def _story_input_is_active_retcon_replay(story_input: Dict[str, Any]) -> bool:
    replay = story_input.get("retcon_replay")
    return isinstance(replay, dict) and str(replay.get("status") or "") == "active"


def _story_input_requires_derived_content_edits(story_input: Dict[str, Any] | None) -> bool:
    if not isinstance(story_input, dict):
        return False
    if _story_input_is_active_retcon_replay(story_input):
        return False
    player_inputs = story_input.get("player_inputs")
    if not isinstance(player_inputs, dict):
        return False
    analysis = player_inputs.get("input_analysis")
    if not isinstance(analysis, dict):
        return False
    directives = analysis.get("narrative_directives")
    if isinstance(directives, dict) and directives.get("rewrite_previous_output") is True:
        return True
    world_updates = analysis.get("world_updates")
    if isinstance(world_updates, dict):
        retcons = world_updates.get("retcon_requests")
        if isinstance(retcons, list) and any(isinstance(item, dict) for item in retcons):
            return True
    units = analysis.get("semantic_units")
    if isinstance(units, list):
        return any(
            isinstance(unit, dict)
            and str(unit.get("type") or "") == "edit_request"
            for unit in units
        )
    return False


def _story_output_derived_content_edits(story: Dict[str, Any]) -> list[Dict[str, Any]]:
    edits: list[Dict[str, Any]] = []
    direct = story.get("derived_content_edits") if isinstance(story, dict) else None
    if isinstance(direct, list):
        edits.extend(_normalize_derived_content_edit(item) for item in direct if isinstance(item, dict))
    content = str(story.get("content") or "") if isinstance(story, dict) else ""
    raw = _extract_tag(content, "derived_content_edits")
    if raw:
        parsed = _loads_json_relaxed(raw)
        if isinstance(parsed, list):
            edits.extend(_normalize_derived_content_edit(item) for item in parsed if isinstance(item, dict))
    return [edit for edit in edits if edit]


def _normalize_derived_content_edit(edit: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(edit)
    if "turn_index" not in normalized:
        raw_turn = normalized.get("turn")
        if raw_turn is None:
            raw_turn = normalized.get("target_turn_index")
        try:
            turn_index = int(raw_turn)
        except (TypeError, ValueError):
            turn_index = None
        if turn_index is not None:
            if "turn" in normalized and turn_index > 0:
                turn_index -= 1
            normalized["turn_index"] = turn_index

    replacement = normalized.get("replacement")
    if replacement is None:
        replacement = normalized.get("value")
    if replacement is None:
        replacement = normalized.get("new_value")
    if isinstance(replacement, str) and replacement.strip():
        action = str(normalized.get("action") or normalized.get("op") or "").strip().lower()
        field = str(normalized.get("field") or "").strip().lower()
        if action in {"replace_ai", "rewrite_ai", "replace_turn", "rewrite_turn", "replace"} or field in {"ai", "content"}:
            normalized.setdefault("ai", replacement.strip())
        elif "first_paragraph" in action or field in {"first_paragraph", "new_first_paragraph"}:
            normalized.setdefault("first_paragraph", replacement.strip())
        else:
            normalized.setdefault("content", replacement.strip())

    note = normalized.get("note")
    if isinstance(note, str) and note.strip() and not str(normalized.get("reason") or "").strip():
        normalized["reason"] = note.strip()
    for alias in ("turn", "target_turn_index", "replacement", "value", "new_value", "note"):
        normalized.pop(alias, None)
    return normalized


def _has_actionable_derived_content_edits(story: Dict[str, Any], *, require_full_ai: bool = False) -> bool:
    fields = _DERIVED_CONTENT_FULL_EDIT_FIELDS if require_full_ai else _DERIVED_CONTENT_EDIT_FIELDS
    for edit in _story_output_derived_content_edits(story):
        if "turn_index" not in edit:
            continue
        if any(isinstance(edit.get(field), str) and edit.get(field).strip() for field in fields):
            return True
    return False


def _force_retcon_derived_edit_revise(critic: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(critic)
    hard_failures = normalized.get("hard_failures")
    if not isinstance(hard_failures, list):
        hard_failures = []
    failure = (
        "missing <derived_content_edits>: player authority requires repairing earlier "
        "AI-derived content before continuing this turn."
    )
    if not any("derived_content_edits" in str(item) for item in hard_failures):
        hard_failures.append(failure)
    normalized["hard_failures"] = hard_failures
    if str(normalized.get("decision") or "") == "pass":
        normalized["decision"] = "revise"
    instruction = str(normalized.get("repair_instruction") or "").strip()
    derived_instruction = (
        "Emit an actionable <derived_content_edits> JSON array that updates the affected "
        "earlier AI turn while preserving every player input field, then rewrite the current scene. "
        "For dream/retcon repairs, provide a complete replacement of the affected previous AI "
        "turn with `turn_index: 0` and `ai`; do not only replace the first paragraph."
    )
    normalized["repair_instruction"] = (
        instruction + "\n" + derived_instruction if instruction else derived_instruction
    )
    if not isinstance(normalized.get("repair_routing"), dict):
        normalized["repair_routing"] = {
            "stage": "story_composition",
            "target_agents": ["story"],
            "rollback": "story_only",
            "can_auto_repair": True,
            "risk": "low",
        }
    return normalized


def _normalize_critic_report_for_story(
    critic: Dict[str, Any],
    story: Dict[str, Any],
    story_input: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = dict(critic)
    hard_failures = normalized.get("hard_failures")
    if isinstance(hard_failures, list) and not _story_has_token_placeholder(story):
        cleaned = [
            item
            for item in hard_failures
            if not _is_token_only_placeholder_failure(item)
            and not _is_unsupported_placeholder_corruption_failure(item, story)
        ]
        if len(cleaned) != len(hard_failures):
            normalized["hard_failures"] = cleaned
            if not cleaned:
                normalized["decision"] = "pass"

    if (
        _story_input_requires_derived_content_edits(story_input)
        and not _has_actionable_derived_content_edits(story, require_full_ai=True)
    ):
        return _force_retcon_derived_edit_revise(normalized)
    _infer_story_repair_routing_from_issues(normalized)
    return normalized


def _infer_story_repair_routing_from_issues(critic: Dict[str, Any]) -> None:
    if str(critic.get("decision") or "") == "pass":
        return
    if isinstance(critic.get("repair_routing"), dict):
        return
    hard_failures = critic.get("hard_failures")
    if isinstance(hard_failures, list) and hard_failures:
        return
    soft_issues = critic.get("soft_issues")
    if not isinstance(soft_issues, list) or not soft_issues:
        return
    if not str(critic.get("repair_instruction") or "").strip():
        return
    for issue in soft_issues:
        if not isinstance(issue, dict):
            return
        if str(issue.get("repair_route") or "") != "story_composition":
            return
    critic["repair_routing"] = {
        "stage": "story_composition",
        "target_agents": ["story"],
        "rollback": "story_only",
        "can_auto_repair": True,
        "risk": "low",
    }


def _story_main_text(story: Dict[str, Any]) -> str:
    raw = str(story.get("content") or "")
    main = _extract_tag(raw, "content") or raw
    return re.sub(r"<[^>]+>", "", main)


def _player_character_names_from_story_input(story_input: Dict[str, Any]) -> list[str]:
    names: list[str] = []
    player_inputs = story_input.get("player_inputs") if isinstance(story_input, dict) else {}
    if isinstance(player_inputs, dict):
        analysis = player_inputs.get("input_analysis")
        if isinstance(analysis, dict):
            world_updates = analysis.get("world_updates")
            if isinstance(world_updates, dict):
                important = world_updates.get("important_characters")
                if isinstance(important, list):
                    for item in important:
                        if not isinstance(item, dict):
                            continue
                        if item.get("status") not in {None, "", "active"}:
                            continue
                        name = str(item.get("name") or "").strip()
                        if name:
                            names.append(name)
    return list(dict.fromkeys(names))


def _reset_delivery_retry_budget(run_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Start a fresh generation attempt without stale critic retry exhaustion."""
    if int(manifest.get("critic_retry_count", 0) or 0) == 0:
        return manifest
    manifest = dict(manifest)
    manifest["critic_retry_count"] = 0
    agent_run.write_json(run_dir / "manifest.json", manifest)
    return manifest


def _read_existing_json_object(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentExecutionError(f"{path}: input analysis output is invalid JSON.") from exc
    except OSError as exc:
        raise AgentExecutionError(f"{path}: input analysis output cannot be read.") from exc
    if not isinstance(payload, dict):
        raise AgentExecutionError(f"{path}: input analysis output must be a JSON object.")
    return payload


def _apply_input_analysis(card: Path, root: Path) -> Dict[str, Any]:
    try:
        result = input_analysis_apply.apply_current_run(card, root)
    except AgentExecutionError:
        raise
    except Exception as exc:
        raise AgentExecutionError(f"input analysis apply failed: {exc}") from exc
    if isinstance(result, dict):
        return result
    return {}


def _input_analysis_apply_allowed(stage: Any) -> bool:
    stage_text = "" if stage is None else str(stage)
    return stage_text in _INPUT_ANALYSIS_APPLY_ALLOWED_STAGES


def _ensure_input_analysis(
    run_dir: Path,
    manifest: Dict[str, Any],
    card: Path,
    root: Path,
    run_claude: Callable[[str, str, str | Path], str],
) -> Dict[str, Any]:
    expected = manifest.get("expected_outputs") or {}
    prompts = manifest.get("prompts") or {}
    if not isinstance(expected, dict):
        raise AgentExecutionError("manifest.expected_outputs must be an object when present.")
    if not isinstance(prompts, dict):
        raise AgentExecutionError("manifest.prompts is required.")

    expected_rel = expected.get("input_analysis")
    prompt_rel = prompts.get("input_analyst")
    if not expected_rel and not prompt_rel:
        return {}
    if expected_rel and not prompt_rel:
        raise AgentExecutionError("manifest.prompts.input_analyst is required.")
    if prompt_rel and not expected_rel:
        raise AgentExecutionError("manifest.expected_outputs.input_analysis is required.")

    output_path = _relative_path(run_dir, str(expected_rel), "input_analysis.output.json")
    existing = _read_existing_json_object(output_path)
    if existing is not None:
        if _input_analysis_apply_allowed(manifest.get("stage")):
            return _apply_input_analysis(card, root)
        return existing

    prompt_path = _relative_path(run_dir, str(prompt_rel), "prompts/input_analyst.prompt.md")
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentExecutionError(f"{prompt_path}: prompt is missing.") from exc
    last_error: AgentExecutionError | None = None
    for attempt in range(2):
        try:
            _dispatch_and_write(
                "input_analyst",
                output_path,
                prompt_text,
                root,
                run_claude,
                attempts=1,
                initial_error=last_error,
            )
            return _apply_input_analysis(card, root)
        except AgentExecutionError as exc:
            last_error = exc
            try:
                output_path.unlink()
            except OSError:
                pass
            if attempt == 1:
                raise

    raise last_error or AgentExecutionError("input analyst did not complete.")


def run_round(
    card_folder: str | Path,
    root_dir: str | Path,
    run_claude: Callable[[str, str, str | Path], str] | None = None,
    run_command: Callable[..., Any] = subprocess.run,
) -> Dict[str, Any]:
    """Generate and deliver the currently prepared round through the thin runtime."""
    card = Path(card_folder).resolve()
    root = Path(root_dir).resolve()
    run_dir = agent_run.current_run_dir(card)
    if run_dir is None:
        raise AgentExecutionError(f"{card / '.agent_runs' / 'current'} is missing or invalid.")

    manifest = _load_manifest(run_dir)
    settings = agent_run.read_json(root / "skills" / "styles" / "settings.json", {}) or {}
    if not isinstance(settings, dict):
        settings = {}
    debug_logger = model_debug.logger_from_settings(
        card,
        str(manifest.get("round_id") or run_dir.name),
        settings,
    )
    if run_claude is None:
        run_claude = llm_runner.run_llm_agent
    active_run_claude = lambda agent_key, prompt, cwd: _run_claude_with_debug(
        debug_logger,
        run_claude,
        agent_key,
        prompt,
        cwd,
    )
    _reset_delivery_retry_budget(run_dir, manifest)
    return round_runtime.run_round(
        card,
        root,
        run_claude=active_run_claude,
        run_command=run_command,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 2:
        print(_stdout_json({"ok": False, "error": "Usage: rp_generate_cli.py <card_folder> <ROOT>"}))
        return 2
    try:
        result = run_round(args[0], args[1])
    except Exception as exc:
        print(_stdout_json({"ok": False, "error": str(exc)}))
        return 1
    print(_stdout_json(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
