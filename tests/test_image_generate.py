import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class ImageGenerateConfigTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("image_generate")
        self.llm_settings = importlib.import_module("llm_settings")
        self.original_frontend_settings_path = self.mod.FRONTEND_SETTINGS_PATH
        self.original_local_settings_path = self.mod.LOCAL_SETTINGS_PATH
        self.original_image_generate_file = self.mod.__file__
        self.original_environ = os.environ.copy()
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "IMAGE_MODEL",
            "AIRP_IMAGE_GENERATION_BASE_URL",
            "AIRP_IMAGE_GENERATION_API_KEY",
            "AIRP_IMAGE_GENERATION_MODEL",
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        self.mod.FRONTEND_SETTINGS_PATH = self.original_frontend_settings_path
        self.mod.LOCAL_SETTINGS_PATH = self.original_local_settings_path
        self.mod.__file__ = self.original_image_generate_file
        os.environ.clear()
        os.environ.update(self.original_environ)

    def _write_settings(
        self,
        tmp: str,
        image_generation: dict[str, str],
        openai_compatible: dict[str, str] | None = None,
        *,
        frontend: bool = True,
    ) -> Path:
        filename = "llm_settings.frontend.json" if frontend else "llm_settings.local.json"
        settings_path = Path(tmp) / "styles" / filename
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"image_generation": image_generation}
        if openai_compatible is not None:
            payload["openai_compatible"] = openai_compatible
        settings_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        if frontend:
            self.mod.FRONTEND_SETTINGS_PATH = settings_path
        else:
            self.mod.LOCAL_SETTINGS_PATH = settings_path
        return settings_path

    def _isolate_image_module_paths(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp) / "repo"
        skills = root / "skills"
        skills.mkdir(parents=True)
        self.mod.__file__ = str(skills / "image_generate.py")
        return root, skills

    def test_load_config_reads_frontend_image_generation_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(
                tmp,
                {
                    "base_url": "https://local-image.example/v1",
                    "api_key": "local-image-key",
                    "model": "local-image-model",
                },
            )

            config = self.mod._load_config(card)

        self.assertEqual(config["base_url"], "https://local-image.example/v1")
        self.assertEqual(config["api_key"], "local-image-key")
        self.assertEqual(config["model"], "local-image-model")

    def test_frontend_image_generation_settings_override_environment_and_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(
                tmp,
                {
                    "base_url": "https://local-image.example/v1",
                    "api_key": "local-image-key",
                    "model": "local-image-model",
                },
                frontend=False,
            )
            self._write_settings(
                tmp,
                {
                    "base_url": "https://frontend-image.example/v1",
                    "api_key": "frontend-image-key",
                    "model": "frontend-image-model",
                },
            )
            os.environ.update(
                {
                    "AIRP_IMAGE_GENERATION_BASE_URL": "https://env-image.example/v1",
                    "AIRP_IMAGE_GENERATION_API_KEY": "env-image-key",
                    "AIRP_IMAGE_GENERATION_MODEL": "env-image-model",
                }
            )

            config = self.mod._load_config(card)

        self.assertEqual(config["base_url"], "https://frontend-image.example/v1")
        self.assertEqual(config["api_key"], "frontend-image-key")
        self.assertEqual(config["model"], "frontend-image-model")

    def test_grouped_environment_overrides_local_and_fills_empty_frontend_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(
                tmp,
                {
                    "base_url": "https://local-image.example/v1",
                    "api_key": "",
                    "model": "",
                },
                frontend=False,
            )
            self._write_settings(
                tmp,
                {
                    "base_url": "",
                    "api_key": "",
                    "model": "",
                },
            )
            os.environ.update(
                {
                    "AIRP_IMAGE_GENERATION_BASE_URL": "https://env-image.example/v1",
                    "AIRP_IMAGE_GENERATION_API_KEY": "env-image-key",
                    "AIRP_IMAGE_GENERATION_MODEL": "env-image-model",
                }
            )

            config = self.mod._load_config(card)

        self.assertEqual(config["base_url"], "https://env-image.example/v1")
        self.assertEqual(config["api_key"], "env-image-key")
        self.assertEqual(config["model"], "env-image-model")

    def test_legacy_image_environment_variables_are_ignored_without_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(tmp, {"base_url": "", "api_key": "", "model": ""})
            self._write_settings(tmp, {"base_url": "", "api_key": "", "model": ""}, frontend=False)
            os.environ.update(
                {
                    "OPENAI_BASE_URL": "https://legacy-env.example/v1",
                    "OPENAI_API_KEY": "legacy-env-key",
                    "IMAGE_MODEL": "legacy-env-model",
                }
            )

            config = self.mod._load_config(card)

        self.assertNotIn("base_url", config)
        self.assertNotIn("api_key", config)
        self.assertNotIn("model", config)

    def test_openai_compatible_text_settings_do_not_feed_image_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(
                tmp,
                {"base_url": "", "api_key": "", "model": ""},
                openai_compatible={
                    "base_url": "https://text.example/v1",
                    "api_key": "text-key",
                    "model": "text-model",
                },
            )
            self._write_settings(tmp, {"base_url": "", "api_key": "", "model": ""}, frontend=False)

            config = self.mod._load_config(card)

        self.assertNotIn("base_url", config)
        self.assertNotIn("api_key", config)
        self.assertNotIn("model", config)

    def test_legacy_image_config_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, skills = self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(
                tmp,
                {
                    "base_url": "https://frontend-image.example/v1",
                    "api_key": "frontend-image-key",
                    "model": "frontend-image-model",
                },
            )
            self._write_settings(
                tmp,
                {
                    "base_url": "https://local-image.example/v1",
                    "api_key": "local-image-key",
                    "model": "local-image-model",
                },
                frontend=False,
            )
            legacy = {
                "base_url": "https://legacy-image.example/v1",
                "api_key": "legacy-image-key",
                "model": "legacy-image-model",
            }
            for path in (
                card / "image_config.local.json",
                skills / "image_config.local.json",
                root / "image_config.local.json",
                root / ".image_api.json",
            ):
                path.write_text(json.dumps(legacy), encoding="utf-8")

            config = self.mod._load_config(card)

        self.assertEqual(config["base_url"], "https://frontend-image.example/v1")
        self.assertEqual(config["api_key"], "frontend-image-key")
        self.assertEqual(config["model"], "frontend-image-model")

    def test_no_code_defaults_apply_when_unified_settings_are_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._isolate_image_module_paths(tmp)
            card = Path(tmp) / "card"
            card.mkdir()
            self._write_settings(tmp, {"base_url": "", "api_key": "", "model": ""})
            self._write_settings(tmp, {"base_url": "", "api_key": "", "model": ""}, frontend=False)

            config = self.mod._load_config(card)

        self.assertNotIn("base_url", config)
        self.assertNotIn("api_key", config)
        self.assertNotIn("model", config)

    def test_call_openai_images_rejects_missing_base_url_without_default(self):
        with self.assertRaisesRegex(RuntimeError, "image_generation.base_url"):
            self.mod._call_openai_images("draw", "image-model", "1024x1024", {"api_key": "secret"})


if __name__ == "__main__":
    unittest.main()
