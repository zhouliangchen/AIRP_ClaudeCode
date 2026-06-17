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
        (self.root / "skills" / "styles" / "response.txt").write_text("<content>hi</content>", encoding="utf-8")

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

    def test_cli_prints_json_result(self):
        (self.root / "skills" / "styles" / "response.txt").write_text("<content>hi</content>", encoding="utf-8")

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
