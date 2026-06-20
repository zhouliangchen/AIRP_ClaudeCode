"""GM-only persistence for player-authored hidden or long-term settings."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def hidden_settings_path(card_folder: Any) -> Path:
    return Path(card_folder) / "memory" / "gm_only_hidden_truths.jsonl"


def _normalize_entry(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    return {
        "id": str(item.get("id") or "").strip(),
        "created_at": str(item.get("created_at") or "").strip(),
        "round_id": str(item.get("round_id") or "").strip(),
        "source_input_id": str(item.get("source_input_id") or "").strip(),
        "source": str(item.get("source") or "").strip(),
        "source_unit_id": str(item.get("source_unit_id") or "").strip(),
        "visibility": str(item.get("visibility") or "gm_only"),
        "status": str(item.get("status") or "active"),
        "text": text,
    }


def load_hidden_settings(card_folder: Any, limit: Optional[int] = 20) -> List[Dict[str, Any]]:
    path = hidden_settings_path(card_folder)
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        normalized = _normalize_entry(item)
        if normalized:
            entries.append(normalized)
    if limit is not None and limit >= 0:
        return entries[-limit:]
    return entries


def _entry_id(text: str, source_input_id: str) -> str:
    digest = hashlib.sha256((source_input_id + "\n" + text).encode("utf-8")).hexdigest()
    return digest[:16]


def persist_hidden_setting_record(
    card_folder: Any,
    record: Any,
    *,
    source_input_id: str = "",
    round_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Persist a validated input-analysis hidden fact record without cue matching."""
    if not isinstance(record, dict):
        return None

    body = str(record.get("text") or "").strip()
    if not body:
        return None

    item_id = _entry_id(body, source_input_id or "")
    existing = load_hidden_settings(card_folder, limit=None)
    for item in existing:
        if item.get("id") == item_id:
            return item

    entry = {
        "id": item_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "round_id": str(round_id or ""),
        "source_input_id": str(source_input_id or ""),
        "source": "input_analysis",
        "source_unit_id": str(record.get("id") or record.get("source_unit_id") or ""),
        "visibility": str(record.get("visibility") or "gm_only"),
        "status": str(record.get("status") or "active"),
        "text": body,
    }
    path = hidden_settings_path(card_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
