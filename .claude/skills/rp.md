---
name: rp
description: Use when starting or resuming this repository's Claude Code direct-driven RP workflow from /rp.
---

## RP Entry

This project is a Claude Code direct-driven RP engine. Claude Code is the live orchestration layer that runs scripts, dispatches subagents, reads and writes file-mailbox artifacts, and performs final quality control.

Load this entry first, then load `rp-orchestrator` for the current turn workflow. Load the other RP skills only when their stage is needed.

## Stage Skills

- `rp-orchestrator`: turn coordination, scripts, subagent dispatch, repair loops, and delivery gates.
- `rp-input-analyst`: semantic input analysis before persistence or actor routing.
- `rp-input-router`: split player-facing role text from authorial or system-level instructions.
- `rp-context-projector`: build GM, player, and character contexts with strict visibility boundaries.
- `rp-gm-agent`: complete-context world simulation and actor-call scheduling.
- `rp-player-agent`: first-person player-character embodiment.
- `rp-character-agent`: first-person important-character embodiment.
- `rp-story-agent`: final prose composition from approved artifacts.
- `rp-critic-agent`: strict review before delivery.
- `rp-delivery`: mechanical mirroring to `skills/styles/response.txt` through `round_deliver.py`.
- `rp-assets-ui`: optional non-blocking image and per-card UI enhancement.

## Core Contract

The main agent coordinates; subagents create routine narrative and character embodiment. The main agent must not directly write normal story prose unless the fallback is explicit and recorded.

Use `.agent_runs/<round>/` as the authoritative turn mailbox. Preserve `gm.output.json`, `actor.outputs.json`, `story.input.json`, `story.output.json`, `critic.report.json`, `<character_dialogues>`, `source="subagent"`, and `round_deliver.py`.
