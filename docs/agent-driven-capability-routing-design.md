# Agent 自主路由与 Capability 执行层设计

## 背景

当前 RP 运行时已经采用 dispatcher-native 控制面：agent 负责创作、判断和产物生成，Python 负责消息、intent、projection、schema、snapshot、repair 和 delivery gate。`docs/重构建议.md` 进一步提出希望减少固定 workflow，让 agent 根据场景自行判断协作方式，同时保留多 agent 架构。

本设计确认新的重构方向：路由语义由 agent 决定，Python 不再硬编码“这类玩家输入应该路由到哪个业务分支”。Python 只维护 capability registry、授权门和安全不变量，并把通过校验的 capability request 转换为受控 intent、message 或 audit artifact。

## 目标

- 让 input analyst 和后续 agent 自行分析是否需要路由、路由给谁、调用什么能力、为什么调用。
- 用 declarative capability registry 取代 Python 中固定的 `if request_type == ...` 语义分支。
- 在不牺牲安全边界的前提下，支持 UI/图片、存档数据变更、retcon/replay、源码请求、story/GM 咨询等更灵活能力。
- 拆分过大的 dispatcher/control-plane 文件，让每类 intent executor 有清晰边界。
- 保持当前默认 RP 链路、artifact 权威边界、projection 隔离、delivery gate 和 control-plane smoke 可验证。

## 非目标

- 不把系统改成 agent 任意读写文件或绕过 Python gate。
- 不允许 player/character agent 直接和其他 actor 或 story/critic 自由通信；它们仍只能通过 GM/subGM 与 projection 保护的路径参与。
- 不在第一阶段实现任意多轮历史重演；先实现可审计的 replay plan 和单轮/当前轮 replay 能力。
- 不把 Claude Code 直驱架构替换成后台 LLM API 调度器。

## 核心设计

### 1. Agent 输出开放式 capability request

`input_analysis.output.json.routing_requests[]` 迁移为更通用的 `capability_requests[]`。为兼容迁移期，可以短期保留 `routing_requests` 作为别名，但新 prompt 和文档只推荐 `capability_requests`。

每个 request 只描述 agent 的判断，不绑定 Python 分支名：

```json
{
  "id": "cap-001",
  "requested_by": "input_analyst",
  "target": "story",
  "capability": "retcon.consult",
  "summary": "玩家要求把上一轮课堂场景改定为梦境并从醒来继续",
  "reason": "当前输入与前一轮 AI 派生事实冲突，需要 story/GM 给出安全回滚点",
  "source_channel": "user_instruction",
  "risk": "medium",
  "authorization_gate": "none",
  "payload": {
    "affected_round_hint": "previous",
    "requested_outcome": "reframe_previous_as_dream"
  },
  "evidence": {
    "raw_excerpt": "……",
    "semantic_unit_ids": ["unit-2"]
  }
}
```

`target` 是协作对象或执行域，例如 `gm`、`story`、`assets-ui`、`card-data`、`replay`、`main-agent`、`memory`。`capability` 是能力名，例如 `assets.generate_image`、`card.patch_data`、`retcon.consult`、`replay.plan`、`source.change_request`。

### 2. Python 使用 declarative capability registry

新增 capability registry，建议放在 `skills/capabilities/*.json` 或一个小型 Python 数据模块中。第一阶段可以先用 Python 常量实现 registry，但数据结构必须声明式，不能把语义判断写进分支。

registry 负责描述：

- capability 是否存在。
- 允许哪些 requester 调用。
- 目标 agent 或 executor。
- 需要的授权门：`none`、`manual_confirmation`、`allowSourceCodeSelfRepair`。
- 默认风险等级上限。
- 结果类型：`message`、`intent`、`audit_only`、`deferred`、`blocked`。
- 需要的安全检查：source hash、snapshot、projection、artifact path、postprocess contract、delivery gate。

Python 只做机械匹配和安全校验：request 的 `capability` 命中 registry 后，按 registry 生成 intent/message/audit artifact。未知 capability 不表示语义错误，而是写入 `unsupported_capability` audit，并通知 main agent 或 GM，不让运行时静默吞掉。

### 3. Capability adapter 替代固定 routing 分支

现有 `input_routing_requests.process_routing_requests()` 改造成通用 adapter：

- 读取 `capability_requests[]`。
- 校验 request schema、source evidence、authorization gate、requester ACL。
- 查 registry。
- 根据 registry 转换为 intent/message/audit artifact。
- 记录 `artifacts/capability_requests/<id>.json`，包含原始 request、registry 命中、生成的 intent/message、授权状态和阻断原因。

这会取代当前 `assets_ui_task`、`story_retcon_consult`、`card_data_edit`、`source_feature_request` 的硬编码分支。旧字段可在迁移期映射为 capability request，但不再扩展旧枚举。

