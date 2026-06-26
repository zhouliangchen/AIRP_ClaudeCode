import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InputAnalysisApplyTest(unittest.TestCase):
    def test_normalizes_non_style_units_that_use_instruction_as_false_evidence(self):
        mod = _load_module("input_analysis_apply")
        input_analysis = mod.input_analysis
        role_text = "I found a pink flower-shaped cloud and lost twenty minutes."
        instruction_text = "Style: energetic school comedy."
        raw_text = role_text + "\n\n[USER_INSTRUCTION]\n" + instruction_text
        raw_request = {
            "raw_text": raw_text,
            "role_text": role_text,
            "user_instruction_text": instruction_text,
            "source_integrity": {
                "raw_text_sha256": input_analysis.sha256_text(raw_text),
                "role_text_sha256": input_analysis.sha256_text(role_text),
                "user_instruction_text_sha256": input_analysis.sha256_text(instruction_text),
                "raw_preserved": True,
            },
        }
        analysis = {
            "schema_version": 1,
            "round_id": "round-000001",
            "analysis_mode": "fixture",
            "source_integrity": dict(raw_request["source_integrity"]),
            "semantic_units": [
                {
                    "id": "unit-wrong-source",
                    "source_channel": "user_instruction",
                    "type": "hidden_setting",
                    "text": "The missing twenty minutes are a mystery.",
                    "raw_excerpt": instruction_text,
                    "derived_summary": "The missing time should be tracked as a mystery.",
                    "confidence": 0.7,
                    "visibility": "gm_only",
                    "persist": False,
                },
                {
                    "id": "unit-style",
                    "source_channel": "user_instruction",
                    "type": "style_guidance",
                    "text": instruction_text,
                    "raw_excerpt": instruction_text,
                    "derived_summary": "Use energetic school comedy style.",
                    "confidence": 0.9,
                    "visibility": "gm_only",
                    "persist": False,
                },
            ],
            "world_updates": {
                "hidden_facts": [],
                "public_facts": [],
                "important_characters": [],
                "retcon_requests": [],
            },
            "narrative_directives": {
                "rewrite_previous_output": False,
                "expand_synopsis_before_continue": True,
                "continue_after_player_action": True,
            },
            "routing": {
                "role_channel": role_text,
                "user_instruction_channel": instruction_text,
                "gm": True,
                "player": True,
                "characters": [],
            },
            "routing_requests": [],
            "capability_requests": [],
            "risks": [],
        }

        normalized, changed = mod._normalize_legacy_semantic_units(
            copy.deepcopy(analysis),
            raw_request,
        )

        self.assertTrue(changed)
        self.assertEqual(normalized["semantic_units"][0]["source_channel"], "role_input")
        self.assertEqual(normalized["semantic_units"][0]["raw_excerpt"], role_text)
        self.assertEqual(normalized["semantic_units"][1]["source_channel"], "user_instruction")
        self.assertEqual(normalized["semantic_units"][1]["raw_excerpt"], instruction_text)
        input_analysis.validate_input_analysis(
            normalized,
            raw_text=raw_text,
            role_text=role_text,
            user_instruction_text=instruction_text,
        )
