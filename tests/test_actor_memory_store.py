import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SELF_PROFILE = "\u6211\u8bb0\u5f97\u81ea\u5df1\u7684\u540d\u5b57\u3002"
SU = "\u82cf"
LI = "\u9ece"
SULI = SU + LI
SULI_PROFILE = SULI + "\u7684\u4eba\u8bbe"
GM_LINE = "\u4f60\u542c\u89c1\u65e7\u95e8\u8f74\u5728\u54cd\u3002"
SELF_LINE = "\u6211\u505c\u5728\u95e8\u53e3\u3002"
GM_MEMORY_LINE = "\u8bb0\u5fc6\u7684\u56de\u58f0\uff1a" + GM_LINE
SELF_MEMORY_LINE = "\u6211\uff1a" + SELF_LINE
CONTROL_PLANE_LINE = "\u5f00\u5c40\u5df2\u9001\u8fbe\u524d\u7aef -> http://localhost:8765\uff0c\u53ef\u4ee5\u5728\u6d4f\u89c8\u5668\u4e2d\u8f93\u5165\u4e0b\u4e00\u6b65\u884c\u52a8\u3002"
OLD_ARCHIVE = "\u65e7\u6863\u6848\u5ba4"
KEY_SUMMARY = "\u6211\u66fe\u5728\u65e7\u6863\u6848\u5ba4\u53d1\u73b0\u5c01\u5b58\u540d\u518c\u3002"
KEY_DETAIL = "\u90a3\u672c\u540d\u518c\u8bb0\u5f55\u4e86\u82cf\u9ece\u5931\u8e2a\u524d\u6700\u540e\u4e00\u6b21\u767b\u8bb0\u3002"
RECALL_FULLWIDTH = "\u6211\u60f3\u56de\u5fc6\uff1a" + OLD_ARCHIVE
RECALL_ASCII = "\u6211\u60f3\u56de\u5fc6: " + OLD_ARCHIVE
OLD_SHORT_TERM = "\u6709\u4eba\u5bf9\u6211\u8bf4\uff1a\u65e7\u77ed\u671f\u8bb0\u5fc6\u3002\n"
LONG_TERM_UPDATE = "\u6211\u957f\u671f\u8bb0\u5f97\u6863\u6848\u5ba4\u7684\u6f6e\u6e7f\u6c14\u5473\u3002"
KEY_UPDATE_SUMMARY = "\u6211\u53d1\u73b0\u5c01\u5b58\u540d\u518c\u3002"
KEY_UPDATE_DETAIL = "\u540d\u518c\u91cc\u6709\u82cf\u9ece\u6700\u540e\u4e00\u6b21\u767b\u8bb0\u3002"


def _load_actor_memory_store():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        "actor_memory_store",
        ROOT / "skills" / "actor_memory_store.py",
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
        self.assertNotIn("\u6709\u4eba\u5bf9\u6211\u8bf4\uff1a", text)
        self.assertNotIn("\u6211\u56de\u5e94\uff1a", text)
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
        self.assertNotIn("\u9001\u8fbe\u524d\u7aef", text)

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
        self.assertIn("\u5c01\u5b58\u540d\u518c", fullwidth["summary"])
        self.assertIn("\u6700\u540e\u4e00\u6b21\u767b\u8bb0", fullwidth["detail"])
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
            "\u93b4",
            "\u6d63",
            "\u93c8",
            "\u93c3",
            "\u947b",
            "\u699b",
            "\u704f",
            "\u935a",
            "\u97ea",
            "\u20ac",
        )
        for relative_path in ("skills/actor_memory_store.py", "tests/test_actor_memory_store.py"):
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
