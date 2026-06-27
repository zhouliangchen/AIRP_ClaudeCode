"""Runtime LLM runner selection for AIRP agent callbacks."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import llm_provider
import llm_settings


class LlmRunnerError(RuntimeError):
    """Raised when no configured LLM runner can return agent text."""


_last_result: dict[str, Any] | None = None


def get_last_result() -> dict[str, Any] | None:
    """Return a defensive copy of the most recent provider result."""
    if _last_result is None:
        return None
    return copy.deepcopy(_last_result)


def _enabled(section: Mapping[str, Any] | None) -> bool:
    return bool(section and section.get("enabled"))


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _require_model(provider: str, config: Mapping[str, Any]) -> None:
    if not _string(config.get("model")):
        raise LlmRunnerError(f"{provider}: missing required model")


def _secret_values(*configs: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for config in configs:
        api_key = _string(config.get("api_key"))
        if api_key:
            values.append(api_key)
        headers = config.get("headers")
        if isinstance(headers, Mapping):
            for value in headers.values():
                text = _string(value)
                if not text:
                    continue
                values.append(text)
                bearer_match = re.match(r"(?i)^Bearer\s+(.+)$", text)
                if bearer_match:
                    values.append(bearer_match.group(1).strip())
    return values


def _redact_error_text(value: Any, *configs: Mapping[str, Any]) -> str:
    text = str(value)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", text)
    for secret in _secret_values(*configs):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _complete(provider: str, prompt: str, *, agent_key: str, config: Mapping[str, Any]) -> str:
    global _last_result
    result = llm_provider.complete(provider, prompt, agent_key=agent_key, config=config)
    if not isinstance(result, dict):
        raise LlmRunnerError(f"{provider}: provider result must be a dict")
    text = result.get("text")
    if not isinstance(text, str):
        raise LlmRunnerError(f"{provider}: provider result missing text")
    _last_result = copy.deepcopy(result)
    return text


def _cc_switch_config(settings: Mapping[str, Any]) -> dict[str, Any]:
    config = dict(settings.get("cc_switch") or {})
    config["model"] = llm_settings.resolve_claude_code_model()
    config["headers"] = llm_settings.claude_code_auth_headers()
    return config


def _openai_compatible_config(settings: Mapping[str, Any]) -> dict[str, Any]:
    return dict(settings.get("openai_compatible") or {})


def run_llm_agent(agent_key: str, prompt: str, cwd: str | Path) -> str:
    """Run one AIRP agent prompt through the configured LLM provider."""
    del cwd
    global _last_result
    _last_result = None

    settings = llm_settings.read_effective_settings()
    cc_enabled = _enabled(settings.get("cc_switch"))
    openai_enabled = _enabled(settings.get("openai_compatible"))
    if not cc_enabled and not openai_enabled:
        raise LlmRunnerError("No enabled LLM provider is available.")

    openai_config = _openai_compatible_config(settings)
    if cc_enabled:
        cc_config = _cc_switch_config(settings)
        try:
            _require_model("cc_switch", cc_config)
            return _complete("cc_switch", prompt, agent_key=agent_key, config=cc_config)
        except Exception as exc:
            if not openai_enabled:
                if isinstance(exc, LlmRunnerError):
                    raise
                error = _redact_error_text(exc, cc_config)
                raise LlmRunnerError(f"cc_switch failed and no fallback provider is enabled: {error}") from None
            cc_error = exc
    else:
        cc_error = None

    if openai_enabled:
        try:
            _require_model("openai_compatible", openai_config)
            return _complete("openai_compatible", prompt, agent_key=agent_key, config=openai_config)
        except Exception as exc:
            if cc_error is not None:
                cc_message = _redact_error_text(cc_error, cc_config, openai_config)
                fallback_message = _redact_error_text(exc, cc_config, openai_config)
                raise LlmRunnerError(
                    "cc_switch failed, then openai_compatible failed: "
                    f"{cc_message}; fallback error: {fallback_message}"
                ) from None
            if isinstance(exc, LlmRunnerError):
                raise
            error = _redact_error_text(exc, openai_config)
            raise LlmRunnerError(f"openai_compatible failed: {error}") from None

    raise LlmRunnerError("No enabled LLM provider is available.")
