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


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def start_command(
    thread_id="side_suli_rooftop",
    allowed=None,
    forbidden=None,
):
    return {
        "action": "start",
        "thread_id": thread_id,
        "title": "Rooftop warning",
        "outline": "SuLi checks the rooftop sigil.",
        "time_window": "same morning",
        "location": "school rooftop",
        "objective": "Advance the off-screen clue.",
        "allowed_characters": allowed or ["character:SuLi"],
        "forbidden_characters": forbidden if forbidden is not None else ["player"],
        "priority": "normal",
        "message": "Start now.",
        "metadata": {},
    }


def visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test subGM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


def subgm_output(thread_id="side_suli_rooftop", **overrides):
    payload = {
        "agent": "subGM",
        "thread_id": thread_id,
        "status": "completed",
        "scene_beats": [{"content": "SuLi sees chalk dust beside the rooftop vent."}],
        "events": [{"type": "scene", "content": "A chalk line glows on the vent."}],
        "actor_calls": [],
        "messages_to_gm": [{"content": "The rooftop clue is ready."}],
        "world_state_delta": [{"scope": "rooftop", "fact": "chalk dust found"}],
        "character_usage": ["character:SuLi"],
        "promotion_requests": [],
        "boundary_requests": [],
        "notes_for_story": ["Use only after GM merges it."],
        "next_resume_point": "resume at the rooftop vent",
    }
    payload.update(overrides)
    for call in payload.get("actor_calls", []):
        if isinstance(call, dict) and "visibility_basis" not in call:
            actor_id = str(call.get("actor_id") or "").strip()
            call["visibility_basis"] = visibility_basis(actor_id or "character:SuLi")
    return payload


def character_output(actor_id="character:SuLi"):
    return "I found chalk dust by the vent."


def projection_pass(packet):
    return {
        "decision": "pass",
        "target_actor_id": str(packet.get("target_actor_id") or ""),
        "source_call_id": str(packet.get("source_call_id") or ""),
        "final_actor_message": str(packet.get("requested_actor_message") or ""),
        "feedback": "",
    }


def wrap_dispatch_with_projection(dispatch):
    def wrapped(agent_key, packet):
        if agent_key == "projection":
            if getattr(dispatch, "handles_projection", False):
                return dispatch(agent_key, packet)
            return projection_pass(packet)
        return dispatch(agent_key, packet)

    return wrapped


class SubgmTurnLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.subgm_threads = load_module("subgm_threads")
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        (self.run_dir / "input.json").write_text(
            json.dumps(
                {
                    "raw_text": "I stay in class.",
                    "routed_input": {
                        "role_channel": "I stay in class.",
                        "user_instruction_channel": "Secret hidden phrase: the rooftop sigil is a trap.",
                    },
                    "character_contexts": {
                        "characters": [
                            {
                                "name": "SuLi",
                                "role": "quiet classmate",
                                "location": "school rooftop",
                                "sensory_channels": ["visual", "auditory"],
                                "memory": {
                                    "long_term": ["I know the rooftop stairs."],
                                    "key_memories": [],
                                    "short_term": [],
                                    "goals": ["Stay unnoticed."],
                                },
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.subgm_turn_loop = load_module("subgm_turn_loop")
        run_side_thread = self.subgm_turn_loop.run_side_thread

        def run_with_projection(run_dir, thread_id, dispatch, *args, **kwargs):
            return run_side_thread(
                run_dir,
                thread_id,
                wrap_dispatch_with_projection(dispatch),
                *args,
                **kwargs,
            )

        self.subgm_turn_loop.run_side_thread = run_with_projection

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_side_thread_dispatches_subgm_and_allowed_character_with_projection(self):
        calls = []
        actor_packets = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You notice chalk dust near the vent.",
                            "reason": "SuLi is physically present in the side thread.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(result["called_actors"], ["character:SuLi"])
        self.assertEqual([item[0] for item in calls], ["subGM:side_suli_rooftop", "character:SuLi"])
        side = self.run_dir / "side_threads" / "side_suli_rooftop"
        self.assertTrue((side / "subgm.output.json").exists())
        self.assertTrue((side / "actor.outputs.json").exists())
        self.assertTrue((side / "interaction.trace.json").exists())
        self.assertEqual(read_json(side / "actor.outputs.json")["character:SuLi"][0]["agent_id"], "character:SuLi")

        actor_packet_text = json.dumps(actor_packets[0], ensure_ascii=False)
        self.assertIn("You notice chalk dust near the vent.", actor_packet_text)
        self.assertNotIn("user_instruction_channel", actor_packet_text)
        self.assertNotIn("Secret hidden phrase", actor_packet_text)
        self.assertNotIn("rooftop sigil is a trap", actor_packet_text)

    def test_run_side_thread_wraps_character_natural_language_reply(self):
        reply = "我蹲下身，用指尖抹过粉尘：这里刚有人来过。"

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "你注意到通风口旁有粉尘。",
                            "reason": "SuLi is physically present in the side thread.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                return reply
            raise AssertionError(agent_key)

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        side = self.run_dir / "side_threads" / "side_suli_rooftop"
        output = read_json(side / "actor.outputs.json")["character:SuLi"][0]
        self.assertEqual(output["natural_reply"], reply)
        self.assertEqual(output["events"], [{"type": "reply", "target": "gm", "content": reply, "metadata": {}}])
        trace = read_json(side / "interaction.trace.json")
        actor_events = [event for event in trace["events"] if event.get("actor") == "character:SuLi"]
        self.assertEqual(actor_events[0]["type"], "reply")
        self.assertEqual(actor_events[0]["content"], reply)

    def test_run_side_thread_reports_subgm_and_actor_progress(self):
        progress_calls = []
        self.subgm_turn_loop.write_progress = lambda *args, **kwargs: progress_calls.append((args, kwargs))

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You notice chalk dust near the vent.",
                            "reason": "SuLi is physically present in the side thread.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                return character_output()
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual([args[0] for args, _kwargs in progress_calls], ["gm_loop.subgm_dispatch", "gm_loop.actor_dispatch"])
        subgm_details = [
            kwargs.get("detail")
            for args, kwargs in progress_calls
            if args and args[0] == "gm_loop.subgm_dispatch"
        ]
        self.assertEqual(subgm_details, [{"subgm_thread_id": "side_suli_rooftop", "step": 1}])
        actor_details = [
            kwargs.get("detail")
            for args, kwargs in progress_calls
            if args and args[0] == "gm_loop.actor_dispatch"
        ]
        self.assertEqual(
            actor_details,
            [{
                "agent": "character:SuLi",
                "subgm_thread_id": "side_suli_rooftop",
                "actor_call_id": "call-character-SuLi-1",
            }],
        )

    def test_side_thread_actor_dispatch_attaches_context_version(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You notice chalk dust near the vent.",
                            "reason": "SuLi is physically present in the side thread.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(actor_packets), 1)
        self.assertEqual(actor_packets[0]["context_version"]["algorithm"], "sha256")
        self.assertTrue(actor_packets[0]["context_version"]["hash"].startswith("sha256:"))

    def test_actor_packet_receives_subgm_visibility_basis(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You notice chalk dust near the vent.",
                            "reason": "SuLi is physically present in the side thread.",
                            "visibility_basis": {
                                "mode": "location",
                                "summary": "SuLi is on the rooftop and can see the chalk dust.",
                                "location": "school rooftop",
                                "visible_to": ["character:SuLi"],
                                "sensory_channels": ["visual"],
                                "target_actor": "character:SuLi",
                            },
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["called_actors"], ["character:SuLi"])
        self.assertEqual(len(actor_packets), 1)
        packet_basis = actor_packets[0]["gm_visibility_basis"]
        self.assertEqual(packet_basis.get("mode"), "location")
        self.assertEqual(packet_basis.get("summary"), "SuLi is on the rooftop and can see the chalk dust.")
        self.assertEqual(packet_basis.get("location"), "school rooftop")
        self.assertEqual(packet_basis.get("visible_to"), ["character:SuLi"])
        self.assertEqual(packet_basis.get("sensory_channels"), ["visual"])
        self.assertEqual(packet_basis.get("target_actor"), "character:SuLi")

    def test_subgm_actor_call_runs_projection_before_actor_dispatch(self):
        actor_dir = Path(self.tmp.name) / "characters" / "SuLi"
        actor_dir.mkdir(parents=True)
        (actor_dir / "profile.md").write_text(
            "I am SuLi, and rooftop messages always make me cautious.",
            encoding="utf-8",
        )
        projection_packets = []
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You hear the rooftop vent rattle.",
                            "reason": "SuLi is physically present in the side thread.",
                            "visibility_basis": visibility_basis("character:SuLi"),
                        }
                    ]
                )
            if agent_key == "projection":
                projection_packets.append(packet)
                return {
                    "decision": "edited",
                    "target_actor_id": "character:SuLi",
                    "source_call_id": "call-character-SuLi-1",
                    "final_actor_message": "You hear the rooftop vent rattle twice.",
                    "feedback": "",
                }
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        dispatch.handles_projection = True
        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(projection_packets), 1)
        self.assertIn("rooftop messages", projection_packets[0]["actor_context"])
        self.assertEqual(actor_packets[0]["gm_prompt"], "You hear the rooftop vent rattle twice.")
        short_term = (actor_dir / "short_term_memories.md").read_text(encoding="utf-8")
        self.assertIn("You hear the rooftop vent rattle twice.", short_term)
        self.assertIn("I found chalk dust by the vent.", short_term)

    def test_actor_packet_visibility_basis_preserves_top_level_subgm_call_metadata(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You hear rain on the rooftop vent.",
                            "reason": "SuLi is physically present in the side thread.",
                            "location": "school rooftop",
                            "visible_to": ["character:SuLi"],
                            "sensory_channels": ["auditory"],
                            "visibility_basis": {
                                "mode": "location",
                                "summary": "SuLi is on the rooftop and can hear the rain.",
                                "target_actor": "character:SuLi",
                            },
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(actor_packets), 1)
        packet_basis = actor_packets[0]["gm_visibility_basis"]
        self.assertEqual(packet_basis.get("mode"), "location")
        self.assertEqual(packet_basis.get("summary"), "SuLi is on the rooftop and can hear the rain.")
        self.assertEqual(packet_basis.get("target_actor"), "character:SuLi")
        self.assertEqual(packet_basis.get("location"), "school rooftop")
        self.assertEqual(packet_basis.get("visible_to"), ["character:SuLi"])
        self.assertEqual(packet_basis.get("sensory_channels"), ["auditory"])

    def test_subgm_actor_call_visibility_basis_must_prove_target_actor_before_dispatch(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You hear a private instruction meant for Eve.",
                            "reason": "This must not be routed to SuLi.",
                            "visibility_basis": {
                                "mode": "direct",
                                "summary": "Eve is directly addressed by this side-thread prompt.",
                                "target_actor": "character:Eve",
                                "visible_to": ["character:Eve"],
                            },
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, r"visibility_basis.*character:SuLi"):
            self.subgm_turn_loop.run_side_thread(
                self.run_dir,
                "side_suli_rooftop",
                dispatch,
            )

        self.assertEqual(actor_packets, [])

    def test_out_of_boundary_actor_call_failure_does_not_leak_unapproved_side_output(self):
        leaked_beat = "UNAPPROVED boundary beat should never persist."
        leaked_resume = "UNAPPROVED boundary resume should never persist."

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    scene_beats=[{"content": leaked_beat}],
                    events=[{"type": "scene", "content": "UNAPPROVED boundary event should never persist."}],
                    actor_calls=[
                        {
                            "call_id": "call-character-Bert-1",
                            "actor_id": "character:Bert",
                            "prompt": "You notice a forbidden side-thread clue.",
                            "reason": "Bert is outside this side-thread boundary.",
                        }
                    ],
                    messages_to_gm=[{"content": "UNAPPROVED boundary message should never persist."}],
                    next_resume_point=leaked_resume,
                )
            raise AssertionError(agent_key)

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "allowed_characters"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        side_dir = self.run_dir / "side_threads" / "side_suli_rooftop"
        self.assertFalse((side_dir / "subgm.output.json").exists())
        self.assertFalse((side_dir / "actor.outputs.json").exists())
        trace_text = (side_dir / "interaction.trace.json").read_text(encoding="utf-8")
        self.assertNotIn("UNAPPROVED", trace_text)
        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["last_scene_beats"], [])
        self.assertEqual(summaries[0]["next_resume_point"], "")

    def test_player_actor_call_failure_does_not_leak_unapproved_side_output(self):
        leaked_beat = "UNAPPROVED player beat should never persist."
        leaked_resume = "UNAPPROVED player resume should never persist."

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    scene_beats=[{"content": leaked_beat}],
                    events=[{"type": "scene", "content": "UNAPPROVED player event should never persist."}],
                    actor_calls=[
                        {
                            "call_id": "call-character-Player-1",
                            "actor_id": "player",
                            "prompt": "This must not route to the player.",
                            "reason": "Player is forbidden in side threads.",
                            "visibility_basis": visibility_basis("player"),
                        }
                    ],
                    messages_to_gm=[{"content": "UNAPPROVED player message should never persist."}],
                    next_resume_point=leaked_resume,
                )
            raise AssertionError(agent_key)

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "must not target player"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        side_dir = self.run_dir / "side_threads" / "side_suli_rooftop"
        self.assertFalse((side_dir / "subgm.output.json").exists())
        self.assertFalse((side_dir / "actor.outputs.json").exists())
        trace_text = (side_dir / "interaction.trace.json").read_text(encoding="utf-8")
        self.assertNotIn("UNAPPROVED", trace_text)
        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["last_scene_beats"], [])
        self.assertEqual(summaries[0]["next_resume_point"], "")

    def test_subgm_scene_visibility_metadata_reaches_side_trace_summary(self):
        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    scene_beats=[{
                        "content": "SuLi sees chalk dust beside the rooftop vent.",
                        "scene_id": "rooftop-1",
                        "location": "school rooftop",
                        "time_window": "same morning",
                        "visible_to": ["character:SuLi"],
                        "sensory_channels": ["visual"],
                        "source_actor": "subGM:side_suli_rooftop",
                        "target_actor": "character:SuLi",
                        "visibility_basis": {
                            "mode": "location",
                            "summary": "SuLi is on the rooftop and can see the chalk dust.",
                            "location": "school rooftop",
                            "visible_to": ["character:SuLi"],
                            "sensory_channels": ["visual"],
                            "target_actor": "character:SuLi",
                        },
                    }],
                    events=[{
                        "type": "scene",
                        "content": "A chalk line glows on the vent.",
                        "location": "school rooftop",
                        "visible_to": ["character:SuLi"],
                        "sensory_channels": ["visual"],
                        "target_actor": "character:SuLi",
                        "visibility_basis": {
                            "mode": "location",
                            "summary": "SuLi can see the chalk line from the vent.",
                            "location": "school rooftop",
                            "visible_to": ["character:SuLi"],
                            "sensory_channels": ["visual"],
                            "target_actor": "character:SuLi",
                        },
                    }],
                    actor_calls=[],
                )
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertTrue(result["ok"])
        side_dir = self.run_dir / "side_threads" / "side_suli_rooftop"
        summary = load_module("agent_interactions").summarize_for_story_input(side_dir)
        self.assertEqual(len(summary["visible_events"]), 2)
        scene_beat = summary["visible_events"][0]
        self.assertEqual(scene_beat["type"], "scene_beat")
        self.assertEqual(scene_beat.get("location"), "school rooftop")
        self.assertEqual(scene_beat.get("visible_to"), ["character:SuLi"])
        self.assertEqual(scene_beat.get("sensory_channels"), ["visual"])
        self.assertEqual(scene_beat.get("target_actor"), "character:SuLi")
        self.assertEqual(
            scene_beat.get("visibility_basis", {}).get("summary"),
            "SuLi is on the rooftop and can see the chalk dust.",
        )
        event = summary["visible_events"][1]
        self.assertEqual(event["type"], "scene")
        self.assertEqual(event.get("location"), "school rooftop")
        self.assertEqual(event.get("visible_to"), ["character:SuLi"])
        self.assertEqual(event.get("sensory_channels"), ["visual"])
        self.assertEqual(
            event.get("visibility_basis", {}).get("summary"),
            "SuLi can see the chalk line from the vent.",
        )

    def test_run_ready_side_threads_runs_sorted_runnable_threads_sequentially(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [start_command(thread_id="side_ada_library", allowed=["character:Ada"])],
        )
        payload = read_json(self.run_dir / "input.json")
        payload["character_contexts"]["characters"].append({"name": "Ada", "memory": [], "goals": []})
        (self.run_dir / "input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            thread_id = agent_key.split(":", 1)[1]
            return subgm_output(thread_id=thread_id, messages_to_gm=[{"content": f"{thread_id} done."}])

        results = self.subgm_turn_loop.run_ready_side_threads(self.run_dir, dispatch, max_workers=1)

        self.assertEqual([item["thread_id"] for item in results], ["side_ada_library", "side_suli_rooftop"])
        self.assertEqual(calls, ["subGM:side_ada_library", "subGM:side_suli_rooftop"])

    def test_run_ready_side_threads_parallel_workers_return_sorted_results(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [start_command(thread_id="side_ada_library", allowed=["character:Ada"])],
        )
        payload = read_json(self.run_dir / "input.json")
        payload["character_contexts"]["characters"].append({"name": "Ada", "memory": [], "goals": []})
        (self.run_dir / "input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        calls = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def dispatch(agent_key, packet):
            with lock:
                calls.append(agent_key)
            barrier.wait(timeout=5)
            thread_id = agent_key.split(":", 1)[1]
            return subgm_output(thread_id=thread_id, messages_to_gm=[{"content": f"{thread_id} done."}])

        results = self.subgm_turn_loop.run_ready_side_threads(self.run_dir, dispatch, max_workers=2)

        self.assertEqual(sorted(calls), ["subGM:side_ada_library", "subGM:side_suli_rooftop"])
        self.assertEqual([item["thread_id"] for item in results], ["side_ada_library", "side_suli_rooftop"])
        messages = self.subgm_threads.load_messages_for_gm(self.run_dir)
        contents = [item.get("content") for item in messages]
        self.assertIn("side_ada_library done.", contents)
        self.assertIn("side_suli_rooftop done.", contents)

    def test_run_ready_side_threads_skips_paused_and_completed_threads(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_suli_rooftop", "message": "Pause.", "metadata": {}}],
        )
        calls = []

        def dispatch(agent_key, packet):
            calls.append(agent_key)
            return subgm_output()

        results = self.subgm_turn_loop.run_ready_side_threads(self.run_dir, dispatch, max_workers=1)

        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_subgm_hidden_scene_beats_and_events_do_not_reach_actor_packet(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    scene_beats=[
                        {
                            "content": "SuLi sees chalk dust. The rooftop sigil is a trap.",
                            "visibility_basis": visibility_basis("character:SuLi"),
                        }
                    ],
                    events=[
                        {
                            "type": "scene",
                            "content": "The rooftop sigil is a trap, but the vent rattles.",
                            "visibility_basis": visibility_basis("character:SuLi"),
                        }
                    ],
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You hear the vent rattle.",
                            "reason": "Safe visible prompt.",
                        }
                    ],
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        trace = read_json(self.run_dir / "side_threads" / "side_suli_rooftop" / "interaction.trace.json")
        visible_trace_text = json.dumps(trace["events"], ensure_ascii=False)
        self.assertIn("chalk dust", visible_trace_text)
        self.assertIn("vent rattles", visible_trace_text)
        self.assertNotIn("rooftop sigil is a trap", visible_trace_text.lower())

        actor_packet_text = json.dumps(actor_packets[0], ensure_ascii=False)
        self.assertNotIn("rooftop sigil is a trap", actor_packet_text.lower())

    def test_subgm_marker_text_does_not_become_world_visible_trace(self):
        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    scene_beats=[
                        {"content": "SuLi scans the vent. world_truth: rooftop trap"}
                    ],
                    events=[
                        {
                            "type": "scene",
                            "content": "GM-only: the rooftop trap is armed.",
                        }
                    ],
                    actor_calls=[],
                )
            raise AssertionError(agent_key)

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        trace = read_json(self.run_dir / "side_threads" / "side_suli_rooftop" / "interaction.trace.json")
        visible_text = json.dumps(
            [event for event in trace["events"] if event.get("visibility") == "world_visible"],
            ensure_ascii=False,
        ).lower()
        self.assertNotIn("world_truth", visible_text)
        self.assertNotIn("gm-only", visible_text)
        self.assertNotIn("rooftop trap", visible_text)

    def test_subgm_actor_call_prompt_is_redacted_before_projection(self):
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-1",
                            "actor_id": "character:SuLi",
                            "prompt": "You see chalk dust near the vent. The rooftop sigil is a trap.",
                            "reason": "Prompt includes a hidden copied phrase.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                actor_packets.append(packet)
                return character_output()
            raise AssertionError(agent_key)

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertEqual(len(actor_packets), 1)
        actor_packet_text = json.dumps(actor_packets[0], ensure_ascii=False)
        self.assertIn("You see chalk dust near the vent.", actor_packet_text)
        self.assertNotIn("rooftop sigil is a trap", actor_packet_text.lower())

    def test_unsafe_actor_call_id_is_rejected_before_actor_dispatch_or_persist(self):
        actor_calls = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "unsafe-call-id",
                            "actor_id": "character:SuLi",
                            "prompt": "You check the vent.",
                            "reason": "Invalid call id should not reach actor.",
                        }
                    ]
                )
            actor_calls.append((agent_key, packet))
            return character_output()

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "call_id"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertEqual(actor_calls, [])
        self.assertFalse((self.run_dir / "side_threads" / "side_suli_rooftop" / "actor.outputs.json").exists())

    def test_valid_actor_call_id_is_preserved_in_side_trace(self):
        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_suli_rooftop":
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-SuLi-7",
                            "actor_id": "character:SuLi",
                            "prompt": "You check the vent.",
                            "reason": "Valid trace-safe call id.",
                        }
                    ]
                )
            if agent_key == "character:SuLi":
                return character_output()
            raise AssertionError(agent_key)

        self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        trace = read_json(self.run_dir / "side_threads" / "side_suli_rooftop" / "interaction.trace.json")
        actor_events = [event for event in trace["events"] if event.get("actor") == "character:SuLi"]
        self.assertTrue(actor_events)
        self.assertEqual({event.get("source_call_id") for event in actor_events}, {"call-character-SuLi-7"})

    def test_run_side_thread_rejects_actor_not_in_allowed_characters(self):
        def dispatch(agent_key, packet):
            if agent_key.startswith("subGM:"):
                return subgm_output(
                    actor_calls=[
                        {
                            "call_id": "call-character-Other-1",
                            "actor_id": "character:Other",
                            "prompt": "You hear the rooftop door.",
                            "reason": "Not allowed.",
                        }
                    ],
                    character_usage=[],
                )
            raise AssertionError(agent_key)

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "allowed"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

    def test_run_side_thread_rejects_player_actor_call(self):
        def dispatch(agent_key, packet):
            return subgm_output(
                actor_calls=[
                    {
                        "call_id": "call-player-1",
                        "actor_id": "player",
                        "prompt": "You join the rooftop thread.",
                        "reason": "Invalid.",
                    }
                ],
                character_usage=[],
            )

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "player"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

    def test_run_side_thread_rejects_unknown_character_context(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [start_command(thread_id="side_ada_library", allowed=["character:Ada"])],
        )

        def dispatch(agent_key, packet):
            return subgm_output(
                thread_id="side_ada_library",
                actor_calls=[
                    {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "You check the library.",
                        "reason": "Ada is allowed but missing from input context.",
                    }
                ],
                character_usage=["character:Ada"],
            )

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "important character"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_ada_library", dispatch)

    def test_run_side_thread_accepts_canonical_character_actor_id(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [start_command(thread_id="side_self_shadow", allowed=["character:character__self"])],
        )
        input_payload = json.loads((self.run_dir / "input.json").read_text(encoding="utf-8"))
        input_payload["character_contexts"]["characters"] = [
            {
                "name": "_self",
                "role": "reserved witness",
                "location": "school rooftop",
                "sensory_channels": ["visual"],
            }
        ]
        (self.run_dir / "input.json").write_text(
            json.dumps(input_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "subGM:side_self_shadow":
                return subgm_output(
                    thread_id="side_self_shadow",
                    actor_calls=[
                        {
                            "call_id": "call-character-character__self-1",
                            "actor_id": "character:character__self",
                            "prompt": "You notice chalk dust near the vent.",
                            "reason": "The canonical character is present in the side thread.",
                        }
                    ],
                    character_usage=["character:character__self"],
                )
            if agent_key == "character:character__self":
                actor_packets.append(packet)
                return character_output("character:character__self")
            raise AssertionError(agent_key)

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_self_shadow", dispatch)

        self.assertEqual(result["called_actors"], ["character:character__self"])
        self.assertEqual(actor_packets[0]["actor_id"], "character:character__self")
        self.assertEqual(actor_packets[0]["self_knowledge"]["name"], "_self")

    def test_run_side_thread_updates_thread_status_and_messages_on_completion(self):
        def dispatch(agent_key, packet):
            return subgm_output(messages_to_gm=[])

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertEqual(result["status"], "completed")
        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["status"], "completed")
        self.assertEqual(summaries[0]["next_resume_point"], "resume at the rooftop vent")
        messages = self.subgm_threads.load_messages_for_gm(self.run_dir)
        self.assertEqual(messages[-1]["from"], "subGM:side_suli_rooftop")
        self.assertEqual(messages[-1]["status"], "completed")
        self.assertEqual(messages[-1]["content"], "")

    def test_paused_thread_is_not_dispatched(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_suli_rooftop", "message": "Pause.", "metadata": {}}],
        )

        def dispatch(agent_key, packet):
            raise AssertionError("paused thread should not dispatch")

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["called_actors"], [])

    def test_completed_thread_is_not_dispatched(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "close", "thread_id": "side_suli_rooftop", "message": "Done.", "metadata": {}}],
        )

        def dispatch(agent_key, packet):
            raise AssertionError("completed thread should not dispatch")

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["called_actors"], [])

    def test_max_steps_zero_does_not_dispatch(self):
        def dispatch(agent_key, packet):
            raise AssertionError("max_steps=0 should not dispatch")

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
            max_steps=0,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "max_steps")
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["called_actors"], [])

    def test_running_thread_that_hits_max_steps_persists_needs_gm_message(self):
        def dispatch(agent_key, packet):
            return subgm_output(
                status="running",
                messages_to_gm=[],
                next_resume_point="continue checking the vent",
            )

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
            max_steps=1,
        )

        self.assertEqual(result["status"], "max_steps")
        self.assertEqual(result["steps"], 1)
        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["status"], "needs_gm")
        messages = self.subgm_threads.load_messages_for_gm(self.run_dir)
        self.assertIn("reached max_steps", messages[-1]["content"])
        self.assertEqual(messages[-1]["status"], "needs_gm")

    def test_blocked_thread_dispatches_once_to_report_current_block(self):
        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "Need GM boundary decision.", "status": "blocked"},
        )
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            return subgm_output(
                status="blocked",
                messages_to_gm=[{"content": "Still blocked on boundary decision."}],
            )

        result = self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"], 1)
        self.assertEqual([item[0] for item in calls], ["subGM:side_suli_rooftop"])

    def test_initially_blocked_thread_dispatches_once_even_if_subgm_returns_running(self):
        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "Need GM boundary decision.", "status": "blocked"},
        )
        calls = []

        def dispatch(agent_key, packet):
            calls.append((agent_key, packet))
            return subgm_output(
                status="running",
                messages_to_gm=[{"content": "I can continue after this report."}],
            )

        result = self.subgm_turn_loop.run_side_thread(
            self.run_dir,
            "side_suli_rooftop",
            dispatch,
            max_steps=4,
        )

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["steps"], 1)
        self.assertEqual([item[0] for item in calls], ["subGM:side_suli_rooftop"])

    def test_subgm_thread_id_mismatch_is_rejected(self):
        def dispatch(agent_key, packet):
            return subgm_output(thread_id="other_thread")

        with self.assertRaisesRegex(self.subgm_turn_loop.SubgmTurnLoopError, "thread_id"):
            self.subgm_turn_loop.run_side_thread(self.run_dir, "side_suli_rooftop", dispatch)


if __name__ == "__main__":
    unittest.main()
