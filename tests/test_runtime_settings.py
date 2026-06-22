import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class RuntimeSettingsTest(unittest.TestCase):
    def setUp(self):
        import importlib
        self.mod = importlib.import_module("runtime_settings")

    def test_normalize_settings_keeps_only_supported_keys(self):
        raw = {
            "style": "轻松活泼",
            "nsfw": "舒缓",
            "person": "第三人称",
            "antiImpersonation": False,
            "bgNpc": True,
            "charName": "旧主角",
            "wordCount": "1200",
            "selfRepairMode": "full",
            "allowSourceCodeSelfRepair": True,
            "modelDebugMode": True,
            "unknown": "drop me",
        }
        result = self.mod.normalize_settings(raw)
        self.assertEqual(list(result.keys()), [
            "style",
            "wordCount",
            "nsfw",
            "selfRepairMode",
            "allowSourceCodeSelfRepair",
        ])
        self.assertEqual(result, {
            "style": "轻松活泼",
            "wordCount": 1200,
            "nsfw": "舒缓",
            "selfRepairMode": "full",
            "allowSourceCodeSelfRepair": True,
        })
        self.assertNotIn("person", result)
        self.assertNotIn("antiImpersonation", result)
        self.assertNotIn("bgNpc", result)
        self.assertNotIn("charName", result)
        self.assertNotIn("modelDebugMode", result)

    def test_normalize_settings_defaults_invalid_values(self):
        result = self.mod.normalize_settings({
            "style": "",
            "nsfw": "",
            "wordCount": "bad",
            "selfRepairMode": "danger",
            "allowSourceCodeSelfRepair": "yes",
            "modelDebugMode": "no",
        })
        self.assertEqual(result["style"], "北棱特调")
        self.assertEqual(result["wordCount"], 600)
        self.assertEqual(result["nsfw"], "直白")
        self.assertEqual(result["selfRepairMode"], "limited")
        self.assertFalse(result["allowSourceCodeSelfRepair"])
        self.assertNotIn("modelDebugMode", result)

    def test_normalize_settings_accepts_nsfw_off_option(self):
        result = self.mod.normalize_settings({"nsfw": "关闭"})
        self.assertEqual(result["nsfw"], "关闭")

    def test_read_settings_accepts_utf8_bom_and_filters_unsupported_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            payload = json.dumps(
                {
                    "style": "北棱特调",
                    "wordCount": 1800,
                    "charName": "旧主角",
                    "modelDebugMode": True,
                },
                ensure_ascii=False,
            )
            path.write_bytes(payload.encode("utf-8-sig"))
            result = self.mod.read_settings(path)
        self.assertEqual(result["style"], "北棱特调")
        self.assertEqual(result["wordCount"], 1800)
        self.assertNotIn("charName", result)
        self.assertNotIn("modelDebugMode", result)

    def test_load_style_profile_reads_selected_json_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            presets = Path(tmp) / "presets"
            presets.mkdir()
            (presets / "轻松活泼.json").write_text(
                json.dumps(
                    {"name": "轻松活泼", "title": "轻快标题", "content": "轻快说明。"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.mod.load_style_profile(presets, "轻松活泼")
        self.assertEqual(result["name"], "轻松活泼")
        self.assertEqual(result["title"], "轻快标题")
        self.assertIn("轻快说明", result["content"])
        self.assertEqual(result["warning"], "")

    def test_load_style_profile_falls_back_when_json_preset_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            presets = Path(tmp) / "presets"
            presets.mkdir()
            (presets / "北棱特调.json").write_text(
                json.dumps({"name": "北棱特调", "content": "默认说明。"}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = self.mod.load_style_profile(presets, "不存在")
        self.assertEqual(result["name"], "北棱特调")
        self.assertEqual(result["title"], "北棱特调")
        self.assertIn("默认说明", result["content"])
        self.assertIn("missing", result["warning"])

    def test_count_words_and_chinese_chars_are_deterministic(self):
        text = "你推开门。Take cover now."
        self.assertEqual(self.mod.count_chinese_chars(text), 4)
        self.assertEqual(self.mod.count_words(text), 7)

    def test_build_quality_metrics_counts_visible_content_and_player_decision_exemption(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "artifacts").mkdir()
            (run_dir / "artifacts" / "interaction.trace.json").write_text(
                json.dumps(
                    {"stop_reason": "player_decision", "decision_point": {"reason": "choose"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            story = {"content": "<content>你推开门。Take cover now.</content>"}
            settings = self.mod.normalize_settings({"wordCount": 1000})
            metrics = self.mod.build_quality_metrics(
                run_dir,
                settings,
                {"name": "北棱特调", "warning": ""},
                story,
            )
        self.assertEqual(metrics["word_count"]["target"], 1000)
        self.assertEqual(metrics["word_count"]["minimum"], 800)
        self.assertEqual(metrics["word_count"]["current"], 7)
        self.assertTrue(metrics["word_count"]["exempted"])
        self.assertEqual(metrics["chinese_char_count"]["current"], 4)
        self.assertEqual(metrics["visible_content"]["text"], "你推开门。Take cover now.")
        self.assertEqual(metrics["output_perspective"]["expected"], "second_person")

    def test_build_quality_metrics_uses_root_trace_when_artifacts_trace_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "interaction.trace.json").write_text(
                json.dumps({"stop_reason": "player_decision"}, ensure_ascii=False),
                encoding="utf-8",
            )
            settings = self.mod.normalize_settings({"wordCount": 1000})
            metrics = self.mod.build_quality_metrics(
                run_dir,
                settings,
                {"name": "北棱特调", "warning": ""},
                {"content": "<content>Door opens.</content>"},
            )
        self.assertTrue(metrics["word_count"]["exempted"])

    def test_build_quality_metrics_uses_root_trace_when_artifacts_trace_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "artifacts").mkdir()
            (run_dir / "artifacts" / "interaction.trace.json").write_text(
                "{invalid json",
                encoding="utf-8",
            )
            (run_dir / "interaction.trace.json").write_text(
                json.dumps({"decision_point": {"reason": "choose"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            settings = self.mod.normalize_settings({"wordCount": 1000})
            metrics = self.mod.build_quality_metrics(
                run_dir,
                settings,
                {"name": "北棱特调", "warning": ""},
                {"content": "<content>Door opens.</content>"},
            )
        self.assertTrue(metrics["word_count"]["exempted"])


if __name__ == "__main__":
    unittest.main()
