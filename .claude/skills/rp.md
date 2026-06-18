---
name: rp
description: Use when starting or resuming this repository's Claude Code direct-driven RP workflow from /rp.
---

## RP Entry

This project is a Claude Code 直驱 RP engine. Claude Code is not a thin client for a backend LLM API; it is the live orchestration layer that runs scripts, dispatches subagents, reads and writes file-mailbox artifacts, and performs final quality control.

Core boundary:

- 主 agent 只负责编排, tool/script execution, code or prompt maintenance, artifact collection, final gating, and delivery.
- Routine narrative writing and role embodiment must be delegated to GM, player, character, story, and critic subagents whenever the environment supports subagents.
- If subagents are unavailable, the main agent may use the same stage skills as a fallback, but must preserve the same context isolation and output artifacts.
- Stage skills are loaded on demand through `rp-orchestrator`; do not load every RP skill when a turn only needs startup, delivery, or a specific repair.

Enter `rp-orchestrator` first. It selects startup mode, runs the required Python pipeline, creates `.agent_runs/<round>/` artifacts, selectively imports the stage skills needed for the turn, and delivers only after critic approval.
