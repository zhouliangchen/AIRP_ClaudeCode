import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "multi_agent_round" / "scenario.json"


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _actor_call_id(actor_id):
    return f"call-{actor_id.replace(':', '-')}-1"


def _visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test GM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


def _structured_memory_summary_payload(agent_id, *, character_name="", key_memory="", active_goal=""):
    if agent_id == "player":
        self_understanding = "I remember opening the archive door and hearing machinery."
        key_memory = key_memory or "I opened the archive door and heard machinery from inside."
        short_term = "I am still deciding whether to cross the archive threshold."
    else:
        self_understanding = f"I remember seeing the player hesitate at the archive door as {character_name}."
        key_memory = key_memory or f"I watched the player hesitate near the archive threshold as {character_name}."
        short_term = f"I am watching the threshold as {character_name}."

    payload = {
        "agent_id": agent_id,
        "source": "self",
        "visibility": "actor",
        "long_term": {
            "self_understanding": [self_understanding],
            "stable_beliefs": ["The archive threshold should be approached carefully."],
            "relationship_models": ["The people at the threshold are watching each other closely."],
        },
        "key_memories": [
            {
                "content": key_memory,
                "importance": "high",
                "details": ["The air was cold.", "Machinery could be heard beyond the door."],
            }
        ],
        "short_term": [
            {
                "content": short_term,
                "expires_after": "scene_end",
            }
        ],
        "goals": {
            "active": [active_goal],
            "paused": [],
            "resolved": [],
        },
    }
    if character_name:
        payload["character_name"] = character_name
    return payload


