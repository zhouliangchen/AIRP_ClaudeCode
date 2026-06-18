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
        self.assertTrue(payload["visibility_guard"]["redacted_actor_call"])
        self.assertIn("SuLi", payload["promotions"]["promoted"])
        self.assertTrue(payload["structured_memory"]["character:SuLi"])
        self.assertEqual(payload["manifest_stage"], "delivered")
        self.assertEqual(payload["delivery"]["mode"], "agent_run")
        self.assertEqual(payload["trace"]["private_event_count"], 3)
        self.assertEqual(len(payload["trace"]["visible_events"]), 2)
        self.assertIn("player", payload["memory_summary"]["ingested"])
        self.assertEqual(payload["input_analysis"]["analysis_mode"], "fixture")


if __name__ == "__main__":
    unittest.main()
