#!/usr/bin/env python3
"""
round_prepare.py — 回合预处理管线。

收集 AI 生成叙事所需的全部上下文，输出到单一的 round_context.txt。
替代 CLAUDE.md「每轮处理」步骤 1-5.1 中所有机械性操作。

缓存策略：静态内容放文件开头（前缀缓存命中），动态内容放文件末尾。

用法:
  python round_prepare.py <card_folder> <ROOT>
"""

import json
import os
import re
import sys
from pathlib import Path

# In-process imports replace subprocess calls (was: subprocess.run to these scripts).
import agent_intents
import agent_messages
import agent_packets
import agent_snapshots
import hidden_settings
import match_worldbook
import mvu_check
from handler import apply_injections, write_progress
from io_utils import read_file, read_json, walk_paths


def list_initvar_paths(initvar):
    """Recursively list all paths in initvar with current values."""
    return "\n".join(walk_paths(initvar))


def _load_reference_sections(card_folder):
    """Read reference.md once and index by ## section title for O(1) lookup."""
    ref_path = Path(card_folder) / "memory" / "reference.md"
    if not ref_path.exists():
        return {}
    try:
        text = ref_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    sections = {}
    current_title = None
    current_lines = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines)
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)
            if len(current_lines) >= 200:
                break
    if current_title is not None:
        sections[current_title] = "\n".join(current_lines)
    return sections


def grep_reference_section(ref_sections, title):
    """Return a cached reference.md section by Markdown or bare title."""
    if not isinstance(ref_sections, dict):
        return ""
    key = str(title or "").lstrip("#").strip()
    if not key:
        return ""
    return str(ref_sections.get(key, "")).strip()


def _load_player_input_history(card_folder, limit=20):
    path = Path(card_folder) / ".player_inputs.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    items = []
    for line in lines[-limit:]:
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


def _load_player_input_edits(card_folder, limit=20, processed=False):
    path = Path(card_folder) / ".player_input_edits.jsonl"
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if processed is not None and bool(item.get("processed", False)) is not bool(processed):
            continue
        items.append(item)
    return items


def _short_context(text, limit=260):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name.strip()) or "_unknown"


def _read_character_file(card_folder, name, fname):
    path = Path(card_folder) / "memory" / "characters" / _safe_name(name) / fname
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")[:3000]
        except Exception:
            pass
    return ""


def _initialize_dispatcher_runtime(run_dir):
    """Create the first message and intent for dispatcher-first execution."""

    message = _find_input_received_message(run_dir)
    if message is None:
        message_result = agent_messages.append_message(
            run_dir,
            {
                "from": "main_agent",
                "to": ["gm", "input_analyst"],
                "type": "input_received",
                "visibility": "gm_only",
                "payload": _fallback_input_received_payload(run_dir),
            },
        )
        if not message_result.get("ok"):
            raise RuntimeError(f"failed to append input_received message: {message_result}")
        message = message_result.get("message", {})

    message_id = (message or {}).get("id", "")
    existing_intent = _find_existing_analyze_input_intent(run_dir, message_id)
    if existing_intent is not None:
        return {
            "message": message,
            "intent": existing_intent,
        }
    intent_result = agent_intents.create_intent(
        run_dir,
        {
            "requested_by": "main_agent",
            "type": "analyze_input",
            "source_message_id": message_id,
            "payload": {
                "input_path": "input.json",
                "input_analysis_request_path": "input_analysis.request.md",
            },
            "policy": {"source": "round_prepare"},
        },
    )
    if not intent_result.get("ok"):
        raise RuntimeError(f"failed to create analyze_input intent: {intent_result}")
    return {
        "message": message,
        "intent": intent_result.get("intent", {}),
    }


def _find_input_received_message(run_dir):
    messages = agent_messages.read_messages(run_dir)
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "input_received":
            continue
        if not isinstance(item.get("id"), str) or not item.get("id"):
            continue
        if item.get("status") != "delivered":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if payload.get("input_path") == "input.json" and payload.get("raw_path") == "input.raw.json":
            return item
    return None


def _fallback_input_received_payload(run_dir):
    payload = {
        "input_path": "input.json",
        "raw_path": "input.raw.json",
    }
    raw_record = read_json(Path(run_dir) / "input.raw.json") or {}
    source_integrity = raw_record.get("source_integrity") if isinstance(raw_record, dict) else {}
    raw_text_hash = source_integrity.get("raw_text_sha256") if isinstance(source_integrity, dict) else None
    if raw_text_hash:
        payload["raw_text_hash"] = raw_text_hash
    return payload


