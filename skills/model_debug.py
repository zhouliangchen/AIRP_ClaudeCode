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
