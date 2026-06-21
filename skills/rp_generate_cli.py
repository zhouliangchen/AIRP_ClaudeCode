#!/usr/bin/env python3
"""Deterministically drive one prepared RP round through Claude Code subagents."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict

import agent_intents
import agent_outputs
import agent_prompts
import agent_run
import agent_schemas
import agent_turn_loop
import input_analysis_apply
import model_debug
import self_repair

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


MAX_DELIVERY_REPAIR_ATTEMPTS = 3
MAX_STORY_PREFLIGHT_ATTEMPTS = 3
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


def _outer_prompt(agent_key: str, subagent_prompt: str, extra_context: Dict[str, Any] | None = None) -> str:
    extra = ""
    if extra_context:
        extra = "\n\n## Runtime Input\n\n```json\n" + json.dumps(extra_context, ensure_ascii=False, indent=2) + "\n```\n"
    return f"""You are the Claude Code general-purpose agent for RP artifact `{agent_key}`.

You are running in embedded-context mode. Use the Context Packet and Runtime Input embedded in this task as the authoritative source.
Do not attempt to read files, inspect run_dir, or require filesystem access.
Do not block because run_dir, story.input.json, story.output.json, or card files are inaccessible; their required contents are embedded below when needed.
If Runtime Input contains `previous_rejected_story_output`, treat it only as a rejected anti-example. Rebuild from `story_input`, current GM/player/character artifacts, and the repair instruction; do not preserve unsupported details from the rejected draft.
Return only one valid JSON object and no prose. Follow the full RP subagent prompt inside the XML-style block below exactly. The prompt may contain Markdown fences; do not treat those fences as the end of the task.

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
        cwd=str(cwd),
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
        )


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


def _read_prompt(run_dir: Path, manifest: Dict[str, Any], key: str) -> str:
    prompts = manifest.get("prompts") or {}
    if not isinstance(prompts, dict):
        raise AgentExecutionError("manifest.prompts is required.")
    prompt_path = _relative_path(run_dir, str(prompts.get(key) or f"prompts/{key}.prompt.md"), f"prompts/{key}.prompt.md")
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentExecutionError(f"{prompt_path}: prompt is missing.") from exc


