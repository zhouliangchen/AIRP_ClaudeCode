import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneSmokeTest(unittest.TestCase):
    def test_control_plane_smoke_reports_delivery_evidence(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "skills" / "control_plane_smoke.py"), "--repo", str(ROOT)],
            text=True,
            capture_output=True,
            check=True,
        )

        payload = json.loads(result.stdout)

        self.assertTrue(payload["ok"])
        self.assertIn("subgm", payload)
        self.assertEqual(payload["subgm"]["started_count"], 2)
        self.assertEqual(payload["subgm"]["completed_count"], 1)
        self.assertEqual(payload["subgm"]["paused_count"], 1)
        self.assertTrue(payload["subgm"]["player_excluded"])
        self.assertTrue(payload["subgm"]["promotion_blocked"])
        threads = payload["subgm"]["threads"]
        self.assertEqual(threads["side_suli_rooftop"]["status"], "completed")
        self.assertIn(
            "The rooftop clue is complete and ready to merge.",
            threads["side_suli_rooftop"]["last_message"]["content"],
        )
        self.assertEqual(threads["side_gate_noise"]["status"], "paused")
        self.assertEqual(
            threads["side_gate_noise"]["next_resume_point"],
            "resume when the main scene moves toward the school gate",
        )
        results = payload["subgm"]["results"]
        self.assertEqual(
            [item["thread_id"] for item in results],
            ["side_gate_noise", "side_suli_rooftop"],
        )
        for item in results:
            self.assertEqual(set(item), {"ok", "thread_id", "status", "steps", "called_actors"})
            self.assertTrue(item["ok"])
            self.assertEqual(item["steps"], 1)
            self.assertEqual(item["called_actors"], [])
        self.assertEqual(
            {item["thread_id"]: item["status"] for item in results},
            {"side_gate_noise": "paused", "side_suli_rooftop": "completed"},
        )
        self.assertTrue(payload["visibility_guard"]["redacted_actor_call"])
        self.assertIn("SuLi", payload["promotions"]["promoted"])
        self.assertTrue(payload["structured_memory"]["character:SuLi"])
        self.assertEqual(payload["manifest_stage"], "delivered")
        self.assertEqual(payload["delivery"]["mode"], "agent_run")
        self.assertEqual(payload["story"]["character_dialogue_source_agents"], ["character:Ada"])
        self.assertTrue(payload["story"]["character_dialogues_source_backed"])
        self.assertEqual(payload["trace"]["private_event_count"], 3)
        self.assertEqual(len(payload["trace"]["visible_events"]), 2)
        self.assertIn("player", payload["memory_summary"]["ingested"])
        self.assertEqual(payload["input_analysis"]["analysis_mode"], "fixture")


if __name__ == "__main__":
    unittest.main()