def _find_existing_analyze_input_intent(run_dir, source_message_id):
    if not isinstance(source_message_id, str) or not source_message_id:
        return None
    for state in agent_intents.VALID_STATES:
        for intent in agent_intents.list_intents(run_dir, state):
            if not isinstance(intent, dict) or intent.get("type") != "analyze_input":
                continue
            if intent.get("source_message_id") == source_message_id:
                return intent
    return None


def build_character_contexts(card_folder, card_data, card_structure, chat_log, user_text):
    """Build compact per-character packets for optional Claude Code subagents."""
    orchestration = card_data.get("character_orchestration", {}) if isinstance(card_data, dict) else {}
    major = []
    for name in orchestration.get("major", []) or []:
        if isinstance(name, str) and name.strip():
            major.append(name.strip())

    # Blank self-card is always a candidate so the emergent role can keep continuity.
    if isinstance(card_data, dict) and (card_data.get("mode") == "blank_bootstrap" or card_data.get("source_type") == "blank"):
        if "_self" not in major:
            major.insert(0, "_self")

    # Use card structure characters as passive major candidates only when explicitly configured absent.
    if not major and isinstance(card_structure, dict):
        for name in (card_structure.get("characters", {}) or {}).keys():
            major.append(name)
            if len(major) >= 2:
                break

    latest_vars = {}
    for turn in reversed(chat_log or []):
        vars_obj = turn.get("variables", {}) if isinstance(turn, dict) else {}
        if isinstance(vars_obj, dict) and isinstance(vars_obj.get("stat_data"), dict):
            latest_vars = vars_obj["stat_data"]
            break

    packets = []
    # `max_parallel_subagents` is a runtime dispatch limit, not a registration cap.
    # Every explicitly registered important character needs an isolated context so
    # the GM can call that character when the scene creates a participation point.
    for name in major:
        safe = _safe_name(name)
        profile_md = _read_character_file(card_folder, safe, "profile.md")
        recent_md = _read_character_file(card_folder, safe, "recent.md")
        goals_md = _read_character_file(card_folder, safe, "goals.md")
        state_json = read_json(Path(card_folder) / "memory" / "characters" / safe / "state.json") or {}
        profile_json = read_json(Path(card_folder) / "memory" / "characters" / safe / "profile.json") or {}
        stat_slice = latest_vars.get(name, {}) if isinstance(latest_vars, dict) else {}
        if name == "_self" and not stat_slice:
            stat_slice = latest_vars.get("角色", {}) if isinstance(latest_vars, dict) else {}
        packets.append({
            "name": name,
            "importance": "major",
            "scene_relevance": "high" if name == "_self" else "normal",
            "profile_summary": profile_md[:1200],
            "recent_state": recent_md[:1200],
            "goals": goals_md[:1000],
            "state": state_json,
            "profile": profile_json,
            "stat_slice": stat_slice,
            "task_for_subagent": "站在该角色自身立场，给出本轮私有反应、意图、可选行动/台词、变量变化建议与记忆增量。不要代写最终叙事。",
        })
    return {"characters": packets, "minor_policy": orchestration.get("minor_policy", "main_agent")}


