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
│  ├─ round_prepare.py      # 每轮上下文收集
│  ├─ round_deliver.py      # 每轮交付、质检和记忆更新
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

旧存档：检测到 `chat_log.json` 与 `memory/` 后，系统会恢复最近剧情、角色状态和用户偏好。

空白启动：当前文件夹没有 PNG/JSON/TXT 时，会创建 `.card_data.json`、`.initvar.json`、`memory/characters/_self/` 和 `ui_manifest.json`。系统不会自动生成 AI 开场，而是等待你在浏览器输入第一轮设定，之后逐轮沉淀角色卡、变量和记忆。

## 浏览器界面

桌面端会显示正文、插图、行动选项、输入框和侧栏配置。移动端默认只保留基础背景、对话历史、插图、下一步行动选择题和自定义输入框；右上角设置按钮会展开覆盖式侧边栏，用于调整文风、NSFW、人称、字数、玩家角色名、当前目标等进阶选项。

前端会轮询并重新加载 `content.js`，因此正文、状态 UI 和图片资产可以在不手动刷新的情况下更新。Claude Code 针对具体剧本生成的热更新 UI 不受移动端简化布局限制，可以按剧情需要插入到合适位置。

默认服务会监听所有网卡，便于手机、平板等同一局域网设备访问。如果只想允许本机访问，可在启动前设置 `$env:AIRP_HOST="127.0.0.1"`。若局域网地址仍打不开，请确认设备在同一网络，并允许 Windows 防火墙中的 Python 入站连接；也可用管理员 PowerShell 执行 `New-NetFirewallRule -DisplayName "AIRP ClaudeCode Frontend 8765" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private,Domain`。

## 图片与 UI 热更新

`skills/image_generate.py` 是 OpenAI-compatible 图片生成适配器，默认模型为 `gpt-image-2`。可用环境变量或本地配置提供密钥：

```powershell
$env:OPENAI_API_KEY="sk-..."
python skills/image_generate.py "<卡片文件夹>" --prompt "rainy seaside convenience store" --kind scene --target scene_illustration --async
```

也可在卡片文件夹、项目根目录或 `skills/` 下放置 `image_config.local.json`，或在项目根目录使用 `.image_api.json` 配置 `api_key`、`base_url`、`model`。这些文件已被忽略，不要提交。生成图片会写入当前卡片文件夹的 `generated/images/` 和 `.card_assets.json`，脚本会重建 `content.js`，已打开的浏览器会自动看到新图片。

单存档 UI 定制请写在卡片文件夹内的 `.beautify_template.html`、`.beautify.json`、`.regex_scripts.json` 和 `ui_manifest.json`。不要把全局 `skills/styles/index.html` 当作某张卡的定制层。

## 核心角色 subagent

`round_prepare.py` 每轮会生成 `skills/styles/character_contexts.json`，汇总核心角色的私有记忆、目标、近况和变量切片。Claude Code 工作流会在场景强相关时最多并行调用 2 个核心角色 subagent，让它们只从角色自身立场返回反应、隐藏意图、行动/台词候选、变量建议和记忆 delta。

subagent 不直接写 `response.txt`，也不直接交付前端；最终叙事仍由主代理整合。如果当前 Claude Code 环境不可用 subagent，会退回主代理兼任，但仍应参考 `CHARACTER_CONTEXTS` 保持角色独立性。

## 常用开发命令

```powershell
python skills/start_server.py .
python skills/import_prepare.py "<卡片文件夹>" "."
python skills/round_prepare.py "<卡片文件夹>" "."
python skills/round_deliver.py "<卡片文件夹>" "."
python -m py_compile skills/<file>.py
cd skills; npm install
```

`npm install` 只用于安装 MVU 服务依赖 `zod`。当前没有真实自动化测试套件，`skills/package.json` 里的 `npm test` 是占位命令，会直接失败。修改 Python 文件后至少运行 `python -m py_compile`；修改前端后请启动本地服务并在 `http://localhost:8765` 和局域网 URL 各做一次浏览器检查。

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
```

仓库内的主要源代码是 `skills/`、`.claude/`、`CLAUDE.md`、`README.md` 和相关参考文档；用户运行产生的存档数据应只留在本机。
