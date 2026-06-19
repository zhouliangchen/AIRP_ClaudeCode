import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_interactions():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_interactions", ROOT / "skills" / "agent_interactions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentInteractionTraceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.agent_interactions = _load_agent_interactions()

    def tearDown(self):
        self.tmp.cleanup()

    def test_trace_records_visible_events_and_decision_point(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:Ada"],
            chapter_target_words=1800,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:Ada",
            visibility="world_visible",
            event_type="dialogue",
            content="Stay close.",
        )
        self.agent_interactions.mark_decision_point(
            self.run_dir,
            reason="player must choose whether to enter",
            options=["enter", "hold back"],
        )

        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))

        self.assertEqual(trace["round_id"], "round-000001")
        self.assertEqual(trace["schema_version"], 2)
        self.assertEqual(trace["participants"], ["gm", "player", "character:Ada"])
        self.assertEqual(trace["parallel_groups"], [])
        self.assertEqual(trace["events"][0]["actor"], "character:Ada")
        self.assertEqual(trace["decision_point"]["reason"], "player must choose whether to enter")
        self.assertEqual(trace["status"], "decision_point")

    def test_trace_records_v2_event_links_and_parallel_groups(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:SuLi"],
            chapter_target_words=1800,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:SuLi",
            visibility="world_visible",
            event_type="dialogue_transfer",
            content="I will take it from here.",
            target="character:SuLi",
            source_call_id="call-player-1",
            causal_links=["event-0"],
        )
        self.agent_interactions.record_parallel_group(
            self.run_dir,
            "group-1",
            ["character:A", "character:B"],
        )

        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(trace["schema_version"], 2)
        self.assertEqual(trace["events"][0]["index"], 0)
        self.assertEqual(trace["events"][0]["target"], "character:SuLi")
        self.assertEqual(trace["events"][0]["source_call_id"], "call-player-1")
        self.assertEqual(trace["events"][0]["causal_links"], ["event-0"])
        self.assertEqual(trace["parallel_groups"], [{
            "group_id": "group-1",
            "actors": ["character:A", "character:B"],
        }])
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["parallel_groups"], trace["parallel_groups"])
        self.assertEqual(summary["visible_events"][0], {
            "actor": "character:SuLi",
            "type": "dialogue_transfer",
            "content": "I will take it from here.",
            "target": "character:SuLi",
            "source_call_id": "call-player-1",
            "causal_links": ["event-0"],
        })

    def test_trace_records_actor_batches_and_routing_warnings(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada", "character:Bea"],
            chapter_target_words=1800,
        )

        self.agent_interactions.record_actor_batch(
            self.run_dir,
            batch_id="batch-1-1",
            kind="parallel",
            actors=["character:Ada", "character:Bea"],
            call_ids=["call-character-Ada-1", "call-character-Bea-1"],
            group_id="group-main",
        )
        self.agent_interactions.record_routing_warning(
            self.run_dir,
            code="dependent_call_in_parallel_group",
            message="dependent calls run serially",
            group_id="group-main",
            actors=["character:Ada"],
            call_ids=["call-character-Ada-2"],
        )

        trace = json.loads((self.run_dir / "interaction.trace.json").read_text(encoding="utf-8"))
        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(trace["actor_batches"], [{
            "batch_id": "batch-1-1",
            "kind": "parallel",
            "group_id": "group-main",
            "actors": ["character:Ada", "character:Bea"],
            "call_ids": ["call-character-Ada-1", "call-character-Bea-1"],
        }])
        self.assertEqual(summary["actor_batches"], trace["actor_batches"])
        self.assertEqual(summary["routing_warnings"][0]["code"], "dependent_call_in_parallel_group")
        self.assertEqual(summary["routing_warnings"][0]["group_id"], "group-main")

    def test_trace_summary_sanitizes_hidden_shaped_batch_and_warning_ids(self):
        self.agent_interactions.init_trace(self.run_dir, participants=["gm"])
        self.agent_interactions.record_actor_batch(
            self.run_dir,
            batch_id="HiddenTruthBatch",
            kind="parallel",
            actors=["character:Ada", "HiddenTruthActor"],
            call_ids=["call-character-Ada-1", "worldTruthCall"],
            group_id="HiddenTruthGroup",
        )
        self.agent_interactions.record_routing_warning(
            self.run_dir,
            code="dependent_call_in_parallel_group",
            message="GM-only moon base",
            group_id="HiddenTruthGroup",
            actors=["character:Ada", "HiddenTruthActor"],
            call_ids=["call-character-Ada-1", "worldTruthCall"],
        )

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["actor_batches"], [])
        self.assertEqual(summary["routing_warnings"], [{
            "code": "dependent_call_in_parallel_group",
            "message": "[redacted]",
            "group_id": "",
            "actors": ["character:Ada"],
            "call_ids": ["call-character-Ada-1"],
        }])

    def test_public_api_drops_hidden_shaped_ids_from_story_summary(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "player", "character:SuLi", "character:ClassRep"],
            chapter_target_words=1800,
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:SuLi",
            visibility="world_visible",
            event_type="dialogue_transfer",
            content="I will take it from here.",
            target="character:SuLi",
            source_call_id="call-character-SuLi-2",
            causal_links=[
                "event-0",
                "event-123",
                "call-player-1",
                "call-character-SuLi-2",
                "HiddenTruthMoonBase",
                "GM_ONLY_moon_base",
                "world_truth_foo",
                "out_of_character_note",
            ],
        )
        self.agent_interactions.append_event(
            self.run_dir,
            actor="character:ClassRep",
            visibility="world_visible",
            event_type="reaction",
            content="I keep watch.",
            target="character:ClassRep",
            source_call_id="HiddenTruthMoonBase",
            causal_links=["HiddenTruthGroup"],
        )
        self.agent_interactions.record_parallel_group(
            self.run_dir,
            "group-side-2",
            [
                "player",
                "character:SuLi",
                "character:ClassRep",
                "HiddenTruthGroup",
                "GM_ONLY_moon_base",
                "world_truth_foo",
                "out_of_character_note",
            ],
        )
        self.agent_interactions.record_parallel_group(
            self.run_dir,
            "HiddenTruthGroup",
            ["player"],
        )

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)
        summary_text = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["visible_events"][0]["source_call_id"], "call-character-SuLi-2")
        self.assertEqual(summary["visible_events"][0]["causal_links"], [
            "event-0",
            "event-123",
            "call-player-1",
            "call-character-SuLi-2",
        ])
        self.assertEqual(summary["visible_events"][1]["source_call_id"], "")
        self.assertEqual(summary["visible_events"][1]["causal_links"], [])
        self.assertEqual(summary["parallel_groups"], [{
            "group_id": "group-side-2",
            "actors": ["player", "character:SuLi", "character:ClassRep"],
        }])
        self.assertNotIn("HiddenTruth", summary_text)
        self.assertNotIn("GM_ONLY", summary_text)
        self.assertNotIn("world_truth", summary_text)
        self.assertNotIn("out_of_character", summary_text)

    def test_trace_summary_filters_private_events_for_story_input(self):
        self.agent_interactions.init_trace(
            self.run_dir,
            participants=["gm", "character:Ada"],
            chapter_target_words=1200,
        )
        self.agent_interactions.append_event(self.run_dir, "character:Ada", "private", "thought", "I fear the door.")
        self.agent_interactions.append_event(self.run_dir, "character:Ada", "world_visible", "dialogue", "Stay close.")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["visible_events"][0]["content"], "Stay close.")
        self.assertEqual(summary["visible_events"][0]["target"], "")
        self.assertEqual(summary["visible_events"][0]["source_call_id"], "")
        self.assertEqual(summary["visible_events"][0]["causal_links"], [])
        self.assertEqual(summary["private_event_count"], 1)

    def test_malformed_trace_summarizes_as_invalid(self):
        (self.run_dir / "interaction.trace.json").write_text("{not json", encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["parallel_groups"], [])
        self.assertEqual(summary["status"], "invalid")
        self.assertEqual(summary["visible_events"], [])
        self.assertEqual(summary["private_event_count"], 0)

    def test_summary_falls_back_when_schema_version_is_non_numeric(self):
        trace = {
            "schema_version": {"not": "a number"},
            "round_id": "round-000001",
            "status": "interacting",
            "events": [],
            "parallel_groups": [],
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["schema_version"], 2)

    def test_summary_drops_malformed_causal_links(self):
        trace = {
            "schema_version": 2,
            "round_id": "round-000001",
            "status": "interacting",
            "events": [
                {
                    "actor": "character:Ada",
                    "visibility": "world_visible",
                    "type": "dialogue",
                    "content": "Stay close.",
                    "source_call_id": "call-player-1",
                    "causal_links": "Hidden truth: event-0.",
                },
                {
                    "actor": "character:Ada",
                    "visibility": "world_visible",
                    "type": "gesture",
                    "content": "Ada points at the latch.",
                    "source_call_id": "call-player-2",
                    "causal_links": ["event-0", "GM only: moon base.", 42, {"id": "event-1"}],
                },
            ],
            "parallel_groups": [],
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["visible_events"][0]["causal_links"], [])
        self.assertEqual(summary["visible_events"][1]["causal_links"], ["event-0"])

    def test_summary_drops_malformed_parallel_groups(self):
        trace = {
            "schema_version": 2,
            "round_id": "round-000001",
            "status": "interacting",
            "events": [],
            "parallel_groups": [
                "group-ignored",
                {"group_id": "group-1", "actors": "character:A"},
                {"group_id": "group-2", "actors": ["character:B", 42, {"id": "character:C"}]},
                {"group_id": "Hidden truth: group.", "actors": ["character:D"]},
            ],
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)

        self.assertEqual(summary["parallel_groups"], [
            {"group_id": "group-1", "actors": []},
            {"group_id": "group-2", "actors": ["character:B"]},
        ])

    def test_summary_drops_hidden_text_from_link_and_group_metadata(self):
        trace = {
            "schema_version": 2,
            "round_id": "round-000001",
            "status": "interacting",
            "events": [
                {
                    "actor": "character:SuLi",
                    "visibility": "world_visible",
                    "type": "dialogue_transfer",
                    "content": "I will take it from here.",
                    "target": "character:SuLi",
                    "source_call_id": "GM only: moon base.",
                    "causal_links": ["event-0", "Hidden truth: moon base.", "call-player-1"],
                },
            ],
            "parallel_groups": [
                {"group_id": "Hidden truth: group.", "actors": ["character:A"]},
                {"group_id": "group-1", "actors": ["player", "GM only: moon base.", "character:SuLi"]},
            ],
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)
        summary_text = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["visible_events"][0]["source_call_id"], "")
        self.assertEqual(summary["visible_events"][0]["causal_links"], ["event-0", "call-player-1"])
        self.assertEqual(summary["parallel_groups"], [{
            "group_id": "group-1",
            "actors": ["player", "character:SuLi"],
        }])
        self.assertNotIn("Hidden truth", summary_text)
        self.assertNotIn("GM only", summary_text)
        self.assertNotIn("moon base", summary_text)

    def test_summary_sanitizes_private_decision_and_event_fields(self):
        trace = {
            "round_id": "round-000001",
            "status": "decision_point",
            "chapter_target_words": 1200,
            "events": [
                {
                    "actor": "character:Ada",
                    "visibility": "world_visible",
                    "type": "dialogue",
                    "content": "Stay close.",
                    "private_notes": "Do not leak this.",
                },
                {
                    "actor": "character:Ada",
                    "visibility": "private",
                    "type": "thought",
                    "content": "Secret fear.",
                },
            ],
            "decision_point": {
                "reason": "private tactical reason",
                "public_reason": "player must choose whether to enter",
                "options": ["enter", "wait"],
                "private_options": ["secret route"],
            },
            "stop_reason": "private tactical reason",
            "public_stop_reason": "player must choose whether to enter",
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)
        summary_text = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["visible_events"][0], {
            "actor": "character:Ada",
            "type": "dialogue",
            "content": "Stay close.",
            "target": "",
            "source_call_id": "",
            "causal_links": [],
        })
        self.assertEqual(summary["decision_point"]["reason"], "player must choose whether to enter")
        self.assertEqual(summary["stop_reason"], "player must choose whether to enter")
        self.assertNotIn("Do not leak this", summary_text)
        self.assertNotIn("Secret fear", summary_text)
        self.assertNotIn("private tactical reason", summary_text)

    def test_summary_omits_private_decision_fields_without_public_copy(self):
        trace = {
            "round_id": "round-000001",
            "status": "decision_point",
            "events": [],
            "decision_point": {
                "reason": "private tactical reason",
                "options": ["secret route"],
            },
            "stop_reason": "private tactical reason",
        }
        (self.run_dir / "interaction.trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

        summary = self.agent_interactions.summarize_for_story_input(self.run_dir)
        summary_text = json.dumps(summary, ensure_ascii=False)

        self.assertIsNone(summary["decision_point"])
        self.assertEqual(summary["stop_reason"], "")
        self.assertNotIn("private tactical reason", summary_text)
        self.assertNotIn("secret route", summary_text)
