#!/usr/bin/env python3
"""
round_deliver.py — 回合后处理管线。

处理 AI 已写入 response.txt 之后的所有机械步骤：
产物检查 → handler 交付 → 记忆更新 → 故事规划检查。

用法:
  python round_deliver.py <card_folder> <ROOT>
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import agent_lifecycle
import agent_memory
import agent_outputs
import agent_run
from handler import write_progress
from io_utils import read_file


def _write_progress_safe(stage, label, percent=None, detail=None):
    try:
        return write_progress(stage, label, percent=percent, detail=detail)
    except Exception:
        return None


def _extract_tag(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "Usage: round_deliver.py <card_folder> <ROOT>"}))
        sys.exit(1)

    card_folder = sys.argv[1]
    root = sys.argv[2]
    styles_dir = Path(root) / "skills" / "styles"
    response_path = styles_dir / "response.txt"
    _write_progress_safe("delivery.validating", "正在检查交付产物", percent=75)

    delivery_gate = agent_outputs.prepare_delivery(card_folder, styles_dir)
    if not delivery_gate.get("ok", False):
        gate_action = str(delivery_gate.get("action") or "retry")
        progress_status = "blocked" if gate_action == "blocked" else "delivery.retrying"
        progress_message = "多代理产物已终止，等待人工处理" if progress_status == "blocked" else "多代理产物未就绪，等待修复"
        _write_progress_safe(
            progress_status,
            progress_message,
            percent=65,
            detail={"reason": str(delivery_gate.get("reason") or "agent_outputs"), "action": gate_action},
        )
        print(json.dumps(delivery_gate, ensure_ascii=False))
        sys.exit(0)
    if delivery_gate.get("mode") == "already_delivered":
        _write_progress_safe("complete", "已交付，无需重复处理", percent=100)
        print(json.dumps({
            "action": "already_done",
            "agent_delivery": delivery_gate,
        }, ensure_ascii=False))
        sys.exit(0)

    if not response_path.exists():
        _write_progress_safe("error", "未找到 response.txt", percent=0)
        print(json.dumps({"ok": False, "error": "response.txt not found"}))
        sys.exit(1)

    response_text = read_file(response_path)
    if not response_text:
        _write_progress_safe("error", "response.txt 为空", percent=0)
        print(json.dumps({"ok": False, "error": "response.txt is empty"}))
        sys.exit(1)

    edit_only = _extract_tag(response_text, "edit_only") and "<derived_content_edits>" in response_text

    # ── 1. Token Collection (checkpoint-based delta) ──
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

    # Append token block to response.txt BEFORE handler reads it.
    # Always append (even with delta=0) so cumulative/startup stats are visible.
    if "<tokens>" not in response_text:
        token_block = token_stats.format_token_block(delta, cumulative, startup_cost=startup_cost)
        with open(response_path, "a", encoding="utf-8") as f:
            f.write("\n" + token_block)

    # ── 2. Deliver to Frontend ──
    _write_progress_safe("delivery.delivering", "正在交付到前端", percent=85)
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
        _write_progress_safe("delivery.failed", "前端交付失败", percent=0, detail=handler_output[:500])
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
        _write_progress_safe("complete", "派生内容已重写", percent=100)
        print(json.dumps({
            "action": "done",
            "edit_only": True,
            "generatedCount": generated_count,
            "story_plan_due": False,
            "tokens": token_data,
            "memory_updated": False,
            "summary": "已按玩家指令重写前文 AI 派生章节"
        }, ensure_ascii=False))
        sys.exit(0)

    # Save checkpoint only after successful delivery
    token_stats.save_checkpoint(card_folder, delta=delta, label="round")

    # ── 3. Memory Update ──
    _write_progress_safe("memory.finalizing", "正在更新记忆", percent=95)
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

    # ── 4. Story Planning Check ──
    state_js = read_file(styles_dir / "state.js")
    generated_count = 0
    if state_js:
        m = re.search(r"generatedCount:\s*(\d+)", state_js)
        if m:
            generated_count = int(m.group(1))

    plan_interval = 8  # default
    story_plan_due = generated_count > 0 and generated_count % plan_interval == 0

    # ── 5. Summary ──
    summary_text = ""
    summary_match = re.search(r"<summary>(.*?)</summary>", response_text, re.DOTALL)
    if summary_match:
        summary_text = re.sub(r"<[^>]+>", "", summary_match.group(1)).strip()[:200]

    agent_delivery = agent_outputs.mark_delivered(card_folder)
    post_round_memory = {
        "ok": True,
        "status": "not_required",
        "scheduled": [],
        "ingested": [],
        "missing": {},
        "failed": {},
    }
    if agent_delivery.get("ok", False):
        try:
            current_run = agent_run.current_run_dir(card_folder)
            if current_run is not None and (current_run / "story.input.json").exists():
                _write_progress_safe("memory.post_round_scheduling", "正在安排回合后记忆", percent=96)
                schedule_result = agent_memory.schedule_post_round_memory_jobs(card_folder, current_run)
                ingest_result = agent_memory.ingest_post_round_memory_jobs(card_folder, current_run)
                post_round_memory = {
                    "ok": bool(schedule_result.get("ok") and ingest_result.get("ok")),
                    "status": ingest_result.get("status")
                    or ("pending" if schedule_result.get("scheduled") else "not_required"),
                    "scheduled": schedule_result.get("scheduled", []),
                    "ingested": ingest_result.get("ingested", []),
                    "missing": ingest_result.get("missing", {}),
                    "failed": ingest_result.get("failed", {}),
                }
        except Exception as exc:
            post_round_memory = {
                "ok": False,
                "status": "error",
                "scheduled": [],
                "ingested": [],
                "missing": {},
                "failed": {},
                "error": str(exc),
            }

    lifecycle_cleanup = {"ok": True, "status": "not_required"}
    try:
        current_run = agent_run.current_run_dir(card_folder)
        if current_run is not None:
            _write_progress_safe("agent_lifecycle.cleanup", "正在关闭本轮代理活动", percent=98)
            lifecycle_cleanup = agent_lifecycle.cleanup_round_agents(
                card_folder,
                current_run,
                reason="delivered",
            )
    except Exception as exc:
        lifecycle_cleanup = {"ok": False, "status": "error", "error": str(exc)}
    _write_progress_safe("complete", "回复已完成", percent=100)

    print(json.dumps({
        "action": "done",
        "generatedCount": generated_count,
        "story_plan_due": story_plan_due,
        "tokens": token_data,
        "memory_updated": memory_ok,
        "agent_memory_updated": agent_memory_ok,
        "agent_memory_error": agent_memory_error,
        "agent_delivery": agent_delivery,
        "post_round_memory": post_round_memory,
        "agent_lifecycle_cleanup": lifecycle_cleanup,
        "summary": summary_text
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
