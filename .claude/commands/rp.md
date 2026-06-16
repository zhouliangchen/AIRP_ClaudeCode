Run `rp-orchestrator` for this card.

- 当前目录先判断启动模式：新卡开局 / 有 chat_log+memory 续玩 / 空白卡模式。
- 进入 `rp-orchestrator`，完成启动模式分支后进入运行循环。
- 主编排层不直接编写常规叙事正文，只交由子技能流程落到 `response.txt` 交付。
