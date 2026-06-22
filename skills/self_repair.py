"""Self-repair mode and routing helpers for RP generation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


MODES = {"off", "analysis_only", "limited", "full"}
MODE_ALIASES = {
    "": "limited",
    "关闭": "off",
    "关": "off",
    "disabled": "off",
    "disable": "off",
    "none": "off",
    "off": "off",
    "仅分析定位": "analysis_only",
    "仅分析": "analysis_only",
    "分析定位": "analysis_only",
    "analysis": "analysis_only",
    "analyze": "analysis_only",
    "analysis_only": "analysis_only",
    "readonly": "analysis_only",
    "read_only": "analysis_only",
    "受限修复": "limited",
    "受限": "limited",
    "limited": "limited",
    "safe": "limited",
    "完全修复": "full",
    "完全": "full",
    "full": "full",
}

ROUTING_STAGES = {
    "story_composition",
    "delivery_gate",
    "gm_loop",
    "actor_agent",
    "subgm",
    "system_code",
    "unknown",
}
ROLLBACK_MODES = {"none", "story_only", "round_progression"}
RISKS = {"low", "medium", "high"}
ROUND_PROGRESSION_STAGES = {"gm_loop", "actor_agent", "subgm"}
STORY_ONLY_STAGES = {"story_composition", "delivery_gate", "unknown"}


class SelfRepairPolicy(SimpleNamespace):
    """Resolved self-repair behavior budget."""

    def __init__(
        self,
        mode: str,
        delivery_repair_attempts: int,
        critic_retry_limit: int,
        can_auto_repair: bool,
        repair_critic_block: bool,
        repair_round_progression: bool,
        allow_source_code_self_repair: bool,
    ) -> None:
        super().__init__(
            mode=mode,
            delivery_repair_attempts=delivery_repair_attempts,
            critic_retry_limit=critic_retry_limit,
            can_auto_repair=can_auto_repair,
            repair_critic_block=repair_critic_block,
            repair_round_progression=repair_round_progression,
            allow_source_code_self_repair=allow_source_code_self_repair,
        )


def normalize_mode(value: Any) -> str:
    text = str(value or "").strip()
    normalized = MODE_ALIASES.get(text) or MODE_ALIASES.get(text.lower())
    if normalized in MODES:
        return normalized
    return "limited"


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "允许", "开启", "启用"}


def _read_settings(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_policy(
    settings_path: str | Path | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> SelfRepairPolicy:
    data: dict[str, Any] = {}
    data.update(_read_settings(settings_path))
    if settings:
        data.update(dict(settings))
    env = os.environ if environ is None else environ

    mode = normalize_mode(env.get("AIRP_SELF_REPAIR_MODE") or data.get("selfRepairMode"))
    allow_source = _bool_value(data.get("allowSourceCodeSelfRepair"))
    if "AIRP_ALLOW_SOURCE_CODE_SELF_REPAIR" in env:
        allow_source = _bool_value(env.get("AIRP_ALLOW_SOURCE_CODE_SELF_REPAIR"))

    if mode == "off":
        return SelfRepairPolicy(mode, 0, 0, False, False, False, allow_source)
    if mode == "analysis_only":
        return SelfRepairPolicy(mode, 0, 0, False, False, False, allow_source)
    if mode == "full":
        return SelfRepairPolicy(mode, 3, 2, True, True, True, allow_source)
    return SelfRepairPolicy(mode, 1, 1, True, False, False, allow_source)


def normalize_repair_routing(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    stage = str(data.get("stage") or "").strip()
    if stage not in ROUTING_STAGES:
        stage = "story_composition"

    rollback = str(data.get("rollback") or "").strip()
    if rollback not in ROLLBACK_MODES:
        if stage in ROUND_PROGRESSION_STAGES:
            rollback = "round_progression"
        elif stage == "system_code":
            rollback = "none"
        else:
            rollback = "story_only"

    risk = str(data.get("risk") or "").strip()
    if risk not in RISKS:
        risk = "medium" if rollback == "round_progression" else "low"

    raw_agents = data.get("target_agents")
    if isinstance(raw_agents, list):
        target_agents = [str(item).strip() for item in raw_agents if str(item).strip()]
    else:
        target_agents = []
    if not target_agents:
        if rollback == "round_progression":
            target_agents = ["gm"]
        elif stage == "system_code":
            target_agents = ["system"]
        else:
            target_agents = ["story"]

    return {
        "stage": stage,
        "target_agents": target_agents,
        "rollback": rollback,
        "can_auto_repair": _bool_value(data.get("can_auto_repair", True)),
        "risk": risk,
    }


def routing_from_delivery_result(delivery_result: Mapping[str, Any] | None) -> dict[str, Any]:
    result = delivery_result if isinstance(delivery_result, Mapping) else {}
    detail = result.get("detail")
    if isinstance(detail, Mapping) and "repair_routing" in detail:
        return normalize_repair_routing(detail.get("repair_routing"))
    reason = str(result.get("reason") or "")
    if reason in {"agent_outputs", "artifact_schema", "handler_failed", "handler_retry", "mechanical_artifact"}:
        return normalize_repair_routing({"stage": "delivery_gate", "rollback": "story_only"})
    return normalize_repair_routing({})


def policy_allows_route(policy: SelfRepairPolicy, routing: Mapping[str, Any], decision: str = "revise") -> bool:
    if not policy.can_auto_repair:
        return False
    if not _bool_value(routing.get("can_auto_repair", True)):
        return False
    if decision == "block" and not policy.repair_critic_block:
        return False
    stage = str(routing.get("stage") or "")
    rollback = str(routing.get("rollback") or "")
    if stage == "system_code":
        return policy.mode == "full" and policy.allow_source_code_self_repair
    if rollback == "round_progression" or stage in ROUND_PROGRESSION_STAGES:
        return policy.repair_round_progression
    return True


__all__ = [
    "SelfRepairPolicy",
    "load_policy",
    "normalize_mode",
    "normalize_repair_routing",
    "policy_allows_route",
    "routing_from_delivery_result",
]
