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


def _actor_reply(agent_id, content, *, character_name=""):
    agent = "player" if agent_id == "player" else "character"
    payload = {
        "agent": agent,
        "agent_id": agent_id,
        "natural_reply": content,
        "events": [{
            "type": "reply",
            "target": "gm",
            "content": content,
            "metadata": {},
        }],
    }
    if agent == "character":
        payload["character_name"] = character_name or agent_id.split(":", 1)[1]
    return payload


def _post_round_memory_update_payload(agent_id, *, character_name="", key_memory="", active_goal=""):
    if agent_id == "player":
        long_term = "I remember opening the archive door and hearing machinery."
        key_memory = key_memory or "I opened the archive door and heard machinery from inside."
        tag = "archive door"
    else:
        long_term = f"I remember seeing the player hesitate at the archive door as {character_name}."
        key_memory = key_memory or f"I watched the player hesitate near the archive threshold as {character_name}."
        tag = "archive watch"
    if active_goal:
        long_term += f" I want to {active_goal[0].lower() + active_goal[1:]}"

    payload = {
        "agent_id": agent_id,
        "long_term_memories": long_term,
        "key_memories": [
            {
                "tag": tag,
                "summary": key_memory,
                "detail": "The air was cold, and machinery could be heard beyond the door.",
            }
        ],
    }
    if character_name:
        payload["character_name"] = character_name
    return payload


