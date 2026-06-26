# 话本 RP - Claude Code 直驱模式

本项目把 Claude Code 用作本地角色扮演编排层：Python 桥接服务器负责浏览器交互和文件状态，Claude Code 负责读取上下文、生成叙事、更新记忆与触发可选的 UI/图片能力。运行方式是“一张卡或一个故事一个文件夹”，所有聊天记录、记忆、变量和定制 UI 都跟随该文件夹保存。

## 快速开始

请在 Windows PowerShell 中操作。

1. 准备 Python 3.x、Node.js、Git 和已登录可用的 Claude Code。
2. 在项目根目录运行一次 `setup-claude-code.bat`，检查本机环境和 Claude Code 命令。
3. 在项目根目录下新建一个卡片/故事文件夹，例如 `我的角色/`。
4. 可选：把 SillyTavern PNG 角色卡、世界书 `.json` 或小说 `.txt` 放进该文件夹。没有素材也可以启动空白模式。
5. 进入该文件夹，启动 `claude`，然后输入 `/rp`。
6. 本机打开 `http://localhost:8765`，在浏览器里提交行动或指令。局域网设备可打开启动输出中的 `http://<本机局域网IP>:8765`。

再次游玩同一存档时，仍从同一文件夹启动 `claude` 并运行 `/rp`。系统会读取 `chat_log.json` 和 `memory/` 继续剧情。

## 项目结构

```text
AIRP_ClaudeCode/
├─ .claude/                 # Claude Code 命令和技能配置
├─ skills/                  # 后端、MVU、导入和回合管线
│  ├─ server.py             # 浏览器桥接服务，默认监听 0.0.0.0:8765
│  ├─ start_server.py       # 启动 server.py 与 MVU 服务
│  ├─ import_prepare.py     # 启动/导入管线
│  ├─ round_prepare.py      # 每轮控制面上下文与 agent 邮箱准备
│  ├─ round_deliver.py      # 每轮机械交付、handler 调用和记忆更新
│  ├─ agent_dispatcher.py   # 消费 pending intents 并显式驱动 projection/actor/subGM 协作
│  ├─ agent_actor_runtime.py # 共享 actor 协作 helper 与 trace/产物写入
│  ├─ agent_messages.py     # 每轮 append-only 消息总线与 inbox 投影
│  ├─ agent_intents.py      # 可执行控制面 intent 生命周期
│  ├─ agent_snapshots.py    # .agent_runs 快照与回滚辅助
│  ├─ agent_prompts.py      # 生成 GM/player/character/story/critic/postprocess prompt
│  ├─ agent_outputs.py      # 校验 agent 产物、生成 story.input.json、校验 postprocess 交付核心、修复记录
│  ├─ agent_memory.py       # 写入 subagent 记忆 delta 与周期摘要
│  ├─ agent_schemas.py      # agent JSON 产物 schema 校验
│  ├─ control_plane_smoke.py # 确定性、无 live model 的控制面冒烟测试
│  ├─ handler.py            # 解析 response.txt 并重建前端数据
│  ├─ mvu_server.js         # Node MVU 校验服务，默认端口 8766
│  └─ styles/               # 前端入口、运行时文件和文风配置
├─ CLAUDE.md                # Claude Code RP 工作流规则
├─ STORY.md                 # 可选的叙事规划参考
└─ <你的卡片文件夹>/          # 用户存档，不应提交到仓库
```

`skills/styles/content.js`、`state.js`、`input.txt`、`.pending`、`round_context.txt`、`import_context.txt` 等是运行时生成文件，不是源代码入口。

## 运行模式

有素材的新卡：`import_prepare.py` 会解析 PNG/JSON/TXT，初始化 `chat_log.json`、`memory/`、变量状态和前端数据。

旧存档：检测到 `chat_log.json` 与 `memory/` 后，系统会恢复最近剧情、角色状态和用户偏好。玩家输入会额外记录到 `.player_inputs.jsonl`，这是玩家原文的权威日志；Claude Code 可以重写 AI 派生叙事、角色资料、变量和记忆来服从玩家新设定，但不得改写、裁剪或摘要玩家原始输入。

玩家输入可以是第一人称行动、第一人称剧情梗概、第三人称上帝视角设定，或三者混合；但生产代码不会通过固定关键词、子串或正则去判断这些语义。单通道玩家原文会先被完整保留给 input analyst 与 GM，语义路由、隐藏事实、重要角色声明和改写意图只能来自显式双通道输入或已校验的 `input_analysis.output.json`。在输入分析应用前，player/character actor-facing packet 不会接收未经分析的原始玩家文本。

