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


def _write_agent_outputs(run_dir: Path) -> None:
    _write_json(
        run_dir / "gm.output.json",
        {
            "agent": "gm",
            "narration": "The archive door opens onto a quiet lamplit room.",
            "npc_events": [{"npc": "archive", "event": "dust stirs in the entryway"}],
            "world_state_delta": [{"scope": "archive", "fact": "the entry door is open"}],
            "handoff": {"focus": "player decision at the archive threshold"},
        },
    )
    _write_json(
        run_dir / "player.output.json",
        {
            "agent": "player",
            "agent_id": "player",
            "action": "I step into the archive and keep close to Ada's lamp.",
            "dialogue": [{"target": "Ada", "text": "I'll stay where I can see the light."}],
            "perception": ["I smell paper dust and warm oil from the lamp."],
            "memory_delta": [
                {
                    "text": "I entered the archive while following Ada's lamp.",
                    "source": "perceived",
                }
            ],
        },
    )
    _write_json(
        run_dir / "characters" / "Ada.output.json",
        {
            "agent": "character",
            "agent_id": "character:Ada",
            "character_name": "Ada",
            "action": "I raise the lamp and watch the shelves for movement.",
            "dialogue": [{"target": "player", "text": "Stay close to the lamp."}],
            "perception": ["I see the player cross the archive threshold."],
            "memory_delta": [
                {
                    "text": "I saw the player enter the archive beside my lamp.",
                    "source": "perceived",
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
            card_data={"title": "Smoke Card", "scenario": "Archive threshold"},
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

        return {
            "ok": True,
            "round_id": run_dir.name,
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
