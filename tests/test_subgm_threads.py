import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_command(thread_id="side_suli_rooftop", character="character:SuLi"):
    return {
        "action": "start",
        "thread_id": thread_id,
        "title": "Rooftop warning",
        "outline": "SuLi checks the rooftop sigil.",
        "time_window": "same morning",
        "location": "school rooftop",
        "objective": "Advance the off-screen clue.",
        "allowed_characters": [character],
        "forbidden_characters": ["player"],
        "priority": "normal",
        "message": "Start now.",
        "metadata": {},
    }


class SubgmThreadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "round-000001"
        self.run_dir.mkdir()
        self.subgm_threads = load_module("subgm_threads")
        self.agent_messages = load_module("agent_messages")

    def tearDown(self):
        self.tmp.cleanup()

    def read_state(self, thread_id="side_suli_rooftop"):
        return json.loads((self.run_dir / "side_threads" / thread_id / "state.json").read_text(encoding="utf-8"))

    def test_start_command_creates_side_thread_state_trace_and_message_log(self):
        result = self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        self.assertEqual(result["started"], ["side_suli_rooftop"])
        thread = self.run_dir / "side_threads" / "side_suli_rooftop"
        self.assertTrue((thread / "state.json").exists())
        self.assertTrue((thread / "messages.jsonl").exists())
        self.assertTrue((thread / "interaction.trace.json").exists())
        state = self.read_state()
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["boundary"]["location"], "school rooftop")
        self.assertEqual(state["allowed_characters"], ["character:SuLi"])
        messages = (thread / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(messages), 1)
        self.assertEqual(json.loads(messages[0])["from"], "gm")

    def test_conflict_check_rejects_same_character_in_two_running_threads(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "already reserved"):
            self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b")])

    def test_apply_gm_commands_prevalidates_later_unsafe_thread_id_before_writes(self):
        commands = [
            start_command(thread_id="side_a", character="character:Ada"),
            start_command(thread_id="BadThread", character="character:Bert"),
        ]

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "thread_id"):
            self.subgm_threads.apply_gm_commands(self.run_dir, commands)

        self.assertFalse((self.run_dir / "side_threads" / "side_a").exists())
        self.assertFalse((self.run_dir / "side_threads").exists())

    def test_apply_gm_commands_prevalidates_duplicate_start_thread_id_before_writes(self):
        commands = [
            start_command(thread_id="dup_thread", character="character:Ada"),
            start_command(thread_id="dup_thread", character="character:Bert"),
        ]

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "already exists"):
            self.subgm_threads.apply_gm_commands(self.run_dir, commands)

        self.assertFalse((self.run_dir / "side_threads" / "dup_thread").exists())
        self.assertFalse((self.run_dir / "side_threads").exists())

    def test_apply_gm_commands_prevalidates_intrabatch_reservation_conflict_before_writes(self):
        commands = [
            start_command(thread_id="side_a", character="character:SuLi"),
            start_command(thread_id="side_b", character="character:SuLi"),
        ]

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "already reserved"):
            self.subgm_threads.apply_gm_commands(self.run_dir, commands)

        self.assertFalse((self.run_dir / "side_threads" / "side_a").exists())
        self.assertFalse((self.run_dir / "side_threads" / "side_b").exists())
        self.assertFalse((self.run_dir / "side_threads").exists())

    def test_paused_thread_releases_character_for_new_thread(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(self.run_dir, [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}])
        result = self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b")])
        self.assertEqual(result["started"], ["side_b"])

    def test_resume_rechecks_character_conflicts(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(self.run_dir, [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}])
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b")])
        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "already reserved"):
            self.subgm_threads.apply_gm_commands(self.run_dir, [{"action": "resume", "thread_id": "side_a", "message": "Resume.", "metadata": {}}])

    def test_merge_rechecks_character_conflicts_when_reactivating_paused_thread(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b")])

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "already reserved"):
            self.subgm_threads.apply_gm_commands(
                self.run_dir,
                [{"action": "merge", "thread_id": "side_a", "message": "Merge.", "metadata": {}}],
            )

        self.assertEqual(self.read_state("side_a")["status"], "paused")
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:SuLi": "side_b"},
        )

    def test_merge_reactivates_paused_thread_when_no_character_conflict_exists(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )

        result = self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "merge", "thread_id": "side_a", "message": "Merge.", "metadata": {}}],
        )

        self.assertEqual(result["merged"], ["side_a"])
        self.assertEqual(self.read_state("side_a")["status"], "merging")
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:SuLi": "side_a"},
        )

    def test_player_cannot_be_allowed_in_side_thread(self):
        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "player"):
            self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(character="player")])

    def test_invalid_allowed_character_ids_are_rejected(self):
        bad_ids = [
            "character:worldtruth",
            "character:../Ada",
            "character:Su Li",
            "character:",
            "character:gm_notes",
            "character:hidden_note",
            "character:omniscient",
        ]
        for bad in bad_ids:
            with self.subTest(bad=bad):
                with self.assertRaises(self.subgm_threads.SubgmThreadError):
                    self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id=f"side_{len(bad)}", character=bad)])

    def test_invalid_forbidden_character_ids_are_rejected_but_player_is_allowed(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_player_forbidden")])
        bad_ids = [
            "character:worldtruth",
            "character:../Ada",
            "character:Su Li",
            "character:",
            "character:gm_notes",
            "character:hidden_note",
            "character:omniscient",
        ]
        for index, bad in enumerate(bad_ids):
            with self.subTest(bad=bad):
                command = start_command(thread_id=f"side_forbidden_{index}", character=f"character:Valid{index}")
                command["forbidden_characters"] = [bad]
                with self.assertRaises(self.subgm_threads.SubgmThreadError):
                    self.subgm_threads.apply_gm_commands(self.run_dir, [command])

    def test_safe_thread_id_rejects_path_and_hidden_shapes(self):
        bad_ids = [
            "",
            "player",
            "../x",
            "side-a",
            "side a",
            "worldtruth_side",
            "worldtruth",
            "world_truth_side",
            "world_truth",
            "gmOnly",
            "gm_only",
            "gmonly",
            "hidden_note",
            "hiddennote",
            "hidden_truth",
            "hiddentruth",
            "gm_notes",
            "gmnotes",
            "omniscient",
            "user_instruction_channel",
            "userinstructionchannel",
            "hidden_facts",
            "hiddenfacts",
            "private_events",
            "privateevents",
            "out_of_character",
            "outofcharacter",
        ]
        for bad in bad_ids:
            with self.subTest(bad=bad):
                with self.assertRaises(self.subgm_threads.SubgmThreadError):
                    self.subgm_threads.safe_thread_id(bad)

    def test_status_commands_update_state_and_messages(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        for action, expected in [("accelerate", "running"), ("merge", "merging"), ("close", "completed")]:
            self.subgm_threads.apply_gm_commands(self.run_dir, [{"action": action, "thread_id": "side_suli_rooftop", "message": action, "metadata": {}}])
            self.assertEqual(self.read_state()["status"], expected)
        messages = (self.run_dir / "side_threads" / "side_suli_rooftop" / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(messages), 4)

    def test_message_command_appends_gm_message_without_status_change(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        result = self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{
                "action": "message",
                "thread_id": "side_suli_rooftop",
                "message": "Check the rooftop door before advancing.",
                "metadata": {"tone": "urgent"},
            }],
        )

        self.assertEqual(result["messaged"], ["side_suli_rooftop"])
        self.assertEqual(self.read_state()["status"], "running")
        messages = [
            json.loads(line)
            for line in (self.run_dir / "side_threads" / "side_suli_rooftop" / "messages.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["from"], "gm")
        self.assertEqual(messages[-1]["action"], "message")
        self.assertEqual(messages[-1]["content"], "Check the rooftop door before advancing.")
        self.assertEqual(messages[-1]["metadata"], {"tone": "urgent"})
        self.assertEqual(self.read_state()["history"][-1]["action"], "message")

    def test_gm_message_command_mirrors_to_common_message_bus(self):
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [start_command(thread_id="side_a", character="character:Ada")],
        )

        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{
                "action": "message",
                "thread_id": "side_a",
                "message": "Check the clue before advancing.",
                "metadata": {"tone": "urgent"},
            }],
        )

        messages = self.agent_messages.read_messages(self.run_dir)
        mirrored = [
            message
            for message in messages
            if message.get("from") == "gm"
            and message.get("to") == ["subGM:side_a"]
            and message.get("payload", {}).get("content") == "Check the clue before advancing."
        ]
        self.assertEqual(len(mirrored), 1)
        message = mirrored[0]
        self.assertEqual(message["type"], "message")
        self.assertEqual(message["visibility"], "gm_only")
        self.assertEqual(
            message["payload"],
            {
                "thread_id": "side_a",
                "action": "message",
                "content": "Check the clue before advancing.",
                "metadata": {"tone": "urgent"},
            },
        )

    def test_append_subgm_message_mirrors_to_common_message_bus(self):
        command = start_command(thread_id="side_a", character="character:Ada")
        command["message"] = "Start"
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [command],
        )

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_a",
            {"content": "The clue is ready.", "status": "needs_gm", "metadata": {}},
        )

        messages = self.agent_messages.read_messages(self.run_dir)
        mirrored = [
            message
            for message in messages
            if message.get("from") == "subGM:side_a"
            and message.get("to") == ["gm"]
            and message.get("payload", {}).get("content") == "The clue is ready."
        ]
        self.assertEqual(len(mirrored), 1)
        message = mirrored[0]
        self.assertEqual(message["type"], "message")
        self.assertEqual(message["visibility"], "gm_only")
        self.assertEqual(
            message["payload"],
            {
                "thread_id": "side_a",
                "action": "message",
                "content": "The clue is ready.",
                "status": "needs_gm",
                "metadata": {},
            },
        )

    def test_common_message_bus_rejection_raises_subgm_thread_error(self):
        if not hasattr(self.subgm_threads, "agent_messages"):
            self.fail("subgm_threads must load agent_messages")
        original_append = self.subgm_threads.agent_messages.append_message

        def reject_append(*_args, **_kwargs):
            return {"ok": False, "reason": "message bus unavailable"}

        self.subgm_threads.agent_messages.append_message = reject_append
        try:
            with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "message bus unavailable"):
                self.subgm_threads.apply_gm_commands(
                    self.run_dir,
                    [start_command(thread_id="side_a", character="character:Ada")],
                )
        finally:
            self.subgm_threads.agent_messages.append_message = original_append

    def test_append_subgm_message_and_load_summaries_for_gm(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {
                "content": "Rooftop clue ready.",
                "last_scene_beats": ["SuLi finds the rooftop sigil."],
                "next_resume_point": "resume at the rooftop vent",
            },
        )
        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["thread_id"], "side_suli_rooftop")
        self.assertEqual(summaries[0]["last_message"]["content"], "Rooftop clue ready.")
        self.assertEqual(summaries[0]["last_scene_beats"], ["SuLi finds the rooftop sigil."])
        self.assertEqual(summaries[0]["next_resume_point"], "resume at the rooftop vent")
        messages = self.subgm_threads.load_messages_for_gm(self.run_dir)
        self.assertEqual(messages[-1]["from"], "subGM:side_suli_rooftop")

    def test_append_subgm_message_last_scene_beats_override_stale_output_file(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        output_path = self.run_dir / "side_threads" / "side_suli_rooftop" / "subgm.output.json"
        output_path.write_text(
            json.dumps({"scene_beats": ["old rooftop beat"]}, ensure_ascii=False),
            encoding="utf-8",
        )

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "new clue", "last_scene_beats": ["new rooftop beat"]},
        )

        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["last_scene_beats"], ["new rooftop beat"])

    def test_append_subgm_message_next_resume_point_overrides_stale_output_file(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        output_path = self.run_dir / "side_threads" / "side_suli_rooftop" / "subgm.output.json"
        output_path.write_text(
            json.dumps({"next_resume_point": "old resume"}, ensure_ascii=False),
            encoding="utf-8",
        )

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "new clue", "next_resume_point": "new resume"},
        )

        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["next_resume_point"], "new resume")

    def test_append_subgm_message_updates_status_and_releases_reservation(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:SuLi": "side_suli_rooftop"},
        )

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "done", "status": "completed"},
        )

        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["status"], "completed")
        self.assertEqual(self.subgm_threads.active_character_reservations(self.run_dir), {})

    def test_append_subgm_message_cannot_resume_inactive_thread_with_conflict(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b")])

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "resume"):
            self.subgm_threads.append_subgm_message(
                self.run_dir,
                "side_a",
                {"content": "ready", "status": "running"},
            )

        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:SuLi": "side_b"},
        )

    def test_append_subgm_message_rejects_paused_to_merging(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "inactive"):
            self.subgm_threads.append_subgm_message(
                self.run_dir,
                "side_a",
                {"content": "merge now", "status": "merging"},
            )

        self.assertEqual(self.subgm_threads.active_character_reservations(self.run_dir), {})

    def test_append_subgm_message_rejects_paused_to_blocked(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "inactive"):
            self.subgm_threads.append_subgm_message(
                self.run_dir,
                "side_a",
                {"content": "blocked", "status": "blocked"},
            )

    def test_append_subgm_message_rejects_paused_to_needs_gm(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])
        self.subgm_threads.apply_gm_commands(
            self.run_dir,
            [{"action": "pause", "thread_id": "side_a", "message": "Pause.", "metadata": {}}],
        )

        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "inactive"):
            self.subgm_threads.append_subgm_message(
                self.run_dir,
                "side_a",
                {"content": "need GM", "status": "needs_gm"},
            )

    def test_append_subgm_message_allows_active_thread_to_report_needs_gm(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a")])

        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_a",
            {"content": "need GM", "status": "needs_gm"},
        )

        summaries = self.subgm_threads.load_thread_summaries(self.run_dir)
        self.assertEqual(summaries[0]["status"], "needs_gm")
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:SuLi": "side_a"},
        )

    def test_append_subgm_message_ignores_caller_supplied_sequence(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        self.subgm_threads.append_subgm_message(
            self.run_dir,
            "side_suli_rooftop",
            {"content": "x", "sequence": "bad"},
        )

        messages = self.subgm_threads.load_messages_for_gm(self.run_dir)
        self.assertIsInstance(messages[-1]["sequence"], int)
        self.assertEqual(messages[-1]["content"], "x")

    def test_active_reservations_are_sorted_and_ignore_completed_threads(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_b", character="character:Bert")])
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command(thread_id="side_a", character="character:Ada")])
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:Ada": "side_a", "character:Bert": "side_b"},
        )
        self.subgm_threads.apply_gm_commands(self.run_dir, [{"action": "close", "thread_id": "side_a", "message": "Done.", "metadata": {}}])
        self.assertEqual(
            self.subgm_threads.active_character_reservations(self.run_dir),
            {"character:Bert": "side_b"},
        )

    def test_main_actor_call_conflict_guard(self):
        self.subgm_threads.apply_gm_commands(self.run_dir, [start_command()])
        with self.assertRaisesRegex(self.subgm_threads.SubgmThreadError, "side_suli_rooftop"):
            self.subgm_threads.assert_main_actor_calls_do_not_conflict(
                self.run_dir,
                [{"actor_id": "character:SuLi", "call_id": "call-character-SuLi-1"}],
            )


if __name__ == "__main__":
    unittest.main()
