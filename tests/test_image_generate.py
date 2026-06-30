import importlib
import json
import os
import sys
import tempfile
import unittest
from tests.module_aliases import load_repo_module
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class ImageGenerateConfigTest(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("image_generate")
        self.llm_settings = load_repo_module("llm_settings")
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

    def test_safe_card_relative_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()

            for value in ("../escape.png", "/tmp/escape.png", "C:/tmp/escape.png", "C:tmp/escape.png"):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        self.mod._safe_card_relative_path(card, value)

    def test_build_manifest_item_preserves_extended_asset_metadata(self):
        item = self.mod._build_manifest_item(
            image_id="scene-0001",
            kind="scene",
            model="test-model",
            prompt="draw scene",
            rel_path="generated/images/scene-0001.png",
            target="scene_illustration",
            created_at=1234567890,
            references=["generated/characters/苏黎/苏黎.png"],
            job_id="scene-round-000004",
            round_id="round-000004",
            characters=["苏黎"],
        )

        self.assertEqual(item["source_job_id"], "scene-round-000004")
        self.assertEqual(item["references"], ["generated/characters/苏黎/苏黎.png"])
        self.assertEqual(item["characters"], ["苏黎"])
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["display_policy"], "story_inline")
        self.assertEqual(item["round_id"], "round-000004")

    def test_build_manifest_item_marks_character_reference_as_hidden_reference(self):
        item = self.mod._build_manifest_item(
            image_id="character-suli-reference",
            kind="portrait",
            model="test-model",
            prompt="draw character reference",
            rel_path="generated/characters/苏黎/苏黎.png",
            target="character_reference",
            created_at=1234567890,
            job_id="character-suli-reference",
            characters=["苏黎"],
        )

        self.assertEqual(item["display_policy"], "hidden_reference")
        self.assertNotIn("round_id", item)

    def test_round_id_is_inferred_from_job_id_only_for_round_pattern(self):
        self.assertEqual(
            self.mod._resolve_round_id(None, "scene-round-000003"),
            "round-000003",
        )
        self.assertEqual(
            self.mod._resolve_round_id("round-000004", "scene-round-000003"),
            "round-000004",
        )
        self.assertEqual(self.mod._resolve_round_id(None, "scene-round-3"), "")

    def test_write_job_status_writes_generated_job_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()

            self.mod._write_job_status(
                card,
                "scene-round-000004",
                {"status": "completed", "path": "generated/images/scene-0001.png"},
            )

            job_path = card / "generated" / "jobs" / "scene-round-000004.json"
            self.assertTrue(job_path.exists())
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["path"], "generated/images/scene-0001.png")

    def test_reconcile_manifest_restores_completed_job_assets_lost_by_parallel_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            jobs = card / "generated" / "jobs"
            jobs.mkdir(parents=True)
            manifest_path = card / ".card_assets.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "id": "character-苏黎-reference",
                                "kind": "character_reference",
                                "path": "generated/characters/苏黎/苏黎.png",
                                "status": "completed",
                                "source_job_id": "character-苏黎-reference",
                                "display_policy": "hidden_reference",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (jobs / "scene-round-000003.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "path": "generated/images/scene-0001.png",
                        "asset": {
                            "id": "scene-0001",
                            "kind": "scene",
                            "path": "generated/images/scene-0001.png",
                            "status": "completed",
                            "source_job_id": "scene-round-000003",
                            "display_policy": "story_inline",
                            "round_id": "round-000003",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.mod._reconcile_manifest_with_completed_jobs(card)
            self.mod._reconcile_manifest_with_completed_jobs(card)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ids = [item["id"] for item in manifest["images"]]
            self.assertEqual(ids.count("scene-0001"), 1)
            self.assertIn("character-苏黎-reference", ids)
            self.assertIn("scene-0001", ids)

    def test_refresh_frontend_assets_rebuilds_served_content_js_with_completed_image(self):
        served_content = SKILLS / "styles" / "content.js"
        original_content = served_content.read_text(encoding="utf-8") if served_content.exists() else None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                card = Path(tmp) / "card"
                jobs = card / "generated" / "jobs"
                jobs.mkdir(parents=True)
                (card / "chat_log.json").write_text(
                    json.dumps(
                        [
                            {
                                "index": 2,
                                "ai": "<content><p>Round three text.</p></content>",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (jobs / "scene-round-000003.json").write_text(
                    json.dumps(
                        {
                            "job_id": "scene-round-000003",
                            "status": "completed",
                            "asset": {
                                "id": "scene-0001",
                                "kind": "scene_illustration",
                                "path": "generated/images/scene-0001.png",
                                "display_policy": "story_inline",
                                "round_id": "round-000003",
                                "status": "completed",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                result = self.mod._refresh_frontend_assets(card)

                self.assertTrue(result["content_js"], result)
                content_js = served_content.read_text(encoding="utf-8")
                self.assertIn("generated/images/scene-0001.png", content_js)
                self.assertIn("/api/card_asset/generated/images/scene-0001.png", content_js)
        finally:
            if original_content is None:
                try:
                    served_content.unlink()
                except FileNotFoundError:
                    pass
            else:
                served_content.write_text(original_content, encoding="utf-8")

    def test_spawn_async_propagates_reference_output_path_job_id_and_character(self):
        args = SimpleNamespace(
            card_folder="card-folder",
            prompt="draw scene",
            kind="scene",
            target="scene_illustration",
            size="1024x1024",
            model="test-model",
            dry_run=True,
            reference=["generated/characters/苏黎/苏黎.png", "generated/images/scene-prev.png"],
            output_path="generated/characters/苏黎/苏黎.png",
            job_id="scene-round-000004",
            round_id="round-000004",
            character=["苏黎", "旁白"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card-folder"
            card.mkdir()
            args.card_folder = str(card)
            popen = mock.Mock()
            with mock.patch.object(self.mod.subprocess, "Popen", popen):
                result = self.mod._spawn_async(args)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--reference", cmd)
        self.assertIn("generated/characters/苏黎/苏黎.png", cmd)
        self.assertIn("generated/images/scene-prev.png", cmd)
        self.assertIn("--output-path", cmd)
        self.assertIn("generated/characters/苏黎/苏黎.png", cmd)
        self.assertIn("--job-id", cmd)
        self.assertIn("scene-round-000004", cmd)
        self.assertIn("--round-id", cmd)
        self.assertIn("round-000004", cmd)
        self.assertIn("--character", cmd)
        self.assertIn("苏黎", cmd)
        self.assertIn("旁白", cmd)

    def test_spawn_async_uses_unique_log_paths_for_same_second_jobs(self):
        args = SimpleNamespace(
            card_folder="card-folder",
            prompt="draw scene",
            kind="scene",
            target="scene_illustration",
            size="1024x1024",
            model="test-model",
            dry_run=True,
            reference=[],
            output_path=None,
            job_id="scene-round-000004",
            round_id="round-000004",
            character=[],
        )

        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card-folder"
            card.mkdir()
            args.card_folder = str(card)
            with mock.patch.object(self.mod.subprocess, "Popen"):
                with mock.patch.object(self.mod.time, "time", return_value=1234567890):
                    first = self.mod._spawn_async(args)
                    second = self.mod._spawn_async(args)

        self.assertNotEqual(first["log"], second["log"])
        self.assertTrue(Path(first["log"]).name.startswith("image-job-scene-round-000004-"))
        self.assertTrue(Path(second["log"]).name.startswith("image-job-scene-round-000004-"))

    def test_main_async_invalid_output_path_exits_with_error_without_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            job_path = card / "generated" / "jobs" / "scene-round-000004.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--output-path",
                "../escape.png",
                "--job-id",
                "scene-round-000004",
                "--async",
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(self.mod.subprocess, "Popen") as popen:
                    with self.assertRaises(SystemExit) as exc:
                        self.mod.main()

            self.assertEqual(exc.exception.code, 2)
            popen.assert_not_called()
            self.assertTrue(job_path.exists())
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "invalid_path")

    def test_main_async_reference_spawns_worker_instead_of_deferring(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            reference = card / "generated" / "characters" / "Ada" / "Ada.png"
            reference.parent.mkdir(parents=True)
            reference.write_bytes(b"png")
            job_path = card / "generated" / "jobs" / "scene-round-000004.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--reference",
                "generated/characters/Ada/Ada.png",
                "--job-id",
                "scene-round-000004",
                "--async",
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(self.mod.subprocess, "Popen") as popen:
                    with self.assertRaises(SystemExit) as exc:
                        self.mod.main()

            self.assertEqual(exc.exception.code, 0)
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertIn("--reference", command)
            self.assertIn("generated/characters/Ada/Ada.png", command)
            self.assertFalse(job_path.exists())

    def test_main_dry_run_custom_output_path_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            portrait = card / "generated" / "characters" / "苏黎" / "苏黎.png"
            portrait.parent.mkdir(parents=True)
            original_bytes = b"portrait-bytes"
            portrait.write_bytes(original_bytes)
            manifest_path = card / ".card_assets.json"
            job_path = card / "generated" / "jobs" / "character-suli-reference.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw character reference",
                "--kind",
                "portrait",
                "--target",
                "character_reference",
                "--dry-run",
                "--output-path",
                "generated/characters/苏黎/苏黎.png",
                "--job-id",
                "character-suli-reference",
            ]

            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    self.mod,
                    "_refresh_frontend_assets",
                    return_value={"content_js": False, "error": None},
                ):
                    with self.assertRaises(SystemExit) as exc:
                        self.mod.main()

            self.assertEqual(exc.exception.code, 0)
            self.assertEqual(portrait.read_bytes(), original_bytes)
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["images"]), 1)
            self.assertEqual(manifest["images"][0]["path"], "generated/characters/苏黎/苏黎.png")
            self.assertEqual(manifest["images"][0]["display_policy"], "hidden_reference")
            self.assertTrue(job_path.exists())
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["asset"]["display_policy"], "hidden_reference")

    def test_main_dry_run_scene_writes_story_inline_and_inferred_round_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            manifest_path = card / ".card_assets.json"
            job_path = card / "generated" / "jobs" / "scene-round-000003.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--kind",
                "scene_illustration",
                "--target",
                "scene_illustration",
                "--dry-run",
                "--job-id",
                "scene-round-000003",
            ]

            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    self.mod,
                    "_refresh_frontend_assets",
                    return_value={"content_js": False, "error": None},
                ):
                    with self.assertRaises(SystemExit) as exc:
                        self.mod.main()

            self.assertEqual(exc.exception.code, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            item = manifest["images"][0]
            self.assertEqual(item["display_policy"], "story_inline")
            self.assertEqual(item["round_id"], "round-000003")
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["asset"]["display_policy"], "story_inline")
            self.assertEqual(payload["asset"]["round_id"], "round-000003")

    def test_main_persists_frontend_refresh_error_on_completed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            job_path = card / "generated" / "jobs" / "scene-round-000003.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--kind",
                "scene_illustration",
                "--target",
                "scene_illustration",
                "--dry-run",
                "--job-id",
                "scene-round-000003",
            ]

            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    self.mod,
                    "_refresh_frontend_assets",
                    return_value={"content_js": False, "error": "content rebuild failed"},
                ):
                    with self.assertRaises(SystemExit) as exc:
                        self.mod.main()

            self.assertEqual(exc.exception.code, 0)
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["frontend"], {"content_js": False, "error": "content rebuild failed"})
            self.assertEqual(payload["frontend_sync_error"], "content rebuild failed")

    def test_call_openai_images_rejects_missing_base_url_without_default(self):
        with self.assertRaisesRegex(RuntimeError, "image_generation.base_url"):
            self.mod._call_openai_images("draw", "image-model", "1024x1024", {"api_key": "secret"})

    def test_call_openai_images_with_reference_uses_edits_multipart(self):
        requests = []

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"data": [{"b64_json": "aW1hZ2UtYnl0ZXM="}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            requests.append(request)
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "ref.png"
            reference.write_bytes(b"png-bytes")

            with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
                image = self.mod._call_openai_images(
                    "draw with reference",
                    "image-model",
                    "1024x1024",
                    {
                        "base_url": "https://image.example/v1",
                        "api_key": "secret",
                    },
                    references=[reference],
                )

        self.assertEqual(image, b"image-bytes")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.full_url, "https://image.example/v1/images/edits")
        self.assertIn("multipart/form-data", request.headers["Content-type"])
        body = request.data
        self.assertIn(b'name="prompt"', body)
        self.assertIn(b"draw with reference", body)
        self.assertIn(b'name="image"; filename="ref.png"', body)
        self.assertIn(b"png-bytes", body)

    def test_main_api_failure_defers_job_for_frontend_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            job_path = card / "generated" / "jobs" / "scene-round-000004.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--job-id",
                "scene-round-000004",
            ]

            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    self.mod,
                    "_load_config",
                    return_value={
                        "base_url": "https://image.example/v1",
                        "api_key": "secret",
                        "model": "image-model",
                    },
                ):
                    with mock.patch.object(
                        self.mod,
                        "_call_openai_images",
                        side_effect=RuntimeError("HTTP 404: Images API is not supported"),
                    ):
                        with self.assertRaises(SystemExit) as exc:
                            self.mod.main()

            self.assertEqual(exc.exception.code, 1)
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "deferred")
            self.assertEqual(payload["reason"], "image_generation_failed")
            self.assertIn("Images API is not supported", payload["error"])

    def test_main_reference_unsupported_defers_with_specific_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            reference = card / "generated" / "characters" / "Ada" / "Ada.png"
            reference.parent.mkdir(parents=True)
            reference.write_bytes(b"png")
            job_path = card / "generated" / "jobs" / "scene-round-000004.json"
            argv = [
                "image_generate.py",
                str(card),
                "--prompt",
                "draw scene",
                "--reference",
                "generated/characters/Ada/Ada.png",
                "--job-id",
                "scene-round-000004",
            ]

            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    self.mod,
                    "_load_config",
                    return_value={
                        "base_url": "https://image.example/v1",
                        "api_key": "secret",
                        "model": "image-model",
                    },
                ):
                    with mock.patch.object(
                        self.mod,
                        "_call_openai_images",
                        side_effect=self.mod.ReferenceImageNotSupported("edits endpoint is not available"),
                    ):
                        with self.assertRaises(SystemExit) as exc:
                            self.mod.main()

            self.assertEqual(exc.exception.code, 1)
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "deferred")
            self.assertEqual(payload["reason"], "reference_image_not_supported")
            self.assertEqual(payload["references"], ["generated/characters/Ada/Ada.png"])


if __name__ == "__main__":
    unittest.main()
