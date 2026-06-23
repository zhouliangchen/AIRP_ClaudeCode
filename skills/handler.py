"""
RP Response Handler — parses Claude Code output and manages chat_log / content.js / state.js.
Also provides reroll and delete-turn logic for the bridge server.
Usage:
  python handler.py <card_folder>          # process response.txt → append turn
  python handler.py <card_folder> --opening # first turn, no user input
"""
import json
import html
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from mvu_engine import extract_commands, execute_commands, compute_current_variables, audit_variables, validate_command, generate_schema, SchemaNode
from io_utils import read_json as _read_json_file, write_json as _write_json_file
from response_parser import (
    parse_response,
    strip_tags as _strip_tags,
    strip_mvu_commands as _strip_mvu_commands,
    text_to_p as _text_to_p,
    extract_options as _extract_options,
)

try:
    import round_state
except Exception:
    round_state = None

try:
    import postprocess_outputs
except Exception:
    postprocess_outputs = None

STYLES = Path(__file__).parent / "styles"
BRIDGE = "http://localhost:8765"
_PROGRESS_WRITE_LOCK = threading.RLock()


# ═══ File I/O ═══

def read_chat_log(card_folder):
    path = Path(card_folder) / "chat_log.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def write_chat_log(card_folder, log):
    path = Path(card_folder) / "chat_log.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pending_user_turn_path(card_folder):
    return Path(card_folder) / ".pending_user_turn.json"


def _player_input_log_path(card_folder):
    return Path(card_folder) / ".player_inputs.jsonl"


def _player_input_edit_log_path(card_folder):
    return Path(card_folder) / ".player_input_edits.jsonl"


def _player_branch_archive_path(card_folder):
    return Path(card_folder) / ".player_input_branches.jsonl"


def _progress_path():
    return STYLES / "progress.json"


