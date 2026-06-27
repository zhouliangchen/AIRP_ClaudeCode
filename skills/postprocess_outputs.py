"""Validation helpers for postprocess.output.json artifacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

ALLOWED_STATE_PATCH_KEYS = {"quest", "stage", "time", "location", "env", "actions"}
DEFAULT_UI_EXTENSIONS = {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}}
DEFAULT_MVU = {"commands": [], "status": "ok", "issues": []}
DEFAULT_POSTPROCESS_CONTRACT = {
    "schema_version": 1,
    "ui_extensions": {"status_panels": {}, "custom_cards": {}, "asset_bindings": {}},
    "metadata": {},
}


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
    if not options and summary and current_goal and not critical_action_evidence:
        options = [
            {
                "label": current_goal,
                "source": "postprocess_fallback",
                "requires_confirmation": False,
            }
        ]

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
        "mvu": _normalize_mvu(payload.get("mvu")),
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
    if option.get("source") != "player_agent_critical_action":
        return False
    if option.get("requires_confirmation") is not True:
        return False
    required_label = _clean_text(evidence.get("required_label")).lower()
    option_label = _clean_text(option.get("label")).lower()
    return bool(required_label and required_label in option_label)


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


def _normalize_mvu(value):
    normalized = dict(DEFAULT_MVU)
    if not isinstance(value, dict):
        return normalized

    commands = []
    if isinstance(value.get("commands"), list):
        for item in value["commands"]:
            text = _clean_text(item)
            if text:
                commands.append(text)
    normalized["commands"] = commands

    status = _clean_text(value.get("status"))
    if status:
        normalized["status"] = status

    issues = value.get("issues")
    if isinstance(issues, list):
        normalized["issues"] = [
            item for item in issues
            if (isinstance(item, dict) and item) or _clean_text(item)
        ]
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


def _record_postprocess_repair(run_dir, card_dir, *, scope, reason, required_keys, source_artifacts):
    root = Path(run_dir)
    card_root = Path(card_dir)
    repair_id = f"postprocess-repair-{uuid.uuid4().hex}"
    record = {
        "schema_version": 1,
        "id": repair_id,
        "round_id": root.name,
        "status": "pending",
        "scope": _clean_text(scope),
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


def record_ui_extension_repair(run_dir, card_dir, *, reason, required_keys, source_artifacts):
    return _record_postprocess_repair(
        run_dir,
        card_dir,
        scope="ui_extensions",
        reason=reason,
        required_keys=required_keys,
        source_artifacts=source_artifacts,
    )


def record_postprocess_contract_repair(run_dir, card_dir, *, reason, required_keys, source_artifacts):
    return _record_postprocess_repair(
        run_dir,
        card_dir,
        scope="postprocess_contract",
        reason=reason,
        required_keys=required_keys,
        source_artifacts=source_artifacts,
    )


def load_postprocess_contract(card_dir):
    path = Path(card_dir) / "postprocess_contract.json"
    contract = json.loads(json.dumps(DEFAULT_POSTPROCESS_CONTRACT))
    if not path.exists():
        return contract
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return contract
    if isinstance(data, dict):
        _deep_merge(contract, data)
    return contract


def apply_ui_schema_contract_update(card_dir, payload):
    """Apply a structured assets-ui UI schema update and optional postprocess contract."""

    card_root = Path(card_dir)
    data = payload if isinstance(payload, dict) else {}
    ui_schema = data.get("ui_schema") if isinstance(data.get("ui_schema"), dict) else None
    postprocess_contract = (
        data.get("postprocess_contract")
        if isinstance(data.get("postprocess_contract"), dict)
        else None
    )
    if ui_schema is None and postprocess_contract is None:
        return {
            "applied": False,
            "ui_schema_status": "not_applicable",
            "postprocess_contract_status": "not_applicable",
            "required_keys": [],
            "missing_keys": [],
            "artifacts": [],
        }

    artifacts = []
    ui_schema_status = "not_applicable"
    if ui_schema is not None:
        manifest_path = card_root / "ui_manifest.json"
        manifest = _read_json_object(manifest_path)
        if not manifest:
            manifest = {"version": 1, "mode": "autonomous", "generated_assets": []}
        existing_schema = manifest.get("ui_schema")
        if not isinstance(existing_schema, dict):
            existing_schema = {}
        _deep_merge(existing_schema, ui_schema)
        manifest["ui_schema"] = existing_schema
        _write_json(manifest_path, manifest)
        artifacts.append("ui_manifest.json")
        ui_schema_status = "applied"

    required_keys = ui_schema_required_postprocess_keys(ui_schema)
    contract_status = "not_required"
    contract = load_postprocess_contract(card_root)
    if postprocess_contract is not None:
        _deep_merge(contract, postprocess_contract)
        metadata = contract.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["source"] = _clean_text(data.get("source")) or "assets-ui"
        _write_json(card_root / "postprocess_contract.json", contract)
        artifacts.append("postprocess_contract.json")
        contract_status = "synced"

    missing_keys = [
        key for key in required_keys
        if not postprocess_contract_covers_key(contract, key)
    ]
    if missing_keys:
        contract_status = "repair_pending"
    elif required_keys and postprocess_contract is None:
        contract_status = "not_required"

    return {
        "applied": True,
        "ui_schema_status": ui_schema_status,
        "postprocess_contract_status": contract_status,
        "required_keys": required_keys,
        "missing_keys": missing_keys,
        "artifacts": artifacts,
    }


def ui_schema_required_postprocess_keys(ui_schema):
    if not isinstance(ui_schema, dict):
        return []
    keys = _clean_string_list(ui_schema.get("postprocess_data_required"))
    data_requirements = ui_schema.get("data_requirements")
    if isinstance(data_requirements, list):
        for item in data_requirements:
            if isinstance(item, dict):
                key = _clean_text(item.get("postprocess_key") or item.get("key"))
            else:
                key = _clean_text(item)
            if key:
                keys.append(key)
    return list(dict.fromkeys(keys))


def postprocess_contract_covers_key(contract, key):
    if not isinstance(contract, dict):
        return False
    text = _clean_text(key)
    if not text:
        return False
    current = contract
    for part in text.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _read_json_object(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _deep_merge(base, incoming):
    if not isinstance(base, dict) or not isinstance(incoming, dict):
        return base
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
