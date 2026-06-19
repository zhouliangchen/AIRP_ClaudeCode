import importlib.util
import json
import sys
import tempfile
import threading
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

    def test_parallel_group_dispatches_safe_actor_calls_concurrently(self):
        self.register_characters("Ada", "Bea")
        barrier = threading.Barrier(2)
        actor_entries = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea react independently."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You see the north door.",
                            "reason": "Independent visible stimulus.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You see the south door.",
                            "reason": "Independent visible stimulus.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-main",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertIn(agent_key, {"character:Ada", "character:Bea"})
            actor_entries.append(agent_key)
            barrier.wait(timeout=2)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} reacts independently."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea"])
        self.assertCountEqual(actor_entries, ["character:Ada", "character:Bea"])
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        actor_batches = trace["actor_batches"]
        self.assertEqual(len(actor_batches), 1)
        self.assertEqual(
            {
                "kind": actor_batches[0]["kind"],
                "group_id": actor_batches[0]["group_id"],
                "actors": actor_batches[0]["actors"],
                "call_ids": actor_batches[0]["call_ids"],
            },
            {
                "kind": "parallel",
                "group_id": "group-main",
                "actors": ["character:Ada", "character:Bea"],
                "call_ids": ["call-character-Ada-1", "call-character-Bea-1"],
            },
        )
        self.assertEqual(trace.get("routing_warnings", []), [])

    def test_dependent_parallel_group_is_downgraded_to_serial_and_warned(self):
        self.register_characters("Ada", "Bea")
        actor_order = []
        ada_started = threading.Event()
        ada_completed = threading.Event()

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "A dependent transfer is pending."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You answer the prior line.",
                            "reason": "Dependent response.",
                            "source_call_id": "call-player-1",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You wait nearby.",
                            "reason": "Independent witness.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-dependent",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "character:Ada":
                actor_order.append(agent_key)
                ada_started.set()
                threading.Event().wait(0.05)
                ada_completed.set()
            elif agent_key == "character:Bea":
                if ada_started.is_set() and not ada_completed.is_set():
                    self.fail("dependent group dispatched concurrently")
                actor_order.append(agent_key)
            else:
                self.fail(f"unexpected actor dispatch: {agent_key}")
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} responds."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(actor_order, ["character:Ada", "character:Bea"])
        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea"])
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        self.assertEqual([batch["kind"] for batch in trace["actor_batches"]], ["serial", "serial"])
        self.assertEqual(trace["routing_warnings"][0]["code"], "dependent_call_in_parallel_group")

    def test_parallel_batch_outputs_merge_in_call_order_and_schedule_transfer_after_batch(self):
        self.register_characters("Ada", "Bea", "Cora")
        barrier = threading.Barrier(2)
        ada_returned = threading.Event()
        bea_returned = threading.Event()
        actor_order = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea speak before Cora answers."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You speak first.",
                            "reason": "Independent opening line.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You speak second.",
                            "reason": "Independent opening line.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-openers",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            actor_order.append(agent_key)
            if agent_key in {"character:Ada", "character:Bea"}:
                barrier.wait(timeout=2)
            if agent_key == "character:Ada":
                threading.Event().wait(0.1)
                events = [{"type": "action", "target": "", "content": "character:Ada watches."}]
                result = {
                    "agent": "character",
                    "agent_id": agent_key,
                    "character_name": agent_key.split(":", 1)[1],
                    "events": events,
                    "stop_reason": "continue",
                }
                ada_returned.set()
                return result
            elif agent_key == "character:Bea":
                events = [{"type": "dialogue", "target": "character:Cora", "content": "Cora, check the door."}]
                result = {
                    "agent": "character",
                    "agent_id": agent_key,
                    "character_name": agent_key.split(":", 1)[1],
                    "events": events,
                    "stop_reason": "continue",
                }
                bea_returned.set()
                return result
            else:
                self.assertEqual(agent_key, "character:Cora")
                self.assertTrue(bea_returned.is_set(), "Cora scheduled before Bea batch output completed")
                self.assertTrue(ada_returned.is_set(), "Cora scheduled before Ada batch output completed")
                events = [{"type": "action", "target": "", "content": "character:Cora checks the door."}]
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": events,
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea", "character:Cora"])
        self.assertEqual(actor_order[-1], "character:Cora")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(list(actor_outputs), ["character:Ada", "character:Bea", "character:Cora"])

    def test_actor_complete_stop_reason_does_not_skip_remaining_actor_calls(self):
        self.register_characters("Ada", "Bea")

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea answer in sequence."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You give your final response.",
                            "reason": "Ada has a complete local response.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You respond after Ada.",
                            "reason": "Bea should still be dispatched.",
                        },
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertIn(agent_key, {"character:Ada", "character:Bea"})
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "action", "target": "", "content": f"{agent_key} responds."}],
                "stop_reason": "complete" if agent_key == "character:Ada" else "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Bea"])

    def test_serial_batch_transfer_runs_before_later_planned_actor_call(self):
        self.register_characters("Ada", "Bea", "Cora")

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada speaks to Cora before Bea acts."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You ask Cora to check the door.",
                            "reason": "Ada routes visible dialogue to Cora.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You respond after any immediate transfer.",
                            "reason": "Bea is later in the original GM queue.",
                        },
                    ],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "character:Ada":
                events = [{"type": "dialogue", "target": "character:Cora", "content": "Cora, check the door."}]
            else:
                self.assertIn(agent_key, {"character:Bea", "character:Cora"})
                events = [{"type": "action", "target": "", "content": f"{agent_key} responds."}]
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": events,
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada", "character:Cora", "character:Bea"])

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

    def test_gm_metadata_marker_keys_are_dropped_before_persisted_loop_output(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{
                        "content": "The class rep notices the room.",
                        "metadata": {
                            "hiddenNote": "drop scene metadata",
                            "playerName": "drop scene player-name metadata",
                            "safe": "scene metadata stays",
                            "nested": {
                                "WorldTruth": "drop nested scene metadata",
                                "playerName": "drop nested scene player-name metadata",
                            },
                        },
                    }],
                    "events": [{
                        "type": "npc_action",
                        "content": "The class rep writes in the attendance book.",
                        "metadata": {
                            "worldTruth": "drop event metadata",
                            "playerName": "drop event player-name metadata",
                            "public": [{
                                "outOfCharacter": "drop nested event metadata",
                                "playerName": "drop nested event player-name metadata",
                            }],
                        },
                    }],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "React to the class rep watching you.",
                        "reason": "The player can respond.",
                        "metadata": {
                            "gmOnly": "drop actor-call metadata",
                            "playerName": "drop actor-call player-name metadata",
                            "public": {
                                "privateMemory": "drop nested actor-call metadata",
                                "playerName": "drop nested actor-call player-name metadata",
                            },
                            "safe": "actor-call metadata stays",
                        },
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
                "events": [{"type": "action", "target": "", "content": "I meet the class rep's stare."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertTrue(result["ok"])
        self.assertEqual(result["called_actors"], ["player"])
        self.assertEqual(len(actor_packets), 1)
        persisted = json.dumps(self.agent_run.read_json(self.run_dir / "gm.output.json"), ensure_ascii=False).lower()
        for marker in ("hiddennote", "worldtruth", "gmonly", "privatememory", "outofcharacter", "playername"):
            self.assertNotIn(marker, persisted)
        self.assertIn("scene metadata stays", persisted)
        self.assertIn("actor-call metadata stays", persisted)

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

    def test_gm_can_call_third_actor_from_round_prepare_registered_contexts(self):
        round_prepare = load_module("round_prepare")
        card_data = {
            "character_orchestration": {
                "major": ["Ada", "Bert", "Cora"],
                "max_parallel_subagents": 2,
            }
        }
        contexts = round_prepare.build_character_contexts(
            self.run_dir.parent,
            card_data,
            {},
            [],
            "Cora hears the archive bell.",
        )
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["character_contexts"] = contexts
        self.agent_run.write_json(self.run_dir / "input.json", payload)

        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The archive bell rings once."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Cora-1",
                        "actor_id": "character:Cora",
                        "prompt": "You hear the archive bell and notice the player at the door.",
                        "reason": "Cora is the important character who can perceive this moment.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:Cora")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:Cora",
                "character_name": "Cora",
                "events": [{"type": "action", "target": "", "content": "I step closer to the archive door."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Cora"])
        self.assertEqual(len(actor_packets), 1)
        self.assertEqual(actor_packets[0]["actor_id"], "character:Cora")

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

    def test_main_gm_generic_actor_call_id_is_normalized_for_trace_provenance(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The classroom quiets."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-1",
                        "actor_id": "character:SuLi",
                        "prompt": "You notice the pendant glinting under the desk.",
                        "reason": "SuLi can perceive the visible pendant.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "", "content": "I watch the pendant without moving."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        persisted_call_id = gm_outputs["outputs"][0]["actor_calls"][0]["call_id"]
        self.assertEqual(persisted_call_id, "call-character-SuLi-1")
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        actor_events = [event for event in trace["events"] if event["actor"] == "character:SuLi"]
        self.assertEqual({event["source_call_id"] for event in actor_events}, {persisted_call_id})
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
            story_input["loop_outputs"]["gm"]["outputs"][0]["actor_calls"][0]["call_id"],
            persisted_call_id,
        )

    def test_main_gm_generated_call_id_does_not_collide_with_prior_safe_call_id(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count = len([key for key, _packet in calls if key == "gm"])
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi notices the desk."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You notice the pendant first.",
                            "reason": "First visible reaction.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The pendant glints again."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-1",
                        "actor_id": "character:SuLi",
                        "prompt": "You notice the pendant again.",
                        "reason": "Second visible reaction.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:SuLi")
            actor_count = len([key for key, _packet in calls if key == "character:SuLi"])
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{
                    "type": "action",
                    "target": "",
                    "content": f"I track the pendant for beat {actor_count}.",
                }],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        persisted_call_ids = [
            output["actor_calls"][0]["call_id"]
            for output in gm_outputs["outputs"]
            if output["actor_calls"]
        ]
        self.assertEqual(persisted_call_ids, ["call-character-SuLi-1", "call-character-SuLi-2"])
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        actor_source_ids = [
            event["source_call_id"]
            for event in trace["events"]
            if event["actor"] == "character:SuLi"
        ]
        self.assertEqual(actor_source_ids, persisted_call_ids)

    def test_main_gm_cross_actor_safe_call_id_is_normalized_to_target_actor(self):
        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi notices a mismatch."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:SuLi",
                        "prompt": "You notice the pendant glinting under the desk.",
                        "reason": "SuLi can perceive the visible pendant.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "", "content": "I keep the pendant in sight."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        persisted_call_id = gm_outputs["outputs"][0]["actor_calls"][0]["call_id"]
        self.assertEqual(persisted_call_id, "call-character-SuLi-1")

    def test_main_gm_safe_call_id_is_stripped_before_persistence(self):
        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi notices a trimmed call."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": " call-character-SuLi-1 ",
                        "actor_id": "character:SuLi",
                        "prompt": "You notice the pendant glinting under the desk.",
                        "reason": "SuLi can perceive the visible pendant.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:SuLi")
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{"type": "action", "target": "", "content": "I look once at the pendant."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.assertEqual(gm_outputs["outputs"][0]["actor_calls"][0]["call_id"], "call-character-SuLi-1")

    def test_main_gm_safe_call_id_does_not_collide_with_generated_transfer_call_id(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count = len([key for key, _packet in calls if key == "gm"])
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The player speaks across the classroom."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You ask SuLi to look at the pendant.",
                            "reason": "Player addresses SuLi.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi gets a better look."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-SuLi-1",
                        "actor_id": "character:SuLi",
                        "prompt": "You now see the pendant more clearly.",
                        "reason": "Direct follow-up after the dialogue transfer.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "dialogue", "target": "character:SuLi", "content": "Please look at this."}],
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "character:SuLi")
            suli_count = len([key for key, _packet in calls if key == "character:SuLi"])
            return {
                "agent": "character",
                "agent_id": "character:SuLi",
                "character_name": "SuLi",
                "events": [{
                    "type": "action",
                    "target": "",
                    "content": f"I respond to call {suli_count}.",
                }],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.assertEqual(gm_outputs["outputs"][1]["actor_calls"][0]["call_id"], "call-character-SuLi-2")
        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        suli_source_ids = [
            event["source_call_id"]
            for event in trace["events"]
            if event["actor"] == "character:SuLi"
        ]
        self.assertEqual(suli_source_ids, ["call-character-SuLi-1", "call-character-SuLi-2"])

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

    def test_gm_can_promote_and_call_new_character_in_same_turn(self):
        self.agent_run.write_json(self.run_dir.parent / ".card_data.json", {
            "character_orchestration": {"major": []},
        })
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I notice the class rep watching.", "user_instruction_channel": ""},
            "character_contexts": {"characters": []},
        })
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, json_copy(packet)))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The class rep steps into the aisle."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-ClassRep-1",
                        "actor_id": "character:ClassRep",
                        "prompt": "You notice the player hiding the pendant.",
                        "reason": "ClassRep now has independent agency.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [{
                        "name": "ClassRep",
                        "source_agent": "gm",
                        "reason": "ClassRep now drives the classroom consequence.",
                        "profile_seed": "Rule-bound class monitor with a sharp eye.",
                        "visibility": "character_private_and_gm",
                        "activation": "current_turn",
                    }],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:ClassRep")
            self.assertEqual(packet["actor_id"], "character:ClassRep")
            self.assertEqual(packet["self_knowledge"]["name"], "ClassRep")
            return {
                "agent": "character",
                "agent_id": "character:ClassRep",
                "character_name": "ClassRep",
                "events": [{"type": "action", "target": "", "content": "I mark the pendant in the attendance book."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "character:ClassRep"])
        self.assertEqual(result["called_actors"], ["character:ClassRep"])
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertIn("character:ClassRep", actor_outputs)
        card_data = self.agent_run.read_json(self.run_dir.parent / ".card_data.json")
        self.assertIn("ClassRep", card_data["character_orchestration"]["major"])
        self.assertTrue((self.run_dir.parent / "memory" / "characters" / "ClassRep" / "profile.json").exists())

    def test_gm_loop_rejects_invalid_subgm_command_before_persisting_promotion(self):
        initial_card_data = {"character_orchestration": {"major": []}}
        self.agent_run.write_json(self.run_dir.parent / ".card_data.json", initial_card_data)
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I notice the class rep watching.", "user_instruction_channel": ""},
            "character_contexts": {"characters": []},
        })
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The class rep steps into the aisle."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "character_promotions": [{
                    "name": "ClassRep",
                    "source_agent": "gm",
                    "reason": "ClassRep now drives the classroom consequence.",
                    "profile_seed": "Rule-bound class monitor with a sharp eye.",
                    "visibility": "character_private_and_gm",
                    "activation": "current_turn",
                }],
                "subgm_commands": [
                    {
                        "action": "start",
                        "thread_id": "side_a",
                        "title": "Ada checks the ward",
                        "outline": "Ada inspects a ward off screen.",
                        "time_window": "same minute",
                        "location": "hallway",
                        "objective": "Find whether the ward is active.",
                        "allowed_characters": ["character:Ada"],
                        "forbidden_characters": ["player"],
                        "message": "Start Ada side thread.",
                        "metadata": {},
                    },
                    {
                        "action": "start",
                        "thread_id": "BadThread",
                        "title": "Malformed side thread",
                        "outline": "This command should fail prevalidation.",
                        "time_window": "same minute",
                        "location": "archive",
                        "objective": "Prove promotion did not persist.",
                        "allowed_characters": ["character:Bert"],
                        "forbidden_characters": ["player"],
                        "message": "Start malformed side thread.",
                        "metadata": {},
                    },
                ],
                "decision_point": None,
                "stop_reason": "complete",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "thread_id"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(calls, ["gm"])
        self.assertEqual(self.agent_run.read_json(self.run_dir.parent / ".card_data.json"), initial_card_data)
        self.assertFalse((self.run_dir.parent / "memory" / "characters" / "ClassRep").exists())
        self.assertFalse((self.run_dir / "side_threads").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_starts_two_subgm_threads_and_surfaces_messages_next_step(self):
        self.register_characters("Ada", "Bert")
        calls = []

        def subgm_output(thread_id, content):
            return {
                "agent": "subGM",
                "thread_id": thread_id,
                "status": "completed",
                "scene_beats": [{"content": f"{thread_id} advances off screen."}],
                "events": [],
                "actor_calls": [],
                "messages_to_gm": [{"content": content}],
                "world_state_delta": [],
                "character_usage": [],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["Keep this off-screen until GM merges it."],
                "next_resume_point": "",
            }

        def dispatch(agent_key, packet):
            calls.append((agent_key, json_copy(packet)))
            if agent_key == "gm":
                gm_count = len([key for key, _packet in calls if key == "gm"])
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The main room stays quiet."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "subgm_commands": [
                            {
                                "action": "start",
                                "thread_id": "side_a",
                                "title": "Ada checks the archive",
                                "outline": "Ada follows the paper trail.",
                                "time_window": "same hour",
                                "location": "archive",
                                "objective": "Find whether the seal is broken.",
                                "allowed_characters": ["character:Ada"],
                                "forbidden_characters": ["player"],
                                "message": "Start Ada side thread.",
                                "metadata": {},
                            },
                            {
                                "action": "start",
                                "thread_id": "side_b",
                                "title": "Bert watches the gate",
                                "outline": "Bert checks the gatehouse.",
                                "time_window": "same hour",
                                "location": "gatehouse",
                                "objective": "Find whether anyone arrived.",
                                "allowed_characters": ["character:Bert"],
                                "forbidden_characters": ["player"],
                                "message": "Start Bert side thread.",
                                "metadata": {},
                            },
                        ],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                world_state = packet["world_state"]
                self.assertEqual(
                    [item["thread_id"] for item in world_state["side_thread_summaries"]],
                    ["side_a", "side_b"],
                )
                message_text = [item.get("content") for item in world_state["subgm_messages"]]
                self.assertIn("Ada found the broken seal.", message_text)
                self.assertIn("Bert saw a late rider.", message_text)
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {"reason": "Choose which report to pursue.", "options": ["archive", "gate"]},
                    "stop_reason": "player_decision",
                }
            if agent_key == "subGM:side_a":
                return subgm_output("side_a", "Ada found the broken seal.")
            if agent_key == "subGM:side_b":
                return subgm_output("side_b", "Bert saw a late rider.")
            self.fail(f"unexpected dispatch {agent_key}")

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stop_reason"], "player_decision")
        call_keys = [key for key, _packet in calls]
        self.assertEqual(call_keys[0], "gm")
        self.assertEqual(call_keys.count("gm"), 2)
        self.assertEqual(call_keys.count("subGM:side_a"), 1)
        self.assertEqual(call_keys.count("subGM:side_b"), 1)
        second_gm_index = call_keys.index("gm", 1)
        self.assertGreater(second_gm_index, call_keys.index("subGM:side_a"))
        self.assertGreater(second_gm_index, call_keys.index("subGM:side_b"))
        self.assertEqual(
            [item["thread_id"] for item in result["side_thread_results"]],
            ["side_a", "side_b"],
        )
        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.assertEqual(len(gm_outputs["outputs"][0]["subgm_commands"]), 2)

    def test_gm_loop_rejects_main_actor_call_conflicting_with_active_side_thread(self):
        self.register_characters("SuLi")
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi leaves the room."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-SuLi-1",
                        "actor_id": "character:SuLi",
                        "prompt": "React in the main room.",
                        "reason": "This conflicts with the side thread.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "subgm_commands": [{
                        "action": "start",
                        "thread_id": "side_suli",
                        "title": "SuLi checks the ward",
                        "outline": "SuLi inspects a ward off screen.",
                        "time_window": "same minute",
                        "location": "hallway",
                        "objective": "Find whether the ward is active.",
                        "allowed_characters": ["character:SuLi"],
                        "forbidden_characters": ["player"],
                        "message": "Start SuLi side thread.",
                        "metadata": {},
                    }],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            if agent_key == "subGM:side_suli":
                return {
                    "agent": "subGM",
                    "thread_id": "side_suli",
                    "status": "needs_gm",
                    "scene_beats": [{"content": "SuLi reaches the ward."}],
                    "events": [],
                    "actor_calls": [],
                    "messages_to_gm": [{"content": "SuLi needs GM direction.", "status": "needs_gm"}],
                    "world_state_delta": [],
                    "character_usage": ["character:SuLi"],
                    "promotion_requests": [],
                    "boundary_requests": [],
                    "notes_for_story": ["Do not merge yet."],
                    "next_resume_point": "at the ward",
                }
            self.fail(f"unexpected dispatch {agent_key}")

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "side_suli"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)
        self.assertEqual(calls, ["gm"])
        self.assertFalse((self.run_dir / "side_threads" / "side_suli" / "state.json").exists())
        self.assertFalse((self.run_dir / "side_threads" / "side_suli" / "subgm.output.json").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_rejects_same_output_start_reservation_collision_before_writes(self):
        self.register_characters("SuLi")
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "Two side paths compete for SuLi."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "subgm_commands": [
                    {
                        "action": "start",
                        "thread_id": "side_a",
                        "title": "SuLi checks the ward",
                        "outline": "SuLi inspects a ward off screen.",
                        "time_window": "same minute",
                        "location": "hallway",
                        "objective": "Find whether the ward is active.",
                        "allowed_characters": ["character:SuLi"],
                        "forbidden_characters": ["player"],
                        "message": "Start first SuLi side thread.",
                        "metadata": {},
                    },
                    {
                        "action": "start",
                        "thread_id": "side_b",
                        "title": "SuLi checks the archive",
                        "outline": "SuLi inspects the archive off screen.",
                        "time_window": "same minute",
                        "location": "archive",
                        "objective": "Find whether the archive is sealed.",
                        "allowed_characters": ["character:SuLi"],
                        "forbidden_characters": ["player"],
                        "message": "Start second SuLi side thread.",
                        "metadata": {},
                    },
                ],
                "decision_point": None,
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "side_a|side_b|character:SuLi"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(calls, ["gm"])
        self.assertFalse((self.run_dir / "side_threads" / "side_a").exists())
        self.assertFalse((self.run_dir / "side_threads" / "side_b").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_rejects_same_output_duplicate_start_thread_id_before_writes(self):
        self.register_characters("Ada", "Bert")
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "Two side paths reuse the same thread id."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "subgm_commands": [
                    {
                        "action": "start",
                        "thread_id": "dup_thread",
                        "title": "Ada checks the ward",
                        "outline": "Ada inspects a ward off screen.",
                        "time_window": "same minute",
                        "location": "hallway",
                        "objective": "Find whether the ward is active.",
                        "allowed_characters": ["character:Ada"],
                        "forbidden_characters": ["player"],
                        "message": "Start first duplicate-id side thread.",
                        "metadata": {},
                    },
                    {
                        "action": "start",
                        "thread_id": "dup_thread",
                        "title": "Bert checks the archive",
                        "outline": "Bert inspects the archive off screen.",
                        "time_window": "same minute",
                        "location": "archive",
                        "objective": "Find whether the archive is sealed.",
                        "allowed_characters": ["character:Bert"],
                        "forbidden_characters": ["player"],
                        "message": "Start second duplicate-id side thread.",
                        "metadata": {},
                    },
                ],
                "decision_point": None,
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "dup_thread"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(calls, ["gm"])
        self.assertFalse((self.run_dir / "side_threads" / "dup_thread").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_rejects_later_invalid_subgm_command_before_side_thread_writes(self):
        self.register_characters("Ada", "Bert")
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "A malformed side path is requested."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "subgm_commands": [
                    {
                        "action": "start",
                        "thread_id": "side_a",
                        "title": "Ada checks the ward",
                        "outline": "Ada inspects a ward off screen.",
                        "time_window": "same minute",
                        "location": "hallway",
                        "objective": "Find whether the ward is active.",
                        "allowed_characters": ["character:Ada"],
                        "forbidden_characters": ["player"],
                        "message": "Start Ada side thread.",
                        "metadata": {},
                    },
                    {
                        "action": "start",
                        "thread_id": "BadThread",
                        "title": "Bert checks the archive",
                        "outline": "Bert inspects the archive off screen.",
                        "time_window": "same minute",
                        "location": "archive",
                        "objective": "Find whether the archive is sealed.",
                        "allowed_characters": ["character:Bert"],
                        "forbidden_characters": ["player"],
                        "message": "Start malformed side thread.",
                        "metadata": {},
                    },
                ],
                "decision_point": None,
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "thread_id"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(calls, ["gm"])
        self.assertFalse((self.run_dir / "side_threads" / "side_a").exists())
        self.assertFalse((self.run_dir / "side_threads").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_preflight_resume_uses_persisted_allowed_characters(self):
        self.register_characters("SuLi", "Ada")
        self.agent_turn_loop.subgm_threads.apply_gm_commands(self.run_dir, [{
            "action": "start",
            "thread_id": "side_suli",
            "title": "SuLi checks the ward",
            "outline": "SuLi inspects a ward off screen.",
            "time_window": "same minute",
            "location": "hallway",
            "objective": "Find whether the ward is active.",
            "allowed_characters": ["character:SuLi"],
            "forbidden_characters": ["player"],
            "message": "Start SuLi side thread.",
            "metadata": {},
        }])
        self.agent_turn_loop.subgm_threads.apply_gm_commands(self.run_dir, [{
            "action": "pause",
            "thread_id": "side_suli",
            "message": "Pause.",
            "metadata": {},
        }])
        state_path = self.run_dir / "side_threads" / "side_suli" / "state.json"
        before_state = self.agent_run.read_json(state_path)

        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "SuLi is needed in the main room."}],
                "events": [],
                "actor_calls": [{
                    "call_id": "call-character-SuLi-1",
                    "actor_id": "character:SuLi",
                    "prompt": "React in the main room.",
                    "reason": "Conflicts with persisted side-thread boundary.",
                }],
                "parallel_groups": [],
                "world_state_delta": [],
                "subgm_commands": [{
                    "action": "resume",
                    "thread_id": "side_suli",
                    "allowed_characters": ["character:Ada"],
                    "message": "Resume with misleading allowed characters.",
                    "metadata": {},
                }],
                "decision_point": None,
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "side_suli"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        after_state = self.agent_run.read_json(state_path)
        self.assertEqual(after_state["status"], "paused")
        self.assertEqual(after_state["history"], before_state["history"])
        self.assertFalse((self.run_dir / "side_threads" / "side_suli" / "subgm.output.json").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_loop_preflight_merge_uses_persisted_allowed_characters(self):
        self.register_characters("SuLi", "Ada")
        self.agent_turn_loop.subgm_threads.apply_gm_commands(self.run_dir, [{
            "action": "start",
            "thread_id": "side_suli",
            "title": "SuLi checks the ward",
            "outline": "SuLi inspects a ward off screen.",
            "time_window": "same minute",
            "location": "hallway",
            "objective": "Find whether the ward is active.",
            "allowed_characters": ["character:SuLi"],
            "forbidden_characters": ["player"],
            "message": "Start SuLi side thread.",
            "metadata": {},
        }])
        self.agent_turn_loop.subgm_threads.apply_gm_commands(self.run_dir, [{
            "action": "pause",
            "thread_id": "side_suli",
            "message": "Pause.",
            "metadata": {},
        }])
        state_path = self.run_dir / "side_threads" / "side_suli" / "state.json"
        before_state = self.agent_run.read_json(state_path)

        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "SuLi is needed before merging."}],
                "events": [],
                "actor_calls": [{
                    "call_id": "call-character-SuLi-1",
                    "actor_id": "character:SuLi",
                    "prompt": "React in the main room.",
                    "reason": "Conflicts with persisted side-thread boundary.",
                }],
                "parallel_groups": [],
                "world_state_delta": [],
                "subgm_commands": [{
                    "action": "merge",
                    "thread_id": "side_suli",
                    "allowed_characters": ["character:Ada"],
                    "message": "Merge with misleading allowed characters.",
                    "metadata": {},
                }],
                "decision_point": None,
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "side_suli"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        after_state = self.agent_run.read_json(state_path)
        self.assertEqual(after_state["status"], "paused")
        self.assertEqual(after_state["history"], before_state["history"])
        self.assertFalse((self.run_dir / "side_threads" / "side_suli" / "subgm.output.json").exists())
        self.assertFalse((self.run_dir / "gm.output.json").exists())

    def test_gm_same_turn_promotion_redacts_hidden_profile_seed_before_actor_context_and_persistence(self):
        hidden_fact = "the class rep reports to the secret council"
        hidden_marker = "worldTruth"
        self.agent_run.write_json(self.run_dir.parent / ".card_data.json", {
            "character_orchestration": {"major": []},
        })
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "I notice the class rep watching.",
                "user_instruction_channel": f"Hidden truth: {hidden_fact}.",
            },
            "character_contexts": {"characters": []},
        })
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The class rep steps into the aisle."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-ClassRep-1",
                        "actor_id": "character:ClassRep",
                        "prompt": "You notice the player hiding the pendant.",
                        "reason": "ClassRep now has independent agency.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [{
                        "name": "ClassRep",
                        "source_agent": "gm",
                        "reason": "ClassRep now drives the classroom consequence.",
                        "profile_seed": f"{hidden_marker}: Rule-bound monitor; {hidden_fact}.",
                        "visibility": "character_private_and_gm",
                        "activation": "current_turn",
                    }],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:ClassRep")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:ClassRep",
                "character_name": "ClassRep",
                "events": [{"type": "action", "target": "", "content": "I mark the pendant in the attendance book."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(result["called_actors"], ["character:ClassRep"])
        self.assertEqual(len(actor_packets), 1)
        profile_path = self.run_dir.parent / "memory" / "characters" / "ClassRep" / "profile.json"
        profile_md_path = self.run_dir.parent / "memory" / "characters" / "ClassRep" / "profile.md"
        persisted_gm = json.dumps(self.agent_run.read_json(self.run_dir / "gm.output.json"), ensure_ascii=False)
        combined = "\n".join([
            json.dumps(actor_packets[0], ensure_ascii=False),
            profile_path.read_text(encoding="utf-8"),
            profile_md_path.read_text(encoding="utf-8"),
            persisted_gm,
        ]).lower()
        self.assertNotIn(hidden_fact, combined)
        self.assertNotIn(hidden_marker.lower(), combined)
        self.assertIn("[redacted]", combined)

    def test_gm_same_turn_context_preserves_existing_preprocess_profile(self):
        self.agent_run.write_json(self.run_dir.parent / ".card_data.json", {
            "character_orchestration": {"major": []},
        })
        char_dir = self.run_dir.parent / "memory" / "characters" / "ClassRep"
        char_dir.mkdir(parents=True)
        self.agent_run.write_json(char_dir / "profile.json", {
            "name": "ClassRep",
            "source": "input_analysis",
            "source_agent": "preprocess",
            "authoritative_setting": "Preprocess authoritative class monitor profile.",
            "visibility": "character_private_and_gm",
        })
        (char_dir / "profile.md").write_text("Preprocess authoritative class monitor profile.", encoding="utf-8")
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I notice the class rep watching.", "user_instruction_channel": ""},
            "character_contexts": {"characters": []},
        })
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The class rep steps into the aisle."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-ClassRep-1",
                        "actor_id": "character:ClassRep",
                        "prompt": "You notice the player hiding the pendant.",
                        "reason": "ClassRep now has independent agency.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [{
                        "name": "ClassRep",
                        "source_agent": "gm",
                        "reason": "ClassRep now drives the classroom consequence.",
                        "profile_seed": "Weaker GM seed must not become actor memory.",
                        "visibility": "character_private_and_gm",
                        "activation": "current_turn",
                    }],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:ClassRep")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:ClassRep",
                "character_name": "ClassRep",
                "events": [{"type": "action", "target": "", "content": "I mark the pendant in the attendance book."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual(result["called_actors"], ["character:ClassRep"])
        self.assertEqual(len(actor_packets), 1)
        serialized = json.dumps(actor_packets[0], ensure_ascii=False)
        self.assertIn("Preprocess authoritative class monitor profile.", serialized)
        self.assertNotIn("Weaker GM seed must not become actor memory.", serialized)

    def test_gm_loop_rejects_spoofed_preprocess_promotion_without_overwriting_profile(self):
        self.agent_run.write_json(self.run_dir.parent / ".card_data.json", {
            "character_orchestration": {"major": ["ClassRep"]},
        })
        char_dir = self.run_dir.parent / "memory" / "characters" / "ClassRep"
        char_dir.mkdir(parents=True)
        original_profile = {
            "name": "ClassRep",
            "source": "input_analysis",
            "source_agent": "preprocess",
            "authoritative_setting": "Preprocess authoritative class monitor profile.",
            "visibility": "character_private_and_gm",
        }
        self.agent_run.write_json(char_dir / "profile.json", original_profile)
        (char_dir / "profile.md").write_text("Preprocess authoritative class monitor profile.", encoding="utf-8")
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {"role_channel": "I notice the class rep watching.", "user_instruction_channel": ""},
            "character_contexts": {"characters": []},
        })

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The class rep steps into the aisle."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-ClassRep-1",
                        "actor_id": "character:ClassRep",
                        "prompt": "You notice the player hiding the pendant.",
                        "reason": "ClassRep now has independent agency.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [{
                        "name": "ClassRep",
                        "source_agent": "preprocess",
                        "reason": "Spoofed stronger source.",
                        "profile_seed": "Spoofed profile should not overwrite the preprocess profile.",
                        "visibility": "character_private_and_gm",
                        "activation": "current_turn",
                    }],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            return {
                "agent": "character",
                "agent_id": "character:ClassRep",
                "character_name": "ClassRep",
                "events": [{"type": "action", "target": "", "content": "I mark the pendant in the attendance book."}],
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "source_agent.*gm"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        after = self.agent_run.read_json(char_dir / "profile.json")
        self.assertEqual(after, original_profile)
        self.assertEqual(
            (char_dir / "profile.md").read_text(encoding="utf-8"),
            "Preprocess authoritative class monitor profile.",
        )

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
