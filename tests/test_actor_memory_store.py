import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SELF_PROFILE = "我记得自己的名字。"
SU = "苏"
LI = "黎"
SULI = SU + LI
SULI_PROFILE = SULI + "的人设"
GM_LINE = "你听见旧门轴在响。"
SELF_LINE = "我停在门口。"
GM_MEMORY_LINE = "记忆的回声：" + GM_LINE
SELF_MEMORY_LINE = "我：" + SELF_LINE
CONTROL_PLANE_LINE = "开局已送达前端 -> http://localhost:8765，可以在浏览器中输入下一步行动。"
OLD_ARCHIVE = "旧档案室"
KEY_SUMMARY = "我曾在旧档案室发现封存名册。"
KEY_DETAIL = "那本名册记录了苏黎失踪前最后一次登记。"
RECALL_FULLWIDTH = "我想回忆：" + OLD_ARCHIVE
RECALL_ASCII = "我想回忆: " + OLD_ARCHIVE
OLD_SHORT_TERM = "有人对我说：旧短期记忆。\n"
LONG_TERM_UPDATE = "我长期记得档案室的潮湿气味。"
KEY_UPDATE_SUMMARY = "我发现封存名册。"
KEY_UPDATE_DETAIL = "名册里有苏黎最后一次登记。"


