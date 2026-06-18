import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_agent_memory():
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location("agent_memory", ROOT / "skills" / "agent_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = Path(self.tmp.name) / "card"
        self.run_dir = self.card / ".agent_runs" / "round-000001"
        self.run_dir.mkdir(parents=True)
        self.agent_memory = _load_agent_memory()
        self._write_story_input()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_story_input(self):
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "player_inputs": {"raw_text": "I open the archive door."},
                "memory_deltas": {
                    "actors": {
                        "player": [
                            {
                                "type": "memory_delta",
                                "content": "I opened the archive door and smelled old paper.",
                                "target": "self",
                            }
                        ],
                        "character:Ada": [
                            {
                                "type": "goal_update",
                                "content": "I saw the player enter the archive.",
                                "target": "self",
                            }
                        ],
                    },
                    "world": [
                        {"scope": "room", "fact": "the archive door is open"}
                    ],
                },
            },
        )

    def _write_memory_summary_manifest(self, mapping):
        _write_json(
            self.run_dir / "manifest.json",
            {"expected_outputs": {"memory_summaries": mapping}},
        )

    def test_ingest_memory_deltas_writes_player_character_and_world_memory(self):
        result = self.agent_memory.ingest_memory_deltas(
            self.card,
            self.run_dir,
            date_str="2026-06-16 12:00",
        )

        self.assertEqual(result["ingested"], ["player", "character:Ada", "world"])
        player_recent = (self.card / "memory" / "player" / "recent.md").read_text(encoding="utf-8")
        ada_recent = (self.card / "memory" / "characters" / "Ada" / "recent.md").read_text(encoding="utf-8")
        world_recent = (self.card / "memory" / "world_delta.md").read_text(encoding="utf-8")
        self.assertIn("I opened the archive door", player_recent)
        self.assertIn("I saw the player enter", ada_recent)
        self.assertIn("the archive door is open", world_recent)

    def test_ingest_memory_deltas_is_idempotent_by_round_and_agent(self):
        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:00")
        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:01")

        player_recent = (self.card / "memory" / "player" / "recent.md").read_text(encoding="utf-8")
        ada_recent = (self.card / "memory" / "characters" / "Ada" / "recent.md").read_text(encoding="utf-8")
        self.assertEqual(player_recent.count("I opened the archive door"), 1)
        self.assertEqual(ada_recent.count("I saw the player enter"), 1)

    def test_actor_memory_rejects_gm_only_source(self):
        story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        story_input["memory_deltas"]["actors"]["character:Ada"] = [
            {
                "type": "memory_delta",
                "content": "The hidden vault was never real.",
                "target": "self",
                "source": "gm_only",
            }
        ]
        _write_json(self.run_dir / "story.input.json", story_input)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "gm_only"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

    def test_actor_memory_rejects_extra_source_field_and_writes_nothing(self):
        story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        story_input["memory_deltas"] = {
            "actors": {
                "player": [
                    {
                        "type": "memory_delta",
                        "content": "I saw the archive door open.",
                        "target": "self",
                        "source": "observed",
                    }
                ]
            }
        }
        _write_json(self.run_dir / "story.input.json", story_input)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "source"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / ".agent_memory_ingested.json").exists())

    def test_actor_memory_rejects_forbidden_marker_field_on_event_delta(self):
        story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
        story_input["memory_deltas"]["actors"]["player"] = [
            {
                "type": "memory_delta",
                "content": "I should not persist hidden notes.",
                "target": "self",
                "hidden_note": "private GM-only marker",
            }
        ]
        _write_json(self.run_dir / "story.input.json", story_input)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "hidden_note"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

    def test_actor_memory_rejects_hidden_markers_in_event_content(self):
        for marker, expected in (("world_truth", "world_truth"), ("hidden-note", "hidden_note")):
            with self.subTest(marker=marker):
                story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))
                story_input["memory_deltas"]["actors"]["player"] = [
                    {
                        "type": "memory_delta",
                        "content": f"I should not persist {marker} knowledge.",
                        "target": "self",
                    }
                ]
                _write_json(self.run_dir / "story.input.json", story_input)

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

                self.assertFalse((self.card / "memory" / "player" / "recent.md").exists())

    def test_ingest_memory_deltas_rejects_legacy_player_and_character_branches(self):
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "memory_deltas": {
                    "player": [{"text": "Old player branch should not write."}],
                    "characters": {
                        "Ada": [{"text": "Old character branch should not write."}]
                    },
                },
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "legacy"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "recent.md").exists())

    def test_ingest_memory_deltas_rejects_malformed_actor_and_world_containers(self):
        cases = [
            (
                {"round_id": "round-000001", "memory_deltas": {"actors": []}},
                "memory_deltas.actors",
            ),
            (
                {"round_id": "round-000001", "memory_deltas": {"world": {"scope": "room", "fact": "bad"}}},
                "memory_deltas.world",
            ),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(run_dir / "story.input.json", payload)

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

                self.assertFalse((card / "memory" / "player" / "recent.md").exists())
                self.assertFalse((card / "memory" / "world_delta.md").exists())
                self.assertFalse((card / "memory" / ".agent_memory_ingested.json").exists())

    def test_ingest_memory_deltas_rejects_falsy_malformed_actor_values(self):
        for actor_value in ({}, "", None, 0):
            with self.subTest(actor_value=actor_value), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(
                    run_dir / "story.input.json",
                    {
                        "round_id": "round-000001",
                        "memory_deltas": {
                            "actors": {
                                "player": actor_value,
                            },
                        },
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "memory_deltas.actors.player"):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

                self.assertFalse((card / "memory" / "player" / "recent.md").exists())
                self.assertFalse((card / "memory" / ".agent_memory_ingested.json").exists())

    def test_actor_memory_rejects_old_item_shapes_under_actors(self):
        for item in ({"text": "old player memory"}, {"fact": "old player fact"}, "old player string"):
            with self.subTest(item=item), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(
                    run_dir / "story.input.json",
                    {
                        "round_id": "round-000001",
                        "memory_deltas": {
                            "actors": {
                                "player": [item],
                            },
                        },
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "type"):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

                self.assertFalse((card / "memory" / "player" / "recent.md").exists())
                self.assertFalse((card / "memory" / ".agent_memory_ingested.json").exists())

    def test_ingest_memory_deltas_is_atomic_when_later_actor_fails_validation(self):
        story_input = {
            "round_id": "round-000001",
            "memory_deltas": {
                "actors": {
                    "player": [
                        {
                            "type": "memory_delta",
                            "content": "I opened the archive door.",
                            "target": "self",
                        }
                    ],
                    "character:Ada": [
                        {
                            "type": "memory_delta",
                            "content": "I should not persist world_truth knowledge.",
                            "target": "self",
                        }
                    ],
                },
                "world": [
                    {"scope": "room", "fact": "the archive door is open"}
                ],
            },
        }
        _write_json(self.run_dir / "story.input.json", story_input)

        for _attempt in range(2):
            with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "world_truth"):
                self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:00")

        self.assertFalse((self.card / "memory" / "player" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / "world_delta.md").exists())

    def test_ingest_memory_deltas_rolls_back_first_write_when_later_write_fails(self):
        story_input = {
            "round_id": "round-000001",
            "memory_deltas": {
                "actors": {
                    "player": [
                        {
                            "type": "memory_delta",
                            "content": "I opened the archive door.",
                            "target": "self",
                        }
                    ],
                    "character:Ada": [
                        {
                            "type": "memory_delta",
                            "content": "I watched the player enter.",
                            "target": "self",
                        }
                    ],
                },
            },
        }
        _write_json(self.run_dir / "story.input.json", story_input)

        original_append = self.agent_memory._append_lines
        calls = []

        def fail_after_first_write(path, header, lines):
            calls.append(Path(path))
            if len(calls) == 2:
                raise OSError("simulated write failure")
            return original_append(path, header, lines)

        self.agent_memory._append_lines = fail_after_first_write
        try:
            with self.assertRaisesRegex(OSError, "simulated write failure"):
                self.agent_memory.ingest_memory_deltas(
                    self.card,
                    self.run_dir,
                    date_str="2026-06-16 12:00",
                )
        finally:
            self.agent_memory._append_lines = original_append

        self.assertEqual(len(calls), 2)
        self.assertFalse((self.card / "memory" / "player" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "recent.md").exists())
        self.assertFalse((self.card / "memory" / ".agent_memory_ingested.json").exists())

    def test_ingest_memory_deltas_preserves_player_input_log(self):
        input_log = self.card / ".player_inputs.jsonl"
        original = '{"id":"p1","raw_text":"  keep exact spaces  ","display_text":"  keep exact spaces  "}\n'
        input_log.write_text(original, encoding="utf-8")

        self.agent_memory.ingest_memory_deltas(self.card, self.run_dir, date_str="2026-06-16 12:00")

        self.assertEqual(input_log.read_text(encoding="utf-8"), original)

    def test_memory_summary_due_every_six_rounds(self):
        self.assertFalse(self.agent_memory.memory_summary_due("round-000005"))
        self.assertTrue(self.agent_memory.memory_summary_due("round-000006"))
        self.assertFalse(self.agent_memory.memory_summary_due("opening"))

    def test_write_memory_summary_prompts_records_manifest_outputs(self):
        manifest = {"prompts": {}, "expected_outputs": {}}

        result = self.agent_memory.write_memory_summary_prompts(
            self.card,
            self.run_dir,
            manifest,
            ["player", "character:Ada"],
        )

        player_prompt = self.run_dir / "prompts" / "memory" / "player.prompt.md"
        character_prompt = self.run_dir / "prompts" / "memory" / "character_Ada.prompt.md"
        self.assertTrue(player_prompt.exists())
        self.assertTrue(character_prompt.exists())
        self.assertEqual(
            manifest["expected_outputs"]["memory_summaries"]["player"],
            "memory_summaries/player.summary.json",
        )
        self.assertEqual(
            manifest["expected_outputs"]["memory_summaries"]["character:Ada"],
            "memory_summaries/character_Ada.summary.json",
        )
        self.assertEqual(
            manifest["prompts"]["memory_summaries"]["character:Ada"],
            "prompts/memory/character_Ada.prompt.md",
        )
        self.assertEqual(result["scheduled"], ["player", "character:Ada"])
        self.assertIn("first-person memory", player_prompt.read_text(encoding="utf-8"))
        self.assertIn("character:Ada", character_prompt.read_text(encoding="utf-8"))

    def test_ingest_memory_summaries_writes_player_and_character_summary_files(self):
        self._write_memory_summary_manifest(
            {
                "player": "memory_summaries/player.summary.json",
                "character:Ada": "memory_summaries/character_Ada.summary.json",
            }
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "summary": "I remember the archive door opening under my hand.",
                "retained_goals": ["Find the sealed index."],
                "forgotten_noise": ["Dust motes in the west corner."],
                "source": "self",
                "visibility": "actor",
            },
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            {
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "summary": "I watched the player enter the archive and kept my distance.",
                "retained_goals": ["Decide whether the player can be trusted."],
                "forgotten_noise": [],
                "source": "self",
                "visibility": "actor",
            },
        )

        result = self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertEqual(result["ingested"], ["player", "character:Ada"])
        player_summary = (self.card / "memory" / "player" / "summary.md").read_text(encoding="utf-8")
        ada_summary = (self.card / "memory" / "characters" / "Ada" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("I remember the archive door", player_summary)
        self.assertIn("Find the sealed index.", player_summary)
        self.assertIn("I watched the player enter", ada_summary)
        self.assertIn("Decide whether the player can be trusted.", ada_summary)

    def test_ingest_memory_summaries_rejects_path_payload_agent_mismatch(self):
        self._write_memory_summary_manifest(
            {"character:Ada": "memory_summaries/character_Ada.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            {
                "agent_id": "player",
                "summary": "I watched the archive from Ada's corner.",
                "source": "self",
                "visibility": "actor",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "agent_id mismatch"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_character_name_mismatch(self):
        self._write_memory_summary_manifest(
            {"character:Ada": "memory_summaries/character_Ada.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            {
                "agent_id": "character:Ada",
                "character_name": "Bob",
                "summary": "I watched the archive from Ada's corner.",
                "source": "self",
                "visibility": "actor",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "character_name mismatch"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "summary.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Bob" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_unknown_extra_summary_files(self):
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "summary": "I remember the archive door.",
                "source": "self",
                "visibility": "actor",
            },
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            {
                "agent_id": "character:Ada",
                "summary": "I should not be accepted because I was not scheduled.",
                "source": "self",
                "visibility": "actor",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "unscheduled"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_missing_scheduled_summary_files(self):
        self._write_memory_summary_manifest(
            {
                "player": "memory_summaries/player.summary.json",
                "character:Ada": "memory_summaries/character_Ada.summary.json",
            }
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "summary": "I remember the archive door.",
                "source": "self",
                "visibility": "actor",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "missing scheduled"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_gm_only_visibility(self):
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "summary": "I somehow know the hidden GM-only vault truth.",
                "source": "self",
                "visibility": "gm_only",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "gm_only"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

    def test_ingest_memory_summaries_rejects_hidden_markers_in_persisted_text(self):
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                "agent_id": "player",
                "summary": "I now remember the world_truth that only the GM should know.",
                "retained_goals": ["Track the omniscient map."],
                "forgotten_noise": ["gm_only breadcrumb"],
                "source": "self",
                "visibility": "actor",
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "world_truth"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_world_truth_aliases(self):
        for marker in ["world-truth", "world truth"]:
            with self.subTest(marker=marker):
                if (self.card / "memory" / "player" / "summary.md").exists():
                    (self.card / "memory" / "player" / "summary.md").unlink()
                self._write_memory_summary_manifest(
                    {"player": "memory_summaries/player.summary.json"}
                )
                _write_json(
                    self.run_dir / "memory_summaries" / "player.summary.json",
                    {
                        "agent_id": "player",
                        "summary": f"I now know the hidden {marker}.",
                        "source": "self",
                        "visibility": "actor",
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "world_truth"):
                    self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

                self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())

    def test_ingest_memory_summaries_rejects_prompt_declared_hidden_markers(self):
        for marker, expected in [
            ("hidden-note", "hidden_note"),
            ("out-of-character", "out_of_character"),
        ]:
            with self.subTest(marker=marker):
                if (self.card / "memory" / "player" / "summary.md").exists():
                    (self.card / "memory" / "player" / "summary.md").unlink()
                self._write_memory_summary_manifest(
                    {"player": "memory_summaries/player.summary.json"}
                )
                _write_json(
                    self.run_dir / "memory_summaries" / "player.summary.json",
                    {
                        "agent_id": "player",
                        "summary": f"I should not persist {marker} knowledge.",
                        "source": "self",
                        "visibility": "actor",
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

                self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())


if __name__ == "__main__":
    unittest.main()
