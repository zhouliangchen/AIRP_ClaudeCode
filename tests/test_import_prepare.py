import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_import_prepare():
    skills_dir = ROOT / "skills"
    if str(skills_dir) not in sys.path:
        sys.path.insert(0, str(skills_dir))
    spec = importlib.util.spec_from_file_location("import_prepare", skills_dir / "import_prepare.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_prepare"] = module
    spec.loader.exec_module(module)
    return module


def _load_import_card():
    skills_dir = ROOT / "skills"
    if str(skills_dir) not in sys.path:
        sys.path.insert(0, str(skills_dir))
    spec = importlib.util.spec_from_file_location("import_card", skills_dir / "import_card.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_card"] = module
    spec.loader.exec_module(module)
    return module


class ImportPrepareTest(unittest.TestCase):
    def setUp(self):
        self.import_prepare = _load_import_prepare()
        self.tmp = tempfile.TemporaryDirectory()
        self.styles_dir = Path(self.tmp.name) / "skills" / "styles"
        self.styles_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cleanup_residual_clears_stale_progress_state(self):
        (self.styles_dir / ".pending").write_text("", encoding="utf-8")
        (self.styles_dir / "progress.json").write_text(
            '{"stage":"retry","label":"质检未通过，等待修复","percent":65}',
            encoding="utf-8",
        )

        result = self.import_prepare.cleanup_residual(self.styles_dir)

        self.assertTrue(result["stale_pending_cleared"])
        self.assertTrue(result["stale_progress_cleared"])
        self.assertFalse((self.styles_dir / "progress.json").exists())

    def test_extract_openings_sanitizes_tagged_first_message(self):
        import_card = _load_import_card()
        card_data = {
            "data": {
                "first_mes": (
                    "<content>\n"
                    "<p>第一段剧情。</p>\n"
                    "<p>第二段剧情。</p>\n"
                    "</content>\n"
                    "<summary>不应进入正文或标签</summary>"
                )
            }
        }

        openings = import_card.extract_openings(card_data)

        self.assertEqual(openings[0]["label"], "第一段剧情。")
        self.assertIn("<p>第一段剧情。</p>", openings[0]["content"])
        self.assertIn("<p>第二段剧情。</p>", openings[0]["content"])
        self.assertNotIn("<content>", openings[0]["content"])
        self.assertNotIn("<summary>", openings[0]["content"])

    def test_run_import_prefills_response_without_summary_or_options_tags(self):
        import_card = _load_import_card()
        card_dir = Path(self.tmp.name) / "json_card"
        card_dir.mkdir()
        (card_dir / "card.json").write_text(
            json.dumps(
                {
                    "name": "Json Card",
                    "data": {
                        "name": "Json Card",
                        "first_mes": "<p>第一段剧情。</p>",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = import_card.run_import(str(card_dir), self.tmp.name)

        self.assertEqual(result["source_type"], "json")
        response_text = (self.styles_dir / "response.txt").read_text(encoding="utf-8")
        self.assertIn("<content>", response_text)
        self.assertNotIn("<summary>", response_text)
        self.assertNotIn("<options>", response_text)

    def test_run_import_creates_missing_blank_card_folder(self):
        import_card = _load_import_card()
        card_dir = Path(self.tmp.name) / "new_blank_card"

        result = import_card.run_import(str(card_dir), self.tmp.name)

        self.assertEqual(result["source_type"], "blank")
        self.assertTrue(card_dir.is_dir())
        self.assertTrue((card_dir / ".card_data.json").exists())
        self.assertTrue((card_dir / "memory").is_dir())

    def test_run_import_continues_existing_save_without_treating_runtime_json_as_card(self):
        import_card = _load_import_card()
        card_dir = Path(self.tmp.name) / "existing_save"
        card_dir.mkdir()
        (card_dir / ".card_data.json").write_text(
            json.dumps({"name": "Existing Save", "data": {"name": "Existing Save"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (card_dir / "chat_log.json").write_text(
            json.dumps([{"index": 0, "ai": "<p>old</p>"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (card_dir / "ui_manifest.json").write_text("{}", encoding="utf-8")

        result = import_card.run_import(str(card_dir), self.tmp.name)

        self.assertEqual(result["source_type"], "existing_card_data")
        self.assertEqual(result["card_name"], "Existing Save")
        saved = json.loads((card_dir / ".card_data.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["name"], "Existing Save")

    def test_blank_bootstrap_clears_stale_character_name_setting(self):
        settings_path = self.styles_dir / "settings.json"
        settings_path.write_text(
            '{"charName":"旧主角","wordCount":4000,"selfRepairMode":"full"}',
            encoding="utf-8",
        )

        result = self.import_prepare.clear_card_scoped_settings_for_blank_bootstrap(
            self.styles_dir,
            blank_bootstrap=True,
        )

        self.assertTrue(result["charName_cleared"])
        settings = self.import_prepare.read_json(settings_path)
        self.assertEqual(settings["charName"], "")
        self.assertEqual(settings["wordCount"], 4000)
        self.assertEqual(settings["selfRepairMode"], "full")


if __name__ == "__main__":
    unittest.main()