空白启动：当前文件夹没有 PNG/JSON/TXT 时，会创建 `.card_data.json`、`.initvar.json`、`memory/characters/_self/` 和 `ui_manifest.json`。系统不会自动生成 AI 开场，而是等待你在浏览器输入第一轮设定，之后逐轮沉淀角色卡、变量和记忆。

## 浏览器界面

桌面端会显示正文、插图、行动选项、输入框和侧栏配置。移动端默认只保留基础背景、对话历史、插图、下一步行动选择题和自定义输入框；右上角设置按钮会展开覆盖式侧边栏，用于调整文风、NSFW、字数、自我修复模式、源码自修复开关、调试模式和当前目标等进阶选项。

玩家提交输入后，本轮输入会先作为“等待 Claude Code 回复”的 pending 回合显示在前端；AI 回复交付后，pending 回合会被正式回合替换。前端会轮询并重新加载 `content.js`，因此正文、状态 UI 和图片资产可以在不手动刷新的情况下更新。可用时，顶部会显示回复进度条，例如已接收、整理上下文、生成中、交付中和完成。

进度条由 schema v2 状态机驱动：主界面显示稳定阶段标签和百分比，展开详情可查看当前 agent、subGM 支线、actor call、重试次数或阻塞原因。旧的 `stage` 字段仍作为兼容字段保留，但新增代码应写入 `skills/round_state.py` 中声明的状态 ID。

每个已交付的玩家回合可点击“编辑输入”。“仅更新”只修改权威输入日志和当前显示，并把影响记录到 `.player_input_edits.jsonl`，等待后续回合评估；“更新并提交”会从该回合截断旧分支，将修订后的输入重新提交为 pending 回合。Claude Code 针对具体剧本生成的热更新 UI 不受移动端简化布局限制，可以按剧情需要插入到合适位置。

默认服务会监听所有网卡，便于手机、平板等同一局域网设备访问。如果只想允许本机访问，可在启动前设置 `$env:AIRP_HOST="127.0.0.1"`。若局域网地址仍打不开，请确认设备在同一网络，并允许 Windows 防火墙中的 Python 入站连接；也可用管理员 PowerShell 执行 `New-NetFirewallRule -DisplayName "AIRP ClaudeCode Frontend 8765" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private,Domain`。

## 图片与 UI 热更新

`skills/image_generate.py` 是 OpenAI-compatible 图片生成适配器，默认模型为 `gpt-image-2`。可用环境变量或本地配置提供密钥：

```powershell
$env:OPENAI_API_KEY="sk-..."
python skills/image_generate.py "<卡片文件夹>" --prompt "rainy seaside convenience store" --kind scene --target scene_illustration --async
```

也可在卡片文件夹、项目根目录或 `skills/` 下放置 `image_config.local.json`，或在项目根目录使用 `.image_api.json` 配置 `api_key`、`base_url`、`model`。这些文件已被忽略，不要提交。生成图片会写入当前卡片文件夹的 `generated/images/` 和 `.card_assets.json`，脚本会重建 `content.js`，已打开的浏览器会自动看到新图片。

图片与 UI 工作永远是正文交付后的异步增强。story、critic、GM 或主 agent 可以把视觉需求记录成非阻塞 `assets_task`，dispatcher 会写入 `artifacts/assets_tasks/<intent_id>.json` 与消息状态；在没有外部图片/UI worker 时会标记为 `deferred`，不会阻塞已经通过 critic 的文本交付，也不会调用真实图片模型。

单存档 UI 定制请写在卡片文件夹内的 `.beautify_template.html`、`.beautify.json`、`.regex_scripts.json`、`ui_manifest.json` 和按需生成的 `postprocess_contract.json`。不要把全局 `skills/styles/index.html` 当作某张卡的定制层。若 assets-ui 更新存档级 UI schema，必须同步更新 `postprocess_contract.json`；缺少必要数据契约时写入 `.agent_runs/postprocess_repair_queue.jsonl`，下一轮再补齐。

## 核心角色 subagent

