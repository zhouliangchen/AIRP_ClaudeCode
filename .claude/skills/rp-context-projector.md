---
name: rp-context-projector
description: Use when preparing GM, player, or character subagent context that must enforce knowledge boundaries.
---

## RP Context Projector

Projection decides what each agent is allowed to know. It is the main defense for immersion, character independence, and instruction leakage.

## Visibility Model

- GM agent may receive full story state, hidden facts, user instructions, worldbook context, variables, and all relevant history.
- Player and character agents receive strictly first-person projected context.
- Actor projections may include sensory context, visible events, self memory, current goals, misconceptions, and GM-provided second-person prompts.
- Actor projections must not include `user_instruction_channel`, GM hidden notes, world truth, prompt mechanics, full recent chat, other characters' private thoughts, files, or Claude Code internals.

## Required Projection Fields

Actor packets should expose:

- `actor_id`
- `visibility`
- `gm_prompt`
- `self_knowledge`
- `memory`
- `sensory_context`
- `visible_events`
- `misconceptions`
- `forbidden_removed`

Use `world-visible` or equivalent trace visibility only for events that may be used in final prose. Private reasoning and hidden facts are audit material, not story text.
