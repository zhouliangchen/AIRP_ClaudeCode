---
name: rp-gm-actor-routing
description: Use when the GM decides which player or character actors participate in a turn and how their calls are sequenced.
---

## RP GM Actor Routing

Use this policy when preparing `actor_calls`, interaction sequencing, and GM continuation after actor responses.

## Participation Points

Call an actor only when the current scene gives that actor a meaningful participation point: a direct player action to embody, a visible stimulus to perceive, a dialogue turn to answer, a goal conflict to resolve, or a decision boundary that depends on that actor's agency.

Do not call an important character merely because they exist in the cast. Do not ask an actor to confirm GM exposition or carry hidden setup.

## Serial Routing

Route actor calls serially when one actor's visible action, dialogue, or perception can change another actor's response. The later actor should receive only the updated visible situation and dialogue transfer, not the previous actor's private reasoning.

Use `source_call_id` in traces and downstream records when a response depends on a previous call.

## Metadata-Only Parallel Groups

`parallel_groups` is metadata only. It may declare that several calls can be dispatched together because they do not depend on each other's outputs, but it must not smuggle extra prompts, hidden facts, or scheduling instructions outside `actor_calls`.

## Dialogue Transfer

When a character says something that another actor can hear, transfer the spoken line as visible dialogue. Preserve the speaker, target if known, and concrete wording. Do not transfer private intent, hidden motives, or GM interpretation unless it is visible in tone, action, or wording.

## Perception Continuation

If an actor requests perception or waits for GM clarification, continue with visible sensory feedback only. The answer should let the actor decide what to do next without revealing hidden causality, future stakes, or authorial framing.

## Stop Reasons

Use `stop_reason` to mark the next control-plane step:

- `continue` when GM can safely continue routing or composing.
- `player_decision` when the next meaningful choice belongs to the real player.
- `word_target` when the scene has reached a requested chapter or response size target.
- `blocked` when required artifacts, contradictions, or visibility risks prevent safe delivery.
