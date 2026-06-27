import json
import http.client
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        pass


class RawBytesResponse:
    def __init__(self, raw: bytes, status: int = 200):
        self.raw = raw
        self.status = status

    def read(self) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        pass


class UnreadableResponse:
    def read(self) -> bytes:
        raise OSError("cannot read error body")

    def close(self):
        pass


class CapturingUrlopen:
    def __init__(self, payload: dict, status: int = 200):
        self.response = FakeResponse(payload, status=status)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        return self.response


class RawBytesUrlopen:
    def __init__(self, raw: bytes, status: int = 200):
        self.response = RawBytesResponse(raw, status=status)

    def __call__(self, request, timeout=None):
        return self.response


class FailingUrlopen:
    def __call__(self, request, timeout=None):
        raise URLError("service unavailable")


class HttpExceptionUrlopen:
    def __call__(self, request, timeout=None):
        raise http.client.HTTPException("connection closed early")


class LlmProviderTest(unittest.TestCase):
    def setUp(self):
        import importlib
        self.mod = importlib.import_module("llm_provider")

    def _request_json(self, capture: CapturingUrlopen) -> dict:
        request, _timeout = capture.requests[-1]
        return json.loads(request.data.decode("utf-8"))

    def test_openai_compatible_posts_chat_completion_and_extracts_text(self):
        capture = CapturingUrlopen(
            {
                "model": "chat-model",
                "choices": [{"message": {"content": "你好，世界"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        )

        result = self.mod.complete_openai_compatible(
            "Say hi",
            agent_key="gm",
            config={
                "base_url": "https://llm.example/v1/",
                "api_key": "secret-key",
                "model": "chat-model",
            },
            urlopen=capture,
        )

        request, timeout = capture.requests[-1]
        body = self._request_json(capture)
        self.assertEqual(request.full_url, "https://llm.example/v1/chat/completions")
        self.assertEqual(timeout, 300)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(body, {
            "model": "chat-model",
            "messages": [{"role": "user", "content": "Say hi"}],
            "temperature": 0,
        })
        self.assertEqual(result["provider"], "openai_compatible")
        self.assertEqual(result["agent_key"], "gm")
        self.assertEqual(result["text"], "你好，世界")
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "completion_tokens": 4})
        self.assertEqual(result["model"], "chat-model")
        self.assertEqual(result["status"], 200)

    def test_cc_switch_posts_anthropic_message_and_extracts_text_blocks(self):
        capture = CapturingUrlopen(
            {
                "model": "claude-sonnet",
                "content": [
                    {"type": "text", "text": "第一段"},
                    {"type": "tool_use", "name": "ignored"},
                    {"type": "text", "text": "第二段"},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 6},
            }
        )

        result = self.mod.complete_cc_switch(
            "Continue",
            agent_key="story",
            config={
                "service_url": "http://127.0.0.1:15721/",
                "model": "claude-sonnet",
                "max_tokens": 2048,
                "headers": {"x-api-key": "local-secret"},
            },
            urlopen=capture,
        )

        request, timeout = capture.requests[-1]
        body = self._request_json(capture)
        self.assertEqual(request.full_url, "http://127.0.0.1:15721/v1/messages")
        self.assertEqual(timeout, 300)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(request.get_header("X-api-key"), "local-secret")
        self.assertEqual(body, {
            "model": "claude-sonnet",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": "Continue"}],
        })
        self.assertEqual(result["provider"], "cc_switch")
        self.assertEqual(result["agent_key"], "story")
        self.assertEqual(result["text"], "第一段\n第二段")
        self.assertEqual(result["usage"], {"input_tokens": 5, "output_tokens": 6})
        self.assertEqual(result["model"], "claude-sonnet")
        self.assertEqual(result["status"], 200)

    def test_cc_switch_uses_large_default_max_tokens_when_not_configured(self):
        capture = CapturingUrlopen(
            {
                "model": "claude-sonnet",
                "content": [{"type": "text", "text": "{}"}],
                "usage": {},
            }
        )

        self.mod.complete_cc_switch(
            "Continue",
            agent_key="input_analyst",
            config={
                "service_url": "http://127.0.0.1:15721",
                "model": "claude-sonnet",
            },
            urlopen=capture,
        )

        body = self._request_json(capture)
        self.assertEqual(body["max_tokens"], 800000)

    def test_missing_required_config_raises_provider_error(self):
        with self.assertRaisesRegex(self.mod.LlmProviderError, "openai_compatible.*api_key"):
            self.mod.complete_openai_compatible(
                "prompt",
                agent_key="gm",
                config={"base_url": "https://llm.example/v1", "model": "chat-model"},
                urlopen=CapturingUrlopen({}),
            )

        with self.assertRaisesRegex(self.mod.LlmProviderError, "cc_switch.*model"):
            self.mod.complete_cc_switch(
                "prompt",
                agent_key="gm",
                config={"service_url": "http://127.0.0.1:15721"},
                urlopen=CapturingUrlopen({}),
            )

    def test_complete_dispatches_known_providers(self):
        capture = CapturingUrlopen({"model": "m", "choices": [{"message": {"content": "ok"}}]})

        result = self.mod.complete(
            provider="openai_compatible",
            prompt="prompt",
            agent_key="critic",
            config={"base_url": "https://llm.example/v1", "api_key": "secret", "model": "m"},
            urlopen=capture,
        )

        self.assertEqual(result["text"], "ok")
        with self.assertRaisesRegex(self.mod.LlmProviderError, "unknown provider"):
            self.mod.complete("bogus", "prompt", agent_key="critic", config={}, urlopen=capture)

    def test_test_connection_returns_status_preview_and_does_not_raise(self):
        success = CapturingUrlopen(
            {
                "model": "chat-model",
                "choices": [{"message": {"content": "connection looks good"}}],
                "usage": {},
            }
        )
        result = self.mod.test_connection(
            "openai_compatible",
            {"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
            urlopen=success,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "openai_compatible")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["model"], "chat-model")
        self.assertEqual(result["response_preview"], "connection looks good")

        failed = self.mod.test_connection(
            "cc_switch",
            {"service_url": "http://127.0.0.1:15721", "model": "claude-sonnet"},
            urlopen=FailingUrlopen(),
        )

        self.assertFalse(failed["ok"])
        self.assertEqual(failed["provider"], "cc_switch")
        self.assertIn("cc_switch", failed["error"])

    def test_invalid_json_response_raises_provider_error(self):
        with self.assertRaisesRegex(self.mod.LlmProviderError, "openai_compatible.*invalid JSON"):
            self.mod.complete_openai_compatible(
                "prompt",
                agent_key="gm",
                config={"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
                urlopen=RawBytesUrlopen(b"{not-json"),
            )

    def test_non_object_json_response_raises_provider_error(self):
        with self.assertRaisesRegex(self.mod.LlmProviderError, "openai_compatible.*object"):
            self.mod.complete_openai_compatible(
                "prompt",
                agent_key="gm",
                config={"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
                urlopen=RawBytesUrlopen(b"[]"),
            )

    def test_test_connection_returns_error_for_decode_and_http_client_failures(self):
        decode_failed = self.mod.test_connection(
            "openai_compatible",
            {"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
            urlopen=RawBytesUrlopen(b"\xff\xfe"),
        )
        self.assertFalse(decode_failed["ok"])
        self.assertEqual(decode_failed["provider"], "openai_compatible")
        self.assertIn("openai_compatible", decode_failed["error"])
        self.assertNotIn("secret-key", decode_failed["error"])

        http_failed = self.mod.test_connection(
            "cc_switch",
            {"service_url": "http://127.0.0.1:15721", "model": "claude-sonnet"},
            urlopen=HttpExceptionUrlopen(),
        )
        self.assertFalse(http_failed["ok"])
        self.assertEqual(http_failed["provider"], "cc_switch")
        self.assertIn("HTTP client error", http_failed["error"])

    def test_provider_errors_do_not_expose_api_keys(self):
        def fail_with_http_error(request, timeout=None):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                FakeResponse({"error": {"message": "bad auth for secret-key"}}, status=401),
            )

        with self.assertRaises(self.mod.LlmProviderError) as caught:
            self.mod.complete_openai_compatible(
                "prompt",
                agent_key="gm",
                config={"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
                urlopen=fail_with_http_error,
            )

        self.assertNotIn("secret-key", str(caught.exception))
        self.assertIn("openai_compatible", str(caught.exception))

    def test_http_error_body_redacts_api_key_and_header_token(self):
        def fail_with_http_error(request, timeout=None):
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                RawBytesResponse(b'{"error":"api-key-secret and header-token-secret rejected"}', status=403),
            )

        with self.assertRaises(self.mod.LlmProviderError) as caught:
            self.mod.complete_cc_switch(
                "prompt",
                agent_key="gm",
                config={
                    "service_url": "http://127.0.0.1:15721",
                    "model": "claude-sonnet",
                    "headers": {
                        "x-api-key": "api-key-secret",
                        "authorization": "Bearer header-token-secret",
                    },
                },
                urlopen=fail_with_http_error,
            )

        text = str(caught.exception)
        self.assertIn("cc_switch", text)
        self.assertNotIn("api-key-secret", text)
        self.assertNotIn("header-token-secret", text)

    def test_unreadable_http_error_body_still_raises_provider_error(self):
        def fail_with_unreadable_http_error(request, timeout=None):
            raise HTTPError(request.full_url, 500, "Broken", {}, UnreadableResponse())

        with self.assertRaisesRegex(self.mod.LlmProviderError, "openai_compatible.*HTTP 500"):
            self.mod.complete_openai_compatible(
                "prompt",
                agent_key="gm",
                config={"base_url": "https://llm.example/v1", "api_key": "secret-key", "model": "chat-model"},
                urlopen=fail_with_unreadable_http_error,
            )


if __name__ == "__main__":
    unittest.main()
