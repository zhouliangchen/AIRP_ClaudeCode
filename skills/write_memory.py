#!/usr/bin/env python3
"""Append a dated turn-summary entry to memory/project.md and update MEMORY.md index.

Replaces the mechanical portion of CLAUDE.md step 8. AI still verifies/edits
the result and handles feedback.md / user.md (which require narrative understanding).

Usage:
  python write_memory.py <card_folder>
Output:
  Prints what was written to project.md and the updated index line.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import actor_memory_store
from io_utils import read_json as _read_json
from response_parser import parse_response


CST = timezone(timedelta(hours=8))


def _extract_parts(response_text: str) -> dict:
    """Parse response.txt tags (content/summary/polished_input)."""
    parts = parse_response(response_text)
    return {k: parts[k] for k in ("polished_input", "content", "summary") if k in parts}


def _get_last_n_turns(card_folder: Path, n: int = 3) -> list[dict]:
    """Read last N turns from chat_log.json."""
    log_path = card_folder / "chat_log.json"
    if not log_path.exists():
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        log = json.load(f)
    return log[-n:] if len(log) > n else log


def _write_character_memory(card: Path, date_str: str, summary: str, recent_turns: list[dict]) -> list[str]:
    """Maintain lightweight per-character memory files for blank/subagent contexts."""
    updated = []
    card_data = _read_json(card / ".card_data.json", {}) or {}
    targets: list[tuple[str, Path]] = []
    seen: set[str] = set()
    if card_data.get("mode") == "blank_bootstrap" or card_data.get("source_type") == "blank":
        paths = actor_memory_store.ensure_actor_files(card, "player")
        targets.append((paths.name, paths.objective_dir))
        seen.add(paths.name)

    orchestration = card_data.get("character_orchestration", {})
    for name in orchestration.get("major", []) or []:
        if isinstance(name, str) and name.strip():
            paths = actor_memory_store.ensure_actor_files(card, f"character:{name.strip()}")
            if paths.name not in seen:
                targets.append((paths.name, paths.objective_dir))
                seen.add(paths.name)

    if not targets:
        return updated

    last = recent_turns[-1] if recent_turns else {}
    stat_data = last.get("variables", {}).get("stat_data", {}) if isinstance(last.get("variables"), dict) else {}

    for target, char_dir in targets:
        char_dir.mkdir(parents=True, exist_ok=True)
        recent_path = char_dir / "recent.md"
        existing = recent_path.read_text(encoding="utf-8") if recent_path.exists() else "# 近期角色沉淀\n"
        line = f"\n- {date_str}: {summary}\n"
        recent_path.write_text((existing.rstrip() + line)[-6000:], encoding="utf-8")

        goals_path = char_dir / "goals.md"
        if not goals_path.exists():
            goals_path.write_text("# 角色目标\n\n待后续剧情或 subagent 反馈沉淀。\n", encoding="utf-8")

        profile_path = char_dir / "profile.md"
        if not profile_path.exists():
            profile_path.write_text("# 角色档案\n\n待后续剧情沉淀。\n", encoding="utf-8")

        state_path = char_dir / "state.json"
        state = _read_json(state_path, {}) or {}
        state["last_updated"] = date_str
        state["last_summary"] = summary
        if stat_data:
            state["latest_shared_stat_keys"] = list(stat_data.keys())[:20]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        updated.append(target)
    return updated


def write_memory(card_folder: str) -> dict:
    card = Path(card_folder)
    memory_dir = card / "memory"
    project_path = memory_dir / "project.md"
    mem_index_path = memory_dir / "MEMORY.md"
    styles_dir = Path(__file__).parent / "styles"

    # 1. Read response.txt (may already be cleaned up by handler.py)
    resp_path = styles_dir / "response.txt"
    parts = {}
    if resp_path.exists():
        response_text = resp_path.read_text(encoding="utf-8")
        parts = _extract_parts(response_text)

    # 2. Read recent turns from chat_log
    recent_turns = _get_last_n_turns(card, 3)

    # If response.txt was already cleaned up, extract summary from last turn
    if not parts and recent_turns:
        last = recent_turns[-1]
        summary_text = last.get("summary", "") or ""
        ai_text = last.get("ai", "")
        user_text = last.get("user", "")
        if not summary_text:
            # Try extracting summary tag from ai text
            m = re.search(r'<summary>(.*?)</summary>', ai_text, re.DOTALL)
            if m:
                summary_text = m.group(1).strip()
        parts = {"summary": summary_text}

    # 3. Read existing project.md
    existing = ""
    if project_path.exists():
        existing = project_path.read_text(encoding="utf-8")

    # 4. Build new entry
    now = datetime.now(CST)
    date_str = now.strftime("%Y-%m-%d %H:%M")
    summary = parts.get("summary", "(无摘要)")

    # Extract user action from recent turn
    user_action = ""
    if recent_turns:
        last = recent_turns[-1]
        user_text = last.get("user", "")
        if user_text:
            # Strip HTML tags for display
            user_text_clean = re.sub(r"<[^>]*>", "", user_text)[:200]
            user_action = user_text_clean

    # Extract variable changes from recent turn
    var_changes = ""
    if recent_turns:
        last = recent_turns[-1]
        delta = last.get("variables", {}).get("delta", {})
        if delta:
            change_items = []
            for path, change in delta.items():
                if path.startswith("_"):
                    continue
                old = change.get("old")
                new = change.get("new")
                reason = change.get("reason", "")
                if reason:
                    change_items.append(f"- {path}: {old} → {new} ({reason})")
                else:
                    change_items.append(f"- {path}: {old} → {new}")
            if change_items:
                var_changes = "\n### 变量变更\n" + "\n".join(change_items[:10])

    entry = f"""
---

## {date_str}

**摘要**: {summary}

**用户行动**: {user_action or "(开局/系统事件)"}
{var_changes}
"""

    # 5. Append to project.md
    new_content = existing.rstrip() + "\n" + entry + "\n"
    project_path.write_text(new_content, encoding="utf-8")

    # 6. Update per-character memory for blank-card and subagent contexts
    character_memory_updated = _write_character_memory(card, date_str, summary, recent_turns)

    # 7. Update MEMORY.md index (update the project.md line)
    index_updated = False
    if mem_index_path.exists():
        index_text = mem_index_path.read_text(encoding="utf-8")
        new_summary_line = f"- [project.md](memory/project.md) — {date_str} {summary[:60]}"
        if re.search(r"\[project\.md\]\(memory/project\.md\)", index_text):
            index_text = re.sub(
                r"- \[project\.md\]\(memory/project\.md\).*",
                new_summary_line,
                index_text,
            )
        else:
            index_text = index_text.rstrip() + "\n" + new_summary_line + "\n"
        mem_index_path.write_text(index_text, encoding="utf-8")
        index_updated = True

    return {
        "ok": True,
        "date": date_str,
        "summary": summary[:80],
        "project_md_size": len(new_content),
        "index_updated": index_updated,
        "character_memory_updated": character_memory_updated,
    }


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    result = write_memory(folder)
    print(json.dumps(result, ensure_ascii=False, indent=2))
