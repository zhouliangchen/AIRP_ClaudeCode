import importlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class CodeStructureTests(unittest.TestCase):
    def test_internal_runtime_modules_live_in_packages(self):
        expected_modules = [
            "agents.actor.memory_store",
            "agents.actor.context_renderer",
            "agents.actor.runtime",
            "agents.assets_ui.runtime",
            "agents.projection.agent",
            "agents.subgm.turn_loop",
            "runtime.round_runtime",
            "runtime.agent_run",
            "runtime.agent_messages",
            "runtime.agent_intents",
            "capabilities.registry",
            "capabilities.executors",
            "llm.runner",
            "frontend.handler",
            "importing.card",
            "domain.objective_world",
        ]
        for module_name in expected_modules:
            with self.subTest(module_name=module_name):
                importlib.import_module(module_name)

    def test_old_flat_internal_modules_are_removed(self):
        removed = [
            "agent_run.py",
            "agent_messages.py",
            "agent_intents.py",
            "agent_prompts.py",
            "agent_runtime_pump.py",
            "actor_memory_store.py",
            "assets_ui_runtime.py",
            "capability_registry.py",
            "llm_runner.py",
            "handler.py",
            "import_card.py",
            "objective_world.py",
            "subgm_turn_loop.py",
        ]
        for filename in removed:
            with self.subTest(filename=filename):
                self.assertFalse((SKILLS / filename).exists(), filename)

    def test_public_entry_scripts_remain_at_top_level(self):
        entries = [
            "start_server.py",
            "server.py",
            "import_prepare.py",
            "round_prepare.py",
            "input_analysis_apply.py",
            "rp_generate_cli.py",
            "round_deliver.py",
            "image_generate.py",
            "control_plane_smoke.py",
        ]
        for filename in entries:
            with self.subTest(filename=filename):
                self.assertTrue((SKILLS / filename).exists(), filename)

    def test_frontend_handler_writes_to_served_styles_directory(self):
        handler = importlib.import_module("frontend.handler")
        handler = importlib.reload(handler)

        self.assertEqual(handler.STYLES.resolve(), (SKILLS / "styles").resolve())

    def test_legacy_routing_compatibility_runtime_is_removed(self):
        input_apply = (SKILLS / "input_analysis_apply.py").read_text(encoding="utf-8")
        routing_requests = (SKILLS / "capabilities" / "input_routing_requests.py").read_text(encoding="utf-8")
        registry = (SKILLS / "capabilities" / "registry.py").read_text(encoding="utf-8")

        self.assertNotIn("_normalize_legacy_routing_requests", input_apply)
        self.assertNotIn("legacy_routing_request_to_capability", input_apply)
        self.assertNotIn("def process_routing_requests", routing_requests)
        self.assertNotIn("legacy_routing_request_to_capability", routing_requests)
        self.assertNotIn("def legacy_routing_request_to_capability", registry)


if __name__ == "__main__":
    unittest.main()
