"""Deterministic no-model smoke check for the multi-agent control plane."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


HIDDEN_SMOKE_PHRASE = "the pendant burns identity"
PROMOTED_CHARACTER = "SuLi"
PROMOTION_RECORD = {
    "name": PROMOTED_CHARACTER,
    "source_agent": "gm",
    "reason": "SuLi becomes central to the pendant scene.",
    "profile_seed": "A reserved classmate with occult knowledge.",
    "visibility": "character_private_and_gm",
    "activation": "current_turn",
}


def _insert_skills_path(repo: Path) -> None:
    skills_dir = str((repo / "skills").resolve())
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _contains_text(value: Any, text: str) -> bool:
    return str(text).lower() in json.dumps(value, ensure_ascii=False).lower()


def _build_input_analysis_fixture(run_dir: Path, input_payload: Dict[str, Any], input_analysis) -> Dict[str, Any]:
    raw_text = str(input_payload.get("raw_text") or "")
    role_text = str(input_payload.get("role_text") or "")
    user_instruction_text = str(input_payload.get("user_instruction_text") or "")
    analysis = {
        "schema_version": 1,
        "round_id": run_dir.name,
        "analysis_mode": "fixture",
        "source_integrity": {
            "raw_text_sha256": input_analysis.sha256_text(raw_text),
            "role_text_sha256": input_analysis.sha256_text(role_text),
            "user_instruction_text_sha256": input_analysis.sha256_text(user_instruction_text),
            "raw_preserved": True,
        },
        "semantic_units": [
            {
                "id": "fixture-role-action-1",
                "source_channel": "role_input",
                "type": "action",
                "raw_excerpt": role_text,
                "derived_summary": "The player shows interest in the pendant.",
                "confidence": 1.0,
                "visibility": "player_pov",
                "persist": False,
            },
            {
                "id": "fixture-style-guidance-1",
                "source_channel": "user_instruction",
                "type": "style_guidance",
                "raw_excerpt": user_instruction_text,
                "derived_summary": "Keep the hidden pendant truth out of actor-facing text.",
                "confidence": 1.0,
                "visibility": "gm_only",
                "persist": False,
            },
        ],
        "world_updates": {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [],
            "retcon_requests": [],
        },
        "narrative_directives": {
            "rewrite_previous_output": False,
            "expand_synopsis_before_continue": False,
            "continue_after_player_action": True,
            "must_stop_for_player_decision": True,
        },
        "routing": {
            "role_channel": role_text,
            "user_instruction_channel": user_instruction_text,
            "gm": True,
            "player": True,
            "characters": [],
        },
        "risks": [],
    }
    return input_analysis.validate_input_analysis(
        analysis,
        raw_text=raw_text,
        role_text=role_text,
        user_instruction_text=user_instruction_text,
    )


def _run_deterministic_gm_loop(run_dir: Path, agent_turn_loop) -> Dict[str, Any]:
    raw_gm_output = {
        "agent": "gm",
        "scene_beats": [{"content": "SuLi notices the pendant before the player can hide it."}],
        "events": [],
        "actor_calls": [
            {
                "call_id": "call-character-SuLi-1",
                "actor_id": "character:SuLi",
                "prompt": (
                    "You recognize the pendant burns identity, but respond only from "
                    "what SuLi can safely perceive."
                ),
                "reason": "GM must test that the hidden pendant truth is redacted.",
            }
        ],
        "parallel_groups": [],
        "world_state_delta": [{"scope": "pendant", "fact": "SuLi notices the pendant publicly."}],
        "character_promotions": [PROMOTION_RECORD],
        "decision_point": {
            "reason": "The player must choose whether to show SuLi the pendant.",
            "options": ["show the pendant", "hide the pendant"],
        },
        "stop_reason": "player_decision",
    }
    captured: Dict[str, Any] = {"raw_gm_output": raw_gm_output, "actor_packets": []}

    def dispatch(agent_key: str, packet: Dict[str, Any]) -> Dict[str, Any]:
        if agent_key == "gm":
            return raw_gm_output
        if agent_key == "character:SuLi":
            captured["actor_packets"].append(packet)
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [
                    {
                        "type": "dialogue",
                        "target": "",
                        "content": "That pendant is older than this school.",
                    },
                    {
                        "type": "memory_delta",
                        "target": "self",
                        "content": "I noticed the player carrying a pendant tied to old rites.",
                    },
                    {
                        "type": "goal_update",
                        "target": "self",
                        "content": "Find out why the pendant reacted near the archive.",
                    },
                    {
                        "type": "wait_for_gm",
                        "target": "",
                        "content": "I wait for the GM to confirm what my senses can safely know.",
                    },
                ],
                "stop_reason": "continue",
            }
        raise RuntimeError(f"unexpected deterministic dispatch target: {agent_key}")

    captured["loop_result"] = agent_turn_loop.run_interactive_loop(run_dir, dispatch, max_steps=1)
    return captured


def _write_story_and_critic_outputs(run_dir: Path) -> None:
    _write_json(
        run_dir / "story.output.json",
        {
            "content": (
                '<content>SuLi lowered her voice. "That pendant is older than this school," '
                "she said, stopping before the next choice belonged to the player.</content>"
            ),
            "character_dialogues": [
                {
                    "character": "SuLi",
                    "text": "That pendant is older than this school.",
                    "source_agent": "character:SuLi",
                }
            ],
            "metadata": {"round_id": run_dir.name, "source": "control_plane_smoke"},
        },
    )
    _write_json(
        run_dir / "critic.report.json",
        {
            "decision": "pass",
            "hard_failures": [],
            "soft_issues": [],
            "repair_instruction": "",
            "system_iteration_suggestion": "",
        },
    )


def _summary_payload(agent_id: str) -> Dict[str, Any]:
    goals = {"active": [], "paused": [], "resolved": []}
    if agent_id == "player":
        return {
            "agent_id": "player",
            "source": "self",
            "visibility": "actor",
            "long_term": {
                "self_understanding": ["I remember entering the archive beside Ada's lamp."],
                "stable_beliefs": ["The archive should be explored carefully."],
                "relationship_models": ["Ada is close enough to guide me with her lamp."],
            },
            "key_memories": [
                {
                    "content": "I entered the archive beside Ada's raised lamp.",
                    "importance": "high",
                    "details": ["Cold air leaked from the archive.", "I heard machinery behind the door."],
                }
            ],
            "short_term": [
                {
                    "content": "I am still near Ada at the archive threshold.",
                    "expires_after": "scene_end",
                }
            ],
            "goals": {
                **goals,
                "active": ["Stay close to Ada while exploring the archive."],
            },
        }

    if agent_id == "character:SuLi":
        return {
            "agent_id": "character:SuLi",
            "character_name": "SuLi",
            "source": "self",
            "visibility": "actor",
            "long_term": {
                "self_understanding": ["I am SuLi, a reserved classmate who knows old occult signs."],
                "stable_beliefs": ["The pendant scene matters because old rites respond to attention."],
                "relationship_models": ["The player may need careful warning before trusting the pendant."],
            },
            "key_memories": [
                {
                    "content": "I noticed the player carrying a pendant tied to old rites.",
                    "importance": "high",
                    "details": ["I warned that the pendant was older than the school.", "The next choice belonged to the player."],
                }
            ],
            "short_term": [
                {
                    "content": "I am near the player while the pendant remains unresolved.",
                    "expires_after": "scene_end",
                }
            ],
            "goals": {
                **goals,
                "active": ["Find out why the pendant reacted near the archive."],
            },
        }

    if agent_id.startswith("character:"):
        character_name = agent_id.split(":", 1)[1]
        return {
            "agent_id": agent_id,
            "character_name": character_name,
            "source": "self",
            "visibility": "actor",
            "long_term": {
                "self_understanding": ["I remember guiding the player into the archive with my lamp raised."],
                "stable_beliefs": ["The player needs a steady guide near the archive threshold."],
                "relationship_models": ["The player hesitates before entering unfamiliar spaces."],
            },
            "key_memories": [
                {
                    "content": "I raised my lamp and guided the player at the archive door.",
                    "importance": "high",
                    "details": ["The player stayed close.", "Cold air and machinery came from inside."],
                }
            ],
            "short_term": [
                {
                    "content": "I am beside the player with my lamp raised.",
                    "expires_after": "scene_end",
                }
            ],
            "goals": {
                **goals,
                "active": ["Keep the player close enough to protect."],
            },
        }

    return {
        "agent_id": agent_id,
        "source": "self",
        "visibility": "actor",
        "long_term": {
            "self_understanding": [f"{agent_id} completed the smoke memory organization."],
            "stable_beliefs": [],
            "relationship_models": [],
        },
        "key_memories": [],
        "short_term": [],
        "goals": goals,
    }


def _write_scheduled_memory_summaries(run_dir: Path) -> Dict[str, str]:
    manifest = _read_json(run_dir / "manifest.json")
    expected_outputs = manifest.get("expected_outputs", {})
    summaries = expected_outputs.get("memory_summaries", {})
    if not isinstance(summaries, dict) or not summaries:
        raise RuntimeError("memory summaries were not scheduled")

    written: Dict[str, str] = {}
    for agent_id, relative_path in summaries.items():
        path = run_dir / str(relative_path)
        _write_json(path, _summary_payload(str(agent_id)))
        written[str(agent_id)] = str(relative_path)
    return written


def _schedule_suli_memory_summary(card: Path, run_dir: Path, agent_memory) -> None:
    manifest = _read_json(run_dir / "manifest.json")
    agent_memory.write_memory_summary_prompts(
        card,
        run_dir,
        manifest,
        ["player", "character:SuLi"],
    )
    _write_json(run_dir / "manifest.json", manifest)


def _promotion_evidence(card: Path) -> Dict[str, Any]:
    card_data = _read_json(card / ".card_data.json")
    major = (
        card_data.get("character_orchestration", {}).get("major", [])
        if isinstance(card_data.get("character_orchestration"), dict)
        else []
    )
    profile = _read_json(card / "memory" / "characters" / "SuLi" / "profile.json")
    promoted = []
    if (
        PROMOTED_CHARACTER in major
        and profile.get("source_agent") == "gm"
        and profile.get("authoritative_setting") == PROMOTION_RECORD["profile_seed"]
    ):
        promoted.append(PROMOTED_CHARACTER)
    return {
        "promoted": promoted,
        "registered": [name for name in major if name == PROMOTED_CHARACTER],
        "profile_source_agent": profile.get("source_agent", ""),
    }


def _structured_memory_evidence(card: Path) -> Dict[str, Any]:
    memory_dir = card / "memory" / "characters" / "SuLi"
    long_term = _read_text(memory_dir / "long_term.md")
    key_memories = _read_text(memory_dir / "key_memories.md")
    short_term = _read_text(memory_dir / "short_term.md")
    goals = _read_json(memory_dir / "goals.json") if (memory_dir / "goals.json").exists() else {}
    has_suli_buckets = (
        (memory_dir / "long_term.md").exists()
        and (memory_dir / "key_memories.md").exists()
        and (memory_dir / "short_term.md").exists()
        and (memory_dir / "goals.json").exists()
        and "character:SuLi" in long_term
        and "pendant tied to old rites" in key_memories
        and "pendant remains unresolved" in short_term
        and "Find out why the pendant reacted near the archive." in json.dumps(goals, ensure_ascii=False)
    )
    return {"character:SuLi": has_suli_buckets}


def _visibility_guard_evidence(run_dir: Path, captured_loop: Dict[str, Any]) -> Dict[str, Any]:
    gm_output = _read_json(run_dir / "gm.output.json")
    story_input = _read_json(run_dir / "story.input.json")
    loop_outputs = story_input.get("loop_outputs", {})
    actor_packets = captured_loop.get("actor_packets", [])
    raw_exposed_phrase = _contains_text(captured_loop.get("raw_gm_output", {}), HIDDEN_SMOKE_PHRASE)
    sanitized_actor_fields = [gm_output.get("outputs", []), loop_outputs.get("gm", {}), actor_packets]
    redacted_actor_call = (
        raw_exposed_phrase
        and not any(_contains_text(field, HIDDEN_SMOKE_PHRASE) for field in sanitized_actor_fields)
        and any(_contains_text(field, "[redacted]") for field in sanitized_actor_fields)
    )
    return {"redacted_actor_call": redacted_actor_call}


def run_smoke(repo: Path) -> Dict[str, Any]:
    _insert_skills_path(repo)

    import agent_interactions
    import agent_memory
    import agent_outputs
    import agent_packets
    import agent_turn_loop

    with tempfile.TemporaryDirectory(prefix="airp-control-plane-smoke-") as tmp:
        temp_root = Path(tmp)
        card = temp_root / "smoke_card"
        styles_dir = temp_root / "repo" / "skills" / "styles"
        card.mkdir(parents=True)
        styles_dir.mkdir(parents=True)

        card_data = {
            "title": "Smoke Card",
            "scenario": "Archive threshold",
            "character_orchestration": {
                "major": [],
                "minor_policy": "main_agent",
                "max_parallel_subagents": 2,
            },
        }
        _write_json(card / ".card_data.json", card_data)

        input_payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": (
                "I notice the old pendant in my hand.\n\n[USER_INSTRUCTION]\n"
                f"Hidden truth: {HIDDEN_SMOKE_PHRASE}."
            ),
            "display_text": "I notice the old pendant in my hand.",
            "role_text": "I notice the old pendant in my hand.",
            "user_instruction_text": f"Hidden truth: {HIDDEN_SMOKE_PHRASE}.",
        }
        prepared = agent_packets.prepare_agent_run(
            card_folder=card,
            user_text="fallback text should not be routed",
            chat_log=[{"index": 4, "summary": "The player reached the archive door with a pendant."}],
            card_data=card_data,
            character_contexts={"characters": []},
            turn_index=5,
            input_payload=input_payload,
        )
        run_dir = Path(prepared["run_dir"])
        if run_dir.name != "round-000006":
            raise RuntimeError(f"expected round-000006, got {run_dir.name}")

        import input_analysis
        import input_analysis_apply

        analysis = _build_input_analysis_fixture(run_dir, input_payload, input_analysis)
        _write_json(run_dir / "input_analysis.output.json", analysis)
        applied_analysis = input_analysis_apply.apply_current_run(card, repo)

        captured_loop = _run_deterministic_gm_loop(run_dir, agent_turn_loop)
        _schedule_suli_memory_summary(card, run_dir, agent_memory)
        _write_story_and_critic_outputs(run_dir)
        delivery = agent_outputs.prepare_delivery(card, styles_dir)
        if not delivery.get("ok"):
            raise RuntimeError(f"delivery failed: {delivery}")

        memory_delta = agent_memory.ingest_memory_deltas(
            card,
            run_dir,
            date_str="2026-06-16 12:00",
        )
        scheduled_summaries = _write_scheduled_memory_summaries(run_dir)
        memory_summary = agent_memory.ingest_memory_summaries(card, run_dir)
        delivered = agent_outputs.mark_delivered(card)
        manifest = _read_json(run_dir / "manifest.json")
        trace = agent_interactions.summarize_for_story_input(run_dir)
        story_input = _read_json(run_dir / "story.input.json")
        story_input_analysis = (
            story_input.get("player_inputs", {}).get("input_analysis", {})
            if isinstance(story_input, dict)
            else {}
        )

        return {
            "ok": True,
            "round_id": run_dir.name,
            "input_analysis": {
                "analysis_mode": story_input_analysis.get("analysis_mode"),
                "stage": applied_analysis.get("stage"),
            },
            "visibility_guard": _visibility_guard_evidence(run_dir, captured_loop),
            "promotions": _promotion_evidence(card),
            "structured_memory": _structured_memory_evidence(card),
            "delivery": {
                "ok": bool(delivery.get("ok")),
                "mode": delivery.get("mode"),
            },
            "manifest_stage": manifest.get("stage") or delivered.get("stage"),
            "trace": trace,
            "loop": captured_loop.get("loop_result", {}),
            "memory_delta": {
                "ok": bool(memory_delta.get("ok")),
                "round_id": memory_delta.get("round_id"),
                "ingested": memory_delta.get("ingested", []),
            },
            "memory_summary": memory_summary,
            "scheduled_memory_summaries": scheduled_summaries,
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic control-plane smoke check.")
    parser.add_argument("--repo", default=".", help="Repository root containing the skills directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = Path(args.repo).resolve()
    try:
        payload = run_smoke(repo)
    except Exception as exc:
        print(f"control_plane_smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
