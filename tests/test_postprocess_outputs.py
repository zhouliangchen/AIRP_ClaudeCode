import json
import sys
import tempfile
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
            "mvu": {
                "commands": [
                    "<UpdateVariable><JSONPatch>[{\"op\":\"replace\",\"path\":\"/mood\",\"value\":\"alert\"}]</JSONPatch></UpdateVariable>",
                    "   ",
                    123,
                ],
                "status": "ok",
                "issues": [],
            },
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
        self.assertEqual(
            output["mvu"],
            {
                "commands": [
                    "<UpdateVariable><JSONPatch>[{\"op\":\"replace\",\"path\":\"/mood\",\"value\":\"alert\"}]</JSONPatch></UpdateVariable>"
                ],
                "status": "ok",
                "issues": [],
            },
        )
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

    def test_validate_requires_fixed_option_for_critical_player_action(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You brace at the sealed door.",
                "options": ["Step back and reassess"],
                "current_goal": "Confirm the risky action before continuing.",
            },
        }
        evidence = [
            {
                "id": "critical-action-1",
                "required_label": "push open the sealed door",
                "risk_level": "critical",
            }
        ]

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertFalse(result["ok"])
        self.assertIn(
            "missing fixed option for critical action: critical-action-1",
            result["errors"],
        )

    def test_validate_accepts_fixed_option_for_critical_player_action(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You brace at the sealed door.",
                "options": [
                    {
                        "label": "Confirm action: push open the sealed door",
                        "source": "player_agent_critical_action",
                        "requires_confirmation": True,
                    }
                ],
                "current_goal": "Confirm the risky action before continuing.",
            },
        }
        evidence = [
            {
                "id": "critical-action-1",
                "required_label": "push open the sealed door",
                "risk_level": "critical",
            }
        ]

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertTrue(result["ok"])

    def test_validate_rejects_plain_postprocess_option_for_critical_player_action(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You brace at the sealed door.",
                "options": [
                    {
                        "label": "Confirm action: push open the sealed door",
                        "source": "postprocess",
                        "requires_confirmation": False,
                    }
                ],
                "current_goal": "Confirm the risky action before continuing.",
            },
        }
        evidence = [
            {
                "id": "critical-action-1",
                "required_label": "push open the sealed door",
                "risk_level": "critical",
            }
        ]

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertFalse(result["ok"])
        self.assertIn(
            "missing fixed option for critical action: critical-action-1",
            result["errors"],
        )

    def test_validate_requires_each_critical_player_action_to_match_option_label(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You face two risky choices.",
                "options": [
                    {
                        "label": "Confirm critical player action",
                        "source": "player_agent_critical_action",
                        "requires_confirmation": True,
                    }
                ],
                "current_goal": "Confirm each risky action before continuing.",
            },
        }
        evidence = [
            {
                "id": "critical-action-1",
                "required_label": "push open the sealed door",
                "risk_level": "critical",
            },
            {
                "id": "critical-action-2",
                "required_label": "pull the alarm lever",
                "risk_level": "critical",
            },
        ]

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertFalse(result["ok"])
        self.assertIn(
            "missing fixed option for critical action: critical-action-1",
            result["errors"],
        )
        self.assertIn(
            "missing fixed option for critical action: critical-action-2",
            result["errors"],
        )

    def test_validate_accepts_two_critical_player_actions_with_two_matching_options(self):
        payload = {
            "schema_version": 1,
            "core": {
                "summary": "You face two risky choices.",
                "options": [
                    {
                        "label": "Confirm action: push open the sealed door",
                        "source": "player_agent_critical_action",
                        "requires_confirmation": True,
                    },
                    {
                        "label": "Confirm action: pull the alarm lever",
                        "source": "player_agent_critical_action",
                        "requires_confirmation": True,
                    },
                ],
                "current_goal": "Confirm each risky action before continuing.",
            },
        }
        evidence = [
            {
                "id": "critical-action-1",
                "required_label": "push open the sealed door",
                "risk_level": "critical",
            },
            {
                "id": "critical-action-2",
                "required_label": "pull the alarm lever",
                "risk_level": "critical",
            },
        ]

        result = self.mod.validate_postprocess_output(payload, critical_action_evidence=evidence)

        self.assertTrue(result["ok"])

    def test_record_ui_extension_repair_writes_run_artifact_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "card"
            run_dir = card / ".agent_runs" / "round-000001"
            run_dir.mkdir(parents=True)

            record = self.mod.record_ui_extension_repair(
                run_dir,
                card,
                reason="missing relationship panel data",
                required_keys=["ui_extensions.status_panels.relationships"],
                source_artifacts=["artifacts/postprocess.output.json"],
            )

            repair_path = run_dir / "artifacts" / "postprocess_repairs" / f"{record['id']}.json"
            self.assertTrue(repair_path.exists())
            artifact = json.loads(repair_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["schema_version"], 1)
            self.assertEqual(artifact["id"], record["id"])
            self.assertEqual(artifact["round_id"], "round-000001")
            self.assertEqual(artifact["status"], "pending")
            self.assertEqual(artifact["scope"], "ui_extensions")
            self.assertEqual(artifact["reason"], "missing relationship panel data")
            self.assertEqual(
                artifact["required_keys"],
                ["ui_extensions.status_panels.relationships"],
            )
            self.assertEqual(artifact["source_artifacts"], ["artifacts/postprocess.output.json"])
            self.assertEqual(artifact["attempts"], 1)

            queue_path = card / ".agent_runs" / "postprocess_repair_queue.jsonl"
            queue_items = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(queue_items, [artifact])

    def test_record_postprocess_contract_repair_writes_contract_scope_queue_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "card"
            run_dir = card / ".agent_runs" / "round-000001"
            run_dir.mkdir(parents=True)

            record = self.mod.record_postprocess_contract_repair(
                run_dir,
                card,
                reason="ui schema data contract missing",
                required_keys=["ui_extensions.status_panels.relationships"],
                source_artifacts=["artifacts/assets_tasks/intent-1.json", "ui_manifest.json"],
            )

            repair_path = run_dir / "artifacts" / "postprocess_repairs" / f"{record['id']}.json"
            self.assertTrue(repair_path.exists())
            artifact = json.loads(repair_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "pending")
            self.assertEqual(artifact["scope"], "postprocess_contract")
            self.assertEqual(artifact["reason"], "ui schema data contract missing")
            self.assertEqual(
                artifact["required_keys"],
                ["ui_extensions.status_panels.relationships"],
            )
            self.assertEqual(
                artifact["source_artifacts"],
                ["artifacts/assets_tasks/intent-1.json", "ui_manifest.json"],
            )

            queue_path = card / ".agent_runs" / "postprocess_repair_queue.jsonl"
            queue_items = [
                json.loads(line)
                for line in queue_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(queue_items, [artifact])

    def test_read_pending_repairs_only_returns_pending_queue_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            queue_path = card / ".agent_runs" / "postprocess_repair_queue.jsonl"
            queue_path.parent.mkdir(parents=True)
            rows = [
                {"id": "repair-1", "status": "pending", "required_keys": ["ui.a"]},
                {"id": "repair-2", "status": "completed", "required_keys": ["ui.b"]},
                {"id": "repair-3", "status": "pending", "required_keys": ["ui.c"]},
            ]
            queue_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\nnot-json\n",
                encoding="utf-8",
            )

            repairs = self.mod.read_pending_repairs(card)

            self.assertEqual([item["id"] for item in repairs], ["repair-1", "repair-3"])

    def test_ui_extension_repair_helpers_detect_status_and_required_keys(self):
        payload = {
            "ui_extension_status": {
                "status": "needs_repair",
                "issues": [
                    {"key": "ui_extensions.status_panels.relationships", "message": "missing"},
                    "ui_extensions.custom_cards.map",
                    {"message": "no key"},
                ],
            }
        }

        self.assertTrue(self.mod.ui_extensions_need_repair(payload))
        self.assertEqual(
            self.mod.ui_extension_required_keys(payload),
            ["ui_extensions.status_panels.relationships", "ui_extensions.custom_cards.map"],
        )


if __name__ == "__main__":
    unittest.main()
