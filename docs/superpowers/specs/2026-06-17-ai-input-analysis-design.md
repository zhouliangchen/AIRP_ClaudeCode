# AI 输入语义解析重构设计

## 状态

本文定义下一轮针对玩家输入/用户指令解析的重构设计。目标是减少关键词匹配依赖，让 Claude Code 通过专门 subagent 完成灵活的语义分析，同时保留当前项目的文件协议、可测试性和“玩家原文权威”原则。

## 问题

当前输入解析分散在 `round_prepare.py`、`hidden_settings.py`、`agent_packets.py` 和 `rp-input-router` prompt 中，主要依靠关键词、正则和固定写法判断玩家意图。玩家实际输入可能混合第一人称行动、剧情梗概、第三人称设定、隐藏长期引导和历史修正请求，写法不稳定；继续堆叠关键词会导致漏判、误判和上下文泄漏。

## 目标

- 显式双通道优先：前端分别提交“角色输入”和“用户指令”时，通道归属是最高优先级。
- 支持混合输入：玩家把行动、梗概、设定和指令混写时，由 AI 语义拆分。
- 玩家原文不可变：AI 只能解释、分类和生成派生控制数据，不得改写、精简或替换玩家输入。
- 保持 Claude Code 直接驱动：语义解析由 Claude Code 调度的 input analyst subagent 完成，不改造成普通后端直接调用 LLM API。
- 保持 deterministic 测试能力：无 live model 测试可使用固定 fixture 或严格 fallback。

## 非目标

- 不在本轮重写前端输入控件。
- 不让 Python server 直接持有模型密钥或调用模型 API。
- 不取消现有显式标签、JSON、文件协议和测试 smoke。
- 不允许 AI 解析结果覆盖 `.player_inputs.jsonl` 中的原始输入。

## 推荐架构

新增 `input analyst` 阶段，位于正式 GM/player/character/story/critic 编排之前。

1. `round_prepare` 先收集原始输入、显式通道、历史摘要和当前文件状态，写入 `.agent_runs/<round>/input.raw.json` 与 `input_analysis.request.md`。
2. Claude Code 主 agent 调用 input analyst subagent，读取请求文件并写入 `.agent_runs/<round>/input_analysis.output.json`。
3. `round_prepare` 的 apply 阶段，或独立的 `input_analysis_apply.py`，校验并消费该 JSON。
4. apply 阶段更新隐藏设定、重要角色声明、冲突修正计划、可见性边界和 agent routing。
5. 后续 GM、player、character、story、critic agents 只消费结构化控制面数据，不再各自猜测玩家意图。

关键词和正则只保留三类用途：显式格式解析、无模型测试 fixture 生成、AI 解析失败后的受限 fallback。

## 数据契约

`input_analysis.output.json` 使用稳定 schema，建议字段如下：

```json
{
  "schema_version": 1,
  "round_id": "round-000123",
  "source_integrity": {
    "player_input_sha256": "...",
    "user_instruction_sha256": "...",
    "raw_preserved": true
  },
  "semantic_units": [
    {
      "id": "u1",
      "source_channel": "role_input",
      "type": "action | synopsis | omniscient_setting | hidden_setting | character_declaration | edit_request | system_command | style_guidance | unclear",
      "raw_excerpt": "玩家原文片段",
      "derived_summary": "非权威摘要",
      "confidence": 0.0,
      "visibility": "gm_only | public_world | player_pov | character_pov | specific_characters"
    }
  ],
  "world_updates": {
    "hidden_facts": [],
    "public_facts": [],
    "important_characters": [],
    "retcon_requests": []
  },
  "narrative_directives": {
    "rewrite_previous_output": false,
    "expand_synopsis_before_continue": false,
    "continue_after_player_action": true,
    "must_stop_for_player_decision": false
  },
  "routing": {
    "gm": true,
    "player": true,
    "characters": []
  },
  "risks": []
}
```

`raw_excerpt` 和 hash 只用于追溯。所有权威内容仍以原始输入文件为准；`derived_summary` 只能作为 prompt 辅助，不得回写替代原文。

## 失败处理

live RP 流程中，如果 `input_analysis.output.json` 缺失、JSON 不合法、hash 不匹配或 schema 不通过，主 agent 应重试 input analyst 一次。仍失败时，写入 progress/error，不进入正式创作，避免静默误判。

自动测试和 control-plane smoke 可使用固定 `input_analysis.output.json` fixture。若必须 fallback，只允许生成低置信度的保守分析，并标记 `analysis_mode: "fallback"`，不得执行高风险操作，例如历史重写、隐藏设定持久化或重要角色升级。

## Prompt 与 Skill 调整

新增 `.claude/skills/rp-input-analyst.md`，明确其职责：

- 识别玩家输入中的行动、剧情梗概、上帝视角设定、隐藏长期引导、历史修正、重要角色声明和直接系统指令。
- 对每个语义单元标注来源通道、可见性、置信度和是否需要持久化。
- 不改写玩家原文，不代表角色创作，不推进剧情。
- 输出严格 JSON，禁止夹带正文。

现有 `rp-input-router.md` 应降级为通道协议说明，不再要求通过固定关键词判断意图。

## 测试策略

新增 focused `unittest` 覆盖：

- 显式双通道优先于 AI 推断。
- 混合输入 fixture 被拆分为行动、梗概、隐藏设定和重要角色声明。
- hash 不匹配时拒绝 apply。
- fallback 模式不允许执行高风险持久化。
- 重要角色声明通过 AI 分析产物进入 `character_orchestration.major`。
- 隐藏设定只进入 GM 可见数据，不泄漏到 player/character packets。
- 历史修正请求生成 retcon plan，但不修改玩家原文。

手动验收应至少覆盖无素材开局、继续游戏、大段设定输入、重要角色 subagent 对话、历史修正和移动端双通道缺省情况下的混合输入。

## 推出顺序

1. 定义 input analysis schema、validator 和 fixture。
2. 新增 input analyst skill/prompt。
3. 将 `/rp` 工作流拆为 raw prepare、input analysis、apply analysis、agent orchestration。
4. 把 `hidden_settings`、重要角色抽取、输入路由迁移为消费 analysis artifact。
5. 保留旧启发式作为测试/异常 fallback，并在日志中明确标记。
6. 扩展 smoke 与真实游玩测试，确认复杂输入不再依赖关键词写法。
