# 仓库指南

## 项目结构与模块组织

本仓库运行一个由 Claude Code 作为入口和维护者、本地 Python runtime 直接调用 LLM API 的 RP 引擎。核心 Python 运行时代码位于 `skills/`：`server.py` 和 `start_server.py` 提供浏览器桥接服务，`import_prepare.py` 初始化卡片/故事文件夹，`round_prepare.py` 创建每轮上下文，`input_analysis_apply.py` 校验语义输入分析，`round_deliver.py` 对已批准输出进行交付门控。`llm_settings.py`、`llm_provider.py` 和 `llm_runner.py` 负责 cc_switch Claude Code 本地反代、OpenAI-compatible 文本 API 和图片生成 API 的统一配置与调用；运行时 input-analyst、GM、story、critic、postprocess、subGM、player、character、projection 和 assets-ui agent 不再依赖一次性 Claude CLI subprocess。多 agent 文件契约由 `agent_dispatcher.py`、`agent_actor_runtime.py`、`agent_messages.py`、`agent_intents.py`、`agent_snapshots.py`、`agent_prompts.py`、`agent_outputs.py`、`agent_interactions.py`、`agent_memory.py`、`agent_schemas.py`、`input_analysis.py` 和 `character_registry.py` 处理；input analyst 会为普通 GM/story 处理之外的 system/UI/replay/card/source 工作发出 agent 驱动的 `capability_requests[]`。运行时通过声明式 capability registry 映射这些请求；Python 负责强制执行授权、ACL、artifact、projection、snapshot 和 delivery 边界，但不从玩家文本中决定语义路由策略。`agent_dispatcher.py` 消费 pending intents，在默认 live path 中显式执行 projection、actor 和 subGM thread intents，把权威 artifacts 写入 `artifacts/`，并阻断不安全或停滞的运行；`agent_actor_runtime.py` 拥有共享 actor 协作 helper；`agent_messages.py`、`agent_intents.py` 和 `agent_snapshots.py` 实现消息运行时、可执行 intent 生命周期和回滚快照；旧的 broad GM loops 如仍保留，只作为 helper/regression-only 路径，而不是默认运行时。浏览器资产和生成的运行时文件位于 `skills/styles/`；请把 `content.js`、`state.js`、`.pending`、`round_context.txt`、`progress.json`、`llm_settings.frontend.json` 和 `llm_settings.local.json` 视为运行时 artifacts，其中两个 `llm_settings.*.json` 文件必须保持 gitignored。Claude Code prompts 和 slash commands 位于 `.claude/`。测试位于 `tests/`。

## 构建、测试与开发命令

- `python skills/start_server.py .` 启动本地/局域网前端桥接服务。
- `python skills/import_prepare.py "<card_folder>" "."` 初始化卡片/故事文件夹。
- `python skills/round_prepare.py "<card_folder>" "."` 创建 `round_context.txt` 和 `.agent_runs/<round>/`。
- `python skills/input_analysis_apply.py "<card_folder>" "."` 校验 `input_analysis.output.json`，持久化已批准的设置/重要角色，并重建已路由的 agent packets。
- `python skills/round_deliver.py "<card_folder>" "."` 校验 agent artifacts，镜像已批准的 story 输出，并交付到前端。
- `python skills/image_generate.py "<card_folder>" --prompt "..." --kind scene --target scene_illustration` 使用统一图片 API 配置生成 assets-ui 图片资产。
- `python -m unittest discover -s tests -v` 运行完整测试套件。
- `python skills/control_plane_smoke.py --repo .` 运行确定性、无 live model 的多 agent 控制面冒烟测试。
- `python -m py_compile skills/<file>.py` 检查被修改的 Python 文件。
- `cd skills; npm install` 安装 MVU 的 `zod` 依赖。不要使用 `npm test`；它只是占位命令。

## 最终验收清单

- `python -m unittest discover -s tests -v`
- `python skills/control_plane_smoke.py --repo .`
- `python -m compileall -q skills` 编译当前实际存在的 Python 运行时代码。
- 启动 `python skills/start_server.py .`，并验证 `http://localhost:8765`，使用视觉识别确认前端界面显示良好，UI布局合理。
- 使用docs\测试样例-空白卡.md中描述的测试流程，能快速、无错误地完成一轮完整的测试。（仅在大规模修改后进行此项测试）
- 测试完成后，需主动关闭所有测试相关的进程服务。

## 代码风格与命名约定