`round_prepare.py` 每轮会生成 `skills/styles/character_contexts.json`，并在当前卡片文件夹下创建 `.agent_runs/<round>/` 消息驱动运行时。它保留原始输入、创建 input analyst 请求，并为准备阶段当前存在的前置 agent 与 story/critic 等正文后续 agent 物化初始 prompt、manifest 和 `expected_outputs`，但不负责从玩家文本中推断语义。postprocess 不属于初始 manifest `expected_outputs`，而是在 critic pass 后由 dispatcher 通过 `run_postprocess` 动态调度，并在该阶段生成对应 prompt 与输出。该目录包含 `input.json`、`input.raw.json`、`input_analysis.request.md`、`gm.context.json`、`player.context.json`、`characters/*.context.json`、`prompts/*.prompt.md` 和 `manifest.json`；运行时还会维护 append-only 通信日志 `messages.jsonl`、每个 agent 的投递索引 `inboxes/`、可执行控制面请求 `intents/`，以及物化交付文件 `artifacts/`。Agents 可以通过消息和 intent 请求协作，Python 仍对 ACL、投影、trace、snapshot、schema 和机械交付边界保持权威控制。运行中还会按需写入 `input_analysis.output.json`、`gm.output.json`、`actor.outputs.json`、`interaction.trace.json`、`story.input.json`、`postprocess.output.json`、`memory_summaries/*.summary.json` 和 `repair_history.jsonl`；这些 root artifact 主要是交付、记忆边界和调试导出，控制面权威以 `artifacts/` 与 intent/message runtime 为准。player/character prompt 不再注入 `.claude/skills/rp-player-agent.md` 或 `.claude/skills/rp-character-agent.md`，而是由 `skills/agent_prompts.py` 的统一 actor 模板生成；character prompt 中面向模型展示的角色名来自 actor packet 的 `self_knowledge.name`，协议 actor id、manifest key 和文件路径仍使用 safe name。`manifest.json` 的初始 `expected_outputs` 使用当前契约：`input_analysis`、`gm`、`actors`、`story`、`critic`，以及可选的 `memory_summaries`；不会再要求独立的 `player.output.json`、`characters/*.output.json`，也不会在准备阶段要求 `postprocess.output.json`。`manifest.json` 会记录阶段历史，例如 `prepared`、`prompts_ready`、`awaiting_input_analysis`、`analysis_applied`、`awaiting_agent_outputs`、`story_ready`、`critic_passed`、`delivered` 或 `blocked`。

浏览器设置中的运行时选项由 `runtime_settings.py` 统一规范化。每轮只会把 `style`、`wordCount`、`nsfw`、`selfRepairMode` 和 `allowSourceCodeSelfRepair` 写入 round context、agent context、manifest 与 story input；调试模式仍由前端/服务端单独保存。`style` 会尝试读取 `skills/styles/presets/<文风>.json` 的 `name`、`title`、`content` 作为 story agent 的创作指引，并把同一份文风资料交给 critic 作为后续风格检查输入。`wordCount` 当前作为 GM 的软性节奏提示和 story output target；critic 会收到由 `runtime_settings.py` 生成的确定性字数指标并写入结构化长度检查，若本轮停在真实玩家决策点则可豁免扩写。`nsfw` 只作为 GM/subGM/story 创作语气提示，不作为 critic 校验项。

不再提供独立的“人称”“防抢话”“背景 NPC”或“玩家角色名”设置。角色通道默认按玩家角色第一人称输入处理，指令通道默认按第三人称或上帝视角系统指令处理，最终正文默认使用第二人称；若用户需要改变叙事人称，必须通过经过校验的输入分析产物表达，而不是通过全局设置。player agent 与 character agent 一样按沉浸式第一人称行动；当玩家 agent 产生高风险或关键行动时，GM 会把它作为真实玩家决策点并停止本轮自动推进。GM 可按剧情需要引入普通 NPC 或提出重要角色提升，玩家角色名则应由玩家在角色通道自述，或在指令通道通过输入分析修改。

`input_analysis_apply.py` 只接受通过校验的结构化输入分析；若 live model 返回旧式 `semantic_units[].content` 形态，会在严格校验前只补齐结构字段（如 `id`、`source_channel`、`raw_excerpt`、`derived_summary`、`confidence`、`persist`），不会从玩家原文额外推断语义。live 模式下若 input analyst 首次返回畸形 JSON，`rp_generate_cli.py` 会把具体解析错误作为 `Previous Attempt Rejection` 反馈给同一 agent 重试，避免后续流程在缺失或损坏的输入分析上继续推进。重要角色设定必须区分公开档案、角色本人私有自知和 GM-only 隐藏事实：例如角色明确保留记忆、真实身份或能力时，input analyst 应同时写入该角色的 `character_private_and_gm` 记录，而不是只写公开 facade 或全局 hidden fact。

