---
name: rp-delivery
description: RP 交付技能，负责将最终响应写入 `skills/styles/response.txt` 并衔接交付。

---

## Delivery Rules

- Owner / trigger: `orchestrator` 调用 `rp-delivery`，用于在交付前对照 `final.response.txt`。
- 读取 `final.response.txt` 后，镜像到 `skills/styles/response.txt`，并执行：
  `python "{ROOT}/skills/round_deliver.py" "<card_folder>" "{ROOT}"`

- `orchestrator` 负责在每轮审稿通过后将 `final.response.txt` 写入并镜像到
  `skills/styles/response.txt`，随后触发 `rp-delivery` 执行该交付命令。
