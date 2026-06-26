import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "skills" / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_projection_agent():
    return _load_module("projection_agent")


def _load_rp_generate_cli():
    return _load_module("rp_generate_cli")


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

    def test_blank_feedback_uses_projection_feedback_alias(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "blocked",
                "feedback": "   ",
                "projection_feedback": "Projection cannot reconcile the target actor.",
            },
            actor_id="character:Bob",
            source_call_id="call-bob-1",
        )

        self.assertEqual(output["feedback"], "Projection cannot reconcile the target actor.")

    def test_final_actor_message_rejects_non_string_values(self):
        for value in ({"content": "You see the gate."}, ["You see the gate."]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(self.projection.ProjectionValidationError, "final_actor_message"):
                    self.projection.validate_projection_output(
                        {
                            "decision": "pass",
                            "final_actor_message": value,
                        },
                        actor_id="character:Bob",
                        source_call_id="call-bob-1",
                    )

    def test_feedback_rejects_non_string_values(self):
        for value in ({"reason": "unsafe"}, ["unsafe"]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(self.projection.ProjectionValidationError, "feedback"):
                    self.projection.validate_projection_output(
                        {
                            "decision": "blocked",
                            "feedback": value,
                        },
                        actor_id="character:Bob",
                        source_call_id="call-bob-1",
                    )

    def test_projection_feedback_rejects_non_string_values(self):
        for value in ({"reason": "unsafe"}, ["unsafe"]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(self.projection.ProjectionValidationError, "projection_feedback"):
                    self.projection.validate_projection_output(
                        {
                            "decision": "blocked",
                            "projection_feedback": value,
                        },
                        actor_id="character:Bob",
                        source_call_id="call-bob-1",
                    )

    def test_build_review_packet_uses_natural_language_review_context(self):
        actor_packet = {
            "immersive_context": "You remember: I was taught that cursed heroes endanger civilians.",
            "visible_events": [{"content": "A traveler enters the market."}],
        }
        objective_context = {"facts": [{"fact": "The old hero was framed by the king."}]}

        packet = self.projection.build_review_packet(
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-1",
            source_message_id="msg-paladin-1",
            requested_actor_message="You discover the cursed hero in the market.",
            actor_packet=actor_packet,
            objective_context=objective_context,
        )

        self.assertEqual(packet["target_actor_id"], "character:CurrentPaladin")
        self.assertEqual(packet["source_call_id"], "call-paladin-1")
        self.assertEqual(packet["source_message_id"], "msg-paladin-1")
        self.assertEqual(packet["requested_actor_message"], "You discover the cursed hero in the market.")
        self.assertIn("cursed heroes", packet["actor_context"])
        self.assertIn("framed by the king", packet["review_reference"])
        self.assertNotIn("actor_visible_events", packet)
        self.assertIsInstance(packet["actor_context"], str)
        self.assertIsInstance(packet["review_reference"], str)
        self.assertIn("pass", packet["instruction"])
        self.assertIn("edited", packet["instruction"])
        self.assertIn("needs_rewrite", packet["instruction"])
        self.assertIn("blocked", packet["instruction"])

    def test_build_review_packet_reads_actor_memory_store_without_structured_review_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            actor_dir = card / "characters" / "Ada"
            objective_dir = card / "memory" / "characters" / "Ada"
            actor_dir.mkdir(parents=True)
            objective_dir.mkdir(parents=True)
            (actor_dir / "profile.md").write_text(
                "I am Ada, the archivist who trusts sealed oaths.",
                encoding="utf-8",
            )
            (actor_dir / "long_term_memories.md").write_text(
                "I remember the west archive flood.",
                encoding="utf-8",
            )
            (actor_dir / "key_memories.json").write_text(
                json.dumps(
                    {
                        "memories": [
                            {
                                "tag": "sealed ledger",
                                "summary": "I vaguely remember a sealed ledger.",
                                "detail": "The ledger names the hidden witness.",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (actor_dir / "short_term_memories.md").write_text(
                "Someone told me the old door clicked.",
                encoding="utf-8",
            )
            (objective_dir / "profile.md").write_text(
                "Ada is the archive keeper assigned to the west wing.",
                encoding="utf-8",
            )
            (objective_dir / "background.md").write_text(
                "Ada grew up in the coastal archive after the flood.",
                encoding="utf-8",
            )

            packet = self.projection.build_review_packet(
                actor_id="character:Ada",
                source_call_id="call-ada-1",
                source_message_id="msg-ada-1",
                requested_actor_message="You hear the sealed ledger shift behind the door.",
                actor_packet={
                    "card_folder": str(card),
                    "immersive_context": "I can smell wet paper from the west stacks.",
                    "self_knowledge": {"identity": "structured identity must not leak"},
                    "memory": {"long_term": ["structured memory must not leak"]},
                    "visible_events": [{"content": "structured event must not leak"}],
                    "gm_visibility_basis": {"mode": "direct"},
                    "actor_visible_events": [{"content": "structured bucket must not leak"}],
                    "gm_only_hidden_settings": [{"fact": "hidden setting must not leak"}],
                },
                objective_context={
                    "facts": ["The door is mechanically sealed."],
                    "gm_only_hidden_settings": [{"fact": "Ada's patron forged the ledger."}],
                },
            )

        actor_context = packet["actor_context"]
        review_reference = packet["review_reference"]
        serialized_prompt_text = actor_context + "\n" + review_reference

        self.assertIn("wet paper", actor_context)
        self.assertIn("archivist who trusts sealed oaths", actor_context)
        self.assertIn("west archive flood", actor_context)
        self.assertIn("I vaguely remember a sealed ledger", actor_context)
        self.assertIn("old door clicked", actor_context)
        self.assertNotIn("hidden witness", actor_context)
        self.assertIn("archive keeper assigned to the west wing", review_reference)
        self.assertIn("coastal archive after the flood", review_reference)
        self.assertIn("The door is mechanically sealed.", review_reference)

        for forbidden in (
            "self_knowledge",
            "memory",
            "visible_events",
            "gm_visibility_basis",
            "actor_visible_events",
            "gm_only_hidden_settings",
            "structured identity must not leak",
            "structured memory must not leak",
            "structured event must not leak",
            "structured bucket must not leak",
            "hidden setting must not leak",
            "Ada's patron forged the ledger",
            "facts",
        ):
            self.assertNotIn(forbidden, serialized_prompt_text)

    def test_build_review_packet_reads_card_folder_from_projected_actor_packet(self):
        import agent_projection

        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            actor_dir = card / "characters" / "Ada"
            actor_dir.mkdir(parents=True)
            (actor_dir / "profile.md").write_text(
                "I am Ada and I never open a sealed archive without a witness.",
                encoding="utf-8",
            )
            actor_packet = agent_projection.project_actor_context(
                "character:Ada",
                {},
                {"name": "Ada", "card_folder": str(card)},
                "You hear the archive lock click.",
            )

            packet = self.projection.build_review_packet(
                actor_id="character:Ada",
                source_call_id="call-ada-1",
                source_message_id="msg-ada-1",
                requested_actor_message="You hear the archive lock click.",
                actor_packet=actor_packet,
                objective_context={},
            )

        self.assertEqual(actor_packet["card_folder"], str(card))
        self.assertIn("never open a sealed archive", packet["actor_context"])

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
        self.assertIn("not cursed", packet["review_reference"])
        self.assertNotIn("misconception", packet["actor_context"].lower())
        self.assertNotIn("false", packet["actor_context"].lower())
        self.assertNotIn("framed by the king", packet["actor_context"].lower())

    def test_bob_edited_projection_replaces_unsupported_vampire_label(self):
        packet = self.projection.build_review_packet(
            actor_id="character:Bob",
            source_call_id="call-bob-1",
            source_message_id="msg-bob-1",
            requested_actor_message="You see a vampire at Alice's door.",
            actor_packet={
                "immersive_context": "You only know the stranger as a black-robed figure.",
                "visible_events": [{"content": "A black-robed figure stands at Alice's door."}],
            },
            objective_context={"gm_notes": ["The figure is a vampire."]},
        )
        output = self.projection.validate_projection_output(
            {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "final_actor_message": "You see a black-robed figure at Alice's door.",
                "feedback": "Changed the label to Bob-visible wording.",
            },
            actor_id=packet["target_actor_id"],
            source_call_id=packet["source_call_id"],
        )

        self.assertNotIn("vampire", output["final_actor_message"].lower())
        self.assertIn("black-robed figure", output["final_actor_message"])

    def test_alice_pass_projection_preserves_supported_private_vampire_label(self):
        packet = self.projection.build_review_packet(
            actor_id="character:Alice",
            source_call_id="call-alice-1",
            source_message_id="msg-alice-1",
            requested_actor_message="You recognize the vampire at your door.",
            actor_packet={
                "immersive_context": "Your private memory: the black-robed visitor is the vampire who spared you.",
                "visible_events": [{"content": "The black-robed visitor returns to your door."}],
            },
            objective_context={"gm_notes": ["Alice privately knows the visitor is a vampire."]},
        )
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "target_actor_id": "character:Alice",
                "source_call_id": "call-alice-1",
                "final_actor_message": "You recognize the vampire at your door.",
                "feedback": "",
            },
            actor_id=packet["target_actor_id"],
            source_call_id=packet["source_call_id"],
        )

        self.assertIn("vampire", output["final_actor_message"].lower())

    def test_paladin_pass_projection_preserves_subjective_cursed_hero_label(self):
        output = self.projection.validate_projection_output(
            {
                "decision": "pass",
                "target_actor_id": "character:CurrentPaladin",
                "source_call_id": "call-paladin-1",
                "final_actor_message": "You confront the cursed hero in the market.",
                "feedback": "",
            },
            actor_id="character:CurrentPaladin",
            source_call_id="call-paladin-1",
        )
        serialized = json.dumps(output, ensure_ascii=False).lower()

        self.assertIn("cursed hero", output["final_actor_message"])
        self.assertNotIn("misconception", serialized)
        self.assertNotIn("false", serialized)
        self.assertNotIn("framed by the king", serialized)

    def test_rp_generate_cli_validates_projection_output(self):
        cli = _load_rp_generate_cli()

        output = cli._validate(
            "projection",
            {
                "decision": "edited",
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "final_actor_message": "You see a black-robed figure.",
                "feedback": "Changed label to Bob-visible wording.",
            },
            {
                "projection_packet": {
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                }
            },
        )

        self.assertEqual(output["decision"], "edited")
        self.assertEqual(output["target_actor_id"], "character:Bob")
        self.assertEqual(output["source_call_id"], "call-bob-1")
        self.assertEqual(output["final_actor_message"], "You see a black-robed figure.")
        self.assertEqual(output["feedback"], "Changed label to Bob-visible wording.")

    def test_rp_generate_cli_unwraps_projection_output_wrapper(self):
        cli = _load_rp_generate_cli()

        output = cli._validate(
            "projection",
            {
                "projection_output": {
                    "decision": "pass",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "final_actor_message": "You hear Alice whisper from the hall.",
                    "feedback": "",
                }
            },
            {
                "projection_packet": {
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                }
            },
        )

        self.assertEqual(output["decision"], "pass")
        self.assertEqual(output["final_actor_message"], "You hear Alice whisper from the hall.")

    def test_rp_generate_cli_rejects_projection_identity_mismatch(self):
        cli = _load_rp_generate_cli()

        with self.assertRaisesRegex(cli.AgentExecutionError, "projection returned invalid artifact"):
            cli._validate(
                "projection",
                {
                    "decision": "pass",
                    "target_actor_id": "character:Alice",
                    "source_call_id": "call-alice-1",
                    "final_actor_message": "You hear Alice whisper from the hall.",
                    "feedback": "",
                },
                {
                    "projection_packet": {
                        "target_actor_id": "character:Bob",
                        "source_call_id": "call-bob-1",
                    }
                },
            )

    def test_rp_generate_cli_rejects_projection_without_validation_context(self):
        cli = _load_rp_generate_cli()

        with self.assertRaisesRegex(cli.AgentExecutionError, "projection validation context"):
            cli._validate(
                "projection",
                {
                    "decision": "pass",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "final_actor_message": "You hear Alice whisper from the hall.",
                    "feedback": "",
                },
            )

    def test_dispatch_agent_payload_binds_projection_identity_from_extra_context(self):
        cli = _load_rp_generate_cli()
        payload = {
            "decision": "pass",
            "target_actor_id": "character:Alice",
            "source_call_id": "call-alice-1",
            "final_actor_message": "You hear Alice whisper from the hall.",
            "feedback": "",
        }

        def fake_run_claude(_agent_key, _prompt, _cwd):
            return json.dumps(payload, ensure_ascii=False)

        with self.assertRaisesRegex(cli.AgentExecutionError, "projection returned invalid artifact"):
            cli._dispatch_agent_payload(
                "projection",
                "# projection\n",
                ROOT,
                fake_run_claude,
                extra_context={
                    "projection_packet": {
                        "target_actor_id": "character:Bob",
                        "source_call_id": "call-bob-1",
                    }
                },
                attempts=1,
            )

    def test_rp_generate_cli_converts_projection_validation_error(self):
        cli = _load_rp_generate_cli()

        with self.assertRaisesRegex(cli.AgentExecutionError, "projection returned invalid artifact"):
            cli._validate(
                "projection",
                {
                    "decision": "edited",
                    "target_actor_id": "character:Bob",
                    "source_call_id": "call-bob-1",
                    "feedback": "Missing final actor message.",
                },
                {
                    "projection_packet": {
                        "target_actor_id": "character:Bob",
                        "source_call_id": "call-bob-1",
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()
