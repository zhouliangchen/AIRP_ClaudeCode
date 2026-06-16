---
name: rp-input-router
description: Use when an RP turn contains player text, authorial settings, history edits, or mixed in-character and out-of-character instructions.
---

## RP Input Router

Every submission is authoritative, but not every sentence belongs to the same channel. Preserve the raw text exactly, then split interpretation into independent channels that 互不干扰.

## Channels

`role_channel`:

- First-person immediate action: "我推门进去", "I take her hand".
- 第一人称剧情梗概 / first-person near-future synopsis: "我先稳住她, 之后带她离开".
- First-person emotional or sensory intent that the player character can actually feel.

`user_instruction_channel`:

- 第三人称上帝视角设定, world truth, hidden premise, future rule.
- Direct instruction to Claude Code, GM, pacing, style, rewrite, rollback, or repair.
- Important/core character declarations.
- Edits to prior AI-derived text, memory, variables, or setting.

## Mixed Input Policy

When a message contains both channels, keep order but split responsibility:

1. Preserve raw input unchanged in `input.json`.
2. Send only `role_channel` to player and character first-person packets.
3. Send `user_instruction_channel` to orchestrator, GM, story, and critic.
4. If user instructions change world facts, use `rp-context-projector` to decide which facts become world-visible this turn.

## Classification Notes

- A parenthesized sentence is not automatically instruction. "(I look away)" remains role-channel.
- "我重写符文" is role action, not a rewrite command.
- Explicit cues such as `系统指令:`, `用户指令:`, `上帝视角:`, `设定:`, `重要角色:`, `System:`, `Omniscient:` route to user instruction.
- For a first-person synopsis, story must expand the synopsis before advancing beyond it.
- For an action, story briefly reflects the action's immediate consequence before moving forward.
- For omniscient settings, update derived data and memory even if no in-world character currently knows the fact.

## Output Contract

Return or verify:

```json
{
  "role_channel": "...",
  "user_instruction_channel": "...",
  "components": [
    {"channel": "role", "text": "..."},
    {"channel": "user_instruction", "text": "..."}
  ]
}
```
