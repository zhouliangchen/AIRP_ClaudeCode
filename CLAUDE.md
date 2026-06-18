# CLAUDE.md

This file guides Claude Code when it works inside this repository.

## Development Commands

Use PowerShell for manual project operation on Windows. Runtime cleanup and server checks rely on Windows process queries.

- Configure the local environment once: run `setup-claude-code.bat` from the repository root, or run `setup-claude-code.ps1` in PowerShell.
- Start an RP session from a card/story folder: launch `claude`, then run `/rp`.
- Start the bridge server manually: `python skills/start_server.py .`.
- Run the import/startup pipeline manually: `python skills/import_prepare.py "<card_folder>" "."`.
- Run a turn preparation pipeline manually: `python skills/round_prepare.py "<card_folder>" "."`.
- Deliver a generated response after `.agent_runs/<round>/story.output.json` and `critic.report.json` are ready: `python skills/round_deliver.py "<card_folder>" "."`.
- Run the deterministic no-live-model control-plane smoke test: `python skills/control_plane_smoke.py --repo .`.
- Process a response directly only for debugging: `python skills/handler.py "<card_folder>"`; add `--opening` for an opening turn.
- Run the MVU service directly only when debugging schema validation: from `skills/`, run `node mvu_server.js`.
- Install or update the MVU dependency: from `skills/`, run `npm install`.

Use `python -m unittest discover -s tests -v` for the repository test suite. `skills/package.json` defines `npm test` as a placeholder, so do not use it as primary verification. For narrow Python edits, add targeted checks such as `python -m py_compile skills/<file>.py`.

## Architecture Overview

This project turns Claude Code into the orchestration layer for a local role-playing engine. Users keep one card/story folder per RP, start Claude Code from that folder, and interact through the browser UI served at `http://localhost:8765` on the host machine or `http://<host-LAN-IP>:8765` from LAN devices.

The runtime is a Python standard-library bridge plus a small Node validation service:

- `skills/server.py` serves the frontend, accepts browser input, records authoritative player input in `.player_inputs.jsonl`, writes a pending user turn for immediate display, writes `skills/styles/input.txt`, and uses `.pending` as the Claude Code signal.
- `skills/start_server.py` launches `server.py`, checks port `8765`, starts the MVU service on `8766`, waits for readiness, and prints local/LAN URLs.
- `skills/import_prepare.py` prepares a card/story folder, initializes `.card_path`, `state.js`, `content.js`, `chat_log.json`, and writes `skills/styles/import_context.txt`.
- `skills/round_prepare.py` prepares each turn, writes `skills/styles/round_context.txt`, and creates `.agent_runs/<round>/` packets, prompts, and `manifest.json`.
- Claude Code dispatches RP subagents that write `gm.output.json`, `actor.outputs.json`, `story.output.json`, and `critic.report.json`.
- `skills/agent_workflow.py` gives deterministic next-action advice from the run manifest.
- `skills/agent_outputs.py` validates artifacts, assembles `story.input.json`, records `repair_history.jsonl`, appends system suggestions to `.agent_runs/improvement_queue.jsonl`, and mirrors approved story content to `skills/styles/response.txt`.
- `skills/round_deliver.py` gates approved output, updates memory, invokes `handler.py`, and completes the frontend delivery.
- `skills/control_plane_smoke.py` runs a deterministic no-live-model smoke that covers artifacts, trace, memory, and delivery.

Important turn flow: browser submit -> `.player_inputs.jsonl` and pending turn -> `round_prepare.py` -> `.agent_runs/<round>/` -> input analysis -> GM loop -> actor outputs -> `story.input.json` -> story and critic -> `round_deliver.py` -> `handler.py` -> browser refresh.

## RP Constitution

Claude Code remains the direct driver. This project is not a backend LLM API scheduler. The main agent coordinates scripts, subagent dispatch, artifact collection, repair loops, system iteration when authorized, and final delivery.

Routine narration and role embodiment must be delegated to subagents:

Load stage skills as needed; do not keep every detailed RP prompt in the main context by default.

- `rp-orchestrator` owns the turn workflow and file-mailbox control plane.
- `rp-input-router` separates `role_channel` from `user_instruction_channel`.
- `rp-context-projector` enforces first-person knowledge boundaries.
- `rp-gm-agent` simulates the complete-context world and schedules actor calls.
- `rp-player-agent` embodies the player character from projected first-person context.
- `rp-character-agent` embodies an important character from projected first-person context.
- `rp-story-agent` composes approved GM and actor artifacts into final prose.
- `rp-critic-agent` audits narrative quality, player authority, context isolation, and delivery safety.
- `rp-delivery` mirrors approved output to `skills/styles/response.txt` through `round_deliver.py`.
- `rp-assets-ui` handles optional non-blocking images and per-card UI enhancements.

The main agent must not directly write routine story prose unless subagents are unavailable and the fallback is explicit in the artifacts. Player and character subagents must not see hidden user instructions, full recent chat, GM-only notes, prompt mechanics, files, or Claude Code internals. GM may see full story state, hidden facts, and `user_instruction_channel`.

## Current Runtime Protocol

Each turn uses `.agent_runs/<round>/` as the file mailbox. Preserve current artifact names and JSON keys:

- `input_analysis.output.json`
- `gm.output.json`
- `actor.outputs.json`
- `story.input.json`
- `story.output.json`
- `critic.report.json`
- `interaction.trace.json`
- `repair_history.jsonl`
- `memory_summaries/*.summary.json`

The GM loop writes `gm.output.json` as a `gm_loop` wrapper with one or more GM outputs. Actor outputs are aggregated in `actor.outputs.json`; do not restore legacy `player.output.json` or `characters/*.output.json` contracts. `agent_outputs.py` validates GM/actor artifacts and builds `story.input.json`.

The story agent may polish, order, and bridge events, but it must not invent core responses for important characters. If an important character was dispatched, the final response may include `<character_dialogues>` entries sourced from that subagent. The runtime accepts dialogue objects with `source="subagent"` and keeps only subagent-sourced character dialogue boxes.

`round_deliver.py` is the delivery gate. It mirrors approved `story.output.json` content to `skills/styles/response.txt` only after required artifacts exist and the critic passes. If the critic returns `revise` or `block`, record the gate result, use `repair_history.jsonl`, and only apply system changes when the current task explicitly authorizes project iteration.

Images and per-card UI customization are optional immersion tasks. They must not block text delivery.

## Final Acceptance Checklist

- `python -m unittest discover -s tests -v`
- `python skills/control_plane_smoke.py --repo .`
- `python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py`
- Start `python skills/start_server.py .` and verify `http://localhost:8765`.
- Verify the printed LAN URL from a phone or other LAN device.
- In Claude Code, run `/rp` against a blank folder and complete at least five player turns. Check immediate player-input display, independent important-character dialogue boxes, progress updates, hot UI/image refresh, and stopping at player decisions.
