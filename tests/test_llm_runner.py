import importlib
import sys
import traceback
import unittest
from tests.module_aliases import load_repo_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class LlmRunnerTest(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("llm.runner", None)
        self.mod = load_repo_module("llm_runner")
        self.original_read_settings = self.mod.llm_settings.read_effective_settings
        self.original_resolve_model = self.mod.llm_settings.resolve_claude_code_model_tier
        self.original_auth_headers = self.mod.llm_settings.claude_code_auth_headers
        self.original_complete = self.mod.llm_provider.complete

    def tearDown(self):
        self.mod.llm_settings.read_effective_settings = self.original_read_settings
        self.mod.llm_settings.resolve_claude_code_model_tier = self.original_resolve_model
        self.mod.llm_settings.claude_code_auth_headers = self.original_auth_headers
        self.mod.llm_provider.complete = self.original_complete

    def _settings(self, *, cc_enabled=True, openai_enabled=False):
        return {
            "cc_switch": {
                "enabled": cc_enabled,
                "service_url": "http://127.0.0.1:15721",
            },
            "openai_compatible": {
                "enabled": openai_enabled,
                "core": {
                    "base_url": "https://core.example/v1",
                    "api_key": "core-key",
                    "model": "core-model",
                },
                "review": {
                    "base_url": "https://review.example/v1",
                    "api_key": "review-key",
                    "model": "review-model",
                },
                "actor": {
                    "base_url": "https://actor.example/v1",
                    "api_key": "actor-key",
                    "model": "actor-model",
                },
            },
        }

    def _patch_settings(self, settings, *, models=None, headers=None):
        model_by_tier = models or {"core": "claude-opus", "review": "claude-sonnet", "actor": "claude-haiku"}
        self.mod.llm_settings.read_effective_settings = lambda: settings
        self.mod.llm_settings.resolve_claude_code_model_tier = lambda tier: model_by_tier.get(tier, "")
        self.mod.llm_settings.claude_code_auth_headers = lambda: dict(headers or {"x-api-key": "anthropic-key"})

    def test_prefers_cc_switch_when_both_providers_enabled(self):
        self._patch_settings(self._settings(cc_enabled=True, openai_enabled=True))
        calls = []

        def fake_complete(provider, prompt, *, agent_key, config):
            calls.append((provider, prompt, agent_key, config))
            return {
                "text": "cc result",
                "provider": provider,
                "model": config["model"],
                "usage": {"input_tokens": 1},
                "raw_response": {"ok": True},
                "status": 200,
            }

        self.mod.llm_provider.complete = fake_complete

        text = self.mod.run_llm_agent("gm", "prompt", ROOT)

        self.assertEqual(text, "cc result")
        self.assertEqual([call[0] for call in calls], ["cc_switch"])
        self.assertEqual(calls[0][3]["model"], "claude-opus")
        self.assertEqual(calls[0][3]["headers"], {"x-api-key": "anthropic-key"})
        self.assertNotIn("api_key", calls[0][3])
        self.assertEqual(self.mod.get_last_result()["provider"], "cc_switch")

    def test_cc_switch_selects_model_tier_by_agent_key(self):
        self._patch_settings(self._settings(cc_enabled=True, openai_enabled=False))
        calls = []

        def fake_complete(provider, prompt, *, agent_key, config):
            calls.append((agent_key, config["model"]))
            return {
                "text": "ok",
                "provider": provider,
                "model": config["model"],
                "usage": {},
                "raw_response": {},
                "status": 200,
            }

        self.mod.llm_provider.complete = fake_complete

        self.mod.run_llm_agent("gm", "prompt", ROOT)
        self.mod.run_llm_agent("projection", "prompt", ROOT)
        self.mod.run_llm_agent("player", "prompt", ROOT)
        self.mod.run_llm_agent("character:苏黎", "prompt", ROOT)

        self.assertEqual(calls, [
            ("gm", "claude-opus"),
            ("projection", "claude-sonnet"),
            ("player", "claude-haiku"),
            ("character:苏黎", "claude-haiku"),
        ])

    def test_falls_back_to_openai_compatible_when_cc_switch_fails(self):
        self._patch_settings(self._settings(cc_enabled=True, openai_enabled=True))
        calls = []

        def fake_complete(provider, prompt, *, agent_key, config):
            calls.append(provider)
            if provider == "cc_switch":
                raise RuntimeError("cc down")
            return {
                "text": "openai result",
                "provider": provider,
                "model": config["model"],
                "usage": {"total_tokens": 3},
                "raw_response": {"choices": []},
                "status": 200,
            }

        self.mod.llm_provider.complete = fake_complete

        text = self.mod.run_llm_agent("story", "prompt", ROOT)

        self.assertEqual(text, "openai result")
        self.assertEqual(calls, ["cc_switch", "openai_compatible"])
        self.assertEqual(self.mod.get_last_result()["provider"], "openai_compatible")

    def test_uses_openai_compatible_when_it_is_the_only_enabled_provider(self):
        self._patch_settings(self._settings(cc_enabled=False, openai_enabled=True))
        calls = []

        def fake_complete(provider, prompt, *, agent_key, config):
            calls.append((provider, config))
            return {
                "text": "openai only",
                "provider": provider,
                "model": config["model"],
                "usage": {},
                "raw_response": {},
                "status": 200,
            }

        self.mod.llm_provider.complete = fake_complete

        text = self.mod.run_llm_agent("critic", "prompt", ROOT)

        self.assertEqual(text, "openai only")
        self.assertEqual(calls, [("openai_compatible", self._settings(cc_enabled=False, openai_enabled=True)["openai_compatible"]["core"])])

    def test_openai_compatible_selects_model_tier_by_agent_key(self):
        self._patch_settings(self._settings(cc_enabled=False, openai_enabled=True))
        calls = []

        def fake_complete(provider, prompt, *, agent_key, config):
            calls.append((agent_key, config["base_url"], config["api_key"], config["model"]))
            return {
                "text": "ok",
                "provider": provider,
                "model": config["model"],
                "usage": {},
                "raw_response": {},
                "status": 200,
            }

        self.mod.llm_provider.complete = fake_complete

        self.mod.run_llm_agent("gm", "prompt", ROOT)
        self.mod.run_llm_agent("projection", "prompt", ROOT)
        self.mod.run_llm_agent("player", "prompt", ROOT)
        self.mod.run_llm_agent("character:苏黎", "prompt", ROOT)

        self.assertEqual(calls, [
            ("gm", "https://core.example/v1", "core-key", "core-model"),
            ("projection", "https://review.example/v1", "review-key", "review-model"),
            ("player", "https://actor.example/v1", "actor-key", "actor-model"),
            ("character:苏黎", "https://actor.example/v1", "actor-key", "actor-model"),
        ])


    def test_raises_when_no_provider_is_enabled(self):
        self._patch_settings(self._settings(cc_enabled=False, openai_enabled=False))

        with self.assertRaisesRegex(self.mod.LlmRunnerError, "No enabled LLM provider"):
            self.mod.run_llm_agent("gm", "prompt", ROOT)

    def test_raises_when_enabled_cc_switch_has_no_claude_code_model(self):
        self._patch_settings(self._settings(cc_enabled=True, openai_enabled=False), models={"core": ""})

        with self.assertRaisesRegex(self.mod.LlmRunnerError, "cc_switch.*model"):
            self.mod.run_llm_agent("gm", "prompt", ROOT)

    def test_raises_when_openai_compatible_is_enabled_without_model(self):
        settings = self._settings(cc_enabled=False, openai_enabled=True)
        settings["openai_compatible"]["core"]["model"] = ""
        self._patch_settings(settings)

        with self.assertRaisesRegex(self.mod.LlmRunnerError, "openai_compatible.*model"):
            self.mod.run_llm_agent("gm", "prompt", ROOT)

    def test_dual_provider_failure_redacts_secrets_from_runner_error(self):
        settings = self._settings(cc_enabled=True, openai_enabled=True)
        settings["openai_compatible"]["core"]["api_key"] = "openai-secret"
        self._patch_settings(
            settings,
            headers={
                "x-api-key": "cc-header-secret",
                "authorization": "Bearer cc-bearer-token",
            },
        )

        def fake_complete(provider, prompt, *, agent_key, config):
            if provider == "cc_switch":
                raise RuntimeError(
                    "cc failed with cc-header-secret and Authorization: Bearer cc-bearer-token"
                )
            raise RuntimeError(
                "openai failed with openai-secret and Authorization: Bearer openai-error-token"
            )

        self.mod.llm_provider.complete = fake_complete

        with self.assertRaises(self.mod.LlmRunnerError) as caught:
            self.mod.run_llm_agent("gm", "prompt", ROOT)

        message = str(caught.exception)
        self.assertIn("cc_switch failed", message)
        self.assertIn("openai_compatible failed", message)
        self.assertNotIn("cc-header-secret", message)
        self.assertNotIn("cc-bearer-token", message)
        self.assertNotIn("openai-secret", message)
        self.assertNotIn("openai-error-token", message)
        self.assertIn("Bearer [redacted]", message)
        cause = caught.exception.__cause__
        if cause is not None:
            cause_text = str(cause)
            self.assertNotIn("cc-header-secret", cause_text)
            self.assertNotIn("cc-bearer-token", cause_text)
            self.assertNotIn("openai-secret", cause_text)
            self.assertNotIn("openai-error-token", cause_text)
        formatted = "".join(
            traceback.format_exception(
                type(caught.exception),
                caught.exception,
                caught.exception.__traceback__,
            )
        )
        self.assertNotIn("cc-header-secret", formatted)
        self.assertNotIn("cc-bearer-token", formatted)
        self.assertNotIn("openai-secret", formatted)
        self.assertNotIn("openai-error-token", formatted)

    def test_get_last_result_returns_copy(self):
        self._patch_settings(self._settings(cc_enabled=False, openai_enabled=True))
        self.mod.llm_provider.complete = lambda provider, prompt, *, agent_key, config: {
            "text": "ok",
            "provider": provider,
            "model": config["model"],
            "usage": {},
            "raw_response": {},
            "status": 200,
        }

        self.mod.run_llm_agent("gm", "prompt", ROOT)
        result = self.mod.get_last_result()
        result["provider"] = "mutated"

        self.assertEqual(self.mod.get_last_result()["provider"], "openai_compatible")


if __name__ == "__main__":
    unittest.main()
