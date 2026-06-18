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
