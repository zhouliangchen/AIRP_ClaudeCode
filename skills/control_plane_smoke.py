"""Deterministic no-model smoke check for the multi-agent control plane."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


HIDDEN_SMOKE_PHRASE = "the pendant burns identity"
VISIBLE_ADA_PROMPT = "You notice the player trying to hide the pendant and respond only from Ada's visible perception."
PROMOTED_CHARACTER = "SuLi"
PROMOTION_RECORD = {
    "name": PROMOTED_CHARACTER,
    "source_agent": "gm",
    "reason": "SuLi becomes central to the pendant scene.",
    "profile_seed": "A reserved classmate with occult knowledge.",
    "visibility": "character_private_and_gm",
    "activation": "current_turn",
}


def _side_thread_start_command(
    thread_id: str,
    *,
    title: str,
    outline: str,
    location: str,
    objective: str,
    allowed_character: str,
) -> Dict[str, Any]:
    return {
        "action": "start",
        "thread_id": thread_id,
        "title": title,
        "outline": outline,
        "time_window": "same morning",
        "location": location,
        "objective": objective,
        "allowed_characters": [allowed_character],
        "forbidden_characters": ["player"],
        "priority": "normal",
        "message": "Start this scoped side thread now.",
        "metadata": {"source": "control_plane_smoke"},
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


def _assert_actor_calls_have_visibility_basis(output: Dict[str, Any], label: str) -> None:
    for index, call in enumerate(output.get("actor_calls", [])):
        if not isinstance(call, dict):
            continue
        basis = call.get("visibility_basis")
        if not isinstance(basis, dict) or not str(basis.get("summary") or "").strip():
            raise RuntimeError(f"{label}.actor_calls[{index}] missing visibility_basis.summary")


def _actor_packet_visibility_basis_evidence(packet: Dict[str, Any] | None) -> Dict[str, Any]:
    basis = packet.get("gm_visibility_basis", {}) if isinstance(packet, dict) else {}
    if not isinstance(basis, dict):
        basis = {}
    visible_to = basis.get("visible_to")
    sensory_channels = basis.get("sensory_channels")
    return {
        "mode": basis.get("mode"),
        "summary_present": bool(str(basis.get("summary") or "").strip()),
        "summary_hidden_text_absent": not _contains_text(basis.get("summary", ""), HIDDEN_SMOKE_PHRASE),
        "location": basis.get("location"),
        "visible_to": visible_to if isinstance(visible_to, list) else [],
        "sensory_channels": sensory_channels if isinstance(sensory_channels, list) else [],
        "target_actor": basis.get("target_actor"),
    }


def _assert_actor_packet_visibility_basis(packet: Dict[str, Any]) -> None:
    evidence = _actor_packet_visibility_basis_evidence(packet)
    expected = {
        "mode": "location",
        "location": "classroom",
        "visible_to": ["character:Ada"],
        "sensory_channels": ["visual"],
        "target_actor": "character:Ada",
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise RuntimeError(f"unexpected actor packet visibility basis {key}: {evidence!r}")
    if not evidence["summary_present"]:
        raise RuntimeError(f"actor packet visibility basis summary is empty: {evidence!r}")
    if not evidence["summary_hidden_text_absent"]:
        raise RuntimeError("hidden pendant identity text leaked into actor packet visibility basis")


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


def _smoke_character_contexts() -> list[Dict[str, Any]]:
    return [
        {
            "name": "Ada",
            "role": "archive guide",
            "memory": {
                "long_term": ["I guide the player near the archive."],
                "key_memories": [],
                "short_term": [],
                "goals": ["Keep the player steady near the archive."],
            },
        },
        {
            "name": "GateKeeper",
            "role": "quiet gate watcher",
            "memory": {
                "long_term": ["I know the sound of the old school gate."],
                "key_memories": [],
                "short_term": [],
                "goals": ["Find the source of gate noises."],
            },
        },
    ]


def _ensure_smoke_character_contexts(run_dir: Path) -> None:
    input_json = _read_json(run_dir / "input.json")
    contexts = input_json.get("character_contexts")
    if not isinstance(contexts, dict):
        contexts = {}
    characters = contexts.get("characters")
    if not isinstance(characters, list):
        characters = []
    existing = {
        str(item.get("name") or item.get("character_name") or "")
        for item in characters
        if isinstance(item, dict)
    }
    for context in _smoke_character_contexts():
        if context["name"] not in existing:
            characters.append(context)
    contexts["characters"] = characters
    input_json["character_contexts"] = contexts
    _write_json(run_dir / "input.json", input_json)


def _run_deterministic_gm_loop(run_dir: Path, agent_turn_loop) -> Dict[str, Any]:
    raw_gm_output = {
        "agent": "gm",
        "scene_beats": [{"content": "Ada notices the pendant before the player can hide it."}],
        "events": [],
        "actor_calls": [
            {
                "call_id": "call-character-Ada-1",
                "actor_id": "character:Ada",
                "prompt": VISIBLE_ADA_PROMPT,
                "reason": f"Ada can see the player's hand move toward the pendant. Smoke hidden leak: {HIDDEN_SMOKE_PHRASE}.",
                "visibility_basis": {
                    "mode": "location",
                    "summary": f"Ada is nearby and can see the player move the pendant. Smoke hidden leak: {HIDDEN_SMOKE_PHRASE}.",
                    "location": "classroom",
                    "visible_to": ["character:Ada"],
                    "sensory_channels": ["visual"],
                    "target_actor": "character:Ada",
                },
            }
        ],
        "parallel_groups": [],
        "world_state_delta": [{"scope": "pendant", "fact": "Ada notices the pendant publicly."}],
        "character_promotions": [PROMOTION_RECORD],
        "subgm_commands": [
            _side_thread_start_command(
                "side_suli_rooftop",
                title="Rooftop warning",
                outline="SuLi checks whether the rooftop sigil reacts to the pendant.",
                location="school rooftop",
                objective="Resolve the off-screen rooftop clue and report it to GM.",
                allowed_character="character:SuLi",
            ),
            _side_thread_start_command(
                "side_gate_noise",
                title="Gate noise",
                outline="GateKeeper traces the sound at the school gate.",
                location="school gate",
                objective="Pause once the gate clue reaches a GM decision point.",
                allowed_character="character:GateKeeper",
            ),
        ],
        "decision_point": {
            "reason": "The player must choose whether to show Ada the pendant.",
            "options": ["show the pendant", "hide the pendant"],
        },
        "stop_reason": "player_decision",
    }
    _assert_actor_calls_have_visibility_basis(raw_gm_output, "raw_gm_output")
    captured: Dict[str, Any] = {"raw_gm_output": raw_gm_output, "actor_packets": []}

    def dispatch(agent_key: str, packet: Dict[str, Any]) -> Dict[str, Any]:
        if agent_key == "gm":
            return raw_gm_output
        if agent_key == "subGM:side_suli_rooftop":
            return {
                "agent": "subGM",
                "thread_id": "side_suli_rooftop",
                "status": "completed",
                "scene_beats": [{"content": "SuLi finds chalk dust beside the rooftop vent."}],
                "events": [{"type": "scene", "content": "The rooftop sigil no longer glows."}],
                "actor_calls": [],
                "messages_to_gm": [{"content": "The rooftop clue is complete and ready to merge."}],
                "world_state_delta": [{"scope": "rooftop", "fact": "The rooftop sigil is dormant."}],
                "character_usage": ["character:SuLi"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["Use only if GM merges the side-thread clue."],
                "next_resume_point": "",
            }
        if agent_key == "subGM:side_gate_noise":
            return {
                "agent": "subGM",
                "thread_id": "side_gate_noise",
                "status": "paused",
                "scene_beats": [{"content": "GateKeeper hears metal scrape behind the locked gate."}],
                "events": [{"type": "scene", "content": "The gate noise stops before anyone arrives."}],
                "actor_calls": [],
                "messages_to_gm": [{"content": "Gate thread paused at the locked gate."}],
                "world_state_delta": [{"scope": "gate", "fact": "A metal scrape came from behind the gate."}],
                "character_usage": ["character:GateKeeper"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["Resume only when GM wants to reveal the gate clue."],
                "next_resume_point": "resume when the main scene moves toward the school gate",
            }
        if agent_key == "character:Ada":
            captured["actor_packets"].append(packet)
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
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
    if not captured["actor_packets"]:
        raise RuntimeError("deterministic actor dispatch did not capture actor packets")
    actor_packet = captured["actor_packets"][0]
    _assert_actor_packet_visibility_basis(actor_packet)
    if actor_packet.get("gm_prompt") != VISIBLE_ADA_PROMPT:
        raise RuntimeError(f"actor packet prompt is not the expected visible-only prompt: {actor_packet.get('gm_prompt')!r}")
    if "burns identity" in json.dumps(captured["actor_packets"], ensure_ascii=False).lower():
        raise RuntimeError("hidden pendant identity text leaked into actor packets")
    return captured


def _write_story_and_critic_outputs(run_dir: Path) -> None:
    _write_json(
        run_dir / "story.output.json",
        {
            "content": (
                '<content>Ada lowered her voice. "That pendant is older than this school," '
                "she said, stopping before the next choice belonged to the player.</content>"
            ),
            "character_dialogues": [
                {
                    "character": "Ada",
                    "text": "That pendant is older than this school.",
                    "source_agent": "character:Ada",
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
    input_json = _read_json(run_dir / "input.json")
    loop_outputs = story_input.get("loop_outputs", {})
    actor_packets = captured_loop.get("actor_packets", [])
    first_actor_packet = actor_packets[0] if actor_packets and isinstance(actor_packets[0], dict) else {}
    raw_actor_calls = (
        captured_loop.get("raw_gm_output", {}).get("actor_calls", [])
        if isinstance(captured_loop.get("raw_gm_output"), dict)
        else []
    )
    hidden_source_present = _contains_text(input_json, HIDDEN_SMOKE_PHRASE)
    raw_actor_facing_fields = [
        {
            "prompt": call.get("prompt"),
            "reason": call.get("reason"),
            "visibility_basis": call.get("visibility_basis"),
            "metadata": call.get("metadata"),
        }
        for call in raw_actor_calls
        if isinstance(call, dict)
    ]
    sanitized_loop_fields = [gm_output.get("outputs", []), loop_outputs.get("gm", {})]
    raw_actor_facing_hidden_leak_detected = any(
        _contains_text(field, HIDDEN_SMOKE_PHRASE)
        for field in raw_actor_facing_fields
    )
    sanitized_loop_output_hidden_text_absent = not any(
        _contains_text(field, HIDDEN_SMOKE_PHRASE)
        for field in sanitized_loop_fields
    )
    actor_packet_hidden_text_absent = "burns identity" not in json.dumps(actor_packets, ensure_ascii=False).lower()
    actor_packet_prompt_visible_only = first_actor_packet.get("gm_prompt") == VISIBLE_ADA_PROMPT
    actor_packet_visibility_basis = _actor_packet_visibility_basis_evidence(first_actor_packet)
    sanitized_actor_facing_redaction_marker_present = any(
        _contains_text(field, "[redacted]")
        for field in [*sanitized_loop_fields, actor_packets]
    )
    redacted_actor_call = (
        hidden_source_present
        and raw_actor_facing_hidden_leak_detected
        and sanitized_loop_output_hidden_text_absent
        and actor_packet_hidden_text_absent
        and actor_packet_prompt_visible_only
        and sanitized_actor_facing_redaction_marker_present
    )
    return {
        "redacted_actor_call": redacted_actor_call,
        "raw_actor_facing_hidden_leak_detected": raw_actor_facing_hidden_leak_detected,
        "sanitized_loop_output_hidden_text_absent": sanitized_loop_output_hidden_text_absent,
        "sanitized_actor_facing_redaction_marker_present": sanitized_actor_facing_redaction_marker_present,
        "actor_packet_visibility_basis": actor_packet_visibility_basis,
        "actor_packet_hidden_text_absent": actor_packet_hidden_text_absent,
        "actor_packet_prompt_visible_only": actor_packet_prompt_visible_only,
    }


def _subgm_promotion_blocked(agent_schemas) -> bool:
    try:
        agent_schemas.validate_subgm_output(
            {
                "agent": "subGM",
                "thread_id": "side_suli_rooftop",
                "status": "completed",
                "scene_beats": [],
                "events": [],
                "actor_calls": [],
                "messages_to_gm": [],
                "world_state_delta": [],
                "character_usage": ["character:SuLi"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["negative promotion smoke"],
                "next_resume_point": "",
                "character_promotions": [PROMOTION_RECORD],
            }
        )
    except agent_schemas.ValidationError:
        return True
    return False


def _subgm_evidence(
    run_dir: Path,
    side_thread_results: list[Dict[str, Any]],
    agent_schemas,
    subgm_threads,
) -> Dict[str, Any]:
    summaries = subgm_threads.load_thread_summaries(run_dir)
    statuses = [str(item.get("status") or "") for item in summaries]
    threads = {
        str(item.get("thread_id")): {
            "status": str(item.get("status") or ""),
            "last_message": item.get("last_message") if isinstance(item.get("last_message"), dict) else {},
            "next_resume_point": str(item.get("next_resume_point") or ""),
        }
        for item in summaries
        if item.get("thread_id")
    }
    allowed_sets = [
        item.get("allowed_characters", [])
        for item in summaries
        if isinstance(item.get("allowed_characters", []), list)
    ]
    forbidden_sets = [
        item.get("forbidden_characters", [])
        for item in summaries
        if isinstance(item.get("forbidden_characters", []), list)
    ]
    player_excluded = bool(summaries) and all(
        "player" not in allowed for allowed in allowed_sets
    ) and all(
        "player" in forbidden for forbidden in forbidden_sets
    )
    return {
        "started_count": len(summaries),
        "completed_count": statuses.count("completed"),
        "paused_count": statuses.count("paused"),
        "player_excluded": player_excluded,
        "promotion_blocked": _subgm_promotion_blocked(agent_schemas),
        "threads": threads,
        "results": side_thread_results,
    }


def _story_evidence(run_dir: Path) -> Dict[str, Any]:
    story_output = _read_json(run_dir / "story.output.json")
    dialogues = story_output.get("character_dialogues", [])
    if not isinstance(dialogues, list):
        dialogues = []
    source_agents = [
        str(item.get("source_agent") or "")
        for item in dialogues
        if isinstance(item, dict) and item.get("source_agent")
    ]

    gm_output = _read_json(run_dir / "gm.output.json")
    main_actor_calls = {
        str(call.get("actor_id") or "")
        for output in gm_output.get("outputs", [])
        if isinstance(output, dict)
        for call in output.get("actor_calls", [])
        if isinstance(call, dict)
    }
    side_actor_outputs = set()
    side_root = run_dir / "side_threads"
    if side_root.exists():
        for path in side_root.glob("*/actor.outputs.json"):
            data = _read_json(path)
            side_actor_outputs.update(str(key) for key in data if key)

    backed_sources = main_actor_calls | side_actor_outputs
    return {
        "character_dialogue_source_agents": source_agents,
        "character_dialogues_source_backed": bool(source_agents)
        and all(source in backed_sources for source in source_agents),
    }


def run_smoke(repo: Path) -> Dict[str, Any]:
    _insert_skills_path(repo)

    import agent_interactions
    import agent_memory
    import agent_outputs
    import agent_packets
    import agent_schemas
    import agent_turn_loop
    import subgm_threads

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
            character_contexts={"characters": _smoke_character_contexts()},
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
        _ensure_smoke_character_contexts(run_dir)

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
            "subgm": _subgm_evidence(
                run_dir,
                captured_loop.get("loop_result", {}).get("side_thread_results", []),
                agent_schemas,
                subgm_threads,
            ),
            "promotions": _promotion_evidence(card),
            "structured_memory": _structured_memory_evidence(card),
            "delivery": {
                "ok": bool(delivery.get("ok")),
                "mode": delivery.get("mode"),
            },
            "story": _story_evidence(run_dir),
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
