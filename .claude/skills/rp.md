---
name: rp
description: Use when starting or resuming this repository's Claude Code entry plus LLM API runtime RP workflow from /rp.
---

## RP Entry

This project keeps Claude Code as the RP entry, maintenance, script orchestration, and final quality-control agent. Runtime RP subagents are executed by the Python runtime through configured LLM APIs, while the `.agent_runs/<round>/` file-mailbox remains the control-plane authority.

Core boundary:

- 主 agent 只负责编排, tool/script execution, code or prompt maintenance, artifact collection, final gating, and delivery.
- Routine narrative writing and role embodiment must be delegated to GM, player, character, story, critic, postprocess, projection, and assets-ui runtime agents through `llm_runner.run_llm_agent()`.
- If the configured LLM provider is unavailable, block or route repair explicitly; do not silently fall back to main-agent narrative writing.
- Main-agent skills are loaded on demand through `rp-orchestrator`; runtime agent prompt contracts live under `skills/agents/**/prompts/` and are materialized by Python.

Enter `rp-orchestrator` first. It selects startup mode, runs the required Python pipeline, creates `.agent_runs/<round>/` artifacts, uses main-agent skills only for orchestration/delivery/fallback work, and delivers only after critic approval.
