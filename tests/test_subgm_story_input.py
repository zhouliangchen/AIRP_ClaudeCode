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


def _visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test subGM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


class SubgmStoryInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        (self.card / ".agent_runs" / "current").write_text(str(self.run_dir.resolve()), encoding="utf-8")
        self.agent_outputs = _load_module("agent_outputs")
        self.agent_interactions = _load_module("agent_interactions")
        self._write_base_round()
        self.side_dir = self.run_dir / "side_threads" / "side_ada_rooftop"
        self._write_side_thread(self.side_dir, "side_ada_rooftop")

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
                    "user_instruction_channel": "Hidden truth: moon base archive",
                },
                "hidden_facts": [{"fact": "moon base archive"}],
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
                        "world_state_delta": [{"scope": "room", "fact": "the archive door is open"}],
                        "decision_point": None,
                        "stop_reason": "complete",
                    }
                ],
            },
        )
        _write_json(self.run_dir / "actor.outputs.json", {})
        self.agent_interactions.init_trace(self.run_dir, participants=["gm"], chapter_target_words=1200)

    def _write_side_thread(self, side_dir, thread_id):
        _write_json(
            side_dir / "state.json",
            {
                "thread_id": thread_id,
                "status": "completed",
                "title": "Ada checks [redacted] records",
                "boundary": {"location": "rooftop", "outline": "Off-screen check"},
                "objective": "Confirm the rooftop signal without exposing secrets.",
                "allowed_characters": ["character:Ada"],
                "forbidden_characters": ["player"],
                "last_scene_beats": [{"content": "Ada reaches the roof."}],
                "next_resume_point": "Ada can report the signal.",
                "urgency": "normal",
            },
        )
        _write_json(
            side_dir / "subgm.output.json",
            {
                "agent": "subGM",
                "thread_id": thread_id,
                "status": "completed",
                "scene_beats": [{"content": "Ada reaches the roof."}],
                "events": [{"type": "signal_seen", "content": "A green lamp blinks twice."}],
                "actor_calls": [
                    {
                        "call_id": "call-character-Ada-1",
                        "actor_id": "character:Ada",
                        "prompt": "React to the rooftop signal.",
                        "reason": "Ada is alone on the roof.",
                        "visibility_basis": _visibility_basis("character:Ada"),
                    }
                ],
                "messages_to_gm": [{"content": "Ada can report the signal."}],
                "world_state_delta": [{"scope": "rooftop", "fact": "a green lamp blinked twice"}],
                "character_usage": ["character:Ada"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": ["Use as an off-screen intercut only if pacing benefits."],
                "next_resume_point": "Ada can report the signal.",
            },
        )
        _write_json(
            side_dir / "actor.outputs.json",
            {
                "character:Ada": [
                    {
                        "agent": "character",
                        "agent_id": "character:Ada",
                        "character_name": "Ada",
                        "events": [
                            {"type": "dialogue", "target": "", "content": "I found a signal."},
                            {
                                "type": "memory_delta",
                                "target": "self",
                                "content": "I found a green rooftop signal.",
                            },
                            {"type": "goal_update", "target": "self", "content": "Tell the player about the signal."},
                        ],
                        "stop_reason": "continue",
                    }
                ],
            },
        )
        self.agent_interactions.init_trace(
            side_dir,
            participants=[f"subGM:{thread_id}", "character:Ada"],
            chapter_target_words=300,
        )
        self.agent_interactions.append_event(
            side_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="dialogue",
            content="I found a signal.",
            source_call_id="call-character-Ada-1",
        )
        self.agent_interactions.append_event(
            side_dir,
            actor="character:Ada",
            visibility="actor_visible",
            event_type="memory_delta",
            content="I found a green rooftop signal.",
            target="self",
            source_call_id="call-character-Ada-1",
        )
        self.agent_interactions.append_event(
            side_dir,
            actor="character:Ada",
            visibility="actor_visible",
            event_type="goal_update",
            content="Tell the player about the signal.",
            target="self",
            source_call_id="call-character-Ada-1",
        )

    def test_completed_side_thread_appears_without_hidden_phrase_in_side_bundle(self):
        state = json.loads((self.side_dir / "state.json").read_text(encoding="utf-8"))
        state["title"] = "Ada checks moon base archive records"
        _write_json(self.side_dir / "state.json", state)

        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(story_input["side_threads"]["threads"][0]["thread_id"], "side_ada_rooftop")
        self.assertEqual(
            story_input["side_threads"]["threads"][0]["actor_output_source_call_ids"],
            {"character:Ada": ["call-character-Ada-1"]},
        )
        self.assertEqual(
            story_input["player_inputs"]["routed_input"]["user_instruction_channel"],
            "Hidden truth: moon base archive",
        )
        side_text = json.dumps(story_input["side_threads"], ensure_ascii=False)
        self.assertNotIn("moon base archive", side_text)

    def test_side_actor_output_requires_side_trace_source_call_id(self):
        trace = json.loads((self.side_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        trace["events"] = [
            event
            for event in trace["events"]
            if event.get("type") != "dialogue"
        ]
        _write_json(self.side_dir / "interaction.trace.json", trace)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "raw trace source_call_id"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_side_actor_output_targeting_player_branch_is_rejected(self):
        _write_json(
            self.side_dir / "actor.outputs.json",
            {
                "player": [
                    {
                        "agent": "player",
                        "agent_id": "player",
                        "events": [{"type": "action", "target": "", "content": "I should not be here."}],
                        "stop_reason": "continue",
                    }
                ],
            },
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "player"):
            self.agent_outputs.build_story_input(self.run_dir)

    def test_side_actor_calls_require_side_actor_outputs_without_writing_story_input(self):
        (self.side_dir / "actor.outputs.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"actor\.outputs\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_actor_call_blank_call_id_is_rejected_without_writing_story_input(self):
        subgm_output = json.loads((self.side_dir / "subgm.output.json").read_text(encoding="utf-8"))
        subgm_output["actor_calls"][0]["call_id"] = ""
        _write_json(self.side_dir / "subgm.output.json", subgm_output)
        (self.side_dir / "actor.outputs.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "call_id"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_actor_output_without_subgm_actor_call_is_rejected_without_writing_story_input(self):
        subgm_output = json.loads((self.side_dir / "subgm.output.json").read_text(encoding="utf-8"))
        subgm_output["actor_calls"] = []
        _write_json(self.side_dir / "subgm.output.json", subgm_output)

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "actor_calls"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_actor_output_outside_allowed_characters_is_rejected_without_writing_story_input(self):
        _write_json(
            self.side_dir / "subgm.output.json",
            {
                "agent": "subGM",
                "thread_id": "side_ada_rooftop",
                "status": "completed",
                "scene_beats": [{"content": "Bob answers from outside the boundary."}],
                "events": [],
                "actor_calls": [
                    {
                        "call_id": "call-character-Bob-1",
                        "actor_id": "character:Bob",
                        "prompt": "React despite not being allowed.",
                        "reason": "Regression fixture.",
                        "visibility_basis": _visibility_basis("character:Bob"),
                    }
                ],
                "messages_to_gm": [],
                "world_state_delta": [],
                "character_usage": ["character:Bob"],
                "promotion_requests": [],
                "boundary_requests": [],
                "notes_for_story": [],
                "next_resume_point": "",
            },
        )
        _write_json(
            self.side_dir / "actor.outputs.json",
            {
                "character:Bob": [
                    {
                        "agent": "character",
                        "agent_id": "character:Bob",
                        "character_name": "Bob",
                        "events": [{"type": "dialogue", "target": "", "content": "I should not be in this side scene."}],
                        "stop_reason": "continue",
                    }
                ],
            },
        )
        self.agent_interactions.append_event(
            self.side_dir,
            actor="character:Bob",
            visibility="world_visible",
            event_type="dialogue",
            content="I should not be in this side scene.",
            source_call_id="call-character-Bob-1",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "allowed_characters"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_actor_extra_source_call_id_is_rejected_without_writing_story_input(self):
        actor_outputs = json.loads((self.side_dir / "actor.outputs.json").read_text(encoding="utf-8"))
        actor_outputs["character:Ada"].append(
            {
                "agent": "character",
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "events": [{"type": "dialogue", "target": "", "content": "I found another signal."}],
                "stop_reason": "continue",
            }
        )
        _write_json(self.side_dir / "actor.outputs.json", actor_outputs)
        self.agent_interactions.append_event(
            self.side_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="dialogue",
            content="I found another signal.",
            source_call_id="call-character-Ada-2",
        )

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, "extra"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_subgm_message_or_world_hidden_phrase_is_rejected_without_writing_story_input(self):
        for field, replacement in (
            ("messages_to_gm", [{"content": "Ada discovered the moon base archive."}]),
            ("world_state_delta", [{"scope": "rooftop", "fact": "moon base archive"}]),
        ):
            with self.subTest(field=field):
                if (self.run_dir / "story.input.json").exists():
                    self.run_dir.joinpath("story.input.json").unlink()
                self._write_side_thread(self.side_dir, "side_ada_rooftop")
                subgm_output = json.loads((self.side_dir / "subgm.output.json").read_text(encoding="utf-8"))
                subgm_output[field] = replacement
                _write_json(self.side_dir / "subgm.output.json", subgm_output)

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, field):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_subgm_request_hidden_marker_is_rejected_without_writing_story_input(self):
        for field, replacement in (
            ("boundary_requests", [{"hidden_note": "private side boundary"}]),
            ("promotion_requests", [{"reason": "world_truth should not reach story"}]),
        ):
            with self.subTest(field=field):
                if (self.run_dir / "story.input.json").exists():
                    self.run_dir.joinpath("story.input.json").unlink()
                self._write_side_thread(self.side_dir, "side_ada_rooftop")
                subgm_output = json.loads((self.side_dir / "subgm.output.json").read_text(encoding="utf-8"))
                subgm_output[field] = replacement
                _write_json(self.side_dir / "subgm.output.json", subgm_output)

                with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, field):
                    self.agent_outputs.build_story_input(self.run_dir)

                self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_thread_missing_trace_is_rejected_without_writing_story_input(self):
        (self.side_dir / "actor.outputs.json").unlink()
        (self.side_dir / "interaction.trace.json").unlink()

        with self.assertRaisesRegex(self.agent_outputs.AgentOutputError, r"interaction\.trace\.json"):
            self.agent_outputs.build_story_input(self.run_dir)

        self.assertFalse((self.run_dir / "story.input.json").exists())

    def test_side_subgm_world_state_delta_has_source_thread_id(self):
        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertIn(
            {"scope": "rooftop", "fact": "a green lamp blinked twice", "source_thread_id": "side_ada_rooftop"},
            story_input["memory_deltas"]["world"],
        )

    def test_side_actor_memory_delta_and_goal_update_merge_into_actor_memory(self):
        story_input = self.agent_outputs.build_story_input(self.run_dir)

        self.assertEqual(
            [item["type"] for item in story_input["memory_deltas"]["actors"]["character:Ada"]],
            ["memory_delta", "goal_update"],
        )


if __name__ == "__main__":
    unittest.main()
