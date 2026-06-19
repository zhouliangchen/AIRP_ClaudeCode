import importlib.util
import sys
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


def visibility_basis(actor_id):
    return {
        "mode": "direct",
        "summary": f"{actor_id} is directly addressed by this test subGM prompt.",
        "target_actor": actor_id,
        "visible_to": [actor_id],
    }


class SubgmSchemasTest(unittest.TestCase):
    def setUp(self):
        self.schemas = load_module("agent_schemas")

    def gm_base(self, commands=None):
        payload = {
            "agent": "gm",
            "scene_beats": [],
            "events": [],
            "actor_calls": [],
            "parallel_groups": [],
            "world_state_delta": [],
            "character_promotions": [],
            "decision_point": None,
            "stop_reason": "continue",
        }
        if commands is not None:
            payload["subgm_commands"] = commands
        return payload

    def subgm_base(self, **overrides):
        payload = {
            "agent": "subGM",
            "thread_id": "side_suli_rooftop",
            "status": "running",
            "scene_beats": [],
            "events": [],
            "actor_calls": [],
            "messages_to_gm": [],
            "world_state_delta": [],
            "character_usage": [],
            "promotion_requests": [],
            "boundary_requests": [],
            "notes_for_story": [],
            "next_resume_point": "",
        }
        payload.update(overrides)
        for call in payload.get("actor_calls", []):
            if isinstance(call, dict) and "visibility_basis" not in call:
                actor_id = str(call.get("actor_id") or "").strip()
                call["visibility_basis"] = visibility_basis(actor_id or "character:SuLi")
        return payload

    def test_gm_output_defaults_subgm_commands_to_empty_list(self):
        normalized = self.schemas.validate_gm_output(self.gm_base())
        self.assertEqual(normalized["subgm_commands"], [])

    def test_gm_output_accepts_subgm_start_command(self):
        normalized = self.schemas.validate_gm_output(self.gm_base([{
            "action": "start",
            "thread_id": "side_suli_rooftop",
            "title": "Rooftop warning",
            "outline": "SuLi checks the rooftop sigil while the classroom scene continues.",
            "time_window": "same morning, first period",
            "location": "school rooftop",
            "objective": "Reveal only what SuLi can plausibly discover off-screen.",
            "allowed_characters": ["character:SuLi"],
            "forbidden_characters": ["player"],
            "priority": "normal",
            "message": "Advance this scene until it can either pause or report a consequence.",
            "metadata": {"source": "gm"},
        }]))
        command = normalized["subgm_commands"][0]
        self.assertEqual(command["action"], "start")
        self.assertEqual(command["thread_id"], "side_suli_rooftop")
        self.assertEqual(command["allowed_characters"], ["character:SuLi"])

    def test_gm_output_requires_start_fields(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "title"):
            self.schemas.validate_gm_output(self.gm_base([{"action": "start", "thread_id": "side"}]))

    def test_gm_output_rejects_blank_start_title(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "title"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "start",
                "thread_id": "side_suli_rooftop",
                "title": "   ",
                "outline": "SuLi checks the rooftop sigil while the classroom scene continues.",
                "time_window": "same morning, first period",
                "location": "school rooftop",
                "objective": "Reveal only what SuLi can plausibly discover off-screen.",
            }]))

    def test_gm_output_accepts_non_start_message_command(self):
        normalized = self.schemas.validate_gm_output(self.gm_base([{
            "action": "pause",
            "thread_id": "side_suli_rooftop",
            "message": "Main GM needs SuLi in the main scene.",
        }]))
        self.assertEqual(normalized["subgm_commands"][0]["action"], "pause")
        self.assertEqual(normalized["subgm_commands"][0]["message"], "Main GM needs SuLi in the main scene.")

    def test_gm_output_rejects_blank_non_start_message(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "message"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "pause",
                "thread_id": "side_suli_rooftop",
                "message": " ",
            }]))

    def test_gm_output_normalizes_non_start_optional_command_fields(self):
        normalized = self.schemas.validate_gm_output(self.gm_base([{
            "action": "resume",
            "thread_id": "side_suli_rooftop",
            "message": "Resume from the vent clue.",
        }]))

        self.assertEqual(normalized["subgm_commands"][0], {
            "action": "resume",
            "thread_id": "side_suli_rooftop",
            "title": "",
            "outline": "",
            "time_window": "",
            "location": "",
            "objective": "",
            "allowed_characters": [],
            "forbidden_characters": [],
            "priority": "",
            "message": "Resume from the vent clue.",
            "metadata": {},
        })

    def test_gm_output_rejects_unknown_subgm_action_and_blank_thread_id(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "action"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "teleport",
                "thread_id": "side_suli_rooftop",
                "message": "Not a valid subGM command.",
            }]))

        with self.assertRaisesRegex(self.schemas.ValidationError, "thread_id"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "pause",
                "thread_id": " ",
                "message": "Missing thread id.",
            }]))

    def test_gm_output_rejects_non_string_allowed_characters(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "allowed_characters"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "start",
                "thread_id": "side_suli_rooftop",
                "title": "Rooftop warning",
                "outline": "SuLi checks the rooftop sigil while the classroom scene continues.",
                "time_window": "same morning, first period",
                "location": "school rooftop",
                "objective": "Reveal only what SuLi can plausibly discover off-screen.",
                "allowed_characters": [123],
            }]))

    def test_gm_output_rejects_player_in_allowed_characters(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "allowed_characters"):
            self.schemas.validate_gm_output(self.gm_base([{
                "action": "start",
                "thread_id": "side_suli_rooftop",
                "title": "Rooftop warning",
                "outline": "SuLi checks the rooftop sigil while the classroom scene continues.",
                "time_window": "same morning, first period",
                "location": "school rooftop",
                "objective": "Reveal only what SuLi can plausibly discover off-screen.",
                "allowed_characters": ["player"],
            }]))

    def test_gm_output_allows_player_or_character_in_forbidden_characters(self):
        normalized = self.schemas.validate_gm_output(self.gm_base([{
            "action": "start",
            "thread_id": "side_suli_rooftop",
            "title": "Rooftop warning",
            "outline": "SuLi checks the rooftop sigil while the classroom scene continues.",
            "time_window": "same morning, first period",
            "location": "school rooftop",
            "objective": "Reveal only what SuLi can plausibly discover off-screen.",
            "forbidden_characters": ["player", "character:SuLi"],
        }]))

        self.assertEqual(
            normalized["subgm_commands"][0]["forbidden_characters"],
            ["player", "character:SuLi"],
        )

    def test_subgm_output_accepts_minimal_completed_thread(self):
        payload = {
            "agent": "subGM",
            "thread_id": "side_suli_rooftop",
            "status": "completed",
            "scene_beats": [{"content": "SuLi finds chalk dust on the rooftop vent."}],
            "events": [],
            "actor_calls": [],
            "messages_to_gm": [{"content": "The clue is ready to merge."}],
            "world_state_delta": [{"scope": "rooftop", "fact": "chalk dust found"}],
            "character_usage": ["character:SuLi"],
            "promotion_requests": [],
            "boundary_requests": [],
            "notes_for_story": ["Use only if GM merges it."],
            "next_resume_point": "",
        }
        normalized = self.schemas.validate_subgm_output(payload)
        self.assertEqual(normalized["agent"], "subGM")
        self.assertEqual(normalized["status"], "completed")

    def test_subgm_output_returns_only_schema_keys(self):
        payload = self.subgm_base(extra_key="ignored")

        normalized = self.schemas.validate_subgm_output(payload)

        self.assertEqual(list(normalized), [
            "agent",
            "thread_id",
            "status",
            "scene_beats",
            "events",
            "actor_calls",
            "messages_to_gm",
            "world_state_delta",
            "character_usage",
            "promotion_requests",
            "boundary_requests",
            "notes_for_story",
            "next_resume_point",
        ])
        self.assertNotIn("extra_key", normalized)

    def test_subgm_output_rejects_plain_string_messages_to_gm(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "messages_to_gm"):
            self.schemas.validate_subgm_output(self.subgm_base(messages_to_gm=["plain"]))

    def test_subgm_output_rejects_blank_notes_for_story(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "notes_for_story"):
            self.schemas.validate_subgm_output(self.subgm_base(notes_for_story=[""]))

    def test_subgm_output_rejects_player_character_usage(self):
        with self.assertRaisesRegex(self.schemas.ValidationError, "character_usage"):
            self.schemas.validate_subgm_output(self.subgm_base(character_usage=["player"]))

    def test_subgm_output_rejects_promotion_and_subgm_spawn(self):
        payload = {
            "agent": "subGM",
            "thread_id": "side_suli_rooftop",
            "status": "running",
            "scene_beats": [],
            "events": [],
            "actor_calls": [],
            "messages_to_gm": [],
            "world_state_delta": [],
            "character_usage": [],
            "promotion_requests": [],
            "boundary_requests": [],
            "notes_for_story": [],
            "next_resume_point": "",
            "character_promotions": [],
            "subgm_commands": [],
        }
        with self.assertRaisesRegex(self.schemas.ValidationError, "must not contain"):
            self.schemas.validate_subgm_output(payload)

    def test_subgm_output_rejects_player_actor_call(self):
        payload = {
            "agent": "subGM",
            "thread_id": "side_suli_rooftop",
            "status": "running",
            "scene_beats": [],
            "events": [],
            "actor_calls": [{"call_id": "call-player-1", "actor_id": "player", "prompt": "You are elsewhere.", "reason": "not allowed"}],
            "messages_to_gm": [],
            "world_state_delta": [],
            "character_usage": [],
            "promotion_requests": [],
            "boundary_requests": [],
            "notes_for_story": [],
            "next_resume_point": "",
        }
        with self.assertRaisesRegex(self.schemas.ValidationError, "player"):
            self.schemas.validate_subgm_output(payload)

    def test_subgm_output_rejects_blank_and_non_character_actor_calls(self):
        for actor_id, expected in (("", "actor_id"), ("SuLi", "character")):
            with self.subTest(actor_id=actor_id):
                payload = self.subgm_base(actor_calls=[{
                    "call_id": "call-side-1",
                    "actor_id": actor_id,
                    "prompt": "Report your rooftop observation.",
                    "reason": "SubGM can only call independent character actors.",
                }])

                with self.assertRaisesRegex(self.schemas.ValidationError, expected):
                    self.schemas.validate_subgm_output(payload)

    def test_subgm_output_rejects_blank_actor_call_id(self):
        for call_id in ("", "   "):
            with self.subTest(call_id=repr(call_id)):
                payload = self.subgm_base(actor_calls=[{
                    "call_id": call_id,
                    "actor_id": "character:SuLi",
                    "prompt": "Report your rooftop observation.",
                    "reason": "SubGM can only call independent character actors.",
                }])

                with self.assertRaisesRegex(self.schemas.ValidationError, "call_id"):
                    self.schemas.validate_subgm_output(payload)

    def test_subgm_output_accepts_nonblank_actor_call_id(self):
        payload = self.subgm_base(actor_calls=[{
            "call_id": "call-character-SuLi-1",
            "actor_id": "character:SuLi",
            "prompt": "Report your rooftop observation.",
            "reason": "SubGM can only call independent character actors.",
        }])

        normalized = self.schemas.validate_subgm_output(payload)

        self.assertEqual(normalized["actor_calls"][0]["call_id"], "call-character-SuLi-1")


if __name__ == "__main__":
    unittest.main()