除非项目已经依赖某个包，否则使用 Python 标准库代码。文件协议保持为显式 JSON 对象，使用稳定键名和 UTF-8 编码。优先使用小型 helper 模块，而不是把隐藏行为塞进 `round_prepare.py` 或 `round_deliver.py`。Python 函数和文件使用 snake_case；只有当现有中英混合用户可见文本属于已测试 UI、运行时输出或 prompt 契约时，才保留它们。

## 语义输入策略

代码不得通过固定关键词、子串或正则匹配来推断用户输入的意图，必须使用LLM进行语义分析。

## LLM 与 API 配置

运行时 agent 默认通过 `llm_runner.run_llm_agent()` 调用 LLM provider。cc_switch Claude Code 本地反代优先，OpenAI-compatible 文本 API 兜底；cc_switch 配置只允许 `enabled` 和 `service_url`，API key、模型名和上游 provider 映射由 CC Switch/Claude Code 本地配置维护。前端 API 设置页统一管理 cc_switch、OpenAI-compatible 文本 API 和图片生成 API，并写入 `skills/styles/llm_settings.frontend.json`；本地兜底配置统一写入 `skills/styles/llm_settings.local.json`。三类配置的字段优先级统一为：前端设置 > 环境变量 > 本地配置文件；代码不得再注入 API 默认值，三层均缺失时必须在前端 API 设置页报错。环境变量分三组：`AIRP_CC_SWITCH_ENABLED` / `AIRP_CC_SWITCH_SERVICE_URL`，`AIRP_OPENAI_COMPATIBLE_ENABLED` / `AIRP_OPENAI_COMPATIBLE_BASE_URL` / `AIRP_OPENAI_COMPATIBLE_API_KEY` / `AIRP_OPENAI_COMPATIBLE_MODEL`，以及 `AIRP_IMAGE_GENERATION_BASE_URL` / `AIRP_IMAGE_GENERATION_API_KEY` / `AIRP_IMAGE_GENERATION_MODEL`。图片生成只读取 `image_generation`，不会复用文本 `openai_compatible`，也不再读取旧 `image_config.local.json`、`.image_api.json`、`OPENAI_API_KEY`、`OPENAI_BASE_URL` 或 `IMAGE_MODEL`。

## 项目 Agent 记忆

在 `docs/` 下维护面向维护者的技术文档，在README.md中维护面向使用者的说明文档。每个任务开始时，先阅读与任务关联的相关文档，然后逐项审视用户请求再行动。用户指令可能不合理、不准确或不精确；拒绝不合理的请求，把不准确措辞纠正为技术上合理的目标，并执行纠正后的目标。

每个任务结束后，需更新相关文档，使其符合最新情况。更新过的文档需在最终输出中简要说明更新内容。

本项目仍处于活跃开发中，且随时面临重构。除非用户明确要求，否则不要为旧游玩存档、旧测试集或过时内部API添加兼容层。永远不要保留向前兼容的逻辑。优先考虑清晰、可维护的代码，而不是向后兼容的代码。任何过时的逻辑都应被删除。

默认用简体中文编写skill和文档，可以保留英文技术术语。编辑已有英文文档时，先将其直译为简体中文。

若发生文件/终端乱码问题，请在解决问题后，把解决方案记录到AGENTS.md，即本文件。

Do not rewrite UTF-8 source files that contain Chinese with PowerShell Get-Content + Set-Content; it can add a BOM or corrupt characters under the active console code page. Prefer apply_patch for edits, and use byte-level scripts only for mechanical fixes such as removing a UTF-8 BOM.

PowerShell 中用 here-string 管道给 `python -` 传递包含中文的内联脚本时，中文字面量可能被当前控制台编码替换成 `?`。需要做字符串断言时，优先在 Python 代码里使用 `\uXXXX` Unicode 转义，或先显式切换 `$OutputEncoding` 和 `[Console]::OutputEncoding` 为 UTF-8 后再传输中文脚本。

PowerShell 中运行 Python 并需要打印包含中文路径或中文 JSON 的 stdout 时，优先临时设置 `$env:PYTHONIOENCODING='utf-8'`，避免 Python 按当前控制台代码页输出导致终端显示乱码；这不影响文件本身的 UTF-8 内容。

在大规模重构时，在有临时备份的准备后，优先进行激进的删除、重构，而不是兼容修改迁移。跑通流程后，删除临时备份。

谨慎对数据进行结构化处理，过度的结构化会破坏prompt自然语言的连贯性，降低LLM的理解能力。

目前为项目的重构阶段，权威文档为docs\重构建议.md和docs\存档文件指南.md，优先级高于其他文档，发生冲突时修改其他文档；

## Commit指南

为本仓库创建 git commits 时，commit messages 使用规范的简体中文提交格式。不要提交docs/下的文件，但允许本地修改、添加git追踪。
