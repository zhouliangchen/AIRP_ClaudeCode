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
