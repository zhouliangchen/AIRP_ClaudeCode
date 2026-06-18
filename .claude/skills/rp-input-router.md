---
name: rp-input-router
description: Use when an RP turn contains player text, authorial settings, history edits, or mixed in-character and out-of-character instructions.
---

## RP Input Router

Every submission is authoritative, but not every sentence belongs to the same channel. Preserve raw text exactly, then split interpretation into independent channels.

## Channels

- `role_channel`: first-person action, first-person dialogue intent, and first-person synopsis of what the player character attempts.
- `user_instruction_channel`: third-person authorial settings, hidden facts, retcons, important-character declarations, direct Claude Code instructions, and system-level constraints.

The channels must not contaminate each other. Player and character agents may see only projected in-world effects, never raw hidden instructions, unless those effects have become perceptible in the story world.

## Routing Rules

- A first-person synopsis is still player-character intent. Expand it conservatively through the player agent.
- A third-person omniscient setting is not player-character knowledge by default.
- Retcons and history edits are authoritative user instructions. Route them to GM/story/critic repair context.
- Preserve exact raw input in `.player_inputs.jsonl`; do not trim, summarize, or rewrite it.
- Keep `role_channel` and `user_instruction_channel` in `input.json` for downstream artifacts.

## Output

The router supports the semantic `input_analysis.output.json` contract. It should classify intent and risk, not write fiction.
