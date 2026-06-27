import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class PlayerDecisionEvidenceTest(unittest.TestCase):
    def setUp(self):
        import importlib

        self.mod = importlib.import_module("player_decision_evidence")

    def test_rejects_player_decision_without_prior_player_reply(self):
        result = self.mod.valid_gm_player_decision(
            {
                "stop_reason": "player_decision",
                "actor_calls": [],
                "decision_point": {"reason": "The player must choose."},
            },
            player_participated_before_gm=False,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "missing_prior_player_reply")

    def test_rejects_same_output_that_calls_player(self):
        result = self.mod.valid_gm_player_decision(
            {
                "stop_reason": "player_decision",
                "actor_calls": [{"actor_id": "player", "prompt": "What do you do?"}],
                "decision_point": {"reason": "The player must choose."},
            },
            player_participated_before_gm=True,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "same_output_calls_player")

    def test_accepts_structured_decision_after_player_reply_and_returns_label(self):
        decision_point = {
            "id": "gm-decision-1",
            "required_label": "Open the sealed door.",
            "options": ["open", "wait"],
        }

        result = self.mod.valid_gm_player_decision(
            {
                "stop_reason": "player_decision",
                "actor_calls": [],
                "decision_point": decision_point,
            },
            player_participated_before_gm=True,
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["reason"], "valid")
        self.assertEqual(result["label"], "Open the sealed door.")
        self.assertIs(result["decision_point"], decision_point)

    def test_extracts_evidence_only_when_player_replied_using_latest_natural_reply(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do first?",
                                }
                            ],
                            "decision_point": None,
                        },
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-2",
                                    "actor_id": "player",
                                    "prompt": "What do you do next?",
                                }
                            ],
                            "decision_point": None,
                        },
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-1",
                                "required_label": "Open the sealed door.",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "natural_reply": "I touch the door and listen.",
                            "events": [{"type": "reply", "content": "Older reply."}],
                        },
                        {
                            "natural_reply": "I brace my shoulder and pry open the sealed door.",
                            "events": [],
                        },
                    ],
                },
            },
        }

        result = self.mod.extract_player_critical_action_evidence(story_input)

        self.assertEqual(
            result,
            [
                {
                    "id": "gm-decision-1",
                    "required_label": "I brace my shoulder and pry open the sealed door.",
                    "risk_level": "gm_decision",
                }
            ],
        )
        self.assertTrue(self.mod.has_valid_player_decision(story_input))

    def test_no_player_reply_means_no_evidence(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {"required_label": "Open the sealed door."},
                        },
                    ],
                },
                "actors": {"player": [{"events": [{"type": "action", "content": "Not a reply."}]}]},
            },
        }

        self.assertEqual(self.mod.extract_player_critical_action_evidence(story_input), [])
        self.assertFalse(self.mod.has_valid_player_decision(story_input))

    def test_role_action_channel_counts_as_player_reply_fallback(self):
        story_input = {
            "player_inputs": {
                "routed_input": {
                    "role_action_channel": "I break the seal with both hands.",
                },
            },
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-role-action",
                                "required_label": "Break the seal.",
                            },
                        },
                    ],
                },
                "actors": {},
            },
        }

        self.assertEqual(
            self.mod.extract_player_critical_action_evidence(story_input),
            [
                {
                    "id": "gm-decision-role-action",
                    "required_label": "I break the seal with both hands.",
                    "risk_level": "gm_decision",
                }
            ],
        )

    def test_premature_output_before_later_player_reply_returns_no_evidence(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-premature",
                                "required_label": "Choose before asking.",
                            },
                        },
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do?",
                                }
                            ],
                            "decision_point": None,
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "source_call_id": "call-player-1",
                            "natural_reply": "I answer only after the second GM output.",
                            "events": [],
                        }
                    ],
                },
            },
        }

        self.assertEqual(self.mod.extract_player_critical_action_evidence(story_input), [])
        self.assertFalse(self.mod.has_valid_player_decision(story_input))

    def test_valid_prior_player_call_allows_later_player_decision_evidence(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do?",
                                }
                            ],
                            "decision_point": None,
                        },
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-valid",
                                "required_label": "Choose after replying.",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "source_call_id": "call-player-1",
                            "natural_reply": "I take the irreversible step.",
                            "events": [],
                        }
                    ],
                },
            },
        }

        self.assertEqual(
            self.mod.extract_player_critical_action_evidence(story_input),
            [
                {
                    "id": "gm-decision-valid",
                    "required_label": "I take the irreversible step.",
                    "risk_level": "gm_decision",
                }
            ],
        )

    def test_prior_player_call_without_source_call_id_consumes_reply_in_order(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "continue",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do?",
                                }
                            ],
                            "decision_point": None,
                        },
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [],
                            "decision_point": {
                                "id": "gm-decision-unsourced",
                                "required_label": "Choose after an unsourced reply.",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "natural_reply": "I answer a call from the prior GM output.",
                            "events": [],
                        }
                    ],
                },
            },
        }

        self.assertEqual(
            self.mod.extract_player_critical_action_evidence(story_input),
            [
                {
                    "id": "gm-decision-unsourced",
                    "required_label": "I answer a call from the prior GM output.",
                    "risk_level": "gm_decision",
                }
            ],
        )

    def test_same_output_player_call_and_player_decision_returns_no_evidence(self):
        story_input = {
            "loop_outputs": {
                "gm": {
                    "outputs": [
                        {
                            "stop_reason": "player_decision",
                            "actor_calls": [
                                {
                                    "call_id": "call-player-1",
                                    "actor_id": "player",
                                    "prompt": "What do you do?",
                                }
                            ],
                            "decision_point": {
                                "id": "gm-decision-same-output",
                                "required_label": "Choose while being asked.",
                            },
                        },
                    ],
                },
                "actors": {
                    "player": [
                        {
                            "source_call_id": "call-player-1",
                            "natural_reply": "I cannot prove the same GM output.",
                            "events": [],
                        }
                    ],
                },
            },
        }

        self.assertEqual(self.mod.extract_player_critical_action_evidence(story_input), [])


if __name__ == "__main__":
    unittest.main()
