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
import agent_packets
import agent_workflow
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


def _analyze_player_input_for_plan(user_text, chat_log):
    """Heuristic, non-authoritative breakdown used to force generation-time discipline.

    The model still makes the final literary decision, but this section prevents mixed
    player inputs from being treated as a single ordinary action.
    """
    text = user_text or ""
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    components = []

    setting_cues = ["用于长期剧情引导", "长期剧情", "上帝视角", "设定", "主角是", "吊坠为", "代价是", "不需要立刻", "规则", "真相"]
    derived_edit_cues = ["第一段", "首段", "上一段", "上轮", "前文", "前面", "所有章节", "章节", "修改", "改写", "重写", "重新构思", "重新编写", "编写", "修正", "回复"]
    important_character_cues = ["设定重要角色", "重要角色", "核心角色"]
    synopsis_cues = ["直到", "之后", "再回过神", "梦境", "梦醒", "醒来", "时间却", "唯一提醒", "正在", "化作泡影", "记住", "预示", "过去了", "下课后"]
    action_pattern = re.compile(r"(^|[。！？，,\n])\s*我\s*(尝试|决定|打算|伸手|走向|冲向|询问|问|说|拿起|捡起|扔掉|丢掉|后退|离开|跟上|拉住|打开|看向|靠近|躲开|拒绝|同意)")

    def add(kind, para, reason):
        snippet = _short_context(para)
        if not any(c["type"] == kind and c["text"] == snippet for c in components):
            components.append({"type": kind, "text": snippet, "reason": reason})

    for para in paragraphs:
        stripped = para.strip()
        inner = stripped[1:-1].strip() if stripped.startswith("（") and stripped.endswith("）") else stripped
        if stripped.startswith("（") and stripped.endswith("）") or any(cue in inner for cue in setting_cues):
            # Parenthetical authorial guidance is not character action unless it also has action cues below.
            kind = "OMNISCIENT_SETTING"
            reason = "括号/设定提示/长期剧情规则，必须作为权威设定或暗线保存"
            if any(cue in inner for cue in derived_edit_cues):
                kind = "DERIVED_CONTENT_EDIT"
                reason = "玩家要求修改既有 AI 回复/前文；必须写入 <derived_content_edits> 修正旧派生内容，不得只在新回复里演出"
            elif any(cue in inner for cue in important_character_cues):
                kind = "IMPORTANT_CHARACTER_DECLARATION"
                reason = "玩家手动指定重要角色；必须更新 character_orchestration.major 并为该角色开启 subagent/独立上下文"
            add(kind, para, reason)
        if any(cue in para for cue in synopsis_cues):
            add("SYNOPSIS", para, "玩家概述已发生或将发生的剧情，先按原顺序扩写/承认")
        if action_pattern.search(para):
            add("ACTION", para, "玩家当前行动，必须在处理完设定与梗概后解析其直接后果")

    if not components and text.strip():
        components.append({"type": "UNCLASSIFIED", "text": _short_context(text), "reason": "未命中启发式分类；仍以玩家原文为权威"})

    conflict_patterns = [
        "梦境破碎", "醒来", "梦醒", "现实", "时间却只过去", "只过去了一分钟",
        "不是梦境", "唯一提醒", "之前", "其实", "而是", "不需要立刻在剧情中体现",
        "长期剧情引导", "无法再被回忆", "化作泡影",
    ]
    conflict_cues = [cue for cue in conflict_patterns if cue in text]
    previous = {}
    if chat_log:
        last = chat_log[-1] or {}
        previous = {
            "index": last.get("index", "?"),
            "summary": _short_context(last.get("summary", ""), 220),
            "ai_excerpt": _short_context(re.sub(r"<[^>]+>", "", last.get("ai", "")), 260),
        }
    declared_major = []
    for m in re.finditer(r"(?:设定重要角色|重要角色|核心角色)[：:，,\s]*([一-鿿A-Za-z0-9_·]{1,20})", text):
        name = m.group(1).strip()
        if name and name not in declared_major:
            declared_major.append(name)

    return {"components": components, "conflict_cues": conflict_cues, "previous": previous, "declared_major_characters": declared_major}


