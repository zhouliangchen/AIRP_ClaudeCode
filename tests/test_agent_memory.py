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

    def _write_story_input(self, actor_outputs=None, trace_visible_events=None):
        if actor_outputs is not None:
            _write_json(
                self.run_dir / "manifest.json",
                {
                    "round_id": self.run_dir.name,
                    "stage": "delivered",
                    "expected_outputs": {},
                },
            )
            _write_json(
                self.run_dir / "story.input.json",
                {
                    "round_id": self.run_dir.name,
                    "loop_outputs": {"actors": actor_outputs, "gm": {"outputs": []}},
                    "side_threads": {"threads": []},
                    "memory_deltas": {"actors": {}, "world": []},
                    "interaction_trace": {"visible_events": trace_visible_events or []},
                },
            )
            return

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
        long_term = self_understanding
        if active_goal:
            long_term += f" I want to {active_goal[0].lower() + active_goal[1:]}"
        payload = {
            "agent_id": agent_id,
            "character_name": character_name,
            "long_term_memories": long_term,
            "key_memories": [
                {
                    "tag": "archive door",
                    "summary": key_memory,
                    "detail": "The threshold smelled like old paper.",
                }
            ],
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

    def test_schedule_post_round_memory_jobs_only_participating_actors(self):
        actor_outputs = {
            "player": [
                {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [
                        {
                            "type": "memory_delta",
                            "content": "I opened the archive door.",
                            "target": "self",
                        }
                    ],
                    "stop_reason": "continue",
                }
            ],
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [
                        {
                            "type": "dialogue",
                            "target": "player",
                            "content": "Stay close.",
                        }
                    ],
                    "stop_reason": "continue",
                }
            ],
            "character:Unseen": [],
        }
        trace_visible_events = [
            {
                "type": "dialogue",
                "source_actor": "character:Ada",
                "target_actor": "player",
                "content": "Stay close.",
            }
        ]
        self._write_story_input(actor_outputs, trace_visible_events)

        result = self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["scheduled"], ["character:Ada", "player"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        jobs = manifest["post_round_memory_jobs"]
        self.assertEqual(jobs["status"], "pending")
        self.assertIn("character:Ada", jobs["scheduled"])
        job_path = self.run_dir / "post_round_memory_jobs" / "character_Ada.job.json"
        self.assertTrue(job_path.exists())
        job_payload = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job_payload["agent_id"], "character:Ada")
        self.assertNotIn("actor_outputs", job_payload)
        self.assertNotIn("visible_events", job_payload)
        self.assertIn("round_dialogue", job_payload)
        self.assertIn("short_term_memories", job_payload)
        self.assertIn("long_term_memories", job_payload)
        self.assertIn("key_memory_cues", job_payload)
        self.assertNotIn("user_instruction_channel", json.dumps(job_payload, ensure_ascii=False))

    def test_schedule_post_round_memory_jobs_includes_only_round_dialogue_for_short_term(self):
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": self.run_dir.name,
                "stage": "delivered",
                "expected_outputs": {},
            },
        )
        _write_json(
            self.run_dir / "story.input.json",
            {
                "round_id": self.run_dir.name,
                "loop_outputs": {
                    "gm": {
                        "outputs": [
                            {
                                "agent": "gm",
                                "actor_calls": [
                                    {
                                        "call_id": "call-ada-main",
                                        "actor_id": "character:Ada",
                                        "prompt": "你听见门外有人轻声叫你的名字。",
                                    }
                                ],
                            }
                        ]
                    },
                    "actors": {
                        "character:Ada": [
                            {
                                "agent": "character",
                                "agent_id": "character:Ada",
                                "character_name": "Ada",
                                "events": [
                                    {
                                        "type": "dialogue",
                                        "content": "我把灯压低，轻声回应。",
                                        "source_call_id": "call-ada-main",
                                    }
                                ],
                                "stop_reason": "continue",
                            }
                        ]
                    },
                },
                "side_threads": {
                    "threads": [
                        {
                            "thread_id": "side-door",
                            "subgm_output": {
                                "agent": "subGM",
                                "actor_calls": [
                                    {
                                        "call_id": "call-ada-side",
                                        "actor_id": "character:Ada",
                                        "prompt": "你看到侧门的影子动了一下。",
                                    }
                                ],
                            },
                            "actor_outputs": {
                                "character:Ada": [
                                    {
                                        "agent": "character",
                                        "agent_id": "character:Ada",
                                        "character_name": "Ada",
                                        "events": [
                                            {
                                                "type": "action",
                                                "content": "我把手按在门闩上。",
                                                "source_call_id": "call-ada-side",
                                            }
                                        ],
                                        "stop_reason": "continue",
                                    }
                                ]
                            },
                        }
                    ]
                },
                "memory_deltas": {"actors": {}, "world": []},
                "interaction_trace": {
                    "visible_events": [
                        {
                            "id": "public-rain",
                            "type": "scene",
                            "content": "雨点敲着走廊窗户。",
                            "visible_to": ["all"],
                        }
                    ]
                },
            },
        )

        self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)

        job_payload = json.loads(
            (self.run_dir / "post_round_memory_jobs" / "character_Ada.job.json").read_text(
                encoding="utf-8"
            )
        )
        round_dialogue = json.dumps(job_payload["round_dialogue"], ensure_ascii=False)
        prompt_text = (
            self.run_dir / "prompts" / "post_round_memory" / "character_Ada.prompt.md"
        ).read_text(encoding="utf-8")

        self.assertIn("你听见门外有人轻声叫你的名字。", round_dialogue)
        self.assertIn("我把灯压低，轻声回应。", round_dialogue)
        self.assertIn("你看到侧门的影子动了一下。", round_dialogue)
        self.assertIn("我把手按在门闩上。", round_dialogue)
        self.assertNotIn("雨点敲着走廊窗户", round_dialogue)
        self.assertIn("现在我需要整理一下我的记忆", prompt_text)
        self.assertIn("本轮我和对我说话者的对话", prompt_text)
        self.assertIn("1. 对我说的话：你听见门外有人轻声叫你的名字。", prompt_text)
        self.assertNotIn("Actor-Safe Job Input", prompt_text)
        self.assertNotIn('"round_dialogue"', prompt_text)
        self.assertNotIn('"visible_events"', prompt_text)

    def test_ingest_post_round_memory_jobs_marks_absent_jobs_not_required(self):
        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, self.run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "not_required")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["post_round_memory_jobs"]["status"], "not_required")

    def test_ingest_post_round_memory_jobs_marks_empty_scheduled_not_required(self):
        _write_json(
            self.run_dir / "manifest.json",
            {
                "round_id": self.run_dir.name,
                "post_round_memory_jobs": {
                    "status": "pending",
                    "scheduled": {},
                    "failed": {},
                },
            },
        )

        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, self.run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "not_required")
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["post_round_memory_jobs"]["status"], "not_required")

    def test_ingest_post_round_memory_jobs_writes_memory_and_marks_complete(self):
        actor_outputs = {
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [
                        {
                            "type": "memory_delta",
                            "target": "self",
                            "content": "I heard the archive shelf move.",
                        }
                    ],
                    "stop_reason": "continue",
                }
            ],
        }
        self._write_story_input(actor_outputs, [])
        self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)
        _write_json(
            self.run_dir / "post_round_memory_jobs" / "character_Ada.summary.json",
            {
                "agent_id": "character:Ada",
                "character_name": "Ada",
                "long_term_memories": "I listen carefully when the archive changes.",
                "key_memories": [
                    {
                        "tag": "archive shelf",
                        "summary": "I heard the archive shelf move.",
                        "detail": "The movement came from inside the archive.",
                    }
                ],
            },
        )

        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, self.run_dir)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["ingested"], ["character:Ada"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["post_round_memory_jobs"]["status"], "complete")
        actor_dir = self.card / "characters" / "Ada"
        self.assertIn(
            "I listen carefully",
            (actor_dir / "long_term_memories.md").read_text(encoding="utf-8"),
        )
        key_payload = json.loads((actor_dir / "key_memories.json").read_text(encoding="utf-8"))
        self.assertEqual(key_payload["memories"][0]["summary"], "I heard the archive shelf move.")
        self.assertEqual((actor_dir / "short_term_memories.md").read_text(encoding="utf-8"), "")

    def test_ingest_post_round_memory_jobs_marks_degraded_on_hidden_marker_failure(self):
        actor_outputs = {
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [
                        {
                            "type": "memory_delta",
                            "target": "self",
                            "content": "I heard the archive shelf move.",
                        }
                    ],
                    "stop_reason": "continue",
                }
            ],
        }
        self._write_story_input(actor_outputs, [])
        self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)
        summary = self._structured_memory_update(
            "character:Ada",
            character_name="Ada",
            self_understanding="world_truth says I know too much.",
            active_goal="Stay near the archive shelf.",
        )
        _write_json(self.run_dir / "post_round_memory_jobs" / "character_Ada.summary.json", summary)

        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, self.run_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "degraded_memory_state")
        self.assertIn("character:Ada", result["failed"])
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["post_round_memory_jobs"]["status"], "degraded_memory_state")
        self.assertIn("character:Ada", manifest["post_round_memory_jobs"]["failed"])

    def test_ingest_post_round_memory_jobs_reports_pending_status_for_missing_output(self):
        actor_outputs = {
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [{"type": "action", "content": "I lift the lamp."}],
                    "stop_reason": "continue",
                }
            ],
        }
        self._write_story_input(actor_outputs, [])
        self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)

        result = self.agent_memory.ingest_post_round_memory_jobs(self.card, self.run_dir)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(
            result["missing"],
            {"character:Ada": "post_round_memory_jobs/character_Ada.summary.json"},
        )
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["post_round_memory_jobs"]["status"], "pending")

    def test_schedule_post_round_memory_jobs_excludes_visible_events_from_actor_jobs(self):
        actor_outputs = {
            "player": [
                {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "action", "content": "I hold the door."}],
                    "stop_reason": "continue",
                }
            ],
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [{"type": "action", "content": "I lift the lamp."}],
                    "stop_reason": "continue",
                }
            ],
            "character:SuLi": [
                {
                    "agent": "character",
                    "agent_id": "character:SuLi",
                    "character_name": "SuLi",
                    "events": [{"type": "action", "content": "I check the stairs."}],
                    "stop_reason": "continue",
                }
            ],
        }
        trace_visible_events = [
            {
                "id": "ada-self",
                "type": "action",
                "actor": "character:Ada",
                "content": "Ada lifts the lamp.",
            },
            {
                "id": "target-ada",
                "type": "dialogue",
                "target": "character:Ada",
                "content": "Ada, stay close.",
            },
            {
                "id": "visible-to-ada",
                "type": "perception",
                "visible_to": ["character:Ada"],
                "content": "The lamp flickers near Ada.",
            },
            {
                "id": "metadata-visible-to-ada",
                "type": "perception",
                "visibility_metadata": {"visible_to": ["character:Ada"]},
                "content": "Ada hears a click.",
            },
            {
                "id": "dialogue-ada",
                "type": "dialogue",
                "actor": "character:Ada",
                "target": "player",
                "content": "Stay close.",
            },
            {
                "id": "action-ada",
                "type": "action",
                "actor": "character:Ada",
                "target": "player",
                "content": "Ada blocks the threshold.",
            },
            {
                "id": "public-top-level-all",
                "type": "scene",
                "visible_to": ["all"],
                "content": "Dust falls across the public hallway.",
            },
            {
                "id": "public-basis-all",
                "type": "scene",
                "visibility_basis": {"mode": "public", "visible_to": ["all"]},
                "content": "Everyone hears the archive bell.",
            },
            {
                "id": "public-metadata-all",
                "type": "scene",
                "visibility_metadata": {"visible_to": ["all"]},
                "content": "The public lamp flares.",
            },
            {
                "id": "public-mode",
                "type": "scene",
                "visibility_basis": {"mode": "public"},
                "content": "The corridor becomes visibly brighter.",
            },
            {
                "id": "suli-only",
                "type": "perception",
                "actor": "character:SuLi",
                "target": "character:SuLi",
                "visible_to": ["character:SuLi"],
                "content": "SuLi notices dust upstairs.",
            },
        ]
        self._write_story_input(actor_outputs, trace_visible_events)

        self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)

        ada_job = json.loads(
            (self.run_dir / "post_round_memory_jobs" / "character_Ada.job.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("visible_events", ada_job)
        ada_dialogue = json.dumps(ada_job["round_dialogue"], ensure_ascii=False)
        self.assertIn("I lift the lamp.", ada_dialogue)
        self.assertNotIn("SuLi notices dust upstairs.", ada_dialogue)
        player_job = json.loads(
            (self.run_dir / "post_round_memory_jobs" / "player.job.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("visible_events", player_job)
        player_dialogue = json.dumps(player_job["round_dialogue"], ensure_ascii=False)
        self.assertIn("I hold the door.", player_dialogue)
        self.assertNotIn("SuLi notices dust upstairs.", player_dialogue)

    def test_schedule_post_round_memory_jobs_rejects_actor_unsafe_hidden_markers(self):
        markers = ("user_instruction_channel", "hidden_fact", "hidden_text", "private_notes")
        cases = (
            ("actor_outputs", lambda marker: ({marker: "leaked hidden channel"}, {})),
            ("visible_events", lambda marker: ({}, {marker: "leaked hidden channel"})),
        )
        for marker in markers:
            for location, build_payloads in cases:
                with self.subTest(marker=marker, location=location):
                    run_dir = self.card / ".agent_runs" / f"round-hidden-{marker}-{location}"
                    run_dir.mkdir(parents=True, exist_ok=True)
                    actor_extra, visible_extra = build_payloads(marker)
                    actor_outputs = {
                        "player": [
                            {
                                "agent": "player",
                                "agent_id": "player",
                                "events": [
                                    {
                                        "type": "action",
                                        "content": "I wait by the archive door.",
                                    }
                                ],
                                "stop_reason": "continue",
                                **actor_extra,
                            }
                        ],
                    }
                    visible_events = [
                        {
                            "type": "action",
                            "source_actor": "player",
                            "content": "I wait by the archive door.",
                            **visible_extra,
                        }
                    ]
                    _write_json(
                        run_dir / "manifest.json",
                        {
                            "round_id": run_dir.name,
                            "stage": "delivered",
                            "expected_outputs": {},
                        },
                    )
                    _write_json(
                        run_dir / "story.input.json",
                        {
                            "round_id": run_dir.name,
                            "loop_outputs": {"actors": actor_outputs, "gm": {"outputs": []}},
                            "side_threads": {"threads": []},
                            "memory_deltas": {"actors": {}, "world": []},
                            "interaction_trace": {"visible_events": visible_events},
                        },
                    )

                    with self.assertRaisesRegex(self.agent_memory.MemoryIngestionError, marker):
                        self.agent_memory.schedule_post_round_memory_jobs(self.card, run_dir)

                    self.assertFalse((run_dir / "post_round_memory_jobs" / "player.job.json").exists())

    def test_schedule_post_round_memory_jobs_rolls_back_artifacts_when_prompt_write_fails(self):
        actor_outputs = {
            "character:Ada": [
                {
                    "agent": "character",
                    "agent_id": "character:Ada",
                    "character_name": "Ada",
                    "events": [{"type": "action", "content": "I lift the lamp."}],
                    "stop_reason": "continue",
                }
            ],
            "player": [
                {
                    "agent": "player",
                    "agent_id": "player",
                    "events": [{"type": "action", "content": "I hold the door."}],
                    "stop_reason": "continue",
                }
            ],
        }
        self._write_story_input(actor_outputs, [])
        original_manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        original_write_text = self.agent_memory._write_text

        def fail_prompt_write(path, text):
            if "post_round_memory" in path.as_posix():
                raise OSError("simulated prompt write failure")
            return original_write_text(path, text)

        self.agent_memory._write_text = fail_prompt_write
        try:
            with self.assertRaisesRegex(OSError, "simulated prompt write failure"):
                self.agent_memory.schedule_post_round_memory_jobs(self.card, self.run_dir)
        finally:
            self.agent_memory._write_text = original_write_text

        self.assertFalse((self.run_dir / "post_round_memory_jobs" / "character_Ada.job.json").exists())
        self.assertFalse((self.run_dir / "post_round_memory_jobs" / "player.job.json").exists())
        self.assertFalse((self.run_dir / "prompts" / "post_round_memory" / "character_Ada.prompt.md").exists())
        manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, original_manifest)

    def test_previous_post_round_memory_state_ignores_non_current_round_dirs(self):
        stale_run = self.card / ".agent_runs" / "round-old"
        _write_json(
            stale_run / "manifest.json",
            {
                "round_id": "round-old",
                "post_round_memory_jobs": {
                    "status": "pending",
                    "scheduled": {"player": {"output": "post_round_memory_jobs/player.summary.json"}},
                    "failed": {},
                },
            },
        )
        delivered_run = self.card / ".agent_runs" / "round-000001"
        _write_json(
            delivered_run / "manifest.json",
            {
                "round_id": "round-000001",
                "stage": "delivered",
            },
        )

        self.assertEqual(self.agent_memory.previous_post_round_memory_state(self.card), {})

if __name__ == "__main__":
    unittest.main()
