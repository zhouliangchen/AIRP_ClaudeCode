"""HTTP provider adapters for AIRP LLM calls."""

from __future__ import annotations

import json
import http.client
import urllib.request
from json import JSONDecodeError
from typing import Any, Mapping
from urllib.error import HTTPError, URLError


DEFAULT_TIMEOUT = 60
DEFAULT_CC_SWITCH_MAX_TOKENS = 4096


class LlmProviderError(RuntimeError):
    """Raised when an LLM provider call cannot be completed or parsed."""


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return config if isinstance(config, Mapping) else {}


def _required(provider: str, config: Mapping[str, Any], *keys: str) -> dict[str, str]:
    result: dict[str, str] = {}
    missing = []
    for key in keys:
        value = _string(config.get(key))
        if value:
            result[key] = value
        else:
            missing.append(key)
    if missing:
        raise LlmProviderError(f"{provider}: missing required config: {', '.join(missing)}")
    return result


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _json_request(url: str, headers: Mapping[str, str], body: Mapping[str, Any]) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )


def _safe_values(config: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("api_key",):
        value = _string(config.get(key))
        if value:
            values.append(value)
    headers = config.get("headers")
    if isinstance(headers, Mapping):
        for value in headers.values():
            text = _string(value)
            if text:
                values.append(text)
                if text.lower().startswith("bearer "):
                    values.append(text[7:].strip())
    return values


def _redact(text: str, config: Mapping[str, Any]) -> str:
    result = text
    for secret in _safe_values(config):
        if secret:
            result = result.replace(secret, "[redacted]")
    return result


def _error(provider: str, config: Mapping[str, Any], message: str) -> LlmProviderError:
    return LlmProviderError(_redact(f"{provider}: {message}", config))


def _read_json_response(provider: str, request: urllib.request.Request, config: Mapping[str, Any], urlopen) -> tuple[dict[str, Any], int]:
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = ""
        try:
            if exc.fp is not None:
                detail = exc.fp.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise _error(provider, config, f"HTTP {exc.code} {exc.reason}{suffix}") from exc
    except URLError as exc:
        raise _error(provider, config, f"URL error: {exc.reason}") from exc
    except UnicodeDecodeError as exc:
        raise _error(provider, config, f"invalid UTF-8 response: {exc.reason}") from exc
    except http.client.HTTPException as exc:
        raise _error(provider, config, f"HTTP client error: {exc}") from exc
    except OSError as exc:
        raise _error(provider, config, f"request failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except JSONDecodeError as exc:
        raise _error(provider, config, f"invalid JSON response: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise _error(provider, config, "response JSON must be an object")
    return payload, status


def _openai_text(provider: str, payload: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _error(provider, config, "missing choices[0].message.content")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise _error(provider, config, "missing choices[0].message.content")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise _error(provider, config, "missing choices[0].message.content")
    text = message.get("content")
    if not isinstance(text, str):
        raise _error(provider, config, "missing choices[0].message.content")
    return text


def _anthropic_text(provider: str, payload: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise _error(provider, config, "missing content text blocks")
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    if not parts:
        raise _error(provider, config, "missing content text blocks")
    return "\n".join(parts)


def complete_openai_compatible(
    prompt: str,
    *,
    agent_key: str,
    config: Mapping[str, Any] | None,
    urlopen=urllib.request.urlopen,
) -> dict[str, Any]:
    provider = "openai_compatible"
    settings = _config(config)
    required = _required(provider, settings, "base_url", "api_key", "model")
    body = {
        "model": required["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    request = _json_request(
        _join_url(required["base_url"], "chat/completions"),
        {
            "Authorization": "Bearer " + required["api_key"],
            "Content-Type": "application/json",
        },
        body,
    )
    payload, status = _read_json_response(provider, request, settings, urlopen)
    text = _openai_text(provider, payload, settings)
    model = _string(payload.get("model")) or required["model"]
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "provider": provider,
        "agent_key": agent_key,
        "text": text,
        "raw_response": payload,
        "usage": dict(usage),
        "model": model,
        "status": status,
    }


def complete_cc_switch(
    prompt: str,
    *,
    agent_key: str,
    config: Mapping[str, Any] | None,
    urlopen=urllib.request.urlopen,
) -> dict[str, Any]:
    provider = "cc_switch"
    settings = _config(config)
    required = _required(provider, settings, "service_url", "model")
    max_tokens = settings.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        max_tokens = DEFAULT_CC_SWITCH_MAX_TOKENS
    body = {
        "model": required["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    custom_headers = settings.get("headers")
    if isinstance(custom_headers, Mapping):
        for key, value in custom_headers.items():
            header_name = _string(key)
            header_value = _string(value)
            if header_name and header_value:
                headers[header_name] = header_value
    request = _json_request(_join_url(required["service_url"], "v1/messages"), headers, body)
    payload, status = _read_json_response(provider, request, settings, urlopen)
    text = _anthropic_text(provider, payload, settings)
    model = _string(payload.get("model")) or required["model"]
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "provider": provider,
        "agent_key": agent_key,
        "text": text,
        "raw_response": payload,
        "usage": dict(usage),
        "model": model,
        "status": status,
    }


def complete(
    provider: str,
    prompt: str,
    *,
    agent_key: str,
    config: Mapping[str, Any] | None,
    urlopen=urllib.request.urlopen,
) -> dict[str, Any]:
    if provider == "openai_compatible":
        return complete_openai_compatible(prompt, agent_key=agent_key, config=config, urlopen=urlopen)
    if provider == "cc_switch":
        return complete_cc_switch(prompt, agent_key=agent_key, config=config, urlopen=urlopen)
    raise LlmProviderError(f"unknown provider: {provider}")


def test_connection(
    provider: str,
    config: Mapping[str, Any] | None,
    *,
    urlopen=urllib.request.urlopen,
) -> dict[str, Any]:
    try:
        result = complete(provider, "ping", agent_key="connection_test", config=config, urlopen=urlopen)
    except LlmProviderError as exc:
        return {
            "ok": False,
            "provider": provider,
            "error": str(exc),
        }
    text = result.get("text") if isinstance(result.get("text"), str) else ""
    return {
        "ok": True,
        "provider": result["provider"],
        "status": result["status"],
        "model": result["model"],
        "response_preview": text[:200],
    }
