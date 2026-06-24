import contextlib
import importlib.util
import io
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


class RoundPrepareInputSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.card = self.base / "card"
        self.styles = self.root / "skills" / "styles"
        self.card.mkdir(parents=True)
        self.styles.mkdir(parents=True)
        (self.card / "chat_log.json").write_text("[]", encoding="utf-8")
        (self.card / ".card_data.json").write_text(
            json.dumps({"mode": "blank_bootstrap", "source_type": "blank"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.styles / "settings.json").write_text("{}", encoding="utf-8")

        self.handler = _load_module("handler")
        self.old_styles = self.handler.STYLES
        self.handler.STYLES = self.styles

    def tearDown(self):
        self.handler.STYLES = self.old_styles
        self.tmp.cleanup()

    def test_pending_user_turn_overrides_stale_global_input_txt(self):
        stale_text = "我想找苏黎问问我身上正在发生的情况。"
        role_text = "我叫雨蒙，一名普通的高一男生。"
        instruction_text = "作品基调：日式轻小说风格。"
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        (self.styles / "input.txt").write_text(stale_text, encoding="utf-8")

        entry = self.handler.record_player_input(
            str(self.card),
            raw_text,
            role_text,
            role_text=role_text,
            user_instruction_text=instruction_text,
            input_schema="dual_channel_v1",
        )
        self.handler.write_pending_user_turn(
            str(self.card),
            role_text,
            raw_text=raw_text,
            input_id=entry["id"],
            role_text=role_text,
            user_instruction_text=instruction_text,
            input_schema="dual_channel_v1",
        )

        round_prepare = _load_module("round_prepare")
        round_prepare.write_progress = lambda *args, **kwargs: None
        old_argv = sys.argv
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(self.root)]
            with contextlib.redirect_stdout(io.StringIO()):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        current = (self.card / ".agent_runs" / "current").read_text(encoding="utf-8").strip()
        run_dir = Path(current)
        raw_record = json.loads((run_dir / "input.raw.json").read_text(encoding="utf-8"))
        input_record = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        round_context = (self.styles / "round_context.txt").read_text(encoding="utf-8")

        self.assertEqual(raw_record["raw_text"], raw_text)
        self.assertEqual(raw_record["role_text"], role_text)
        self.assertEqual(raw_record["user_instruction_text"], instruction_text)
        self.assertEqual(input_record["id"], entry["id"])
        self.assertEqual(input_record["raw_text"], raw_text)
        self.assertEqual(input_record["routed_input"]["role_channel"], role_text)
        self.assertEqual(input_record["routed_input"]["user_instruction_channel"], instruction_text)
        self.assertEqual((self.styles / "input.txt").read_text(encoding="utf-8"), raw_text)
        self.assertIn(role_text, round_context)
        self.assertNotIn(stale_text, round_context)

    def test_round_prepare_does_not_create_dispatcher_runtime(self):
        (self.styles / "input.txt").write_text("我推开教室门。", encoding="utf-8")

        round_prepare = _load_module("round_prepare")
        round_prepare.write_progress = lambda *args, **kwargs: None
        old_argv = sys.argv
        stdout = io.StringIO()
        try:
            sys.argv = ["round_prepare.py", str(self.card), str(self.root)]
            with contextlib.redirect_stdout(stdout):
                round_prepare.main()
        finally:
            sys.argv = old_argv

        payload = json.loads(stdout.getvalue())
        self.assertNotIn("dispatcher_runtime", payload)
        run_dir = Path(payload["agent_run"])
        intent_files = list((run_dir / "intents").glob("*/*.json"))
        self.assertEqual(intent_files, [])
