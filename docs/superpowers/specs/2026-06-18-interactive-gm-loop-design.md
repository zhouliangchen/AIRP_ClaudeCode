# 交互式 GM 回路重构设计

## 状态

本文记录 2026-06-18 确认的下一轮重构设计。目标是在保留 Claude Code 直驱和现有文件邮箱模式的前提下，把当前“一次性生成 GM/player/character 产物”的流程，重构为更接近跑团的“GM 推进、重要角色多次交互、Story 统一整理、Critic 严格审查”的回合内交互回路。

本文只定义设计范围；具体实现需在后续 implementation plan 中拆分任务。

## 问题

当前实现存在两个核心缺口：

- 角色上下文隔离不足：`player.context.json` 和 `characters/*.context.json` 仍能直接接触玩家游玩文本、近期完整剧情或过度压缩的全局摘要，没有经过严格第一人称视角过滤。
- 角色交互模型过浅：player agent 和 character agent 都像玩家本人一样每轮只输出一次行动，不能在同一回合内多次感知、对话、行动、等待 GM 反馈，也不能自然处理角色间连续互动。

这会削弱沉浸感，让“重要角色 subagent”退化为一次性台词候选，而不是独立活在世界中的角色。

## 目标

- 让 GM 成为每轮剧情推进和角色唤起的核心调度者。
- 让 player/character agent 只接收角色视角下可知、可感知、可记忆的信息。
- 允许重要角色 agent 在同一输出回合内被多次唤起并多次行动。
- 支持角色向 GM 请求感知反馈、向其他角色说话、执行行为、写入记忆、更新目标。
- 让 Story agent 基于完整交互轨迹整理高质量正文，而不是替角色补写关键行动。
- 让 Critic agent 审查上下文隔离、角色能动性、玩家输入权威和决策点停止。
- 保留现有前端、`response.txt` 交付、`chat_log.json`、`.player_inputs.jsonl` 和 `.agent_runs/` 文件邮箱思路。

## 非目标

- 本轮不改造成普通后端直接调用模型 API；Claude Code 仍负责 agent 编排。
- 本轮不重写前端交互布局。
- 本轮不完整实现 GM 助手 agent runner。
- 本轮不为旧格式 `.agent_runs/` 或旧生成存档保留兼容层。项目仍处于开发期，可以升级当前运行协议以保持逻辑简洁。

## 推荐架构

新增“交互式 GM 回路”作为正式创作阶段：

1. `round_prepare.py` 仍负责收集原始输入、历史、设置、输入分析请求和基础运行目录。
2. `input analyst` 继续负责把玩家输入和用户指令解析为结构化控制面数据。
3. `preprocess` 阶段根据输入分析结果，最小化更新角色状态、重要角色注册、隐藏设定、冲突修正计划和可见性边界。
4. `GM agent` 读取完整剧情、用户指令、隐藏事实、变量、世界书和所有角色状态，生成下一段场景推进计划。
5. GM 每次判断某个重要角色参与当前事件时，生成该角色专属的第二人称场景转述，并通过视角投影创建一次 actor call。
6. actor agent 以角色自身第一人称作出事件流响应。响应可包含感知请求、行动、对白、记忆写入、目标更新、等待反馈和停止原因。
7. GM 处理 actor 响应：给出感知反馈、转达对白、判定行为后果、追加世界事件，并继续推进。
8. 循环直到达到字数/场景目标，或遇到必须由真实玩家决定的关键行动。
9. `Story agent` 读取交互轨迹、GM 叙事片段、角色事件流和玩家权威输入，整理成最终小说正文。
10. `Critic agent` 审查候选结果。未通过时进入受限修复循环；通过后仍由 `round_deliver.py` 交付。

主 agent 只负责编排、脚本运行、agent 调度、artifact 收集、修复循环和系统迭代，不直接写常规叙事正文。

## 视角投影

新增或重构 `agent_projection.py`，把完整世界状态投影为角色可见信息。投影结果必须包含：

- `actor_id`：`player` 或 `character:<safe_name>`。
- `address_mode`：GM 对该角色使用的第二人称转述。
- `self_knowledge`：角色对自己身份、身体、处境、关系的认知。
- `memory`：该角色第一视角下的长期记忆、近期记忆和当前目标。
- `sensory_context`：该角色此刻能看见、听见、触碰、闻到、感到或合理推断的信息。
- `visible_events`：当前角色实际感知到的其他角色行动和对白。
- `misconceptions`：角色已有误解，必须保留，不得被全知事实纠正。
- `forbidden_removed`：已剔除的信息类别，用于审计。

角色投影不得包含 `user_instruction_channel`、GM 隐藏计划、世界真相、其他角色内心、Claude Code 工作流、玩家现实身份或文件系统信息，除非这些内容已经在剧情世界中被该角色感知。

## Actor 事件协议

player/character 输出从单个 `action` 升级为事件列表。建议 schema：

```json
{
  "agent": "player",
  "agent_id": "player",
  "events": [
    {
      "type": "perceive_request",
      "target": "classroom_window",
      "content": "我看向窗外，确认粉色云是否还在。"
    },
    {
      "type": "dialogue",
      "target": "character:SuLi",
      "content": "你知道这个吊坠是什么吗？"
    },
    {
      "type": "action",
      "target": "",
      "content": "我把吊坠攥紧，尽量不让同桌看见。"
    },
    {
      "type": "memory_delta",
      "target": "self",
      "content": "我记住了苏黎听见吊坠时一瞬间的动摇。"
    },
    {
      "type": "goal_update",
      "target": "self",
      "content": "弄清楚吊坠和今早梦境的关系。"
    }
  ],
  "stop_reason": "continue"
}
```

