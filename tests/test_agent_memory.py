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

    def _structured_memory_update(
        self,
        agent_id="player",
        *,
        character_name="",
        self_understanding="I remember the archive door opening under my hand.",
        key_memory="I opened the archive door and noticed Ada's lamp behind me.",
        short_term="I am still near the archive threshold.",
        active_goal="Find the sealed index.",
    ):
        payload = {
            "agent_id": agent_id,
            "character_name": character_name,
            "source": "self",
            "visibility": "actor",
            "long_term": {
                "self_understanding": [self_understanding],
                "stable_beliefs": ["The archive should be explored carefully."],
                "relationship_models": ["Ada notices small changes in the room."],
            },
            "key_memories": [
                {
                    "content": key_memory,
                    "importance": "high",
                    "details": ["The threshold smelled like old paper."],
                }
            ],
            "short_term": [
                {
                    "content": short_term,
                    "expires_after": "scene_end",
                }
            ],
            "goals": {
                "active": [active_goal],
                "paused": [],
                "resolved": [],
            },
        }
        if not character_name and not agent_id.startswith("character:"):
            payload.pop("character_name")
        return payload

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

    def test_actor_memory_rejects_camel_hidden_markers_in_event_content(self):
        for marker, expected in (
            ("gmOnly", "gm_only"),
            ("worldTruth", "world_truth"),
            ("hiddenNote", "hidden_note"),
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(
                    run_dir / "story.input.json",
                    {
                        "round_id": "round-000001",
                        "memory_deltas": {
                            "actors": {
                                "player": [
                                    {
                                        "type": "memory_delta",
                                        "content": f"I should not persist {marker} knowledge.",
                                        "target": "self",
                                    }
                                ]
                            }
                        },
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

                self.assertFalse((card / "memory" / "player" / "recent.md").exists())

    def test_actor_memory_rejects_explicit_hidden_marker_variants_in_event_content(self):
        for marker, expected in (
            ("gmOnly", "gm_only"),
            ("gm-only", "gm_only"),
            ("gm only", "gm_only"),
            ("gm_only", "gm_only"),
            ("worldTruth", "world_truth"),
            ("hiddenNote", "hidden_note"),
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(
                    run_dir / "story.input.json",
                    {
                        "round_id": "round-000001",
                        "memory_deltas": {
                            "actors": {
                                "player": [
                                    {
                                        "type": "memory_delta",
                                        "content": f"I should not persist {marker} knowledge.",
                                        "target": "self",
                                    }
                                ]
                            }
                        },
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

                self.assertFalse((card / "memory" / "player" / "recent.md").exists())

    def test_actor_memory_allows_marker_words_inside_normal_prose(self):
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "memory_deltas": {
                    "actors": {
                        "player": [
                            {
                                "type": "memory_delta",
                                "content": "I met a player named Ada",
                                "target": "self",
                            },
                            {
                                "type": "memory_delta",
                                "content": "I found a hidden notebook",
                                "target": "self",
                            },
                            {
                                "type": "memory_delta",
                                "content": "The world truthfully felt smaller.",
                                "target": "self",
                            },
                        ]
                    }
                },
            },
        )

        try:
            result = self.agent_memory.ingest_memory_deltas(
                self.card,
                self.run_dir,
                date_str="2026-06-16 12:00",
            )
        except self.agent_memory.MemoryIngestionError as exc:
            self.fail(f"normal prose should be accepted: {exc}")

        self.assertEqual(result["ingested"], ["player"])
        player_recent = (self.card / "memory" / "player" / "recent.md").read_text(encoding="utf-8")
        self.assertIn("I met a player named Ada", player_recent)
        self.assertIn("I found a hidden notebook", player_recent)
        self.assertIn("The world truthfully felt smaller.", player_recent)

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

    def test_ingest_memory_deltas_rejects_invalid_empty_actor_id(self):
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": "round-000001",
                "memory_deltas": {
                    "actors": {
                        "gm_only": [],
                    },
                },
            },
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "gm_only"):
            self.agent_memory.ingest_memory_deltas(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / ".agent_memory_ingested.json").exists())

    def test_ingest_memory_deltas_rejects_empty_hidden_marker_actor_branches(self):
        for actor_key, expected in (
            ("character:", "character:"),
            ("character:gmOnly", "gm_only"),
            ("gm_only", "gm_only"),
        ):
            with self.subTest(actor_key=actor_key), tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                run_dir = card / ".agent_runs" / "round-000001"
                _write_json(
                    run_dir / "story.input.json",
                    {
                        "round_id": "round-000001",
                        "memory_deltas": {
                            "actors": {
                                actor_key: [],
                            },
                        },
                    },
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_deltas(card, run_dir)

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

    def test_ingested_ledger_does_not_skip_current_actor_or_world_validation(self):
        self.agent_memory.ingest_memory_deltas(
            self.card,
            self.run_dir,
            date_str="2026-06-16 12:00",
        )
        story_input = json.loads((self.run_dir / "story.input.json").read_text(encoding="utf-8"))

        malformed_actor = json.loads(json.dumps(story_input))
        malformed_actor["memory_deltas"]["actors"]["player"] = [{"text": "old player memory shape"}]
        _write_json(self.run_dir / "story.input.json", malformed_actor)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "type"):
            self.agent_memory.ingest_memory_deltas(
                self.card,
                self.run_dir,
                date_str="2026-06-16 12:01",
            )

        malformed_world = json.loads(json.dumps(story_input))
        malformed_world["memory_deltas"]["world"] = [{"scope": "room"}]
        _write_json(self.run_dir / "story.input.json", malformed_world)

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "fact"):
            self.agent_memory.ingest_memory_deltas(
                self.card,
                self.run_dir,
                date_str="2026-06-16 12:02",
            )

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
        player_prompt_text = player_prompt.read_text(encoding="utf-8")
        character_prompt_text = character_prompt.read_text(encoding="utf-8")
        self.assertIn("first-person memory", player_prompt_text)
        self.assertIn("organization is not compression", player_prompt_text)
        self.assertIn("preserve enough details", player_prompt_text)
        self.assertIn('"long_term"', player_prompt_text)
        self.assertIn('"key_memories"', player_prompt_text)
        self.assertIn('"short_term"', player_prompt_text)
        self.assertIn('"goals"', player_prompt_text)
        self.assertIn("character:Ada", character_prompt_text)

    def test_ingest_memory_summaries_writes_player_and_character_summary_files(self):
        self._write_memory_summary_manifest(
            {
                "player": "memory_summaries/player.summary.json",
                "character:Ada": "memory_summaries/character_Ada.summary.json",
            }
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            self._structured_memory_update(
                "player",
                self_understanding="I remember the archive door opening under my hand.",
                key_memory="I pushed the archive door open while Ada watched.",
                active_goal="Find the sealed index.",
            ),
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            self._structured_memory_update(
                "character:Ada",
                character_name="Ada",
                self_understanding="I watched the player enter the archive and kept my distance.",
                key_memory="I raised my lamp as the player crossed the threshold.",
                active_goal="Decide whether the player can be trusted.",
            ),
        )

        result = self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertEqual(result["ingested"], ["player", "character:Ada"])
        player_long_term = (self.card / "memory" / "player" / "long_term.md").read_text(encoding="utf-8")
        player_key = (self.card / "memory" / "player" / "key_memories.md").read_text(encoding="utf-8")
        player_short = (self.card / "memory" / "player" / "short_term.md").read_text(encoding="utf-8")
        player_goals = json.loads((self.card / "memory" / "player" / "goals.json").read_text(encoding="utf-8"))
        ada_long_term = (self.card / "memory" / "characters" / "Ada" / "long_term.md").read_text(encoding="utf-8")
        ada_key = (self.card / "memory" / "characters" / "Ada" / "key_memories.md").read_text(encoding="utf-8")
        ada_short = (self.card / "memory" / "characters" / "Ada" / "short_term.md").read_text(encoding="utf-8")
        ada_goals = json.loads((self.card / "memory" / "characters" / "Ada" / "goals.json").read_text(encoding="utf-8"))
        self.assertIn("I remember the archive door", player_long_term)
        self.assertIn("I pushed the archive door open", player_key)
        self.assertIn("scene_end", player_short)
        self.assertEqual(player_goals["goals"]["active"], ["Find the sealed index."])
        self.assertIn("I watched the player enter", ada_long_term)
        self.assertIn("I raised my lamp", ada_key)
        self.assertIn("scene_end", ada_short)
        self.assertEqual(ada_goals["goals"]["active"], ["Decide whether the player can be trusted."])
        self.assertFalse((self.card / "memory" / "player" / "summary.md").exists())
        self.assertFalse((self.card / "memory" / "player" / "profile.md").exists())
        self.assertFalse((self.card / "memory" / "player" / "profile.json").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "summary.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "profile.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "profile.json").exists())

    def test_ingest_memory_summaries_removes_recent_delta_after_organization(self):
        player_dir = self.card / "memory" / "player"
        player_dir.mkdir(parents=True, exist_ok=True)
        (player_dir / "recent.md").write_text(
            "# Player Agent Memory\n\n- stale recent player delta\n",
            encoding="utf-8",
        )
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            self._structured_memory_update(
                "player",
                short_term="I have organized the current scene memory.",
            ),
        )

        self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((player_dir / "recent.md").exists())
        short_term = (player_dir / "short_term.md").read_text(encoding="utf-8")
        self.assertIn("I have organized the current scene memory.", short_term)
        self.assertNotIn("stale recent player delta", short_term)

    def test_ingest_memory_summaries_rolls_back_bucket_files_when_later_write_fails(self):
        player_dir = self.card / "memory" / "player"
        player_dir.mkdir(parents=True, exist_ok=True)
        existing_long_term = "existing long term\n"
        existing_recent = "existing recent delta\n"
        (player_dir / "long_term.md").write_text(existing_long_term, encoding="utf-8")
        (player_dir / "recent.md").write_text(existing_recent, encoding="utf-8")
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            self._structured_memory_update("player"),
        )

        original_write_text = self.agent_memory._write_text
        calls = []

        def fail_on_second_bucket(path, text):
            calls.append(Path(path).name)
            if Path(path).name == "key_memories.md":
                raise OSError("simulated summary write failure")
            return original_write_text(path, text)

        self.agent_memory._write_text = fail_on_second_bucket
        try:
            with self.assertRaisesRegex(OSError, "simulated summary write failure"):
                self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)
        finally:
            self.agent_memory._write_text = original_write_text

        self.assertIn("long_term.md", calls)
        self.assertIn("key_memories.md", calls)
        self.assertEqual((player_dir / "long_term.md").read_text(encoding="utf-8"), existing_long_term)
        self.assertEqual((player_dir / "recent.md").read_text(encoding="utf-8"), existing_recent)
        self.assertFalse((player_dir / "key_memories.md").exists())
        self.assertFalse((player_dir / "short_term.md").exists())
        self.assertFalse((player_dir / "goals.json").exists())

    def test_ingest_memory_summaries_rejects_path_payload_agent_mismatch(self):
        self._write_memory_summary_manifest(
            {"character:Ada": "memory_summaries/character_Ada.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            self._structured_memory_update(
                "player",
                self_understanding="I watched the archive from Ada's corner.",
            ),
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "agent_id mismatch"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_character_name_mismatch(self):
        self._write_memory_summary_manifest(
            {"character:Ada": "memory_summaries/character_Ada.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            self._structured_memory_update(
                "character:Ada",
                character_name="Bob",
                self_understanding="I watched the archive from Ada's corner.",
            ),
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "character_name mismatch"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "characters" / "Ada" / "long_term.md").exists())
        self.assertFalse((self.card / "memory" / "characters" / "Bob" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_unknown_extra_summary_files(self):
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            self._structured_memory_update("player"),
        )
        _write_json(
            self.run_dir / "memory_summaries" / "character_Ada.summary.json",
            self._structured_memory_update(
                "character:Ada",
                character_name="Ada",
                self_understanding="I should not be accepted because I was not scheduled.",
            ),
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "unscheduled"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_missing_scheduled_summary_files(self):
        self._write_memory_summary_manifest(
            {
                "player": "memory_summaries/player.summary.json",
                "character:Ada": "memory_summaries/character_Ada.summary.json",
            }
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            self._structured_memory_update("player"),
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "missing scheduled"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_gm_only_visibility(self):
        self._write_memory_summary_manifest(
            {"player": "memory_summaries/player.summary.json"}
        )
        _write_json(
            self.run_dir / "memory_summaries" / "player.summary.json",
            {
                **self._structured_memory_update(
                    "player",
                    self_understanding="I remember the archive door.",
                ),
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
            self._structured_memory_update(
                "player",
                self_understanding="I now remember the world_truth that only the GM should know.",
                active_goal="Track the normal archive map.",
            ),
        )

        with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "world_truth"):
            self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

        self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_world_truth_aliases(self):
        for marker in ["world-truth", "world truth"]:
            with self.subTest(marker=marker):
                if (self.card / "memory" / "player" / "long_term.md").exists():
                    (self.card / "memory" / "player" / "long_term.md").unlink()
                self._write_memory_summary_manifest(
                    {"player": "memory_summaries/player.summary.json"}
                )
                _write_json(
                    self.run_dir / "memory_summaries" / "player.summary.json",
                    self._structured_memory_update(
                        "player",
                        self_understanding=f"I now know the hidden {marker}.",
                    ),
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, "world_truth"):
                    self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

                self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())

    def test_ingest_memory_summaries_rejects_prompt_declared_hidden_markers(self):
        for marker, expected in [
            ("hidden-note", "hidden_note"),
            ("out-of-character", "out_of_character"),
        ]:
            with self.subTest(marker=marker):
                if (self.card / "memory" / "player" / "long_term.md").exists():
                    (self.card / "memory" / "player" / "long_term.md").unlink()
                self._write_memory_summary_manifest(
                    {"player": "memory_summaries/player.summary.json"}
                )
                _write_json(
                    self.run_dir / "memory_summaries" / "player.summary.json",
                    self._structured_memory_update(
                        "player",
                        self_understanding=f"I should not persist {marker} knowledge.",
                    ),
                )

                with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, expected):
                    self.agent_memory.ingest_memory_summaries(self.card, self.run_dir)

                self.assertFalse((self.card / "memory" / "player" / "long_term.md").exists())


if __name__ == "__main__":
    unittest.main()
