import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


class AgentTurnLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_run = load_module("agent_run")
        self.agent_turn_loop = load_module("agent_turn_loop")
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "I ask SuLi about the pendant.",
                "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            },
            "recent_chat": [{"summary": "Morning classroom.", "ai": "GM-only foreshadowing."}],
            "gm_only_hidden_settings": [{"fact": "Pendant is dangerous."}],
            "character_contexts": {
                "characters": [
                    {"name": "SuLi", "memory": ["I know old rituals."], "goals": ["Avoid attention."]},
                ],
            },
        })

    def tearDown(self):
        self.tmp.cleanup()

    def register_characters(self, *names):
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["character_contexts"] = {"characters": [{"name": name} for name in names]}
        self.agent_run.write_json(self.run_dir / "input.json", payload)

    def test_loop_routes_dialogue_to_target_character_and_stops_at_decision(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count = len([key for key, _packet in calls if key == "gm"])
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The classroom noise thins."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You decide to ask SuLi quietly.",
                            "reason": "Player intent.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player must decide whether to reveal the pendant.",
                        "options": ["show it", "hide it"],
                    },
                    "stop_reason": "player_decision",
                }
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{
                        "type": "dialogue",
                        "target": "character:SuLi",
                        "content": "Do you know what this pendant is?",
                    }],
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "dialogue", "target": "player", "content": "Where did you get that?"}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=5)

        actor_order = [key for key, _packet in calls if key != "gm"]
        self.assertEqual(actor_order[:2], ["player", "character:SuLi"])
        self.assertIn("player", actor_order)
        self.assertIn("character:SuLi", actor_order)
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertEqual(result["gm_steps"], 2)
        self.assertEqual(result["called_actors"][:2], ["player", "character:SuLi"])
        self.assertEqual(result["decision_point"]["options"], ["show it", "hide it"])

        player_packet = calls[1][1]
        suli_packet = calls[2][1]
        serialized_packets = json.dumps([player_packet, suli_packet], ensure_ascii=False)
        self.assertNotIn("user_instruction_channel", serialized_packets)
        self.assertNotIn("pendant burns identity", serialized_packets)
        self.assertNotIn("GM-only foreshadowing", serialized_packets)
        self.assertIn("Do you know what this pendant is?", suli_packet["gm_prompt"])
        self.assertEqual(suli_packet["actor_id"], "character:SuLi")

        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        event_types = [event["type"] for event in trace["events"]]
        contents = [event["content"] for event in trace["events"]]
        self.assertIn("dialogue_transfer", event_types)
        self.assertIn("Do you know what this pendant is?", contents)
        self.assertIn("Where did you get that?", contents)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(gm_outputs["agent"], "gm_loop")
        self.assertEqual(len(gm_outputs["outputs"]), 2)
        self.assertIn("player", actor_outputs)
        self.assertIn("character:SuLi", actor_outputs)

    def test_actor_call_prompt_redacts_hidden_phrase_without_marker_words(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "I ask about the pendant.",
                "user_instruction_channel": "Hidden truth: the pendant burns identity.",
            },
            "user_instruction_channel": "Never reveal that the moon is painted glass.",
            "gm_only_hidden_settings": [{"fact": "The teacher is an illusion."}],
            "hidden_facts": {"pendant": "The pendant burns identity."},
            "world_truth": "The moon is painted glass.",
            "gm_only_recent_chat": [{"content": "The clock remembers blood."}],
            "recent_chat": [{"ai": "GM-only foreshadowing says the hallway eats names."}],
            "character_contexts": {"characters": [{"name": "SuLi"}]},
        })
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": (
                            "You sense the pendant burns identity. "
                            "The hallway eats names. "
                            "The clock remembers blood. "
                            "Ask SuLi what she sees."
                        ),
                        "reason": "The moon is painted glass, so prompt carefully.",
                        "metadata": {"note": "The teacher is an illusion."},
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            self.assertEqual(agent_key, "player")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "I keep my voice low."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        serialized = json.dumps(actor_packets, ensure_ascii=False).lower()
        self.assertNotIn("the pendant burns identity", serialized)
        self.assertNotIn("hallway eats names", serialized)
        self.assertNotIn("clock remembers blood", serialized)
        self.assertIn("ask suli what she sees", serialized)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        persisted = json.dumps(gm_outputs, ensure_ascii=False).lower()
        self.assertNotIn("the pendant burns identity", persisted)
        self.assertNotIn("hallway eats names", persisted)
        self.assertNotIn("clock remembers blood", persisted)
        self.assertNotIn("moon is painted glass", persisted)
        self.assertNotIn("teacher is an illusion", persisted)
        self.assertIn("[redacted]", persisted)

    def test_actor_call_prompt_redacts_short_cjk_hidden_phrase(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "我看向门后。",
                "user_instruction_channel": "门后是梦境",
            },
            "hidden_facts": ["门后是梦境"],
            "character_contexts": {"characters": [{"name": "SuLi"}]},
        })
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "你知道门后是梦境，但只能告诉玩家门后有光。",
                        "reason": "Prompt contains short CJK hidden material.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            self.assertEqual(agent_key, "player")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "我停在门前。"}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        serialized = json.dumps(actor_packets, ensure_ascii=False)
        self.assertNotIn("门后是梦境", serialized)
        self.assertIn("门后有光", serialized)

    def test_actor_call_prompt_redacts_spaced_and_punctuated_cjk_hidden_phrase(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "我看向门后。",
                "user_instruction_channel": "门后是梦境",
            },
            "hidden_facts": ["门后是梦境"],
            "character_contexts": {"characters": [{"name": "SuLi"}]},
        })
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": (
                            "你知道门后 是 梦境。"
                            "也知道门后，是梦境。"
                            "最后确认门后 是 梦境。"
                            "只告诉玩家门后有光。"
                        ),
                        "reason": "Prompt contains fuzzy CJK hidden material.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            self.assertEqual(agent_key, "player")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "我停在门前。"}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        serialized = json.dumps(actor_packets, ensure_ascii=False)
        self.assertNotIn("门后 是 梦境", serialized)
        self.assertNotIn("门后，是梦境", serialized)
        self.assertNotIn("门后是梦境", serialized)
        self.assertIn("门后有光", serialized)

    def test_actor_call_prompt_extracts_cjk_hidden_fact_from_instruction_sentence(self):
        hidden_sources = [
            "隐藏设定：门后是梦境，不要提前透露。",
            "门后是梦境，不要提前透露给玩家。",
            "隐藏事实是门后是梦境，不要提前透露。",
            "隐藏事实为门后是梦境，不要提前透露。",
            "隐藏设定为门后是梦境，不要提前透露。",
            "世界真相是门后是梦境，不要提前透露。",
        ]
        for hidden_source in hidden_sources:
            with self.subTest(hidden_source=hidden_source):
                self.agent_run.write_json(self.run_dir / "input.json", {
                    "routed_input": {
                        "role_channel": "我看向走廊。",
                        "user_instruction_channel": hidden_source,
                    },
                    "hidden_facts": [hidden_source],
                    "character_contexts": {"characters": [{"name": "SuLi"}]},
                })
                actor_packets = []

                def dispatch(agent_key, packet):
                    if agent_key == "gm":
                        return {
                            "agent": "gm",
                            "scene_beats": [],
                            "events": [],
                            "actor_calls": [{
                                "call_id": "call-player-1",
                                "actor_id": "player",
                                "prompt": "GM知道门后 是 梦境，但只能告诉玩家光线变亮。",
                                "reason": "Prompt contains a fact embedded in a Chinese instruction.",
                            }],
                            "parallel_groups": [],
                            "world_state_delta": [],
                            "decision_point": None,
                            "stop_reason": "word_target",
                        }
                    self.assertEqual(agent_key, "player")
                    actor_packets.append(json_copy(packet))
                    return {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [{"type": "action", "target": "", "content": "我停下。"}],
                        "stop_reason": "continue",
                    }

                self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

                serialized = json.dumps(actor_packets, ensure_ascii=False)
                self.assertNotIn("门后", serialized)
                self.assertNotIn("梦境", serialized)
                self.assertIn("光线变亮", serialized)

    def test_perception_requests_are_sent_to_next_gm_step_once(self):
        gm_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                gm_packets.append(json_copy(packet))
                step = len(gm_packets)
                if step == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You inspect the pendant.",
                            "reason": "Player asks to perceive.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "continue" if step == 2 else "complete",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{
                    "type": "perceive_request",
                    "target": "pendant",
                    "content": "I check whether the pendant is warm.",
                }],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual(gm_packets[0]["pending_perception_requests"], [])
        self.assertEqual(len(gm_packets[1]["pending_perception_requests"]), 1)
        self.assertEqual(len(gm_packets[1]["world_state"]["pending_perception_requests"]), 1)
        self.assertEqual(
            gm_packets[1]["pending_perception_requests"][0]["content"],
            "I check whether the pendant is warm.",
        )
        self.assertEqual(gm_packets[2]["pending_perception_requests"], [])
        self.assertEqual(gm_packets[2]["world_state"]["pending_perception_requests"], [])

    def test_world_state_delta_is_visible_to_later_gm_steps(self):
        gm_packets = []
        delta = {"scope": "classroom", "fact": "The back door is locked."}

        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            gm_packets.append(json_copy(packet))
            if len(gm_packets) == 1:
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The lock clicks."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [delta],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            return {
                "agent": "gm",
                "scene_beats": [],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual(gm_packets[0]["world_state"].get("world_state_delta", []), [])
        self.assertIn(delta, gm_packets[1]["world_state"]["world_state_delta"])

    def test_dialogue_to_unregistered_character_is_not_routed_to_subagent(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You call toward someone not in the registry.",
                        "reason": "Player speaks to an unregistered target.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{
                    "type": "dialogue",
                    "target": "character:NotRegistered",
                    "content": "Are you there?",
                }],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player"])
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(sorted(actor_outputs), ["player"])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        event_types = [event["type"] for event in trace["events"]]
        self.assertIn("dialogue", event_types)
        self.assertNotIn("dialogue_transfer", event_types)

    def test_direct_gm_actor_call_to_unregistered_character_is_not_dispatched(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-NotRegistered-1",
                            "actor_id": "character:NotRegistered",
                            "prompt": "This character is not in character_contexts.",
                            "reason": "Unknown direct actor must be skipped.",
                        },
                        {
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You hear a registered voice.",
                            "reason": "Player is always registered.",
                        },
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You answer the player.",
                            "reason": "Registered direct character.",
                        },
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            if agent_key == "character:NotRegistered":
                return {
                    "agent": "character",
                    "agent_id": "character:NotRegistered",
                    "character_name": "NotRegistered",
                    "events": [{"type": "action", "target": "", "content": "I should not run."}],
                    "stop_reason": "continue",
                }
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "action", "target": "", "content": "I listen."}],
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "", "content": "I stay close."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(
            [agent_key for agent_key, _packet in calls],
            ["gm", "player", "character:SuLi"],
        )
        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        persisted_actor_calls = gm_outputs["outputs"][0]["actor_calls"]
        self.assertEqual(
            [call["actor_id"] for call in persisted_actor_calls],
            ["player", "character:SuLi"],
        )
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(sorted(actor_outputs), ["character:SuLi", "player"])
        self.agent_run.write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "awaiting_agent_outputs",
                "expected_outputs": {
                    "gm": "gm.output.json",
                    "actors": "actor.outputs.json",
                    "story": "story.output.json",
                    "critic": "critic.report.json",
                },
            },
        )

        story_input = load_module("agent_outputs").build_story_input(self.run_dir)

        self.assertEqual(
            [call["actor_id"] for call in story_input["loop_outputs"]["gm"]["outputs"][0]["actor_calls"]],
            ["player", "character:SuLi"],
        )

    def test_generated_transfer_source_call_id_is_ascii_safe_for_non_ascii_actor(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I ask Su Li for help.", "user_instruction_channel": ""},
            "character_contexts": {"characters": [{"name": "苏黎"}]},
        })

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You ask Su Li for help.",
                        "reason": "Player asks a registered non-ASCII character.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "dialogue", "target": "character:苏黎", "content": "Can you help?"}],
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "character:苏黎")
            return {
                "agent": "character",
                "agent_id": "character:苏黎",
                "character_name": "苏黎",
                "events": [{"type": "action", "target": "", "content": "I step beside you."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        summary = load_module("agent_interactions").summarize_for_story_input(self.run_dir)
        source_ids = [
            event["source_call_id"]
            for event in summary["visible_events"]
            if event["content"] == "I step beside you."
        ]
        self.assertEqual(len(source_ids), 1)
        self.assertRegex(source_ids[0], r"^call-character-[A-Za-z][A-Za-z0-9_]*-[0-9]+$")

    def test_same_actor_can_be_called_multiple_times_from_direct_and_perception_continuation(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count = len([key for key, _packet in calls if key == "gm"])
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi studies the pendant."}],
                        "events": [],
                        "actor_calls": [
                            {
                                "call_id": "call-character-SuLi-1",
                                "actor_id": "character:SuLi",
                                "prompt": "You see the pendant for the first time.",
                                "reason": "Visible pendant.",
                            },
                            {
                                "call_id": "call-character-SuLi-2",
                                "actor_id": "character:SuLi",
                                "prompt": "You notice its surface is warming.",
                                "reason": "Continued observation.",
                            },
                        ],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [{
                        "type": "perception_feedback",
                        "target": "character:SuLi",
                        "content": "The pendant gives off a faint heat.",
                    }],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:SuLi")
            actor_call_count = len([key for key, _packet in calls if key == "character:SuLi"])
            if actor_call_count == 1:
                return {
                    "agent": "character",
                    "agent_id": "character:SuLi",
                    "character_name": "SuLi",
                    "events": [{
                        "type": "perceive_request",
                        "target": "pendant",
                        "content": "I look for heat or old ritual marks.",
                    }],
                    "stop_reason": "continue",
                }
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "pendant", "content": "I pull my hand back."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=4)

        self.assertEqual(result["stop_reason"], "complete")
        self.assertEqual(result["called_actors"], ["character:SuLi", "character:SuLi"])
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(len(actor_outputs["character:SuLi"]), 2)
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        event_types = [event["type"] for event in trace["events"]]
        self.assertIn("perceive_request", event_types)
        self.assertIn("perception_feedback", event_types)

    def test_max_steps_bounds_non_stopping_gm_loop(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The bell keeps ringing."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertEqual(result["gm_steps"], 3)
        self.assertEqual(len(calls), 3)

    def test_max_steps_does_not_truncate_direct_gm_actor_fanout(self):
        calls = []
        actor_ids = [f"character:A{index}" for index in range(1, 6)]
        self.register_characters(*[actor_id.split(":", 1)[1] for actor_id in actor_ids])

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Five students react at once."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": f"call-character-A{index}-1",
                            "actor_id": actor_id,
                            "prompt": f"You hear your name called, A{index}.",
                            "reason": "GM requested direct fanout.",
                        }
                        for index, actor_id in enumerate(actor_ids, start=1)
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            self.assertIn(agent_key, actor_ids)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} looks up."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        actor_order = [agent_key for agent_key, _packet in calls if agent_key != "gm"]
        self.assertEqual(actor_order, actor_ids)
        self.assertEqual(result["called_actors"], actor_ids)
        self.assertEqual(result["gm_steps"], 1)
        self.assertEqual(result["stop_reason"], "max_steps")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(sorted(actor_outputs), actor_ids)
        for actor_id in actor_ids:
            self.assertEqual(len(actor_outputs[actor_id]), 1)

    def test_complete_stop_reason_does_not_skip_direct_gm_actor_calls(self):
        calls = []
        actor_ids = [f"character:A{index}" for index in range(1, 6)]
        self.register_characters(*[actor_id.split(":", 1)[1] for actor_id in actor_ids])

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The scene resolves around five witnesses."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": f"call-character-A{index}-1",
                            "actor_id": actor_id,
                            "prompt": f"You give your final reaction, A{index}.",
                            "reason": "Terminal step direct fanout.",
                        }
                        for index, actor_id in enumerate(actor_ids, start=1)
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertIn(agent_key, actor_ids)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} nods once."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        actor_order = [agent_key for agent_key, _packet in calls if agent_key != "gm"]
        self.assertEqual(actor_order, actor_ids)
        self.assertEqual(result["called_actors"], actor_ids)
        self.assertEqual(result["gm_steps"], 1)
        self.assertEqual(result["stop_reason"], "complete")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(sorted(actor_outputs), actor_ids)
        for actor_id in actor_ids:
            self.assertEqual(len(actor_outputs[actor_id]), 1)

    def test_word_target_stop_reason_stops_after_one_gm_step(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The chapter reaches its target length."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "word_target",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual(result["stop_reason"], "word_target")
        self.assertEqual(result["gm_steps"], 1)
        self.assertEqual(result["called_actors"], [])
        self.assertEqual(len(calls), 1)

    def test_word_target_stop_reason_drains_direct_actor_calls_first(self):
        calls = []
        actor_ids = ["character:Ada", "character:Bea"]
        self.register_characters("Ada", "Bea")

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The chapter closes after two reactions."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You give one final visible reaction.",
                            "reason": "Terminal word target fanout.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You answer with one final gesture.",
                            "reason": "Terminal word target fanout.",
                        },
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "word_target",
                }
            self.assertIn(agent_key, actor_ids)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} reacts once."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "character:Ada", "character:Bea"])
        self.assertEqual(result["called_actors"], actor_ids)
        self.assertEqual(result["stop_reason"], "word_target")
        self.assertEqual(result["gm_steps"], 1)
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(sorted(actor_outputs), actor_ids)
        self.assertEqual(len(actor_outputs["character:Ada"]), 1)
        self.assertEqual(len(actor_outputs["character:Bea"]), 1)

    def test_decision_point_is_marked_after_direct_gm_actor_calls(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi watches the pendant."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-SuLi-1",
                        "actor_id": "character:SuLi",
                        "prompt": "You see the player hesitate with the pendant.",
                        "reason": "SuLi can react before the player's choice.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player must choose whether to show the pendant.",
                        "options": ["show", "hide"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "", "content": "I lean closer to inspect the pendant."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "character:SuLi"])
        self.assertEqual(result["called_actors"], ["character:SuLi"])
        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertEqual(result["decision_point"]["options"], ["show", "hide"])
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(len(actor_outputs["character:SuLi"]), 1)

    def test_invalid_gm_output_raises_clear_loop_error(self):
        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            return {"agent": "gm", "scene_beats": []}

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "invalid gm output"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

    def test_invalid_actor_output_raises_clear_loop_error(self):
        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You look up.",
                        "reason": "Prompt player.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            return {"agent": "player", "agent_id": "player", "action": "legacy action"}

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "invalid actor output"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)


if __name__ == "__main__":
    unittest.main()