def _unwrap_payload(agent_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    wrapper = ""
    if agent_key == "gm":
        wrapper = "gm_output"
    elif agent_key in {"player"} or agent_key.startswith("character:"):
        wrapper = "actor_output"
    elif agent_key.startswith("subGM:"):
        wrapper = "subgm_output"
    elif agent_key == "story":
        wrapper = "story_output"
    elif agent_key == "critic":
        wrapper = "critic_report"

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


def _validate(agent_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _unwrap_payload(agent_key, payload)
    try:
        if agent_key == "gm":
            normalized = agent_schemas.validate_gm_output(payload)
            normalized["world_state_delta"] = _normalize_world_state_delta(normalized.get("world_state_delta", []))
            return normalized
        if agent_key in {"player"} or agent_key.startswith("character:"):
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
            return agent_schemas.validate_story_output(payload)
        if agent_key == "critic":
            return agent_schemas.validate_critic_report(payload)
        if agent_key == "input_analyst":
            return payload
    except agent_schemas.ValidationError as exc:
        raise AgentExecutionError(f"{agent_key} returned invalid artifact: {exc}") from exc
    raise AgentExecutionError(f"Unknown agent key: {agent_key}")


def _dispatch_agent_payload(
    agent_key: str,
    prompt_text: str,
    cwd: Path,
    run_claude: Callable[[str, str, str | Path], str],
    extra_context: Dict[str, Any] | None = None,
    attempts: int = 2,
) -> Dict[str, Any]:
    last_error: AgentExecutionError | None = None
    attempts = max(1, int(attempts or 1))
    for attempt in range(attempts):
        try:
            stream = run_claude(agent_key, _outer_prompt(agent_key, prompt_text, extra_context), cwd)
            text = _extract_agent_or_direct_text(stream)
            payload = _extract_json_object(text)
            return _validate(agent_key, payload)
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
) -> Dict[str, Any]:
    normalized = _dispatch_agent_payload(
        agent_key,
        prompt_text,
        cwd,
        run_claude,
        extra_context=extra_context,
        attempts=attempts,
    )
    agent_run.write_json(output_path, normalized)
    return normalized


def _read_loop_prompt(
    run_dir: Path,
    manifest: Dict[str, Any],
    agent_key: str,
    packet: Dict[str, Any] | None = None,
) -> str:
    if agent_key in {"gm", "player"}:
        return _read_prompt(run_dir, manifest, agent_key)
    if agent_key.startswith("subGM:"):
        return agent_prompts.subgm_prompt_text(packet or {})
    if not agent_key.startswith("character:"):
        raise AgentExecutionError(f"Unknown loop agent key: {agent_key}")

    prompts = manifest.get("prompts") or {}
    character_prompts = prompts.get("characters") if isinstance(prompts, dict) else None
    if not isinstance(character_prompts, dict):
        raise AgentExecutionError("manifest.prompts.characters is required.")
    actor_name = agent_key.split(":", 1)[1]
    prompt_rel = character_prompts.get(actor_name) or character_prompts.get(agent_run.safe_name(actor_name))
    if not isinstance(prompt_rel, str) or not prompt_rel:
        if packet is not None:
            return agent_prompts.character_prompt_text(packet)
        raise AgentExecutionError(f"Missing prompt path for {agent_key}.")
    prompt_path = run_dir / prompt_rel
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        if packet is not None:
            return agent_prompts.character_prompt_text(packet)
        raise AgentExecutionError(f"{prompt_path}: prompt is missing.") from exc


def _run_interactive_agent_loop(
    run_dir: Path,
    manifest: Dict[str, Any],
    root: Path,
    run_claude: Callable[[str, str, str | Path], str],
    repair_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    def dispatch(agent_key: str, packet: Dict[str, Any]) -> Dict[str, Any]:
        extra_context = {"loop_packet": packet}
        if repair_context and agent_key == "gm":
            extra_context["repair_context"] = repair_context
        return _dispatch_agent_payload(
            agent_key,
            _read_loop_prompt(run_dir, manifest, agent_key, packet),
            root,
            run_claude,
            extra_context=extra_context,
        )

    try:
        return agent_turn_loop.run_interactive_loop(run_dir, dispatch, card_folder=run_dir.parents[1])
    except agent_turn_loop.AgentTurnLoopError as exc:
        raise AgentExecutionError(str(exc)) from exc


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
    for name in [
        "gm.output.json",
        "actor.outputs.json",
        "interaction.trace.json",
        "story.input.json",
        "story.output.json",
        "critic.report.json",
    ]:
        try:
            (run_dir / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    for name in ["side_threads", "memory_summaries"]:
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


def _normalize_required_person(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "第三" in text or "third" in text:
        return "第三人称"
    if "第二" in text or "second" in text:
        return "第二人称"
    return "第二人称"


def _delivery_requirements(root: Path) -> Dict[str, Any]:
    settings_path = root / "skills" / "styles" / "settings.json"
    settings = agent_run.read_json(settings_path, {}) or {}
    try:
        target = int(settings.get("wordCount", 2000))
    except (TypeError, ValueError):
        target = 2000
    return {
        "word_count_target": target,
        "minimum_chinese_chars": int(target * 0.8),
        "delivery_gate": "round_deliver.py",
        "preflight_word_count": settings_path.exists(),
        "required_person": _normalize_required_person(settings.get("person")),
    }


def _delivery_complete(delivery: Dict[str, Any]) -> bool:
    delivery_result = delivery.get("result") if isinstance(delivery.get("result"), dict) else {}
    delivery_action = delivery_result.get("action")
    complete = bool(delivery.get("ok")) and delivery_action not in {"retry", "blocked"}
    if delivery_result.get("ok") is False:
        complete = False
    return complete


def _pending_repair_intents(run_dir: Path) -> list[dict[str, Any]]:
    return [item for item in agent_intents.list_intents(run_dir, "pending") if item.get("type") == "repair_request"]


def _complete_pending_repair_intents(run_dir: Path, outputs: Dict[str, Any]) -> None:
    for intent in _pending_repair_intents(run_dir):
        agent_intents.complete_intent(run_dir, intent.get("id", ""), outputs=outputs)


def _block_pending_repair_intents(run_dir: Path, reason: str, outputs: Dict[str, Any]) -> None:
    for intent in _pending_repair_intents(run_dir):
        agent_intents.block_intent(run_dir, intent.get("id", ""), reason, outputs=outputs)


def _repair_intent_delivery_output(delivery: Dict[str, Any]) -> Dict[str, Any]:
    delivery_result = delivery.get("result") if isinstance(delivery.get("result"), dict) else {}
    output = dict(delivery_result)
    output["ok"] = _delivery_complete(delivery)
    return output


def _load_existing_story_input(run_dir: Path) -> Dict[str, Any] | None:
    path = run_dir / "story.input.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentExecutionError(f"{path}: story input is invalid JSON.") from exc
    except OSError as exc:
        raise AgentExecutionError(f"{path}: story input cannot be read.") from exc
    if not isinstance(payload, dict):
        raise AgentExecutionError(f"{path}: story input must be a JSON object.")
    return payload


def _loop_result_from_story_input(story_input: Dict[str, Any]) -> Dict[str, Any]:
    loop_outputs = story_input.get("loop_outputs") if isinstance(story_input, dict) else {}
    if not isinstance(loop_outputs, dict):
        loop_outputs = {}
    gm_outputs = loop_outputs.get("gm")
    if not isinstance(gm_outputs, list):
        gm_outputs = loop_outputs.get("gm_outputs")
    if not isinstance(gm_outputs, list):
        gm_outputs = []
    actors = loop_outputs.get("actors")
    if not isinstance(actors, dict):
        actors = {}
    called_actors = sorted(
        str(actor_id)
        for actor_id, outputs in actors.items()
        if str(actor_id).strip() and isinstance(outputs, list) and outputs
    )
    return {"gm_steps": len(gm_outputs), "called_actors": called_actors}


def _delivery_retry_context(
    delivery_result: Dict[str, Any],
    story: Dict[str, Any],
    critic: Dict[str, Any],
    attempt: int,
) -> Dict[str, Any]:
    reason = delivery_result.get("reason") or "delivery_retry"
    repair_detail = delivery_result.get("detail") if isinstance(delivery_result.get("detail"), dict) else {}
    repair_instruction = (
        repair_detail.get("repair_instruction")
        or delivery_result.get("hint")
        or "Revise the story output and critic report so round_deliver.py can approve delivery."
    )
    repair_instruction = (
        str(repair_instruction)
        + " Raw player role_channel and raw_text outrank GM/player/character artifacts; discard any subagent output that continues an obsolete scene, invents player actions/dialogue, or reveals hidden user instructions. "
        + "If prior AI-derived content is reframed by the player, include <derived_content_edits> JSON that repairs the affected earlier AI turn while preserving all player input fields."
    )
    return {
        "reason": reason,
        "delivery_result": delivery_result,
        "authoritative_sources": ["story_input", "loop_outputs", "gm.output.json", "actor.outputs.json", "raw_player_input"],
        "previous_rejected_story_output": story,
        "previous_critic_report": critic,
        "do_not_preserve_rejected_content": True,
        "repair_attempt": attempt,
        "max_repair_attempts": MAX_DELIVERY_REPAIR_ATTEMPTS,
        "repair_routing": self_repair.routing_from_delivery_result(delivery_result),
        "instruction": repair_instruction,
    }


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
        aside = ""
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
                if event_type == "dialogue" and not line:
                    line = content
                elif event_type == "perceive_request" and not aside:
                    aside = content[:500]
                if line and aside:
                    break
            if line:
                break
        if not line:
            continue
        entry = {"name": str(name), "source": "subagent", "line": line[:1000]}
        if aside:
            entry["aside"] = aside
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
    content = _strip_tag_ci(content, "polished_input")
    content = _strip_tag_ci(content, "tokens")
    content = _strip_tag_ci(content, "metadata")
    content = _strip_tag_ci(content, "character_dialogues")
    content = _normalize_update_variable_analysis(content)

    dialogues = normalized.get("character_dialogues")
    if not isinstance(dialogues, list):
        dialogues = []
    if not dialogues:
        dialogues = _dialogues_from_story_input(story_input)
    normalized["character_dialogues"] = dialogues
    dialogue_block = "<character_dialogues>" + json.dumps(dialogues, ensure_ascii=False) + "</character_dialogues>"

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


def _normalize_critic_report_for_story(critic: Dict[str, Any], story: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(critic)
    hard_failures = normalized.get("hard_failures")
    if not isinstance(hard_failures, list) or _story_has_token_placeholder(story):
        return normalized

    cleaned = [
        item
        for item in hard_failures
        if not _is_token_only_placeholder_failure(item)
        and not _is_unsupported_placeholder_corruption_failure(item, story)
    ]
    if len(cleaned) == len(hard_failures):
        return normalized

    normalized["hard_failures"] = cleaned
    if not cleaned:
        normalized["decision"] = "pass"
    return normalized


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


def _has_second_person_violation(main_text: str, requirements: Dict[str, Any]) -> bool:
    required = str(requirements.get("required_person") or "")
    if "第二" not in required and "second" not in required.lower():
        return False
    sample = re.sub(r"\s+", "", main_text[:1200])
    if "你" in sample[:500]:
        return False
    names = [str(name) for name in requirements.get("player_character_names") or [] if str(name).strip()]
    name_driven = any(
        re.search(rf"{re.escape(name)}(醒|坐|站|抬|低|看|闻|伸|把|走|想|觉得|没有|慢慢|忽然)", sample)
        for name in names
    )
    pronoun_driven = ("他" in sample or "她" in sample) and not names
    return bool(name_driven or pronoun_driven)


def _input_analysis_requires_prior_rewrite(story_input: Dict[str, Any] | None) -> bool:
    if not isinstance(story_input, dict):
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
    return False


def _extract_derived_content_edits_from_story(story: Dict[str, Any]) -> list[Any]:
    raw = _extract_tag(str(story.get("content") or ""), "derived_content_edits")
    if not raw:
        return []
    try:
        edits = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return edits if isinstance(edits, list) else []


def _has_actionable_derived_content_edits(story: Dict[str, Any]) -> bool:
    for edit in _extract_derived_content_edits_from_story(story):
        if not isinstance(edit, dict):
            continue
        try:
            int(edit.get("turn_index", 0))
        except Exception:
            continue
        for key in ("ai", "content", "new_ai", "first_paragraph", "new_first_paragraph", "summary"):
            value = edit.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def _story_preflight_issues(
    story: Dict[str, Any],
    requirements: Dict[str, Any],
    story_input: Dict[str, Any] | None = None,
) -> list[str]:
    issues = []
    if requirements.get("preflight_word_count"):
        minimum = int(requirements.get("minimum_chinese_chars", 0) or 0)
        content_text = _extract_tag(str(story.get("content") or ""), "content") or str(story.get("content") or "")
        current = _count_chinese_chars(content_text)
        if current < minimum:
            issues.append(f"content_chinese_chars {current} is below required minimum {minimum}")
    main_text = _story_main_text(story)
    if _has_second_person_violation(main_text, requirements):
        issues.append("second_person_required_but_story_uses_third_person_player_narration")
    if "<character_dialogues>" in str(story.get("content") or ""):
        raw = _extract_tag(str(story.get("content") or ""), "character_dialogues")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, list):
            issues.append("character_dialogues tag must contain a JSON array")
    if _input_analysis_requires_prior_rewrite(story_input) and not _has_actionable_derived_content_edits(story):
        issues.append("rewrite_previous_output_requires_actionable_derived_content_edits")
    return issues


def _story_preflight_repair_context(
    story: Dict[str, Any],
    issues: list[str],
    requirements: Dict[str, Any],
    attempt: int,
    repair_context: Dict[str, Any] | None,
    max_attempts: int = MAX_STORY_PREFLIGHT_ATTEMPTS,
) -> Dict[str, Any]:
    minimum = int(requirements.get("minimum_chinese_chars", 0) or 0)
    target = int(requirements.get("word_count_target", 0) or 0)
    recommended = max(minimum, int(target * 0.9) if target > 0 else int(minimum * 1.1))
    content_text = _extract_tag(str(story.get("content") or ""), "content") or str(story.get("content") or "")
    current = _count_chinese_chars(content_text)
    missing = max(0, minimum - current)
    return {
        "reason": "story_preflight",
        "preflight_issues": issues,
        "delivery_requirements": requirements,
        "word_count_contract": {
            "method": "round_deliver.py count_chinese strips tags and counts CJK characters only",
            "word_count_target": target,
            "minimum_chinese_chars": minimum,
            "recommended_chinese_chars": recommended,
            "current_chinese_chars": current,
            "missing_chinese_chars": missing,
        },
        "authoritative_sources": ["story_input", "loop_outputs", "gm.output.json", "actor.outputs.json", "raw_player_input"],
        "previous_rejected_story_output": story,
        "do_not_preserve_rejected_content": True,
        "repair_attempt": attempt,
        "max_repair_attempts": max_attempts,
        "prior_repair_context": repair_context or {},
        "instruction": (
            "Rewrite story.output.json from story_input and current agent artifacts. "
            f"The <content> body must contain at least {minimum} CJK characters excluding tags, "
            f"and should aim for about {recommended} CJK characters so round_deliver.py count_chinese passes safely. "
            f"The rejected draft currently has {current} CJK characters and is missing about {missing}. "
            "If preflight_issues includes rewrite_previous_output_requires_actionable_derived_content_edits, "
            "emit a <derived_content_edits> JSON array with handler-actionable objects containing turn_index and at least one of "
            "summary, first_paragraph, new_first_paragraph, ai, content, or new_ai; do not use JSON Patch objects there. "
            "Do not summarize or shorten the accepted scene structure; expand it with sensory detail, NPC micro-reactions, "
            "environmental motion, physical continuity, and the protagonist's immediate embodied response while preserving the same decision boundary. "
            "Preserve raw player input authority, "
            "obey required_person perspective exactly, avoid visible meta-analysis or <polished_input>, "
            "keep <character_dialogues>[]</character_dialogues> as a valid JSON array before <summary>, "
            "and do not include a fake <tokens> block."
        ),
    }


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
        raise AgentExecutionError("manifest.expected_outputs is required.")
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
    run_claude: Callable[[str, str, str | Path], str] = run_claude_agent,
    run_command: Callable[..., Any] = subprocess.run,
) -> Dict[str, Any]:
    """Generate and deliver the currently prepared round."""
    card = Path(card_folder).resolve()
    root = Path(root_dir).resolve()
    run_dir = agent_run.current_run_dir(card)
    if run_dir is None:
        raise AgentExecutionError(f"{card / '.agent_runs' / 'current'} is missing or invalid.")

    repair_policy = self_repair.load_policy(root / "skills" / "styles" / "settings.json")
    manifest = _load_manifest(run_dir)
    settings = agent_run.read_json(root / "skills" / "styles" / "settings.json", {}) or {}
    if not isinstance(settings, dict):
        settings = {}
    debug_logger = model_debug.logger_from_settings(
        card,
        str(manifest.get("round_id") or run_dir.name),
        settings,
    )
    active_run_claude = lambda agent_key, prompt, cwd: _run_claude_with_debug(
        debug_logger,
        run_claude,
        agent_key,
        prompt,
        cwd,
    )
    manifest = _reset_delivery_retry_budget(run_dir, manifest)
    _write_progress_safe("input_analysis.running", "正在分析玩家输入", percent=38)
    _ensure_input_analysis(run_dir, manifest, card, root, active_run_claude)
    _write_progress_safe("input_analysis.applied", "输入分析已应用", percent=45)
    manifest = _load_manifest(run_dir)
    expected = manifest.get("expected_outputs") or {}
    if not isinstance(expected, dict):
        raise AgentExecutionError("manifest.expected_outputs is required.")

    story_path = _relative_path(run_dir, str(expected.get("story") or "story.output.json"), "story.output.json")
    critic_path = _relative_path(run_dir, str(expected.get("critic") or "critic.report.json"), "critic.report.json")
    story_input = _load_existing_story_input(run_dir)
    if story_input is None:
        _write_progress_safe("gm_loop.starting", "正在启动 GM 回合", percent=46, detail={"run_id": run_dir.name})
        loop_result = _run_interactive_agent_loop(run_dir, manifest, root, active_run_claude)
        story_input = agent_outputs.build_story_input(run_dir)
    else:
        loop_result = _loop_result_from_story_input(story_input)
    story_prompt = _read_prompt(run_dir, manifest, "story")
    critic_prompt = _read_prompt(run_dir, manifest, "critic")
    requirements = _delivery_requirements(root)
    requirements["player_character_names"] = _player_character_names_from_story_input(story_input)

    def dispatch_story_and_critic(repair_context: Dict[str, Any] | None = None) -> tuple[Dict[str, Any], Dict[str, Any]]:
        story_extra = {"story_input": story_input, "delivery_requirements": requirements}
        if repair_context:
            story_extra["repair_context"] = repair_context
        next_story = {}
        active_repair_context = repair_context
        for preflight_attempt in range(repair_policy.story_preflight_attempts + 1):
            _write_progress_safe("story.running", "正在写作正文", percent=68, detail={"attempt": preflight_attempt + 1})
            next_story = _dispatch_and_write(
                "story",
                story_path,
                story_prompt,
                root,
                active_run_claude,
                story_extra,
            )
            next_story = _normalize_story_output(next_story, story_input)
            agent_run.write_json(story_path, next_story)
            issues = _story_preflight_issues(next_story, requirements, story_input)
            if not issues or preflight_attempt == repair_policy.story_preflight_attempts:
                break
            active_repair_context = _story_preflight_repair_context(
                next_story,
                issues,
                requirements,
                preflight_attempt + 1,
                active_repair_context,
                repair_policy.story_preflight_attempts,
            )
            _write_progress_safe(
                "story.preflight_repair",
                "正在修正文稿预检问题",
                percent=70,
                detail={
                    "attempt": preflight_attempt + 1,
                    "max_attempts": repair_policy.story_preflight_attempts,
                    "issues": issues,
                },
            )
            story_extra = {
                "story_input": story_input,
                "delivery_requirements": requirements,
                "repair_context": active_repair_context,
            }
        _write_progress_safe("critic.running", "正在质检正文", percent=73)
        next_critic = _dispatch_and_write(
            "critic",
            critic_path,
            critic_prompt,
            root,
            active_run_claude,
            {"story_input": story_input, "story_output": next_story, "delivery_requirements": requirements},
        )
        next_critic = _normalize_critic_report_for_story(next_critic, next_story)
        agent_run.write_json(critic_path, next_critic)
        return next_story, next_critic

    story, critic = dispatch_story_and_critic()

    delivery = _run_delivery(card, root, run_command)
    delivery_result = delivery.get("result") if isinstance(delivery.get("result"), dict) else {}
    repair_attempt = 0
    repair_intents_blocked = False
    while (
        not _delivery_complete(delivery)
        and delivery_result.get("action") == "retry"
        and repair_attempt < repair_policy.delivery_repair_attempts
    ):
        repair_attempt += 1
        _write_progress_safe(
            "delivery.retrying",
            "交付前等待修复",
            percent=65,
            detail={
                "attempt": repair_attempt,
                "max_attempts": repair_policy.delivery_repair_attempts,
                "reason": delivery_result.get("reason", ""),
            },
        )
        repair_context = _delivery_retry_context(delivery_result, story, critic, repair_attempt)
        repair_context["max_repair_attempts"] = repair_policy.delivery_repair_attempts
        repair_routing = self_repair.routing_from_delivery_result(delivery_result)
        repair_decision = _delivery_retry_decision(delivery_result)
        if not self_repair.policy_allows_route(repair_policy, repair_routing, repair_decision):
            _block_pending_repair_intents(
                run_dir,
                "repair_not_completed",
                {"delivery": _repair_intent_delivery_output(delivery)},
            )
            repair_intents_blocked = True
            break
        if repair_routing.get("rollback") == "round_progression":
            _reset_round_progression_outputs(run_dir)
            manifest = _load_manifest(run_dir)
            loop_result = _run_interactive_agent_loop(run_dir, manifest, root, active_run_claude, repair_context)
            story_input = agent_outputs.build_story_input(run_dir)
            requirements = _delivery_requirements(root)
            requirements["player_character_names"] = _player_character_names_from_story_input(story_input)
        story, critic = dispatch_story_and_critic(repair_context)
        delivery = _run_delivery(card, root, run_command)
        delivery_result = delivery.get("result") if isinstance(delivery.get("result"), dict) else {}
    delivery_complete = _delivery_complete(delivery)
    if delivery_complete and repair_attempt > 0:
        _complete_pending_repair_intents(
            run_dir,
            {"delivery": _repair_intent_delivery_output(delivery)},
        )
    elif delivery_result.get("action") == "retry" and not repair_intents_blocked:
        _block_pending_repair_intents(
            run_dir,
            "repair_not_completed",
            {"delivery": _repair_intent_delivery_output(delivery)},
        )
    return {
        "ok": delivery_complete,
        "action": "generated",
        "run_dir": str(run_dir),
        "artifacts": {
            "gm": int(loop_result.get("gm_steps", 0) or 0),
            "actors": sorted(set(loop_result.get("called_actors", []) or [])),
            "called_actors": list(loop_result.get("called_actors", []) or []),
            "story": bool(story),
            "critic": bool(critic),
        },
        "delivery": delivery,
    }


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
