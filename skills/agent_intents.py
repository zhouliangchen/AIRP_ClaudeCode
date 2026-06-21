import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


VALID_STATES = ("pending", "accepted", "rejected", "completed", "blocked")
INTENT_ID_PATTERN = re.compile(r"^intent_[0-9]{6}$")
LOCK_FILE = ".intents.lock"
_INTENT_LOCK = threading.RLock()


class AgentIntentError(ValueError):
    pass


class _FileLock:
    def __init__(self, path, timeout=10.0, poll_interval=0.01):
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = f"pid={os.getpid()} thread={threading.get_ident()} timestamp={time.time():.6f}\n"
                try:
                    os.write(self.fd, payload.encode("utf-8"))
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
                    raise AgentIntentError(f"timed out waiting for intent lock: {self.path}")
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
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
    if "id" in payload:
        raise AgentIntentError("id is assigned by create_intent")

    intent_id = _next_intent_id(Path(run_dir))
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
    with _locked_intents(run_dir):
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


def attach_source_message(run_dir, intent_id, source_message_id):
    run_dir = Path(run_dir)
    if not _is_valid_intent_id(intent_id):
        return {"ok": False, "reason": "invalid_intent_id"}
    if not isinstance(source_message_id, str) or not source_message_id:
        return {"ok": False, "reason": "invalid_source_message_id"}

    with _locked_intents(run_dir):
        current_path = _find_intent_path(run_dir, intent_id)
        if current_path is None:
            return {"ok": False, "reason": "intent_missing"}
        if current_path.parent.name != "pending":
            return {"ok": False, "reason": "intent_not_pending"}

        intent = _read_json(current_path)
        if intent.get("state") != "pending":
            return {"ok": False, "reason": "intent_not_pending"}
        intent["source_message_id"] = source_message_id
        intent["updated_at"] = _now()
        _write_json(current_path, intent)
        return {"ok": True, "intent": intent}


def list_intents(run_dir, state="pending"):
    if state not in VALID_STATES:
        raise AgentIntentError(f"invalid intent state: {state}")
    state_dir = Path(run_dir) / "intents" / state
    if not state_dir.exists():
        return []
    return [_read_json(path) for path in sorted(state_dir.glob("intent_*.json"))]


def _transition_intent(run_dir, intent_id, target_state, ok, reason=None, outputs=None):
    run_dir = Path(run_dir)
    if not _is_valid_intent_id(intent_id):
        result = _result(intent_id, target_state, "invalid_intent_id", outputs)
        return {"ok": False, "reason": "invalid_intent_id", "result": result}

    with _locked_intents(run_dir):
        current_path = _find_intent_path(run_dir, intent_id)
        if current_path is None:
            result = _result(intent_id, target_state, "intent_missing", outputs)
            return {"ok": False, "reason": "intent_missing", "result": result}

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
    if not _is_valid_intent_id(intent_id):
        raise AgentIntentError(f"invalid intent id: {intent_id}")
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
    if not _is_valid_intent_id(intent_id):
        raise AgentIntentError(f"invalid intent id: {intent_id}")
    return Path(run_dir) / "intents" / state / f"{intent_id}.json"


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{path}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _locked_intents(run_dir):
    lock_path = Path(run_dir) / "intents" / LOCK_FILE
    return _IntentLockContext(lock_path)


class _IntentLockContext:
    def __init__(self, lock_path):
        self.lock_path = lock_path
        self.file_lock = None

    def __enter__(self):
        _INTENT_LOCK.acquire()
        try:
            self.file_lock = _FileLock(self.lock_path)
            self.file_lock.__enter__()
            return self
        except Exception:
            _INTENT_LOCK.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.file_lock is not None:
                self.file_lock.__exit__(exc_type, exc, tb)
        finally:
            _INTENT_LOCK.release()


def _is_valid_intent_id(intent_id):
    return isinstance(intent_id, str) and INTENT_ID_PATTERN.fullmatch(intent_id) is not None