当用户在指令通道主动请求系统能力、UI/图片更新、剧情回滚/重演协商、存档角色数据调整或源码功能实现时，input analyst 可以在 `input_analysis.output.json.capability_requests[]` 中写入 agent 自主判断后的能力请求。Python 不再通过固定关键词或固定 request type 判断路由语义，只根据 capability registry 校验能力是否存在、授权门是否满足、目标 agent/executor 是否可用，以及 artifact 路径、snapshot、projection、delivery gate 等安全不变量。源码能力 `source.change_request` 仍必须经过 `allowSourceCodeSelfRepair` 授权；需要存档改写或 replay 的能力在未确认前只写 audit/message，不直接改文件。

每轮会为已注册的重要角色生成隔离上下文；`max_parallel_subagents` 只限制运行时同一安全批次最多并行调度多少角色，不限制已注册重要角色的上下文数量。GM 输出的 `parallel_groups` 会被控制面校验；互不依赖、不同角色的合法 actor calls 可并行执行，不安全的并行声明会降级为串行并写入路由警告；若 actor call 与活跃 subGM 占用冲突，则会在批次调度前被拒绝。默认 live path 中，GM turn 不再隐藏调用 actor/subGM：GM 只产出 actor call、支线命令或停止原因，`agent_dispatcher.py` 随后显式创建并执行 `request_projection`、`run_actor`、`run_subgm_thread` 和必要的 GM continuation intents。`agent_actor_runtime.py` 提供共享 actor 协作 helper，统一处理投影包、actor 输出校验、trace 和物化产物写入。Claude Code 工作流会在场景强相关时最多并行调用配置允许数量的核心角色 subagent，让它们只从角色自身立场返回反应、隐藏意图、行动/台词候选、变量建议和记忆 delta。GM 可读取完整剧情与用户指令；player/character 只读取第一人称投影上下文，不接触 GM 隐藏事实。

projection 采用两层边界：actor context rendering / `agent_projection.project_actor_context` 先从 actor 记忆、信念、感官和可见事实构建 immersive context；LLM projection agent 只负责对待交付的 actor-facing 消息做语义 review 与局部改写，并返回 `final_actor_message`。Python 只保留最小确定性协议门，校验 actor id、source call id、ACL、artifact provenance、retry 和 rollback 不变量。player/character 收到的是 immersive context；隐藏事实、projection feedback 和 misconception 标签都不是 actor-facing 内容，错误认知应作为角色自己的主观记忆或信念保留。

GM 输出进入 actor/story-facing 字段前会先执行可见性清理。来自 `user_instruction_channel`、隐藏设定和 GM-only 历史的隐藏短语不得保留在 `scene_beats`、`events.content/metadata/target/source_call_id`、`events.visibility_basis`、`decision_point.reason/options`、`actor_calls.source_call_id/prompt/reason/metadata`、`actor_calls.visibility_basis`、`character_promotions.reason/profile_seed` 中；`scene_beats` 与 `events` 可以携带可选的 `scene_id`、`location`、`time_window`、`visible_to`、`sensory_channels`、`source_actor`、`target_actor` 和 `visibility_basis`，`events` 还可以携带 `target` 与 `source_call_id`，这些事件字段同样必须保持 actor/story-facing，不得使用隐藏标记、复制隐藏短语，或用 `moon-base-archive`、`moon_base_archive`、`moon/base/archive`、`moon:base:archive`、`moon|base|archive`、`moon—base—archive` 这类非字母数字分隔符变体绕过短语检测；生成 `story.input.json` 前，主 `interaction_trace.visible_events` 与公开决策/停止原因也会做同样的 story-facing 校验或清理。隐藏短语红线会先做快速预筛选并缓存正则；较长的连续 CJK 隐藏短语只做精确匹配，避免模糊正则在长设定上造成阻塞。`story.input.json` 会继续保留 raw loop outputs 与 `memory_deltas` 供审计、critic 和记忆调度使用，但 dispatcher 传给 story agent 和 postprocess agent 的 Runtime Input 只使用其中的 `story_prompt_context` 安全视图；该视图会结合当前输入与持久 GM-only 隐藏设定过滤 actor call prompt/reason、私密 memory/goal 事件，以及误标为可见的隐藏设定片段。`stop_reason` 只允许 `continue`、`player_decision`、`word_target`、`complete`、`max_steps` 枚举值；GM/subGM 的每个 `actor_calls[]` 必须携带带 `summary` 的 `visibility_basis`，用于说明该 actor 为什么能直接感知或被合法寻址；若可见性无法证明，信息必须保留在 GM-only 范围，不得投递给 actor call、可见事件、感知回答或对话转交。确定性 control-plane smoke 会保留 GM-only 隐藏来源，并验证落盘后的 loop output、actor packet 与 story-facing prompt context 只保留清理后的内容。

