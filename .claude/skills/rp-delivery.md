---
name: rp-delivery
description: RP 交付代理：最终写出 response.txt 并触发交付脚本
---

## Delivery Rules

- 仅主编排层负责执行本代理流程：
  - 最终写入 `skills/styles/response.txt`。
  - 调用 `python skills/round_deliver.py` 完成后处理。
- 禁止其他代理直接写 `response.txt` 或执行交付脚本。
- 禁止普通来源文件（`content.js`, `state.js`, `chat_log.json`）在此阶段直接改写。

## Final File

- 读取 `final.response.txt`，先镜像到 `skills/styles/response.txt`。
- 随后由 orchestrator 触发 `python skills/round_deliver.py "<card_folder>" "."` 进行交付。