允许的 `type` 至少包括 `perceive_request`、`dialogue`、`action`、`memory_delta`、`goal_update`、`wait_for_gm`、`stop_for_player_decision`。角色不能替真实玩家做关键决策；player agent 只可补足低风险动作、反应和过渡。

## GM 事件协议

GM 输出不再只是一次性 `narration`，而应包含回合状态和下一步调度建议：

```json
{
  "agent": "gm",
  "scene_beats": [],
  "events": [],
  "actor_calls": [],
  "parallel_groups": [],
  "world_state_delta": [],
  "decision_point": null,
  "stop_reason": "continue"
}
```

- `scene_beats` 保存 GM 已推进的可见叙事片段。
- `events` 保存 GM 判定的世界事件、NPC 行为、感知反馈和转达消息。
- `actor_calls` 指定需要唤起的重要角色，以及给该角色的第二人称场景转述。
- `parallel_groups` 标记可并行唤起的一组 actor；若行动互相影响，必须串行。
- `decision_point` 只在需要真实玩家选择时出现。

GM 负责处理 `perceive_request` 和 `dialogue`。当角色对白目标是重要角色时，GM 必须把可感知的对白内容转成目标角色的新 actor call；不得由 GM 或 Story agent 编造该重要角色的核心回应。

## 交互轨迹

扩展 `interaction.trace.json` 为权威交互日志。每条事件应记录：

- `index`
- `phase`
- `actor`
- `visibility`
- `type`
- `content`
- `target`
- `source_call_id`
- `causal_links`

Story agent 只能直接使用 `world_visible` 和 `actor_visible` 中适合公开的内容；私有感知、隐藏事实和内部调度只能作为整理参考，不得泄露到正文。

## GM 助手 agent 预留

本轮不完整实现 GM 助手 runner，但协议预留多实例能力：

- GM 可在未来创建多个 `gm_assistant:<thread_id>`。
- 每个助手负责一个并行支线，拥有自己的 `side_threads/<thread_id>/interaction.trace.json`。
- 多个支线可并行推进，用于模拟与玩家角色无关或弱相关的世界事件。
- 支线可唤起重要角色，但必须通过主 GM 仲裁时间、地点和身体唯一性冲突。
- 支线结果持久化后进入 GM 后续参考和 Story 可选素材；默认不抢占主线篇幅。

该能力进入下一轮实现，除非主线交互回路已稳定通过真实游玩测试。

## Prompt 与文档语言

本轮应同步清理 `.claude/skills/*.md` 和 `CLAUDE.md` 的语言。按照项目级记忆：

- `README.md` 和 `docs/` 下技术文档使用简体中文。
- `AGENTS.md`、`CLAUDE.md`、`.claude/skills/*.md` 使用地道英语。
- UI 文案、测试数据、运行时剧情文本可按既有需求保留中文。

这项清理不是审美问题，而是降低 prompt 污染和执行歧义的工程要求。

## 测试策略

需要新增 deterministic 测试，避免依赖 live model：

- 角色投影不包含完整 `recent_chat`、`user_instruction_channel`、隐藏世界真相或其他角色私有信息。
- actor schema 支持多事件输出，并拒绝旧的单 `action` 作为唯一协议。
- GM loop 可串行唤起同一角色多次。
- GM loop 可并行唤起互不干扰的多个角色，并在 trace 中保留并行组。
- 角色的 `perceive_request` 会触发 GM 感知反馈。
- 角色对白目标为重要角色时，会生成目标角色 actor call。
- Story input 由交互轨迹组装，而不是直接读取完整隐藏上下文。
- Critic 能识别角色上下文泄漏、重要角色核心回应未由对应 agent 产生、player agent 替玩家做关键决策。
- `control_plane_smoke.py` 覆盖至少一个多次唤起、一次对白转达和一次决策点停止。

真实验收应至少覆盖无素材开局、继续游戏、一个玩家角色加一个重要角色、同一重要角色被连续唤起、第二人称正文输出、重要角色对白框和关键决策点停止。

## 推出顺序

1. 定义 actor event schema、GM event schema 和 trace v2。
2. 实现角色视角投影，移除 player/character 对完整玩家文本和完整历史的直接依赖。
3. 改造 prompt 生成，让 GM 负责 actor call，actor 只处理单次角色视角输入。
4. 改造 runner，从一次性 dispatch 变为有上限的 GM loop。
5. 改造 story input 组装逻辑，以 trace v2 和事件流为核心。
6. 更新 Story/Critic prompt，强化角色能动性、上下文隔离和决策点审查。
7. 更新 deterministic tests、control-plane smoke、README 和相关 docs。

## 风险与边界

- 交互回路可能增加延迟，因此必须限制每轮最大 actor call 数、最大 GM loop 步数和最大修复次数。
- player agent 的自动行动必须保守，不能代替真实玩家完成不可逆选择。
- 角色并行只适用于互不影响的行动；任何会互相观察、回应或改变同一现场状态的事件都必须串行。
- Story agent 可润色、排序和补足过渡，但不得发明重要角色的核心对白或关键行动。
- 若 Critic 发现上下文泄漏或角色回应来源不合法，应硬失败并要求重新生成对应阶段。
