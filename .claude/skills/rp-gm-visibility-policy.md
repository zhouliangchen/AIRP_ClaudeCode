---
name: rp-gm-visibility-policy
description: Use when the GM prepares actor calls, perception feedback, hidden-fact disclosure, or scene information boundaries.
---

## RP GM Visibility Policy

Use this policy whenever GM-facing knowledge must be projected into actor-facing work. The GM may know hidden facts, future pressure, author instructions, and private world truth, but actor-facing fields must preserve what the actor can actually perceive or infer in the current scene.

## Actor-Facing Boundary

`actor_calls[].prompt`, `actor_calls[].reason`, generated perception feedback, and dialogue transfers must contain second-person visible situation only. They describe what the actor can see, hear, feel, remember, or receive as direct speech in-world.

These fields must not contain hidden facts, foreshadowing hints, or euphemistic substitutes. Do not leak secrets through suggestive wording, suspicious emphasis, meta labels, genre hints, or "you feel this matters" phrasing unless the character has a concrete visible reason to know it.

## Disclosure

Hidden facts become actor-visible only after an in-world event exposes them. When disclosure happens, state the visible evidence or spoken line, not the GM's hidden reasoning. If the GM is unsure whether a fact is visible, keep it in GM-only notes and ask for perception or dialogue that can reveal it later.
