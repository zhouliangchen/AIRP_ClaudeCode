# 交互式 GM 回路重构状态

## 状态

已完成。本文取代早期逐步实施计划，记录当前交互式 GM loop 的实现契约。项目仍坚持 Claude Code 直接编排：Python 只负责文件协议、校验、投影、聚合和交付门禁，叙事创作与角色扮演仍由 Claude Code subagent 完成。

## 目标

本轮重构解决两个核心问题：

- player/character agent 不再读取完整游玩文本或 GM 隐藏事实，而是只接收严格视角投影后的第二人称场景提示、第一人称记忆和当前目标。
- player/character agent 在一轮输出中可以被 GM 多次唤起并多次行动，不再限制为每回合只输出一次行动。

## 当前模块

- `skills/agent_projection.py`：根据 actor 身份构建第一视角上下文，过滤 GM-only、world-truth、private event 等信息。
- `skills/agent_turn_loop.py`：执行 GM 驱动的回合内循环，处理 actor calls、并行组、感知反馈、对白转达和决策点停止。
- `skills/agent_schemas.py`：校验 GM 事件协议、actor events 协议和 story/critic 产物。
- `skills/agent_interactions.py`：维护 trace v2，包含 `source_call_id`、`target`、`causal_links`、`parallel_group` 和 actor 可见摘要。
- `skills/agent_outputs.py`：从 `gm.output.json`、`actor.outputs.json` 和 `interaction.trace.json` 生成 `story.input.json`，并拒绝缺失或不一致产物。
- `skills/rp_generate_cli.py`：在 input analysis 之后进入 GM loop，再执行 story、critic 和 delivery。
- `skills/control_plane_smoke.py`：确定性覆盖多次 actor 唤起、私有事件过滤、story 聚合和交付路径。

## 运行契约

GM 每次输出一个或多个 scene beats、events、actor calls、parallel groups、world state deltas 和 stop reason。若 GM 判断某个重要角色感知到事件、需要推动剧情或当前视角落在该角色上，就必须发起 actor call。

Actor 只返回 events，例如：

- `perceive_request`
- `dialogue`
- `action`
- `memory_delta`
- `goal_update`
- `wait_for_gm`
- `stop_for_player_decision`

Actor 的 private thought、hidden intent 和仅角色本人可知的记忆不会直接进入前端对白，也不会泄露给其他 actor。Story agent 只能使用已允许公开的事件、GM 叙述和必要的结构化摘要来整理正文。

## 并行与支线

GM 可以在 `parallel_groups` 中声明互不干扰的 actor calls，由 Claude Code 在可行时并行调度以加快输出。GM 助手多实例目前保留为协议方向和后续扩展点；当前运行时重点保障主线 GM loop 的稳定性、上下文隔离和产物校验。

## 交付门禁

`round_deliver.py` 仍是唯一前端交付入口。critic 通过时镜像 `story.output.json` 到 `response.txt` 并调用 `handler.py`；critic 返回 `revise` 或 `block` 时也必须进入交付门禁，写入 `repair_history.jsonl` 并返回 `retry` 或 `blocked`。普通游玩回合不会自动修改项目源码。

## 验证

局部验证可优先运行：

```powershell
python -m unittest tests.test_agent_schemas tests.test_agent_projection tests.test_agent_turn_loop tests.test_agent_outputs tests.test_rp_generate_cli -v
python skills/control_plane_smoke.py --repo .
```

完整验收仍以仓库根目录的最终验收清单为准。
