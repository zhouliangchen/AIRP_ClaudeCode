"""Deterministic no-model smoke check for the multi-agent control plane."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


def _insert_skills_path(repo: Path) -> None:
    skills_dir = str((repo / "skills").resolve())
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
                "derived_summary": "The player enters the archive.",
                "confidence": 1.0,
                "visibility": "player_pov",
                "persist": False,
            },
            {
                "id": "fixture-style-guidance-1",
                "source_channel": "user_instruction",
                "type": "style_guidance",
                "raw_excerpt": user_instruction_text,
                "derived_summary": "Keep the scene quiet and inspectable.",
                "confidence": 1.0,
                "visibility": "gm_only",
                "persist": False,
            },
        ],
        "world_updates": {
            "hidden_facts": [],
            "public_facts": [],
            "important_characters": [
                {
                    "id": "fixture-character-ada",
                    "name": "Ada",
                    "text": "Ada is cautious and carries the lamp.",
                    "visibility": "character_private_and_gm",
                    "status": "active",
                }
            ],
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
            "characters": ["Ada"],
        },
        "risks": [],
    }
    return input_analysis.validate_input_analysis(
        analysis,
        raw_text=raw_text,
        role_text=role_text,
        user_instruction_text=user_instruction_text,
    )


def _write_agent_outputs(run_dir: Path) -> None:
    _write_json(
        run_dir / "gm.output.json",
        {
            "agent": "gm_loop",
            "outputs": [
                {
                    "agent": "gm",
                    "scene_beats": [{"content": "The archive door opens onto a quiet lamplit room."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "Respond to entering the archive from the player's first-person view.",
                            "reason": "The player submitted the current action.",
                        },
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "React as Ada while carrying the lamp.",
                            "reason": "Ada is a current important character in the scene.",
                        },
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [{"scope": "archive", "fact": "the entry door is open"}],
                    "decision_point": {
                        "reason": "The player must choose whether to inspect the shelves.",
                        "options": ["inspect shelves", "wait at threshold"],
                    },
                    "stop_reason": "player_decision",
                }
            ],
        },
    )
    _write_json(
        run_dir / "actor.outputs.json",
        {
            "player": [
                {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [
                        {"type": "action", "target": "", "content": "I step into the archive and keep close to Ada's lamp."},
                        {
                            "type": "memory_delta",
                            "target": "self",
                            "content": "I entered the archive while following Ada's lamp.",
                        },
                    ],
                    "stop_reason": "continue",
                }
            ],
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [
                        {"type": "dialogue", "target": "player", "content": "Stay close to the lamp."},
                        {
                            "type": "memory_delta",
                            "target": "self",
                            "content": "I saw the player enter the archive beside my lamp.",
                        },
                    ],
                    "stop_reason": "continue",
                }
            ],
        },
    )
    _write_json(
        run_dir / "story.output.json",
        {
            "content": '<content>Ada raised the lamp. "Stay close to the lamp," she said, and the player stepped into the archive.</content>',
            "character_dialogues": [
                {
                    "character": "Ada",
                    "text": "Stay close to the lamp.",
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
    if agent_id == "player":
        return {
            "agent_id": "player",
            "summary": "I remember entering the archive beside Ada's lamp.",
            "retained_goals": ["Stay close to Ada while exploring the archive."],
            "forgotten_noise": ["The exact pattern of dust near the threshold."],
            "source": "self",
            "visibility": "actor",
        }

    if agent_id.startswith("character:"):
        character_name = agent_id.split(":", 1)[1]
        return {
            "agent_id": agent_id,
            "character_name": character_name,
            "summary": "I remember guiding the player into the archive with my lamp raised.",
            "retained_goals": ["Keep the player close enough to protect."],
            "forgotten_noise": [],
            "source": "self",
            "visibility": "actor",
        }

    return {
        "agent_id": agent_id,
        "summary": f"{agent_id} completed the smoke memory summary.",
        "retained_goals": [],
        "forgotten_noise": [],
        "source": "self",
        "visibility": "actor",
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


def run_smoke(repo: Path) -> Dict[str, Any]:
    _insert_skills_path(repo)

    import agent_interactions
    import agent_memory
    import agent_outputs
    import agent_packets

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
            "raw_text": "I enter the archive.\n\n[USER_INSTRUCTION]\nKeep the scene quiet and inspectable.",
            "display_text": "I enter the archive.",
            "role_text": "I enter the archive.",
            "user_instruction_text": "Keep the scene quiet and inspectable.",
        }
        prepared = agent_packets.prepare_agent_run(
            card_folder=card,
            user_text="fallback text should not be routed",
            chat_log=[{"index": 4, "summary": "The player reached the archive door."}],
            card_data=card_data,
            character_contexts={
                "characters": [
                    {
                        "name": "Ada",
                        "profile_summary": "Ada is cautious and carries the lamp.",
                    }
                ]
            },
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

        agent_interactions.init_trace(
            run_dir,
            participants=["gm", "player", "character:Ada"],
            chapter_target_words=600,
        )
        agent_interactions.append_event(
            run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="dialogue",
            content="Stay close to the lamp.",
            target="player",
            source_call_id="call-character-Ada-1",
        )
        agent_interactions.append_event(
            run_dir,
            actor="character:Ada",
            visibility="actor_visible",
            event_type="memory_delta",
            content="I saw the player enter the archive beside my lamp.",
            target="self",
            source_call_id="call-character-Ada-1",
        )
        agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="world_visible",
            event_type="action",
            content="I step into the archive and keep close to Ada's lamp.",
            source_call_id="call-player-1",
        )
        agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="actor_visible",
            event_type="memory_delta",
            content="I entered the archive while following Ada's lamp.",
            target="self",
            source_call_id="call-player-1",
        )
        agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="private",
            event_type="thought",
            content="I am not sure what waits between the shelves.",
        )
        agent_interactions.mark_decision_point(
            run_dir,
            reason="The player must choose whether to inspect the shelves.",
            options=["inspect shelves", "wait at threshold"],
        )

        _write_agent_outputs(run_dir)
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
            "delivery": {
                "ok": bool(delivery.get("ok")),
                "mode": delivery.get("mode"),
            },
            "manifest_stage": manifest.get("stage") or delivered.get("stage"),
            "trace": trace,
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
