"""Local LLM provider settings for AIRP runtime helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


SETTINGS_DIR = Path(__file__).resolve().parent / "styles"
DEFAULT_FRONTEND_SETTINGS_PATH = SETTINGS_DIR / "llm_settings.frontend.json"
DEFAULT_LOCAL_SETTINGS_PATH = SETTINGS_DIR / "llm_settings.local.json"
DEFAULT_SETTINGS_PATH = DEFAULT_FRONTEND_SETTINGS_PATH
DEFAULT_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
TRUE_BOOL_STRINGS = {"1", "true", "yes", "y", "on"}
FALSE_BOOL_STRINGS = {"0", "false", "no", "n", "off"}


def _read_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_BOOL_STRINGS:
            return True
        if normalized in FALSE_BOOL_STRINGS:
            return False
    return default


def _claude_env(claude_settings_path: str | Path | None = None) -> dict[str, str]:
    path = Path(claude_settings_path) if claude_settings_path is not None else DEFAULT_CLAUDE_SETTINGS_PATH
    payload = _read_json(path, {})
    if not isinstance(payload, Mapping):
        return {}
    env = payload.get("env")
    if not isinstance(env, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in env.items():
        if isinstance(key, str):
            text = _string(value)
            if text:
                result[key] = text
    return result


def normalize_settings(
    raw: Mapping[str, Any] | None,
    claude_settings_path: str | Path | None = None,
) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    cc_switch = _section(data, "cc_switch")
    openai_compatible = _section(data, "openai_compatible")
    image_generation = _section(data, "image_generation")

    return {
        "cc_switch": {
            "enabled": _bool_value(cc_switch.get("enabled"), False),
            "service_url": _string(cc_switch.get("service_url")),
        },
        "openai_compatible": {
            "enabled": _bool_value(openai_compatible.get("enabled"), False),
            "base_url": _string(openai_compatible.get("base_url")),
            "api_key": _string(openai_compatible.get("api_key")),
            "model": _string(openai_compatible.get("model")),
        },
        "image_generation": {
            "base_url": _string(image_generation.get("base_url")),
            "api_key": _string(image_generation.get("api_key")),
            "model": _string(image_generation.get("model")),
        },
    }


def read_settings(
    path: str | Path | None = None,
    claude_settings_path: str | Path | None = None,
) -> dict[str, Any]:
    settings_path = Path(path) if path is not None else DEFAULT_FRONTEND_SETTINGS_PATH
    payload = _read_json(settings_path, {})
    return normalize_settings(payload if isinstance(payload, Mapping) else {}, claude_settings_path)


def _raw_settings(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = _read_json(path, {})
    return dict(payload) if isinstance(payload, Mapping) else {}


def _has_explicit_bool(section: Mapping[str, Any], key: str = "enabled") -> bool:
    value = section.get(key)
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip().lower() in TRUE_BOOL_STRINGS | FALSE_BOOL_STRINGS
    return False


def _env_bool(env: Mapping[str, str], key: str) -> bool | None:
    if key not in env:
        return None
    value = env.get(key)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_BOOL_STRINGS:
            return True
        if normalized in FALSE_BOOL_STRINGS:
            return False
    return None


def _explicit_bool(section: Mapping[str, Any], key: str = "enabled") -> bool | None:
    value = section.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_BOOL_STRINGS:
            return True
        if normalized in FALSE_BOOL_STRINGS:
            return False
    return None


def _merge_string(frontend: Any, env: Mapping[str, str], env_key: str, local: Any) -> str:
    return _string(frontend) or _string(env.get(env_key)) or _string(local)


def read_effective_settings(
    path: str | Path | None = None,
    claude_settings_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    local_path: str | Path | None = None,
) -> dict[str, Any]:
    environ = env if env is not None else os.environ
    frontend_path = Path(path) if path is not None else DEFAULT_FRONTEND_SETTINGS_PATH
    fallback_path = Path(local_path) if local_path is not None else DEFAULT_LOCAL_SETTINGS_PATH
    frontend_raw = _raw_settings(frontend_path)
    local_raw = _raw_settings(fallback_path)
    local_normalized = normalize_settings(local_raw, claude_settings_path)
    frontend_cc_switch = dict(_section(frontend_raw, "cc_switch"))
    frontend_openai = dict(_section(frontend_raw, "openai_compatible"))
    frontend_image = dict(_section(frontend_raw, "image_generation"))
    local_cc_switch = dict(_section(local_raw, "cc_switch"))
    local_openai = dict(_section(local_raw, "openai_compatible"))

    merged = {
        "cc_switch": {
            "service_url": _merge_string(
                frontend_cc_switch.get("service_url"),
                environ,
                "AIRP_CC_SWITCH_SERVICE_URL",
                local_normalized["cc_switch"].get("service_url"),
            ),
        },
        "openai_compatible": {
            "base_url": _merge_string(
                frontend_openai.get("base_url"),
                environ,
                "AIRP_OPENAI_COMPATIBLE_BASE_URL",
                local_normalized["openai_compatible"].get("base_url"),
            ),
            "api_key": _merge_string(
                frontend_openai.get("api_key"),
                environ,
                "AIRP_OPENAI_COMPATIBLE_API_KEY",
                local_normalized["openai_compatible"].get("api_key"),
            ),
            "model": _merge_string(
                frontend_openai.get("model"),
                environ,
                "AIRP_OPENAI_COMPATIBLE_MODEL",
                local_normalized["openai_compatible"].get("model"),
            ),
        },
        "image_generation": {
            "base_url": _merge_string(
                frontend_image.get("base_url"),
                environ,
                "AIRP_IMAGE_GENERATION_BASE_URL",
                local_normalized["image_generation"].get("base_url"),
            ),
            "api_key": _merge_string(
                frontend_image.get("api_key"),
                environ,
                "AIRP_IMAGE_GENERATION_API_KEY",
                local_normalized["image_generation"].get("api_key"),
            ),
            "model": _merge_string(
                frontend_image.get("model"),
                environ,
                "AIRP_IMAGE_GENERATION_MODEL",
                local_normalized["image_generation"].get("model"),
            ),
        },
    }

    frontend_value = _explicit_bool(frontend_cc_switch)
    env_value = _env_bool(environ, "AIRP_CC_SWITCH_ENABLED")
    local_value = _explicit_bool(local_cc_switch)
    if frontend_value is not None:
        merged["cc_switch"]["enabled"] = frontend_value
    elif env_value is not None:
        merged["cc_switch"]["enabled"] = env_value
    elif local_value is not None:
        merged["cc_switch"]["enabled"] = local_value

    frontend_value = _explicit_bool(frontend_openai)
    env_value = _env_bool(environ, "AIRP_OPENAI_COMPATIBLE_ENABLED")
    local_value = _explicit_bool(local_openai)
    if frontend_value is not None:
        merged["openai_compatible"]["enabled"] = frontend_value
    elif env_value is not None:
        merged["openai_compatible"]["enabled"] = env_value
    elif local_value is not None:
        merged["openai_compatible"]["enabled"] = local_value

    return normalize_settings(merged, claude_settings_path)


def write_settings(path: str | Path, settings: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return normalized


def redact_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    result = {
        "cc_switch": dict(normalized["cc_switch"]),
        "openai_compatible": dict(normalized["openai_compatible"]),
        "image_generation": dict(normalized["image_generation"]),
    }
    for section_name in ("openai_compatible", "image_generation"):
        section = result[section_name]
        section["api_key_set"] = bool(section.get("api_key"))
        section["api_key"] = ""
    return result


def settings_errors(settings: Mapping[str, Any] | None) -> list[str]:
    normalized = normalize_settings(settings)
    errors: list[str] = []
    cc_switch = normalized["cc_switch"]
    openai_compatible = normalized["openai_compatible"]
    image_generation = normalized["image_generation"]

    cc_usable = bool(cc_switch.get("enabled") and cc_switch.get("service_url"))
    openai_required = ("base_url", "api_key", "model")
    openai_missing = [key for key in openai_required if not openai_compatible.get(key)]
    openai_usable = bool(openai_compatible.get("enabled") and not openai_missing)

    if cc_switch.get("enabled") and not cc_switch.get("service_url"):
        errors.append("cc_switch 缺少 service_url")
    if openai_compatible.get("enabled"):
        for key in openai_missing:
            errors.append(f"OpenAI-compatible 缺少 {key}")
    if not cc_usable and not openai_usable:
        errors.append("未启用可用的文本 LLM provider")

    for key in ("base_url", "model", "api_key"):
        if not image_generation.get(key):
            errors.append(f"图片生成 API 缺少 {key}")

    return errors


def resolve_claude_code_model(claude_settings_path: str | Path | None = None) -> str:
    env = _claude_env(claude_settings_path)
    return env.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or env.get("ANTHROPIC_MODEL") or ""


def claude_code_auth_headers(claude_settings_path: str | Path | None = None) -> dict[str, str]:
    env = _claude_env(claude_settings_path)
    headers: dict[str, str] = {}
    api_key = env.get("ANTHROPIC_API_KEY")
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN")
    if api_key:
        headers["x-api-key"] = api_key
    if auth_token:
        headers["authorization"] = "Bearer " + auth_token
    return headers