普通 character `action` 事件按审计材料处理，不直接进入 story/postprocess prompt；只有安全视图中保留的角色台词、对话转交和可见 custom action 可作为正文组装依据。

如果 actor 输出 `perceive_request`，下一次 GM 输出必须通过 `perception_responses[]` 对每个待处理请求作答或关闭；`answered` 的可见感官反馈会自动再次调用原请求 actor，让其基于反馈继续行动，`closed` 则只记录无需继续的可见理由。普通 actor 响应即使不要求 GM resolution，也会回到 `run_gm_turn` 由 GM 继续仲裁；只有玩家高风险动作或 `stop_for_player_decision` 会停在真实玩家决策点而不创建 GM follow-up。GM 不应在感知请求未处理时跳过到无关叙事或结束本轮。

当 actor 的 `dialogue` 事件直接指向已注册的重要角色时，运行时可以把这句话转交给目标角色。该事件的 `metadata` 只允许并只保留 `exact_visible_words`、`delivery_channel` 和 `visible_tone_or_action`：转交 prompt 只包含说话者、目标可听见的原话、公开通道和公开动作/语气，不携带私下意图、隐藏动机或 GM 解释；`interaction.trace.json` 中的 `dialogue_transfer` 也只记录这些安全字段与 `source_call_id`。任何隐藏意图、GM-only 标记或未列入白名单的对话转交 metadata 都会在 actor 输出校验或 `story.input.json` 生成前被拒绝。

actor 可以返回受控的 `custom_action` 事件来表达非标准但可见的角色行动，例如撬门、布置障碍或尝试高风险动作。该事件必须提供非空的顶层 `target`，并在 `metadata` 中提供 `category`、与事件正文完全一致的 `visible_content`、布尔型 `requires_gm_resolution` 和 `risk_level`（`low`、`medium`、`high` 或 `critical`）；未列入白名单的字段、隐藏标记、动态隐藏短语、缺失目标或与正文不一致的可见内容都会被拒绝。运行时只把这些公开字段写入 `interaction.trace.json` 的 `custom_action` 摘要；当玩家 agent 返回 `high` 或 `critical` 风险的 `custom_action` 时，本轮会强制停在真实玩家决策点，由玩家确认下一步。

重要角色可由输入分析 preprocess 或主 GM 通过 `character_promotions` 提升为 major。preprocess 写入玩家权威档案；GM 输出中的 `character_promotions` 必须使用 `source_agent: "gm"`，主 GM 只能写入非玩家权威的 promotion seed，且不会覆盖已有 preprocess/player profile。`gm_assistant` 已收敛为 `subGM` 支线模型：subGM 可读取支线所需的全知信息，但只能在主 GM 分配的边界内行动。支线状态持久化在 `.agent_runs/<round>/side_threads/<thread_id>/`，每条支线拥有自己的 `state.json`、`messages.jsonl`、`interaction.trace.json` 和可选 agent 产物。subGM 可以向 GM 发送消息；GM 可以对支线执行 `accelerate`、`pause`、`resume`、`merge` 或 `close`。subGM 不能创建或提升重要角色，不能再派生 subGM，不能包含玩家角色，也不能直接修改边界；相关诉求只能作为 `promotion_requests` 或 `boundary_requests` 留给主 GM 仲裁。

