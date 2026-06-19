import json
import subprocess
import sys
from pathlib import Path


def find_root(start):
    path = Path(start).resolve()
    for candidate in (path, *path.parents):
        if (candidate / "CLAUDE.md").exists() and (candidate / ".claude" / "skills").exists():
            return candidate
    raise FileNotFoundError(f"Cannot locate repository root from {path}")


def read_chat_count(card_dir):
    chat_path = Path(card_dir) / "chat_log.json"
    if not chat_path.exists():
        return 0
    raw = chat_path.read_text(encoding="utf-8").strip()
    if not raw:
        return 0
    data = json.loads(raw)
    if isinstance(data, list):
        return len(data)
    return 0


def _run_python(root, script_name, *args, run_command=subprocess.call):
    command = [sys.executable, str(Path(root) / "skills" / script_name), *map(str, args)]
    return run_command(command)


def _is_active_card(styles, card):
    card_path = Path(styles) / ".card_path"
    if not card_path.exists():
        return False
    raw = card_path.read_text(encoding="utf-8").strip()
    if not raw:
        return False
    return Path(raw).resolve() == Path(card).resolve()


def _read_card_data(card):
    card_data_path = Path(card) / ".card_data.json"
    if not card_data_path.exists():
        return {}
    try:
        data = json.loads(card_data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _card_has_prefilled_opening(card):
    card_data = _read_card_data(card)
    if card_data.get("mode") == "blank_bootstrap" or card_data.get("source_type") == "blank":
        return False
    nested = card_data.get("data")
    nested_first_mes = nested.get("first_mes") if isinstance(nested, dict) else ""
    return bool((card_data.get("first_mes") or nested_first_mes or "").strip())


def _remove_stale_response(response_path):
    try:
        response_path.unlink()
    except FileNotFoundError:
        pass


def bootstrap(card_dir=None, root_dir=None, run_command=subprocess.call):
    card = Path(card_dir or Path.cwd()).resolve()
    root = Path(root_dir).resolve() if root_dir else find_root(card)
    styles = root / "skills" / "styles"
    response_path = styles / "response.txt"
    pending_path = styles / ".pending"

    return_codes = []
    return_codes.append(_run_python(root, "start_server.py", root, run_command=run_command))

    chat_count = read_chat_count(card)
    active_card = _is_active_card(styles, card)
    has_prefilled_opening = _card_has_prefilled_opening(card)
    if response_path.exists() and chat_count == 0 and active_card and has_prefilled_opening:
        return_codes.append(_run_python(root, "handler.py", card, "--opening", run_command=run_command))
        action = "opening_delivered"
        instruction = "Opening has already been delivered. Do not run handler.py --opening again; wait for player input."
    elif pending_path.exists():
        return_codes.append(_run_python(root, "round_prepare.py", card, root, run_command=run_command))
        if return_codes[-1] == 0:
            return_codes.append(_run_python(root, "rp_generate_cli.py", card, root, run_command=run_command))
        if return_codes[-1] == 0:
            action = "turn_generated"
            instruction = "The pending player input has already been generated and delivery has been attempted by rp_generate_cli.py. Do not generate this turn again."
        else:
            action = "generation_failed"
            instruction = "The pending player input could not be generated automatically. Inspect progress.json and the current .agent_runs directory before retrying."
    else:
        if chat_count > 0:
            return_codes.append(_run_python(root, "handler.py", card, "--rebuild", run_command=run_command))
        else:
            if response_path.exists():
                _remove_stale_response(response_path)
            return_codes.append(_run_python(root, "import_prepare.py", card, root, run_command=run_command))
        action = "waiting_for_player_input"
        instruction = "No pending player input exists. Wait for browser input instead of inventing an action."

    ok = all(code == 0 for code in return_codes)
    return {
        "ok": ok,
        "action": action,
        "instruction": instruction,
        "root": str(root),
        "card": str(card),
        "return_codes": return_codes,
    }


def main(argv=None, run_command=subprocess.call):
    should_print = argv is None
    argv = list(argv if argv is not None else sys.argv[1:])
    card_dir = argv[0] if argv else None
    root_dir = argv[1] if len(argv) > 1 else None
    result = bootstrap(card_dir, root_dir, run_command=run_command)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if should_print:
        print(payload)
    return payload


if __name__ == "__main__":
    main()