def _load_actor_memory_store():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        "actor_memory_store",
        ROOT / "skills" / "agents" / "actor" / "memory_store.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActorMemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.store = _load_actor_memory_store()

    def tearDown(self):
        self.tmp.cleanup()

    def test_player_maps_through_player_md_to_current_character_dir(self):
        (self.card / "characters").mkdir(parents=True)
        (self.card / "characters" / "player.md").write_text(
            "name: 雨蒙\npath: characters/雨蒙\n",
            encoding="utf-8",
        )
        paths = self.store.ensure_actor_files(self.card, "player", profile=SELF_PROFILE)

        self.assertEqual(paths.name, "雨蒙")
        self.assertEqual(paths.actor_dir, self.card / "characters" / "雨蒙")
        self.assertEqual(paths.objective_dir, self.card / "memory" / "characters" / "雨蒙")
        self.assertTrue((self.card / "characters" / "雨蒙" / "profile.md").exists())
        self.assertTrue((self.card / "memory" / "characters" / "雨蒙" / "profile.md").exists())
        self.assertTrue((self.card / "memory" / "characters" / "雨蒙" / "background.md").exists())
        self.assertTrue((self.card / "memory" / "characters" / "雨蒙" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / "player").exists())
        self.assertFalse((self.card / "characters" / "_self").exists())

        memory = self.store.read_actor_memory(self.card, "player")

        self.assertEqual(memory["name"], "雨蒙")
        self.assertIn(SELF_PROFILE, memory["profile"])

    def test_actor_paths_exposes_expected_public_paths(self):
        (self.card / "characters").mkdir(parents=True)
        (self.card / "characters" / "player.md").write_text(
            "name: 雨蒙\npath: characters/雨蒙\n",
            encoding="utf-8",
        )
        paths = self.store.actor_paths(self.card, "player")
        expected = {
            "card": self.card,
            "actor_id": "player",
            "name": "雨蒙",
            "actor_dir": self.card / "characters" / "雨蒙",
            "objective_dir": self.card / "memory" / "characters" / "雨蒙",
            "profile": self.card / "characters" / "雨蒙" / "profile.md",
            "long_term": self.card / "characters" / "雨蒙" / "long_term_memories.md",
            "key_memories": self.card / "characters" / "雨蒙" / "key_memories.json",
            "short_term": self.card / "characters" / "雨蒙" / "short_term_memories.md",
            "objective_profile": self.card / "memory" / "characters" / "雨蒙" / "profile.md",
            "background": self.card / "memory" / "characters" / "雨蒙" / "background.md",
            "objective_recent": self.card / "memory" / "characters" / "雨蒙" / "recent.md",
        }

        for attr, expected_path in expected.items():
            self.assertTrue(hasattr(paths, attr), attr)
            self.assertEqual(getattr(paths, attr), expected_path)
        self.store.ensure_actor_files(self.card, "player", profile=SELF_PROFILE)
        paths.profile.write_text(SELF_PROFILE, encoding="utf-8")
        self.assertEqual(paths.profile.read_text(encoding="utf-8"), SELF_PROFILE)

    def test_rename_player_placeholder_preserves_identity_memory_and_mapping(self):
        paths = self.store.ensure_actor_files(self.card, "player", profile=SELF_PROFILE)
        paths.long_term.write_text(LONG_TERM_UPDATE + "\n", encoding="utf-8")
        paths.short_term.write_text(OLD_SHORT_TERM, encoding="utf-8")
        paths.key_memories.write_text(
            json.dumps(
                {"memories": [{"tag": OLD_ARCHIVE, "summary": KEY_SUMMARY, "detail": KEY_DETAIL}]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        result = self.store.rename_character_identity(self.card, "player", "雨蒙")

        self.assertEqual(result["from_name"], "player")
        self.assertEqual(result["to_name"], "雨蒙")
        self.assertTrue(result["player_mapping_updated"])
        self.assertFalse((self.card / "characters" / "player").exists())
        self.assertFalse((self.card / "memory" / "characters" / "player").exists())
        self.assertEqual(
            (self.card / "characters" / "player.md").read_text(encoding="utf-8"),
            "name: 雨蒙\npath: characters/雨蒙\n",
        )
        renamed = self.store.actor_paths(self.card, "player")
        self.assertEqual(renamed.name, "雨蒙")
        self.assertEqual(renamed.long_term.read_text(encoding="utf-8"), LONG_TERM_UPDATE + "\n")
        self.assertEqual(renamed.short_term.read_text(encoding="utf-8"), OLD_SHORT_TERM)
        self.assertEqual(
            json.loads(renamed.key_memories.read_text(encoding="utf-8"))["memories"][0]["tag"],
            OLD_ARCHIVE,
        )

    def test_non_placeholder_player_mapping_removes_stale_player_dirs(self):
        stale_actor = self.card / "characters" / "player"
        stale_objective = self.card / "memory" / "characters" / "player"
        stale_actor.mkdir(parents=True)
        stale_objective.mkdir(parents=True)
        (stale_actor / "short_term_memories.md").write_text("stale placeholder memory\n", encoding="utf-8")
        (stale_objective / "recent.md").write_text("stale objective memory\n", encoding="utf-8")

        result = self.store.write_player_mapping(self.card, "Yumeng", "characters/Yumeng")

        self.assertEqual(result["path"], "characters/Yumeng")
        self.assertFalse(stale_actor.exists())
        self.assertFalse(stale_objective.exists())

    def test_rename_non_player_character_does_not_switch_player_mapping(self):
        (self.card / "characters").mkdir(parents=True)
        (self.card / "characters" / "player.md").write_text(
            "name: 雨蒙\npath: characters/雨蒙\n",
            encoding="utf-8",
        )
        self.store.ensure_actor_files(self.card, "player", profile=SELF_PROFILE)
        suli_paths = self.store.ensure_actor_files(self.card, f"character:{SULI}", profile=SULI_PROFILE)
        suli_paths.long_term.write_text("苏黎记得自己的旧名。\n", encoding="utf-8")

        result = self.store.rename_character_identity(self.card, SULI, "苏璃")

        self.assertFalse(result["player_mapping_updated"])
        self.assertEqual(
            (self.card / "characters" / "player.md").read_text(encoding="utf-8"),
            "name: 雨蒙\npath: characters/雨蒙\n",
        )
        self.assertFalse((self.card / "characters" / SULI).exists())
        self.assertTrue((self.card / "characters" / "苏璃").is_dir())
        self.assertEqual(
            (self.card / "characters" / "苏璃" / "long_term_memories.md").read_text(encoding="utf-8"),
            "苏黎记得自己的旧名。\n",
        )

    def test_rename_character_rejects_existing_non_placeholder_target(self):
        self.store.ensure_actor_files(self.card, "character:Ada")
        target = self.store.ensure_actor_files(self.card, "character:Bert")
        target.long_term.write_text("Bert already has memories.\n", encoding="utf-8")

        with self.assertRaisesRegex(self.store.ActorMemoryStoreError, "target_character_exists"):
            self.store.rename_character_identity(self.card, "Ada", "Bert")

    def test_actor_name_mapping_avoids_reserved_empty_and_player_collisions(self):
        self.assertEqual(self.store.actor_paths(self.card, "player").name, "player")
        self.assertEqual(self.store.actor_paths(self.card, "").name, "player")
        self.assertEqual(self.store.actor_paths(self.card, "character:").name, "_unknown_character")
        self.assertEqual(self.store.actor_paths(self.card, "character:.").name, "_unknown_character")
        self.assertEqual(self.store.actor_paths(self.card, "character:   ").name, "_unknown_character")
        self.assertEqual(self.store.actor_paths(self.card, "character:_self").name, "character__self")
        self.assertNotEqual(self.store.actor_paths(self.card, "gm").name, "_self")
        self.assertEqual(self.store.canonical_actor_id("character:Ada//Zero"), "character:Ada_Zero")
        self.assertEqual(self.store.canonical_actor_id("player"), "player")

        for actor_id in ("character:_SELF", "character:_Self", "_SELF"):
            with self.subTest(actor_id=actor_id):
                name = self.store.actor_paths(self.card, actor_id).name
                self.assertNotEqual(name, "_self")
                self.assertNotEqual(name.casefold(), "_self")

        reserved_names = ("CON", "prn", "AUX", "nul", "COM1", "com9", "LPT1", "lpt9")
        for reserved in reserved_names:
            with self.subTest(reserved=reserved):
                name = self.store.actor_paths(self.card, f"character:{reserved}").name
                self.assertNotEqual(name.casefold(), reserved.casefold())
                self.assertNotEqual(name, "_self")
                self.assertNotRegex(name, r'[\\/:*?"<>|]')

    def test_character_initialization_creates_new_files_without_legacy_files(self):
        paths = self.store.ensure_actor_files(self.card, f"character:{SU}<{LI}>|?", profile=SULI_PROFILE)

        self.assertNotRegex(paths.name, r'[\\/:*?"<>|]')
        self.assertIn(SU, paths.name)
        self.assertIn(LI, paths.name)
        self.assertTrue((paths.actor_dir / "profile.md").exists())
        self.assertTrue((paths.actor_dir / "long_term_memories.md").exists())
        self.assertTrue((paths.actor_dir / "key_memories.json").exists())
        self.assertTrue((paths.actor_dir / "short_term_memories.md").exists())
        self.assertTrue((paths.objective_dir / "profile.md").exists())
        self.assertTrue((paths.objective_dir / "background.md").exists())
        self.assertTrue((paths.objective_dir / "recent.md").exists())
        self.assertEqual(
            json.loads((paths.actor_dir / "key_memories.json").read_text(encoding="utf-8")),
            {"memories": []},
        )

        for legacy in ("profile.json", "state.json", "goals.md", "goals.json"):
            self.assertFalse((paths.actor_dir / legacy).exists())
            self.assertFalse((paths.objective_dir / legacy).exists())

    def test_read_actor_memory_uses_subjective_root_files_not_legacy_memory_files(self):
        paths = self.store.ensure_actor_files(self.card, "player", profile=SELF_PROFILE)
        paths.long_term.write_text("new subjective long term\n", encoding="utf-8")
        paths.key_memories.write_text(
            json.dumps(
                {
                    "memories": [
                        {
                            "tag": "new-key",
                            "summary": "new subjective summary",
                            "detail": "new subjective detail",
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        paths.short_term.write_text("new subjective short term\n", encoding="utf-8")

        legacy_character = self.card / "memory" / "characters" / "_self"
        legacy_character.mkdir(parents=True, exist_ok=True)
        (legacy_character / "long_term.md").write_text("legacy character long term\n", encoding="utf-8")
        (legacy_character / "key_memories.md").write_text("legacy character key memory\n", encoding="utf-8")
        (legacy_character / "short_term.md").write_text("legacy character short term\n", encoding="utf-8")
        legacy_player = self.card / "memory" / "player"
        legacy_player.mkdir(parents=True, exist_ok=True)
        (legacy_player / "long_term.md").write_text("legacy player long term\n", encoding="utf-8")
        (legacy_player / "key_memories.md").write_text("legacy player key memory\n", encoding="utf-8")
        (legacy_player / "short_term.md").write_text("legacy player short term\n", encoding="utf-8")

        memory = self.store.read_actor_memory(self.card, "player")

        self.assertEqual(memory["long_term"], "new subjective long term\n")
        self.assertEqual(memory["short_term"], "new subjective short term\n")
        self.assertEqual(
            memory["key_memories"],
            [{"tag": "new-key", "summary": "new subjective summary", "detail": "new subjective detail"}],
        )
        self.assertNotIn("legacy", memory["long_term"])
        self.assertNotIn("legacy", memory["short_term"])
        self.assertNotIn("legacy", json.dumps(memory["key_memories"]))

    def test_read_actor_memory_does_not_create_empty_scaffold(self):
        memory = self.store.read_actor_memory(self.card, "character:Ada")

        self.assertEqual(memory["name"], "Ada")
        self.assertEqual(memory["profile"], "")
        self.assertFalse((self.card / "characters" / "Ada").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada").exists())

    def test_append_short_term_dialogue_deduplicates_by_source_id(self):
        self.assertTrue(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "gm",
                GM_LINE,
                source_id="call-1",
            )
        )
        self.assertFalse(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "subGM",
                GM_LINE,
                source_id="call-1",
            )
        )
        self.assertTrue(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "player",
                SELF_LINE,
                source_id="reply-1",
            )
        )

        text = self.store.actor_paths(self.card, "player").short_term.read_text(encoding="utf-8")
        self.assertEqual(text.count(GM_LINE), 1)
        self.assertIn(GM_MEMORY_LINE, text)
        self.assertIn(SELF_MEMORY_LINE, text)
        self.assertIn(GM_MEMORY_LINE + "\n\n" + SELF_MEMORY_LINE + "\n", text)
        self.assertNotIn("有人对我说：", text)
        self.assertNotIn("我回应：", text)
        self.assertFalse(self.store.append_short_term_dialogue(self.card, "player", "gm", "", source_id="empty"))

    def test_append_short_term_dialogue_rejects_control_plane_delivery_prose(self):
        self.assertTrue(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "gm",
                GM_LINE,
                source_id="call-1",
            )
        )
        self.assertFalse(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "player",
                CONTROL_PLANE_LINE,
                source_id="reply-1",
            )
        )
        self.assertTrue(
            self.store.append_short_term_dialogue(
                self.card,
                "player",
                "player",
                SELF_LINE,
                source_id="reply-1",
            )
        )

        text = self.store.actor_paths(self.card, "player").short_term.read_text(encoding="utf-8")
        self.assertIn(GM_MEMORY_LINE, text)
        self.assertIn(SELF_MEMORY_LINE, text)
        self.assertNotIn("localhost:8765", text)
        self.assertNotIn("送达前端", text)

    def test_recall_key_memory_matches_natural_query_with_fullwidth_and_ascii_colons(self):
        self.store.ensure_actor_files(self.card, "player")
        key_path = self.store.actor_paths(self.card, "player").key_memories
        key_path.write_text(
            json.dumps(
                {
                    "memories": [
                        {
                            "tag": OLD_ARCHIVE,
                            "summary": KEY_SUMMARY,
                            "detail": KEY_DETAIL,
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        fullwidth = self.store.recall_key_memory(self.card, "player", RECALL_FULLWIDTH)
        ascii_colon = self.store.recall_key_memory(self.card, "player", RECALL_ASCII)
        plain_query = self.store.recall_key_memory(self.card, "player", OLD_ARCHIVE)

        self.assertEqual(fullwidth["tag"], OLD_ARCHIVE)
        self.assertIn("封存名册", fullwidth["summary"])
        self.assertIn("最后一次登记", fullwidth["detail"])
        self.assertEqual(ascii_colon, fullwidth)
        self.assertEqual(plain_query, fullwidth)

    def test_damaged_key_memories_json_raises_store_error(self):
        self.store.ensure_actor_files(self.card, "player")
        key_path = self.store.actor_paths(self.card, "player").key_memories
        key_path.write_text("{bad json", encoding="utf-8")

        with self.assertRaises(self.store.ActorMemoryStoreError):
            self.store.read_actor_memory(self.card, "player")

    def test_source_files_do_not_contain_mojibake_protocol_text(self):
        mojibake_markers = (
            chr(0x93B4),
            chr(0x6D63),
            chr(0x93C8),
            chr(0x93C3),
            chr(0x947B),
            chr(0x699B),
            chr(0x704F),
            chr(0x935A),
            chr(0x97EA),
            chr(0x20AC),
        )
        for relative_path in ("skills/agents/actor/memory_store.py", "tests/test_actor_memory_store.py"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                for marker in mojibake_markers:
                    self.assertNotIn(marker, text)

    def test_validate_memory_update_rejects_long_term_and_key_memory_limits(self):
        with self.assertRaisesRegex(ValueError, "long_term_memories"):
            self.store.validate_memory_update(
                {
                    "long_term_memories": "a" * 1001,
                    "key_memories": [],
                }
            )

        cases = [
            {"tag": "t" * 21, "summary": "summary", "detail": ""},
            {"tag": "tag", "summary": "s" * 101, "detail": ""},
            {"tag": "tag", "summary": "summary", "detail": "d" * 601},
            {"tag": "", "summary": "summary", "detail": ""},
            {"tag": "tag", "summary": "", "detail": ""},
        ]
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    self.store.validate_memory_update(
                        {
                            "long_term_memories": "",
                            "key_memories": [item],
                        }
                    )

        with self.assertRaisesRegex(ValueError, "at most 16"):
            self.store.validate_memory_update(
                {
                    "long_term_memories": "",
                    "key_memories": [
                        {"tag": str(index), "summary": "summary", "detail": ""}
                        for index in range(17)
                    ],
                }
            )

    def test_apply_memory_update_writes_long_key_and_clears_short_term(self):
        self.store.ensure_actor_files(self.card, f"character:{SULI}")
        short_term = self.card / "characters" / SULI / "short_term_memories.md"
        short_term.write_text(OLD_SHORT_TERM, encoding="utf-8")
        self.assertTrue(
            self.store.append_short_term_dialogue(
                self.card,
                f"character:{SULI}",
                "gm",
                GM_LINE,
                source_id="call-before-summary",
            )
        )
        source_ledger = self.card / "characters" / SULI / ".short_term_sources.json"
        self.assertEqual(
            json.loads(source_ledger.read_text(encoding="utf-8")),
            {"source_ids": ["call-before-summary"]},
        )

        result = self.store.apply_memory_update(
            self.card,
            f"character:{SULI}",
            {
                "long_term_memories": LONG_TERM_UPDATE,
                "key_memories": [
                    {
                        "tag": OLD_ARCHIVE,
                        "summary": KEY_UPDATE_SUMMARY,
                        "detail": KEY_UPDATE_DETAIL,
                    }
                ],
            },
        )

        self.assertEqual(result["name"], SULI)
        self.assertEqual(
            (self.card / "characters" / SULI / "long_term_memories.md").read_text(encoding="utf-8"),
            LONG_TERM_UPDATE + "\n",
        )
        key_payload = json.loads((self.card / "characters" / SULI / "key_memories.json").read_text(encoding="utf-8"))
        self.assertEqual(key_payload["memories"][0]["tag"], OLD_ARCHIVE)
        self.assertEqual(short_term.read_text(encoding="utf-8"), "")
        self.assertEqual(json.loads(source_ledger.read_text(encoding="utf-8")), {"source_ids": []})

    def test_short_term_write_retries_transient_replace_permission_error(self):
        self.store.ensure_actor_files(self.card, f"character:{SULI}")
        original_replace = self.store.os.replace
        attempts = {"count": 0}

        def flaky_replace(src, dst):
            if attempts["count"] == 0:
                attempts["count"] += 1
                raise PermissionError("temporary Windows file lock")
            return original_replace(src, dst)

        with mock.patch.object(self.store.os, "replace", side_effect=flaky_replace):
            self.assertTrue(
                self.store.append_short_term_dialogue(
                    self.card,
                    f"character:{SULI}",
                    "gm",
                    GM_LINE,
                    source_id="call-after-lock",
                )
            )

        short_term = self.card / "characters" / SULI / "short_term_memories.md"
        self.assertIn(GM_MEMORY_LINE, short_term.read_text(encoding="utf-8"))
        self.assertEqual(attempts["count"], 1)

    def test_apply_memory_update_validation_failure_preserves_short_term(self):
        self.store.ensure_actor_files(self.card, "player")
        short_term = self.store.actor_paths(self.card, "player").short_term
        short_term.write_text(OLD_SHORT_TERM, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.apply_memory_update(
                self.card,
                "player",
                {
                    "long_term_memories": "a" * 1001,
                    "key_memories": [],
                },
            )

        self.assertEqual(short_term.read_text(encoding="utf-8"), OLD_SHORT_TERM)


if __name__ == "__main__":
    unittest.main()