subagent 不直接写 `skills/styles/response.txt`，也不直接交付前端。dispatcher 会把 GM、actor、subGM、story、critic 和 postprocess 的权威物化结果写入 `artifacts/`，并按交付/记忆边界导出根目录 `gm.output.json`、`actor.outputs.json`、`story.input.json`、`postprocess.output.json` 等同名文件。`agent_outputs.py` 会校验 GM/actor 产物和 trace v2 的 `source_call_id` 对应关系后生成 `story.input.json`，并把 `loop_outputs`、`memory_deltas`、可见交互轨迹、私有事件计数和关键决策点整理给审计、critic、postprocess 与记忆调度使用；story agent 和 postprocess agent 实际收到的是 `story_prompt_context` 安全视图，而不是完整 raw 审计视图。story agent 只负责正文与来源支撑的角色对话，写入 `story.output.json`，不再输出 MVU 变量更新命令；当当前 `role_channel` 明确表示玩家停止、拒绝、回避、尚未执行或只是考虑某个动作时，story 不得把该动作改写成已经发生。critic agent 是正文质量门，只审核叙事正文和来源支撑的角色对话，写入 `critic.report.json`，不要求也不修复摘要、行动选项、当前目标、状态面板或 UI 数据；若正文把当前玩家动作的正负极性写反，必须按上下文一致性问题要求修订或阻断。critic 通过后，默认 dispatcher 路径会先执行 `run_postprocess`，由 postprocess agent 基于安全视图生成正文以外的前端数据，包括剧情摘要、下一步行动建议、当前目标、状态面板、MVU 变量更新命令和可选 UI 扩展数据；只有 `postprocess.output.json.core` 校验通过后才创建 `deliver_round` intent。`round_deliver.py` 与 `agent_outputs.prepare_delivery` 在交付前也会要求有效的 `postprocess.output.json.core`，然后才把 story 正文镜像到 `response.txt` 并调用 `handler.py`。`handler.py` 在存在有效 postprocess 输出时只从 `postprocess.output.json.mvu.commands` 执行 MVU，不再从 story 正文抽取变量命令。UI 扩展或 postprocess contract 缺失/失败只有在写入修复记录和 `.agent_runs/postprocess_repair_queue.jsonl` 后才是非阻塞；这些 pending postprocess 修复会在下一轮上下文中显式暴露，等待补齐。进度条和回复状态仍由 `progress.json` / `round_state.py` 运行时状态机负责，不属于 postprocess。若 critic 要求 `revise` 或 `block`，本轮会记录到 `repair_history.jsonl`，同时写入 `repair_request` 消息和待处理 repair intent；`rp_generate_cli.py` 在修复交付成功后将该 intent 标记为 completed，无法完成时标记为 blocked。若该 revise/block 报告提供 `system_iteration_suggestion`，会追加到卡片文件夹的 `.agent_runs/improvement_queue.jsonl`。

critic agent 是唯一叙事质量门。文风、字数、人称、上下文连贯、玩家输入满足度和修复路由由 critic 基于 `story.input.json`、`story.output.json`、文风 profile 与确定性质量指标统一判断；`round_deliver.py` 只负责机械交付、handler 调用、token 统计、记忆任务和生命周期清理，不再因字数、文风、NSFW 或人称发起内容重写。

`agent_dispatcher.py` 以 pending intents 作为可执行下一步来源，调度 input analysis、GM turn、projection、actor、subGM thread、story、critic、postprocess、repair 与 delivery intent，把权威产物写入 `artifacts/`，并在不安全或停滞运行时阻断本轮；根目录同名 story/critic/postprocess 文件只是交付边界导出，不再作为控制面权威。旧 broad GM loop 如仍存在，只作为 helper 或 regression-only 入口，不是默认 live path。`round_prepare.py` 会在准备新一轮前创建 before-round snapshot，快照保存在 `.agent_runs/snapshots/<snapshot_id>/`，用于需要回退本轮派生产物时恢复。`control_plane_smoke.py` 使用临时目录构造一轮确定性的多 agent 控制面流程，不调用 live model，用于快速验证 artifact、trace、memory delta/summary、消息类型、intent 计数、snapshot 和交付路径；当前冒烟链路会显式覆盖 `review_critic -> run_postprocess -> deliver_round` 顺序。

每轮会为 player 和本轮相关 character 安排一次 `memory_summaries/*.summary.json` 自我记忆整理。摘要只允许写入角色自己视角可知的信息；校验会拒绝未排期文件和 `gm_only`、`world_truth`、`gm_notes`、`omniscient`、`hidden_note`、`out_of_character` 等显式隐藏标记。若某个重要角色本轮确实使用了 subagent，story 输出可保留 `character_dialogues` 元数据，前端会在主叙事前以独立对话框显示。

