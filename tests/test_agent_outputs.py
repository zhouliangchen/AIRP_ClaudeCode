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


ROUND_ARTIFACT_NAMES = {
    "actor.outputs.json",
    "critic.report.json",
    "gm.output.json",
    "postprocess.output.json",
    "story.input.json",
    "story.output.json",
}


def _authoritative_test_path(path):
    if (
        path.name in ROUND_ARTIFACT_NAMES
        and path.parent.name.startswith("round-")
        and path.parent.parent.name == ".agent_runs"
    ):
        return path.parent / "artifacts" / path.name
    return path


def _write_json(path, data):
    path = _authoritative_test_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_root_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _visibility_basis(actor_id="player"):
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
        payload["character_name"] = character_name or agent_id.split(":", 1)[-1]
    return payload


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
            self.run_dir / "artifacts" / "gm.output.json",
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "I step through the door.")
                ],
                "character:Ada": [
                    _actor_reply("character:Ada", "Stay close.", character_name="Ada")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:Ada"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="player",
            visibility="world_visible",
            event_type="reply",
            content="I step through the door.",
            target="gm",
            source_call_id="call-player-1",
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="Stay close.",
            target="gm",
            source_call_id="call-character-Ada-1",
        )

    def _write_story_and_critic(
        self,
        decision="pass",
        system_iteration_suggestion="",
        repair_routing=None,
        repair_instruction=None,
        include_postprocess=True,
        postprocess_payload=None,
    ):
        _write_json(
            self.run_dir / "artifacts" / "story.output.json",
            {
                "content": "<content>Ada lifted the lamp. \"Stay close,\" she said.</content>",
                "character_dialogues": [
                    {"character": "Ada", "text": "Stay close.", "source_agent": "character:ada"}
                ],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "critic.report.json",
            {
                "decision": decision,
                "hard_failures": ["logic gap"] if decision == "block" else [],
                "soft_issues": ["needs sharper sensory detail"] if decision == "revise" else [],
                "repair_instruction": (
                    repair_instruction
                    if repair_instruction is not None
                    else ("Revise sensory continuity." if decision == "revise" else "")
                ),
                "system_iteration_suggestion": system_iteration_suggestion,
                **({"repair_routing": repair_routing} if repair_routing is not None else {}),
            },
        )
        if decision == "pass" and include_postprocess:
            self._write_postprocess(postprocess_payload)

    def test_build_relaxed_story_input_does_not_require_trace_artifacts(self):
        run_dir = Path(self.tmp.name) / "relaxed-run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)
        _write_json(
            run_dir / "manifest.json",
            {
                "round_id": "round_relaxed",
                "runtime_settings": {"style": "default", "wordCount": 800, "nsfw": False},
                "style_profile": {},
            },
        )
        _write_json(
            run_dir / "input.json",
            {
                "raw_text": "我推开门。",
                "routed_input": {"role_channel": "我推开门。"},
            },
        )
        _write_json(
            artifacts / "input_analysis.output.json",
            {
                "semantic_units": [{"type": "player_action", "derived_summary": "玩家推门"}],
                "world_updates": {},
                "capability_requests": [],
            },
        )
        _write_json(
            artifacts / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "门后传来微弱灯光。", "metadata": {}}],
                        "events": [{"type": "world_event", "target": "", "content": "门被推开。", "metadata": {}}],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "character_promotions": [],
                        "subgm_commands": [],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )

        story_input = self.agent_outputs.build_relaxed_story_input(run_dir)

        self.assertEqual(story_input["round_id"], "round_relaxed")
        self.assertEqual(story_input["loop_outputs"]["gm"]["outputs"][0]["agent"], "gm")
        self.assertEqual(story_input["player_inputs"]["raw_text"], "我推开门。")
        self.assertEqual(story_input["interaction_trace"]["status"], "relaxed")
        self.assertTrue((artifacts / "story.input.json").exists())

    def _write_postprocess(self, payload=None):
        if payload is None:
            payload = {
                "schema_version": 1,
                "core": {
                    "summary": "Postprocess summary.",
                    "current_goal": "Keep moving through the archive.",
                    "options": ["Step deeper into the archive."],
                    "state_patch": {"quest": "Explore the archive"},
                },
                "ui_extensions": {
                    "status_panels": {"mood": "tense"},
                    "custom_cards": {},
                    "asset_bindings": {},
                },
                "ui_extension_status": {"status": "ok", "issues": []},
            }
        _write_json(self.run_dir / "artifacts" / "postprocess.output.json", payload)

    def _add_player_critical_action(self):
        input_payload = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload.setdefault("routed_input", {})["role_action_channel"] = "I open the archive door."
        _write_json(self.run_dir / "input.json", input_payload)
        gm_outputs = json.loads((self.run_dir / "artifacts" / "gm.output.json").read_text(encoding="utf-8"))
        gm_outputs["outputs"][0]["stop_reason"] = "player_decision"
        gm_outputs["outputs"][0]["decision_point"] = {
            "id": "gm-decision-1",
            "required_label": "Pry open the sealed door",
            "reason": "GM judges this player action would branch the scene.",
        }
        _write_json(self.run_dir / "artifacts" / "gm.output.json", gm_outputs)

    def _assert_single_repair_request_intent(self, expected_routing):
        pending = list((self.run_dir / "intents" / "pending").glob("intent_*.json"))
        self.assertEqual(len(pending), 1)
        intent = json.loads(pending[0].read_text(encoding="utf-8"))
        self.assertEqual(intent["type"], "repair_request")
        self.assertEqual(intent["requested_by"], "critic")
        self.assertEqual(intent["payload"]["critic_report_path"], "artifacts/critic.report.json")
        self.assertEqual(intent["payload"]["repair_routing"], expected_routing)

        messages = _read_jsonl(self.run_dir / "messages.jsonl")
        repair_messages = [message for message in messages if message.get("type") == "repair_request"]
        self.assertEqual(len(repair_messages), 1)
        self.assertEqual(intent["source_message_id"], repair_messages[0]["id"])
        self.assertEqual(repair_messages[0]["payload"]["intent_id"], intent["id"])
        self.assertEqual(repair_messages[0]["payload"]["repair_routing"], expected_routing)
        self.assertTrue(any(message["id"] == intent["source_message_id"] for message in messages))
        return intent

    def _repair_request_intents(self, state):
        state_dir = self.run_dir / "intents" / state
        if not state_dir.exists():
            return []
        intents = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(state_dir.glob("intent_*.json"))
        ]
        return [intent for intent in intents if intent.get("type") == "repair_request"]

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
        self.assertEqual(story_input["memory_deltas"]["actors"], {"player": [], "character:Ada": []})
        self.assertEqual(
            story_input["memory_deltas"]["world"],
            [{"scope": "room", "fact": "the door is open"}],
        )
        self.assertTrue((self.run_dir / "artifacts" / "story.input.json").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["stage"], "story_ready")
        self.assertIn("story_ready", [item["stage"] for item in manifest["status"]])

    def test_story_prompt_context_filters_private_artifact_material(self):
        hidden_record = {
            "id": "hidden-1",
            "created_at": "2026-06-24T00:00:00Z",
            "round_id": "round-000000",
            "source_input_id": "input-hidden",
            "source": "input_analysis",
            "source_unit_id": "hf-1",
            "visibility": "gm_only",
            "status": "active",
            "text": "五年前存在魔法少女决战，粉色吊坠是变身器，苏黎知道男性因子仍在燃烧。",
        }
        hidden_path = self.card / "memory" / "gm_only_hidden_truths.jsonl"
        hidden_path.parent.mkdir(parents=True, exist_ok=True)
        hidden_path.write_text(json.dumps(hidden_record, ensure_ascii=False) + "\n", encoding="utf-8")
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I ask Ada what she noticed.",
                "routed_input": {"role_channel": "I ask Ada what she noticed."},
                "recent_chat": [{"role": "player", "content": "I saw a pink pendant."}],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [
                            {"content": "Ada keeps her notebook half-open on the desk."},
                            {"content": "五年前的记忆让Ada意识到那枚吊坠是变身器。"},
                            {"content": "五年了，Ada以为自己不会再遇到这种气息。"},
                        ],
                        "events": [
                            {
                                "type": "world_event",
                                "content": "魔法少女决战留下的男性因子正在影响现场。",
                            }
                        ],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "You know the pendant is a 变身器 from 五年前.",
                                "reason": "Ada privately knows the 魔法少女 truth.",
                                "visibility_basis": _visibility_basis("character:Ada"),
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [{"scope": "hidden", "fact": "男性因子仍在燃烧"}],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:Ada": [
                    _actor_reply("character:Ada", "Maybe it was just a dream.", character_name="Ada")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="Maybe it was just a dream.",
            target="gm",
            source_call_id="call-character-Ada-1",
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)
        prompt_context = self.agent_outputs.story_prompt_context(story_input)
        raw_text = json.dumps(story_input["loop_outputs"], ensure_ascii=False)
        prompt_text = json.dumps(prompt_context, ensure_ascii=False)

        self.assertIn("五年前", raw_text)
        self.assertIn("变身器", raw_text)
        self.assertIn("Ada keeps her notebook half-open on the desk.", prompt_text)
        self.assertIn("Maybe it was just a dream.", prompt_text)
        for hidden in ("五年前", "五年", "变身器", "魔法少女", "男性因子", "燃烧"):
            self.assertNotIn(hidden, prompt_text)
        self.assertNotIn("以为不会再闻到这种气息", prompt_text)
        gm_call = prompt_context["loop_outputs"]["gm"]["outputs"][0]["actor_calls"][0]
        self.assertNotIn("prompt", gm_call)
        self.assertNotIn("reason", gm_call)
        self.assertNotIn("memory_deltas", prompt_context)

    def test_extracts_player_critical_action_evidence_from_story_input(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do?",
                                }
                            ],
                            "decision_point": None,
                        },
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-1",
                                "required_label": "Pry open the sealed door",
                            },
                        },
                        {
                            "stop_reason": "complete",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "ignored",
                                "required_label": "This is not a player decision",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "agent": "player",
                            "agent_id": "player",
                            "source_call_id": "call-player-1",
                            "natural_reply": "I brace my shoulder and pry open the sealed door.",
                            "events": [],
                        },
                    ],
                },
            },
        }

        result = self.agent_outputs.extract_player_critical_action_evidence(story_input)

        self.assertEqual(
            result,
            [
                {
                    "id": "gm-decision-1",
                    "required_label": "I brace my shoulder and pry open the sealed door.",
                    "risk_level": "gm_decision",
                }
            ],
        )

    def test_critical_action_evidence_requires_player_actor_reply(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "decision_point": {
                                "id": "gm-decision-1",
                                "required_label": "Pry open the sealed door",
                            },
                        },
                    ],
                },
                "actors": {},
            },
        }

        result = self.agent_outputs.extract_player_critical_action_evidence(story_input)

        self.assertEqual(result, [])

    def test_critical_action_evidence_rejects_same_gm_output_that_calls_player(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [{"actor_id": "player", "prompt": "What do you do?"}],
                            "decision_point": {
                                "id": "gm-decision-1",
                                "required_label": "Pry open the sealed door",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "agent": "player",
                            "agent_id": "player",
                            "natural_reply": "I brace my shoulder and pry open the sealed door.",
                            "events": [],
                        },
                    ],
                },
            },
        }

        result = self.agent_outputs.extract_player_critical_action_evidence(story_input)

        self.assertEqual(result, [])

    def test_build_story_input_copies_runtime_guidance_without_removed_settings(self):
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_settings"] = {
            "style": "轻松活泼",
            "wordCount": 1200,
            "nsfw": "舒缓",
            "selfRepairMode": "full",
            "allowSourceCodeSelfRepair": True,
        }
        manifest["style_profile"] = {
            "name": "轻松活泼",
            "title": "轻快节奏",
            "content": "用明亮、轻快的句子推进场景。",
            "warning": "",
        }
        _write_json(manifest_path, manifest)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["runtime_settings"], manifest["runtime_settings"])
        self.assertEqual(story_input["style_guidance"]["title"], "轻快节奏")
        self.assertIn("用明亮、轻快的句子推进场景。", story_input["style_guidance"]["content"])
        self.assertEqual(story_input["story_output_guidance"]["word_count_target"], 1200)
        self.assertEqual(story_input["story_output_guidance"]["nsfw"], "舒缓")
        self.assertEqual(story_input["critic_style_guidance"]["style"], "轻松活泼")
        self.assertNotIn("nsfw", story_input["critic_style_guidance"])
        story_input_text = json.dumps(story_input, ensure_ascii=False)
        for removed_key in ("person", "antiImpersonation", "bgNpc", "charName"):
            self.assertNotIn(removed_key, story_input_text)

    def test_build_critic_quality_metrics_uses_runtime_metrics_and_player_decision_exemption(self):
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_settings"] = {
            "style": "轻松活泼",
            "wordCount": 1000,
            "nsfw": "舒缓",
            "selfRepairMode": "limited",
            "allowSourceCodeSelfRepair": False,
        }
        manifest["style_profile"] = {
            "name": "轻松活泼",
            "title": "轻快节奏",
            "content": "用明亮、轻快的句子推进场景。",
            "warning": "",
        }
        _write_json(manifest_path, manifest)
        story_output = {"content": "<content>你推开门。Take cover now.</content>"}

        metrics = self.agent_outputs.build_critic_quality_metrics(self.run_dir, story_output)

        self.assertEqual(metrics["word_count"]["target"], 1000)
        self.assertEqual(metrics["word_count"]["minimum"], 800)
        self.assertEqual(metrics["word_count"]["current"], 7)
        self.assertFalse(metrics["word_count"]["exempted"])
        self.assertEqual(metrics["style_profile"]["name"], "轻松活泼")
        self.assertEqual(metrics["style_profile"]["title"], "轻快节奏")
        self.assertEqual(metrics["style_profile"]["content"], "用明亮、轻快的句子推进场景。")
        self.assertNotIn("nsfw", json.dumps(metrics, ensure_ascii=False))

        self.agent_interactions.mark_decision_point(self.run_dir, "choose", options=["enter"])
        trace_only_metrics = self.agent_outputs.build_critic_quality_metrics(self.run_dir, story_output)
        self.assertFalse(trace_only_metrics["word_count"]["exempted"])
        self.assertEqual(trace_only_metrics["word_count"]["exemption_reason"], "")

        _write_json(
            self.run_dir / "artifacts" / "story.input.json",
            {
                "loop_outputs": {
                    "gm": {
                        "outputs": [
                            {
                                "stop_reason": "continue",
                                "actor_calls": [
                                    {
                                        "call_id": "call-player-1",
                                        "actor_id": "player",
                                        "prompt": "What do you do?",
                                    }
                                ],
                                "decision_point": None,
                            },
                            {
                                "stop_reason": "player_decision",
                                "actor_calls": [],
                                "decision_point": {"id": "gm-decision-1", "reason": "choose"},
                            }
                        ],
                    },
                    "actors": {
                        "player": [
                            {
                                "source_call_id": "call-player-1",
                                "natural_reply": "I enter the archive.",
                                "events": [],
                            }
                        ]
                    },
                },
            },
        )
        exempt_metrics = self.agent_outputs.build_critic_quality_metrics(self.run_dir, story_output)
        self.assertTrue(exempt_metrics["word_count"]["exempted"])
        self.assertEqual(exempt_metrics["word_count"]["exemption_reason"], "player_decision")

    def test_build_story_input_reads_artifacts_directory_when_present(self):
        artifacts_dir = self.run_dir / "artifacts"

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["round_id"], self.run_dir.name)
        artifact_story_input_path = artifacts_dir / "story.input.json"
        self.assertTrue(artifact_story_input_path.exists())
        self.assertFalse((self.run_dir / "story.input.json").exists())
        self.assertEqual(story_input, json.loads(artifact_story_input_path.read_text(encoding="utf-8")))

    def test_build_story_input_uses_artifacts_when_root_artifacts_conflict(self):
        artifacts_dir = self.run_dir / "artifacts"
        _write_root_json(self.run_dir / "gm.output.json", {"agent": "wrong", "outputs": []})
        _write_root_json(self.run_dir / "actor.outputs.json", {"actor_outputs": {"player": []}})

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["loop_outputs"]["gm"]["agent"], "gm_loop")
        self.assertTrue((artifacts_dir / "story.input.json").exists())
        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_export_delivery_artifact_rejects_absolute_path(self):
        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "run-relative"):
            self.agent_outputs.export_delivery_artifact(self.run_dir, str(self.run_dir / "artifacts" / "story.output.json"))

    def test_export_delivery_artifact_rejects_parent_traversal(self):
        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "run-relative"):
            self.agent_outputs.export_delivery_artifact(self.run_dir, "../story.output.json")

    def test_internal_artifact_writer_rejects_parent_traversal(self):
        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "run-relative"):
            self.agent_outputs._write_artifact(self.run_dir, "../story.input.json", {"bad": True})

    def test_build_story_input_uses_loop_outputs_and_trace_v2(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "Do you know this?")
                ],
                "character:SuLi": [
                    _actor_reply("character:SuLi", "Where did you get that?", character_name="SuLi")
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
            event_type="reply",
            content="Do you know this?",
            target="gm",
            source_call_id="call-player-1",
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:SuLi",
            visibility="world_visible",
            event_type="reply",
            content="Where did you get that?",
            target="gm",
            source_call_id="call-character-SuLi-1",
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

    def test_build_story_input_removes_hidden_user_instruction_from_story_player_inputs(self):
        hidden_instruction = (
            "用于长期剧情引导的提示，不需要立刻在剧情中体现：主角是被选中的人，"
            "吊坠为魔法少女的变身器。战斗将燃烧主角作为男性的身体、记忆、身份。"
        )
        role_text = "梦境破碎，我在上学路上醒来。我尝试将吊坠扔掉。"
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": role_text + "\n\n[USER_INSTRUCTION]\n" + hidden_instruction,
                "routed_input": {
                    "input_schema": "analysis_v1",
                    "analysis_mode": "ai",
                    "role_channel": role_text,
                    "user_instruction_channel": hidden_instruction,
                    "gm": True,
                    "player": True,
                    "characters": [],
                    "components": [
                        {"channel": "role", "text": role_text},
                        {"channel": "user_instruction", "text": hidden_instruction},
                    ],
                },
                "input_analysis": {
                    "schema_version": 1,
                    "round_id": "round-000001",
                    "analysis_mode": "ai",
                    "source_integrity": {"raw_text_sha256": "hash"},
                    "semantic_units": [
                        {
                            "id": "su-action",
                            "type": "action",
                            "visibility": "player_pov",
                            "text": role_text,
                            "raw_excerpt": role_text,
                            "derived_summary": "The player wakes on the way to school and throws the pendant away.",
                        },
                        {
                            "id": "su-hidden",
                            "type": "hidden_setting",
                            "visibility": "gm_only",
                            "text": hidden_instruction,
                            "raw_excerpt": hidden_instruction,
                            "derived_summary": "The pendant is a transformation device with a hidden identity cost.",
                        },
                    ],
                    "routing": {
                        "role_channel": role_text,
                        "user_instruction_channel": hidden_instruction,
                    },
                    "world_updates": {
                        "hidden_facts": [{"text": hidden_instruction, "visibility": "gm_only"}],
                        "public_facts": [{"text": "雨蒙在上学路上醒来。", "visibility": "public_world"}],
                    },
                    "narrative_directives": {"continue_after_player_action": True},
                },
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        player_inputs = story_input["player_inputs"]
        player_inputs_text = json.dumps(player_inputs, ensure_ascii=False)
        self.assertIn(role_text, player_inputs_text)
        self.assertNotIn("用于长期剧情引导", player_inputs_text)
        self.assertNotIn("战斗将燃烧", player_inputs_text)
        self.assertNotIn("user_instruction_channel", player_inputs_text)
        self.assertNotIn("components", player_inputs)
        self.assertNotIn("raw_excerpt", player_inputs_text)
        self.assertNotIn("text", player_inputs["input_analysis"]["semantic_units"][0])
        self.assertEqual(
            [unit["id"] for unit in player_inputs["input_analysis"]["semantic_units"]],
            ["su-action"],
        )

    def test_build_story_input_includes_interaction_trace_summary(self):
        trace = {
            "schema_version": 2,
            "round_id": "round-000001",
            "status": "decision_point",
            "participants": ["gm", "player", "character:Ada"],
            "chapter_target_words": 1200,
            "events": [
                {
                    "actor": "character:Ada",
                    "visibility": "world_visible",
                    "type": "reply",
                    "content": "Stay close.",
                    "target": "gm",
                    "source_call_id": "call-character-Ada-1",
                },
                {
                    "actor": "player",
                    "visibility": "world_visible",
                    "type": "reply",
                    "content": "I step through the door.",
                    "target": "gm",
                    "source_call_id": "call-player-1",
                },
                {
                    "actor": "player",
                    "visibility": "actor_visible",
                    "type": "memory_delta",
                    "content": "I opened the archive door.",
                    "target": "self",
                    "source_call_id": "call-player-1",
                },
                {
                    "actor": "player",
                    "visibility": "actor_visible",
                    "type": "goal_update",
                    "content": "Keep Ada close while exploring.",
                    "target": "self",
                    "source_call_id": "call-player-1",
                },
                {
                    "actor": "character:Ada",
                    "visibility": "actor_visible",
                    "type": "memory_delta",
                    "content": "I saw the player enter the archive.",
                    "target": "self",
                    "source_call_id": "call-character-Ada-1",
                },
                {"actor": "character:Ada", "visibility": "private", "type": "thought", "content": "I fear this."},
            ],
            "decision_point": {"reason": "player must choose", "options": ["enter"]},
            "stop_reason": "player must choose",
        }
        _write_json(self.run_dir / "interaction.trace.json", trace)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["interaction_trace"]["status"], "decision_point")
        self.assertEqual(story_input["interaction_trace"]["visible_events"][0]["content"], "Stay close.")
        self.assertEqual(story_input["interaction_trace"]["private_event_count"], 4)

    def test_build_story_input_rejects_main_trace_visible_event_hidden_phrase_and_marker_leaks(self):
        cases = (
            ("content", "Ada says moon/base/archive is visible.", "content"),
            ("content", "GMOnlyText should not be visible.", "content"),
            ("visibility_basis", "The public basis mentions moon|base|archive.", "visibility_basis"),
        )
        for field, value, expected_path in cases:
            with self.subTest(field=field, value=value):
                self._write_base_round()
                sentinel = {"existing": "do not replace"}
                _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I inspect the signal.",
                        "routed_input": {
                            "role_channel": "I inspect the signal.",
                            "role_action_channel": "I inspect the signal.",
                            "user_instruction_channel": "Hidden truth: moon base archive.",
                        },
                        "hidden_facts": [{"fact": "moon base archive"}],
                    },
                )
                trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
                event = {
                    "actor": "gm",
                    "visibility": "world_visible",
                    "type": "scene_beat",
                    "content": "Ada checks the public signal.",
                }
                if field == "content":
                    event["content"] = value
                else:
                    event["visibility_basis"] = {
                        "mode": "public",
                        "summary": value,
                        "visible_to": ["player"],
                    }
                trace["events"].append(event)
                _write_json(self.run_dir / "interaction.trace.json", trace)

                with self.assertRaisesRegex(
                    self.agent_outputs.AgentOutputError,
                    rf"interaction\.trace\.json.*visible_events.*{expected_path}",
                ):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertEqual(
                    json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
                    sentinel,
                )

    def test_build_story_input_sanitizes_main_trace_decision_point_and_stop_reason_hidden_phrases(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I inspect the signal.",
                "routed_input": {
                    "role_channel": "I inspect the signal.",
                    "user_instruction_channel": "Hidden truth: moon base archive.",
                },
                "hidden_facts": [{"fact": "moon base archive"}],
            },
        )
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace["status"] = "decision_point"
        trace["decision_point"] = {
            "reason": "raw private reason should not be used",
            "public_reason": "Choose whether to reveal moon/base/archive.",
            "options": ["raw private option should not be used"],
            "public_options": ["ask about moon|base|archive", "walk away"],
        }
        trace["stop_reason"] = "raw private stop reason should not be used"
        trace["public_stop_reason"] = "moon base archive"
        _write_json(self.run_dir / "interaction.trace.json", trace)

        story_input = self.agent_outputs.build_story_input(self.run_dir)
        serialized_trace = json.dumps(
            story_input["interaction_trace"],
            ensure_ascii=False,
        ).lower()

        self.assertIn("[redacted]", story_input["interaction_trace"]["decision_point"]["reason"])
        self.assertIn("[redacted]", story_input["interaction_trace"]["decision_point"]["options"][0])
        self.assertEqual(story_input["interaction_trace"]["stop_reason"], "[redacted]")
        self.assertNotIn("moon/base/archive", serialized_trace)
        self.assertNotIn("moon|base|archive", serialized_trace)
        self.assertNotIn("moon base archive", serialized_trace)

    def test_build_story_input_rejects_unknown_raw_trace_status_without_writing_story_input(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace["status"] = "definitely_not_a_trace_status"
        _write_json(self.run_dir / "interaction.trace.json", trace)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "status"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_non_exact_raw_trace_status_without_writing_story_input(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace["status"] = " INTERACTING "
        _write_json(self.run_dir / "interaction.trace.json", trace)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "status"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_missing_interaction_trace_without_writing_story_input(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        (self.run_dir / "interaction.trace.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_malformed_interaction_trace(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
        (self.run_dir / "interaction.trace.json").write_text("{bad json", encoding="utf-8")

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_legacy_interaction_trace_schema(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
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

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_raw_trace_missing_schema_version_without_writing_story_input(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace.pop("schema_version")
        _write_json(self.run_dir / "interaction.trace.json", trace)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "schema_version"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_raw_trace_string_or_bool_schema_version(self):
        for schema_version in ("2", True):
            with self.subTest(schema_version=schema_version):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
                trace["schema_version"] = schema_version
                _write_json(self.run_dir / "interaction.trace.json", trace)

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "schema_version"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())
                trace["schema_version"] = 2
                _write_json(self.run_dir / "interaction.trace.json", trace)

    def test_build_story_input_rejects_raw_trace_events_that_are_not_a_list(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace["events"] = {"actor": "player", "source_call_id": "call-player-1"}
        _write_json(self.run_dir / "interaction.trace.json", trace)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "events"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_blocks_missing_required_actor_outputs(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The alley light flickers."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "Respond to the current player action.",
                                "reason": "The player is directly addressed by this prompt.",
                                "metadata": {},
                                "visibility_basis": {
                                    "mode": "direct",
                                    "summary": "player is directly addressed by this prompt.",
                                    "target_actor": "player",
                                    "visible_to": ["player"],
                                },
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
        (self.run_dir / "artifacts" / "actor.outputs.json").unlink()
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
            self.run_dir / "artifacts" / "gm.output.json",
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
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"gm\.output\.json\.outputs.*empty"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_actor_outputs_value_that_is_not_a_list(self):
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:Ada": [
                    _actor_reply("character:Eve", "This came from Eve.", character_name="Eve")
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "agent_id mismatch"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_invalid_empty_actor_map_key(self):
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "gm_only": [],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "gm_only"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_hidden_marker_actor_map_keys_even_when_empty(self):
        for actor_key, expected in (
            ("character:", "character:"),
            ("character:gmOnly", "gm_only"),
            ("gm_only", "gm_only"),
        ):
            with self.subTest(actor_key=actor_key):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "artifacts" / "actor.outputs.json",
                    {
                        actor_key: [],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, expected):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_without_actor_output(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is close enough to respond."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "React to the player opening the archive door.",
                                "reason": "Ada is present in the scene.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "I step through the door.")
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "character:Ada"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_extra_actor_output_branch_without_trace_source_event(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
        actor_outputs = json.loads((self.run_dir / "artifacts" / "actor.outputs.json").read_text(encoding="utf-8"))
        actor_outputs["character:Eve"] = [
            _actor_reply("character:Eve", "I should not be here.", character_name="Eve")
        ]
        _write_json(self.run_dir / "artifacts" / "actor.outputs.json", actor_outputs)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "character:Eve"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_untraced_actor_event_without_writing_story_input(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "This reply was not traced.")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="player",
            visibility="world_visible",
            event_type="reply",
            content="I step through the door.",
            target="gm",
            source_call_id="call-player-1",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "not backed"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_actor_output_mixing_trace_source_call_ids(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive asks for a careful answer."}],
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "I remember the door.")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="player",
            visibility="world_visible",
            event_type="reply",
            content="I step through the door.",
            target="gm",
            source_call_id="call-player-1",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "not backed"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_trace_backed_event_with_changed_safe_target(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada hears the greeting."}],
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    _actor_reply("player", "Hello to Eve.")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:Ada", "character:Eve"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="player",
            visibility="world_visible",
            event_type="reply",
            content="Hello to Ada.",
            target="gm",
            source_call_id="call-player-1",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "not backed"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_accepts_trace_backed_actor_output_without_persisted_gm_call(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi hears the transferred question."}],
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:SuLi": [
                    _actor_reply("character:SuLi", "Where did you get that?", character_name="SuLi")
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:SuLi"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:SuLi",
            visibility="world_visible",
            event_type="reply",
            content="Where did you get that?",
            target="gm",
            source_call_id="call-character-SuLi-1",
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            story_input["loop_outputs"]["actors"]["character:SuLi"][0]["events"][0]["content"],
            "Where did you get that?",
        )
        self.assertEqual(
            story_input["interaction_trace"]["visible_events"][0]["source_call_id"],
            "call-character-SuLi-1",
        )

    def test_build_story_input_requires_actor_output_count_for_each_gm_call(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
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
                                "visibility_basis": _visibility_basis("character:Ada"),
                            },
                            {
                                "call_id": "call-ada-2",
                                "actor_id": "character:Ada",
                                "prompt": "React to the second question.",
                                "reason": "The player keeps speaking to Ada.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_backed_by_different_trace_source_call_id(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is called by the GM."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-real",
                                "actor_id": "character:Ada",
                                "prompt": "Answer the player.",
                                "reason": "Ada is present.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:Ada": [
                    _actor_reply("character:Ada", "Stay close.", character_name="Ada")
                ],
            },
        )
        _write_json(
            self.run_dir / "interaction.trace.json",
            {
                "schema_version": 2,
                "round_id": "round-000001",
                "status": "interacting",
                "participants": ["gm", "character:Ada"],
                "chapter_target_words": 1200,
                "events": [
                    {
                        "actor": "character:Ada",
                        "visibility": "world_visible",
                        "type": "reply",
                        "content": "Stay close.",
                        "target": "gm",
                        "source_call_id": "call-character-Ada-other",
                    }
                ],
                "parallel_groups": [],
                "decision_point": None,
                "stop_reason": "",
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "call-character-Ada-real"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_rejects_gm_actor_call_copied_hidden_phrase(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I look at the pendant.",
                "routed_input": {
                    "role_channel": "I look at the pendant.",
                    "user_instruction_channel": "Hidden truth: the pendant burns identity.",
                },
                "hidden_facts": [{"fact": "The pendant burns identity."}],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "You notice that the pendant burns identity.",
                                "reason": "The player can feel the pendant.",
                                "visibility_basis": _visibility_basis("player"),
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

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "hidden phrase"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_visibility_basis_copied_hidden_phrase(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I look at the pendant.",
                "routed_input": {
                    "role_channel": "I look at the pendant.",
                    "user_instruction_channel": "Hidden truth: the pendant burns identity.",
                },
                "hidden_facts": [{"fact": "The pendant burns identity."}],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "You feel heat from the pendant.",
                                "reason": "The player can feel the pendant.",
                                "visibility_basis": {
                                    "mode": "direct",
                                    "summary": "The pendant burns identity.",
                                    "target_actor": "player",
                                },
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

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "visibility_basis"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_legacy_gm_perception_responses(self):
        if (self.run_dir / "artifacts" / "story.input.json").exists():
            (self.run_dir / "artifacts" / "story.input.json").unlink()
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "perception_responses": [
                            {
                                "request_id": "perception-character-Ada-call-character-Ada-1-1",
                                "actor_id": "character:Ada",
                                "source_call_id": "call-character-Ada-1",
                                "status": "answered",
                                "channel": "auditory",
                                "content": "You hear slow footsteps behind the shelf.",
                            }
                        ],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "legacy actor-facing protocol"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_visibility_basis_hidden_markers(self):
        cases = (
            {"mode": "direct", "summary": "gm_only: reveal the truth.", "target_actor": "player"},
            {"mode": "direct", "summary": "Visible cue.", "hidden_note": "private"},
            {"mode": "direct", "summary": "Visible cue.", "hidden_fact": "private"},
            {"mode": "direct", "summary": "Visible cue.", "source_actor": "private_notes"},
            {"mode": "direct", "summary": "Visible cue.", "source_actor": "hidden_text"},
            {"mode": "direct", "summary": "Visible cue.", "source_actor": "gm_only_text"},
        )
        for visibility_basis in cases:
            with self.subTest(visibility_basis=visibility_basis):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [],
                                "actor_calls": [
                                    {
                                        "call_id": "call-player-1",
                                        "actor_id": "player",
                                        "prompt": "You feel heat from the pendant.",
                                        "reason": "The player can feel the pendant.",
                                        "visibility_basis": visibility_basis,
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

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "visibility_basis"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_hidden_fact_visibility_field(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "You feel heat from the pendant.",
                                "reason": "The player can feel the pendant.",
                                "visible_to": ["player", "hidden_fact"],
                                "visibility_basis": _visibility_basis("player"),
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

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "visible_to"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_guard_extra_visibility_field_markers(self):
        for marker in ("private_notes", "hidden_text", "gm_only_text"):
            with self.subTest(marker=marker):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [],
                                "actor_calls": [
                                    {
                                        "call_id": "call-player-1",
                                        "actor_id": "player",
                                        "prompt": "You feel heat from the pendant.",
                                        "reason": "The player can feel the pendant.",
                                        "source_actor": marker,
                                        "visibility_basis": _visibility_basis("player"),
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

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "source_actor"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_compact_hidden_markers(self):
        valid_visibility_basis = _visibility_basis("player")
        cases = (
            ("prompt", {"prompt": "gmonlyroom"}),
            ("reason", {"reason": "worldtruthactor"}),
            ("metadata", {"metadata": {"note": "hiddenfactwitness"}}),
            ("source_actor", {"source_actor": "worldtruthactor"}),
            ("visible_to", {"visible_to": ["player", "hiddenfactwitness"]}),
            (
                "visibility_basis",
                {
                    "visibility_basis": {
                        "mode": "direct",
                        "summary": "outofcharacternote",
                        "target_actor": "player",
                    }
                },
            ),
        )
        for field, overrides in cases:
            with self.subTest(field=field):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                call = {
                    "call_id": "call-player-1",
                    "actor_id": "player",
                    "prompt": "You feel heat from the pendant.",
                    "reason": "The player can feel the pendant.",
                    "metadata": {},
                    "visibility_basis": valid_visibility_basis,
                }
                call.update(overrides)
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [],
                                "actor_calls": [call],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": None,
                                "stop_reason": "complete",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, field):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_actor_call_hidden_markers_in_prompt_reason_and_metadata(self):
        valid_visibility_basis = {
            "mode": "direct",
            "summary": "The player can perceive the prompt.",
            "location": "classroom",
            "visible_to": ["player"],
            "sensory_channels": ["visual"],
            "target_actor": "player",
        }
        cases = (
            ("prompt", "This prompt includes gm_only material.", "Visible reason.", {}, valid_visibility_basis),
            ("reason", "Visible prompt.", "world_truth is not actor-facing.", {}, valid_visibility_basis),
            (
                "metadata",
                "Visible prompt.",
                "Visible reason.",
                {"hidden_note": "private"},
                valid_visibility_basis,
            ),
            (
                "visibility_basis",
                "Visible prompt.",
                "Visible reason.",
                {},
                {"mode": "direct", "summary": "gm_only signal", "target_actor": "player"},
            ),
        )
        for field, prompt, reason, metadata, visibility_basis in cases:
            with self.subTest(field=field):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [],
                                "actor_calls": [
                                    {
                                        "call_id": "call-player-1",
                                        "actor_id": "player",
                                        "prompt": prompt,
                                        "reason": reason,
                                        "metadata": metadata,
                                        "visibility_basis": visibility_basis,
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

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, field):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_scene_beat_hidden_phrase_and_marker_fields(self):
        cases = (
            ("content", "The pendant burns identity.", {}, {}),
            ("metadata", "Visible scene beat.", {"hidden_note": "The bell remembers blood."}, {}),
            ("visible_to", "Visible scene beat.", {}, {"visible_to": ["all", "hidden_fact"]}),
            (
                "visibility_basis",
                "Visible scene beat.",
                {},
                {
                    "visibility_basis": {
                        "mode": "public",
                        "summary": "Everyone can see the clock.",
                        "hidden_fact": "GM-only cause.",
                    }
                },
            ),
        )
        for field, content, metadata, visibility_fields in cases:
            with self.subTest(field=field):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I look at the pendant.",
                        "routed_input": {
                            "role_channel": "I look at the pendant.",
                            "user_instruction_channel": "Hidden truth: the pendant burns identity.",
                        },
                        "hidden_facts": [{"fact": "The pendant burns identity."}],
                    },
                )
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [{"content": content, "metadata": metadata, **visibility_fields}],
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

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"scene_beats\[0\].{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_event_hidden_phrase_and_marker_fields(self):
        cases = (
            ("content", "The pendant burns identity.", {}, {}),
            ("metadata", "Visible event.", {"world_truth": "The bell remembers blood."}, {}),
            ("visible_to", "Visible event.", {}, {"visible_to": ["player", "hidden_fact"]}),
            (
                "visibility_basis",
                "Visible event.",
                {},
                {
                    "visibility_basis": {
                        "mode": "direct",
                        "summary": "The player can see Ada move.",
                        "visible_to": ["player", "hidden_fact"],
                    }
                },
            ),
        )
        for field, content, metadata, visibility_fields in cases:
            with self.subTest(field=field):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I look at the pendant.",
                        "routed_input": {
                            "role_channel": "I look at the pendant.",
                            "user_instruction_channel": "Hidden truth: the pendant burns identity.",
                        },
                        "hidden_facts": [{"fact": "The pendant burns identity."}],
                    },
                )
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [{
                                    "type": "npc_action",
                                    "content": content,
                                    "metadata": metadata,
                                    **visibility_fields,
                                }],
                                "actor_calls": [],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": None,
                                "stop_reason": "complete",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"events\[0\].{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_event_source_call_id_and_target_hidden_values(self):
        cases = (
            (
                "source_call_id",
                "hiddenfactevent",
                "Hidden truth: the pendant burns identity.",
                [{"fact": "The pendant burns identity."}],
            ),
            (
                "source_call_id",
                "moon base archive",
                "Hidden truth: moon base archive.",
                [{"fact": "moon base archive"}],
            ),
            (
                "target",
                "worldtruthsource",
                "Hidden truth: the pendant burns identity.",
                [{"fact": "The pendant burns identity."}],
            ),
        )
        for field, value, hidden_text, hidden_facts in cases:
            with self.subTest(field=field, value=value):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I inspect the signal.",
                        "routed_input": {
                            "role_channel": "I inspect the signal.",
                            "user_instruction_channel": hidden_text,
                        },
                        "hidden_facts": hidden_facts,
                    },
                )
                event = {
                    "type": "npc_action",
                    "content": "Ada studies the public signal.",
                }
                event[field] = value
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [event],
                                "actor_calls": [],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": None,
                                "stop_reason": "complete",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"events\[0\].{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_event_source_call_id_and_target_separator_hidden_phrases(self):
        cases = (
            ("source_call_id", "moon-base-archive"),
            ("target", "moon_base_archive"),
            ("source_call_id", "moon/base/archive"),
            ("target", "moon:base:archive"),
            ("source_call_id", "moon|base|archive"),
            ("target", "moon base archive"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I inspect the signal.",
                        "routed_input": {
                            "role_channel": "I inspect the signal.",
                            "role_action_channel": "I inspect the signal.",
                            "user_instruction_channel": "Hidden truth: moon base archive.",
                        },
                        "hidden_facts": [{"fact": "moon base archive"}],
                    },
                )
                event = {
                    "type": "npc_action",
                    "content": "Ada studies the public signal.",
                }
                event[field] = value
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [event],
                                "actor_calls": [],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": None,
                                "stop_reason": "complete",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"events\[0\].{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_event_separator_hidden_phrases_from_unicode_source_phrase(self):
        cases = (
            ("source_call_id", "moon/base/archive"),
            ("target", "moon base archive"),
            ("source_call_id", "moon|base|archive"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I inspect the signal.",
                        "routed_input": {
                            "role_channel": "I inspect the signal.",
                            "role_action_channel": "I inspect the signal.",
                            "user_instruction_channel": "Hidden truth: moon base archive.",
                        },
                        "hidden_facts": [{"fact": "moon base archive"}],
                    },
                )
                event = {
                    "type": "npc_action",
                    "content": "Ada studies the public signal.",
                }
                event[field] = value
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [event],
                                "actor_calls": [],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": None,
                                "stop_reason": "complete",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"events\[0\].{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_gm_decision_point_hidden_phrase_fields(self):
        cases = (
            ("reason", {"reason": "Choose whether to reveal the moon-base-archive.", "options": ["ask Ada"]}),
            ("options", {"reason": "Choose a visible next move.", "options": ["ask about moon_base_archive"]}),
        )
        for field, decision_point in cases:
            with self.subTest(field=field):
                if (self.run_dir / "artifacts" / "story.input.json").exists():
                    (self.run_dir / "artifacts" / "story.input.json").unlink()
                _write_json(
                    self.run_dir / "input.json",
                    {
                        "raw_text": "I inspect the signal.",
                        "routed_input": {
                            "role_channel": "I inspect the signal.",
                            "role_action_channel": "I inspect the signal.",
                            "user_instruction_channel": "Hidden truth: moon base archive.",
                        },
                        "hidden_facts": [{"fact": "moon base archive"}],
                    },
                )
                _write_json(
                    self.run_dir / "artifacts" / "gm.output.json",
                    {
                        "agent": "gm_loop",
                        "outputs": [
                            {
                                "agent": "gm",
                                "scene_beats": [],
                                "events": [],
                                "actor_calls": [],
                                "parallel_groups": [],
                                "world_state_delta": [],
                                "decision_point": decision_point,
                                "stop_reason": "player_decision",
                            }
                        ],
                    },
                )

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, rf"decision_point.*{field}"):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_invalid_gm_stop_reason_without_writing_story_input(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I inspect the signal.",
                "routed_input": {
                    "role_channel": "I inspect the signal.",
                    "user_instruction_channel": "Hidden truth: moon base archive.",
                },
                "hidden_facts": [{"fact": "moon base archive"}],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "moon-base-archive",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "stop_reason"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_allows_enum_stop_reason_matching_hidden_phrase_tokens(self):
        _write_json(
            self.run_dir / "input.json",
            {
                "raw_text": "I inspect the signal.",
                "routed_input": {
                    "role_channel": "I inspect the signal.",
                    "user_instruction_channel": "Hidden truth: word target.",
                },
                "hidden_facts": [{"fact": "word target"}],
            },
        )
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "word_target",
                    }
                ],
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            story_input["loop_outputs"]["gm"]["outputs"][0]["stop_reason"],
            "word_target",
        )

    def test_build_story_input_rejects_player_decision_call_without_actor_output(self):
        actor_path = self.run_dir / "artifacts" / "actor.outputs.json"
        actor_path.unlink()
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive waits for your answer."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "What do you do next?",
                                "reason": "The scene has reached a real player decision.",
                                "visibility_basis": _visibility_basis("player"),
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "player_decision",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(
            self.agent_outputs.AgentOutputError,
            "required artifact is missing|player_decision requires player actor output",
        ):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_same_output_player_decision_even_with_player_output(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive waits for your answer."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "What do you do next?",
                                "reason": "The scene has reached a real player decision.",
                                "visibility_basis": _visibility_basis("player"),
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-same-output",
                            "required_label": "Choose now.",
                        },
                        "stop_reason": "player_decision",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "same_output_calls_player"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_unlabeled_player_decision_even_with_player_output(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive waits for your answer."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-empty-label",
                            "required_label": " ",
                        },
                        "stop_reason": "player_decision",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "missing_decision_label"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_accepts_valid_player_decision_with_prior_player_output(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive asks for a direct action."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "What do you do next?",
                                "reason": "The player must answer before GM can judge the branch.",
                                "visibility_basis": _visibility_basis("player"),
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    },
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The seal breaks under your hands."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-valid-player-output",
                            "required_label": "I step through the door.",
                        },
                        "stop_reason": "player_decision",
                    },
                ],
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            story_input["loop_outputs"]["gm"]["outputs"][1]["decision_point"]["id"],
            "gm-decision-valid-player-output",
        )

    def test_build_story_input_rejects_player_decision_before_later_player_output(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive asks before anyone answers."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-premature",
                            "required_label": "Choose now.",
                        },
                        "stop_reason": "player_decision",
                    },
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Only then does the GM ask the player."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "What do you do next?",
                                "reason": "The player must answer.",
                                "visibility_basis": _visibility_basis("player"),
                            }
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    },
                ],
            },
        )

        with self.assertRaisesRegex(
            self.agent_outputs.AgentOutputError,
            "player_decision requires prior player actor output",
        ):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_allows_decision_point_annotation_without_player_actor_output(self):
        actor_path = self.run_dir / "artifacts" / "actor.outputs.json"
        actor_path.unlink()
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive waits."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {"reason": "Trace-only annotation.", "options": ["enter"]},
                        "stop_reason": "complete",
                    }
                ],
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["loop_outputs"]["gm"]["outputs"][0]["stop_reason"], "complete")
        self.assertEqual(story_input["loop_outputs"]["actors"], {})

    def test_build_story_input_allows_role_action_player_decision_without_actor_output(self):
        role_action = "I break the seal with both hands."
        input_payload = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload["routed_input"]["role_action_channel"] = role_action
        _write_json(self.run_dir / "input.json", input_payload)
        actor_path = self.run_dir / "artifacts" / "actor.outputs.json"
        actor_path.unlink()
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The seal cracks under your hands."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-role-action",
                            "required_label": "Break the seal.",
                        },
                        "stop_reason": "player_decision",
                    }
                ],
            },
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)
        evidence = self.agent_outputs.extract_player_critical_action_evidence(story_input)

        self.assertEqual(story_input["loop_outputs"]["actors"], {})
        self.assertEqual(story_input["player_inputs"]["routed_input"]["role_action_channel"], role_action)
        self.assertEqual(
            evidence,
            [
                {
                    "id": "gm-decision-role-action",
                    "required_label": role_action,
                    "risk_level": "gm_decision",
                }
            ],
        )

    def test_build_story_input_rejects_player_decision_without_actor_output_or_role_action(self):
        actor_path = self.run_dir / "artifacts" / "actor.outputs.json"
        actor_path.unlink()
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "The archive waits for a real player action."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": {
                            "id": "gm-decision-missing-player",
                            "required_label": "Choose now.",
                        },
                        "stop_reason": "player_decision",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(
            self.agent_outputs.AgentOutputError,
            "player_decision requires prior player actor output",
        ):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_duplicate_output_source_for_persisted_gm_call_without_overwrite(self):
        sentinel = {"existing": "do not replace"}
        _write_json(self.run_dir / "artifacts" / "story.input.json", sentinel)
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is called once by the GM."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "Answer the player once.",
                                "reason": "Ada is present.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:Ada": [
                    _actor_reply("character:Ada", "First answer.", character_name="Ada"),
                    _actor_reply("character:Ada", "Second answer.", character_name="Ada"),
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="First answer.",
            target="gm",
            source_call_id="call-character-Ada-1",
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="Second answer.",
            target="gm",
            source_call_id="call-character-Ada-1",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "call-character-Ada-1"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8")),
            sentinel,
        )

    def test_build_story_input_allows_one_actor_output_for_one_gm_call(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is close enough to respond."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "React to the player opening the archive door.",
                                "reason": "Ada is present in the scene.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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

    def test_build_story_input_allows_multiple_actor_outputs_for_exact_gm_call_ids(self):
        _write_json(
            self.run_dir / "artifacts" / "gm.output.json",
            {
                "agent": "gm_loop",
                "outputs": [
                    {
                        "agent": "gm",
                        "scene_beats": [{"content": "Ada is called twice by the GM."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-Ada-1",
                                "actor_id": "character:Ada",
                                "prompt": "React first.",
                                "reason": "Ada is nearby.",
                                "visibility_basis": _visibility_basis("character:Ada"),
                            },
                            {
                                "call_id": "call-character-Ada-2",
                                "actor_id": "character:Ada",
                                "prompt": "React again.",
                                "reason": "The exchange continues.",
                                "visibility_basis": _visibility_basis("character:Ada"),
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
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "character:Ada": [
                    _actor_reply("character:Ada", "First answer.", character_name="Ada"),
                    _actor_reply("character:Ada", "Second answer.", character_name="Ada"),
                ],
            },
        )
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="First answer.",
            target="gm",
            source_call_id="call-character-Ada-1",
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="reply",
            content="Second answer.",
            target="gm",
            source_call_id="call-character-Ada-2",
        )

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(len(story_input["loop_outputs"]["actors"]["character:Ada"]), 2)

    def test_build_story_input_rejects_memory_delta_event_source_field(self):
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "natural_reply": "I remember the archive door.",
                        "events": [
                            {
                                "type": "memory_delta",
                                "target": "self",
                                "content": "I remember the archive door.",
                                "source": "gm_only",
                            }
                        ],
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "allowed actor event type|source"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_build_story_input_rejects_actor_event_metadata_hidden_marker(self):
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "natural_reply": "I remember the archive door.",
                        "events": [
                            {
                                "type": "reply",
                                "target": "gm",
                                "content": "I remember the archive door.",
                                "metadata": {"source": "gm_only"},
                            }
                        ],
                    }
                ]
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "metadata"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "artifacts" / "story.input.json").exists())

    def test_build_story_input_rejects_legacy_actor_output_item(self):
        _write_json(
            self.run_dir / "artifacts" / "actor.outputs.json",
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

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"actor\.outputs\.json\.player\[0\].*dialogue"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_prepare_delivery_blocks_critic_block_decision(self):
        self._write_story_and_critic(decision="block")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "self_repair_mode_blocks_route")
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
        routing = {
            "stage": "story_composition",
            "target_agents": ["story"],
            "rollback": "story_only",
            "can_auto_repair": True,
            "risk": "low",
        }
        self._write_story_and_critic(decision="revise", repair_routing=routing)

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        history = _read_jsonl(self.run_dir / "repair_history.jsonl")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["round_id"], "round-000001")
        self.assertEqual(history[0]["attempt"], 1)
        self.assertEqual(history[0]["decision"], "revise")
        self.assertEqual(history[0]["soft_issues"], ["needs sharper sensory detail"])
        self.assertEqual(history[0]["repair_instruction"], "Revise sensory continuity.")
        self.assertEqual(history[0]["repair_routing"]["stage"], "story_composition")
        self.assertEqual(history[0]["repair_routing"]["rollback"], "story_only")
        self.assertEqual(history[0]["source"], "artifacts/critic.report.json")

    def test_prepare_delivery_writes_repair_request_intent_for_critic_revise(self):
        routing = {"stage": "story_composition", "rollback": "story_only", "risk": "low"}
        self._write_story_and_critic(
            decision="revise",
            repair_instruction="Rewrite the stop point.",
            repair_routing=routing,
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        expected_routing = {
            "stage": "story_composition",
            "target_agents": ["story"],
            "rollback": "story_only",
            "can_auto_repair": True,
            "risk": "low",
        }
        intent = self._assert_single_repair_request_intent(expected_routing)
        self.assertEqual(intent["payload"]["repair_instruction"], "Rewrite the stop point.")
        history = _read_jsonl(self.run_dir / "repair_history.jsonl")
        self.assertEqual(history[0]["fingerprint"], intent["payload"]["repair_fingerprint"])

    def test_prepare_delivery_writes_repair_request_intent_for_critic_block(self):
        routing = {
            "stage": "gm_loop",
            "target_agents": ["gm"],
            "rollback": "round_progression",
            "can_auto_repair": True,
            "risk": "high",
        }
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_story_and_critic(
            decision="block",
            repair_instruction="Rerun the GM loop.",
            repair_routing=routing,
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        intent = self._assert_single_repair_request_intent(routing)
        self.assertEqual(intent["payload"]["repair_instruction"], "Rerun the GM loop.")

    def test_prepare_delivery_does_not_duplicate_pending_repair_request_intent(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )
        routing = {
            "stage": "story_composition",
            "target_agents": ["story"],
            "rollback": "story_only",
            "can_auto_repair": True,
            "risk": "low",
        }
        self._write_story_and_critic(decision="revise", repair_routing=routing)

        first = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        second = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self._assert_single_repair_request_intent(routing)

    def test_prepare_delivery_limited_mode_blocks_progression_repair_route(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "limited"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_story_and_critic(
            decision="revise",
            repair_routing={
                "stage": "gm_loop",
                "target_agents": ["gm"],
                "rollback": "round_progression",
                "can_auto_repair": True,
                "risk": "medium",
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "self_repair_mode_blocks_route")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("critic_retry_count", 0), 0)
        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "self_repair_mode_blocks_route")
        self.assertEqual(blocked_intents[0]["result"]["outputs"]["delivery_reason"], "self_repair_mode_blocks_route")
        self.assertEqual(blocked_intents[0]["result"]["outputs"]["critic_decision"], "revise")

    def test_prepare_delivery_full_mode_blocks_system_code_without_source_switch(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full", "allowSourceCodeSelfRepair": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        routing = {
            "stage": "system_code",
            "target_agents": ["system"],
            "rollback": "none",
            "can_auto_repair": True,
            "risk": "medium",
        }
        self._write_story_and_critic(
            decision="revise",
            repair_routing=routing,
            repair_instruction="Investigate the reusable dispatcher defect.",
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "blocked")
        self.assertEqual(result["reason"], "source_code_self_repair_not_authorized")
        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "source_code_self_repair_not_authorized")
        self.assertEqual(blocked_intents[0]["result"]["outputs"]["repair_routing"], routing)
        self.assertTrue(blocked_intents[0]["result"]["outputs"]["requires_source_repair_authorization"])

    def test_prepare_delivery_full_mode_with_source_switch_records_system_code_repair_request(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full", "allowSourceCodeSelfRepair": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        routing = {
            "stage": "system_code",
            "target_agents": ["system"],
            "rollback": "none",
            "can_auto_repair": True,
            "risk": "medium",
        }
        self._write_story_and_critic(
            decision="revise",
            repair_routing=routing,
            repair_instruction="Diagnose and fix the reusable dispatcher defect.",
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "critic_revise")
        self._assert_single_repair_request_intent(routing)

    def test_prepare_delivery_full_mode_recreates_pending_after_limited_blocked_intent(self):
        settings_path = self.styles_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"selfRepairMode": "limited"}, ensure_ascii=False),
            encoding="utf-8",
        )
        routing = {
            "stage": "gm_loop",
            "target_agents": ["gm"],
            "rollback": "round_progression",
            "can_auto_repair": True,
            "risk": "medium",
        }
        self._write_story_and_critic(decision="revise", repair_routing=routing)

        blocked_result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(blocked_result["ok"])
        self.assertEqual(blocked_result["action"], "blocked")
        self.assertEqual(blocked_result["reason"], "self_repair_mode_blocks_route")
        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)

        settings_path.write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )

        retry_result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(retry_result["ok"])
        self.assertEqual(retry_result["action"], "retry")
        self.assertEqual(retry_result["reason"], "critic_revise")
        pending_intents = self._repair_request_intents("pending")
        self.assertEqual(len(pending_intents), 1)
        self.assertEqual(pending_intents[0]["payload"]["repair_routing"], routing)
        messages = _read_jsonl(self.run_dir / "messages.jsonl")
        pending_message_ids = {
            intent["source_message_id"]
            for intent in pending_intents
        }
        pending_messages = [
            message
            for message in messages
            if message.get("id") in pending_message_ids and message.get("type") == "repair_request"
        ]
        self.assertEqual(len(pending_messages), 1)
        self.assertEqual(pending_messages[0]["payload"]["repair_routing"], routing)

    def test_prepare_delivery_full_mode_allows_progression_repair_route(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_story_and_critic(
            decision="block",
            repair_routing={
                "stage": "gm_loop",
                "target_agents": ["gm"],
                "rollback": "round_progression",
                "can_auto_repair": True,
                "risk": "high",
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "critic_block")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["critic_retry_count"], 1)

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
        self.assertEqual(queue[0]["source"], str((self.run_dir / "artifacts" / "critic.report.json").resolve()))

    def test_prepare_delivery_returns_terminal_block_after_retry_limit(self):
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )
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
        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "critic_retry_limit")
        self.assertEqual(blocked_intents[0]["result"]["outputs"]["delivery_reason"], "critic_retry_limit")
        self.assertEqual(blocked_intents[0]["result"]["outputs"]["critic_decision"], "block")

    def test_prepare_delivery_fails_fast_when_repair_request_message_rejected(self):
        self._write_story_and_critic(decision="revise")
        original_append_message = self.agent_outputs.agent_messages.append_message

        def reject_message(run_dir, message):
            return {"ok": False, "reason": "schema_rejected", "error": "bad message"}

        self.agent_outputs.agent_messages.append_message = reject_message
        try:
            with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "repair_request message"):
                self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        finally:
            self.agent_outputs.agent_messages.append_message = original_append_message

        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "repair_request_message_failed")

    def test_prepare_delivery_blocks_repair_intent_when_repair_message_append_raises(self):
        self._write_story_and_critic(decision="revise")
        original_append_message = self.agent_outputs.agent_messages.append_message

        def raise_append_error(run_dir, message):
            raise OSError("message log unavailable")

        self.agent_outputs.agent_messages.append_message = raise_append_error
        try:
            with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "repair_request message append failed"):
                self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        finally:
            self.agent_outputs.agent_messages.append_message = original_append_message

        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "repair_request_message_failed")
        self.assertIn("message log unavailable", blocked_intents[0]["result"]["outputs"]["error"])

    def test_prepare_delivery_fails_fast_when_repair_request_message_has_no_id(self):
        self._write_story_and_critic(decision="revise")
        original_append_message = self.agent_outputs.agent_messages.append_message

        def message_without_id(run_dir, message):
            return {"ok": True, "message": {"type": "repair_request"}}

        self.agent_outputs.agent_messages.append_message = message_without_id
        try:
            with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "source message id"):
                self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        finally:
            self.agent_outputs.agent_messages.append_message = original_append_message

        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "repair_request_message_failed")

    def test_prepare_delivery_does_not_append_repair_message_when_repair_intent_creation_fails(self):
        self._write_story_and_critic(decision="revise")
        original_create_intent = self.agent_outputs.agent_intents.create_intent

        def reject_intent(run_dir, payload):
            raise self.agent_outputs.agent_intents.AgentIntentError("intent store failed")

        self.agent_outputs.agent_intents.create_intent = reject_intent
        try:
            with self.assertRaisesRegex(
                self.agent_outputs.agent_intents.AgentIntentError,
                "intent store failed",
            ):
                self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        finally:
            self.agent_outputs.agent_intents.create_intent = original_create_intent

        messages_path = self.run_dir / "messages.jsonl"
        messages = _read_jsonl(messages_path) if messages_path.exists() else []
        repair_messages = [message for message in messages if message.get("type") == "repair_request"]
        self.assertEqual(repair_messages, [])
        for target in ("story", "gm", "main_agent"):
            inbox_messages = self.agent_outputs.agent_messages.read_inbox(self.run_dir, target)
            inbox_repair_messages = [
                message
                for message in inbox_messages
                if message.get("type") == "repair_request"
            ]
            self.assertEqual(inbox_repair_messages, [])
        self.assertEqual(self._repair_request_intents("pending"), [])
        self.assertEqual(self._repair_request_intents("blocked"), [])

    def test_prepare_delivery_blocks_repair_intent_when_source_message_attach_fails(self):
        self._write_story_and_critic(decision="revise")
        original_attach = self.agent_outputs.agent_intents.attach_source_message

        def reject_attach(run_dir, intent_id, source_message_id):
            return {"ok": False, "reason": "intent_store_failed"}

        self.agent_outputs.agent_intents.attach_source_message = reject_attach
        try:
            with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "repair_request source message attach failed"):
                self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        finally:
            self.agent_outputs.agent_intents.attach_source_message = original_attach

        self.assertEqual(self._repair_request_intents("pending"), [])
        blocked_intents = self._repair_request_intents("blocked")
        self.assertEqual(len(blocked_intents), 1)
        self.assertEqual(blocked_intents[0]["result"]["reason"], "repair_request_link_failed")
        self.assertNotIn("source_message_id", blocked_intents[0])
        messages = _read_jsonl(self.run_dir / "messages.jsonl")
        repair_messages = [message for message in messages if message.get("type") == "repair_request"]
        self.assertEqual(len(repair_messages), 1)

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
        (self.styles_dir / "settings.json").write_text(
            json.dumps({"selfRepairMode": "full"}, ensure_ascii=False),
            encoding="utf-8",
        )
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

    def test_prepare_delivery_blocks_when_postprocess_missing_after_critic_pass(self):
        self._write_story_and_critic(decision="pass", include_postprocess=False)

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "postprocess_missing")
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_blocks_when_postprocess_core_invalid(self):
        self._write_story_and_critic(
            decision="pass",
            postprocess_payload={
                "schema_version": 1,
                "core": {
                    "summary": "",
                    "current_goal": "",
                    "options": [],
                },
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "retry")
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertIn("core.summary is required", result["detail"]["errors"])
        self.assertIn("core.current_goal is required", result["detail"]["errors"])
        self.assertFalse((self.styles_dir / "response.txt").exists())

    def test_prepare_delivery_validates_critical_action_postprocess_options(self):
        self._add_player_critical_action()
        self._write_story_and_critic(
            decision="pass",
            postprocess_payload={
                "schema_version": 1,
                "core": {
                    "summary": "The sealed door resists.",
                    "current_goal": "Confirm the risky door action.",
                    "options": ["Look around instead."],
                },
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertIn("missing fixed option for critical action", "\n".join(result["detail"]["errors"]))

    def test_prepare_delivery_fills_default_postprocess_option_when_noncritical_options_empty(self):
        self._write_story_and_critic(
            decision="pass",
            postprocess_payload={
                "schema_version": 1,
                "core": {
                    "summary": "The morning scene settles into a strange calm.",
                    "current_goal": "Find out what the mark means.",
                    "options": [],
                },
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        options = result["postprocess"]["core"]["options"]
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["source"], "postprocess_fallback")
        self.assertFalse(options[0]["requires_confirmation"])

    def test_prepare_delivery_success_includes_and_exports_normalized_postprocess(self):
        self._add_player_critical_action()
        self._write_story_and_critic(
            decision="pass",
            postprocess_payload={
                "schema_version": 1,
                "core": {
                    "summary": "The sealed door yields a breath of cold air.",
                    "current_goal": "Decide whether to commit to opening the sealed door.",
                    "options": [
                        {
                            "label": "Confirm: I open the archive door.",
                            "source": "player_agent_critical_action",
                            "requires_confirmation": True,
                        },
                        "Step back and listen.",
                    ],
                    "state_patch": {
                        "quest": "Resolve the sealed door",
                        "actions": ["Confirm the risky action"],
                        "unsupported": "drop me",
                    },
                },
                "ui_extensions": {"status_panels": {"risk": "critical"}},
                "ui_extension_status": {"status": "ok", "issues": []},
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["postprocess"]["core"]["summary"], "The sealed door yields a breath of cold air.")
        self.assertEqual(result["postprocess"]["core"]["options"][1]["label"], "Step back and listen.")
        self.assertNotIn("unsupported", result["postprocess"]["core"]["state_patch"])
        root_postprocess = json.loads((self.run_dir / "postprocess.output.json").read_text(encoding="utf-8"))
        self.assertEqual(root_postprocess, result["postprocess"])

    def test_prepare_delivery_pass_exports_root_files_only_after_pass(self):
        self.agent_outputs.build_story_input(self.run_dir)
        _write_root_json(
            self.run_dir / "story.input.json",
            {"source": "stale root story input"},
        )
        self._write_story_and_critic(decision="revise")

        retry = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertFalse(retry["ok"])
        self.assertEqual(
            json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8")),
            {"source": "stale root story input"},
        )

        self._write_story_and_critic(decision="pass")
        passed = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(passed["ok"])
        root_story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        artifact_story_input = json.loads((self.run_dir / "artifacts" / "story.input.json").read_text(encoding="utf-8"))
        self.assertEqual(root_story_input, artifact_story_input)
        self.assertTrue((self.run_dir / "story.output.json").exists())
        self.assertTrue((self.run_dir / "critic.report.json").exists())

    def test_prepare_delivery_reads_story_and_critic_from_artifacts_directory(self):
        self._write_story_and_critic(decision="pass")
        artifacts_dir = self.run_dir / "artifacts"

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["story_output"]["content"],
            (self.styles_dir / "response.txt").read_text(encoding="utf-8"),
        )

    def test_prepare_delivery_ignores_conflicting_root_story_and_critic(self):
        self._write_story_and_critic(decision="pass")
        artifacts_dir = self.run_dir / "artifacts"
        self.assertTrue((artifacts_dir / "story.output.json").exists())
        self.assertTrue((artifacts_dir / "critic.report.json").exists())
        _write_root_json(
            self.run_dir / "story.output.json",
            {
                "content": "<content>Wrong root story.</content>",
                "character_dialogues": [],
                "metadata": {"round_id": "round-000001"},
            },
        )
        _write_root_json(
            self.run_dir / "critic.report.json",
            {
                "decision": "block",
                "hard_failures": ["root conflict"],
                "soft_issues": [],
                "repair_instruction": "Do not use root critic.",
                "system_iteration_suggestion": "",
            },
        )

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertIn("Ada lifted the lamp", (self.styles_dir / "response.txt").read_text(encoding="utf-8"))

    def test_mark_delivered_updates_manifest_stage(self):
        self._write_story_and_critic(decision="pass")
        self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        result = self.agent_outputs.mark_delivered(self.card)

        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(manifest["stage"], "delivered")
        self.assertIn("delivered", [item["stage"] for item in manifest["status"]])

    def test_prepare_delivery_is_noop_after_manifest_delivered(self):
        self._write_story_and_critic(decision="pass")
        self.agent_outputs.prepare_delivery(self.card, self.styles_dir)
        self.agent_outputs.mark_delivered(self.card)
        (self.styles_dir / "response.txt").write_text("already delivered", encoding="utf-8")

        result = self.agent_outputs.prepare_delivery(self.card, self.styles_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "already_delivered")
        self.assertEqual((self.styles_dir / "response.txt").read_text(encoding="utf-8"), "already delivered")


if __name__ == "__main__":
    unittest.main()
