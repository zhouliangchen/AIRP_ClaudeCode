---
name: rp-context-projector
description: Use when preparing GM, player, or character subagent context that must enforce knowledge boundaries.
---

## RP Context Projector

Projection decides what each agent is allowed to know. It is the main defense for immersion, role independence, and instruction leakage.

## Visibility Matrix

| Agent | May Receive | Must Not Receive |
| --- | --- | --- |
| GM | complete剧情, hidden facts, user_instruction_channel, memory, variables, worldbook, all prior summaries | none, except private real-user data unrelated to the RP |
| Player | strict first-person player knowledge, role_channel, perceived scene, body state, remembered facts, current choices | user_instruction_channel, GM plans, hidden world truth, "玩家/Claude Code/系统" framing |
| Character | strict first-person character knowledge, own memory, own goals, own senses, own misconceptions, visible player action | player private intent, user_instruction_channel, GM notes, other characters' hidden thoughts |
| Story | all subagent outputs and delivery contract | raw hidden chain-of-thought |
| Critic | full candidate, all contracts, full context needed for audit | none within project data |

## Projection Rules

- GM agent 可以接收完整剧情.
- Player and character agents get 严格独立的第一人称视角.
- Never leak `user_instruction_channel` into player/character packets unless the instruction has become a world-visible fact; 换言之, 不得泄露出戏指令、GM 隐藏真相或 Claude Code 工作流信息。
- Use `world-visible` labels for facts that characters can perceive this turn.
- Preserve wrong beliefs. If a character misunderstands something, pass the misconception, not the omniscient truth.
- Include sensory affordances: what the role can see, hear, touch, smell, remember, and plausibly infer.
- Include memory from `memory/characters/<safe_name>/` only for that character.
- Keep packets compact. Prefer local facts over long global summaries for speed.

## Output Requirements

For each projected packet, state:

- `visibility`: `full_story`, `first_person_player`, or `first_person_character`.
- `known_facts`
- `sensory_context`
- `private_memory`
- `misconceptions`
- `forbidden_knowledge_removed`
- `world_visible_changes`