`memory/objective_world.json` 是 GM 拥有的客观世界知识，用于记录隐藏真相、因果和全局设定，并随 snapshot/rollback 一起纳入恢复边界；player/character 的 `memory/` 仍是主观记忆，只保存该 actor 自己可知、相信或感知到的内容。

交付成功后，`round_deliver.py` 会在 `manifest.post_round_memory_jobs` 下为本轮实际参与的 player/character actor 记录回合后记忆任务，并生成 `post_round_memory_jobs/*.job.json` 与 `prompts/post_round_memory/*.prompt.md`。这些任务只包含该 actor 自己的输出、actor 可见交互、近期记忆和目标；没有需要整理的 actor 时会标记 `not_required`；已完成的 `*.summary.json` 会按同一结构化 actor 记忆格式写入并标记 `complete`；缺失输出保持 `pending`，校验失败标记为 `degraded_memory_state`，且不会回滚或删除已交付的 `response.txt`。下一轮会把降级记忆状态显式暴露给上下文准备流程，而不是静默忽略。

回合后记忆处理结束后，`round_deliver.py` 会执行 `agent_lifecycle.cleanup`。该步骤只做文件级清理：把仍处于 `running`、`merging`、`needs_gm`、`blocked` 或 `max_steps` 的 side thread 标记为 `paused`，写入恢复提示并释放角色占用；`completed` 与 `closed` 支线保持不变。清理结果记录在本轮 `manifest.agent_lifecycle_cleanup` 和最终交付 JSON 中，不会终止系统进程，也不会删除已有支线产物。

成功交付到前端后，系统会执行回合级 agent lifecycle cleanup：仍处于 `running`、`merging`、`needs_gm`、`blocked` 或 `max_steps` 的 subGM 支线会被暂停并释放角色占用；未完成的支线不会被标记为 completed，而是保留 `next_resume_point` 供之后由主 GM 恢复。player/character actor 调度前会重新计算 `context_version`，因此人格、背景、记忆或目标文件被更新后，下一次调度会读取最新上下文；已经在运行的 actor 调用不会被强制中断。

结构化 actor 记忆摘要只允许更新 `memory/player/` 或 `memory/characters/<name>/` 下的 `long_term.md`、`key_memories.md`、`short_term.md` 和 `goals.json`。`recent.md` 是回合内增量的暂存来源，成功整理后会被消费；actor 记忆更新必须使用 `source: self` 与 `visibility: actor`，不得写入角色档案字段或隐藏标记。

## 自我修复配置

系统支持四档自我修复模式，配置键为 `selfRepairMode`，默认值是 `limited`（受限修复）。可在浏览器侧栏“自我修复”下拉框调整，也可直接写入 `skills/styles/settings.json`：

```json
{
  "selfRepairMode": "limited",
  "allowSourceCodeSelfRepair": false
}
```

`selfRepairMode` 支持 `off`（关闭）、`analysis_only`（仅分析定位）、`limited`（受限修复）和 `full`（完全修复）。调试测试时建议使用 `analysis_only`，也可以通过环境变量临时覆盖：

```powershell
$env:AIRP_SELF_REPAIR_MODE="analysis_only"
```

- `off`：跳过自动修复步骤，失败后等待人工或外部工具处理。
- `analysis_only`：只保留 critic 诊断和机械交付诊断与修复建议，不自动重写 story 或重跑 GM。
- `limited`：默认模式，只自动处理 critic 要求的低风险 story 修订，以及交付阶段发现的产物、schema、handler 等机械问题；不会自动修复 critic `block` 或重跑整轮剧情推进。
- `full`：允许更高上限的修复循环，并可在 critic 判断问题出在 GM/actor/subGM 剧情推进环节时，回退本轮 `.agent_runs/<round>/` 内的推演派生产物，再让 GM 带着修复上下文重新开始本轮推演。

critic 会在 `critic.report.json.repair_routing` 中标注失败来源和回退范围。`story_composition` 与 `delivery_gate` 只重跑 story/critic；`gm_loop`、`actor_agent` 与 `subgm` 会在 `full` 模式下回退本轮 dispatcher 派生的 GM/actor/subGM 产物并重新调度。当前第一版不做单个 actor/subGM 的局部热修补，以避免 `interaction.trace.json`、`source_call_id` 和 side thread 状态不一致。

