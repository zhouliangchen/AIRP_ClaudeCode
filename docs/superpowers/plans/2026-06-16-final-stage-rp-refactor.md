# 最终阶段 RP 重构记录

## 状态

本阶段已完成。当前实现保持 Claude Code 直驱原则：Python 只负责本地文件协议、浏览器桥接和确定性校验，实际叙事编排、subagent 调度、修复循环和最终质检仍由 Claude Code 会话驱动。

本文档记录当前已落地的最终契约。旧的实现草案、过时输出文件名和中间代码片段不再作为参考。

## 当前回合文件契约

每轮在当前卡片/故事文件夹下创建 `.agent_runs/<round>/` 文件邮箱，并通过 `.agent_runs/current` 指向当前轮次。

当前主要产物：

- `input.raw.json`：玩家原始输入、通道文本和完整性信息。
- `input_analysis.output.json`：输入分析 subagent 的结构化结果。
- `input.json`：已路由的 `role_channel` 与 `user_instruction_channel`。
- `gm.context.json`、`player.context.json`、`characters/*.context.json`：各 agent 的上下文包。
- `prompts/*.prompt.md`：本轮物化后的 prompt。
- `gm.output.json`：GM 交互循环产物，外层为 `gm_loop`。
- `actor.outputs.json`：player 与 important character 的统一 actor 产物集合。
- `interaction.trace.json`：交互循环的可见/私有事件轨迹。
- `story.input.json`：交给 story agent 的规范化创作输入。
- `story.output.json`：story agent 的最终候选正文。
- `critic.report.json`：critic agent 的交付审查报告。
- `repair_history.jsonl`：critic `revise` / `block` 的修复审计。
- `memory_summaries/*.summary.json`：按轮次计划生成的角色自我记忆摘要。

当前实现不再使用独立的 `player.output.json` 或 `characters/*.output.json` 作为正常回合契约。

## 当前工作流

1. `round_prepare.py` 收集上下文，创建 `.agent_runs/<round>/`，写入 prompt、manifest 和 `round_context.txt`。
2. `rp_generate_cli.py` 或 Claude Code 主 agent 先运行 `rp-input-analyst`，再由 `input_analysis_apply.py` 验证并应用分析结果。
3. GM loop 读取完整剧情状态，按需要唤起 player/character actor。player 与 character 只能读取第一人称投影上下文。
4. actor 产物统一写入 `actor.outputs.json`，GM 产物写入 `gm.output.json`，交互痕迹写入 `interaction.trace.json`。
5. `agent_outputs.py` 校验产物并生成 `story.input.json`。
6. story agent 写入 `story.output.json`，critic agent 写入 `critic.report.json`。
7. 无论 critic 决策为 `pass`、`revise` 还是 `block`，都必须运行 `round_deliver.py` 交付门禁。
8. `pass` 时，`round_deliver.py` 将 `story.output.json` 镜像到 `skills/styles/response.txt` 并调用 `handler.py`。
9. `revise` / `block` 时，门禁写入 `repair_history.jsonl`，必要时追加 `.agent_runs/improvement_queue.jsonl`，并返回 `retry` 或 `blocked`。

## Skill 与 Prompt 状态

`.claude/skills/rp*.md` 和 `CLAUDE.md` 已统一为英文 instruction 文件。当前 stage skills 包括：

- `rp`
- `rp-orchestrator`
- `rp-input-analyst`
- `rp-input-router`
- `rp-context-projector`
- `rp-gm-agent`
- `rp-player-agent`
- `rp-character-agent`
- `rp-story-agent`
- `rp-critic-agent`
- `rp-delivery`
- `rp-assets-ui`

`tests/test_turn_state.py` 会扫描 `CLAUDE.md`、`.claude/commands/rp.md` 和所有 `.claude/skills/rp*.md`，确保这些 instruction 文件只使用 ASCII 英文。

## 验证命令

最终阶段已使用以下命令验证：

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py
```

注意：在 Windows 上不要把 `py_compile` 与全量测试并行运行；它们可能同时写入 `skills/__pycache__`，导致临时 `.pyc` 文件重命名被拒绝。若遇到该问题，单独重跑 `py_compile` 即可确认语法状态。

## 手动验收

确定性验证通过后，仍需进行一次真实 Claude Code 游玩验收：

1. 在未纳入版本控制的空白卡片/故事文件夹中启动 `claude` 并运行 `/rp`。
2. 打开 `http://localhost:8765`，再用同一局域网设备打开启动输出中的 LAN URL。
3. 完成至少五轮玩家输入，覆盖角色通道和用户指令通道。
4. 检查玩家输入即时显示、重要角色独立对话框、进度更新、UI/图片热刷新、以及在真实玩家决策点停止。
