import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_projection_agent():
    spec = importlib.util.spec_from_file_location(
        "projection_agent",
        ROOT / "skills" / "projection_agent.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionAgentTest(unittest.TestCase):
    def setUp(self):
        self.projection = _load_projection_agent()

    def test_pass_uses_final_actor_message(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "final_actor_message": "You see a black-robed figure at Alice's door.",
                "feedback": "",
            },
            actor_id="character:Bob",
            source_call_id="call-bob-1",
        )

        self.assertEqual(output["decision"], "pass")
        self.assertEqual(output["target_actor_id"], "character:Bob")
        self.assertEqual(output["source_call_id"], "call-bob-1")
        self.assertEqual(output["final_actor_message"], "You see a black-robed figure at Alice's door.")
        self.assertEqual(output["feedback"], "")

    def test_edited_requires_final_actor_message(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "final_actor_message"):
            self.projection.validate_projection_output(
                {
                    "decision": "edited",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "feedback": "Use Bob's visible label.",
                },
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_needs_rewrite_requires_feedback(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "feedback"):
            self.projection.validate_projection_output(
                {
                    "decision": "needs_rewrite",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "final_actor_message": "",
                },
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_blocked_requires_feedback(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "feedback"):
            self.projection.validate_projection_output(
                {
                    "decision": "blocked",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "feedback": "   ",
                },
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_actor_or_call_mismatch_rejected(self):
        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "target_actor_id"):
            self.projection.validate_projection_output(
                {
                    "decision": "pass",
                    "target_actor_id": "character:Alice",
                    "source_call_id": "call-bob-1",
                    "final_actor_message": "You see the market gate.",
                },
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

        with self.assertRaisesRegex(self.projection.ProjectionValidationError, "source_call_id"):
            self.projection.validate_projection_output(
                {
                    "decision": "pass",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-alice-1",
                    "final_actor_message": "You see the market gate.",
                },
                actor_id="character:Bob",
                source_call_id="call-bob-1",
            )

    def test_projection_feedback_alias_normalized(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "needs_rewrite",
                "projection_feedback": "The message reveals objective truth Bob cannot know.",
            },
            actor_id="character:Bob",
            source_call_id="call-bob-1",
        )

        self.assertEqual(output["decision"], "needs_rewrite")
        self.assertEqual(output["target_actor_id"], "character:Bob")
        self.assertEqual(output["source_call_id"], "call-bob-1")
        self.assertEqual(output["final_actor_message"], "")
        self.assertEqual(output["feedback"], "The message reveals objective truth Bob cannot know.")

    def test_build_review_packet_keeps_contexts_separate_and_deep_copies_inputs(self):
        actor_packet = {
            "immersive_context": "You remember: I was taught that cursed heroes endanger civilians.",
            "visible_events": [{"content": "A traveler enters the market."}],
        }
        objective_context = {"facts": [{"fact": "The old hero was framed by the king."}]}
        original_actor_packet = copy.deepcopy(actor_packet)
        original_objective_context = copy.deepcopy(objective_context)

        packet = self.projection.build_review_packet(
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-1",
            source_message_id="msg-paladin-1",
            requested_actor_message="You discover the cursed hero in the market.",
            actor_packet=actor_packet,
            objective_context=objective_context,
        )

        packet["actor_visible_events"][0]["content"] = "mutated event"
        packet["objective_context"]["facts"][0]["fact"] = "mutated truth"

        self.assertEqual(actor_packet, original_actor_packet)
        self.assertEqual(objective_context, original_objective_context)
        self.assertEqual(packet["target_actor_id"], "character:CurrentPaladin")
        self.assertEqual(packet["source_call_id"], "call-paladin-1")
        self.assertEqual(packet["source_message_id"], "msg-paladin-1")
        self.assertEqual(packet["requested_actor_message"], "You discover the cursed hero in the market.")
        self.assertIn("cursed heroes", packet["actor_context"])
        self.assertIn("traveler enters", original_actor_packet["visible_events"][0]["content"])
        self.assertIn("framed by the king", original_objective_context["facts"][0]["fact"])
        self.assertIn("pass", packet["instruction"])
        self.assertIn("edited", packet["instruction"])
        self.assertIn("needs_rewrite", packet["instruction"])
        self.assertIn("blocked", packet["instruction"])

    def test_subjective_false_belief_stays_in_actor_context_without_false_label(self):
        packet = self.projection.build_review_packet(
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-1",
            source_message_id="msg-paladin-1",
            requested_actor_message="You discover the cursed hero in the market.",
            actor_packet={
                "immersive_context": (
                    "You remember: I was taught that cursed heroes endanger civilians.\n"
                    "You believe the old hero still carries the curse."
                ),
                "visible_events": [{"content": "A traveler with an old sigil enters the market."}],
            },
            objective_context={"facts": ["The old hero was framed by the king and is not cursed."]},
        )

        self.assertIn("You believe the old hero still carries the curse.", packet["actor_context"])
        self.assertIn("not cursed", packet["objective_context"]["facts"][0])
        self.assertNotIn("misconception", packet["actor_context"].lower())
        self.assertNotIn("false", packet["actor_context"].lower())
        self.assertNotIn("framed by the king", packet["actor_context"].lower())


if __name__ == "__main__":
    unittest.main()