def _write_valid_postprocess_artifact(run_dir, *, round_id, include_player_decision_option=False):
    options = [
        {
            "label": "Pause at the archive threshold.",
            "source": "postprocess",
            "requires_confirmation": False,
        }
    ]
    if include_player_decision_option:
        options.append(
            {
                "label": "Confirm action: whether to enter the airlock",
                "source": "player_agent_critical_action",
                "requires_confirmation": True,
            }
        )
    _write_json(
        run_dir / "artifacts" / "postprocess.output.json",
        {
            "schema_version": 1,
            "core": {
                "summary": f"Postprocess summary for {round_id}.",
                "options": options,
                "current_goal": "Decide whether to enter the archive.",
                "state_patch": {
                    "quest": "Explore the archive threshold",
                    "location": "archive threshold",
                    "actions": ["Pause and decide the next step"],
                },
            },
            "ui_extensions": {
                "status_panels": {},
                "custom_cards": {},
                "asset_bindings": {},
            },
            "ui_extension_status": {"status": "ok", "issues": []},
            "repair_requests": [],
        },
    )


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
                _actor_reply("player", "I keep one hand on the doorframe and look inside.")
            ],
        }
        for name in scenario["characters"]:
            actor_id = f"character:{name}"
            actor_outputs[actor_id] = [
                _actor_reply(actor_id, scenario["dialogue"][name], character_name=name)
            ]

        for name in scenario["characters"]:
            actor_id = f"character:{name}"
            call_id = _actor_call_id(actor_id)
            self.agent_interactions.append_event(
                run_dir,
                actor=actor_id,
                visibility="world_visible",
                event_type="reply",
                content=scenario["dialogue"][name],
                target="gm",
                source_call_id=call_id,
            )
        self.agent_interactions.append_event(
            run_dir,
            actor="player",
            visibility="world_visible",
            event_type="reply",
            content="I keep one hand on the doorframe and look inside.",
            target="gm",
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

        _write_json(run_dir / "artifacts" / "gm.output.json", {"agent": "gm_loop", "outputs": [gm_output]})
        _write_json(run_dir / "artifacts" / "actor.outputs.json", actor_outputs)

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
            input_payload={
                "input_schema": "dual_channel_v1",
                "raw_text": scenario["user_text"],
                "role_text": "I open the archive door.",
                "user_instruction_text": (
                    "the archive is secretly a moon base, but characters can only "
                    "perceive machinery and cold air for now."
                ),
            },
        )
        run_dir = Path(result["run_dir"])

        self._write_loop_artifacts(run_dir, scenario)
        _write_json(
            run_dir / "artifacts" / "story.output.json",
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
            run_dir / "artifacts" / "critic.report.json",
            {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": ""},
        )
        _write_valid_postprocess_artifact(run_dir, round_id="round-000001")

        delivery = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        memory = self.agent_memory.ingest_memory_deltas(self.card, run_dir, date_str="2026-06-16 12:00")
        delivered = self.agent_outputs.mark_delivered(self.card)
        story_input = json.loads((run_dir / "story.input.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(delivery["ok"])
        self.assertTrue(memory["ok"])
        self.assertTrue(delivered["ok"])
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), scenario["story_content"])
        self.assertEqual(story_input["player_inputs"]["raw_text"], "I open the archive door.")
        self.assertNotIn("user_instruction_channel", story_input["player_inputs"]["routed_input"])
        self.assertNotIn("moon base", json.dumps(story_input["player_inputs"], ensure_ascii=False))
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["player"], ensure_ascii=False))
        self.assertEqual(story_input["memory_deltas"]["actors"]["player"], [])
        self.assertEqual(len(delivery["story_output"]["character_dialogues"]), 2)
        self.assertIn("the archive is a disguised moon base", (self.card / "memory" / "world_delta.md").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "delivered")

    def test_post_round_memory_failure_does_not_delete_delivered_response(self):
        delivered_response = self.card / "skills" / "styles" / "response.txt"
        delivered_response.parent.mkdir(parents=True)
        delivered_response.write_text("Delivered prose remains visible.", encoding="utf-8")
        run_dir = self.card / ".agent_runs" / "round-000001"
        _write_json(
            run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "delivered",
                "post_round_memory_jobs": {
                    "status": "pending",
                    "scheduled": {
                        "character:Ada": {
                            "output": "post_round_memory_jobs/character_Ada.summary.json"
                        }
                    },
                    "failed": {},
                },
            },
        )
        update = _post_round_memory_update_payload(
            "character:Ada",
            character_name="Ada",
            active_goal="Watch the archive shelf.",
        )
        update["long_term_memories"] = "world_truth says I know too much."
        _write_json(run_dir / "post_round_memory_jobs" / "character_Ada.summary.json", update)

        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, run_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(delivered_response.read_text(encoding="utf-8"), "Delivered prose remains visible.")

    def test_control_plane_fixture_round_with_repair_trace_and_post_round_memory_jobs(self):
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
        self.assertNotIn("memory_summaries", json.dumps(manifest, ensure_ascii=False))
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
            run_dir / "artifacts" / "story.output.json",
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
            run_dir / "artifacts" / "critic.report.json",
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
            run_dir / "artifacts" / "critic.report.json",
            {"decision": "pass", "hard_failures": [], "soft_issues": [], "repair_instruction": "", "system_iteration_suggestion": ""},
        )
        _write_valid_postprocess_artifact(run_dir, round_id="round-000006", include_player_decision_option=True)
        delivery = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        memory_delta = self.agent_memory.ingest_memory_deltas(self.card, run_dir, date_str="2026-06-16 12:00")
        post_round_jobs = self.agent_memory.schedule_post_round_memory_jobs(self.card, run_dir)
        manifest_with_jobs = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        scheduled_jobs = manifest_with_jobs["post_round_memory_jobs"]["scheduled"]
        self.assertEqual(post_round_jobs["scheduled"], sorted(scheduled_jobs))
        for agent_id, entry in scheduled_jobs.items():
            path = entry["output"]
            if agent_id == "player":
                payload = _post_round_memory_update_payload(
                    "player",
                    active_goal="Decide whether to enter.",
                )
            else:
                character_name = agent_id.split(":", 1)[1]
                payload = _post_round_memory_update_payload(
                    agent_id,
                    character_name=character_name,
                    active_goal="Watch the threshold.",
                )
            _write_json(run_dir / path, payload)

        post_round_memory = self.agent_memory.ingest_post_round_memory_jobs(self.card, run_dir)
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
        self.assertTrue(post_round_memory["ok"])
        self.assertTrue(delivered["ok"])
        self.assertEqual(story_input["player_inputs"]["raw_text"], role_text)
        self.assertEqual(story_input["player_inputs"]["routed_input"]["input_schema"], "dual_channel_v1")
        self.assertNotIn("user_instruction_channel", story_input["player_inputs"]["routed_input"])
        self.assertNotIn("moon base", json.dumps(story_input["player_inputs"], ensure_ascii=False))
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["player"], ensure_ascii=False))
        self.assertNotIn("moon base", json.dumps(story_input["loop_outputs"]["actors"]["character:Ada"], ensure_ascii=False))
        self.assertEqual(story_input["memory_deltas"]["actors"]["character:Ada"], [])
        self.assertEqual(story_input["interaction_trace"]["visible_events"][0]["content"], scenario["dialogue"]["Ada"])
        self.assertEqual(story_input["interaction_trace"]["private_event_count"], 1)
        self.assertEqual(story_input["interaction_trace"]["decision_point"]["options"], ["enter", "wait"])
        self.assertEqual(repair_history[0]["attempt"], 1)
        self.assertEqual(repair_history[0]["decision"], "revise")
        self.assertEqual(improvement_queue[0]["suggestion"], "Add a fixture for critic repair history.")
        player_memory_dir = self.card / "characters" / "_self"
        ada_memory_dir = self.card / "characters" / "Ada"
        player_long_term = (player_memory_dir / "long_term_memories.md").read_text(encoding="utf-8")
        player_key_memories = json.loads((player_memory_dir / "key_memories.json").read_text(encoding="utf-8"))
        player_short_term = (player_memory_dir / "short_term_memories.md").read_text(encoding="utf-8")
        ada_long_term = (ada_memory_dir / "long_term_memories.md").read_text(encoding="utf-8")
        ada_key_memories = json.loads((ada_memory_dir / "key_memories.json").read_text(encoding="utf-8"))
        ada_short_term = (ada_memory_dir / "short_term_memories.md").read_text(encoding="utf-8")
        self.assertIn("I remember opening the archive door", player_long_term)
        self.assertIn("I opened the archive door", json.dumps(player_key_memories, ensure_ascii=False))
        self.assertEqual(player_short_term, "")
        self.assertIn("I remember seeing the player hesitate", ada_long_term)
        self.assertIn("I watched the player hesitate", json.dumps(ada_key_memories, ensure_ascii=False))
        self.assertEqual(ada_short_term, "")
        self.assertFalse((player_memory_dir / "summary.md").exists())
        self.assertFalse((ada_memory_dir / "summary.md").exists())
        self.assertEqual(final_manifest["stage"], "delivered")
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), scenario["story_content"])


if __name__ == "__main__":
    unittest.main()