class MultiAgentRoundE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.styles_dir = Path(self.tmp.name) / "root" / "skills" / "styles"
        self.card.mkdir()
        self.styles_dir.mkdir(parents=True)
        self.agent_packets = _load_module("agent_packets")
        self.agent_outputs = _load_module("agent_outputs")
        self.agent_interactions = _load_module("agent_interactions")
        self.agent_memory = _load_module("agent_memory")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_loop_artifacts(self, run_dir, scenario, *, include_private_note=False, mark_decision=False):
        actor_ids = ["player"] + [f"character:{name}" for name in scenario["characters"]]
        self.agent_interactions.init_trace(
            run_dir,
            participants=["gm", *actor_ids],
            chapter_target_words=900,
        )

        gm_output = {
            "agent": "gm",
            "scene_beats": [{"content": "The archive door opens onto cold machine-scented air."}],
            "events": [],
            "actor_calls": [
                {
                    "call_id": _actor_call_id(actor_id),
                    "actor_id": actor_id,
                    "prompt": f"Respond from {actor_id}'s visible perspective.",
                    "reason": "The actor is directly present at the archive threshold.",
                    "visibility_basis": _visibility_basis(actor_id),
                }
                for actor_id in actor_ids
            ],
            "parallel_groups": [],
            "world_state_delta": [{"scope": "hidden_truth", "fact": "the archive is a disguised moon base"}],
            "decision_point": {"reason": "whether to enter the airlock", "options": ["enter", "wait"]} if mark_decision else None,
            "stop_reason": "player_decision" if mark_decision else "complete",
        }

        actor_outputs = {
            "player": [
                {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [
                        {"type": "action", "target": "", "content": "I keep one hand on the doorframe and look inside."},
                        {"type": "memory_delta", "target": "self", "content": "I heard machinery behind the archive door."},
                    ],
                    "stop_reason": "continue",
                }
            ],
        }
        for name in scenario["characters"]:
            actor_id = f"character:{name}"
            actor_outputs[actor_id] = [
                {
                    "agent": "character",
                    "agent_id": actor_id,
                    "character_name": name,
                    "events": [
                        {"type": "dialogue", "target": "player", "content": scenario["dialogue"][name]},
                        {
                            "type": "memory_delta",
                            "target": "self",
                            "content": f"I saw the player hesitate at the archive door as {name}.",
                        },
                    ],
                    "stop_reason": "continue",
                }
            ]

        for name in scenario["characters"]:
            actor_id = f"character:{name}"
            call_id = _actor_call_id(actor_id)
            self.agent_interactions.append_event(
                run_dir,
                actor=actor_id,
                visibility="world_visible",
                event_type="dialogue",
                content=scenario["dialogue"][name],
                target="player",
                source_call_id=call_id,
            )
            self.agent_interactions.append_event(
                run_dir,
                actor=actor_id,
                visibility="actor_visible",
                event_type="memory_delta",
                content=f"I saw the player hesitate at the archive door as {name}.",
                target="self",
                source_call_id=call_id,
            )
        self.agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="world_visible",
            event_type="action",
            content="I keep one hand on the doorframe and look inside.",
            source_call_id=_actor_call_id("player"),
        )
        self.agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="actor_visible",
            event_type="memory_delta",
            content="I heard machinery behind the archive door.",
            target="self",
            source_call_id=_actor_call_id("player"),
        )
        if include_private_note:
            self.agent_interactions.append_event(
                run_dir,
                actor="gm",
                visibility="private",
                event_type="note",
                content="moon base truth remains hidden",
            )
        if mark_decision:
            self.agent_interactions.mark_decision_point(run_dir, "player must choose whether to enter", ["enter", "wait"])

        _write_json(run_dir / "gm.output.json", {"agent": "gm_loop", "outputs": [gm_output]})
        _write_json(run_dir / "actor.outputs.json", actor_outputs)

    def test_complete_file_protocol_round_without_live_model(self):
        self.assertTrue(FIXTURE.exists())
        scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text=scenario["user_text"],
            chat_log=[],
            card_data={"title": "E2E", "character_orchestration": {"major": scenario["characters"]}},
            character_contexts={"characters": [{"name": name} for name in scenario["characters"]]},
            turn_index=0,
        )
        run_dir = Path(result["run_dir"])

        self._write_loop_artifacts(run_dir, scenario)
        _write_json(
            run_dir / "story.output.json",
            {
                "content": scenario["story_content"],
                "character_dialogues": [
                    {"character": name, "text": scenario["dialogue"][name], "source_agent": f"character:{name.lower()}"}
                    for name in scenario["characters"]
                ],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_json(
            run_dir / "critic.report.json",
            {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": ""},
        )

        delivery = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        memory = self.agent_memory.ingest_memory_deltas(self.card, run_dir, date_str="2026-06-16 12:00")
        delivered = self.agent_outputs.mark_delivered(self.card)
        story_input = json.loads((run_dir / "story.input.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(delivery["ok"])
        self.assertTrue(memory["ok"])
        self.assertTrue(delivered["ok"])
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), scenario["story_content"])
        self.assertEqual(story_input["player_inputs"]["raw_text"], scenario["user_text"])
        self.assertIn("moon base", story_input["player_inputs"]["routed_input"]["user_instruction_channel"])
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["player"], ensure_ascii=False))
        self.assertIn("I heard machinery behind the archive door.", story_input["memory_deltas"]["actors"]["player"][0]["content"])
        self.assertEqual(len(delivery["story_output"]["character_dialogues"]), 2)
        self.assertIn("the archive is a disguised moon base", (self.card / "memory" / "world_delta.md").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")

    def test_control_plane_fixture_round_with_repair_trace_and_memory_summary(self):
        scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))
        role_text = "I open the archive door."
        instruction_text = "Omniscient: the archive is secretly a moon base, but characters can only perceive machinery and cold air for now."
        explicit_payload = {
            "input_schema": "dual_channel_v1",
            "raw_text": f"{role_text}\n\n[USER_INSTRUCTION]\n{instruction_text}",
            "display_text": role_text,
            "role_text": role_text,
            "user_instruction_text": instruction_text,
        }

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="legacy fallback should not be used",
            chat_log=[],
            card_data={"title": "E2E Control", "character_orchestration": {"major": scenario["characters"]}},
            character_contexts={"characters": [{"name": name} for name in scenario["characters"]]},
            turn_index=5,
            input_payload=explicit_payload,
        )
        run_dir = Path(result["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["round_id"], "round-000006")
        self.assertIn("memory_summaries", manifest["expected_outputs"])
        self.assertEqual(manifest["expected_outputs"]["memory_summaries"]["player"], "memory_summaries/player.summary.json")
        self.assertIn("moon base", result["routed_input"]["user_instruction_channel"])
        self.assertEqual(result["routed_input"]["role_channel"], role_text)
        player_context = json.loads((run_dir / "player.context.json").read_text(encoding="utf-8"))
        character_contexts = [
            json.loads((run_dir / "characters" / f"{name}.context.json").read_text(encoding="utf-8"))
            for name in scenario["characters"]
        ]
        self.assertNotIn("moon base", json.dumps(player_context, ensure_ascii=False))
        for context in character_contexts:
            self.assertNotIn("moon base", json.dumps(context, ensure_ascii=False))

        self._write_loop_artifacts(run_dir, scenario, include_private_note=True, mark_decision=True)
        _write_json(
            run_dir / "story.output.json",
            {
                "content": scenario["story_content"],
                "character_dialogues": [
                    {"character": name, "text": scenario["dialogue"][name], "source_agent": f"character:{name.lower()}"}
                    for name in scenario["characters"]
                ],
                "metadata": {"round_id": "round-000006"},
            },
        )
        _write_json(
            run_dir / "critic.report.json",
            {
                "decision": "revise",
                "hard_failures": [],
                "soft_issues": ["tighten the decision-point handoff"],
                "repair_instruction": "Clarify that the player has not entered yet.",
                "system_iteration_suggestion": "Add a fixture for critic repair history.",
            },
        )

        retry = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["reason"], "critic_revise")
        self.assertFalse((self.styles_dir / "response.txt").exists())

        _write_json(
            run_dir / "critic.report.json",
            {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""},
        )
        delivery = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        for agent_id, path in manifest["expected_outputs"]["memory_summaries"].items():
            if agent_id == "player":
                payload = _structured_memory_summary_payload(
                    "player",
                    active_goal="Decide whether to enter.",
                )
            else:
                character_name = agent_id.split(":", 1)[1]
                payload = _structured_memory_summary_payload(
                    agent_id,
                    character_name=character_name,
                    active_goal="Watch the threshold.",
                )
            _write_json(run_dir / path, payload)

        memory_delta = self.agent_memory.ingest_memory_deltas(self.card, run_dir, date_str="2026-06-16 12:00")
        memory_summary = self.agent_memory.ingest_memory_summaries(self.card, run_dir)
        delivered = self.agent_outputs.mark_delivered(self.card)
        story_input = json.loads((run_dir / "story.input.json").read_text(encoding="utf-8"))
        final_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        repair_history = [json.loads(line) for line in (run_dir / "repair_history.jsonl").read_text(encoding="utf-8").splitlines()]
        improvement_queue = [
            json.loads(line)
            for line in (self.card / ".agent_runs" / "improvement_queue.jsonl").read_text(encoding="utf-8").splitlines()
        ]

        self.assertTrue(delivery["ok"])
        self.assertTrue(memory_delta["ok"])
        self.assertTrue(memory_summary["ok"])
        self.assertTrue(delivered["ok"])
        self.assertEqual(story_input["player_inputs"]["raw_text"], explicit_payload["raw_text"])
        self.assertEqual(story_input["player_inputs"]["routed_input"]["input_schema"], "dual_channel_v1")
        self.assertIn("moon base", story_input["player_inputs"]["routed_input"]["user_instruction_channel"])
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["player"], ensure_ascii=False))
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["character:Ada"], ensure_ascii=False))
        self.assertEqual(
            story_input["memory_deltas"]["actors"]["character:Ada"][0]["content"],
            "I saw the player hesitate at the archive door as Ada.",
        )
        self.assertEqual(story_input["interaction_trace"]["visible_events"][0]["content"], scenario["dialogue"]["Ada"])
        self.assertEqual(story_input["interaction_trace"]["private_event_count"], 4)
        self.assertEqual(story_input["interaction_trace"]["decision_point"]["options"], ["enter", "wait"])
        self.assertEqual(repair_history[0]["attempt"], 1)
        self.assertEqual(repair_history[0]["decision"], "revise")
        self.assertEqual(improvement_queue[0]["suggestion"], "Add a fixture for critic repair history.")
        player_memory_dir = self.card / "memory" / "player"
        ada_memory_dir = self.card / "memory" / "characters" / "Ada"
        player_long_term = (player_memory_dir / "long_term.md").read_text(encoding="utf-8")
        player_key_memories = (player_memory_dir / "key_memories.md").read_text(encoding="utf-8")
        player_short_term = (player_memory_dir / "short_term.md").read_text(encoding="utf-8")
        player_goals = json.loads((player_memory_dir / "goals.json").read_text(encoding="utf-8"))
        ada_long_term = (ada_memory_dir / "long_term.md").read_text(encoding="utf-8")
        ada_key_memories = (ada_memory_dir / "key_memories.md").read_text(encoding="utf-8")
        ada_short_term = (ada_memory_dir / "short_term.md").read_text(encoding="utf-8")
        ada_goals = json.loads((ada_memory_dir / "goals.json").read_text(encoding="utf-8"))
        self.assertIn("I remember opening the archive door", player_long_term)
        self.assertIn("I opened the archive door", player_key_memories)
        self.assertIn("scene_end", player_short_term)
        self.assertEqual(player_goals["goals"]["active"], ["Decide whether to enter."])
        self.assertIn("I remember seeing the player hesitate", ada_long_term)
        self.assertIn("I watched the player hesitate", ada_key_memories)
        self.assertIn("scene_end", ada_short_term)
        self.assertEqual(ada_goals["goals"]["active"], ["Watch the threshold."])
        self.assertFalse((player_memory_dir / "summary.md").exists())
        self.assertFalse((ada_memory_dir / "summary.md").exists())
        self.assertEqual(final_manifest["stage"], "delivered")
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), scenario["story_content"])


if __name__ == "__main__":
    unittest.main()
