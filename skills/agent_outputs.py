"""Read, validate, and assemble multi-agent round outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import agent_run
import agent_schemas


class AgentOutputError(RuntimeError):
    """Raised when a required agent artifact is missing or invalid."""


def _read_json_required(path: Path) -> Dict[str, Any]:
    data = agent_run.read_json(path)
    if not isinstance(data, dict):
        raise AgentOutputError(f"{path}: required JSON object is missing or invalid")
    return data


def _load_required(path: Path, validator) -> Dict[str, Any]:
    if not path.exists():
        raise AgentOutputError(f"{path.as_posix()}: required artifact is missing")
    try:
        return agent_schemas.load_json_checked(path, validator)
    except agent_schemas.ValidationError as exc:
        raise AgentOutputError(str(exc)) from exc


def _load_manifest(run_dir: Path) -> Dict[str, Any] | None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = agent_run.read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise AgentOutputError(f"{manifest_path}: manifest must be a JSON object")
    return manifest


def _write_manifest(run_dir: Path, manifest: Dict[str, Any]) -> None:
    agent_run.write_json(run_dir / "manifest.json", manifest)


def _expected_outputs(manifest: Dict[str, Any]) -> Dict[str, Any]:
    expected = manifest.get("expected_outputs")
    if not isinstance(expected, dict):
        raise AgentOutputError("manifest.expected_outputs is required")
    return expected


def _memory_deltas(player_output: Dict[str, Any], character_outputs: Dict[str, Dict[str, Any]], gm_output: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "player": player_output.get("memory_delta", []),
        "characters": {
            name: output.get("memory_delta", [])
            for name, output in character_outputs.items()
        },
        "world": gm_output.get("world_state_delta", []),
    }


def build_story_input(run_dir: str | Path) -> Dict[str, Any]:
    """Validate required subagent outputs and write `story.input.json`."""
    root = Path(run_dir)
    manifest = _load_manifest(root)
    if manifest is None:
        raise AgentOutputError(f"{root / 'manifest.json'}: manifest is missing")

    expected = _expected_outputs(manifest)
    input_payload = _read_json_required(root / "input.json")

    gm_output = _load_required(root / expected.get("gm", "gm.output.json"), agent_schemas.validate_gm_output)
    player_output = _load_required(root / expected.get("player", "player.output.json"), agent_schemas.validate_actor_output)

    character_outputs = {}
    for name, relative_path in (expected.get("characters") or {}).items():
        character_outputs[name] = _load_required(root / relative_path, agent_schemas.validate_actor_output)

    story_input = {
        "round_id": manifest.get("round_id", root.name),
        "player_inputs": {
            "raw_text": input_payload.get("raw_text", ""),
            "routed_input": input_payload.get("routed_input", {}),
            "components": (input_payload.get("routed_input") or {}).get("components", []),
        },
        "gm_output": gm_output,
        "actor_outputs": {
            "player": player_output,
            "characters": character_outputs,
        },
        "memory_deltas": _memory_deltas(player_output, character_outputs, gm_output),
        "delivery_constraints": {
            "preserve_raw_player_inputs": True,
            "preserve_character_dialogue_metadata": True,
        },
    }
    agent_run.write_json(root / "story.input.json", story_input)
    agent_run.update_manifest_stage(root, "story_ready", "Validated agent outputs and assembled story.input.json.")
    return story_input


def _retry_result(reason: str, message: str, detail: Any = None) -> Dict[str, Any]:
    result = {
        "ok": False,
        "action": "retry",
        "reason": reason,
        "message": message,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def _increment_retry(run_dir: Path, manifest: Dict[str, Any], stage: str) -> None:
    manifest["retry_count"] = int(manifest.get("retry_count", 0) or 0) + 1
    agent_run.append_manifest_stage(manifest, stage, "Agent run is blocked pending revision.")
    _write_manifest(run_dir, manifest)


def prepare_delivery(card_folder: str | Path, styles_dir: str | Path) -> Dict[str, Any]:
    """Gate delivery for the current run and mirror story output to response.txt."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None:
        return {"ok": True, "mode": "legacy"}

    manifest = _load_manifest(run_dir)
    if manifest is None:
        return {"ok": True, "mode": "legacy"}

    try:
        build_story_input(run_dir)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("agent_outputs", "Required agent outputs are missing or invalid.", str(exc))
    manifest = _load_manifest(run_dir) or manifest

    expected = _expected_outputs(manifest)
    story_path = run_dir / expected.get("story", "story.output.json")
    critic_path = run_dir / expected.get("critic", "critic.report.json")

    try:
        story_output = _load_required(story_path, agent_schemas.validate_story_output)
        critic_report = _load_required(critic_path, agent_schemas.validate_critic_report)
    except AgentOutputError as exc:
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("agent_outputs", "Required delivery artifacts are missing or invalid.", str(exc))

    decision = critic_report["decision"]
    if decision == "block":
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("critic_block", "Critic blocked delivery.", critic_report)
    if decision == "revise":
        _increment_retry(run_dir, manifest, "blocked")
        return _retry_result("critic_revise", "Critic requested revision.", critic_report)

    response_path = Path(styles_dir) / "response.txt"
    agent_run.write_text(response_path, story_output["content"])
    agent_run.append_manifest_stage(manifest, "critic_passed", "Critic passed and story output was mirrored to response.txt.")
    _write_manifest(run_dir, manifest)
    return {
        "ok": True,
        "mode": "agent_run",
        "run_dir": str(run_dir),
        "story_output": story_output,
        "critic_report": critic_report,
    }


def mark_delivered(card_folder: str | Path) -> Dict[str, Any]:
    """Mark the current agent run delivered after frontend handoff succeeds."""
    run_dir = agent_run.current_run_dir(card_folder)
    if run_dir is None or not (run_dir / "manifest.json").exists():
        return {"ok": True, "mode": "legacy"}
    manifest = agent_run.update_manifest_stage(run_dir, "delivered", "Frontend delivery and memory update completed.")
    return {
        "ok": True,
        "mode": "agent_run",
        "run_dir": str(run_dir),
        "stage": manifest.get("stage"),
    }
