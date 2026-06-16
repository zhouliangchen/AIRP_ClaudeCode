---
name: rp
description: RP 入口技能，委托到 rp-orchestrator。
---

## Entry

Delegate startup-mode selection, startup/bootstrap, and per-turn orchestration to `rp-orchestrator`.
The main orchestrator (Claude Code) should not directly craft routine narrative content here; it should invoke subskills and only write the final `skills/styles/response.txt` through `rp-delivery`.
