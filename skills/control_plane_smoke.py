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


def _smoke_temp_parent(repo: Path) -> Path:
    parent = repo / ".tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


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
            {
                "id": "fixture-unsupported-capability-1",
                "source_channel": "user_instruction",
                "type": "system_command",
                "raw_excerpt": "weather",
                "derived_summary": "The input asks for an unsupported external weather lookup.",
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
        },
        "routing": {
            "role_channel": role_text,
            "user_instruction_channel": user_instruction_text,
            "gm": True,
            "player": True,
            "characters": ["Ada", "GateKeeper"],
        },
        "routing_requests": [],
        "capability_requests": [
            {
                "id": "unknown-capability",
                "requested_by": "input_analyst",
                "target": "weather",
                "capability": "external.weather_lookup",
                "summary": "Unsupported capability smoke fixture.",
                "reason": "Prove unsupported capabilities are audited without breaking delivery.",
                "source_channel": "user_instruction",
                "risk": "low",
                "authorization_gate": "none",
                "payload": {},
                "evidence": {"semantic_unit_ids": ["fixture-unsupported-capability-1"], "raw_excerpt": "weather"},
            }
        ],
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
            "location": "classroom",
            "sensory_channels": ["visual"],
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


def _write_smoke_actor_context_packets(run_dir: Path) -> None:
    _write_json(
        run_dir / "characters" / "Ada.context.json",
        {
            "actor_id": "character:Ada",
            "agent": "character",
            "visibility": "first_person_character",
            "gm_prompt": "",
            "gm_visibility_basis": {},
            "address_mode": "second_person_gm_narration",
            "immersive_context": "Ada stands in the classroom and remembers guiding the player near the archive.",
            "self_knowledge": {"name": "Ada", "role": "archive guide"},
            "memory": {
                "long_term": ["I guide the player near the archive."],
                "key_memories": [],
                "short_term": [],
                "goals": ["Keep the player steady near the archive."],
            },
            "sensory_context": {"location": "classroom"},
            "visible_events": [],
        },
    )


def _initial_gm_output_fixture() -> Dict[str, Any]:
    output = {
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
        "decision_point": None,
        "stop_reason": "continue",
    }
    _assert_actor_calls_have_visibility_basis(output, "raw_gm_output")
    return output


def _continuation_gm_output_fixture() -> Dict[str, Any]:
    return {
        "agent": "gm",
        "scene_beats": [{"content": "The classroom settles after Ada's warning."}],
        "events": [],
        "actor_calls": [],
        "parallel_groups": [],
        "world_state_delta": [{"scope": "pendant", "fact": "Ada's visible warning has been acknowledged."}],
        "decision_point": None,
        "stop_reason": "complete",
    }


def _actor_output_fixture() -> Dict[str, Any]:
    reply = "That pendant is older than this school. I angle it toward the classroom light and wait."
    return {
        "agent": "character",
        "agent_id": "character:Ada",
        "character_name": "Ada",
        "natural_reply": reply,
        "events": [{
            "type": "reply",
            "target": "gm",
            "content": reply,
            "metadata": {},
        }],
    }


def _side_thread_output_fixture(thread_id: str) -> Dict[str, Any]:
    if thread_id == "side_suli_rooftop":
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
    if thread_id == "side_gate_noise":
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
    raise RuntimeError(f"unexpected deterministic side-thread target: {thread_id}")


def _story_output_fixture(run_dir: Path) -> Dict[str, Any]:
    return {
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
    }


def _critic_report_fixture() -> Dict[str, Any]:
    return {
        "decision": "pass",
        "hard_failures": [],
        "soft_issues": [],
        "repair_instruction": "",
        "system_iteration_suggestion": "",
    }


