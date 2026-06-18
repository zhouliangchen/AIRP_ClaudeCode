# AI 输入分析重构状态

## 状态

已完成。本文取代早期英文实施计划，记录当前实现契约，避免继续引用旧的 `player.output.json`、`characters/*.output.json` 等废弃产物。

## 目标

输入解析不再依赖关键词匹配，而是由 Claude Code 调度 `rp-input-analyst` subagent 进行语义分析。Python 运行时只负责生成请求、校验分析产物、持久化安全更新并重建 agent packets；Claude Code 仍是唯一 live model 编排层。

## 当前文件

- `skills/input_analysis.py`：定义输入分析 schema、hash 校验、fallback 分析、路由转换和请求渲染。
- `skills/input_analysis_apply.py`：校验 `input_analysis.output.json`，应用隐藏设定、重要角色和 retcon 请求，并重建 `input.json`、上下文、prompt 与 manifest。
- `skills/character_registry.py`：集中管理重要角色持久化，避免散落在 `round_prepare.py` 中。
- `.claude/skills/rp-input-analyst.md`：语义输入分析 subagent 的输出契约。
- `skills/agent_packets.py`：写入 `input.raw.json`、`input_analysis.request.md`，并基于已验证分析生成上下文包。
- `skills/rp_generate_cli.py`：先调度 input analyst，再运行 apply 阶段，之后继续 GM、actor、story、critic 流程。
- `skills/control_plane_smoke.py`：提供无 live model 的确定性输入分析 fixture。
- `tests/test_input_analysis.py`、`tests/test_agent_packets.py`、`tests/test_rp_generate_cli.py`、`tests/test_turn_state.py`：覆盖 schema、包生成、CLI 调度和文档/指令文件约束。

## 当前流程

1. `round_prepare.py` 收集本轮原始输入，保留玩家原文不可改写，并写入 `.agent_runs/<round>/input.raw.json`。
2. 系统生成 `input_analysis.request.md` 和 `prompts/input_analyst.prompt.md`，manifest 等待 `input_analysis`。
3. `rp_generate_cli.py` 调度 `rp-input-analyst`，要求只写一个 `input_analysis.output.json` JSON 对象。
4. `input_analysis_apply.py` 校验 raw hash、channel、visibility、持久化请求和 retcon 请求。
5. apply 阶段重建 `input.json`、`gm.context.json`、`player.context.json`、`characters/*.context.json` 和后续 prompts。
6. GM 交互循环输出 `gm.output.json`，重要角色行动统一聚合到 `actor.outputs.json`，trace 写入 `interaction.trace.json`。
7. `agent_outputs.py` 校验 GM、actor 和 trace 的 `source_call_id` 关系，生成 `story.input.json`。
8. story/critic 完成后，由 `round_deliver.py` 统一交付或记录 retry/block。

## Manifest 契约

`manifest.json.expected_outputs` 当前只使用：

- `input_analysis`: `input_analysis.output.json`
- `gm`: `gm.output.json`
- `actors`: `actor.outputs.json`
- `story`: `story.output.json`
- `critic`: `critic.report.json`
- `memory_summaries`: 可选的 `memory_summaries/*.summary.json`

不得重新引入独立的 `player.output.json` 或 `characters/*.output.json`。player 和 character 的多次行动都必须通过 GM 交互循环和 `actor.outputs.json` 进入后续整理阶段。

## 验证

常用局部验证：

```powershell
python -m unittest tests.test_input_analysis tests.test_agent_packets tests.test_rp_generate_cli -v
```

完整验收仍以仓库根目录的最终验收清单为准：

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py
```
