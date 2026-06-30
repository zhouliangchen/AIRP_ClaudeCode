"""Local debug logging for raw model calls."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


def _settings_enabled(settings: dict[str, Any] | None) -> bool:
    if not isinstance(settings, dict):
        return False
    value = settings.get("modelDebugMode", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "agent"


_API_METADATA_ALLOWED_KEYS = ("provider", "model", "status", "usage", "raw_response", "response_preview")
_REDACTED = "[redacted]"
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]+)", flags=re.IGNORECASE)


def _normalized_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    if normalized in {
        "api_key",
        "x_api_key",
        "authorization",
        "header",
        "headers",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "secret",
        "password",
    }:
        return True
    if any(part in normalized for part in ("api_key", "x_api_key", "authorization", "headers", "secret", "password")):
        return True
    if normalized.endswith("_token") or normalized in {"access_token", "refresh_token", "id_token", "bearer_token"}:
        return True
    return False


def _collect_sensitive_strings(value: Any, found: set[str], *, sensitive_context: bool = False) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _collect_sensitive_strings(item, found, sensitive_context=sensitive_context or _is_sensitive_key(key))
        return
    if isinstance(value, list):
        for item in value:
            _collect_sensitive_strings(item, found, sensitive_context=sensitive_context)
        return
    if isinstance(value, (set, tuple)):
        for item in value:
            _collect_sensitive_strings(item, found, sensitive_context=sensitive_context)
        return
    if isinstance(value, str):
        if sensitive_context and value:
            found.add(value)
        for match in _BEARER_TOKEN_RE.finditer(value):
            found.add(match.group(1))
        return
    if sensitive_context and value is not None:
        found.add(str(value))


def _redact_string(value: str, sensitive_values: set[str]) -> str:
    if value in sensitive_values:
        return _REDACTED
    redacted = _BEARER_TOKEN_RE.sub("Bearer " + _REDACTED, value)
    for secret in sorted((item for item in sensitive_values if item), key=len, reverse=True):
        redacted = redacted.replace(secret, _REDACTED)
    return redacted


def _sort_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return repr(value)


def _redact_api_value(value: Any, sensitive_values: set[str]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key):
                redacted[key_text] = _REDACTED
            else:
                redacted[key_text] = _redact_api_value(item, sensitive_values)
        return redacted
    if isinstance(value, list):
        return [_redact_api_value(item, sensitive_values) for item in value]
    if isinstance(value, (set, tuple)):
        return sorted((_redact_api_value(item, sensitive_values) for item in value), key=_sort_key)
    if isinstance(value, str):
        return _redact_string(value, sensitive_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value), sensitive_values)


def _safe_api_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sensitive_values: set[str] = set()
    _collect_sensitive_strings(value, sensitive_values)
    return {
        key: _redact_api_value(value[key], sensitive_values)
        for key in _API_METADATA_ALLOWED_KEYS
        if key in value
    }


class ModelDebugLogger:
    """Writes raw model input/output under a card-local debug directory."""

    schema_version = 1

    def __init__(self, card_folder: str | Path, round_id: str):
        self.card_folder = Path(card_folder).resolve()
        self.round_id = str(round_id or "unknown-round")
        self.root = self.card_folder / "debug" / "model_calls"
        self.round_dir = self.root / _safe_name(self.round_id)
        self.index_path = self.root / "index.jsonl"
        self._counter = 0

    def write_call(
        self,
        *,
        agent_key: str,
        cwd: str,
        prompt: str,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: int | None = None,
        error: str = "",
        exception_type: str = "",
        api_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        safe_agent = _safe_name(agent_key)
        call_id = f"{self.round_id}-{self._counter:04d}-{safe_agent}"
        path = self.round_dir / f"{self._counter:04d}-{safe_agent}.json"
        self.round_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "schema_version": self.schema_version,
            "call_id": call_id,
            "round_id": self.round_id,
            "agent_key": str(agent_key),
            "cwd": str(cwd),
            "started_at": started_at or "",
            "ended_at": ended_at or "",
            "duration_ms": int(duration_ms or 0),
            "raw_input": {"prompt": str(prompt or "")},
            "raw_output": {
                "stdout": str(stdout or ""),
                "stderr": str(stderr or ""),
                "returncode": returncode,
            },
            "error": str(error or ""),
            "exception_type": str(exception_type or ""),
            "api_metadata": _safe_api_metadata(api_metadata),
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        relative_path = str(path.relative_to(self.card_folder)).replace("\\", "/")
        index_item = {
            "schema_version": self.schema_version,
            "call_id": call_id,
            "round_id": self.round_id,
            "agent_key": str(agent_key),
            "started_at": record["started_at"],
            "ended_at": record["ended_at"],
            "duration_ms": record["duration_ms"],
            "returncode": returncode,
            "relative_path": relative_path,
            "error": record["error"],
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(index_item, ensure_ascii=False) + "\n")
        return index_item


def logger_from_settings(
    card_folder: str | Path,
    round_id: str,
    settings: dict[str, Any] | None,
) -> ModelDebugLogger | None:
    if not _settings_enabled(settings):
        return None
    return ModelDebugLogger(card_folder, round_id)