def _postprocess_output_fixture(run_dir: Path) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "core": {
            "summary": "Ada warns you about the pendant as the next decision waits.",
            "options": [
                {
                    "label": "Ask Ada what she knows",
                    "source": "postprocess",
                    "requires_confirmation": False,
                },
                {
                    "label": "Hide the pendant again",
                    "source": "postprocess",
                    "requires_confirmation": False,
                },
            ],
            "current_goal": "Decide how to respond to Ada's warning.",
            "state_patch": {
                "quest": "Archive threshold",
                "stage": "player_decision",
                "time": "same morning",
                "location": "classroom",
                "env": {"lighting": "classroom light"},
                "actions": ["Ask Ada what she knows", "Hide the pendant again"],
            },
        },
        "ui_extensions": {
            "status_panels": {},
            "custom_cards": {},
            "asset_bindings": {},
        },
        "ui_extension_status": {"status": "ok", "issues": []},
        "repair_requests": [],
        "metadata": {"round_id": run_dir.name, "source": "control_plane_smoke"},
    }


def _post_round_memory_update(agent_id: str) -> Dict[str, Any]:
    if agent_id == "player":
        return {
            "agent_id": "player",
            "long_term_memories": "I remember entering the archive beside Ada's lamp. The archive should be explored carefully.",
            "key_memories": [
                {
                    "tag": "archive threshold",
                    "summary": "I entered the archive beside Ada's raised lamp.",
                    "detail": "Cold air leaked from the archive, and I heard machinery behind the door.",
                }
            ],
        }

    if agent_id == "character:SuLi":
        return {
            "agent_id": "character:SuLi",
            "character_name": "SuLi",
            "long_term_memories": "I am SuLi, a reserved classmate who knows old occult signs. The pendant scene matters because old rites respond to attention.",
            "key_memories": [
                {
                    "tag": "old pendant",
                    "summary": "I noticed the player carrying a pendant tied to old rites.",
                    "detail": "I warned that the pendant was older than the school, then left the next choice to the player.",
                }
            ],
        }

    if agent_id.startswith("character:"):
        character_name = agent_id.split(":", 1)[1]
        return {
            "agent_id": agent_id,
            "character_name": character_name,
            "long_term_memories": "I remember guiding the player into the archive with my lamp raised. The player needs a steady guide near the archive threshold.",
            "key_memories": [
                {
                    "tag": "archive guide",
                    "summary": "I raised my lamp and guided the player at the archive door.",
                    "detail": "The player stayed close while cold air and machinery came from inside.",
                }
            ],
        }

    return {
        "agent_id": agent_id,
        "long_term_memories": f"{agent_id} completed the smoke memory organization.",
        "key_memories": [],
    }


def _write_scheduled_post_round_memory_outputs(run_dir: Path) -> Dict[str, str]:
    manifest = _read_json(run_dir / "manifest.json")
    jobs = manifest.get("post_round_memory_jobs", {})
    scheduled = jobs.get("scheduled", {}) if isinstance(jobs, dict) else {}
    if not isinstance(scheduled, dict) or not scheduled:
        raise RuntimeError("post-round memory jobs were not scheduled")

    written: Dict[str, str] = {}
    for agent_id, entry in scheduled.items():
        if not isinstance(entry, dict):
            continue
        relative_path = str(entry.get("output") or "")
        if not relative_path:
            continue
        path = run_dir / relative_path
        _write_json(path, _post_round_memory_update(str(agent_id)))
        written[str(agent_id)] = relative_path
    return written


def _promotion_evidence(card: Path) -> Dict[str, Any]:
    card_data = _read_json(card / ".card_data.json")
    major = (
        card_data.get("character_orchestration", {}).get("major", [])
        if isinstance(card_data.get("character_orchestration"), dict)
        else []
    )
    objective_profile = _read_text(card / "memory" / "characters" / "SuLi" / "profile.md")
    subjective_profile = _read_text(card / "characters" / "SuLi" / "profile.md")
    promoted = []
    if (
        PROMOTED_CHARACTER in major
        and PROMOTION_RECORD["profile_seed"] in objective_profile
        and PROMOTION_RECORD["profile_seed"] in subjective_profile
    ):
        promoted.append(PROMOTED_CHARACTER)
    return {
        "promoted": promoted,
        "registered": [name for name in major if name == PROMOTED_CHARACTER],
        "profile_source_agent": "gm" if promoted else "",
    }


