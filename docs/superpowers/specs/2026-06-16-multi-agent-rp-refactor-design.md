# 多 Subagent 角色扮演重构设计

## 状态

用户已于 2026-06-16 批准进入设计阶段。本文只定义第一期实现范围，不包含具体代码实现授权。

## 问题

当前项目已经能作为 Claude Code 直驱的本地 RP 引擎运行，但太多职责集中在 `CLAUDE.md` 和 Claude Code 主 agent 上。主 agent 同时负责整理上下文、写叙事、扮演角色、执行系统规则、触发工具和交付检查。这会削弱角色独立性，使核心角色 subagent 的使用不稳定，也让创作质量问题难以定位。

重构必须保留项目的核心优势：Claude Code 仍是直接编排层。本项目不应改造成由普通后端直接调用 LLM API 的架构。

## 目标

- 通过专门 subagent 承担叙事和角色扮演，强化沉浸感。
- 通过拆分世界模拟、角色行动、故事整理和质量审查，提升创作质量。
- 在 Claude Code 支持的范围内并行运行角色和辅助任务，加快输出速度。
- 将巨型 `CLAUDE.md` 拆成主原则和阶段 skill，减少提示污染。
- 第一期继续兼容现有浏览器、Python 桥接服务、文件邮箱和 `response.txt` 交付协议。

## 非目标

- 不用直接 Anthropic/OpenAI 后端集成替代 Claude Code。
- 第一期不重写前端。
- 第一期不强制引入 SSE、WebSocket 或持久任务队列。
- 普通游玩回合不得自动修改项目源码。系统性改进建议只记录到队列，后续单独处理。

## 架构

第一期引入“Claude Code 剧组”模型：

- 主 agent：只负责编排工作流、运行脚本、分发 subagent、收集产物、写入最终交付文件；只有在明确进行系统实现时才编辑项目代码。
- GM agent：拥有完整剧情视野，负责世界运转、背景压力、场景后果、非核心角色和剧情推进。
- Player agent：只接收玩家角色第一人称可见信息。它可以根据玩家给出的行动或剧情梗概延续玩家角色行为，但遇到关键决策点必须停下交还真实玩家。
- Character agents：每个核心角色只接收该角色第一人称上下文、记忆、目标、误解、感官和当前场景。它们不知道 Claude Code、用户、GM agent 或任何出戏机制。
- Story agent：接收 GM、player、character 产物，整理成统一小说文本，并尽可能保留各 agent 的行动、台词、矛盾和角色能动性。
- Critic agent：审查候选回复的叙事连贯、逻辑严密、玩家输入权威性、角色一致性、文风质量，以及是否停在正确决策点。

主 agent 仍是唯一写入 `skills/styles/response.txt` 并运行 `round_deliver.py` 的角色。

## Skill 拆分

`CLAUDE.md` 应收缩为项目宪法：核心原则、数据权威、命令入口和输出协议。细节行为移动到独立 skill 或阶段模块：

- `rp-orchestrator`：主回合流程和 subagent 调度规则。
- `rp-input-router`：区分角色输入和用户/系统指令输入。
- `rp-context-projector`：为不同 agent 构建视角受限上下文。
- `rp-gm-agent`：GM/世界模拟说明。
- `rp-player-agent`：玩家角色具身扮演说明。
- `rp-character-agent`：核心角色具身扮演说明。
- `rp-story-agent`：最终正文整理说明。
- `rp-critic-agent`：质量闸门和重试说明。
- `rp-delivery`：响应标签、handler 集成和回合后清理。
- `rp-assets-ui`：可选图片生成和单存档 UI 演化。

现有 `/rp` 命令应先导入 orchestrator skill，再由 orchestrator 根据当前阶段选择性加载其他 skill。

## 输入通道

前端最终应提供两个用户输入通道，但第一期可以先在后端协议和回合上下文里支持：

- 角色通道：第一人称行动、第一人称未来剧情梗概。进入 player agent 上下文，视为玩家角色意图。
- 用户指令通道：第三人称上帝视角设定、历史修正、重要角色声明、直接给 Claude Code 的指令。进入 orchestrator、GM、story 和 critic 上下文，但不得直接泄露到 player/character 第一人称认知，除非这些指令改变了角色在世界内可感知的事实。

在前端增加独立控件之前，`round_prepare.py` 可以继续对混合输入做分类，并写入不同 channel section。

## 数据流

每轮在当前卡片文件夹下创建结构化运行目录：

```text
.agent_runs/
  round-000123/
    input.json
    gm.context.json
    gm.output.json
    player.context.json
    player.output.json
    characters/
      <name>.context.json
      <name>.output.json
    story.input.json
    story.output.txt
    critic.report.json
    final.response.txt
```

`round_prepare.py` 继续负责读取运行状态，但输出重点从单一大 `round_context.txt` 转为多个 agent packet。`round_context.txt` 保留为调试产物和兼容兜底。

`round_deliver.py` 在 critic 报告存在硬失败时拒绝交付。软问题记录到 run folder，并反馈给主 agent 或 story agent 修正。

## 上下文隔离

GM context 可以包含完整剧情、玩家指令、记忆、变量和隐藏事实。

Player 与 character context 必须是视角投影：

- 只包含第一人称知识。
- 不包含未在世界内感知到的 GM 隐藏注记。
- 不直接读取玩家系统指令。
- 不知道自己由 Claude Code 模拟。
- 从 `memory/characters/<safe_name>/` 加载稳定角色记忆和目标。

若用户指令改变世界事实，由 context projector 决定各角色本轮能感知到哪些变化。

## 质量循环

第一期支持一次自动修复循环：

1. Story agent 生成候选最终回复。
2. Critic agent 评估候选回复。
3. 若存在硬失败，主 agent 将 critic 报告交回 story agent 进行一次修复。
4. 若第二次仍失败，主 agent 将失败写入 `.agent_runs/.../critic.report.json`，进度标记为 error 或 retry，并请求用户介入，避免无限循环。

源码级改进建议写入 `improvement_queue.jsonl`，普通游玩回合不自动应用。

## 兼容性

现有前端继续消费 `content.js`、`state.js`、卡片资产和 progress 文件。最终响应标签仍保持权威：

- `<polished_input>`
- `<content>`
- `<character_dialogues>`
- `<derived_content_edits>`
- `<edit_only>`
- `<summary>`
- `<options>`
- `<tokens>`

新的多 agent 运行产物只作为内部证据和调试数据，不替代 `chat_log.json` 或 `.player_inputs.jsonl`。

## 测试

第一期需要覆盖：

- 玩家输入分类到角色通道和用户指令通道。
- agent packet 生成与上下文隔离。
- 从 `character_orchestration.major` 生成核心角色 packet。
- `round_deliver.py` 对 critic 硬失败的拒绝交付。
- 最终 `response.txt` 与现有 parser/handler 的兼容。

手工验证至少包含：无卡空白模式、带一个重要角色的角色卡模式、同时包含第一人称行动和第三人称设定的混合输入。

## 推出计划

1. 拆分 `CLAUDE.md` 为项目宪法和 focused skills，不改变运行行为。
2. 在 `round_prepare.py` 中增加 agent packet 生成，同时保留 `round_context.txt`。
3. 让 `/rp` 工作流调度 GM、player 和相关 character subagents。
4. 在写入最终 `response.txt` 前增加 story 与 critic 阶段。
5. 持久化 `.agent_runs/` 产物，便于调试和回归测试。
6. 稳定后再考虑前端双通道控件、SSE 进度、模型路由和更丰富的后台图片/UI 任务。
