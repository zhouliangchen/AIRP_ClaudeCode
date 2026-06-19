#!/usr/bin/env python3
"""
round_deliver.py — 回合后处理管线。

处理 AI 已写入 response.txt 之后的所有机械步骤：
质检 → handler 交付 → 记忆更新 → 故事规划检查。

用法:
  python round_deliver.py <card_folder> <ROOT>
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import agent_memory
import agent_outputs
import agent_run
from handler import write_progress
from io_utils import read_file, read_json


def _extract_tag(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _has_processing_evidence(kind, probe):
    if kind in probe:
        return True
    text = str(probe or "")
    semantic_terms = {
        "ACTION": ["action", "attempt", "attempted", "direct consequence", "尝试", "行动", "丢弃", "扔掉", "直接后果"],
        "SYNOPSIS": ["synopsis", "dream", "expanded", "梦境", "梦醒", "梦境残留", "梗概", "扩写", "预兆"],
        "OMNISCIENT_SETTING": ["omniscient", "hidden", "future setting", "长期", "暗线", "隐藏", "不向角色", "显性揭露", "真实用途", "代价"],
        "DERIVED_CONTENT_EDIT": ["derived_content_edits", "prior", "repair", "rewrite", "上一轮", "前文", "修正", "重写", "降级"],
        "IMPORTANT_CHARACTER_DECLARATION": ["important character", "character_dialogues", "核心角色", "重要角色", "subagent"],
    }
    return any(term in text for term in semantic_terms.get(kind, []))


def _derived_edits_actionable(raw):
    try:
        edits = json.loads(raw)
    except Exception:
        return False
    if not isinstance(edits, list):
        return False
    action_keys = {"ai", "content", "new_ai", "first_paragraph", "new_first_paragraph", "summary"}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        if "turn_index" not in edit:
            continue
        if any(isinstance(edit.get(key), str) and edit.get(key).strip() for key in action_keys):
            return True
    return False


def validate_player_processing(response_text, round_context):
    """Guardrail for mixed player inputs and conflict repairs.

    This is intentionally heuristic: it catches the common failure mode where the
    model notices a player correction in prose but does not persist the repaired
    facts into MVU state for future turns.
    """
    warnings = []
    ctx = round_context or ""
    polished = _extract_tag(response_text, "polished_input")
    update_block = _extract_tag(response_text, "UpdateVariable")
    patch_block = _extract_tag(response_text, "JSONPatch")
    derived_edits = _extract_tag(response_text, "derived_content_edits")
    summary = _extract_tag(response_text, "summary")
    response_probe = "\n".join([polished, update_block, patch_block, derived_edits, summary])

    classified = re.findall(r"^\s*\d+\.\s+(OMNISCIENT_SETTING|SYNOPSIS|ACTION|UNCLASSIFIED|DERIVED_CONTENT_EDIT|IMPORTANT_CHARACTER_DECLARATION):", ctx, flags=re.MULTILINE)
    if len(set(classified)) >= 2:
        missing = [kind for kind in sorted(set(classified)) if not _has_processing_evidence(kind, response_probe)]
        if missing:
            warnings.append("Mixed input handling evidence must explicitly list: " + ", ".join(missing))

    if "conflict_cues: (none detected" not in ctx and "required_repair:" in ctx:
        repair_terms = ["修正", "覆盖", "降级", "梦", "预示", "现实", "分支", "派生", "上一轮"]
        if not any(term in response_probe for term in repair_terms):
            warnings.append("Detected conflict cues, but response does not explicitly describe repair/reframing in polished_input/summary/UpdateVariable.")
        if not patch_block:
            warnings.append("Detected conflict cues, but no <JSONPatch> was written to persist repaired derived state.")
        patch_terms = ["梦", "预示", "现实", "覆盖", "分支", "核心异常", "长期", "规则", "吊坠"]
        if patch_block and not any(term in patch_block for term in patch_terms):
            warnings.append("JSONPatch exists but does not appear to persist the player's reframing/long-term setting.")
        if "prior_ai_to_reconcile:" in ctx and not derived_edits:
            warnings.append("Detected required repair of prior AI-derived content, but response lacks <derived_content_edits>; rewrite or reframe the affected earlier turn without touching player inputs.")
        elif "prior_ai_to_reconcile:" in ctx and not _derived_edits_actionable(derived_edits):
            warnings.append("Detected required repair of prior AI-derived content, but <derived_content_edits> is not actionable by handler.py; include turn_index plus ai/content/new_ai/first_paragraph/summary.")

    if "DERIVED_CONTENT_EDIT" in classified and "<derived_content_edits>" not in response_text:
        warnings.append("Player requested editing existing AI-derived content, but response lacks <derived_content_edits>; do not merely place the requested scene in the latest reply.")

    edit_only = _extract_tag(response_text, "edit_only")
    if edit_only and "<derived_content_edits>" in response_text:
        return warnings

    if "IMPORTANT_CHARACTER_DECLARATION" in classified:
        if "source=\"subagent\"" not in response_text and '"source":"subagent"' not in response_text and '"source": "subagent"' not in response_text:
            warnings.append("Player declared an important character, but no subagent-sourced <character_dialogues> entry was provided.")

    if "OMNISCIENT_SETTING" in classified and patch_block:
        setting_terms = ["长期", "规则", "代价", "真相", "暗线", "吊坠", "变身", "黑暗", "魔力"]
        if not any(term in patch_block for term in setting_terms):
            warnings.append("Omniscient setting detected, but JSONPatch does not appear to store it as an ongoing rule/hidden truth.")

    return warnings


def count_chinese(text):
    """Count Chinese characters in text, stripping HTML."""
    clean = re.sub(r"<[^>]+>", "", text)
    count = 0
    for ch in clean:
        cp = ord(ch)
        if 0x4e00 <= cp <= 0x9fff or 0x3400 <= cp <= 0x4dbf or 0x20000 <= cp <= 0x2a6df:
            count += 1
    return count


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "Usage: round_deliver.py <card_folder> <ROOT>"}))
        sys.exit(1)

    card_folder = sys.argv[1]
    root = sys.argv[2]
    styles_dir = Path(root) / "skills" / "styles"
    response_path = styles_dir / "response.txt"
    write_progress("delivering", "正在质检回复", percent=75)

    delivery_gate = agent_outputs.prepare_delivery(card_folder, styles_dir)
    if not delivery_gate.get("ok", False):
        detail = str(delivery_gate.get("detail") or delivery_gate.get("message") or "")[:500]
        gate_action = str(delivery_gate.get("action") or "retry")
        progress_status = "blocked" if gate_action == "blocked" else "retry"
        progress_message = "多代理产物已终止，等待人工处理" if progress_status == "blocked" else "多代理产物未就绪，等待修复"
        write_progress(progress_status, progress_message, percent=65, detail=detail)
        print(json.dumps(delivery_gate, ensure_ascii=False))
        sys.exit(0)
    if delivery_gate.get("mode") == "already_delivered":
        write_progress("done", "已交付，无需重复处理", percent=100)
        print(json.dumps({
            "action": "already_done",
            "agent_delivery": delivery_gate,
        }, ensure_ascii=False))
        sys.exit(0)

    if not response_path.exists():
        write_progress("error", "未找到 response.txt", percent=0)
        print(json.dumps({"ok": False, "error": "response.txt not found"}))
        sys.exit(1)

    response_text = read_file(response_path)
    if not response_text:
        write_progress("error", "response.txt 为空", percent=0)
        print(json.dumps({"ok": False, "error": "response.txt is empty"}))
        sys.exit(1)

    critic_report = agent_run.read_current_critic_report(card_folder)
    critic_hard_failures = []
    if isinstance(critic_report, dict):
        raw_hard_failures = critic_report.get("hard_failures")
        if isinstance(raw_hard_failures, list):
            critic_hard_failures = raw_hard_failures
        if critic_report.get("passed") is False and critic_hard_failures:
            write_progress("retry", "质检未通过，等待修复", percent=65, detail="; ".join(map(str, critic_hard_failures))[:500])
            print(json.dumps({
                "action": "retry",
                "reason": "critic_hard_failures",
                "critic_report": critic_report,
                "hint": "根据 critic.report.json 修复 story 输出后重新写入 response.txt。"
            }, ensure_ascii=False))
            sys.exit(0)

    # ── 1. Word Count Check ──
    settings = read_json(styles_dir / "settings.json") or {}
    word_count_target = settings.get("wordCount", 2000)
    threshold = int(word_count_target * 0.8)

    content_match = re.search(r"<content>(.*?)</content>", response_text, re.DOTALL)
    content_text = content_match.group(1) if content_match else response_text
    chinese_count = count_chinese(content_text)
    edit_only = _extract_tag(response_text, "edit_only") and "<derived_content_edits>" in response_text

    # ── 2. Token Collection (checkpoint-based delta) ──
    import token_stats

    transcript_path = token_stats.locate_transcript()
    cp = token_stats.load_checkpoint(card_folder) if transcript_path else {}
    byte_offset = cp.get("last_byte_offset", 0)

    # Compute startup cost on first round (previous_checkpoint signals new session)
    startup_delta = None
    if cp.get("previous_checkpoint"):
        startup_delta = token_stats.compute_startup_cost(card_folder)
        # Reload checkpoint — compute_startup_cost wrote startup_cost into it
        cp = token_stats.load_checkpoint(card_folder)
        byte_offset = cp.get("last_byte_offset", 0)

    # Read all usage since last checkpoint
    usage_entries = token_stats.read_usage_since(transcript_path, byte_offset) if transcript_path else []
    delta = token_stats.compute_delta(usage_entries)
    cumulative = cp.get("cumulative", {"input_tokens": 0, "output_tokens": 0})

    # Retrieve startup cost from checkpoint (set by handler.py --opening or compute_startup_cost)
    startup_cost = cp.get("startup_cost")
    st_in = startup_cost.get("input_tokens", 0) if startup_cost else 0
    st_out = startup_cost.get("output_tokens", 0) if startup_cost else 0

    token_data = {
        "in": delta["input_tokens"],
        "out": delta["output_tokens"],
        "total": delta["input_tokens"] + delta["output_tokens"],
        "cache_read": delta["cache_read"],
        "cache_hit_pct": delta["cache_hit_pct"],
        "startup_in": st_in,
        "startup_out": st_out,
        "startup_total": st_in + st_out,
        "cumulative_in": cumulative.get("input_tokens", 0) + delta["input_tokens"],
        "cumulative_out": cumulative.get("output_tokens", 0) + delta["output_tokens"],
        "cumulative_total": (cumulative.get("input_tokens", 0) + cumulative.get("output_tokens", 0) +
                             delta["input_tokens"] + delta["output_tokens"]),
    }

    # ── 3. Quality Gate ──
    ratio = chinese_count / word_count_target if word_count_target > 0 else 1.0
    round_context = read_file(styles_dir / "round_context.txt") or ""
    processing_warnings = validate_player_processing(response_text, round_context)

    if processing_warnings:
        write_progress("retry", "回复未按玩家输入权威规则修正，等待重写", percent=65)
        print(json.dumps({
            "action": "retry",
            "reason": "player_input_processing",
            "warnings": processing_warnings,
            "word_count": {"current": chinese_count, "target": word_count_target, "threshold": threshold, "ratio": round(ratio, 2)},
            "tokens": token_data,
            "hint": "必须先按 PLAYER_INPUT_PROCESSING_PLAN 分类处理：冲突需修正派生设定/变量，梗概需先承认并扩写，上帝视角设定需存入暗线变量，最后才推进玩家行动。"
        }, ensure_ascii=False))
        sys.exit(0)

    if not edit_only and chinese_count < threshold:
        # Word count failed — signal retry (do NOT save checkpoint)
        write_progress("retry", "回复未达字数要求，等待重写", percent=65)
        print(json.dumps({
            "action": "retry",
            "word_count": {"current": chinese_count, "target": word_count_target, "threshold": threshold, "ratio": round(ratio, 2)},
            "tokens": token_data,
            "hint": f"当前 {chinese_count} 字，目标 {word_count_target} 字（最低 {threshold} 字）。请扩充感官细节、NPC 微反应、环境变化。禁止灌水重复。"
        }, ensure_ascii=False))
        sys.exit(0)

    # Append token block to response.txt BEFORE handler reads it.
    # Always append (even with delta=0) so cumulative/startup stats are visible.
    if "<tokens>" not in response_text:
        token_block = token_stats.format_token_block(delta, cumulative, startup_cost=startup_cost)
        with open(response_path, "a", encoding="utf-8") as f:
            f.write("\n" + token_block)

    # ── 4. Deliver to Frontend ──
    write_progress("delivering", "正在交付到前端", percent=85)
    handler_ok = False
    try:
        result = subprocess.run(
            [sys.executable, str(Path(root) / "skills" / "handler.py"), card_folder],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        handler_ok = result.returncode == 0
        handler_output = result.stdout.strip()
    except Exception as e:
        handler_output = str(e)

    if not handler_ok:
        write_progress("error", "前端交付失败", percent=0, detail=handler_output[:500])
        print(json.dumps({
            "ok": False,
            "error": "handler.py failed",
            "detail": handler_output[:500]
        }, ensure_ascii=False))
        sys.exit(1)

    if edit_only:
        token_stats.save_checkpoint(card_folder, delta=delta, label="round")
        state_js = read_file(styles_dir / "state.js")
        generated_count = 0
        if state_js:
            m = re.search(r"generatedCount:\s*(\d+)", state_js)
            if m:
                generated_count = int(m.group(1))
        write_progress("complete", "派生内容已重写", percent=100)
        print(json.dumps({
            "action": "done",
            "edit_only": True,
            "generatedCount": generated_count,
            "story_plan_due": False,
            "word_count": {"current": chinese_count, "target": word_count_target, "ratio": round(ratio, 2)},
            "tokens": token_data,
            "memory_updated": False,
            "summary": "已按玩家指令重写前文 AI 派生章节"
        }, ensure_ascii=False))
        sys.exit(0)

    # Save checkpoint only after successful delivery
    token_stats.save_checkpoint(card_folder, delta=delta, label="round")

    # ── 5. Memory Update ──
    write_progress("finalizing", "正在更新记忆", percent=95)
    memory_ok = False
    try:
        result = subprocess.run(
            [sys.executable, str(Path(root) / "skills" / "write_memory.py"), card_folder],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        memory_ok = result.returncode == 0
    except Exception:
        pass

    agent_memory_ok = False
    agent_memory_error = ""
    try:
        current_run = agent_run.current_run_dir(card_folder)
        if current_run is not None and (current_run / "story.input.json").exists():
            delta_result = agent_memory.ingest_memory_deltas(card_folder, current_run)
            summary_result = agent_memory.ingest_memory_summaries(card_folder, current_run)
            agent_memory_ok = bool(delta_result.get("ok") and summary_result.get("ok"))
    except Exception as exc:
        agent_memory_error = str(exc)

    # ── 6. Story Planning Check ──
    state_js = read_file(styles_dir / "state.js")
    generated_count = 0
    if state_js:
        m = re.search(r"generatedCount:\s*(\d+)", state_js)
        if m:
            generated_count = int(m.group(1))

    plan_interval = 8  # default
    story_plan_due = generated_count > 0 and generated_count % plan_interval == 0

    # ── 7. Summary ──
    summary_text = ""
    summary_match = re.search(r"<summary>(.*?)</summary>", response_text, re.DOTALL)
    if summary_match:
        summary_text = re.sub(r"<[^>]+>", "", summary_match.group(1)).strip()[:200]

    agent_delivery = agent_outputs.mark_delivered(card_folder)
    write_progress("complete", "回复已完成", percent=100)

    print(json.dumps({
        "action": "done",
        "generatedCount": generated_count,
        "story_plan_due": story_plan_due,
        "word_count": {"current": chinese_count, "target": word_count_target, "ratio": round(ratio, 2)},
        "tokens": token_data,
        "memory_updated": memory_ok,
        "agent_memory_updated": agent_memory_ok,
        "agent_memory_error": agent_memory_error,
        "agent_delivery": agent_delivery,
        "summary": summary_text
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