def _structured_memory_evidence(card: Path) -> Dict[str, Any]:
    memory_dir = card / "characters" / "Ada"
    long_term = _read_text(memory_dir / "long_term_memories.md")
    key_memories = _read_json(memory_dir / "key_memories.json") if (memory_dir / "key_memories.json").exists() else {}
    short_term = _read_text(memory_dir / "short_term_memories.md")
    serialized_key_memories = json.dumps(key_memories, ensure_ascii=False)
    has_ada_buckets = (
        (memory_dir / "long_term_memories.md").exists()
        and (memory_dir / "key_memories.json").exists()
        and (memory_dir / "short_term_memories.md").exists()
        and "I remember guiding the player" in long_term
        and "I raised my lamp" in serialized_key_memories
        and short_term == ""
    )
    return {"character:Ada": has_ada_buckets}


def _visibility_guard_evidence(run_dir: Path, captured_loop: Dict[str, Any]) -> Dict[str, Any]:
    gm_output = _read_json(run_dir / "gm.output.json")
    story_input = _read_json(run_dir / "artifacts" / "story.input.json")
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
    if not side_thread_results:
        side_thread_results = [
            {
                "ok": True,
                "thread_id": str(item.get("thread_id") or ""),
                "status": str(item.get("status") or ""),
                "steps": int((item.get("last_message") or {}).get("sequence") or 0)
                if isinstance(item.get("last_message"), dict)
                else 0,
                "called_actors": [],
            }
            for item in summaries
            if item.get("thread_id")
        ]
        side_thread_results.sort(key=lambda item: str(item.get("thread_id") or ""))
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
    import agent_intents
    import agent_lifecycle
    import agent_memory
    import agent_messages
    import agent_outputs
    import agent_packets
    import agent_schemas
    import agent_snapshots
    import rp_generate_cli
    import round_state
    import subgm_threads

    with tempfile.TemporaryDirectory(prefix="airp-control-plane-smoke-", dir=_smoke_temp_parent(repo)) as tmp:
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
        snapshot = agent_snapshots.create_snapshot(card, "round-smoke", reason="control_plane_smoke")

        input_payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": (
                "I notice the old pendant in my hand.\n\n[USER_INSTRUCTION]\n"
                f"Hidden truth: {HIDDEN_SMOKE_PHRASE}. Also check the weather."
            ),
            "display_text": "I notice the old pendant in my hand.",
            "role_text": "I notice the old pendant in my hand.",
            "user_instruction_text": f"Hidden truth: {HIDDEN_SMOKE_PHRASE}. Also check the weather.",
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

        analysis = _build_input_analysis_fixture(run_dir, input_payload, input_analysis)
        _write_json(run_dir / "input_analysis.output.json", analysis)
        _ensure_smoke_character_contexts(run_dir)
        _write_smoke_actor_context_packets(run_dir)

        captured_loop: Dict[str, Any] = {
            "raw_gm_output": _initial_gm_output_fixture(),
            "actor_packets": [],
            "gm_packets": [],
            "critic_context": {},
            "postprocess_context": {},
            "loop_result": {
                "ok": True,
                "gm_steps": 0,
                "called_actors": [],
                "stop_reason": "",
                "side_thread_results": [],
            },
        }
        delivery: Dict[str, Any] = {}
        original_dispatch = rp_generate_cli._dispatch_agent_payload
        original_delivery = rp_generate_cli._run_delivery

        def fake_dispatch(
            agent_key,
            prompt_text,
            cwd,
            run_claude,
            extra_context=None,
            attempts=2,
            initial_error=None,
        ):
            context = extra_context if isinstance(extra_context, dict) else {}
            if agent_key == "gm":
                packet = context.get("packet")
                if not isinstance(packet, dict):
                    packet = context.get("loop_packet")
                if isinstance(packet, dict):
                    captured_loop["gm_packets"].append(packet)
                loop_result = captured_loop["loop_result"]
                loop_result["gm_steps"] = int(loop_result.get("gm_steps") or 0) + 1
                if loop_result["gm_steps"] == 1:
                    return captured_loop["raw_gm_output"]
                loop_result["stop_reason"] = "complete"
                return _continuation_gm_output_fixture()
            if agent_key.startswith("subGM:"):
                thread_id = agent_key.split(":", 1)[1]
                return _side_thread_output_fixture(thread_id)
            if agent_key == "projection":
                projection_packet = context.get("projection_packet")
                if not isinstance(projection_packet, dict):
                    raise RuntimeError("deterministic projection dispatch did not receive a projection packet")
                return {
                    "decision": "pass",
                    "target_actor_id": str(projection_packet.get("target_actor_id") or ""),
                    "source_call_id": str(projection_packet.get("source_call_id") or ""),
                    "final_actor_message": str(projection_packet.get("requested_actor_message") or ""),
                    "feedback": "",
                }
            if agent_key == "character:Ada":
                actor_packet = context.get("actor_packet")
                if not isinstance(actor_packet, dict):
                    actor_packet = context.get("packet")
                if not isinstance(actor_packet, dict):
                    actor_packet = context.get("loop_packet")
                if not isinstance(actor_packet, dict):
                    raise RuntimeError("deterministic actor dispatch did not receive an actor packet")
                captured_loop["actor_packets"].append(actor_packet)
                _assert_actor_packet_visibility_basis(actor_packet)
                if actor_packet.get("gm_prompt") != VISIBLE_ADA_PROMPT:
                    raise RuntimeError(
                        "actor packet prompt is not the expected visible-only prompt: "
                        f"{actor_packet.get('gm_prompt')!r}"
                    )
                if "burns identity" in json.dumps(actor_packet, ensure_ascii=False).lower():
                    raise RuntimeError("hidden pendant identity text leaked into actor packet")
                captured_loop["loop_result"].setdefault("called_actors", []).append("character:Ada")
                return _actor_output_fixture()
            if agent_key == "story":
                return _story_output_fixture(run_dir)
            if agent_key == "critic":
                captured_loop["critic_context"] = context
                return _critic_report_fixture()
            if agent_key == "postprocess":
                postprocess_context = context.get("postprocess_context")
                captured_loop["postprocess_context"] = (
                    postprocess_context if isinstance(postprocess_context, dict) else {}
                )
                return _postprocess_output_fixture(run_dir)
            raise RuntimeError(f"unexpected deterministic dispatch target: {agent_key}")

        def fake_delivery(card_folder, root_dir, run_command):
            result = agent_outputs.prepare_delivery(card_folder, styles_dir)
            delivery.clear()
            delivery.update(result)
            return result

        try:
            rp_generate_cli._dispatch_agent_payload = fake_dispatch
            rp_generate_cli._run_delivery = fake_delivery

            round_result = rp_generate_cli.run_round(
                card,
                repo,
                run_claude=lambda *args: "",
                run_command=lambda *args, **kwargs: None,
            )
            if not round_result.get("ok") or round_result.get("action") != "generated":
                raise RuntimeError(f"thin runtime smoke failed: {round_result}")
            runtime_result = round_result.get("runtime") if isinstance(round_result.get("runtime"), dict) else {}
            applied_analysis = (
                round_result.get("input_analysis")
                if isinstance(round_result.get("input_analysis"), dict)
                else {}
            )
            capability_result = applied_analysis.get("capability_requests_result", {})
            if not isinstance(capability_result, dict):
                capability_result = {}
        finally:
            rp_generate_cli._dispatch_agent_payload = original_dispatch
            rp_generate_cli._run_delivery = original_delivery

        if not delivery.get("ok"):
            raise RuntimeError(f"delivery failed: {delivery}")

        memory_delta = agent_memory.ingest_memory_deltas(
            card,
            run_dir,
            date_str="2026-06-16 12:00",
        )
        post_round_jobs = agent_memory.schedule_post_round_memory_jobs(card, run_dir)
        scheduled_post_round_outputs = _write_scheduled_post_round_memory_outputs(run_dir)
        post_round_memory = agent_memory.ingest_post_round_memory_jobs(card, run_dir)
        round_state.write_progress_state(
            styles_dir,
            "agent_lifecycle.cleanup",
            run_id=run_dir.name,
            run_dir=run_dir,
            manifest_message="Control-plane smoke lifecycle cleanup started.",
        )
        cleanup_result = agent_lifecycle.cleanup_round_agents(card, run_dir, reason="delivered")
        if not cleanup_result.get("ok"):
            raise RuntimeError(f"lifecycle cleanup failed: {cleanup_result}")
        cleanup_manifest = _read_json(run_dir / "manifest.json").get("agent_lifecycle_cleanup")
        if not isinstance(cleanup_manifest, dict):
            raise RuntimeError("lifecycle cleanup evidence missing from manifest")
        if cleanup_manifest != cleanup_result:
            raise RuntimeError(
                "lifecycle cleanup manifest evidence does not match cleanup result"
            )
        round_state.write_progress_state(
            styles_dir,
            "complete",
            run_id=run_dir.name,
            run_dir=run_dir,
            manifest_message="Control-plane smoke complete.",
        )
        progress_path = styles_dir / "progress.json"
        progress = _read_json(progress_path) if progress_path.exists() else {}
        manifest = _read_json(run_dir / "manifest.json")
        cleanup_evidence = manifest.get("agent_lifecycle_cleanup")
        if cleanup_evidence != cleanup_result:
            raise RuntimeError("final manifest cleanup evidence changed unexpectedly")
        status = manifest.get("status") if isinstance(manifest.get("status"), list) else []
        states = [
            str(item.get("stage") or "")
            for item in status
            if isinstance(item, dict)
        ]
        trace = agent_interactions.summarize_for_story_input(run_dir)
        story_input = _read_json(run_dir / "artifacts" / "story.input.json")
        story_input_analysis = (
            story_input.get("player_inputs", {}).get("input_analysis", {})
            if isinstance(story_input, dict)
            else {}
        )
        gm_output = _read_json(run_dir / "gm.output.json")
        postprocess_output = _read_json(run_dir / "artifacts" / "postprocess.output.json")
        post_round_manifest = manifest.get("post_round_memory_jobs", {})
        if not isinstance(post_round_manifest, dict):
            post_round_manifest = {}
        capability_artifacts = capability_result.get("artifacts", [])
        if not isinstance(capability_artifacts, list):
            capability_artifacts = []
        capability_results = capability_result.get("results", [])
        if not isinstance(capability_results, list):
            capability_results = []
        capability_audits = []
        for artifact in capability_artifacts:
            audit = _read_json(run_dir / artifact)
            capability_audits.append(
                {
                    "artifact": artifact,
                    "status": audit.get("status"),
                    "capability": audit.get("capability"),
                }
            )
        messages = agent_messages.read_messages(run_dir)
        intent_counts = {
            "pending": len(agent_intents.list_intents(run_dir, "pending")),
            "accepted": len(agent_intents.list_intents(run_dir, "accepted")),
            "rejected": len(agent_intents.list_intents(run_dir, "rejected")),
            "completed": len(agent_intents.list_intents(run_dir, "completed")),
            "blocked": len(agent_intents.list_intents(run_dir, "blocked")),
        }

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
            "runtime": runtime_result,
            "capability_requests": {
                "unsupported_count": sum(
                    1
                    for item in capability_results
                    if isinstance(item, dict)
                    if item.get("status") == "unsupported_capability"
                ),
                "artifacts": capability_artifacts,
                "audits": capability_audits,
            },
            "quality_metrics": captured_loop.get("critic_context", {}).get("quality_metrics", {}),
            "story": _story_evidence(run_dir),
            "postprocess": postprocess_output,
            "manifest_stage": manifest.get("stage"),
            "progress": {
                "schema_version": progress.get("schema_version"),
                "state": progress.get("state") or progress.get("stage"),
                "states": states,
            },
            "agent_lifecycle_cleanup": cleanup_evidence,
            "trace": trace,
            "loop": captured_loop.get("loop_result", {}),
            "memory_delta": {
                "ok": bool(memory_delta.get("ok")),
                "round_id": memory_delta.get("round_id"),
                "ingested": memory_delta.get("ingested", []),
            },
            "post_round_memory": post_round_memory,
            "scheduled_post_round_memory_outputs": scheduled_post_round_outputs,
            "post_round_memory_jobs": {
                "status": post_round_manifest.get("status")
                or ("pending" if post_round_jobs.get("scheduled") else "not_required"),
                "scheduled_count": len(post_round_jobs.get("scheduled", [])),
            },
            "messages": {
                "total": len(messages),
                "types": sorted({str(item.get("type") or "") for item in messages}),
            },
            "intents": intent_counts,
            "snapshot": snapshot,
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
