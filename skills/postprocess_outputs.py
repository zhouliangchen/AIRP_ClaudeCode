"""Validation helpers for postprocess.output.json artifacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ALLOWED_STATE_PATCH_KEYS = {"quest", "stage", "time", "location", "env", "actions"}
DEFAULT_UI_EXTENSIONS = {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}}


class PostprocessOutputError(ValueError):
    """Raised when postprocess output cannot be normalized."""


def validate_postprocess_output(payload, *, critical_action_evidence=None):
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "postprocess_core_invalid",
            "errors": ["postprocess output must be an object"],
        }

    core = payload.get("core")
    if not isinstance(core, dict):
        core = {}

    errors = []
    summary = _clean_text(core.get("summary"))
    current_goal = _clean_text(core.get("current_goal"))
    options = _normalize_options(core.get("options"))

    if not summary:
        errors.append("core.summary is required")
    if not current_goal:
        errors.append("core.current_goal is required")
    if not options:
        errors.append("core.options must include at least one valid option")

    critical_errors = validate_critical_action_options(options, critical_action_evidence)
    errors.extend(critical_errors)

    if errors:
        return {"ok": False, "reason": "postprocess_core_invalid", "errors": errors}

    output = {
        "schema_version": payload.get("schema_version", 1),
        "core": {
            "summary": summary,
            "options": options,
            "current_goal": current_goal,
            "state_patch": _normalize_state_patch(core.get("state_patch")),
        },
        "ui_extensions": _normalize_ui_extensions(payload.get("ui_extensions")),
        "ui_extension_status": payload.get("ui_extension_status")
        if isinstance(payload.get("ui_extension_status"), dict)
        else {"status": "ok", "issues": []},
        "repair_requests": payload.get("repair_requests")
        if isinstance(payload.get("repair_requests"), list)
        else [],
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    return {"ok": True, "output": output}


def validate_critical_action_options(options, evidence):
    if not evidence:
        return []
    if not isinstance(evidence, list):
        evidence = [evidence]

    errors = []
    for index, item in enumerate(evidence):
        if not any(option_matches_evidence(option, item) for option in options):
            evidence_id = _clean_text(item.get("id")) if isinstance(item, dict) else ""
            detail = evidence_id or f"#{index}"
            errors.append(f"missing fixed option for critical action: {detail}")
    return errors


def option_matches_evidence(option, evidence):
    if not isinstance(option, dict):
        return False
    if not isinstance(evidence, dict):
        return False
    required_label = _clean_text(evidence.get("required_label")).lower()
    option_label = _clean_text(option.get("label")).lower()
    if not required_label or required_label not in option_label:
        return False
    if option.get("source") == "player_agent_critical_action":
        return option.get("requires_confirmation") is True
    return True


def _clean_text(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def _option_item(value):
    if isinstance(value, str):
        label = _clean_text(value)
        if not label:
            return None
        return {
            "label": label,
            "source": "postprocess",
            "requires_confirmation": False,
        }
    if not isinstance(value, dict):
        return None

    label = _clean_text(value.get("label"))
    if not label:
        return None
    source = _clean_text(value.get("source")) or "postprocess"
    return {
        "label": label,
        "source": source,
        "requires_confirmation": value.get("requires_confirmation") is True,
    }


def _normalize_options(value):
    if not isinstance(value, list):
        return []
    options = []
    for item in value:
        option = _option_item(item)
        if option:
            options.append(option)
    return options


def _normalize_state_patch(value):
    if not isinstance(value, dict):
        return {}

    normalized = {}
    for key in ALLOWED_STATE_PATCH_KEYS:
        if key not in value:
            continue
        if key == "actions":
            if isinstance(value[key], list):
                actions = [_clean_text(item) for item in value[key]]
                normalized[key] = [item for item in actions if item]
            continue
        normalized[key] = value[key]
    return normalized


def _normalize_ui_extensions(value):
    normalized = {key: {} for key in DEFAULT_UI_EXTENSIONS}
    if not isinstance(value, dict):
        return normalized
    for key in DEFAULT_UI_EXTENSIONS:
        if isinstance(value.get(key), dict):
            normalized[key] = value[key]
    return normalized


def _clean_string_list(value):
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def _append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_pending_repairs(card_dir):
    """Return pending postprocess UI-extension repairs for the card."""

    queue_path = Path(card_dir) / ".agent_runs" / "postprocess_repair_queue.jsonl"
    if not queue_path.exists():
        return []

    repairs = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("status") == "pending":
            repairs.append(row)
    return repairs


def ui_extensions_need_repair(postprocess):
    """Return whether non-core UI extension data needs a follow-up repair."""

    if not isinstance(postprocess, dict):
        return False
    status = postprocess.get("ui_extension_status")
    if not isinstance(status, dict):
        return False
    return str(status.get("status") or "").strip().lower() in {"failed", "partial", "needs_repair"}


def ui_extension_required_keys(postprocess):
    """Extract affected UI extension keys from ui_extension_status.issues."""

    if not isinstance(postprocess, dict):
        return []
    status = postprocess.get("ui_extension_status")
    if not isinstance(status, dict):
        return []
    issues = status.get("issues")
    if not isinstance(issues, list):
        return []

    keys = []
    for issue in issues:
        if isinstance(issue, dict):
            key = _clean_text(issue.get("key"))
        else:
            key = _clean_text(issue)
        if key:
            keys.append(key)
    return keys


def record_ui_extension_repair(run_dir, card_dir, *, reason, required_keys, source_artifacts):
    root = Path(run_dir)
    card_root = Path(card_dir)
    repair_id = f"postprocess-repair-{uuid.uuid4().hex}"
    record = {
        "schema_version": 1,
        "id": repair_id,
        "round_id": root.name,
        "status": "pending",
        "scope": "ui_extensions",
        "reason": _clean_text(reason),
        "required_keys": _clean_string_list(required_keys),
        "source_artifacts": _clean_string_list(source_artifacts),
        "attempts": 1,
    }

    repair_path = root / "artifacts" / "postprocess_repairs" / f"{repair_id}.json"
    repair_path.parent.mkdir(parents=True, exist_ok=True)
    repair_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_jsonl(card_root / ".agent_runs" / "postprocess_repair_queue.jsonl", record)
    return record
