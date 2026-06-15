在当前目录下启动话本RP流程。

## 第一步：扫描当前目录

查找素材文件（PNG角色卡、JSON世界书、TXT小说）：
- Glob 搜索 `*.png`, `*.json`, `*.txt`
- 同时检查 `chat_log.json` 和 `memory/` 是否存在

## 第二步：根据扫描结果执行

### 情况 A — 有素材，无 chat_log.json（新卡开局）

按 CLAUDE.md「自动启动流程」步骤 0-8 完整执行：

0. 清理残留 Python 进程，确认端口 8765 空闲
1. 启动桥接服务器 `python skills/server.py &`
2. 写入卡片路径到 `skills/styles/.card_path`
3. 按 CLAUDE.md「自动启动流程」步骤 1-6 完整执行（导入管线 → 服务器 → 上下文 → ScheduleWakeup 输入监听 → 开局交付）
4. 告知用户：「前端已就绪，本机打开 http://localhost:8765；同一局域网设备打开启动输出 urls 中的 http://<本机局域网IP>:8765」「在输入框打字，点提交即可」

### 情况 B — 有 chat_log.json + memory/（老卡续玩）

加载 chat_log 和 memory/ 下所有记忆文件（除 reference.md 外）重建完整叙事上下文。
告知用户当前剧情进度（从 project.md 摘要 + 最近 3 轮对话概括）。
按 CLAUDE.md「自动启动流程」步骤 2-4 恢复运行（启动服务器 → 读上下文 → ScheduleWakeup 输入监听），继续等待用户输入。

### 情况 C — 无任何素材文件（空白角色卡模式）

直接按 CLAUDE.md「自动启动流程」步骤 1-5 执行。导入管线会自动创建临时空白角色卡：
- 写入 `.card_data.json`、`.initvar.json`、`memory/`、`memory/characters/_self/`、`ui_manifest.json`
- 不生成 AI 开局，不执行 `handler.py --opening`
- 启动前端和输入监听后，等待用户在浏览器输入第一轮开局设定或行动
- 每轮会沉淀到 `memory/characters/_self/` 和 `.card_data.json.evolving_profile`，逐步形成自定义角色卡

告知用户：
> 当前目录没有素材，已进入空白角色卡模式。
