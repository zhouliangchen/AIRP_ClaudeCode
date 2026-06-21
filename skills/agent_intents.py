import json
from datetime import datetime, timezone
from pathlib import Path


VALID_STATES = ("pending", "accepted", "rejected", "completed", "blocked")


class AgentIntentError(ValueError):
    pass


def normalize_intent(run_dir, payload):
    if not isinstance(payload, dict):
        raise AgentIntentError("intent payload must be an object")

    requested_by = payload.get("requested_by")
    intent_type = payload.get("type")
    intent_payload = payload.get("payload")
    source_message_id = payload.get("source_message_id")
    policy = payload.get("policy")

    if not isinstance(requested_by, str) or not requested_by:
        raise AgentIntentError("requested_by is required")
    if not isinstance(intent_type, str) or not intent_type:
        raise AgentIntentError("type is required")
    if not isinstance(intent_payload, dict):
        raise AgentIntentError("payload must be an object")
    if source_message_id is not None and not isinstance(source_message_id, str):
        raise AgentIntentError("source_message_id must be a string")
    if policy is not None and not isinstance(policy, dict):
        raise AgentIntentError("policy must be an object")

    intent_id = payload.get("id") or _next_intent_id(Path(run_dir))
    if not isinstance(intent_id, str) or not intent_id:
        raise AgentIntentError("id must be a string")
    if _find_intent_path(Path(run_dir), intent_id) is not None:
        raise AgentIntentError(f"duplicate intent id: {intent_id}")

    intent = {
        "id": intent_id,
        "requested_by": requested_by,
        "type": intent_type,
        "payload": intent_payload,
        "state": "pending",
        "created_at": _now(),
        "updated_at": _now(),
    }
    if source_message_id is not None:
        intent["source_message_id"] = source_message_id
    if policy is not None:
        intent["policy"] = policy
    return intent


def create_intent(run_dir, payload):
    run_dir = Path(run_dir)
    intent = normalize_intent(run_dir, payload)
    path = _intent_path(run_dir, "pending", intent["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise AgentIntentError(f"duplicate intent id: {intent['id']}")
    _write_json(path, intent)
    return {"ok": True, "intent": intent}


def accept_intent(run_dir, intent_id, outputs=None):
    return _transition_intent(run_dir, intent_id, "accepted", True, outputs=outputs)


def reject_intent(run_dir, intent_id, reason, outputs=None):
    return _transition_intent(run_dir, intent_id, "rejected", False, reason=reason, outputs=outputs)


def complete_intent(run_dir, intent_id, outputs=None):
    return _transition_intent(run_dir, intent_id, "completed", True, outputs=outputs)


def block_intent(run_dir, intent_id, reason, outputs=None):
    return _transition_intent(run_dir, intent_id, "blocked", False, reason=reason, outputs=outputs)


def list_intents(run_dir, state="pending"):
    if state not in VALID_STATES:
        raise AgentIntentError(f"invalid intent state: {state}")
    state_dir = Path(run_dir) / "intents" / state
    if not state_dir.exists():
        return []
    return [_read_json(path) for path in sorted(state_dir.glob("intent_*.json"))]


def _transition_intent(run_dir, intent_id, target_state, ok, reason=None, outputs=None):
    run_dir = Path(run_dir)
    current_path = _find_intent_path(run_dir, intent_id)
    if current_path is None:
        result = _result(intent_id, target_state, "intent_missing", outputs)
        return {"ok": False, "result": result}

    intent = _read_json(current_path)
    intent["state"] = target_state
    intent["updated_at"] = _now()
    intent["result"] = _result(intent_id, target_state, reason, outputs)

    target_path = _intent_path(run_dir, target_state, intent_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path != current_path:
        raise AgentIntentError(f"duplicate intent id: {intent_id}")
    _write_json(target_path, intent)
    if target_path != current_path:
        current_path.unlink()
    return {"ok": ok, "intent": intent, "result": intent["result"]}


def _result(intent_id, status, reason=None, outputs=None):
    return {
        "intent_id": intent_id,
        "status": status,
        "reason": reason,
        "outputs": outputs or {},
        "updated_at": _now(),
    }


def _next_intent_id(run_dir):
    highest = 0
    for state in VALID_STATES:
        state_dir = run_dir / "intents" / state
        if not state_dir.exists():
            continue
        for path in state_dir.glob("intent_*.json"):
            stem = path.stem
            suffix = stem.removeprefix("intent_")
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"intent_{highest + 1:06d}"


def _find_intent_path(run_dir, intent_id):
    matches = []
    for state in VALID_STATES:
        path = _intent_path(run_dir, state, intent_id)
        if path.exists():
            matches.append(path)
    if len(matches) > 1:
        raise AgentIntentError(f"duplicate intent id: {intent_id}")
    return matches[0] if matches else None


def _intent_path(run_dir, state, intent_id):
    if state not in VALID_STATES:
        raise AgentIntentError(f"invalid intent state: {state}")
    return Path(run_dir) / "intents" / state / f"{intent_id}.json"


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