### 4. Dispatcher 拆分 executor，不改变外部协议

`agent_dispatcher.py` 保留公共入口 `dispatch_next()`、artifact helper 和统一 result/blocker。具体 intent executor 逐步迁移到小模块：

- `agent_executors/input.py`：`analyze_input` 和 capability adapter 调用。
- `agent_executors/gm.py`：`run_gm_turn` 和 GM continuation。
- `agent_executors/actor.py`：`request_projection`、`run_actor`。
- `agent_executors/subgm.py`：`run_subgm_thread`。
- `agent_executors/story.py`：`compose_story`、`review_critic`、`run_postprocess`。
- `agent_executors/repair.py`：`repair_request`、`rollback_request`、`system_request`。
- `agent_executors/delivery.py`：`deliver_round`、postprocess gate。
- `agent_executors/assets.py`：`assets_task` 和后续 asset worker 接口。

拆分原则是行为保持不变，先移动代码和测试，再引入新 capability 行为。

### 5. Retcon/replay 作为能力，不作为 input analyst 特判

retcon/replay 不由 Python 从文本判断触发。agent 可以请求：

- `retcon.consult`：story/GM 咨询前文冲突和安全修正方式。
- `replay.plan`：生成结构化 replay plan，包括回滚点、受影响轮次、每轮需要保留的玩家原文、需要丢弃的 AI 派生内容。
- `replay.execute_round`：在授权和 snapshot 存在时重演一轮。

第一阶段只执行单轮或当前轮 replay；多轮 replay 先写 plan 和 audit，不自动连续执行，避免污染历史与记忆。

### 6. 安全不变量保留在 Python

以下逻辑仍必须在 Python 中强制：

- `.player_inputs.jsonl` 权威性，不允许 agent 改写玩家原文。
- player/character actor-facing 内容必须经过 projection。
- actor/subGM/GM artifact schema、`source_call_id`、trace provenance、hidden phrase guard。
- snapshot/rollback path 必须在卡片 `.agent_runs/` 边界内。
- capability request 的 artifact path 不能逃逸工作目录。
- source-code 修改必须显式授权。
- delivery 前必须 critic pass 和有效 postprocess core。

## 错误处理

- 未知 capability：写 audit artifact，创建给 main agent 的 `unsupported_capability` 消息，不阻塞普通 GM/story 链路，除非 request 标记为 critical。
- 授权不足：写 `authorization_required` artifact/message，不创建可执行 intent。
- schema 无效：阻断对应 capability request，但不删除原始 input analysis；默认继续普通 GM 链路。
- replay plan 缺少 snapshot 或玩家原文证据：blocked，要求人工确认。
- asset worker 不可用：deferred，不阻塞正文交付。

## 测试策略

- 单元测试 capability request schema、registry lookup、ACL、authorization gate、unknown capability audit。
- 迁移测试旧 `routing_requests` 到新 `capability_requests` 的兼容映射。
- dispatcher 拆分前后 smoke 输出应保持默认链路一致。
- replay plan 测试必须验证玩家原文保留、snapshot 边界、AI 派生产物清理范围。
- source feature request 测试必须验证无授权时只写消息/audit，不创建可执行源码修改 intent。
- control-plane smoke 增加 capability request fixture，验证普通链路不被未知或 deferred capability 破坏。

## 分阶段实施

### 第一阶段：协议与低风险拆分

1. 更新 input analyst prompt/schema，引入 `capability_requests[]`。
2. 新增 capability registry 和 adapter，保留旧 `routing_requests` 兼容映射。
3. 把 `input_routing_requests.py` 改为 capability adapter。
4. 拆出 input/actor/delivery 三类 executor，保持行为不变。
5. 更新 README、CLAUDE、AGENTS 与 smoke。

### 第二阶段：可执行 retcon/replay

1. 实现 `retcon.consult` 和 `replay.plan` capability。
2. 实现单轮/当前轮 `replay.execute_round`，必须依赖 snapshot。
3. 增加 replay plan artifact 和对应测试。

### 第三阶段：assets 与更多能力

1. 把 assets-ui 从 deferred 记录扩展为可选 worker 接口。
2. 支持 postprocess contract 自动补齐后的异步 UI 数据修复。
3. 继续拆分 GM/story/repair executor，降低 dispatcher 文件体积。

## 验收标准

- `python -m unittest discover -s tests -v` 通过。
- `python skills/control_plane_smoke.py --repo .` 通过，默认链路仍包含 `run_postprocess -> deliver_round`。
- `python -m py_compile` 覆盖 dispatcher、executor、capability、input analysis、routing adapter、repair/delivery 相关文件。
- 新 capability request 不允许绕过 projection、snapshot、source authorization 或 delivery gate。
- 文档中不再把具体路由策略描述成 Python 固定分类，而是描述为 agent-driven capability request。
