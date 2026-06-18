import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class AgentOutputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.styles_dir = Path(self.tmp.name) / "root" / "skills" / "styles"
        self.run_dir.mkdir(parents=True)
        self.styles_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        self.agent_outputs = _load_module("agent_outputs")
        self.agent_interactions = _load_module("agent_interactions")
        self._write_base_round()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_base_round(self):
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "prompts_ready",
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "actors": "actor.outputs.json",
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I open the archive door.",
                "routed_input": {
                    "role_channel": "I open the archive door.",
                    "user_instruction_channel": "",
                },
            },
        )
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive answers with stale air."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [{"scope": "room", "fact": "the door is open"}],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [
                            {"type": "action", "target": "", "content": "I step through the door."},
                            {"type": "memory_delta", "target": "self", "content": "I opened the archive door."},
                            {"type": "goal_update", "target": "self", "content": "Keep Ada close while exploring."},
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
                            {"type": "dialogue", "target": "player", "content": "Stay close."},
                            {
                                "type": "memory_delta",
                                "target": "self",
                                "content": "I saw the player enter the archive.",
                            },
                        ],
                        "stop_reason": "continue",
                    }
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:Ada"],
            chapter_target_words=1200,
        )

    def _write_story_and_critic(self, decision="pass", system_iteration_suggestion=""):
        _write_json(
            self.run_dir / "story.output.json",
            {
                "content": "<content>Ada lifted the lamp. \"Stay close,\" she said.</content>",
                "character_dialogues": [
                    {"character": "Ada", "text": "Stay close.", "source_agent": "character:ada"}
                ],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_json(
            self.run_dir / "critic.report.json",
            {
                "decision": decision,
                "hard_failures": ["logic gap"] if decision == "block" else [],
                "soft_issues": ["needs sharper sensory detail"] if decision == "revise" else [],
                "repair_instruction": "Revise sensory continuity." if decision == "revise" else "",
                "system_iteration_suggestion": system_iteration_suggestion,
            },
        )

    def test_build_story_input_assembles_loop_outputs_and_memory_deltas(self):
        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["round_id"], "round-000001")
        self.assertEqual(story_input["player_inputs"]["raw_text"], "I open the archive door.")
        self.assertEqual(story_input["loop_outputs"]["gm"]["outputs"][0]["world_state_delta"][0]["fact"], "the door is open")
        self.assertEqual(
            story_input["loop_outputs"]["actors"]["player"][0]["events"][0]["content"],
            "I step through the door.",
        )
        self.assertEqual(
            story_input["loop_outputs"]["actors"]["character:Ada"][0]["events"][0]["content"],
            "Stay close.",
        )
        self.assertEqual(
            story_input["memory_deltas"]["actors"]["character:Ada"][0]["content"],
            "I saw the player enter the archive.",
        )
        self.assertEqual(
            [item["type"] for item in story_input["memory_deltas"]["actors"]["player"]],
            ["memory_delta", "goal_update"],
        )
        self.assertEqual(
            story_input["memory_deltas"]["world"],
            [{"scope": "room", "fact": "the door is open"}],
        )
        self.assertTrue((self.run_dir / "story.input.json").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "story_ready")
        self.assertIn("story_ready", [item["stage"] for item in manifest["status"]])

    def test_build_story_input_uses_loop_outputs_and_trace_v2(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The classroom goes quiet."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [
                            {"type": "dialogue", "target": "character:SuLi", "content": "Do you know this?"}
                        ],
                        "stop_reason": "continue",
                    }
                ],
                "character:SuLi": [
                    {
                        "agent": "character",
                        "agent_id": "character:SuLi",
                        "character_name": "SuLi",
                        "events": [
                            {"type": "dialogue", "target": "player", "content": "Where did you get that?"}
                        ],
                        "stop_reason": "continue",
                    }
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:SuLi"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="player",
            visibility="world_visible",
            event_type="dialogue",
            content="Do you know this?",
            target="character:SuLi",
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertIn("loop_outputs", story_input)
        self.assertEqual(story_input["interaction_trace"]["schema_version"], 2)
        self.assertEqual(
            story_input["loop_outputs"]["actors"]["character:SuLi"][0]["events"][0]["content"],
            "Where did you get that?",
        )

    def test_build_story_input_preserves_input_analysis(self):
        input_payload = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload["input_analysis"] = {
            "schema_version": 1,
            "analysis_mode": "fixture",
        }
        _write_json(self.run_dir / "input.json", input_payload)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            story_input["player_inputs"]["input_analysis"]["analysis_mode"],
            "fixture",
        )

    def test_build_story_input_includes_interaction_trace_summary(self):
        trace = {
            "schema_version": 2,
            "round_id": "round-000001",
            "status": "decision_point",
            "participants": ["gm", "player", "character:Ada"],
            "chapter_target_words": 1200,
            "events": [
                {"actor": "character:Ada", "visibility": "world_visible", "type": "dialogue", "content": "Stay close."},
                {"actor": "character:Ada", "visibility": "private", "type": "thought", "content": "I fear this."},
            ],
            "decision_point": {"reason": "player must choose", "options": ["enter"]},
            "stop_reason": "player must choose",
        }
        _write_json(self.run_dir / "interaction.trace.json", trace)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["interaction_trace"]["status"], "decision_point")
        self.assertEqual(story_input["interaction_trace"]["visible_events"][0]["content"], "Stay close.")
        self.assertEqual(story_input["interaction_trace"]["private_event_count"], 1)

    def test_build_story_input_rejects_missing_interaction_trace_without_writing_story_input(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "story.input.json", sentinel)
        (self.run_dir / "interaction.trace.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_malformed_interaction_trace(self):
        if (self.run_dir / "story.input.json").exists():
            (self.run_dir / "story.input.json").unlink()
        (self.run_dir / "interaction.trace.json").write_text("{bad json", encoding="utf-8")

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_rejects_legacy_interaction_trace_schema(self):
        if (self.run_dir / "story.input.json").exists():
            (self.run_dir / "story.input.json").unlink()
        _write_json(
            self.run_dir / "interaction.trace.json",
            {
                "schema_version": 1,
                "round_id": "round-000001",
                "status": "interacting",
                "participants": ["gm", "player"],
                "events": [],
                "parallel_groups": [],
                "decision_point": None,
                "stop_reason": "",
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_blocks_missing_required_actor_outputs(self):
        (self.run_dir / "actor.outputs.json").unlink()
        _write_json(
            self.run_dir / "player.output.json",
            {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "legacy fallback should not be used"}],
                "stop_reason": "continue",
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "actor.outputs.json"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_legacy_gm_output(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm",
                "scene_beats": [{"content": "legacy direct gm output"}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "gm.output.json.*gm_loop"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_empty_gm_loop_outputs(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"gm\.output\.json\.outputs.*empty"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_actor_outputs_value_that_is_not_a_list(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "action", "target": "", "content": "I step forward."}],
                    "stop_reason": "continue",
                }
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"actor\.outputs\.json\.player.*list"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_actor_map_key_agent_id_mismatch(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "character:Ada": [
                    {
                        "agent": "character",
                        "agent_id": "character:Eve",
                        "character_name": "Eve",
                        "events": [
                            {"type": "dialogue", "target": "player", "content": "This came from Eve."}
                        ],
                        "stop_reason": "continue",
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "agent_id mismatch"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_invalid_empty_actor_map_key(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "gm_only": [],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "gm_only"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_rejects_hidden_marker_actor_map_keys_even_when_empty(self):
        for actor_key, expected in (
            ("character:", "character:"),
            ("character:gmOnly", "gm_only"),
            ("gm_only", "gm_only"),
        ):
            with self.subTest(actor_key=actor_key):
                if (self.run_dir / "story.input.json").exists():
                    (self.run_dir / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "actor.outputs.json",
                    {
                        actor_key: [],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, expected):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_without_actor_output(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is close enough to respond."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-ada",
                                "actor_id": "character:Ada",
                                "prompt": "React to the player opening the archive door.",
                                "reason": "Ada is present in the scene.",
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [
                            {"type": "action", "target": "", "content": "I step through the door."}
                        ],
                        "stop_reason": "continue",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "character:Ada"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_requires_actor_output_count_for_each_gm_call(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada answers once, then is asked again."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "React to the door opening.",
                                "reason": "Ada is present.",
                            },
                            {
                                "call_id": "call-ada-2",
                                "actor_id": "character:Ada",
                                "prompt": "React to the second question.",
                                "reason": "The player keeps speaking to Ada.",
                            },
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "character:Ada"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_allows_one_actor_output_for_one_gm_call(self):
        _write_json(
            self.run_dir / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is close enough to respond."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-ada",
                                "actor_id": "character:Ada",
                                "prompt": "React to the player opening the archive door.",
                                "reason": "Ada is present in the scene.",
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(len(story_input["loop_outputs"]["actors"]["character:Ada"]), 1)

    def test_build_story_input_rejects_memory_delta_event_source_field(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [
                            {
                                "type": "memory_delta",
                                "target": "self",
                                "content": "I remember the archive door.",
                                "source": "gm_only",
                            }
                        ],
                        "stop_reason": "continue",
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "source"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_actor_event_metadata_hidden_marker(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [
                            {
                                "type": "memory_delta",
                                "target": "self",
                                "content": "I remember the archive door.",
                                "metadata": {"source": "gm_only"},
                            }
                        ],
                        "stop_reason": "continue",
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "gm_only"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_build_story_input_rejects_legacy_actor_output_item(self):
        _write_json(
            self.run_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "dialogue": [{"target": "character:Ada", "text": "Stay close."}],
                        "stop_reason": "continue",
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"actor\.outputs\.json\.player\[0\].*legacy"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_prepare_delivery_blocks_critic_block_decision(self):
        self._write_story_and_critic(decision="block")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "critic_block")
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_blocks_missing_story_output(self):
        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "agent_outputs")
        self.assertIn("story.output.json", result["detail"])
        self.assertFalse((self.styles_dir / "response.txt").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "blocked")
        self.assertIn("blocked", [item["stage"] for item in manifest["status"]])

    def test_artifact_retries_do_not_consume_critic_retry_budget(self):
        first_missing = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        second_missing = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        self._write_story_and_critic(decision="revise")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(first_missing["ok"])
        self.assertFalse(second_missing["ok"])
        self.assertEqual(first_missing["reason"], "agent_outputs")
        self.assertEqual(second_missing["reason"], "agent_outputs")
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "critic_revise")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["retry_count"], 2)
        self.assertEqual(manifest["critic_retry_count"], 1)
        self.assertEqual(manifest["stage"], "blocked")

    def test_prepare_delivery_revise_increments_critic_retry_without_rewriting_player_input(self):
        self._write_story_and_critic(decision="revise")
        before = (self.run_dir / "input.json").read_text(encoding="utf-8")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "critic_revise")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["critic_retry_count"], 1)
        self.assertEqual((self.run_dir / "input.json").read_text(encoding="utf-8"), before)
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_records_critic_repair_history(self):
        self._write_story_and_critic(decision="revise")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        history = _read_jsonl(self.run_dir / "repair_history.jsonl")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["round_id"], "round-000001")
        self.assertEqual(history[0]["attempt"], 1)
        self.assertEqual(history[0]["decision"], "revise")
        self.assertEqual(history[0]["soft_issues"], ["needs sharper sensory detail"])
        self.assertEqual(history[0]["repair_instruction"], "Revise sensory continuity.")
        self.assertEqual(history[0]["source"], "critic.report.json")

    def test_prepare_delivery_appends_system_iteration_suggestion_to_improvement_queue(self):
        self._write_story_and_critic(
            decision="block",
            system_iteration_suggestion="Add a context-isolation regression test.",
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        queue = _read_jsonl(self.card / ".agent_runs" / "improvement_queue.jsonl")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["round_id"], "round-000001")
        self.assertEqual(queue[0]["decision"], "block")
        self.assertEqual(queue[0]["suggestion"], "Add a context-isolation regression test.")
        self.assertEqual(queue[0]["source"], str((self.run_dir / "critic.report.json").resolve()))

    def test_prepare_delivery_returns_terminal_block_after_retry_limit(self):
        self._write_story_and_critic(
            decision="block",
            system_iteration_suggestion="Tighten prompt isolation checks.",
        )
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["critic_retry_count"] = 2
        _write_json(self.run_dir / "manifest.json", manifest)

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "critic_retry_limit")
        self.assertFalse((self.styles_dir / "response.txt").exists())
        final_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(final_manifest["stage"], "blocked")
        self.assertEqual(final_manifest["critic_retry_count"], 2)

    def test_prepare_delivery_repair_attempt_uses_repair_history_count(self):
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["retry_count"] = 1
        _write_json(self.run_dir / "manifest.json", manifest)
        self._write_story_and_critic(decision="revise")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        history = _read_jsonl(self.run_dir / "repair_history.jsonl")
        self.assertEqual(history[0]["attempt"], 1)

    def test_prepare_delivery_does_not_duplicate_unchanged_critic_repair(self):
        self._write_story_and_critic(
            decision="block",
            system_iteration_suggestion="Add a context-isolation regression test.",
        )

        first = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        second = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        third = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertFalse(third["ok"])
        self.assertEqual(first["action"], "retry")
        self.assertEqual(second["action"], "retry")
        self.assertEqual(third["action"], "blocked")
        self.assertEqual(third["reason"], "critic_retry_limit")
        history = _read_jsonl(self.run_dir / "repair_history.jsonl")
        queue = _read_jsonl(self.card / ".agent_runs" / "improvement_queue.jsonl")
        self.assertEqual(len(history), 1)
        self.assertEqual(len(queue), 1)
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["critic_retry_count"], 2)
        self.assertEqual(manifest["stage"], "blocked")

    def test_prepare_delivery_pass_writes_story_content_to_response(self):
        self._write_story_and_critic(decision="pass")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["story_output"]["content"], (self.styles_dir / "response.txt").read_text(encoding="utf-8"))
        self.assertFalse((self.run_dir / "repair_history.jsonl").exists())
        self.assertFalse((self.card / ".agent_runs" / "improvement_queue.jsonl").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "critic_passed")
        self.assertIn("critic_passed", [item["stage"] for item in manifest["status"]])

    def test_mark_delivered_updates_manifest_stage(self):
        self._write_story_and_critic(decision="pass")
        self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        result = self.agent_outputs.mark_delivered(self.card)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(manifest["stage"], "delivered")
        self.assertIn("delivered", [item["stage"] for item in manifest["status"]])


if __name__ == "__main__":
    unittest.main()