如果 critic 怀疑是系统代码问题，会使用 `stage: "system_code"` 并写入修复建议。源码自修复必须同时满足 `selfRepairMode: "full"` 和 `allowSourceCodeSelfRepair: true`；普通游玩中不会因为 critic 报告而自动修改关键项目源码。未授权时，本轮会以 `source_code_self_repair_not_authorized` 阻断并保留 repair intent；已授权时，运行时只创建有边界的 `system_request` intent/message，交给主 agent 按显式工作流诊断和修改源码，dispatcher 本身不会执行任意源码编辑。

与此相对，用户主动在指令通道提出的源码功能实现请求会被记录为 `source.change_request` capability，它不要求 `selfRepairMode: "full"`，但仍要求 `allowSourceCodeSelfRepair: true`。未授权时系统只写入需要授权的审计记录和消息，不创建可执行源码修改 intent。

## 模型调用调试日志

系统支持本地调试模式，配置键为 `modelDebugMode`，默认值是 `false`。可在浏览器侧栏“调试模式”开关中启用，也可直接写入 `skills/styles/settings.json`：

```json
{
  "modelDebugMode": true
}
```

开启后，每次大模型调用都会把实际发送给模型的完整原始 prompt，以及模型进程返回的完整 stdout、stderr、returncode 或异常信息写入当前存档目录的 `debug/model_calls/` 下。单次调用日志位于 `debug/model_calls/<round_id>/`，全局索引位于 `debug/model_calls/index.jsonl`。这些日志可能包含 GM-only 隐藏设定、角色私有记忆、用户指令和模型原始输出，只用于本地调试，不会进入 agent 上下文，也不应提交到仓库。

## 常用开发命令

```powershell
python skills/start_server.py .
python skills/import_prepare.py "<卡片文件夹>" "."
python skills/round_prepare.py "<卡片文件夹>" "."
python skills/round_deliver.py "<卡片文件夹>" "."
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/<file>.py
cd skills; npm install
```

`npm install` 只用于安装 MVU 服务依赖 `zod`。`skills/package.json` 里的 `npm test` 是占位命令，会直接失败。修改 Python 或前端桥接逻辑后优先运行 `python -m unittest discover -s tests -v`，并对改动过的 Python 文件运行 `python -m py_compile`。修改前端后请启动本地服务并在 `http://localhost:8765` 和局域网 URL 各做一次浏览器检查。

`python skills/control_plane_smoke.py --repo .` 会运行确定性、无 live model 的多 agent 控制面冒烟测试，并验证 critic 通过后先执行 `run_postprocess`，再进入 `deliver_round`。

## 最终验收

- `python -m unittest discover -s tests -v`
- `python skills/control_plane_smoke.py --repo .`
- `python -m py_compile skills/agent_dispatcher.py skills/agent_actor_runtime.py skills/agent_messages.py skills/agent_intents.py skills/agent_snapshots.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/input_routing_requests.py skills/capability_registry.py skills/replay_capabilities.py skills/character_registry.py skills/rp_generate_cli.py skills/self_repair.py skills/agent_executors/__init__.py skills/agent_executors/input_executor.py skills/agent_executors/actor_executor.py skills/agent_executors/delivery_executor.py`
- 启动 `python skills/start_server.py .`，确认 `http://localhost:8765` 可访问。
- 用手机或其他同一局域网设备访问启动输出中的 LAN URL。
- 在 Claude Code 中对空白文件夹运行 `/rp`，完成至少 5 个玩家回合；检查玩家输入即时显示、重要角色独立对话框、进度更新、UI/图片热刷新，以及在玩家决策点停止。

## 数据与安全

不要提交卡片文件夹、聊天记录、记忆、生成图片、运行时状态或本地密钥。重点保持以下文件在版本控制外：

```text
chat_log.json
memory/
generated/
image_config.local.json
skills/image_config.local.json
.image_api.json
*.secret.json
skills/styles/content.js
skills/styles/state.js
skills/styles/.pending
skills/styles/progress.json
.pending_user_turn.json
.player_inputs.jsonl
.player_input_edits.jsonl
.player_input_branches.jsonl
.agent_runs/
```

仓库内的主要源代码是 `skills/`、`.claude/`、`CLAUDE.md`、`README.md` 和相关参考文档；用户运行产生的存档数据应只留在本机。
