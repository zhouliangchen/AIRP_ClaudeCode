---
name: rp
description: RP 入口技能，委托到 rp-orchestrator。
---

Run `rp-orchestrator` for the per-turn RP pipeline.
The main orchestrator (Claude Code) should not directly craft routine narrative content here; it should invoke subskills and only write the final `skills/styles/response.txt` through `rp-delivery`.