def main():
    if len(sys.argv) < 3:
        print("Usage: python round_prepare.py <card_folder> <ROOT>", file=sys.stderr)
        sys.exit(1)

    card_folder = sys.argv[1]
    root = sys.argv[2]
    styles_dir = Path(root) / "skills" / "styles"
    write_progress("round.preparing", "正在整理回合上下文", percent=30)

    # ── Token delta capture (retroactively fixes previous turn) ──
    pending_tokens = {}
    try:
        import token_stats
        ts_path = token_stats.locate_transcript()
        cp = token_stats.load_checkpoint(card_folder) if ts_path else {}
        t_offset = cp.get("last_byte_offset", 0)

        if cp.get("previous_checkpoint"):
            token_stats.compute_startup_cost(card_folder)
            cp = token_stats.load_checkpoint(card_folder)
            t_offset = cp.get("last_byte_offset", 0)

        usage = token_stats.read_usage_since(ts_path, t_offset) if ts_path else []
        pending_delta = token_stats.compute_delta(usage)

        if pending_delta.get("request_count", 0) > 0:
            pd_in = pending_delta["input_tokens"]
            pd_out = pending_delta["output_tokens"]
            pending_tokens = {
                "round_in": pd_in,
                "round_out": pd_out,
                "round_total": pd_in + pd_out,
                "cache_read": pending_delta["cache_read"],
                "cache_hit": pending_delta["cache_hit_pct"],
            }

            # Retroactively fix the previous AI turn's token data in chat_log
            cl_path = Path(card_folder) / "chat_log.json"
            cl = read_json(cl_path) or []
            if cl:
                prev_turn = cl[-1]
                cum = cp.get("cumulative", {})
                prev_turn["tokens"] = {
                    "in": pd_in,
                    "out": pd_out,
                    "total": pd_in + pd_out,
                    "cache_read": pending_delta["cache_read"],
                    "cache_hit": pending_delta["cache_hit_pct"],
                    "cumulative_in": cum.get("input_tokens", 0) + pd_in,
                    "cumulative_out": cum.get("output_tokens", 0) + pd_out,
                    "cumulative_total": cum.get("input_tokens", 0) + cum.get("output_tokens", 0) + pd_in + pd_out,
                }
                with open(cl_path, "w", encoding="utf-8") as f:
                    json.dump(cl, f, ensure_ascii=False, indent=2)

            # Advance checkpoint to current transcript position
            token_stats.save_checkpoint(card_folder, delta=pending_delta, label="round")
    except Exception:
        pass

    # ── Gather data first ──
    input_path = styles_dir / "input.txt"
    user_input = read_file(input_path)
    if user_input is None:
        user_input = ""
    user_text = user_input
    settings_path = styles_dir / "settings.json"
    settings = read_json(settings_path) or {}

    project_md = Path(card_folder) / "memory" / "project.md"
    recent_memory = ""
    if project_md.exists():
        raw = read_file(project_md)
        if raw:
            entries = re.split(r"\n(?=## \d{4}-\d{2}-\d{2})", raw)
            recent = entries[-3:] if len(entries) > 3 else entries
            recent_memory = "".join(recent).strip()[:3000]

    wb_index_path = Path(card_folder) / "memory" / ".worldbook_index.json"
    wb_index = read_json(wb_index_path) or []

    card_structure_path = Path(card_folder) / "memory" / ".card_structure.json"
    card_structure = read_json(card_structure_path)

    card_data = read_json(Path(card_folder) / ".card_data.json") or {}
    evolving_profile = card_data.get("evolving_profile", {}) if isinstance(card_data, dict) else {}

    # Worldbook variable matching
    match_result = None
    try:
        match_result = match_worldbook.match_worldbook(card_folder)
    except Exception:
        pass

    # Injections (apply_injections prints to stdout for CLI use; suppress here)
    injections = []
    try:
        import io as _io, contextlib as _ctxlib
        with _ctxlib.redirect_stdout(_io.StringIO()):
            injections = apply_injections(card_folder)
        if injections is None:
            injections = []
    except Exception:
        pass

    # Variable paths
    mvu_data = None
    try:
        mvu_data = mvu_check.generate_checklist(card_folder)
    except Exception:
        pass

    initvar_path = Path(card_folder) / ".initvar.json"
    initvar = read_json(initvar_path)

    chat_log_path = Path(card_folder) / "chat_log.json"
    chat_log = read_json(chat_log_path) or []
    player_input_history = _load_player_input_history(card_folder)
    player_input_edits = _load_player_input_edits(card_folder, processed=False)
    latest_player_input = player_input_history[-1] if player_input_history else {}
    explicit_input_payload = {}
    if (
        isinstance(latest_player_input, dict)
        and latest_player_input.get("input_schema") == "dual_channel_v1"
    ):
        latest_raw_text = "" if latest_player_input.get("raw_text") is None else str(latest_player_input.get("raw_text"))
        latest_role_text = "" if latest_player_input.get("role_text") is None else str(latest_player_input.get("role_text"))
        current_user_text = "" if user_text is None else str(user_text)
        current_user_text_for_matching = current_user_text.strip()
        if (
            latest_raw_text == current_user_text
            or latest_raw_text.strip() == current_user_text_for_matching
            or latest_role_text == current_user_text
            or latest_role_text.strip() == current_user_text_for_matching
        ):
            explicit_input_payload = dict(latest_player_input)
    try:
        hidden_setting_records = hidden_settings.load_hidden_settings(card_folder)
    except Exception:
        hidden_setting_records = []

    # Load reference.md once for O(1) section lookups this round
    ref_sections = _load_reference_sections(card_folder)

    character_contexts = build_character_contexts(
        card_folder, card_data, card_structure or {}, chat_log, user_text
    )
    agent_run_info = None
    agent_run_error = None
    dispatcher_runtime = None
    snapshot_result = None
    turn_index = len(chat_log)
    round_id = f"round-{turn_index + 1:06d}" if isinstance(turn_index, int) else "round-current"
    snapshot_result = agent_snapshots.create_snapshot(
        card_folder,
        round_id,
        reason="before_round_prepare",
    )
    try:
        agent_run_info = agent_packets.prepare_agent_run(
            card_folder=card_folder,
            user_text=user_text,
            chat_log=chat_log,
            card_data=card_data,
            character_contexts=character_contexts,
            turn_index=turn_index,
            input_payload=explicit_input_payload or None,
            hidden_setting_records=hidden_setting_records,
        )
    except Exception as exc:
        agent_run_error = str(exc)
        agent_run_info = None
        dispatcher_runtime = None
    run_dir = agent_run_info.get("run_dir") if isinstance(agent_run_info, dict) else None
    if run_dir:
        dispatcher_runtime = _initialize_dispatcher_runtime(run_dir)

    # ═══════════════════════════════════════════════
    # BUILD OUTPUT — static prefix first (cached),
    # dynamic suffix last (uncached per round).
    # ═══════════════════════════════════════════════

    static_parts = []
    dynamic_parts = []

    # ── STATIC PREFIX (rarely changes, good for prompt cache) ──

    static_parts.append(f"=== WORLD_INDEX ({len(wb_index)} entries) ===")
    if wb_index:
        for entry in wb_index:
            static_parts.append(
                f"  [{entry.get('keyword','?')}] {entry.get('one_liner','')[:80]}"
            )

    if card_structure:
        static_parts.append(f"\n=== CARD_STRUCTURE ===")
        static_parts.append(f"  has_stages: {card_structure.get('has_stages', False)}")
        static_parts.append(f"  has_events: {card_structure.get('has_events', False)}")
        chars = card_structure.get("characters", {})
        if chars:
            static_parts.append(f"  characters: {', '.join(chars.keys())}")
    else:
        static_parts.append("\n=== CARD_STRUCTURE ===\n  (none)")

    static_parts.append("\n=== SETTINGS ===")
    for key in ["style", "nsfw", "person", "wordCount", "antiImpersonation", "bgNpc", "charName"]:
        val = settings.get(key, "未设置")
        static_parts.append(f"  {key}: {val}")

    # Initvar paths are static (never change after card import)
    if initvar:
        static_parts.append("\n=== INITVAR_PATHS (baseline structure) ===")
        static_parts.append(list_initvar_paths(initvar))

    # ── DYNAMIC SUFFIX (changes every round) ──

    dynamic_parts.append("=== USER_INPUT ===")
    dynamic_parts.append(user_text)

    dynamic_parts.append("\n=== PLAYER_AUTHORITY_RULES ===")
    dynamic_parts.append("- 玩家历次输入是唯一权威事实源；不得改写、润色或删除 .player_inputs.jsonl 中的 raw_text/display_text。")
    dynamic_parts.append("- 不得擅自编辑、裁剪、合并或摘要玩家输入；正常回合不要输出 <polished_input>，处理证据应写入 <UpdateVariable>/<derived_content_edits> 等非显示控制标签。")
    dynamic_parts.append("- 若新玩家输入与既有 AI 叙事、角色资料、变量、记忆或世界设定冲突，以玩家新输入为准。")
    dynamic_parts.append("- 每轮都必须实时根据玩家最新输入评估过去剧情和设定；发现冲突时，可以小幅修改或完全重写 AI 派生数据。")
    dynamic_parts.append("- 冲突不是只在正文里解释一句；必须在本轮 <UpdateVariable> 中修正所有受影响的 AI 派生变量/角色状态/世界假设。")
    dynamic_parts.append("- 可修正或重写 AI 生成内容、摘要、memory、角色状态、变量和派生设定，使其服从玩家输入；不得把玩家新设定当作可选建议。")
    dynamic_parts.append("- 若玩家把上一轮 AI 推演改定为梦境、误会、预示、回忆、幻觉、演戏或已被覆盖的分支，本轮必须明确降级上一轮 AI 内容为对应性质，并阻止它继续作为现实事实污染后续变量。")

    dynamic_parts.append("\n=== PLAYER_INPUT_INTERPRETATION ===")
    dynamic_parts.append("- Do not infer semantic intent from fixed keywords in the preserved player text.")
    dynamic_parts.append("- Durable routing, hidden facts, retcons, character promotion, and narrative directives must come from validated input_analysis.output.json applied by input_analysis_apply.py.")
    dynamic_parts.append("- Before input analysis is applied, raw text is preserved for the input analyst and GM only; actor-facing packets must use explicit dual-channel or analysis-applied routing.")

    if hidden_setting_records:
        dynamic_parts.append("\n=== GM_ONLY_HIDDEN_SETTINGS ===")
        dynamic_parts.append("These are player-authored hidden or long-term facts. GM/story/critic may use them for continuity, but player/character agents must not receive knowledge their viewpoint cannot have.")
        for item in hidden_setting_records[-10:]:
            dynamic_parts.append(
                "  - "
                f"id={item.get('id', '')} "
                f"round={item.get('round_id', '')} "
                f"source_input_id={item.get('source_input_id', '')} "
                f"status={item.get('status', 'active')} "
                f"text={item.get('text', '')[:600]!r}"
            )

    if player_input_history:
        dynamic_parts.append("\n=== PLAYER_INPUT_HISTORY (authoritative, recent) ===")
        for item in player_input_history:
            stamp = item.get("created_at", "")
            raw = item.get("raw_text", "")
            display = item.get("display_text", raw)
            dynamic_parts.append(f"  - {stamp} [{item.get('id', '?')}] raw={raw[:300]!r} display={display[:300]!r}")

    if player_input_edits:
        dynamic_parts.append("\n=== PLAYER_INPUT_EDITS_PENDING ===")
        dynamic_parts.append("These player-authored edits may require repairing AI narrative, memory, variables, character files, or derived UI. Do not change the edited player text.")
        for item in player_input_edits:
            dynamic_parts.append(
                "  - "
                f"{item.get('created_at', '')} [{item.get('id', '?')}] "
                f"mode={item.get('mode', '')} input_id={item.get('input_id', '')} "
                f"branch_from_index={item.get('branch_from_index', '')} "
                f"old={item.get('old_raw_text', '')[:220]!r} "
                f"new={item.get('new_raw_text', '')[:220]!r}"
            )

    # Pending token delta from previous round's generation
    if pending_tokens:
        dynamic_parts.append("\n=== PENDING_TOKENS ===")
        for k, v in pending_tokens.items():
            dynamic_parts.append(f"  {k}: {v}")

    # Worldbook variable matches
    dynamic_parts.append("\n=== WORLD_MATCHES ===")
    if match_result:
        for i, m in enumerate(match_result[:3]):
            dynamic_parts.append(f"\n  --- Match {i+1}: {m['keyword']} (score={m['score']}, {m['reason']}) ---")
            dynamic_parts.append(f"  Title: {m['title']}")
            dynamic_parts.append(f"  One-liner: {m['one_liner'][:100]}")
            full = grep_reference_section(ref_sections, m["section"].lstrip("#").strip())
            if full:
                dynamic_parts.append("  Full entry:")
                for line in full.split("\n")[:100]:
                    dynamic_parts.append(f"    {line}")
    else:
        dynamic_parts.append("  (no matches)")

    # Injections — each item is a dict with keyword/section/one_liner
    dynamic_parts.append("\n=== INJECTIONS ===")
    if injections:
        for inj in injections:
            kw = inj.get("keyword", "") if isinstance(inj, dict) else str(inj)
            one_liner = inj.get("one_liner", "") if isinstance(inj, dict) else ""
            section = inj.get("section", f"## {kw}") if isinstance(inj, dict) else f"## {kw}"
            dynamic_parts.append(f"\n  Keyword: {kw}" + (f" — {one_liner[:80]}" if one_liner else ""))
            full = grep_reference_section(ref_sections, section.lstrip("#").strip())
            if full:
                for line in full.split("\n")[:80]:
                    dynamic_parts.append(f"    {line}")
    else:
        dynamic_parts.append("  (no injections)")

    # Variable paths
    dynamic_parts.append("\n=== VARIABLE_PATHS ===")
    if mvu_data:
        dynamic_parts.append(f"  Sections: {', '.join(mvu_data.get('sections', []))}")
        dynamic_parts.append(f"  Total paths: {mvu_data.get('total_paths', '?')}")
        dynamic_parts.append(f"  Touched last turn: {', '.join(mvu_data.get('touched_last_turn', []))}")
        dynamic_parts.append(f"  Untouched last turn: {', '.join(mvu_data.get('untouched_last_turn', []))}")
        checklist = mvu_data.get("checklist", "")
        if checklist:
            dynamic_parts.append("\n  Path details:")
            for line in checklist.split("\n"):
                dynamic_parts.append(f"  {line}")
        dynamic_parts.append(f"\n  Reminder: {mvu_data.get('reminder', '')}")
    else:
        dynamic_parts.append("  (mvu_check unavailable)")

    # Recent memory
    if recent_memory:
        dynamic_parts.append("\n=== RECENT_MEMORY ===")
        dynamic_parts.append(recent_memory)

    if evolving_profile:
        dynamic_parts.append("\n=== EVOLVING_PROFILE ===")
        dynamic_parts.append(json.dumps(evolving_profile, ensure_ascii=False, indent=2)[:4000])

    if character_contexts.get("characters"):
        dynamic_parts.append("\n=== CHARACTER_CONTEXTS ===")
        dynamic_parts.append(f"  minor_policy: {character_contexts.get('minor_policy', 'main_agent')}")
        dynamic_parts.append("  Full JSON also written to skills/styles/character_contexts.json")
        for ch in character_contexts.get("characters", []):
            dynamic_parts.append(f"\n  Character: {ch.get('name')} ({ch.get('importance')}, relevance={ch.get('scene_relevance')})")
            if ch.get("profile_summary"):
                dynamic_parts.append("    Profile: " + ch.get("profile_summary", "")[:300].replace("\n", " / "))
            if ch.get("recent_state"):
                dynamic_parts.append("    Recent: " + ch.get("recent_state", "")[:300].replace("\n", " / "))

    if agent_run_info:
        dynamic_parts.append("\n=== AGENT_RUN ===")
        dynamic_parts.append(f"  run_dir: {agent_run_info.get('run_dir', '')}")
        routed = agent_run_info.get("routed_input", {})
        dynamic_parts.append("  role_channel: " + (routed.get("role_channel") or "(empty)")[:500])
        dynamic_parts.append("  user_instruction_channel: " + (routed.get("user_instruction_channel") or "(empty)")[:500])
        dynamic_parts.append("  packet_contract: GM/player/character context packets are written under this run_dir.")
    elif agent_run_error:
        dynamic_parts.append("\n=== AGENT_RUN ===")
        dynamic_parts.append("  agent_run_error: " + agent_run_error[:200])

    # Recent chat
    if chat_log:
        dynamic_parts.append("\n=== RECENT_CHAT (last 3 turns) ===")
        for entry in chat_log[-3:]:
            idx = entry.get("index", "?")
            user_txt = entry.get("user", "")[:200]
            summary = entry.get("summary", "")[:200]
            ai_txt = re.sub(r"<[^>]+>", "", entry.get("ai", ""))[:300]
            dynamic_parts.append(f"\n  Turn {idx}:")
            dynamic_parts.append(f"    User: {user_txt}")
            dynamic_parts.append(f"    AI: {ai_txt}")
            if summary:
                dynamic_parts.append(f"    Summary: {summary}")
    else:
        dynamic_parts.append("\n=== RECENT_CHAT ===\n  (no history — first turn)")

    # ── Write Output ──
    output_path = styles_dir / "round_context.txt"
    output_text = "\n".join(static_parts + dynamic_parts)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    character_contexts_path = styles_dir / "character_contexts.json"
    with open(character_contexts_path, "w", encoding="utf-8") as f:
        json.dump(character_contexts, f, ensure_ascii=False, indent=2)

    write_progress("input_analysis.awaiting", "等待输入分析", percent=35)

    print(json.dumps({
        "ok": True,
        "output": str(output_path),
        "character_contexts": str(character_contexts_path),
        "agent_run": agent_run_info.get("run_dir") if agent_run_info else None,
        "dispatcher_runtime": dispatcher_runtime,
        "snapshot": snapshot_result,
        "character_count": len(character_contexts.get("characters", [])),
        "size": len(output_text),
        "matches": len(match_result or []),
        "injections": len(injections),
        "is_first_turn": len(chat_log) == 0
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
