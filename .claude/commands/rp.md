Run `rp-orchestrator` for this card.

- 当前目录先判断启动模式：新卡开局 / 有 `chat_log.json` + `memory/` 续玩 / 空白卡模式。
- 进入 `rp-orchestrator`。它按需导入 `rp-input-router`、`rp-context-projector`、GM/player/character/story/critic/delivery 等阶段 skill。
- 主 agent 只负责 Claude Code 直驱编排、脚本运行、subagent 调度、系统迭代和最终质检；常规叙事创作与角色扮演任务必须交给 subagent。
- 每轮以 `.agent_runs/<round>/` 作为文件邮箱，critic 通过后才镜像到 `skills/styles/response.txt` 并执行交付。
