---
name: rp-delivery
description: RP 交付代理：最终写出 response.txt 并触发交付脚本
---

## Delivery Rules

- 仅主编排层负责执行本代理流程。
- 本代理只做交付文件落盘与校验，不承担 `round_deliver.py` 执行。
- 最终写入 `skills/styles/response.txt`。
- 禁止其他代理直接写 `response.txt` 或执行交付脚本。
- 禁止普通来源文件（`content.js`, `state.js`, `chat_log.json`）在此阶段直接改写。

## Final File

- 读取 `final.response.txt`，校验响应标签后镜像到 `skills/styles/response.txt`。
- 待 `skills/styles/response.txt` 就绪后，由 orchestrator 触发 `python skills/round_deliver.py "<card_folder>" "."` 进行最终交付。