def _keyword_score(keyword, text):
    """Score a keyword against text — mirrors match_worldbook.py logic.

    Returns integer score, 0 if no meaningful match.
    """
    if not keyword or not text:
        return 0
    if keyword in text:
        return 10
    if text in keyword:
        return 6
    # CJK character overlap (2+ shared chars)
    kw_chars = set(keyword)
    txt_chars = set(text)
    overlap = len(kw_chars & txt_chars)
    if overlap >= 2:
        return 3 + min(overlap, 5)
    return 0


def _input_matches(wb_index, user_text, ref_sections):
    """Scan user input against worldbook index keywords, return top-3 with full entry text."""
    scored = []
    for entry in wb_index:
        keyword = entry.get("keyword", "")
        score = _keyword_score(keyword, user_text)
        if score > 0:
            scored.append({**entry, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)

    lines = []
    if not scored:
        lines.append("  (no matches)")
        return lines

    for i, m in enumerate(scored[:3]):
        lines.append(f"\n  --- Input Match {i+1}: {m['keyword']} (score={m['score']}) ---")
        lines.append(f"  Title: {m['title']}")
        lines.append(f"  One-liner: {m['one_liner'][:100]}")
        full = grep_reference_section(ref_sections, m["section"].lstrip("#").strip())
        if full:
            lines.append("  Full entry:")
            for fl in full.split("\n")[:100]:
                lines.append(f"    {fl}")
    return lines


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
    for name in major[: max(1, int(orchestration.get("max_parallel_subagents", 2) or 2))]:
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
            "scene_relevance": "high" if name == "_self" or name in user_text else "normal",
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
    write_progress("preparing", "正在整理回合上下文", percent=30)

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
    user_input = read_file(input_path) or "(无输入)"
    user_text = user_input.strip()

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
        if (
            latest_raw_text == current_user_text
            or latest_raw_text.strip() == current_user_text
            or latest_role_text == current_user_text
            or latest_role_text.strip() == current_user_text
        ):
            explicit_input_payload = dict(latest_player_input)
    input_plan = _analyze_player_input_for_plan(user_text, chat_log)
    if input_plan.get("declared_major_characters") and isinstance(card_data, dict):
        orchestration = card_data.setdefault("character_orchestration", {})
        major = orchestration.setdefault("major", [])
        changed_orchestration = False
        for name in input_plan.get("declared_major_characters", []):
            if name and name not in major:
                major.append(name)
                changed_orchestration = True
        orchestration.setdefault("minor_policy", "main_agent")
        orchestration.setdefault("max_parallel_subagents", 2)
        if changed_orchestration:
            try:
                with open(Path(card_folder) / ".card_data.json", "w", encoding="utf-8") as f:
                    json.dump(card_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # Load reference.md once for O(1) section lookups this round
    ref_sections = _load_reference_sections(card_folder)

    character_contexts = build_character_contexts(
        card_folder, card_data, card_structure or {}, chat_log, user_text
    )
    agent_run_info = None
    agent_run_error = None
    agent_workflow_advice = None
    try:
        agent_run_info = agent_packets.prepare_agent_run(
            card_folder=card_folder,
            user_text=user_text,
            chat_log=chat_log,
            card_data=card_data,
            character_contexts=character_contexts,
            turn_index=len(chat_log),
            input_payload=explicit_input_payload or None,
        )
        run_dir = agent_run_info.get("run_dir") if isinstance(agent_run_info, dict) else None
        if run_dir:
            agent_workflow_advice = agent_workflow.advise_next_actions(run_dir)
    except Exception as exc:
        agent_run_error = str(exc)

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
    dynamic_parts.append("- 不得擅自编辑、裁剪、合并或摘要玩家输入；response.txt 中的 <polished_input> 仅可作为内部解释，不可覆盖玩家原文。")
    dynamic_parts.append("- 若新玩家输入与既有 AI 叙事、角色资料、变量、记忆或世界设定冲突，以玩家新输入为准。")
    dynamic_parts.append("- 每轮都必须实时根据玩家最新输入评估过去剧情和设定；发现冲突时，可以小幅修改或完全重写 AI 派生数据。")
    dynamic_parts.append("- 冲突不是只在正文里解释一句；必须在本轮 <UpdateVariable> 中修正所有受影响的 AI 派生变量/角色状态/世界假设。")
    dynamic_parts.append("- 可修正或重写 AI 生成内容、摘要、memory、角色状态、变量和派生设定，使其服从玩家输入；不得把玩家新设定当作可选建议。")
    dynamic_parts.append("- 若玩家把上一轮 AI 推演改定为梦境、误会、预示、回忆、幻觉、演戏或已被覆盖的分支，本轮必须明确降级上一轮 AI 内容为对应性质，并阻止它继续作为现实事实污染后续变量。")

    dynamic_parts.append("\n=== PLAYER_INPUT_INTERPRETATION ===")
    dynamic_parts.append("- 先分类，后创作；不要把混合输入整体当作 ACTION 直接推进。")
    dynamic_parts.append("- SYNOPSIS: 玩家给出的剧情梗概/已发生经过，必须先承认并按玩家顺序扩写；不得跳过梗概直接续写下一幕。")
    dynamic_parts.append("- OMNISCIENT_SETTING: 玩家给出的括号设定、长期引导、世界规则、真相和代价，视为权威设定；不必一次性向角色揭示，但必须写入变量/记忆暗线。")
    dynamic_parts.append("- ACTION: 玩家当前行动只在处理完设定与梗概后推进；先给出该行动的直接后果，再引出新的变化。")
    dynamic_parts.append("- DERIVED_CONTENT_EDIT: 玩家要求修改第一段/首段/上一轮/前文/既有回复时，不是把指定内容写进最新回复；必须在 response.txt 写 <derived_content_edits> JSON，定点修正旧 AI 派生内容。")
    dynamic_parts.append("- IMPORTANT_CHARACTER_DECLARATION: 玩家手动指定重要角色时，必须写入角色变量/记忆，并将其加入 character_orchestration.major；本轮若 scene_relevance=high/normal，应调用该角色 subagent，除非运行环境无法调用。")
    dynamic_parts.append("- MIXED: 按 DERIVED_CONTENT_EDIT → IMPORTANT_CHARACTER_DECLARATION → OMNISCIENT_SETTING → SYNOPSIS → ACTION 的顺序逐项处理；response.txt 的 <polished_input> 必须简要列出本轮识别到的类型和修正动作。")

    dynamic_parts.append("\n=== PLAYER_INPUT_PROCESSING_PLAN (must follow before writing response.txt) ===")
    comps = input_plan.get("components", [])
    if comps:
        for idx, comp in enumerate(comps, 1):
            dynamic_parts.append(f"  {idx}. {comp.get('type')}: {comp.get('text')}")
            dynamic_parts.append(f"     why: {comp.get('reason')}")
    else:
        dynamic_parts.append("  (no classified components; still obey raw user text)")
    if input_plan.get("declared_major_characters"):
        dynamic_parts.append("  declared_major_characters: " + ", ".join(input_plan.get("declared_major_characters", [])))
        dynamic_parts.append("  required_orchestration: add these names to .card_data.json character_orchestration.major and use a role subagent when generating this turn.")
    if input_plan.get("conflict_cues"):
        dynamic_parts.append("  conflict_cues: " + ", ".join(input_plan.get("conflict_cues", [])[:12]))
        prev = input_plan.get("previous") or {}
        if prev:
            dynamic_parts.append(f"  prior_ai_to_reconcile: turn={prev.get('index')} summary={prev.get('summary')}")
            dynamic_parts.append("  required_repair: identify which prior AI-derived facts become dream/preview/false branch/obsolete; update variables so future turns follow the player's latest framing. Repair can be a small edit or a full rewrite of prior derived story/settings/memory, while preserving raw player inputs.")
    else:
        dynamic_parts.append("  conflict_cues: (none detected heuristically; still check manually)")

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

    dynamic_parts.append("\n=== INPUT_MATCHES ===")
    dynamic_parts.extend(_input_matches(wb_index, user_text, ref_sections))

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
        if agent_workflow_advice is not None:
            dynamic_parts.append("\n=== AGENT_WORKFLOW ===")
            dynamic_parts.append(json.dumps(agent_workflow_advice, ensure_ascii=False, indent=2))
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

    write_progress("generating", "Claude Code 正在生成回复", percent=60)

    print(json.dumps({
        "ok": True,
        "output": str(output_path),
        "character_contexts": str(character_contexts_path),
        "agent_run": agent_run_info.get("run_dir") if agent_run_info else None,
        "character_count": len(character_contexts.get("characters", [])),
        "size": len(output_text),
        "matches": len(match_result or []),
        "injections": len(injections),
        "is_first_turn": len(chat_log) == 0
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
