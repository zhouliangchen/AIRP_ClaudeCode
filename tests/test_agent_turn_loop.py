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


def visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test GM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


def add_visibility_basis(output):
    if not isinstance(output, dict):
        return output
    for call in output.get("actor_calls", []):
        if isinstance(call, dict) and "visibility_basis" not in call:
            actor_id = str(call.get("actor_id") or "").strip()
            call["visibility_basis"] = visibility_basis(actor_id or "player")
    return output


def actor_stub_reply(agent_key, output):
    if agent_key == "gm" or agent_key == "projection" or str(agent_key).startswith("subGM:"):
        return output
    if not isinstance(output, dict) or "natural_reply" in output:
        return output
    events = output.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict):
                content = str(event.get("content") or "").strip()
                if content:
                    context_version = output.get("context_version")
                    if isinstance(context_version, dict):
                        agent_id = str(output.get("agent_id") or agent_key)
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
                            "context_version": context_version,
                        }
                        if agent == "character":
                            payload["character_name"] = str(output.get("character_name") or agent_id.split(":", 1)[-1])
                        return payload
                    return content
    return output


def projection_pass(packet):
    return {
        "decision": "pass",
        "target_actor_id": str(packet.get("target_actor_id") or ""),
        "source_call_id": str(packet.get("source_call_id") or ""),
        "final_actor_message": str(packet.get("requested_actor_message") or ""),
        "feedback": "",
    }


def wrap_dispatch_with_visibility(dispatch):
    def wrapped(agent_key, packet):
        if agent_key == "projection":
            if getattr(dispatch, "handles_projection", False):
                return dispatch(agent_key, packet)
            return projection_pass(packet)
        output = dispatch(agent_key, packet)
        if agent_key == "gm" or str(agent_key).startswith("subGM:"):
            add_visibility_basis(output)
        output = actor_stub_reply(agent_key, output)
        return output

    return wrapped


class AgentTurnLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_run = load_module("agent_run")
        self.agent_messages = load_module("agent_messages")
        self.agent_intents = load_module("agent_intents")
        self.agent_turn_loop = load_module("agent_turn_loop")
        run_interactive_loop = self.agent_turn_loop.run_interactive_loop

        def run_with_visibility(run_dir, dispatch, *args, **kwargs):
            return run_interactive_loop(run_dir, wrap_dispatch_with_visibility(dispatch), *args, **kwargs)

        self.agent_turn_loop.run_interactive_loop = run_with_visibility
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

    def register_character_states(self, *characters):
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["character_contexts"] = {"characters": [dict(character) for character in characters]}
        self.agent_run.write_json(self.run_dir / "input.json", payload)

    def test_filter_gm_actor_calls_keeps_registered_player_and_characters(self):
        gm_output = {
            "actor_calls": [
                {"actor_id": "player", "prompt": "Say what you do next."},
                {"actor_id": "character:SuLi", "prompt": "你看见雨蒙攥着吊坠。"},
                {"actor_id": "character:Ghost", "prompt": "Unregistered character."},
            ],
        }

        filtered = self.agent_turn_loop._filter_gm_actor_calls(
            gm_output,
            {"player", "character:SuLi"},
        )

        self.assertEqual(
            filtered["actor_calls"],
            [
                {"actor_id": "player", "prompt": "Say what you do next."},
                {"actor_id": "character:SuLi", "prompt": "你看见雨蒙攥着吊坠。"},
            ],
        )

    def promote_loop_outputs_to_artifacts(self):
        artifacts = self.run_dir / "artifacts"
        self.agent_run.write_json(
            artifacts / "gm.output.json",
            self.agent_run.read_json(self.run_dir / "gm.output.json"),
        )
        self.agent_run.write_json(
            artifacts / "actor.outputs.json",
            self.agent_run.read_json(self.run_dir / "actor.outputs.json"),
        )

    def test_characters_by_actor_id_registers_raw_and_canonical_actor_ids(self):
        payload = {
            "character_contexts": {
                "characters": [
                    {"name": "Ada//Zero", "role": "archivist"},
                ],
            },
        }

        registered = self.agent_turn_loop._characters_by_actor_id(payload)

        self.assertIn("character:Ada//Zero", registered)
        self.assertIn("character:Ada_Zero", registered)
        self.assertNotIn("character:Ada__Zero", registered)
        self.assertEqual(registered["character:Ada_Zero"]["role"], "archivist")

    def test_actor_call_creates_intent_projected_message_and_actor_response_message(self):
        self.register_characters("Ada")

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a bell.",
                        "reason": "Ada can hear it.",
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "The bell is audible nearby.",
                            "target_actor": "character:Ada",
                            "visible_to": ["character:Ada"],
                            "sensory_channels": ["auditory"],
                        },
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player decides what to do next.",
                        "options": ["wait", "answer"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "character:Ada")
            self.assertEqual(packet["actor_id"], "character:Ada")
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{
                    "type": "dialogue",
                    "target": "",
                    "content": "I heard it.",
                    "metadata": {"exact_visible_words": "I heard it."},
                }],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        messages = self.agent_messages.read_messages(self.run_dir)
        self.assertIn("request_actor", [message.get("type") for message in messages])
        self.assertIn("projected_message", [message.get("type") for message in messages])
        self.assertIn("actor_response", [message.get("type") for message in messages])
        actor_responses = [message for message in messages if message.get("type") == "actor_response"]
        self.assertEqual(actor_responses[0].get("source_call_id"), "call-character-Ada-1")
        completed_intents = self.agent_intents.list_intents(self.run_dir, "completed")
        self.assertTrue(
            any(intent.get("type") == "request_projection" for intent in completed_intents),
            completed_intents,
        )
        self.assertTrue((self.run_dir / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "actor.outputs.json").exists())
        self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.agent_run.read_json(self.run_dir / "actor.outputs.json")

    def test_actor_natural_language_reply_is_wrapped_for_internal_artifacts(self):
        self.register_characters("Ada")
        reply = "我把门轻轻合上，低声说：先听听外面的脚步。"

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "你听见门外有脚步声。",
                        "reason": "Ada is directly addressed.",
                        "visibility_basis": visibility_basis("character:Ada"),
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:Ada")
            return reply

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Ada"])
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        output = actor_outputs["character:Ada"][0]
        self.assertEqual(output["natural_reply"], reply)
        self.assertEqual(output["events"], [{"type": "reply", "target": "gm", "content": reply, "metadata": {}}])
        self.assertNotIn("stop_reason", output)
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        actor_events = [event for event in trace["events"] if event.get("actor") == "character:Ada"]
        self.assertEqual(actor_events[0]["type"], "reply")
        self.assertEqual(actor_events[0]["content"], reply)

    def test_main_actor_call_runs_projection_before_actor_dispatch(self):
        self.register_characters("Ada")
        actor_dir = self.tmp.name and Path(self.tmp.name) / "characters" / "Ada"
        actor_dir.mkdir(parents=True)
        (actor_dir / "profile.md").write_text(
            "I am Ada, and I only trust messages delivered through the archive door.",
            encoding="utf-8",
        )
        projection_packets = []
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a knock.",
                        "reason": "Ada hears the door.",
                        "visibility_basis": visibility_basis("character:Ada"),
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "projection":
                projection_packets.append(packet)
                return {
                    "decision": "edited",
                    "target_actor_id": "character:Ada",
                    "source_call_id": "call-character-Ada-1",
                    "final_actor_message": "You hear two careful knocks at the archive door.",
                    "feedback": "",
                }
            if agent_key == "character:Ada":
                actor_packets.append(packet)
                return "I listen before answering."
            raise AssertionError(agent_key)

        dispatch.handles_projection = True
        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        self.assertEqual(len(projection_packets), 1)
        self.assertIn("only trust messages", projection_packets[0]["actor_context"])
        self.assertNotIn("self_knowledge", json.dumps(projection_packets[0], ensure_ascii=False))
        self.assertEqual(actor_packets[0]["gm_prompt"], "You hear two careful knocks at the archive door.")
        inbox = self.agent_messages.read_inbox(self.run_dir, "character:Ada")
        projected_payload = [item for item in inbox if item.get("type") == "projected_message"][0]["payload"]
        self.assertEqual(projected_payload["natural_message"], "You hear two careful knocks at the archive door.")
        short_term = (actor_dir / "short_term_memories.md").read_text(encoding="utf-8")
        self.assertIn(
            "记忆的回声：You hear two careful knocks at the archive door.\n\n"
            "我：I listen before answering.\n",
            short_term,
        )
        self.assertNotIn("有人对我说：", short_term)
        self.assertNotIn("我回应：", short_term)

    def test_initial_role_action_enters_player_short_term_without_narrative_guidance(self):
        card = self.run_dir.parent
        action_text = "我扶住门框，低声问她是不是也听见了铃声。"
        guidance_text = "接下来我希望剧情推进到走廊灯忽然熄灭。"
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": action_text + "\n" + guidance_text,
                "role_action_channel": action_text,
                "narrative_guidance_channel": guidance_text,
                "user_instruction_channel": "",
            },
            "recent_chat": [],
            "character_contexts": {"characters": []},
        })

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                self.assertEqual(packet["role_action_channel"], action_text)
                self.assertEqual(packet["narrative_guidance_channel"], guidance_text)
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "走廊灯闪了一下。", "metadata": {}}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "character_promotions": [],
                    "subgm_commands": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            raise AssertionError(agent_key)

        result = self.agent_turn_loop.run_interactive_loop(
            self.run_dir,
            dispatch,
            max_steps=1,
            card_folder=card,
        )

        self.assertEqual(result["stop_reason"], "complete")
        short_term = (card / "characters" / "player" / "short_term_memories.md").read_text(encoding="utf-8")
        self.assertIn("我：" + action_text, short_term)
        self.assertNotIn(guidance_text, short_term)

    def test_actor_call_does_not_record_gm_prompt_when_actor_output_fails(self):
        self.register_characters("Ada")
        actor_dir = self.run_dir.parent / "characters" / "Ada"

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a bell.",
                        "reason": "Ada can hear the bell.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "projection":
                return {
                    "decision": "pass",
                    "target_actor_id": "character:Ada",
                    "source_call_id": "call-character-Ada-1",
                    "final_actor_message": "You hear a bell.",
                    "feedback": "",
                }
            if agent_key == "character:Ada":
                return {"agent": "character", "agent_id": "character:Ada", "events": []}
            raise AssertionError(agent_key)

        dispatch.handles_projection = True
        with self.assertRaises(self.agent_turn_loop.AgentTurnLoopError):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        short_term = actor_dir / "short_term_memories.md"
        self.assertFalse(short_term.exists())

    def test_actor_dispatch_wraps_runtime_write_exceptions_as_loop_errors(self):
        self.register_characters("Ada")
        original_create_intent = self.agent_turn_loop.agent_actor_runtime.agent_intents.create_intent
        root_cause = RuntimeError("intent store unavailable")

        def raise_create_intent(*_args, **_kwargs):
            raise root_cause

        self.agent_turn_loop.agent_actor_runtime.agent_intents.create_intent = raise_create_intent

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a bell.",
                        "reason": "Ada can hear it.",
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "The bell is audible nearby.",
                            "target_actor": "character:Ada",
                            "visible_to": ["character:Ada"],
                            "sensory_channels": ["auditory"],
                        },
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "wait_for_gm", "target": "", "content": "I wait."}],
                "stop_reason": "continue",
            }

        try:
            with self.assertRaisesRegex(
                self.agent_turn_loop.AgentTurnLoopError,
                "record request_actor intent failed: intent store unavailable",
            ) as raised:
                self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)
        finally:
            self.agent_turn_loop.agent_actor_runtime.agent_intents.create_intent = original_create_intent

        self.assertIs(raised.exception.__cause__, root_cause)

    def test_parallel_actor_calls_create_projected_and_response_messages(self):
        self.register_characters("Ada", "Bea")
        barrier = threading.Barrier(2)

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada and Bea hear separate bells."}],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "You hear the north bell.",
                            "reason": "Ada can hear the north bell.",
                            "visibility_basis": {
                                "mode": "direct",
                                "summary": "The north bell is audible to Ada.",
                                "target_actor": "character:Ada",
                                "visible_to": ["character:Ada"],
                                "sensory_channels": ["auditory"],
                            },
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "You hear the south bell.",
                            "reason": "Bea can hear the south bell.",
                            "visibility_basis": {
                                "mode": "direct",
                                "summary": "The south bell is audible to Bea.",
                                "target_actor": "character:Bea",
                                "visible_to": ["character:Bea"],
                                "sensory_channels": ["auditory"],
                            },
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-bells",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertIn(agent_key, {"character:Ada", "character:Bea"})
            barrier.wait(timeout=2)
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "events": [{"type": "wait_for_gm", "target": "", "content": f"{agent_key} listens."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        messages = self.agent_messages.read_messages(self.run_dir)
        projected = [message for message in messages if message.get("type") == "projected_message"]
        responses = [message for message in messages if message.get("type") == "actor_response"]
        self.assertEqual(len(projected), 2)
        self.assertEqual(len(responses), 2)
        self.assertEqual(len({message["id"] for message in projected}), 2)
        self.assertEqual(len({message["id"] for message in responses}), 2)
        self.assertCountEqual(
            [message.get("source_call_id") for message in projected],
            ["call-character-Ada-1", "call-character-Bea-1"],
        )
        self.assertCountEqual(
            [message.get("source_call_id") for message in responses],
            ["call-character-Ada-1", "call-character-Bea-1"],
        )
        completed_intents = self.agent_intents.list_intents(self.run_dir, "completed")
        self.assertEqual(
            len([intent for intent in completed_intents if intent.get("type") == "request_projection"]),
            2,
        )
        ada_inbox = self.agent_messages.read_inbox(self.run_dir, "character:Ada")
        bea_inbox = self.agent_messages.read_inbox(self.run_dir, "character:Bea")
        self.assertEqual([message.get("type") for message in ada_inbox], ["projected_message"])
        self.assertEqual([message.get("type") for message in bea_inbox], ["projected_message"])
        self.assertTrue((self.run_dir / "gm.output.json").exists())
        self.assertTrue((self.run_dir / "actor.outputs.json").exists())
        self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.agent_run.read_json(self.run_dir / "actor.outputs.json")

    def test_actor_packet_receives_gm_visibility_basis(self):
        self.register_character_states({
            "name": "Ada",
            "location": "classroom",
            "sensory_channels": ["visual"],
        })
        packets = []
        gm_output = {
            "agent": "gm",
            "scene_beats": [{
                "content": "Ada sees the player close his hand.",
                "location": "classroom",
                "visible_to": ["character:Ada"],
                "sensory_channels": ["visual"],
                "visibility_basis": {
                    "mode": "location",
                    "summary": "Ada is in the classroom and can see the player's hand.",
                    "location": "classroom",
                    "visible_to": ["character:Ada"],
                    "sensory_channels": ["visual"],
                },
            }],
            "events": [],
            "actor_calls": [{
                "call_id": "call-character-Ada-1",
                "actor_id": "character:Ada",
                "prompt": "You see the player close his hand around something pink.",
                "reason": "Ada is in the classroom and can see the movement.",
                "visibility_basis": {
                    "mode": "location",
                    "summary": "Ada is in the classroom and can see the player's hand.",
                    "location": "classroom",
                    "visible_to": ["character:Ada"],
                    "sensory_channels": ["visual"],
                    "target_actor": "character:Ada",
                },
            }],
            "parallel_groups": [],
            "world_state_delta": [],
            "decision_point": None,
            "stop_reason": "complete",
        }

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return gm_output
            packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "wait_for_gm", "target": "", "content": "I stay alert."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        self.assertEqual(len(packets), 1)
        packet_basis = packets[0]["gm_visibility_basis"]
        self.assertEqual(packet_basis.get("mode"), "location")
        self.assertEqual(packet_basis.get("summary"), "Ada is in the classroom and can see the player's hand.")
        self.assertEqual(packet_basis.get("location"), "classroom")
        self.assertEqual(packet_basis.get("visible_to"), ["character:Ada"])
        self.assertEqual(packet_basis.get("sensory_channels"), ["visual"])
        self.assertEqual(packet_basis.get("target_actor"), "character:Ada")

    def test_interactive_loop_reports_gm_actor_and_decision_progress(self):
        self.register_character_states({"name": "Ada"})
        input_payload = self.agent_run.read_json(self.run_dir / "input.json")
        input_payload["routed_input"]["role_action_channel"] = "I close my hand around the pendant."
        self.agent_run.write_json(self.run_dir / "input.json", input_payload)
        progress_calls = []
        self.agent_turn_loop.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Ada sees the player close his hand."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You see the player close his hand around something pink.",
                        "reason": "Ada is in the classroom and can see the movement.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player must choose whether to show the pendant.",
                        "options": ["show", "hide"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "character:Ada")
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "wait_for_gm", "target": "", "content": "I stay alert."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual(result["stop_reason"], "player_decision")
        states = [args[0] for args, _kwargs in progress_calls]
        self.assertEqual(
            states,
            [
                "gm_loop.gm_dispatch",
                "gm_loop.actor_batch",
                "gm_loop.actor_dispatch",
                "gm_loop.waiting_player_decision",
                "gm_loop.completed",
            ],
        )
        actor_details = [
            kwargs.get("detail")
            for args, kwargs in progress_calls
            if args and args[0] == "gm_loop.actor_dispatch"
        ]
        self.assertEqual(actor_details, [{"actor": "character:Ada", "actor_call_id": "call-character-Ada-1"}])
        completed_details = [
            kwargs.get("detail")
            for args, kwargs in progress_calls
            if args and args[0] == "gm_loop.completed"
        ]
        self.assertEqual(completed_details, [{"stop_reason": "player_decision"}])

    def test_interactive_loop_ignores_progress_write_failures(self):
        self.agent_turn_loop.write_progress = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("progress down"))

        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The room settles."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stop_reason"], "complete")

    def test_runtime_actor_dispatch_attaches_context_version_and_warns_on_stale_return(self):
        self.register_character_states({"name": "Ada"})
        self.agent_run.write_json(
            self.run_dir / "manifest.json",
            {"round_id": "round-000001", "stage": "awaiting_agent_outputs", "status": []},
        )
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You see the corridor.",
                        "reason": "Ada is present.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:Ada")
            actor_packets.append(json_copy(packet))
            self.assertEqual(packet["context_version"]["algorithm"], "sha256")
            self.assertTrue(packet["context_version"]["hash"].startswith("sha256:"))
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "context_version": {"algorithm": "sha256", "hash": "sha256:stale"},
                "events": [{"type": "wait_for_gm", "target": "", "content": "I wait."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        self.assertEqual(len(actor_packets), 1)
        manifest = self.agent_run.read_json(self.run_dir / "manifest.json")
        warnings = manifest.get("actor_context_warnings", [])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["actor_id"], "character:Ada")
        self.assertEqual(warnings[0]["returned_hash"], "sha256:stale")
        self.assertEqual(warnings[0]["current_hash"], actor_packets[0]["context_version"]["hash"])

    def test_parallel_stale_actor_context_warnings_are_recorded_serially(self):
        self.register_characters("Ada", "Bea")
        self.agent_run.write_json(
            self.run_dir / "manifest.json",
            {"round_id": "round-000001", "stage": "awaiting_agent_outputs", "status": []},
        )
        main_thread_id = threading.get_ident()
        packets = []
        original_record = self.agent_turn_loop.agent_lifecycle.record_stale_actor_context_warning

        def record_on_main_thread_only(run_dir, actor_id, returned_hash, current_hash):
            self.assertEqual(threading.get_ident(), main_thread_id)
            return original_record(run_dir, actor_id, returned_hash, current_hash)

        self.agent_turn_loop.agent_lifecycle.record_stale_actor_context_warning = record_on_main_thread_only

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [
                        {
                            "call_id": "call-character-Ada-1",
                            "actor_id": "character:Ada",
                            "prompt": "Ada sees the corridor.",
                            "reason": "Ada is present.",
                        },
                        {
                            "call_id": "call-character-Bea-1",
                            "actor_id": "character:Bea",
                            "prompt": "Bea sees the window.",
                            "reason": "Bea is present.",
                        },
                    ],
                    "parallel_groups": [{
                        "group_id": "group-stale",
                        "actors": ["character:Ada", "character:Bea"],
                    }],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": agent_key,
                "character_name": agent_key.split(":", 1)[1],
                "context_version": {"algorithm": "sha256", "hash": f"sha256:stale-{agent_key}"},
                "events": [{"type": "wait_for_gm", "target": "", "content": "I wait."}],
                "stop_reason": "continue",
            }

        try:
            result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)
        finally:
            self.agent_turn_loop.agent_lifecycle.record_stale_actor_context_warning = original_record

        self.assertTrue(result["ok"])
        self.assertEqual(len(packets), 2)
        manifest = self.agent_run.read_json(self.run_dir / "manifest.json")
        warnings = manifest.get("actor_context_warnings", [])
        self.assertCountEqual(
            [warning["actor_id"] for warning in warnings],
            ["character:Ada", "character:Bea"],
        )

    def test_actor_packet_visibility_basis_preserves_top_level_call_metadata(self):
        self.register_character_states({
            "name": "Ada",
            "location": "classroom",
            "sensory_channels": ["auditory"],
        })
        packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear the bell over your desk.",
                        "reason": "Ada is in the classroom.",
                        "location": "classroom",
                        "visible_to": ["character:Ada"],
                        "sensory_channels": ["auditory"],
                        "visibility_basis": {
                            "mode": "location",
                            "summary": "Ada is in the classroom and can hear the bell.",
                            "target_actor": "character:Ada",
                        },
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "wait_for_gm", "target": "", "content": "I listen."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertTrue(result["ok"])
        self.assertEqual(len(packets), 1)
        packet_basis = packets[0]["gm_visibility_basis"]
        self.assertEqual(packet_basis.get("mode"), "location")
        self.assertEqual(packet_basis.get("summary"), "Ada is in the classroom and can hear the bell.")
        self.assertEqual(packet_basis.get("target_actor"), "character:Ada")
        self.assertEqual(packet_basis.get("location"), "classroom")
        self.assertEqual(packet_basis.get("visible_to"), ["character:Ada"])
        self.assertEqual(packet_basis.get("sensory_channels"), ["auditory"])

    def test_actor_call_visibility_basis_must_prove_target_actor_before_dispatch(self):
        self.register_characters("Ada", "Eve")
        packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You hear a private instruction meant for Eve.",
                        "reason": "This must not be routed to Ada.",
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "Eve is directly addressed by this prompt.",
                            "target_actor": "character:Eve",
                            "visible_to": ["character:Eve"],
                        },
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "wait_for_gm", "target": "", "content": "I wait."}],
                "stop_reason": "continue",
            }

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, r"visibility_basis.*character:Ada"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(packets, [])

    def test_gm_scene_visibility_metadata_reaches_trace_summary(self):
        self.register_characters("Ada")

        def dispatch(agent_key, packet):
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{
                    "content": "The bell rings over Ada's desk.",
                    "scene_id": "classroom-1",
                    "location": "classroom",
                    "time_window": "current",
                    "visible_to": ["character:Ada"],
                    "sensory_channels": ["auditory"],
                    "source_actor": "gm",
                    "target_actor": "character:Ada",
                    "visibility_basis": {
                        "mode": "location",
                        "summary": "Ada is in the classroom and can hear the bell.",
                        "location": "classroom",
                        "visible_to": ["character:Ada"],
                        "sensory_channels": ["auditory"],
                        "target_actor": "character:Ada",
                    },
                }],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "complete",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)
        summary = load_module("agent_interactions").summarize_for_story_input(self.run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(len(summary["visible_events"]), 1)
        visible_event = summary["visible_events"][0]
        self.assertEqual(visible_event["type"], "scene_beat")
        self.assertEqual(visible_event["content"], "The bell rings over Ada's desk.")
        self.assertEqual(visible_event.get("location"), "classroom")
        self.assertEqual(visible_event.get("visible_to"), ["character:Ada"])
        self.assertEqual(visible_event.get("sensory_channels"), ["auditory"])
        self.assertEqual(
            visible_event.get("visibility_basis", {}).get("summary"),
            "Ada is in the classroom and can hear the bell.",
        )
        self.assertEqual(visible_event.get("source_actor"), "gm")
        self.assertEqual(visible_event.get("target_actor"), "character:Ada")

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
                        "source_call_id": "the/pendant/burns/identity",
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
        self.assertNotIn("the/pendant/burns/identity", persisted)
        self.assertEqual(gm_outputs["outputs"][0]["actor_calls"][0].get("source_call_id"), "[redacted]")
        self.assertIn("[redacted]", persisted)

    def test_actor_packet_redacts_compact_hidden_marker_prompt(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "I ask about the pendant.",
                "user_instruction_channel": "",
            },
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
                        "prompt": "gmonlyroom",
                        "reason": "worldtruthactor",
                        "metadata": {"note": "hiddenfactwitness"},
                        "visibility_basis": {
                            "mode": "direct",
                            "summary": "The player is directly addressed.",
                            "target_actor": "player",
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
                "events": [{"type": "action", "target": "", "content": "I wait."}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        serialized_packets = json.dumps(actor_packets, ensure_ascii=False).lower()
        self.assertNotIn("gmonlyroom", serialized_packets)
        self.assertNotIn("worldtruthactor", serialized_packets)
        self.assertNotIn("hiddenfactwitness", serialized_packets)
        self.assertIn("[redacted]", serialized_packets)

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
        self.promote_loop_outputs_to_artifacts()

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
        self.promote_loop_outputs_to_artifacts()

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

    def test_default_loop_no_longer_stops_at_old_eight_step_limit(self):
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["runtime_settings"] = {"wordCount": 1000}
        self.agent_run.write_json(self.run_dir / "input.json", payload)
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            if len(calls) < 9:
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "Short beat."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "continue",
                }
            return {
                "agent": "gm",
                "scene_beats": [{"content": "Final beat after the old step limit."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "word_target",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch)

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["stop_reason"], "word_target")
        self.assertEqual(result["gm_steps"], 9)
        self.assertEqual(len(calls), 9)
        self.assertEqual(calls[-1][1]["world_state"]["raw_story_progress"]["target"], 1000)
        self.assertGreater(calls[-1][1]["world_state"]["raw_story_progress"]["current"], 0)

    def test_raw_story_text_over_120_percent_word_count_stops_with_word_target(self):
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["runtime_settings"] = {"wordCount": 10}
        self.agent_run.write_json(self.run_dir / "input.json", payload)
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "一二三四五六七八九十十一十二十三"}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": None,
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=20)

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["stop_reason"], "word_target")
        self.assertEqual(result["gm_steps"], 1)
        self.assertEqual(len(calls), 1)

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

    def test_decision_point_is_not_marked_from_same_gm_output_that_calls_player(self):
        calls = []
        gm_count = 0

        def dispatch(agent_key, packet):
            nonlocal gm_count
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count += 1
                if gm_count > 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi keeps the pendant hidden for now."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "max_steps",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "SuLi watches the pendant."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You feel the pendant warming in your palm.",
                        "reason": "The player character must decide the critical action.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player must choose whether to show the pendant.",
                        "options": ["show", "hide"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "I decide whether to show the pendant."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player", "gm"])
        self.assertEqual(calls[1][1].get("card_folder"), str(self.run_dir.parent))
        self.assertNotEqual(result["stop_reason"], "player_decision")
        self.assertEqual(result["stop_reason"], "max_steps")
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(len(actor_outputs["player"]), 1)

    def test_player_actor_call_is_injected_when_initial_gm_omits_player(self):
        calls = []
        input_payload = self.agent_run.read_json(self.run_dir / "input.json")
        input_payload.setdefault("routed_input", {})["player"] = True
        input_payload.setdefault("input_analysis", {})["narrative_directives"] = {
            "expand_synopsis_before_continue": True,
        }
        self.agent_run.write_json(self.run_dir / "input.json", input_payload)

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "雨蒙坐在教室里，左手手背微微发热。"}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "max_steps",
                }
            self.assertEqual(agent_key, "player")
            self.assertIn("雨蒙坐在教室里", packet["gm_prompt"])
            return {
                "agent": "player",
                "agent_id": "player",
                "natural_reply": "我低头看向自己的左手。",
                "events": [{
                    "type": "reply",
                    "target": "gm",
                    "content": "我低头看向自己的左手。",
                    "metadata": {},
                }],
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player"])
        self.assertEqual(result["called_actors"], ["player"])
        self.assertEqual(result["stop_reason"], "max_steps")

    def test_decision_point_is_marked_after_prior_player_actor_response(self):
        calls = []
        gm_count = 0

        def dispatch(agent_key, packet):
            nonlocal gm_count
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count += 1
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi watches the pendant."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You feel the pendant warming in your palm.",
                            "reason": "The player character must decide the critical action.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The pendant light reaches the floor."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "The player's action would reveal the pendant.",
                        "options": ["show", "hide"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "action", "target": "", "content": "I decide whether to show the pendant."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player", "gm"])
        self.assertEqual(result["called_actors"], ["player"])
        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertEqual(result["decision_point"]["options"], ["show", "hide"])

    def test_decision_point_reason_falls_back_to_helper_label(self):
        gm_count = 0

        def dispatch(agent_key, packet):
            nonlocal gm_count
            if agent_key == "gm":
                gm_count += 1
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The sealed door hums."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You feel the seal respond to your hand.",
                            "reason": "The player must respond before GM can judge the action.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The seal is ready to break."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "required_label": "Break the seal now.",
                        "options": ["break", "wait"],
                    },
                    "stop_reason": "player_decision",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "natural_reply": "I press harder against the seal.",
                "events": [{"type": "reply", "target": "gm", "content": "I press harder against the seal."}],
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")

        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertEqual(trace["decision_point"]["reason"], "Break the seal now.")

    def test_decision_point_without_player_decision_stop_does_not_stop_loop(self):
        calls = []
        gm_count = 0

        def dispatch(agent_key, packet):
            nonlocal gm_count
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count += 1
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "SuLi watches the pendant."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You feel the pendant warming in your palm.",
                            "reason": "The player character must decide the critical action.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The pendant light reaches the floor."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": {
                        "reason": "This is only a trace annotation.",
                        "options": ["show", "hide"],
                    },
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "reply", "target": "gm", "content": "I keep the pendant hidden."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player", "gm"])
        self.assertEqual(result["called_actors"], ["player"])
        self.assertEqual(result["stop_reason"], "max_steps")
        self.assertIsNone(result["decision_point"])

    def test_gm_can_call_player_again_after_player_actor_response(self):
        calls = []
        gm_count = 0

        def dispatch(agent_key, packet):
            nonlocal gm_count
            calls.append((agent_key, packet))
            if agent_key == "gm":
                gm_count += 1
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "You wake up in the classroom."}],
                        "events": [],
                        "actor_calls": [{
                            "call_id": "call-player-1",
                            "actor_id": "player",
                            "prompt": "You see your notebook open on the desk.",
                            "reason": "The player should inspect the immediate scene.",
                        }],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The classroom settles back into silence."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-2",
                        "actor_id": "player",
                        "prompt": "Morning study starts in twenty minutes. What do you do?",
                        "reason": "The player has finished observing and must choose the next action.",
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
                    "type": "reply",
                    "target": "gm",
                    "content": "I check whether the notebook has my handwriting.",
                }],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=2)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm", "player", "gm", "player"])
        self.assertEqual(result["called_actors"], ["player", "player"])
        gm_outputs = self.agent_run.read_json(self.run_dir / "gm.output.json")
        self.assertEqual(
            [call["call_id"] for output in gm_outputs["outputs"] for call in output["actor_calls"]],
            ["call-player-1", "call-player-2"],
        )
        actor_outputs = self.agent_run.read_json(self.run_dir / "actor.outputs.json")
        self.assertEqual(len(actor_outputs["player"]), 2)
        responses = [
            message
            for message in self.agent_messages.read_messages(self.run_dir)
            if message.get("type") == "actor_response"
        ]
        self.assertEqual(
            [message["source_call_id"] for message in responses],
            ["call-player-1", "call-player-2"],
        )

    def test_role_action_channel_counts_as_prior_player_participation_for_decision(self):
        input_payload = self.agent_run.read_json(self.run_dir / "input.json")
        input_payload["routed_input"]["role_action_channel"] = "I grip the pendant and step forward."
        self.agent_run.write_json(self.run_dir / "input.json", input_payload)
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The pendant flashes as the player steps forward."}],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": {
                    "reason": "The player's direct action changes the scene.",
                    "options": ["continue", "stop"],
                },
                "stop_reason": "player_decision",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm"])
        self.assertEqual(result["stop_reason"], "player_decision")
        self.assertEqual(result["decision_point"]["reason"], "The player's direct action changes the scene.")

    def test_gm_cannot_stop_for_player_decision_without_actor_work(self):
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [
                    {"content": "The GM describes the player reaching for the pendant."}
                ],
                "events": [],
                "actor_calls": [],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": {
                    "reason": "The player must choose whether to take the pendant.",
                    "options": ["take it", "leave it"],
                },
                "stop_reason": "player_decision",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertNotEqual(result["stop_reason"], "player_decision")
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        self.assertNotEqual(trace.get("status"), "decision_point")
        self.assertEqual([agent_key for agent_key, _packet in calls], ["gm"])

    def test_pre_player_gm_output_preserves_sensory_claims(self):
        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{
                        "content": "雨蒙左手手背有淡粉色痕迹，不痛不痒，头有点轻。"
                    }],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "你看见左手手背有淡粉色痕迹，不疼不痒，头晕。",
                        "reason": "The player should react.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "player")
            return {
                "agent": "player",
                "agent_id": "player",
                "events": [{"type": "reply", "target": "gm", "content": "我盯着手背。"}],
                "stop_reason": "continue",
            }

        self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        trace = json.dumps(
            self.agent_run.read_json(self.run_dir / "interaction.trace.json"),
            ensure_ascii=False,
        )
        gm_output = json.dumps(
            self.agent_run.read_json(self.run_dir / "gm.output.json"),
            ensure_ascii=False,
        )
        for claim in ("不痛不痒", "头有点轻"):
            self.assertIn(claim, trace)
            self.assertIn(claim, gm_output)
        for claim in ("不疼不痒", "头晕"):
            self.assertIn(claim, gm_output)

    def test_sanitized_gm_decision_point_reaches_trace_without_hidden_phrase(self):
        self.agent_run.write_json(self.run_dir / "input.json", {
            "routed_input": {
                "role_channel": "I inspect the signal.",
                "user_instruction_channel": "Hidden truth: moon base archive.",
            },
            "hidden_facts": [{"fact": "moon base archive"}],
            "character_contexts": {"characters": []},
        })

        def dispatch(agent_key, packet):
            if agent_key == "player":
                return {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "action", "target": "", "content": "I keep watching the signal."}],
                    "stop_reason": "continue",
                }
            self.assertEqual(agent_key, "gm")
            return {
                "agent": "gm",
                "scene_beats": [{"content": "The public signal keeps blinking."}],
                "events": [],
                "actor_calls": [{
                    "call_id": "call-player-1",
                    "actor_id": "player",
                    "prompt": "You see the public signal blinking.",
                    "reason": "The player must decide whether to reveal the signal.",
                }],
                "parallel_groups": [],
                "world_state_delta": [],
                "decision_point": {
                    "reason": "Choose whether to reveal moon-base-archive.",
                    "options": ["ask about moon_base_archive", "walk away"],
                },
                "stop_reason": "player_decision",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)
        trace = self.agent_run.read_json(self.run_dir / "interaction.trace.json")
        gm_loop = self.agent_run.read_json(self.run_dir / "gm.output.json")
        serialized = repr({
            "result_decision": result["decision_point"],
            "trace_decision": trace.get("decision_point"),
            "gm_decision": gm_loop["outputs"][0]["decision_point"],
        }).lower()

        self.assertNotEqual(result["stop_reason"], "player_decision")
        self.assertIsNone(result["decision_point"])
        self.assertIn("[redacted]", serialized)
        self.assertNotIn("moon-base-archive", serialized)
        self.assertNotIn("moon_base_archive", serialized)

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
        objective_dir = self.run_dir.parent / "memory" / "characters" / "ClassRep"
        actor_dir = self.run_dir.parent / "characters" / "ClassRep"
        self.assertTrue((objective_dir / "profile.md").exists())
        self.assertTrue((objective_dir / "background.md").exists())
        self.assertTrue((actor_dir / "profile.md").exists())
        self.assertFalse((objective_dir / "profile.json").exists())
        actor_profile = (actor_dir / "profile.md").read_text(encoding="utf-8")
        self.assertIn("Rule-bound class monitor with a sharp eye.", actor_profile)
        self.assertNotIn("source_agent", actor_profile)

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
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "subGM:side_a":
                return subgm_output("side_a", "Ada found the broken seal.")
            if agent_key == "subGM:side_b":
                return subgm_output("side_b", "Bert saw a late rider.")
            self.fail(f"unexpected dispatch {agent_key}")

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stop_reason"], "complete")
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

    def test_gm_loop_rejects_complete_with_unresolved_active_subgm_thread(self):
        self.register_characters("Ada")
        calls = []

        def subgm_output():
            return {
                "agent": "subGM",
                "thread_id": "side_a",
                "status": "needs_gm",
                "scene_beats": [{"content": "Ada reaches the locked side door."}],
                "events": [],
                "actor_calls": [],
                "messages_to_gm": [
                    {"content": "Ada needs GM direction before this side thread can finish.", "status": "needs_gm"}
                ],
                "world_state_delta": [],
                "character_usage": ["character:Ada"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["Do not merge the side thread yet."],
                "next_resume_point": "at the locked side door",
            }

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            if agent_key == "gm":
                gm_count = calls.count("gm")
                if gm_count == 1:
                    return {
                        "agent": "gm",
                        "scene_beats": [{"content": "The main room has settled."}],
                        "events": [],
                        "actor_calls": [],
                        "parallel_groups": [],
                        "world_state_delta": [],
                        "subgm_commands": [
                            {
                                "action": "start",
                                "thread_id": "side_a",
                                "title": "Ada checks the side door",
                                "outline": "Ada investigates a locked door away from the player.",
                                "time_window": "same scene",
                                "location": "side corridor",
                                "objective": "Find whether the locked door can be opened.",
                                "allowed_characters": ["character:Ada"],
                                "forbidden_characters": ["player"],
                                "message": "Start Ada side thread.",
                                "metadata": {},
                            }
                        ],
                        "decision_point": None,
                        "stop_reason": "continue",
                    }
                self.assertEqual(packet["world_state"]["side_thread_summaries"][0]["status"], "needs_gm")
                self.assertIn(
                    "Ada needs GM direction",
                    packet["world_state"]["subgm_messages"][-1]["content"],
                )
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The main room seems ready to close."}],
                    "events": [],
                    "actor_calls": [],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "subgm_commands": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            if agent_key == "subGM:side_a":
                return subgm_output()
            self.fail(f"unexpected dispatch {agent_key}")

        with self.assertRaisesRegex(self.agent_turn_loop.AgentTurnLoopError, "unresolved subGM side thread.*side_a"):
            self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=3)

        self.assertIn("subGM:side_a", calls)
        self.assertFalse((self.run_dir / "gm.output.json").exists())

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

    def test_gm_loop_normalizes_chinese_allowed_character_names_for_subgm_commands(self):
        input_payload = {"character_contexts": {"characters": [{"name": "苏黎"}]}}
        gm_output = {
            "subgm_commands": [{
                "action": "start",
                "thread_id": "side_suli",
                "title": "苏黎检查校门",
                "outline": "苏黎在场外确认异常来源。",
                "time_window": "same minute",
                "location": "校门",
                "objective": "确认吊坠异常是否外泄。",
                "allowed_characters": ["character:苏黎"],
                "forbidden_characters": ["player"],
                "message": "Start Su Li side thread.",
                "metadata": {},
            }],
        }

        self.agent_turn_loop._normalize_subgm_command_actor_ids(gm_output, input_payload)
        normalized_actor = gm_output["subgm_commands"][0]["allowed_characters"][0]
        self.assertRegex(normalized_actor, r"^character:C_[0-9a-f]{8}$")
        self.agent_turn_loop._prevalidate_subgm_commands(self.run_dir, gm_output, input_payload)
        self.agent_turn_loop._apply_subgm_commands(self.run_dir, gm_output, input_payload)

        state = self.agent_run.read_json(self.run_dir / "side_threads" / "side_suli" / "state.json")
        self.assertEqual(state["allowed_characters"], [normalized_actor])
        self.assertEqual(gm_output["subgm_commands"][0]["allowed_characters"], [normalized_actor])

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
        profile_md_path = self.run_dir.parent / "memory" / "characters" / "ClassRep" / "profile.md"
        actor_profile_path = self.run_dir.parent / "characters" / "ClassRep" / "profile.md"
        persisted_gm = json.dumps(self.agent_run.read_json(self.run_dir / "gm.output.json"), ensure_ascii=False)
        combined = "\n".join([
            json.dumps(actor_packets[0], ensure_ascii=False),
            profile_md_path.read_text(encoding="utf-8"),
            actor_profile_path.read_text(encoding="utf-8"),
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
