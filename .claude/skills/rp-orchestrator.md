---
name: rp-orchestrator
description: Use when /rp starts, a pending RP input is detected, or a multi-agent RP turn must be coordinated.
---

## RP Orchestrator

You are the main Claude Code coordinator. Keep Claude Code as the direct driver, but keep the main agent out of routine fiction writing. Your job is workflow orchestration, script execution, subagent dispatch, artifact collection, repair loops, code/system iteration when authorized, and final delivery.

## Stage Selection

按需导入 stage skills. Load only what the current phase needs:

- Startup or resume: use this skill plus `rp-delivery` only if there is an opening to deliver.
- New player input: use `rp-input-router`, `rp-context-projector`, GM/player/character skills, `rp-story-agent`, `rp-critic-agent`, and `rp-delivery`.
- Pure user instruction, repair, or planning: load only router, GM/story/critic, and delivery as needed.
- Image or UI enhancement after text delivery: load `rp-assets-ui`.

## Startup Modes

- New card: run `python "{ROOT}/skills/start_server.py" "{ROOT}"`, then `python "{ROOT}/skills/import_prepare.py" "<card_folder>" "{ROOT}"`.
- Existing `chat_log.json` + `memory/`: run `python "{ROOT}/skills/start_server.py" "{ROOT}"`, then `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"` when a pending input exists.
- Blank bootstrap: run startup normally, then wait for the first browser input; do not invent a character before the player gives one.

## Per-Turn Flow

1. Run `python "{ROOT}/skills/round_prepare.py" "<card_folder>" "{ROOT}"`.
2. Read `skills/styles/round_context.txt` and the `AGENT_RUN` run directory.
3. Use `rp-input-router` to confirm `role_channel` and `user_instruction_channel`.
4. Use `rp-context-projector` to decide what GM, player, and character agents may see.
5. Dispatch GM, player, and relevant character subagents. Parallelize independent agents when possible to improve speed.
6. Run an interaction loop / 交互循环: agents may respond to each other's world-visible actions and dialogue through the orchestrator until a real player 关键决策点 is reached, the 章节字数 or scene target is met, or the critic says enough.
7. After GM/player/character outputs exist, build or request `story.input.json` as the canonical story bundle.
8. Ask `rp-story-agent` to compose `story.output.json` while preserving subagent agency.
9. Ask `rp-critic-agent` to write `critic.report.json`.
10. If hard failures exist, repair once through story/agent loop. If the failure is systemic and this is a development task or the user has authorized iteration, update prompts/code, rerun the turn, and document the change. Otherwise append the issue to `improvement_queue.jsonl`.
11. On approval, invoke `rp-delivery`; it runs `round_deliver.py`, which validates artifacts and mirrors approved story content to `skills/styles/response.txt`.

主 agent 不得直接撰写常规叙事正文 except as an explicitly marked fallback.

## Agent Run Artifacts

Use the current `.agent_runs/<round>/` folder as a mailbox:

- `input.json`: routed raw input and channel split.
- `gm.context.json` -> `gm.output.json`
- `player.context.json` -> `player.output.json`
- `characters/*.context.json` -> `characters/*.output.json`
- `story.input.json`
- `story.output.json`
- `critic.report.json`
- `manifest.json` with stages such as `awaiting_agent_outputs`, `story_ready`, `critic_passed`, `delivered`, or `blocked`

Only the orchestrator runs delivery. Subagents never write `skills/styles/response.txt`.

## Non-Negotiable Boundaries

- `.player_inputs.jsonl` is authoritative player data. Do not rewrite, trim, summarize, or delete it.
- Player and character agents must not receive hidden user instructions, GM notes, or out-of-world mechanics.
- GM may see complete剧情 and user instructions; player and character agents only see first-person projected facts.
- Stop at meaningful player choices. Do not silently decide relationship commitments, irreversible danger, consent-sensitive actions, or major plot direction for the player.
- Keep latency low: dispatch independent subagents in parallel, keep packet outputs compact, and defer image/UI work until after text delivery.
