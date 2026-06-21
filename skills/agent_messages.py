"""Append-only per-round message bus for RP agents."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


VALID_VISIBILITIES = {"gm_only", "story_facing", "actor_facing", "public"}
MESSAGE_LOG = "messages.jsonl"
LOCK_FILE = ".messages.lock"
# Single-process critical section for deterministic IDs and matching log/inbox appends.
_APPEND_LOCK = threading.RLock()


class AgentMessageError(ValueError):
    """Raised when an agent message payload is malformed."""


class _FileLock:
    def __init__(self, path: Path, timeout: float = 10.0, poll_interval: float = 0.01):
        self.path = path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = f"pid={os.getpid()} timestamp={time.time():.6f}\n".encode("utf-8")
                try:
                    os.write(self.fd, payload)
                except Exception:
                    fd = self.fd
                    self.fd = None
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    raise
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise AgentMessageError(f"timed out waiting for message lock: {self.path}")
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def safe_agent_filename(agent_id: str) -> str:
    """Return a filesystem-safe inbox filename for an agent id."""

    if not isinstance(agent_id, str) or not agent_id:
        raise AgentMessageError("agent_id must be a non-empty string")
    safe = "".join(_safe_filename_char(char) for char in agent_id)
    if _is_simple_agent_id(agent_id):
        return f"{safe}.jsonl"

    if safe.startswith((".", "-")):
        safe = f"agent_{safe}"
    if not safe:
        safe = "agent"
    digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:12]
    safe = f"{safe}_h{digest}"
    return f"{safe or 'agent'}.jsonl"


def _safe_filename_char(char: str) -> str:
    if char.isascii() and (char.isalnum() or char in "._-"):
        return char
    if char == ":":
        return "_"
    return f"_u{ord(char):06x}"


def _is_simple_agent_id(agent_id: str) -> bool:
    if agent_id.startswith((".", "-")) or "_" in agent_id:
        return False
    return all(char.isascii() and (char.isalnum() or char in ".-:") for char in agent_id)


def _is_player_or_character(agent_id: str) -> bool:
    return agent_id == "player" or agent_id.startswith("character:")


def _is_gm_or_subgm(agent_id: str) -> bool:
    return agent_id == "gm" or agent_id.startswith("subGM:")


def acl_reason(sender: str, targets: list[str], message_type: str, visibility: str) -> str | None:
    """Return a rejection reason for a normalized message, or None if allowed."""

    if _is_player_or_character(sender):
        if any(not _is_gm_or_subgm(target) for target in targets):
            return "acl_rejected"

    if any(_is_player_or_character(target) for target in targets):
        if sender != "projection" or message_type != "projected_message":
            return "projection_required"

    if sender.startswith("subGM:"):
        allowed = {"gm", "projection", "story"}
        if any(target not in allowed and not target.startswith("character:") for target in targets):
            return "acl_rejected"

    return None


def _next_message_id(run_dir: Path) -> str:
    count = len(read_messages(run_dir))
    return f"msg_{count + 1:06d}"


def normalize_message(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload and return the canonical message object."""

    if not isinstance(payload, dict):
        raise AgentMessageError("payload must be an object")

    sender = payload.get("from")
    targets = payload.get("to")
    message_type = payload.get("type")
    visibility = payload.get("visibility")
    body = payload.get("payload")

    if not isinstance(sender, str) or not sender:
        raise AgentMessageError("from must be a non-empty string")
    if not isinstance(targets, list) or not targets or not all(isinstance(item, str) and item for item in targets):
        raise AgentMessageError("to must be a non-empty list of strings")
    if not isinstance(message_type, str) or not message_type:
        raise AgentMessageError("type must be a non-empty string")
    if visibility not in VALID_VISIBILITIES:
        raise AgentMessageError("visibility must be one of: actor_facing, gm_only, public, story_facing")
    if not isinstance(body, dict):
        raise AgentMessageError("payload must be an object")

    message = {
        "id": _next_message_id(Path(run_dir)),
        "from": sender,
        "to": list(targets),
        "type": message_type,
        "visibility": visibility,
        "payload": body,
    }
    if "source_call_id" in payload:
        source_call_id = payload["source_call_id"]
        if not isinstance(source_call_id, str) or not source_call_id:
            raise AgentMessageError("source_call_id must be a non-empty string")
        message["source_call_id"] = source_call_id
    return message


def append_message(run_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Append an accepted or rejected message, indexing accepted messages by inbox."""

    root = Path(run_dir)
    with _APPEND_LOCK:
        with _FileLock(root / LOCK_FILE):
            try:
                message = normalize_message(root, payload)
            except AgentMessageError as exc:
                return {"ok": False, "reason": "schema_rejected", "error": str(exc)}

            reason = acl_reason(message["from"], message["to"], message["type"], message["visibility"])
            if reason:
                message["status"] = "rejected"
                message["reject_reason"] = reason
                _append_jsonl(root / MESSAGE_LOG, message)
                return {"ok": False, "reason": reason, "message": message}

            message["status"] = "delivered"
            _append_jsonl(root / MESSAGE_LOG, message)
            for target in message["to"]:
                _append_jsonl(root / "inboxes" / safe_agent_filename(target), message)
            return {"ok": True, "message": message}


def read_messages(run_dir: str | Path) -> list[dict[str, Any]]:
    """Read the append-only message log for a run directory."""

    return _read_jsonl(Path(run_dir) / MESSAGE_LOG)


def read_inbox(run_dir: str | Path, agent_id: str) -> list[dict[str, Any]]:
    """Read a single agent inbox."""

    return _read_jsonl(Path(run_dir) / "inboxes" / safe_agent_filename(agent_id))


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