def _read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _write_jsonl(path, items):
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _append_jsonl(path, entry):
    path = Path(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record_player_input(
    card_folder,
    raw_text,
    display_text=None,
    role_text=None,
    user_instruction_text=None,
    input_schema=None,
):
    """Append an immutable player-authored input entry.

    This log is the authority source for player wording. Claude Code may revise
    generated narrative or derived memory, but should not mutate this file.
    """
    has_explicit_channels = (
        input_schema == "dual_channel_v1"
        or role_text is not None
        or user_instruction_text is not None
    )
    entry = {
        "id": uuid.uuid4().hex,
        "created_at": _utc_timestamp(),
        "source": "player",
        "raw_text": raw_text or "",
        "display_text": display_text if display_text is not None else (raw_text or ""),
    }
    if has_explicit_channels:
        entry["input_schema"] = "dual_channel_v1"
        entry["role_text"] = "" if role_text is None else str(role_text)
        entry["user_instruction_text"] = (
            "" if user_instruction_text is None else str(user_instruction_text)
        )
    path = _player_input_log_path(card_folder)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_player_inputs(card_folder):
    return _read_jsonl(_player_input_log_path(card_folder))


def frontend_player_inputs(card_folder):
    """Return only browser-visible player input metadata."""
    referenced_ids = []
    try:
        for turn in read_chat_log(card_folder):
            input_id = turn.get("player_input_id") if isinstance(turn, dict) else ""
            if input_id:
                referenced_ids.append(str(input_id))
        pending = read_pending_user_turn(card_folder)
        if isinstance(pending, dict) and pending.get("id"):
            referenced_ids.append(str(pending.get("id")))
    except Exception:
        referenced_ids = []
    referenced = set(referenced_ids)

    visible = []
    for item in read_player_inputs(card_folder):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        if referenced and item_id not in referenced:
            continue
        display_text = (
            item.get("display_text")
            if "display_text" in item
            else item.get("raw_text")
        ) or ""
        entry = {
            "id": item_id,
            "created_at": item.get("created_at", ""),
            "source": item.get("source", ""),
            "raw_text": display_text,
            "display_text": display_text,
        }
        if item.get("input_schema"):
            entry["input_schema"] = item.get("input_schema")
        visible.append(entry)
    return visible


def _write_player_inputs(card_folder, items):
    _write_jsonl(_player_input_log_path(card_folder), items)


def read_player_input_edits(card_folder, processed=None):
    items = _read_jsonl(_player_input_edit_log_path(card_folder))
    if processed is None:
        return items
    return [item for item in items if bool(item.get("processed", False)) is bool(processed)]


def write_pending_user_turn(
    card_folder,
    display_text,
    raw_text=None,
    input_id=None,
    role_text=None,
    user_instruction_text=None,
    input_schema=None,
):
    has_explicit_channels = (
        input_schema == "dual_channel_v1"
        or role_text is not None
        or user_instruction_text is not None
    )
    entry = {
        "id": input_id or uuid.uuid4().hex,
        "created_at": _utc_timestamp(),
        "raw_text": raw_text if raw_text is not None else display_text,
        "display_text": display_text if display_text is not None else (raw_text or ""),
    }
    if has_explicit_channels:
        entry["input_schema"] = "dual_channel_v1"
        entry["role_text"] = "" if role_text is None else str(role_text)
        entry["user_instruction_text"] = (
            "" if user_instruction_text is None else str(user_instruction_text)
        )
    _write_json_file(_pending_user_turn_path(card_folder), entry)
    return entry


def read_pending_user_turn(card_folder):
    data = _read_json_file(_pending_user_turn_path(card_folder), None)
    return data if isinstance(data, dict) else None


def clear_pending_user_turn(card_folder):
    _pending_user_turn_path(card_folder).unlink(missing_ok=True)


def _find_player_input_turn_index(log, player_inputs, input_id):
    for i, turn in enumerate(log or []):
        if turn.get("player_input_id") == input_id:
            return i

    input_pos = None
    for i, item in enumerate(player_inputs or []):
        if item.get("id") == input_id:
            input_pos = i
            break
    if input_pos is None:
        return None

    user_turns = [i for i, turn in enumerate(log or []) if turn.get("user")]
    if input_pos < len(user_turns):
        return user_turns[input_pos]
    return None


def edit_player_input(card_folder, input_id, new_text, mode="update_only"):
    """Apply a player-authored edit to a historical input.

    The edited text is written exactly as provided by the browser. The edit is
    audited separately so Claude Code can later evaluate and repair derived
    AI state without treating generated text as authoritative.
    """
    if mode not in ("update_only", "branch_submit"):
        raise ValueError("mode must be update_only or branch_submit")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")

    player_inputs = read_player_inputs(card_folder)
    input_entry = None
    for item in player_inputs:
        if item.get("id") == input_id:
            input_entry = item
            break
    if input_entry is None:
        raise ValueError("player input not found")

    now = _utc_timestamp()
    old_raw = input_entry.get("raw_text", "")
    old_display = input_entry.get("display_text", old_raw)
    input_entry["raw_text"] = new_text
    input_entry["display_text"] = new_text
    for stale_key in ("input_schema", "role_text", "user_instruction_text"):
        input_entry.pop(stale_key, None)
    input_entry["updated_at"] = now
    input_entry["edit_count"] = int(input_entry.get("edit_count", 0) or 0) + 1
    _write_player_inputs(card_folder, player_inputs)

    log = read_chat_log(card_folder)
    turn_index = _find_player_input_turn_index(log, player_inputs, input_id)
    branch_from_index = turn_index
    if mode == "branch_submit" and branch_from_index is None:
        branch_from_index = len(log)

    if turn_index is not None and 0 <= turn_index < len(log):
        log[turn_index]["user"] = new_text
        log[turn_index]["player_input_id"] = input_id
        log[turn_index]["player_input_edited_at"] = now

    edit_event = {
        "id": uuid.uuid4().hex,
        "created_at": now,
        "source": "player",
        "input_id": input_id,
        "mode": mode,
        "old_raw_text": old_raw,
        "old_display_text": old_display,
        "new_raw_text": new_text,
        "new_display_text": new_text,
        "branch_from_index": branch_from_index,
        "processed": False,
        "status": "pending_impact_review",
    }
    _append_jsonl(_player_input_edit_log_path(card_folder), edit_event)

    if mode == "branch_submit":
        archived_turns = log[branch_from_index:] if branch_from_index < len(log) else []
        if archived_turns:
            _append_jsonl(_player_branch_archive_path(card_folder), {
                "id": uuid.uuid4().hex,
                "created_at": now,
                "input_id": input_id,
                "edit_id": edit_event["id"],
                "branch_from_index": branch_from_index,
                "archived_turns": archived_turns,
            })
        write_chat_log(card_folder, log[:branch_from_index])
        write_pending_user_turn(card_folder, new_text, raw_text=new_text, input_id=input_id)
        (STYLES / "input.txt").write_text(new_text, encoding="utf-8")
        (STYLES / ".pending").touch()
        write_progress("received", "已接收历史输入分支", percent=10)
    else:
        write_chat_log(card_folder, log)

    write_content_js(card_folder)
    return edit_event


def write_progress(stage, label, percent=None, detail=None):
    if round_state is not None:
        try:
            data = round_state.legacy_progress_record(stage, label, percent=percent, detail=detail)
        except Exception:
            pass
        else:
            _write_progress_file(data)
            return data
    data = {
        "stage": stage,
        "label": label,
        "percent": percent,
        "detail": detail or "",
        "updated_at": _utc_timestamp(),
    }
    if isinstance(percent, (int, float)):
        data["percent"] = max(0, min(100, int(percent)))
    _write_progress_file(data)
    return data


def _write_progress_file(data):
    path = _progress_path()
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with _PROGRESS_WRITE_LOCK:
        try:
            _write_json_file(tmp_path, data)
            tmp_path.replace(path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def read_progress():
    data = _read_json_file(_progress_path(), None)
    if isinstance(data, dict):
        return data
    return {"stage": "idle", "state": "idle", "label": "", "percent": None, "detail": ""}


def read_state():
    path = STYLES / "state.js"
    if not path.exists():
        return (
            'window.STATE = {\n'
            '  world: "", stage: "开局", time: "", location: "", env: "",\n'
            '  quest: "", generatedCount: 0, totalTokens: 0, actions: [],\n'
            '  player: "", hp: 0, hpMax: 0, mp: 0, mpMax: 0, exp: 0, expMax: 0, ed: false,\n'
            '  npcs: []\n'
            '};\n'
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_state(js, card_folder=None):
    path = STYLES / "state.js"
    with open(path, "w", encoding="utf-8") as f:
        f.write(js)
    if card_folder:
        card_js_path = Path(card_folder) / "state.js"
        with open(card_js_path, "w", encoding="utf-8") as f:
            f.write(js)


def _strip_html(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _load_postprocess_output(card_folder):
    if postprocess_outputs is None:
        return None
    path = Path(card_folder) / "postprocess.output.json"
    if not path.exists():
        return None
    try:
        data = _read_json_file(path, None)
        result = postprocess_outputs.validate_postprocess_output(data)
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    output = result.get("output")
    return output if isinstance(output, dict) else None


def _postprocess_option_labels(postprocess):
    if not isinstance(postprocess, dict):
        return []
    core = postprocess.get("core")
    if not isinstance(core, dict):
        return []
    labels = []
    options = core.get("options")
    if not isinstance(options, list):
        return labels
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "").strip()
        if label:
            labels.append(label)
    return labels


def _replace_state_field(raw, key, value):
    key_pattern = re.escape(str(key))
    if isinstance(value, str):
        return re.sub(
            rf'(\s+{key_pattern}:\s*)"[^"]*"',
            lambda match: match.group(1) + json.dumps(value, ensure_ascii=False),
            raw,
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return re.sub(rf'(\s+{key_pattern}:\s*)\d+', rf'\g<1>{value}', raw)
    if isinstance(value, list):
        return re.sub(
            rf'(\s+{key_pattern}:\s*)\[.*?\]',
            lambda match: match.group(1) + json.dumps(value, ensure_ascii=False),
            raw,
            flags=re.DOTALL,
        )
    return raw


def _apply_postprocess_state(raw, postprocess):
    if not isinstance(postprocess, dict):
        return raw
    core = postprocess.get("core")
    if not isinstance(core, dict):
        return raw
    patch = core.get("state_patch")
    if not isinstance(patch, dict):
        patch = {}
    applied = dict(patch)
    current_goal = str(core.get("current_goal") or "").strip()
    if current_goal and not str(applied.get("quest") or "").strip():
        applied["quest"] = current_goal
    for key, value in applied.items():
        raw = _replace_state_field(raw, key, value)
    return raw


def _normalize_character_dialogues(dialogues):
    if isinstance(dialogues, str):
        try:
            dialogues = json.loads(dialogues)
        except json.JSONDecodeError:
            return []
    if not isinstance(dialogues, list):
        return []

    normalized = []
    for item in dialogues:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "") or "").strip()
        agent = str(item.get("agent", "") or "").strip().lower()
        agent_id = str(item.get("agent_id", "") or "").strip().lower()
        if source != "subagent":
            if agent == "character" or agent_id.startswith("character:"):
                source = "subagent"
            else:
                continue
        if source != "subagent":
            continue
        name = str(item.get("name", "") or "").strip()
        line = str(item.get("line", "") or "").strip()
        aside = str(item.get("aside", "") or "").strip()
        if not name or not line:
            continue
        entry = {
            "name": name[:80],
            "source": "subagent",
            "line": line[:1000],
        }
        if aside:
            entry["aside"] = aside[:500]
        normalized.append(entry)
        if len(normalized) >= 6:
            break
    return normalized


def _render_character_dialogues(dialogues):
    dialogues = _normalize_character_dialogues(dialogues)
    if not dialogues:
        return ""
    parts = ['<div class="character-dialogues" aria-label="重要角色对话">']
    for item in dialogues:
        name = html.escape(item.get("name", ""))
        line = html.escape(item.get("line", "")).replace("\n", "<br>")
        aside = html.escape(item.get("aside", "")).replace("\n", "<br>")
        parts.append('<div class="character-dialogue-card">')
        parts.append('<div class="character-dialogue-name">' + name + '</div>')
        parts.append('<div class="character-dialogue-line">' + line + '</div>')
        if aside:
            parts.append('<div class="character-dialogue-aside">' + aside + '</div>')
        parts.append('</div>')
    parts.append('</div>')
    return "".join(parts)


def _insert_character_dialogues(ai_html, dialogues):
    dialogue_html = _render_character_dialogues(dialogues)
    if not dialogue_html:
        return ai_html
    html_text = str(ai_html or "")
    paragraph_ends = [m.end() for m in re.finditer(r"</p\s*>", html_text, flags=re.IGNORECASE)]
    if paragraph_ends:
        insert_at = paragraph_ends[0]
        return html_text[:insert_at] + dialogue_html + html_text[insert_at:]
    return html_text + dialogue_html


def _shorten(text, limit=600):
    text = _strip_html(text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _card_asset_url(path):
    if not path:
        return ""
    path = str(path).replace("\\", "/")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) or path.startswith("data:"):
        return path
    return "/api/card_asset/" + path.lstrip("/")


def _load_card_assets(card_folder):
    assets = _read_json_file(Path(card_folder) / ".card_assets.json", {"images": []}) or {"images": []}
    if not isinstance(assets, dict):
        assets = {"images": []}
    images = []
    for item in assets.get("images", []) or []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["url"] = _card_asset_url(copied.get("path", ""))
        images.append(copied)
    assets["images"] = images
    return assets


def _load_ui_manifest(card_folder):
    manifest = _read_json_file(Path(card_folder) / "ui_manifest.json", {}) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    return manifest


def _get_latest_variables(log):
    """Extract current stat_data from the most recent turn that has variables."""
    for turn in reversed(log):
        variables = turn.get("variables")
        if variables and "stat_data" in variables:
            return variables["stat_data"]
    return {}


def _get_latest_delta(log):
    """Extract delta from the most recent turn."""
    if log:
        variables = log[-1].get("variables")
        if variables and "delta" in variables:
            return variables["delta"]
    return {}


def _get_turn_variables(log):
    """Return per-turn variable snapshots for inline card rendering.
    Returns [{index, stat_data, delta}, ...] for every turn.
    """
    result = []
    for turn in log:
        entry = {"index": turn.get("index", 0)}
        variables = turn.get("variables")
        if variables:
            entry["stat_data"] = variables.get("stat_data", {})
            entry["delta"] = variables.get("delta", {})
        else:
            entry["stat_data"] = {}
            entry["delta"] = {}
        result.append(entry)
    return result


def resolve_macros(text, stat_data):
    """Replace {{getvar::path}} and {{formatvar::path}} macros with variable values.

    {{getvar::玩家.姓名}}   → renders the scalar value directly
    {{formatvar::互动对象}}  → renders nested dict as indented YAML/JSON block
    """
    import re as _re

    def _resolve(path_str):
        keys = path_str.split(".")
        current = stat_data
        for k in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(k)
        return current

    def _format_val(v):
        if v is None:
            return "(未定义)"
        if isinstance(v, (int, float, bool, str)):
            return str(v)
        if isinstance(v, (dict, list)):
            try:
                import yaml
                return yaml.dump(v, allow_unicode=True, default_flow_style=False).strip()
            except ImportError:
                return json.dumps(v, ensure_ascii=False, indent=2)
        return str(v)

    # {{getvar::path}}
    text = _re.sub(
        r"\{\{getvar::([^}]+)\}\}",
        lambda m: _format_val(_resolve(m.group(1).strip())),
        text,
    )

    # {{formatvar::path}}
    text = _re.sub(
        r"\{\{formatvar::([^}]+)\}\}",
        lambda m: _format_val(_resolve(m.group(1).strip())),
        text,
    )

    # {{format_message_variable::stat_data.XXX}} — SillyTavern macro for beautify panel
    text = _re.sub(
        r"\{\{format_message_variable::stat_data\.([^}]+)\}\}",
        lambda m: _format_val(_resolve(m.group(1).strip())),
        text,
    )

    # {{format_message_variable::XXX}} without stat_data prefix (resolve from root)
    text = _re.sub(
        r"\{\{format_message_variable::([^}]+)\}\}",
        lambda m: _format_val(_resolve(m.group(1).strip())),
        text,
    )

    return text


def _stat_color(name):
    """Map stat names to bar colors."""
    n = name.lower()
    if '悔恨' in n: return '#b0624a'
    if '情欲' in n or '情慾' in n: return '#d4948a'
    if '屈从' in n or '屈從' in n: return '#c49a56'
    if '献身' in n or '獻身' in n: return '#9a7aaa'
    if 'hp' in n or '血' in n: return '#b0624a'
    if 'mp' in n or '魔' in n or '蓝' in n: return '#5a8a9a'
    if 'exp' in n or '经验' in n: return '#cc9a56'
    return '#5a7a5a'


def _stat_max_guess(val):
    """Guess a sensible max for a stat value to normalize bar width."""
    if val <= 10: return 10
    if val <= 50: return 50
    if val <= 100: return 100
    mag = 10 ** (len(str(int(val))) - 1)
    import math
    return int(math.ceil(val / mag) * mag)


def _render_stat_bar(label, val, max_val=None):
    """Render a single stat bar as inline HTML."""
    if max_val is None:
        max_val = _stat_max_guess(val)
    pct = min(100, round(val / max_val * 100))
    color = _stat_color(label)
    return (
        '<div class="tv-stat-row">'
        '<span class="tv-stat-label">' + label + '</span>'
        '<div class="tv-stat-bar-bg"><div class="tv-stat-bar-fill" style="width:'
        + str(pct) + '%;background:' + color + '"></div></div>'
        '<span class="tv-stat-value">' + str(val) + '</span>'
        '</div>'
    )


def _html_escape(text):
    """Minimal HTML escaping."""
    if not isinstance(text, str):
        text = str(text)
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _build_beautify_panel(stat_data, delta, beautify_data):
    """Build the full inline beautify panel HTML from latest variables.

    Returns a complete HTML string to be appended after all turn-wrap divs.
    Supports phone_data from tavern_helper for rich theme rendering
    (avatars, backgrounds, fonts, user profile).
    """
    if not stat_data:
        return ''

    bd = beautify_data or {}
    phone = bd.get('phone_data', {})
    panel_title = bd.get('panel_title', '') or phone.get('user', {}).get('name', '') or ''
    user_name = bd.get('user_name', '') or phone.get('user', {}).get('name', '')
    user_avatar = bd.get('user_avatar', '') or phone.get('user', {}).get('avatar', '')
    panel_bg = bd.get('panel_bg', '') or phone.get('user', {}).get('phoneBg', '')
    panel_font = bd.get('panel_font', '') or phone.get('user', {}).get('font', '')
    fonts = bd.get('fonts', []) or phone.get('fonts', [])
    random_avatars = bd.get('randomAvatars', []) or phone.get('randomAvatars', [])

    # Separate world metadata from characters
    world_data = stat_data.get('世界', {})
    # Character keys — main cast (with sub-objects) come first, NPCs last
    char_keys = []
    npc_keys = []
    for k in stat_data:
        if k == '世界':
            continue
        v = stat_data[k]
        if isinstance(v, dict):
            has_subs = any(isinstance(sv, dict) for sv in v.values())
            if has_subs:
                char_keys.append(k)
            else:
                npc_keys.append(k)
    ordered_keys = char_keys + npc_keys

    # ---- Font CSS (load from phone_data fonts list) ----
    font_css = ''
    if fonts:
        for f in fonts:
            fname = f.get('name', '')
            furl = f.get('url', '')
            if furl:
                font_css += '@import url(' + _html_escape(furl) + ');\n'

    # ---- Panel background style ----
    bg_style = ''
    if panel_bg:
        bg_style = 'background-image:url(' + _html_escape(panel_bg) + ');background-size:cover;background-position:center;'

    # ---- Tabs ----
    tabs_html = ''
    all_tabs = []
    if world_data:
        all_tabs.append(('世界', '世界'))

    for i, ck in enumerate(ordered_keys):
        # Assign avatar round-robin from randomAvatars if available
        all_tabs.append((ck, ck))

    for i, (tab_id, tab_label) in enumerate(all_tabs):
        active = ' active' if i == 0 else ''
        # Avatar icon for character tabs
        avatar_html = ''
        if tab_id != '世界' and random_avatars:
            av_idx = (i - (1 if world_data else 0)) % len(random_avatars)
            avatar_html = '<span class="beautify-tab-avatar" style="background-image:url(' + _html_escape(random_avatars[av_idx]) + ')"></span>'
        tabs_html += '<button class="beautify-tab-btn' + active + '" data-tab="' + _html_escape(tab_id) + '">' + avatar_html + '<span>' + _html_escape(tab_label) + '</span></button>'

    # ---- Tab body ----
    body_html = ''

    # World tab
    if world_data:
        body_html += '<div class="beautify-tab-panel" data-tab="世界">'
        body_html += '<div class="beautify-info-grid">'
        for key in world_data:
            val = world_data[key]
            body_html += '<div class="beautify-info-card"><div class="beautify-info-label">' + _html_escape(key) + '</div><div class="beautify-info-value">' + _html_escape(str(val)) + '</div></div>'
        body_html += '</div></div>'

    # Character tabs
    for ci, ck in enumerate(ordered_keys):
        cd = stat_data[ck]
        is_npc = ck in npc_keys
        body_html += '<div class="beautify-tab-panel" data-tab="' + _html_escape(ck) + '">'

        # ---- Character card header with avatar ----
        av_idx = ci % len(random_avatars) if random_avatars else -1
        char_avatar = random_avatars[av_idx] if av_idx >= 0 else ''

        body_html += '<div class="beautify-char-card">'

        # Avatar
        if char_avatar:
            body_html += '<div class="beautify-char-avatar-wrap"><div class="beautify-char-avatar" style="background-image:url(' + _html_escape(char_avatar) + ')" data-zoom="' + _html_escape(char_avatar) + '" onclick="zoomPortrait(this.dataset.zoom)" title="点击放大"></div></div>'

        # Info column
        body_html += '<div class="beautify-char-info">'
        body_html += '<div class="beautify-char-name">' + _html_escape(ck) + '</div>'

        # Current condition
        if cd.get('当前状况'):
            body_html += '<div class="beautify-char-condition">' + _html_escape(str(cd['当前状况'])) + '</div>'

        # Stat bars
        stat_items = [(k, v) for k, v in cd.items() if isinstance(v, (int, float))]
        if stat_items:
            body_html += '<div class="beautify-stat-bars">'
            for skey, sval in stat_items:
                body_html += _render_stat_bar(skey, sval)
            body_html += '</div>'

        # Pregnancy / stage badges
        badges_html = ''
        if cd.get('是否受孕'):
            badges_html += '<span class="beautify-badge badge-pregnant">孕</span>'
        if cd.get('当前阶段'):
            badges_html += '<span class="beautify-badge badge-stage">阶段 ' + _html_escape(str(cd['当前阶段'])) + '</span>'
        if badges_html:
            body_html += '<div class="beautify-badges">' + badges_html + '</div>'

        body_html += '</div>'  # end char-info
        body_html += '</div>'  # end char-card

        # ---- Sub-objects: 着装 + 身体状况 side by side ----
        outfit = cd.get('着装', {})
        body_stats = cd.get('身体状况', {})
        if outfit or body_stats:
            body_html += '<div class="beautify-sub-grid">'
            if outfit:
                body_html += '<div class="beautify-sub-card"><div class="beautify-sub-title">着装</div>'
                for sk, sv in outfit.items():
                    body_html += '<div class="beautify-sub-row"><span class="beautify-sub-key">' + _html_escape(sk) + '</span><span class="beautify-sub-val">' + _html_escape(str(sv)) + '</span></div>'
                body_html += '</div>'
            if body_stats:
                body_html += '<div class="beautify-sub-card"><div class="beautify-sub-title">身体</div>'
                for sk, sv in body_stats.items():
                    body_html += '<div class="beautify-sub-row"><span class="beautify-sub-key">' + _html_escape(sk) + '</span><span class="beautify-sub-val">' + _html_escape(str(sv)) + '</span></div>'
                body_html += '</div>'
            body_html += '</div>'

        # Other dict sub-objects (not 着装/身体状况)
        for key, val in cd.items():
            if isinstance(val, dict) and key not in ('着装', '身体状况'):
                body_html += '<details class="beautify-sub"><summary>' + _html_escape(key) + '</summary>'
                for sk, sv in val.items():
                    body_html += '<div class="beautify-sub-row"><span class="beautify-sub-key">' + _html_escape(sk) + '</span><span class="beautify-sub-val">' + _html_escape(str(sv)) + '</span></div>'
                body_html += '</details>'

        # Delta changes
        char_delta = {}
        for dk, dv in (delta or {}).items():
            if dk.startswith(ck + '.'):
                short_key = dk[len(ck) + 1:]
                char_delta[short_key] = dv

        if char_delta:
            body_html += '<div class="beautify-delta">'
            for dk, dv in char_delta.items():
                old_v = dv.get('old', '?') if isinstance(dv, dict) else '?'
                new_v = dv.get('new', '?') if isinstance(dv, dict) else str(dv)
                body_html += '<div class="beautify-delta-item"><span class="beautify-delta-key">' + _html_escape(dk) + '</span> <span class="beautify-delta-old">' + _html_escape(str(old_v)) + '</span> → <span class="beautify-delta-new">' + _html_escape(str(new_v)) + '</span></div>'
            body_html += '</div>'

        body_html += '</div>'  # end tab-panel

    # ---- Assemble full panel ----
    panel_html = ''

    # Font loading
    if font_css:
        panel_html += '<style>' + font_css + '</style>'

    panel_html += '<div class="beautify-panel-inline" style="' + bg_style + '">'

    # Overlay for readability when bg is set
    if panel_bg:
        panel_html += '<div class="beautify-panel-overlay">'

    panel_html += '<div class="beautify-dashboard">'

    # Header with user avatar
    panel_html += '<div class="beautify-header">'
    if user_avatar:
        panel_html += '<div class="beautify-user-avatar" style="background-image:url(' + _html_escape(user_avatar) + ')"></div>'
    panel_html += '<div class="beautify-header-text">'
    panel_html += '<span class="beautify-header-title">' + _html_escape(panel_title or '状态面板') + '</span>'
    if user_name:
        panel_html += '<span class="beautify-header-sub">' + _html_escape(user_name) + '</span>'
    panel_html += '</div></div>'

    # Tabs
    panel_html += '<div class="beautify-tabs">' + tabs_html + '</div>'

    # Tab body
    panel_html += '<div class="beautify-tab-content">' + body_html + '</div>'

    panel_html += '</div>'  # end dashboard

    if panel_bg:
        panel_html += '</div>'  # end overlay

    panel_html += '</div>'  # end panel-inline

    # Font family
    if panel_font:
        panel_html += '<style>.beautify-panel-inline .beautify-dashboard{font-family:"' + _html_escape(panel_font) + '",sans-serif;}</style>'

    # Tab switching script
    panel_html += '''<script>
(function(){
  var panel = document.querySelector('.beautify-panel-inline');
  if (!panel || panel.getAttribute('data-tab-wired')) return;
  panel.setAttribute('data-tab-wired', '1');
  var tabs = panel.querySelectorAll('.beautify-tab-btn');
  var panels = panel.querySelectorAll('.beautify-tab-panel');
  for (var i = 0; i < panels.length; i++) {
    panels[i].style.display = (i === 0) ? '' : 'none';
  }
  for (var j = 0; j < tabs.length; j++) {
    tabs[j].addEventListener('click', function(e) {
      var tabId = this.getAttribute('data-tab');
      for (var k = 0; k < tabs.length; k++) {
        tabs[k].classList.remove('active');
      }
      this.classList.add('active');
      for (var m = 0; m < panels.length; m++) {
        panels[m].style.display = (panels[m].getAttribute('data-tab') === tabId) ? '' : 'none';
      }
    });
  }
})();
</script>'''

    return panel_html


def write_content_js(card_folder):
    """Rebuild content.js from chat_log.json. Exposes TURN_TOKENS for per-turn token display."""
    log = read_chat_log(card_folder)
    pending_turn = read_pending_user_turn(card_folder)
    player_inputs = read_player_inputs(card_folder)
    frontend_inputs = frontend_player_inputs(card_folder)
    postprocess = _load_postprocess_output(card_folder)

    html_parts = []
    turn_tokens = {}  # { "N": {"in": X, "out": Y, "total": Z}, ... }
    user_turn_seq = 0

    for turn in log:
        ai_raw = turn.get("ai", "")
        user_raw = turn.get("user", "")
        turn_idx = turn.get("index", 0)

        # Strip <options>/<summary>/<tokens> from display
        ai_display = _strip_tags(ai_raw, "options")
        ai_display = _strip_tags(ai_display, "summary")
        ai_display = _strip_tags(ai_display, "tokens")
        ai_display = _strip_tags(ai_display, "character_dialogues")
        ai_display = _strip_mvu_commands(ai_display)
        # Strip hardcoded text colors from inline styles
        ai_display = re.sub(
            r'\bcolor\s*:\s*#[0-9a-fA-F]{3,8}\s*;?\s*',
            '', ai_display,
        )

        # Collect token data for exposure
        tokens = turn.get("tokens")
        if tokens:
            turn_tokens[str(turn_idx)] = tokens

        wrap = '<div class="turn-wrap">'
        if user_raw:
            input_id = turn.get("player_input_id")
            if not input_id and user_turn_seq < len(player_inputs):
                input_id = player_inputs[user_turn_seq].get("id")
            attrs = ' data-player-input-id="' + _escape_attr(input_id) + '"' if input_id else ""
            user_display = html.escape(user_raw).replace("\n", "<br>")
            wrap += '<div class="turn-user"' + attrs + '><div class="turn-role">你</div><div class="turn-text">' + user_display + '</div></div>'
            user_turn_seq += 1
        ai_display = _insert_character_dialogues(ai_display, turn.get("character_dialogues", []))
        wrap += '<div class="turn-ai"><div class="turn-role">叙事</div><div class="turn-text">' + ai_display + '</div></div>'
        wrap += '</div>'
        html_parts.append(wrap)

    if pending_turn:
        pending_text = (
            pending_turn.get("display_text")
            if "display_text" in pending_turn
            else pending_turn.get("raw_text")
        ) or ""
        pending_html = html.escape(pending_text).replace("\n", "<br>")
        pending_id = pending_turn.get("id")
        attrs = ' data-player-input-id="' + _escape_attr(pending_id) + '"' if pending_id else ""
        wrap = '<div class="turn-wrap turn-pending">'
        wrap += '<div class="turn-user"' + attrs + '><div class="turn-role">你</div><div class="turn-text">' + pending_html + '</div></div>'
        wrap += '<div class="turn-ai turn-pending-ai"><div class="turn-role">叙事</div><div class="turn-text"><p class="pending-reply">等待 Claude Code 回复...</p></div></div>'
        wrap += '</div>'
        html_parts.append(wrap)

    # Extract startup cost from turn 0 token data (persistent across rounds)
    startup_cost = {}
    if log and log[0].get("tokens"):
        t0 = log[0]["tokens"]
        st_in = t0.get("startup_in", 0) or t0.get("in", 0)
        st_out = t0.get("startup_out", 0) or t0.get("out", 0)
        st_total = t0.get("startup_total", 0) or t0.get("total", 0)
        if st_total > 0:
            startup_cost = {
                "in": st_in,
                "out": st_out,
                "total": st_total,
                "cache_hit": t0.get("cache_hit", 0),
            }

    content_html = "".join(html_parts)

    # Load card-specific beautify data if available
    beautify_data = {}
    beautify_path = Path(card_folder) / ".beautify.json"
    if beautify_path.exists():
        try:
            with open(beautify_path, "r", encoding="utf-8") as f:
                beautify_data = json.load(f)
        except Exception:
            pass

    # Load card author's beautify panel template (from regex_scripts).
    # The template is provided as a separate BEAUTIFY_HTML variable so the
    # beautify panel renders independently of narrative content — opening
    # switches and name changes no longer destroy the panel DOM.
    # _st_shims.js (loaded in index.html) provides ST/MVU API shims so the
    # original author script runs unchanged.
    beautify_html = ""
    template_path = Path(card_folder) / ".beautify_template.html"
    if template_path.exists():
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template_html = f.read()
            # Strip structural document tags
            template_html = re.sub(
                r'<!doctype[^>]*>', '', template_html, flags=re.IGNORECASE,
            )
            template_html = re.sub(
                r'</?html[^>]*>', '', template_html, flags=re.IGNORECASE,
            )
            template_html = re.sub(
                r'</?head[^>]*>', '', template_html, flags=re.IGNORECASE,
            )
            template_html = re.sub(
                r'</?body[^>]*>', '', template_html, flags=re.IGNORECASE,
            )
            # <script type="module"> → <script> so it runs as classic script
            template_html = template_html.replace(
                '<script type="module">', '<script>'
            )
            # Macros ({{format_message_variable}}, {{getvar}}, etc.) are left INTACT
            # in the template — they are resolved client-side at display time
            # against window.MVU_VARIABLES, matching the real MVU pipeline where
            # the engine resolves macros dynamically on each render cycle.
            beautify_html = template_html
        except Exception:
            pass
    else:
        # No author template — use fallback inline beautify panel
        latest_vars = _get_latest_variables(log)
        latest_delta = _get_latest_delta(log)
        panel_html = _build_beautify_panel(latest_vars, latest_delta, beautify_data)
        if panel_html:
            beautify_html = panel_html

    # Strip <StatusPlaceHolderImpl/> markers from narrative content
    content_html = content_html.replace("<StatusPlaceHolderImpl/>", "")

    postprocess_core = postprocess.get("core", {}) if isinstance(postprocess, dict) else {}
    latest_summary = str(postprocess_core.get("summary") or "").strip()
    if not latest_summary:
        latest_summary = log[-1].get("summary", "") if log else ""
    latest_ai = log[-1].get("ai", "") if log else ""

    # Extract options from latest AI content
    options = _postprocess_option_labels(postprocess)
    opts_match = re.search(r"<options>(.*?)</options>", latest_ai, re.DOTALL)
    if not options and opts_match:
        for line in opts_match.group(1).strip().split("\n"):
            line = line.strip()
            if line:
                options.append(line)

    # Load card author's regex_scripts for frontend application
    regex_scripts = []
    regex_path = Path(card_folder) / ".regex_scripts.json"
    if regex_path.exists():
        try:
            with open(regex_path, "r", encoding="utf-8") as f:
                regex_scripts = json.load(f)
        except Exception:
            pass

    # Load per-card UI manifest and generated assets for autonomous UI evolution.
    ui_manifest = _load_ui_manifest(card_folder)
    card_assets = _load_card_assets(card_folder)
    postprocess_ui = (
        postprocess.get("ui_extensions", {})
        if isinstance(postprocess, dict) and isinstance(postprocess.get("ui_extensions"), dict)
        else {}
    )

    js = (
        "window.CONTENT_HTML = " + json.dumps(content_html, ensure_ascii=False) + ";\n"
        "window.BEAUTIFY_HTML = " + json.dumps(beautify_html, ensure_ascii=False) + ";\n"
        "window.SUMMARY_TEXT = " + json.dumps(latest_summary, ensure_ascii=False) + ";\n"
        "window.TURN_OPTIONS = " + json.dumps(options, ensure_ascii=False) + ";\n"
        "window.POSTPROCESS_UI = " + json.dumps(postprocess_ui, ensure_ascii=False) + ";\n"
        "window.TURN_TOKENS = " + json.dumps(turn_tokens, ensure_ascii=False) + ";\n"
        "window.STARTUP_COST = " + json.dumps(startup_cost, ensure_ascii=False) + ";\n"
        "window.PLAYER_INPUTS = " + json.dumps(frontend_inputs, ensure_ascii=False) + ";\n"
        "window.MVU_VARIABLES = " + json.dumps(_get_latest_variables(log), ensure_ascii=False) + ";\n"
        "window.MVU_DELTA = " + json.dumps(_get_latest_delta(log), ensure_ascii=False) + ";\n"
        "window.TURN_VARIABLES = " + json.dumps(_get_turn_variables(log), ensure_ascii=False) + ";\n"
        "window.BEAUTIFY_DATA = " + json.dumps(beautify_data, ensure_ascii=False) + ";\n"
        "window.REGEX_SCRIPTS = " + json.dumps(regex_scripts, ensure_ascii=False) + ";\n"
        "window.UI_MANIFEST = " + json.dumps(ui_manifest, ensure_ascii=False) + ";\n"
        "window.CARD_ASSETS = " + json.dumps(card_assets, ensure_ascii=False) + ";\n"
    )

    path = STYLES / "content.js"
    with open(path, "w", encoding="utf-8") as f:
        f.write(js)

    # Dual write to card folder for per-card frontend
    card_path = Path(card_folder) / "content.js"
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(js)


def _escape_attr(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def rebuild_content(card_folder):
    """Activate a card folder and rebuild current frontend display only."""
    card_path = str(Path(card_folder).resolve())
    (STYLES / ".card_path").write_text(card_path, encoding="utf-8")
    write_content_js(card_path)
    return {"ok": True, "card": card_path, "content_js": str(STYLES / "content.js")}


def update_state(**kwargs):
    """Update fields in state.js. Keys: stage, time, location, env, quest, generatedCount, npcs, etc."""
    raw = read_state()
    for key, value in kwargs.items():
        if isinstance(value, str):
            raw = re.sub(rf'(\s+{key}:\s*")[^"]*(")', rf'\g<1>{value}\g<2>', raw)
        elif isinstance(value, (int, float)):
            raw = re.sub(rf'(\s+{key}:\s*)\d+', rf'\g<1>{value}', raw)
        elif isinstance(value, list):
            raw = re.sub(rf'(\s+{key}:\s*)\[.*?\]', lambda m: m.group(1) + json.dumps(value, ensure_ascii=False), raw, flags=re.DOTALL)
    write_state(raw)


# ═══ Blank-card profile evolution ═══

def _profile_from_card_data(card_data):
    profile = card_data.get("evolving_profile")
    if not isinstance(profile, dict):
        profile = {
            "version": 1,
            "last_turn": 0,
            "confidence": "low",
            "fields": {
                "role": "",
                "appearance": "",
                "voice": "",
                "motivation": "",
                "relationship_to_user": "",
                "world_assumptions": [],
            },
        }
    profile.setdefault("version", 1)
    profile.setdefault("last_turn", 0)
    profile.setdefault("confidence", "low")
    fields = profile.setdefault("fields", {})
    for key, default in {
        "role": "",
        "appearance": "",
        "voice": "",
        "motivation": "",
        "relationship_to_user": "",
        "world_assumptions": [],
    }.items():
        fields.setdefault(key, default)
    return profile


def _find_first_str(obj, names):
    if isinstance(obj, dict):
        for name in names:
            val = obj.get(name)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in obj.values():
            found = _find_first_str(val, names)
            if found:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = _find_first_str(val, names)
            if found:
                return found
    return ""


def _derive_blank_identity_from_user_text(user_text):
    text = str(user_text or "")
    text = re.sub(r"\[USER_INSTRUCTION\].*", "", text, flags=re.DOTALL).strip()
    name = ""
    role = ""

    match = re.search(r"【([^】\n]{1,24})】", text)
    if match:
        name = match.group(1).strip()
    if not name:
        match = re.search(r"(?:我叫|我是)([\u4e00-\u9fffA-Za-z0-9_·]{1,24})", text)
        if match:
            name = match.group(1).strip()

    if name:
        role_match = re.search(rf"(?:我叫|我是){re.escape(name)}[，,]\s*([^。；;\n]+)", text)
        if role_match:
            role = role_match.group(1).strip()
    if not role:
        role_match = re.search(r"(?:我叫|我是)[^，,。；;\n]{1,24}[，,]\s*([^。；;\n]+)", text)
        if role_match:
            role = role_match.group(1).strip()
    role = re.sub(r"^(?:一名|一个|一位|名|个|位)", "", role).strip()
    return name, role


def evolve_blank_profile(card_folder, turn_index, user_text, ai_text, summary, stat_data):
    """Persist incremental custom-card state for blank_bootstrap cards."""
    card_path = Path(card_folder) / ".card_data.json"
    card_data = _read_json_file(card_path, {}) or {}
    if card_data.get("mode") != "blank_bootstrap" and card_data.get("source_type") != "blank":
        return

    profile = _profile_from_card_data(card_data)
    fields = profile["fields"]

    # Use explicit MVU values when available; otherwise keep existing values.
    # Blank-bootstrap evolves the story/card from authoritative player input, so
    # never let an NPC written under the generic /角色 slot rename the whole card
    # when /玩家/姓名 is present. Treat /角色 as the current focus NPC, not self.
    player_name = ""
    name = ""
    if isinstance(stat_data, dict):
        player_obj = stat_data.get("玩家")
        if isinstance(player_obj, dict):
            player_name = str(player_obj.get("姓名") or player_obj.get("名字") or "").strip()
        role_obj = stat_data.get("角色")
        if isinstance(role_obj, dict):
            name = str(role_obj.get("姓名") or role_obj.get("名字") or "").strip()
    if player_name and player_name != "{{user}}":
        name = player_name
    if not name:
        name = _find_first_str(stat_data, ["姓名", "名字", "名称", "name"])
    role = ""
    if isinstance(stat_data, dict):
        player_obj = stat_data.get("玩家")
        if isinstance(player_obj, dict):
            role = _find_first_str(player_obj, ["身份", "职业", "角色定位", "role"])
    if not role:
        role = _find_first_str(stat_data, ["身份", "职业", "角色定位", "role"])
    derived_name, derived_role = _derive_blank_identity_from_user_text(user_text)
    if not name and derived_name:
        name = derived_name
    if not role and derived_role:
        role = derived_role
    situation = _find_first_str(stat_data, ["当前状况", "当前状态", "状态"])
    location = _find_first_str(stat_data, ["地点", "当前位置"])
    scene = _find_first_str(stat_data, ["当前场景", "场景"])

    if name and name != "{{user}}":
        card_data["name"] = name
        card_data.setdefault("data", {})["name"] = name
    if role:
        fields["role"] = role
        card_data.setdefault("data", {})["description"] = role
    if situation and not fields.get("motivation"):
        fields["motivation"] = situation
    if not fields.get("relationship_to_user") and user_text:
        fields["relationship_to_user"] = "关系正在通过互动建立"
    if location or scene:
        assumption = " / ".join([x for x in [location, scene] if x])
        assumptions = fields.setdefault("world_assumptions", [])
        if assumption and assumption not in assumptions:
            assumptions.append(assumption)
            fields["world_assumptions"] = assumptions[-12:]

    profile["last_turn"] = turn_index
    profile["confidence"] = "medium" if turn_index >= 3 else "low"
    profile.setdefault("recent_observations", [])
    observation = {
        "turn": turn_index,
        "user": _shorten(user_text, 240),
        "summary": _shorten(summary or ai_text, 240),
    }
    if observation["summary"] or observation["user"]:
        profile["recent_observations"].append(observation)
        profile["recent_observations"] = profile["recent_observations"][-12:]

    card_data["evolving_profile"] = profile
    card_data.setdefault("data", {})["extensions"] = card_data.get("data", {}).get("extensions", {})
    _write_json_file(card_path, card_data)

    char_dir = Path(card_folder) / "memory" / "characters" / "_self"
    _write_json_file(char_dir / "profile.json", profile)
    md = [
        "# 自定义角色卡",
        "",
        f"- 最后更新轮次: {turn_index}",
        f"- 置信度: {profile.get('confidence', 'low')}",
        f"- 姓名: {card_data.get('name', '')}",
        f"- 身份/定位: {fields.get('role', '')}",
        f"- 外貌: {fields.get('appearance', '')}",
        f"- 声口: {fields.get('voice', '')}",
        f"- 动机/状态: {fields.get('motivation', '')}",
        f"- 与用户关系: {fields.get('relationship_to_user', '')}",
        "",
        "## 世界假设",
    ]
    for item in fields.get("world_assumptions", []) or []:
        md.append(f"- {item}")
    _write_json_file(char_dir / "profile.json", profile)
    (char_dir / "profile.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    recent_lines = ["# 近期角色沉淀", ""]
    for obs in profile.get("recent_observations", [])[-8:]:
        recent_lines.append(f"- 第 {obs.get('turn')} 轮：{obs.get('summary', '')}")
    (char_dir / "recent.md").write_text("\n".join(recent_lines) + "\n", encoding="utf-8")


# ═══ Turn Operations ═══

MVU_SERVER = "http://127.0.0.1:8766"

def _mvu_post(endpoint, data=None):
    """POST to mvu_server, return parsed JSON or None on failure."""
    import urllib.request as _ur
    try:
        body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
        req = _ur.Request(f"{MVU_SERVER}/{endpoint}", data=body,
                          headers={"Content-Type": "application/json"})
        with _ur.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _validate_commands_via_server(commands):
    """Batch validate commands via mvu_server. Returns (valid_cmds, errors)."""
    if not commands:
        return commands, []
    payload = {"commands": []}
    for cmd in commands:
        item = {"op": cmd.type}
        if cmd.args:
            item["path"] = cmd.args[0] if len(cmd.args) > 0 else None
            item["value"] = cmd.args[1] if len(cmd.args) > 1 else None
        if len(cmd.args) > 2:
            item["extra"] = cmd.args[2]
        payload["commands"].append(item)
    result = _mvu_post("validate_all", payload)
    if result is None or "results" not in result:
        return commands, []  # Server unavailable → allow all
    valid = []
    errors = []
    for i, r in enumerate(result["results"]):
        if r.get("ok"):
            valid.append(commands[i])
        else:
            errors.append({
                "command": commands[i].full_match.strip() if commands[i].full_match else str(commands[i].args),
                "error": r.get("error", "unknown"),
            })
    return valid, errors


def _get_injections_via_server(stat_data):
    """Get injection keywords from mvu_server. Returns list of dicts."""
    result = _mvu_post("inject", {"stat_data": stat_data})
    if result is None:
        return []
    keywords = result.get("keywords", [])
    return [{"keyword": kw, "section": f"## {kw}"} for kw in keywords]


def _load_var_schema(card_folder, fallback_data=None):
    """Load variable schema.

    Prefers mvu_server (real Zod schema loaded from card scripts).
    Falls back to .initvar_schema.json → generate_schema() from data.
    """
    # Try mvu_server first
    schema_meta = _mvu_post("schema")
    if schema_meta and schema_meta.get("fields"):
        return _build_schema_from_definition(schema_meta)

    # Fallback: file-based schema
    schema_path = Path(card_folder) / ".initvar_schema.json"
    if schema_path.exists():
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_raw = json.load(f)
            return _build_schema_from_definition(schema_raw)
        except Exception:
            pass

    # Last resort: generate from data
    if fallback_data is None:
        initvar_path = Path(card_folder) / ".initvar.json"
        if initvar_path.exists():
            try:
                with open(initvar_path, "r", encoding="utf-8") as f:
                    fallback_data = json.load(f)
            except Exception:
                pass
    if fallback_data:
        return generate_schema(fallback_data)
    return None


def _build_schema_from_definition(schema_def):
    """Build a SchemaNode tree from Node.js runner's schema definition."""
    fields = schema_def.get("fields", {})
    enums = schema_def.get("enums", {})
    constraints = schema_def.get("constraints", [])

    # Group field paths into a tree structure
    root = {"_children": {}, "_type": "object"}

    for path, info in fields.items():
        parts = path.split(".")
        node = root
        for i, part in enumerate(parts):
            if part == "*":
                # Wildcard = key can be anything
                node["_type"] = "object"
                continue
            if part not in node["_children"]:
                node["_children"][part] = {"_children": {}, "_type": "any"}
            node = node["_children"][part]
            if i == len(parts) - 1:
                node["_type"] = info.get("type", "any")
                node["_nullable"] = info.get("nullable", True)

    # Apply enum constraints
    for enum_path, enum_values in enums.items():
        parts = enum_path.split(".")
        node = root
        for part in parts:
            if part.startswith("_"):
                # _keys / _values are metadata keys
                break
            if part == "*":
                node["_type"] = "object"
                continue
            if part not in node["_children"]:
                node["_children"][part] = {"_children": {}, "_type": "any"}
            node = node["_children"][part]

    # Convert to SchemaNode
    return _dict_to_schema_node(root)


def _dict_to_schema_node(d):
    """Recursively convert dict tree to SchemaNode."""
    node_type = d.get("_type", "any")
    properties = {}
    for k, v in d.get("_children", {}).items():
        properties[k] = _dict_to_schema_node(v)

    schema = SchemaNode(
        type=node_type,
        extensible="*" in d.get("_children", {}),
    )
    if properties:
        schema.properties = properties
    return schema


def _looks_like_prior_reframe(edit):
    probe_parts = []
    if isinstance(edit, dict):
        for key in ("reason", "summary", "first_paragraph", "new_first_paragraph", "ai", "content", "new_ai"):
            value = edit.get(key)
            if isinstance(value, str):
                probe_parts.append(value)
    probe = "\n".join(probe_parts).lower()
    cues = (
        "previous",
        "prior",
        "earlier",
        "old",
        "reframe",
        "dream",
        "\u4e0a\u4e00\u8f6e",
        "\u5148\u524d",
        "\u4e4b\u524d",
        "\u524d\u6587",
        "\u65e7",
        "\u68a6",
        "\u68a6\u5883",
        "\u6539\u5b9a",
        "\u4fee\u6b63",
        "\u91cd\u5199",
        "\u56de\u62e8",
    )
    return any(cue in probe for cue in cues)


def _derived_edit_record(turn_index, op, reason, original_turn_index=None):
    item = {"turn_index": turn_index, "op": op, "reason": reason}
    if original_turn_index is not None and original_turn_index != turn_index:
        item["original_turn_index"] = original_turn_index
    return item


def apply_derived_content_edits(log, edits, existing_turn_count=None):
    """Apply player-requested repairs to AI-derived turn content.

    Edits never touch player input fields. They are for cases where the user says
    e.g. "modify the first AI paragraph" while also submitting the next turn.
    """
    if not isinstance(edits, list):
        return []
    applied = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        try:
            turn_index = int(edit.get("turn_index", 0))
        except Exception:
            continue
        original_turn_index = turn_index
        if existing_turn_count is not None and turn_index >= existing_turn_count:
            if existing_turn_count > 0 and _looks_like_prior_reframe(edit):
                turn_index = existing_turn_count - 1
            else:
                continue
        if turn_index < 0 or turn_index >= len(log):
            continue
        target = log[turn_index]
        ai_text = target.get("ai", "") or ""
        summary_text = target.get("summary", "") or ""
        reason = str(edit.get("reason") or "player-directed derived content repair")
        applied_this_edit = False

        # Full replacement for broad rewrite requests (e.g. "rewrite all previous chapters").
        new_ai = edit.get("ai") or edit.get("content") or edit.get("new_ai")
        if isinstance(new_ai, str) and new_ai.strip():
            target["ai"] = new_ai.strip()
            applied.append(_derived_edit_record(turn_index, "replace_ai", reason, original_turn_index))
            applied_this_edit = True

        # Currently supported partial operation: replace the first narrative paragraph.
        new_first = edit.get("first_paragraph") or edit.get("new_first_paragraph")
        if isinstance(new_first, str) and new_first.strip():
            para = new_first.strip()
            if not para.startswith("<p>"):
                para = "<p>" + para + "</p>"
            if re.search(r"<p>.*?</p>", ai_text, flags=re.DOTALL):
                ai_text = re.sub(r"<p>.*?</p>", para, ai_text, count=1, flags=re.DOTALL)
            else:
                ai_text = para + "\n" + ai_text
            target["ai"] = ai_text
            applied.append(_derived_edit_record(turn_index, "replace_first_paragraph", reason, original_turn_index))
            applied_this_edit = True

        new_summary = edit.get("summary")
        if isinstance(new_summary, str) and new_summary.strip():
            target["summary"] = new_summary.strip()
            target["ai"] = re.sub(r"<summary>.*?</summary>", "<summary>" + new_summary.strip() + "</summary>", target.get("ai", ""), flags=re.DOTALL)
            applied.append(_derived_edit_record(turn_index, "replace_summary", reason, original_turn_index))
            applied_this_edit = True

        if applied_this_edit:
            target.setdefault("derived_repairs", []).append({
                "reason": reason,
                "source": "response.txt/derived_content_edits",
            })
    return applied


def append_turn(card_folder, polished_input=None, content="", summary="", options="", is_opening=False, tokens=None, full_text="", character_dialogues=None, derived_content_edits=None):
    """Append a new turn to chat_log and rebuild content.js."""
    log = read_chat_log(card_folder)
    next_index = len(log)
    pending_user_turn = read_pending_user_turn(card_folder)

    # ── MVU: Compute current variables ──
    prev_vars = compute_current_variables(log)

    # ── MVU: Load variable schema for validation ──
    var_schema = _load_var_schema(card_folder, prev_vars)

    # ── MVU: Extract commands from full response text ──
    commands = extract_commands(full_text or content)
    # On first turn, try loading .initvar.json as baseline
    if not prev_vars:
        initvar_path = Path(card_folder) / ".initvar.json"
        if initvar_path.exists():
            try:
                with open(initvar_path, "r", encoding="utf-8") as f:
                    prev_vars = json.load(f)
            except Exception:
                pass

    # ── MVU: Validate commands against schema via mvu_server (real Zod) ──
    valid_commands = []
    validation_errors = []
    if commands:
        # Try server-side validation first (real Zod schema)
        valid_commands, validation_errors = _validate_commands_via_server(commands)
        # If server returned nothing (unavailable), fall back to file-based schema
        if not valid_commands and not validation_errors:
            if var_schema:
                for cmd in commands:
                    ok, err = validate_command(cmd, var_schema)
                    if ok:
                        valid_commands.append(cmd)
                    else:
                        validation_errors.append({"command": cmd.full_match.strip() if cmd.full_match else str(cmd.args), "error": err})
            else:
                valid_commands = commands
        if validation_errors:
            for ve in validation_errors:
                print(f"[handler] schema validation: {ve['error']} (command: {ve['command'][:80]})")
    else:
        valid_commands = commands

    new_vars, changes = execute_commands(prev_vars, valid_commands) if valid_commands else (prev_vars, {})
    # Attach validation errors to changes delta
    if validation_errors:
        changes["_validation_errors"] = validation_errors

    # ── Resolve template macros in content ──
    resolved_vars = new_vars if new_vars else prev_vars
    content = resolve_macros(content, resolved_vars)

    ai_text = content
    if summary:
        ai_text += "\n\n<summary>" + summary + "</summary>"
    if options:
        ai_text += "\n\n<options>\n" + options + "\n</options>"

    entry = {"index": next_index, "ai": ai_text, "summary": summary}
    normalized_dialogues = _normalize_character_dialogues(character_dialogues)
    if normalized_dialogues:
        entry["character_dialogues"] = normalized_dialogues
    if not is_opening:
        if pending_user_turn:
            pending_display = (
                pending_user_turn.get("display_text")
                if "display_text" in pending_user_turn
                else pending_user_turn.get("raw_text")
            ) or ""
            if pending_display:
                entry["user"] = pending_display
            if pending_user_turn.get("id"):
                entry["player_input_id"] = pending_user_turn.get("id")
        elif polished_input:
            entry["user"] = polished_input
        if polished_input:
            entry["polished_input"] = polished_input
    if tokens:
        entry["tokens"] = tokens
    # Store variables if any exist or were changed
    if new_vars:
        entry["variables"] = {"stat_data": new_vars}
        if changes:
            entry["variables"]["delta"] = changes
    # Always carry forward variables from previous turns even if unchanged
    elif prev_vars:
        entry["variables"] = {"stat_data": prev_vars}

    existing_turn_count = len(log)
    log.append(entry)
    applied_repairs = apply_derived_content_edits(log, derived_content_edits, existing_turn_count=existing_turn_count)
    if applied_repairs:
        entry["derived_content_edits_applied"] = applied_repairs
    write_chat_log(card_folder, log)
    if pending_user_turn:
        clear_pending_user_turn(card_folder)
    postprocess = _load_postprocess_output(card_folder)

    try:
        evolve_blank_profile(
            card_folder,
            next_index,
            entry.get("user", ""),
            ai_text,
            summary,
            new_vars or prev_vars or {},
        )
    except Exception as e:
        print(f"[handler] blank profile evolution skipped: {e}")

    write_content_js(card_folder)

    # ── Variable audit: write diff to .var_diff.json for next-turn awareness ──
    try:
        audit = audit_variables(prev_vars or {}, new_vars or {}, content)
        audit_path = Path(card_folder) / ".var_diff.json"
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # never block turn delivery for audit failure

    # Update state: increment generatedCount and accumulate totalTokens
    state_raw = read_state()
    state_raw = _apply_postprocess_state(state_raw, postprocess)
    new_count = (next_index + 1)
    state_raw = re.sub(r'(\s+generatedCount:\s*)\d+', rf'\g<1>{new_count}', state_raw)
    if tokens:
        turn_total = tokens.get("total") or tokens.get("round_total") or tokens.get("startup_total") or 0
        if turn_total > 0:
            # Accumulate into totalTokens
            m = re.search(r'totalTokens:\s*(\d+)', state_raw)
            prev_total = int(m.group(1)) if m else 0
            new_total = prev_total + turn_total
            state_raw = re.sub(r'(\s+totalTokens:\s*)\d+', rf'\g<1>{new_total}', state_raw)
    write_state(state_raw, card_folder)

    return next_index


def reroll_last(card_folder):
    """Delete last turn, restore user input for regeneration. Returns the user text."""
    log = read_chat_log(card_folder)
    if not log:
        return None

    last = log[-1]

    # Refuse to reroll an opening (no user field) — nothing to regenerate from
    if not last.get("user"):
        return None

    user_text = last.get("user", "")
    log.pop()
    write_chat_log(card_folder, log)
    write_content_js(card_folder)

    # Update generatedCount
    state_raw = read_state()
    new_count = len(log)
    state_raw = re.sub(r'(\s+generatedCount:\s*)\d+', rf'\g<1>{new_count}', state_raw)
    write_state(state_raw, card_folder)
    (STYLES / "input.txt").write_text(user_text, encoding="utf-8")
    (STYLES / ".pending").touch()
    return user_text


def delete_turns(card_folder, from_index):
    """Delete turns with index >= from_index."""
    log = read_chat_log(card_folder)
    log = [t for t in log if t.get("index", 0) < from_index]
    write_chat_log(card_folder, log)
    write_content_js(card_folder)

    # Update generatedCount and clear pending
    (STYLES / ".pending").unlink(missing_ok=True)
    state_raw = read_state()
    new_count = len(log)
    state_raw = re.sub(r'(\s+generatedCount:\s*)\d+', rf'\g<1>{new_count}', state_raw)
    write_state(state_raw, card_folder)


# ═══ Injection Rules ═══

def apply_injections(card_folder):
    """Get injection keywords from mvu_server (real script execution).

    Falls back to file-based .injection_rules.json parsing.

    Returns a list of dicts: [{keyword, source_path, one_liner, section}, ...]
    Prints JSON to stdout for consumption by Cron prompt.
    """
    import re as _re

    # Get current variables from chat_log
    log = read_chat_log(card_folder)
    stat_data = {}
    for turn in reversed(log):
        v = turn.get("variables")
        if v and "stat_data" in v:
            stat_data = v["stat_data"]
            break

    # Try mvu_server first (real keyword script execution)
    server_keywords = _get_injections_via_server(stat_data)
    if server_keywords:
        # Load worldbook index for one_liner enrichment
        index_path = Path(card_folder) / "memory" / ".worldbook_index.json"
        worldbook_index = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    for entry in json.load(f):
                        worldbook_index[entry.get("keyword", "")] = entry
            except Exception:
                pass
        for kw in server_keywords:
            entry = worldbook_index.get(kw["keyword"], {})
            kw["one_liner"] = entry.get("one_liner", "")
            kw["section"] = entry.get("section", kw["section"])
        print(json.dumps(server_keywords, ensure_ascii=False))
        return server_keywords

    # Fallback: file-based rules
    rules_path = Path(card_folder) / ".injection_rules.json"
    if not rules_path.exists():
        print(json.dumps([]))
        return []

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = json.load(f)
    except Exception:
        print(json.dumps([]))
        return []

    if not rules:
        print(json.dumps([]))
        return []

    # Load worldbook index
    index_path = Path(card_folder) / "memory" / ".worldbook_index.json"
    worldbook_index = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    worldbook_index[entry.get("keyword", "")] = entry
        except Exception:
            pass

    results = []
    seen = set()

    for rule in rules:
        source_path = rule.get("source_path", "")
        split_pattern = rule.get("split_pattern", "[、,，\\n]")
        prefix = rule.get("prefix", "")

        value = _lodash_get(stat_data, source_path)
        if not value or not isinstance(value, str) or not value.strip():
            continue

        split_re = split_pattern
        if split_re.startswith("/") and split_re.rfind("/") > 0:
            last_slash = split_re.rfind("/")
            split_re = split_re[1:last_slash]
        try:
            keywords = _re.split(split_re, value)
        except _re.error:
            keywords = value.replace("、", ",").replace("，", ",").split(",")

        for kw in keywords:
            kw = kw.strip()
            if not kw:
                continue
            if prefix and not kw.startswith(prefix):
                kw = prefix + kw
            if kw in seen:
                continue
            seen.add(kw)
            entry = worldbook_index.get(kw, {})
            results.append({
                "keyword": kw,
                "source_path": source_path,
                "one_liner": entry.get("one_liner", ""),
                "section": entry.get("section", f"## {kw}"),
            })

    print(json.dumps(results, ensure_ascii=False))
    return results


def _lodash_get(obj, path_str):
    """Resolve dot-separated path like '世界设定.性癖' from nested dict."""
    if not obj or not path_str:
        return None
    keys = path_str.split(".")
    current = obj
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
        if current is None:
            return None
    return current


# ═══ Bridge Calls ═══

def bridge_done():
    try:
        urllib.request.urlopen(BRIDGE + "/api/done")
    except Exception:
        pass


# ═══ Openings Management ═══

OPENINGS_FILE = STYLES / "openings.json"


def save_openings(openings):
    """Save openings list to openings.json."""
    with open(OPENINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(openings, f, ensure_ascii=False, indent=2)


def list_openings():
    """Return list of available openings."""
    if OPENINGS_FILE.exists():
        with open(OPENINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def switch_opening(card_folder, opening_id):
    """Replace the current opening (index 0) with a different one."""
    openings = list_openings()
    target = None
    for o in openings:
        if o["id"] == opening_id:
            target = o
            break
    if not target:
        return False

    log = read_chat_log(card_folder)
    if not log:
        return False

    # Only allow switching the opening (index 0 must be AI-only, no user input)
    if log[0].get("user"):
        return False

    # Replace opening AI content with the selected greeting
    # Convert plain-text paragraphs to <p> tags if not already HTML
    greeting = target["content"]
    if "<p>" not in greeting and "<content>" not in greeting:
        greeting = _text_to_p(greeting)

    # Use per-opening options if available, otherwise keep existing
    opts = target.get("options", "")
    if not opts:
        opts = _extract_options(log[0].get("ai", ""))
    opts_block = "\n".join('<font color="#b06a3d">' + o + '</font>' for o in opts) if isinstance(opts, list) else opts if opts else ""

    log[0]["ai"] = "<content>\n" + greeting + "\n</content>\n\n<summary>" + log[0].get("summary", "") + "</summary>\n\n<options>\n" + opts_block + "\n</options>"

    # Apply per-opening variable state if the opening defines one.
    # This matches real MVU behaviour where alternate greetings embed
    # <UpdateVariable> blocks to override [InitVar] baseline values.
    opening_vars = target.get("variables")
    if opening_vars:
        if "variables" not in log[0] or not log[0]["variables"]:
            log[0]["variables"] = {}
        log[0]["variables"]["stat_data"] = opening_vars
        log[0]["variables"]["delta"] = {}

    write_chat_log(card_folder, log)
    write_content_js(card_folder)
    return True


# ═══ CLI ═══

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python handler.py <card_folder> [--opening|--injections]")
        sys.exit(1)

    card_folder = sys.argv[1]

    if "--rebuild" in sys.argv:
        result = rebuild_content(card_folder)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)

    if "--injections" in sys.argv:
        result = apply_injections(card_folder)
        if result:
            print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)

    is_opening = "--opening" in sys.argv

    # Read response.txt
    resp_path = STYLES / "response.txt"
    if not resp_path.exists():
        write_progress("error", "未找到 response.txt", percent=0)
        print("[handler] No response.txt found")
        sys.exit(1)

    response_text = resp_path.read_text(encoding="utf-8")
    parts = parse_response(response_text)

    content = parts.get("content", response_text)
    summary = parts.get("summary", "")
    options = parts.get("options", "")
    character_dialogues = parts.get("character_dialogues", [])
    derived_content_edits = parts.get("derived_content_edits", [])
    edit_only = bool(parts.get("edit_only")) and bool(derived_content_edits)
    polished_input = parts.get("polished_input", "")
    tokens = parts.get("tokens", None)

    if edit_only:
        log = read_chat_log(card_folder)
        apply_derived_content_edits(log, derived_content_edits)
        write_chat_log(card_folder, log)
        write_content_js(card_folder)
        clear_pending_user_turn(card_folder)
        resp_path.unlink(missing_ok=True)
        bridge_done()
        write_progress("complete", "派生内容已重写", percent=100)
        print("[handler] Derived content edits applied. content.js rebuilt.")
        sys.exit(0)

    # ── Opening: compute startup cost BEFORE append_turn so turn 0 has token stats ──
    if is_opening and not tokens:
        try:
            from token_stats import save_checkpoint, load_checkpoint
            save_checkpoint(card_folder, label="startup_end")
            cp = load_checkpoint(card_folder)
            startup_cost = cp.get("startup_cost", {})
            st_in = startup_cost.get("input_tokens", 0)
            st_out = startup_cost.get("output_tokens", 0)
            if st_in > 0 or st_out > 0:
                tokens = {
                    "in": st_in,
                    "out": st_out,
                    "total": st_in + st_out,
                    "cache_read": startup_cost.get("cache_read", 0),
                    "cache_hit": startup_cost.get("cache_hit_pct", 0.0),
                    "is_startup": True,
                }
        except Exception:
            pass

    idx = append_turn(
        card_folder,
        polished_input=polished_input if not is_opening else None,
        content=content,
        summary=summary,
        options=options,
        character_dialogues=character_dialogues,
        derived_content_edits=derived_content_edits,
        is_opening=is_opening,
        tokens=tokens,
        full_text=response_text,
    )

    # Clean up
    resp_path.unlink(missing_ok=True)
    bridge_done()
    write_progress("complete", "回复已完成", percent=100)

    print(f"[handler] Turn {idx} saved. content.js rebuilt.")
