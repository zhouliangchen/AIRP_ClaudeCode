"""Deterministic next-action advice for multi-agent RP runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


PathSpec = Tuple[str, str]
VALID_CRITIC_DECISIONS = {"pass", "revise", "block"}


def _read_json_object(path: Path) -> Dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _missing_manifest(reason: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "stage": "missing_manifest",
        "next_action": "create_agent_run",
        "missing_required": [{"path": "manifest.json"}],
        "reason": reason,
    }


def _advice(
    manifest: Dict[str, Any],
    next_action: str,
    missing_required: Iterable[Dict[str, str]] = (),
) -> Dict[str, Any]:
    return {
        "stage": str(manifest.get("stage") or "unknown"),
        "next_action": next_action,
        "missing_required": list(missing_required),
    }


def _expected_path(expected: Dict[str, Any], key: str, default: str) -> str | None:
    if key not in expected:
        return default
    value = expected.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _expected_artifacts(manifest: Dict[str, Any]) -> Tuple[List[PathSpec], List[PathSpec]] | None:
    expected = manifest.get("expected_outputs")
    if not isinstance(expected, dict):
        return None

    gm_path = _expected_path(expected, "gm", "gm.output.json")
    player_path = _expected_path(expected, "player", "player.output.json")
    story_path = _expected_path(expected, "story", "story.output.json")
    critic_path = _expected_path(expected, "critic", "critic.report.json")
    if not all([gm_path, player_path, story_path, critic_path]):
        return None

    characters = expected.get("characters") or {}
    if not isinstance(characters, dict):
        return None

    actor_specs: List[PathSpec] = [("gm", gm_path), ("player", player_path)]
    for name in sorted(characters):
        relative_path = characters.get(name)
        if not isinstance(relative_path, str) or not relative_path.strip():
            return None
        actor_specs.append((f"character:{name}", relative_path))

    delivery_specs: List[PathSpec] = [("story", story_path), ("critic", critic_path)]
    return actor_specs, delivery_specs


def _path_label(relative_path: str) -> str:
    return Path(relative_path).as_posix()


def _missing_artifacts(run_dir: Path, specs: Iterable[PathSpec]) -> List[Dict[str, str]]:
    missing = []
    for agent, relative_path in specs:
        path = run_dir / relative_path
        if not path.exists():
            missing.append({"agent": agent, "path": _path_label(relative_path)})
            continue
        if agent == "critic":
            critic_report = _read_json_object(path) or {}
            if critic_report.get("decision") not in VALID_CRITIC_DECISIONS:
                missing.append({"agent": agent, "path": _path_label(relative_path)})
    return missing


def _retry_count(manifest: Dict[str, Any]) -> int:
    try:
        return int(manifest.get("retry_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def advise_next_actions(run_dir: str | Path) -> Dict[str, Any]:
    """Return the next deterministic action for a multi-agent run directory."""
    root = Path(run_dir)
    manifest = _read_json_object(root / "manifest.json")
    if manifest is None:
        return _missing_manifest("manifest_missing_or_invalid")

    stage = str(manifest.get("stage") or "unknown")
    if stage == "delivered":
        return _advice(manifest, "none")

    artifacts = _expected_artifacts(manifest)
    if artifacts is None:
        return _missing_manifest("manifest_expected_outputs_invalid")
    actor_specs, delivery_specs = artifacts

    if stage == "blocked":
        critic_path = dict(delivery_specs).get("critic", "critic.report.json")
        critic_report = _read_json_object(root / critic_path) or {}
        critic_decision = critic_report.get("decision")
        if critic_decision in {"revise", "block"}:
            advice = _advice(manifest, "repair_from_critic")
            advice["critic_decision"] = critic_decision
            advice["retry_count"] = _retry_count(manifest)
            return advice

    story_input_ready = stage == "story_ready" or (root / "story.input.json").exists()
    if story_input_ready:
        missing_delivery = _missing_artifacts(root, delivery_specs)
        if missing_delivery:
            return _advice(manifest, "dispatch_story_and_critic", missing_delivery)
        return _advice(manifest, "run_delivery_gate")

    missing_actor_outputs = _missing_artifacts(root, actor_specs)
    if missing_actor_outputs:
        return _advice(manifest, "dispatch_agent_outputs", missing_actor_outputs)

    return _advice(manifest, "build_story_input")
