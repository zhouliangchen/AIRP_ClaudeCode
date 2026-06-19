import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from skills import rp_bootstrap


class RpBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.card = self.root / "card"
        (self.root / ".claude" / "skills").mkdir(parents=True)
        (self.root / "skills" / "styles").mkdir(parents=True)
        self.card.mkdir(parents=True)
        (self.root / "CLAUDE.md").write_text("# test\n", encoding="utf-8")
        (self.card / "chat_log.json").write_text("[]", encoding="utf-8")
        self.commands = []

    def tearDown(self):
        self.tmp.cleanup()

    def _runner(self, command):
        self.commands.append([str(part) for part in command])
        return 0

    def test_prefilled_empty_card_delivers_opening(self):
        (self.root / "skills" / "styles" / ".card_path").write_text(str(self.card.resolve()), encoding="utf-8")
        (self.root / "skills" / "styles" / "response.txt").write_text("<content>hi</content>", encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps({"first_mes": "hi", "data": {"first_mes": "hi"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = rp_bootstrap.bootstrap(self.card, self.root, run_command=self._runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "opening_delivered")
        self.assertEqual(Path(result["root"]), self.root)
        self.assertEqual(Path(result["card"]), self.card)
        self.assertTrue(any("start_server.py" in " ".join(command) for command in self.commands))
        self.assertTrue(any("handler.py" in " ".join(command) and "--opening" in command for command in self.commands))

    def test_pending_input_prepares_and_generates_round_after_starting_server(self):
        (self.root / "skills" / "styles" / ".pending").write_text("1", encoding="utf-8")

        result = rp_bootstrap.bootstrap(self.card, self.root, run_command=self._runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "turn_generated")
        self.assertIn("already been generated", result["instruction"])
        self.assertTrue(any("round_prepare.py" in " ".join(command) for command in self.commands))
        self.assertTrue(any("rp_generate_cli.py" in " ".join(command) for command in self.commands))

    def test_running_server_without_pending_input_imports_requested_blank_card_before_waiting(self):
        stale_card = self.root / "stale_card"
        requested_card = self.root / "requested_blank_card"
        stale_card.mkdir()
        requested_card.mkdir()
        card_path = self.root / "skills" / "styles" / ".card_path"
        card_path.write_text(str(stale_card), encoding="utf-8")

        def runner(command):
            self.commands.append([str(part) for part in command])
            if any("import_prepare.py" in str(part) for part in command):
                card_path.write_text(str(Path(command[2]).resolve()), encoding="utf-8")
            return 0

        result = rp_bootstrap.bootstrap(requested_card, self.root, run_command=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "waiting_for_player_input")
        self.assertEqual(card_path.read_text(encoding="utf-8"), str(requested_card.resolve()))
        self.assertTrue(any("import_prepare.py" in " ".join(command) for command in self.commands))
        self.assertFalse(any("handler.py" in " ".join(command) for command in self.commands))

    def test_stale_global_response_for_blank_requested_card_imports_before_waiting(self):
        stale_card = self.root / "stale_card"
        requested_card = self.root / "requested_blank_card"
        stale_card.mkdir()
        requested_card.mkdir()
        card_path = self.root / "skills" / "styles" / ".card_path"
        card_path.write_text(str(stale_card.resolve()), encoding="utf-8")
        (self.root / "skills" / "styles" / "response.txt").write_text("<content>stale</content>", encoding="utf-8")

        result = rp_bootstrap.bootstrap(requested_card, self.root, run_command=self._runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "waiting_for_player_input")
        self.assertTrue(any("import_prepare.py" in " ".join(command) for command in self.commands))
        self.assertFalse(any("handler.py" in " ".join(command) and "--opening" in " ".join(command) for command in self.commands))

    def test_repeated_blank_bootstrap_does_not_deliver_stale_global_response(self):
        stale_card = self.root / "stale_card"
        requested_card = self.root / "requested_blank_card"
        stale_card.mkdir()
        requested_card.mkdir()
        card_path = self.root / "skills" / "styles" / ".card_path"
        response_path = self.root / "skills" / "styles" / "response.txt"
        card_path.write_text(str(stale_card.resolve()), encoding="utf-8")
        response_path.write_text("<content>stale</content>", encoding="utf-8")

        def runner(command):
            self.commands.append([str(part) for part in command])
            if any("import_prepare.py" in str(part) for part in command):
                card_path.write_text(str(Path(command[2]).resolve()), encoding="utf-8")
                (requested_card / ".card_data.json").write_text(
                    json.dumps({"mode": "blank_bootstrap", "source_type": "blank"}, ensure_ascii=False),
                    encoding="utf-8",
                )
            return 0

        first = rp_bootstrap.bootstrap(requested_card, self.root, run_command=runner)
        second = rp_bootstrap.bootstrap(requested_card, self.root, run_command=runner)

        self.assertTrue(first["ok"])
        self.assertEqual(first["action"], "waiting_for_player_input")
        self.assertTrue(second["ok"])
        self.assertEqual(second["action"], "waiting_for_player_input")
        self.assertFalse(any("handler.py" in " ".join(command) and "--opening" in command for command in self.commands))

    def test_existing_idle_save_rebuilds_requested_card_without_import_or_generation(self):
        stale_card = self.root / "stale_card"
        stale_card.mkdir()
        card_path = self.root / "skills" / "styles" / ".card_path"
        card_path.write_text(str(stale_card), encoding="utf-8")
        (self.card / "chat_log.json").write_text(
            json.dumps([
                {
                    "index": 0,
                    "ai": "<content><p>Existing story.</p></content>",
                    "summary": "Existing story",
                }
            ]),
            encoding="utf-8",
        )

        def runner(command):
            self.commands.append([str(part) for part in command])
            if any("handler.py" in str(part) for part in command):
                card_path.write_text(str(Path(command[2]).resolve()), encoding="utf-8")
            return 0

        result = rp_bootstrap.bootstrap(self.card, self.root, run_command=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "waiting_for_player_input")
        self.assertEqual(card_path.read_text(encoding="utf-8"), str(self.card.resolve()))
        self.assertTrue(any("handler.py" in " ".join(command) and "--rebuild" in command for command in self.commands))
        self.assertFalse(any("import_prepare.py" in " ".join(command) for command in self.commands))
        self.assertFalse(any("round_prepare.py" in " ".join(command) for command in self.commands))
        self.assertFalse(any("rp_generate_cli.py" in " ".join(command) for command in self.commands))
        self.assertFalse(any("handler.py" in " ".join(command) and "--rebuild" not in command for command in self.commands))

    def test_cli_prints_json_result(self):
        (self.root / "skills" / "styles" / ".card_path").write_text(str(self.card.resolve()), encoding="utf-8")
        (self.root / "skills" / "styles" / "response.txt").write_text("<content>hi</content>", encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps({"first_mes": "hi", "data": {"first_mes": "hi"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        payload = rp_bootstrap.main([str(self.card), str(self.root)], run_command=self._runner)

        data = json.loads(payload)
        self.assertEqual(data["action"], "opening_delivered")
        self.assertEqual(data["return_codes"], [0, 0])

    def test_cli_entrypoint_prints_final_bootstrap_json(self):
        (self.root / "skills" / "styles" / ".pending").write_text("1", encoding="utf-8")
        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["rp_bootstrap.py", str(self.card), str(self.root)]
            with contextlib.redirect_stdout(stdout):
                payload = rp_bootstrap.main(run_command=self._runner)
        finally:
            sys.argv = old_argv

        printed = stdout.getvalue()
        self.assertIn('"action": "turn_generated"', printed)
        self.assertEqual(json.loads(payload)["action"], "turn_generated")
