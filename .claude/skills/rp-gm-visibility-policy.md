---
name: rp-gm-visibility-policy
description: Use when the GM prepares actor calls, natural-language perception feedback, hidden-fact disclosure, or scene information boundaries.
---

## RP GM Visibility Policy

Use this policy whenever GM-facing knowledge must be projected into actor-facing work. The GM may know hidden facts, future pressure, author instructions, and private world truth, but actor-facing fields must preserve what the actor can actually perceive or infer in the current scene.

## Actor-Facing Boundary

`actor_calls[].prompt`, `actor_calls[].reason`, generated perception feedback, and dialogue transfers must contain second-person visible situation only. They describe what the actor can see, hear, feel, remember, or receive as direct speech in-world.

These fields must not contain hidden facts, foreshadowing hints, or euphemistic substitutes. Do not leak secrets through suggestive wording, suspicious emphasis, meta labels, genre hints, or "you feel this matters" phrasing unless the character has a concrete visible reason to know it.

## Structured Visibility Proof

Actor-facing GM output must carry compact proof for why the actor can receive the information.

For `scene_beats[]`, `events[]`, and `actor_calls[]`, use these fields when applicable: `scene_id`, `location`, `time_window`, `visible_to`, `sensory_channels`, `source_actor`, `target_actor`, and `visibility_basis`.

`actor_calls[].visibility_basis` is required. It must explain why the target actor can perceive, receive, or reasonably infer the second-person prompt. Keep the summary concrete and visible: "Ada is in the classroom and can see the player's hand close" is valid; "Ada is near the hidden truth" is not.

If visibility cannot be proven, keep the information GM-only. Do not route it to an actor call, visible event, perception answer, or dialogue transfer.

## Perception Feedback

Perception feedback must answer only with evidence available to the requesting actor through the requested channel. Put that answer into a new natural-language `actor_calls[].prompt`; do not use `perceive_request`, `perception_responses`, or pending perception fields as actor-facing protocol. The prompt must not reveal hidden causality, author intent, future stakes, or user-instruction summaries.

## Disclosure

Hidden facts become actor-visible only after an in-world event exposes them. When disclosure happens, state the visible evidence or spoken line, not the GM's hidden reasoning. If the GM is unsure whether a fact is visible, keep it in GM-only notes and ask for perception or dialogue that can reveal it later.
