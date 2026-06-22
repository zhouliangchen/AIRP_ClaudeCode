import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class PostprocessOutputTest(unittest.TestCase):
    def setUp(self):
        import importlib

        self.mod = importlib.import_module("postprocess_outputs")

    def test_validate_accepts_core_data(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You reach the observatory door.",
                "options": [
                    "Open the door",
                    {
                        "label": "Listen first",
                        "source": "critic",
                        "requires_confirmation": True,
                    },
                    {"label": "  Check the window  ", "requires_confirmation": False},
                ],
                "current_goal": "Find a safe way inside.",
                "state_patch": {
                    "quest": "Observatory",
                    "stage": "door",
                    "time": "night",
                    "location": "hill",
                    "env": {"weather": "rain"},
                    "actions": ["Open the door", "Listen first"],
                },
            },
            "ui_extensions": {
                "status_panels": {"goal": {"title": "Goal"}},
                "custom_cards": {"map": {"title": "Map"}},
                "asset_bindings": {"scene": {"asset": "door"}},
                "unsafe": {"drop": True},
            },
            "ui_extension_status": {"status": "ok", "issues": []},
            "repair_requests": [{"target": "story"}],
            "metadata": {"round_id": "round-000001"},
        }

        result = self.mod.validate_postprocess_output(payload)

        self.assertTrue(result["ok"])
        output = result["output"]
        self.assertEqual(output["schema_version"], 1)
        self.assertEqual(output["core"]["summary"], "You reach the observatory door.")
        self.assertEqual(
            output["core"]["options"],
            [
                {
                    "label": "Open the door",
                    "source": "postprocess",
                    "requires_confirmation": False,
                },
                {
                    "label": "Listen first",
                    "source": "critic",
                    "requires_confirmation": True,
                },
                {
                    "label": "Check the window",
                    "source": "postprocess",
                    "requires_confirmation": False,
                },
            ],
        )
        self.assertEqual(output["core"]["current_goal"], "Find a safe way inside.")
        self.assertEqual(output["core"]["state_patch"]["location"], "hill")
        self.assertEqual(output["ui_extensions"]["status_panels"], {"goal": {"title": "Goal"}})
        self.assertEqual(output["ui_extensions"]["custom_cards"], {"map": {"title": "Map"}})
        self.assertEqual(output["ui_extensions"]["asset_bindings"], {"scene": {"asset": "door"}})
        self.assertNotIn("unsafe", output["ui_extensions"])
        self.assertEqual(output["ui_extension_status"], {"status": "ok", "issues": []})
        self.assertEqual(output["repair_requests"], [{"target": "story"}])
        self.assertEqual(output["metadata"], {"round_id": "round-000001"})

    def test_validate_rejects_missing_core_fields(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "  ",
                "options": ["", {"label": "   "}, {}],
                "current_goal": "",
            },
        }

        result = self.mod.validate_postprocess_output(payload)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "postprocess_core_invalid")
        self.assertIn("core.summary is required", result["errors"])
        self.assertIn("core.current_goal is required", result["errors"])
        self.assertIn("core.options must include at least one valid option", result["errors"])

    def test_validate_filters_state_patch_to_frontend_safe_keys(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "The scene shifts.",
                "options": ["Continue"],
                "current_goal": "Stay alert.",
                "state_patch": {
                    "quest": "Find Ada",
                    "stage": "arrival",
                    "time": "dawn",
                    "location": "station",
                    "env": {"sound": "train"},
                    "actions": ["Look around", "", "  Ask Ada  ", 123],
                    "world": {"hidden": "do not expose"},
                },
            },
        }

        result = self.mod.validate_postprocess_output(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["output"]["core"]["state_patch"],
            {
                "quest": "Find Ada",
                "stage": "arrival",
                "time": "dawn",
                "location": "station",
                "env": {"sound": "train"},
                "actions": ["Look around", "Ask Ada"],
            },
        )


if __name__ == "__main__":
    unittest.main()
