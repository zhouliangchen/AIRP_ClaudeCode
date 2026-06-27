import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class LlmSettingsTest(unittest.TestCase):
    def setUp(self):
        import importlib
        self.mod = importlib.import_module("llm_settings")

    def _claude_settings(self, tmp: str, env: dict[str, str]) -> Path:
        path = Path(tmp) / "claude-settings.json"
        path.write_text(json.dumps({"env": env}, ensure_ascii=False), encoding="utf-8")
        return path

    def test_normalize_settings_does_not_inject_code_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = self._claude_settings(
                tmp,
                {"ANTHROPIC_BASE_URL": "http://127.0.0.1:16666"},
            )
            result = self.mod.normalize_settings(None, claude_settings_path=claude_path)

        self.assertEqual(result["cc_switch"], {
            "enabled": False,
            "service_url": "",
        })
        self.assertEqual(result["openai_compatible"], {
            "enabled": False,
            "base_url": "",
            "api_key": "",
            "model": "",
        })
        self.assertEqual(result["image_generation"], {
            "base_url": "",
            "api_key": "",
            "model": "",
        })

    def test_settings_errors_report_missing_sources_without_defaults(self):
        result = self.mod.read_effective_settings(
            Path("missing-frontend.json"),
            claude_settings_path=Path("missing-claude-settings.json"),
            env={},
            local_path=Path("missing-local.json"),
        )

        errors = self.mod.settings_errors(result)

        self.assertEqual(result["cc_switch"], {"enabled": False, "service_url": ""})
        self.assertIn("未启用可用的文本 LLM provider", errors)
        self.assertIn("图片生成 API 缺少 base_url", errors)
        self.assertIn("图片生成 API 缺少 model", errors)
        self.assertIn("图片生成 API 缺少 api_key", errors)

    def test_cc_switch_drops_api_key_and_model(self):
        result = self.mod.normalize_settings(
            {
                "cc_switch": {
                    "enabled": False,
                    "service_url": "http://127.0.0.1:18000",
                    "api_key": "secret",
                    "model": "should-not-persist",
                }
            },
            claude_settings_path=Path("missing-claude-settings.json"),
        )

        self.assertEqual(result["cc_switch"], {
            "enabled": False,
            "service_url": "http://127.0.0.1:18000",
        })
        self.assertNotIn("api_key", result["cc_switch"])
        self.assertNotIn("model", result["cc_switch"])

    def test_read_write_and_redact_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "styles" / "llm_settings.local.json"
            settings = {
                "cc_switch": {"enabled": True, "service_url": "http://127.0.0.1:17777", "api_key": "drop"},
                "openai_compatible": {
                    "enabled": True,
                    "base_url": "https://llm.example/v1",
                    "api_key": "openai-secret",
                    "model": "chat-model",
                },
                "image_generation": {
                    "base_url": "https://image.example/v1",
                    "api_key": "image-secret",
                    "model": "image-model",
                },
            }

            written = self.mod.write_settings(path, settings)
            raw_text = path.read_text(encoding="utf-8")
            read_back = self.mod.read_settings(path, claude_settings_path=Path(tmp) / "missing.json")
            redacted = self.mod.redact_settings(read_back)

        self.assertEqual(written, read_back)
        self.assertTrue(raw_text.endswith("\n"))
        self.assertNotIn("drop", raw_text)
        self.assertEqual(redacted["cc_switch"], {
            "enabled": True,
            "service_url": "http://127.0.0.1:17777",
        })
        self.assertEqual(redacted["openai_compatible"]["api_key"], "")
        self.assertTrue(redacted["openai_compatible"]["api_key_set"])
        self.assertEqual(redacted["image_generation"]["api_key"], "")
        self.assertTrue(redacted["image_generation"]["api_key_set"])

    def test_resolve_claude_code_model_prefers_default_sonnet_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = self._claude_settings(
                tmp,
                {
                    "ANTHROPIC_MODEL": "fallback-model",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet-model",
                    "ANTHROPIC_API_KEY": "anthropic-key",
                    "ANTHROPIC_AUTH_TOKEN": "auth-token",
                },
            )

            model = self.mod.resolve_claude_code_model(claude_path)
            headers = self.mod.claude_code_auth_headers(claude_path)

        self.assertEqual(model, "sonnet-model")
        self.assertEqual(headers, {
            "x-api-key": "anthropic-key",
            "authorization": "Bearer auth-token",
        })

    def test_read_effective_settings_uses_frontend_env_local_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "llm_settings.local.json"
            frontend_path = Path(tmp) / "llm_settings.frontend.json"
            local_path.write_text(
                json.dumps(
                    {
                        "cc_switch": {"enabled": True, "service_url": "http://local-switch"},
                        "openai_compatible": {
                            "enabled": False,
                            "base_url": "https://local-openai/v1",
                            "api_key": "local-openai-key",
                            "model": "local-chat-model",
                        },
                        "image_generation": {
                            "base_url": "https://local-image/v1",
                            "api_key": "local-image-key",
                            "model": "local-image-model",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            frontend_path.write_text(
                json.dumps(
                    {
                        "cc_switch": {"enabled": False, "service_url": "http://frontend-switch"},
                        "openai_compatible": {
                            "enabled": True,
                            "base_url": "https://frontend-openai/v1",
                            "api_key": "",
                            "model": "frontend-chat-model",
                        },
                        "image_generation": {
                            "base_url": "https://frontend-image/v1",
                            "api_key": "frontend-image-key",
                            "model": "",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            claude_path = self._claude_settings(tmp, {"ANTHROPIC_BASE_URL": "http://claude-switch"})
            env = {
                "AIRP_CC_SWITCH_ENABLED": "true",
                "AIRP_CC_SWITCH_SERVICE_URL": "http://env-switch",
                "AIRP_OPENAI_COMPATIBLE_ENABLED": "false",
                "AIRP_OPENAI_COMPATIBLE_BASE_URL": "https://env-openai/v1",
                "AIRP_OPENAI_COMPATIBLE_API_KEY": "env-openai-key",
                "AIRP_OPENAI_COMPATIBLE_MODEL": "env-chat-model",
                "AIRP_IMAGE_GENERATION_BASE_URL": "https://env-image/v1",
                "AIRP_IMAGE_GENERATION_API_KEY": "env-image-key",
                "AIRP_IMAGE_GENERATION_MODEL": "env-image-model",
            }

            result = self.mod.read_effective_settings(
                frontend_path,
                claude_settings_path=claude_path,
                env=env,
                local_path=local_path,
            )

        self.assertEqual(result["cc_switch"], {
            "enabled": False,
            "service_url": "http://frontend-switch",
        })
        self.assertEqual(result["openai_compatible"], {
            "enabled": True,
            "base_url": "https://frontend-openai/v1",
            "api_key": "env-openai-key",
            "model": "frontend-chat-model",
        })
        self.assertEqual(result["image_generation"], {
            "base_url": "https://frontend-image/v1",
            "api_key": "frontend-image-key",
            "model": "env-image-model",
        })

    def test_read_effective_settings_uses_env_over_local_when_frontend_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "llm_settings.local.json"
            frontend_path = Path(tmp) / "llm_settings.frontend.json"
            local_path.write_text(
                json.dumps(
                    {
                        "cc_switch": {"enabled": True, "service_url": "http://local-switch"},
                        "openai_compatible": {
                            "enabled": False,
                            "base_url": "https://local-openai/v1",
                            "api_key": "local-openai-key",
                            "model": "local-chat-model",
                        },
                        "image_generation": {
                            "base_url": "https://local-image/v1",
                            "api_key": "local-image-key",
                            "model": "local-image-model",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            frontend_path.write_text(json.dumps({"cc_switch": {"enabled": ""}}, ensure_ascii=False), encoding="utf-8")
            result = self.mod.read_effective_settings(
                frontend_path,
                claude_settings_path=Path(tmp) / "missing.json",
                env={
                    "AIRP_CC_SWITCH_ENABLED": "false",
                    "AIRP_CC_SWITCH_SERVICE_URL": "http://env-switch",
                    "AIRP_OPENAI_COMPATIBLE_ENABLED": "true",
                    "AIRP_OPENAI_COMPATIBLE_BASE_URL": "https://env-openai/v1",
                    "AIRP_OPENAI_COMPATIBLE_API_KEY": "env-openai-key",
                    "AIRP_OPENAI_COMPATIBLE_MODEL": "env-chat-model",
                    "AIRP_IMAGE_GENERATION_BASE_URL": "https://env-image/v1",
                    "AIRP_IMAGE_GENERATION_API_KEY": "env-image-key",
                    "AIRP_IMAGE_GENERATION_MODEL": "env-image-model",
                },
                local_path=local_path,
            )

        self.assertEqual(result["cc_switch"], {
            "enabled": False,
            "service_url": "http://env-switch",
        })
        self.assertEqual(result["openai_compatible"], {
            "enabled": True,
            "base_url": "https://env-openai/v1",
            "api_key": "env-openai-key",
            "model": "env-chat-model",
        })
        self.assertEqual(result["image_generation"], {
            "base_url": "https://env-image/v1",
            "api_key": "env-image-key",
            "model": "env-image-model",
        })

    def test_read_effective_settings_uses_env_when_local_cc_switch_enabled_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "llm_settings.local.json"
            settings_path.write_text(
                json.dumps({"cc_switch": {"enabled": ""}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = self.mod.read_effective_settings(
                settings_path,
                claude_settings_path=Path(tmp) / "missing.json",
                env={"AIRP_CC_SWITCH_ENABLED": "false"},
                local_path=Path(tmp) / "missing-local.json",
            )

        self.assertFalse(result["cc_switch"]["enabled"])

    def test_read_effective_settings_uses_env_when_local_openai_enabled_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "llm_settings.local.json"
            settings_path.write_text(
                json.dumps({"openai_compatible": {"enabled": "maybe"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = self.mod.read_effective_settings(
                settings_path,
                claude_settings_path=Path(tmp) / "missing.json",
                env={"AIRP_OPENAI_COMPATIBLE_ENABLED": "true"},
                local_path=Path(tmp) / "missing-local.json",
            )

        self.assertTrue(result["openai_compatible"]["enabled"])


if __name__ == "__main__":
    unittest.main()
