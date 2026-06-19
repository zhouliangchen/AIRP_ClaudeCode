# RP Control-Plane Hardening Design

## Status

Confirmed by the user on 2026-06-19.

This spec is the second-stage hardening design for the multi-agent RP refactor. It does not replace the current GM loop, actor context projection, subGM side-thread runner, story composition, critic gate, or memory ingestion pipeline. It tightens the control plane so the intended tabletop-style roleplay semantics are enforced by file protocols, validators, and schedulers instead of relying primarily on prompt discipline.

## Background

The current implementation already contains the core pieces required by the earlier refactor:

- GM-driven interactive turn loop with repeated actor calls.
- Actor context projection for player and character agents.
- Structured actor outputs with action, dialogue, perception request, memory delta, goal update, wait, and stop events.
- subGM side threads with main-GM-owned boundaries and character reservations.
- Story and Critic stages before delivery.
- Post-delivery actor memory delta ingestion and scheduled memory summaries.

The remaining issues are not architectural absence. They are weak enforcement points:

- Important-character context generation is capped by the same setting used for parallelism.
- Mainline actor calls are still executed as a serial queue even when `parallel_groups` declares independent calls.
- Actor-visible context filtering has hidden-field and hidden-phrase protection, but lacks strong spatial, sensory, and recipient visibility proof.
- Perception requests and dialogue transfers exist, but their continuation behavior is thin.
- Post-delivery memory organization is scheduled, not guaranteed for every actor that participated in the delivered turn.
- GM rules are mostly prompt-level and need schema plus validator support for hidden-fact leakage, actor routing, and visibility boundaries.

## Goals

- Preserve the current file-mailbox architecture and current-format-only development policy.
- Make important-character participation independent from parallel execution limits.
- Make GM-declared actor parallelism executable when safe and safely downgraded when unsafe.
- Require actor-facing information to carry explicit visibility proof.
- Ensure perception feedback and important-character dialogue transfer form a closed interaction loop.
- Ensure participating actors get self-perspective post-round memory organization without blocking already approved delivery.
- Keep subGM authority scoped and auditable.
- Add focused tests for every behavior change, with no live model dependency.

## Non-Goals

- Do not build a full physical simulation engine.
- Do not add compatibility branches for old generated `.agent_runs/` artifacts.
- Do not let Story directly mutate authoritative character profile, background, personality, or body facts.
- Do not let subGM create or promote important characters, spawn other subGMs, include the player character, or modify its own boundary.
- Do not make prompt wording the only enforcement mechanism for critical permissions.

## Design Overview

The recommended path is protocol-first hardening:

1. Decouple important-character context registration from actor parallelism.
2. Replace the mainline serial actor queue with validated actor batches.
3. Extend visible events and actor calls with visibility proof.
4. Close perception and dialogue continuation loops.
5. Add bounded extensibility for custom actor actions.
6. Convert actor memory handling into a two-stage post-delivery job model.
7. Split role-state write authority between preprocess, GM, actor, Story, and delivery validators.
8. Add subGM boundary proof and merge discipline.
9. Harden GM behavior through prompt, schema, and validator layers.

## P0: Important-Character Context Registration

`max_parallel_subagents` must no longer limit how many important-character context files are generated.

`round_prepare` should:

- Generate context packets for every registered important character.
- Keep the blank `_self` bootstrap behavior.
- Use passive card-structure characters only as fallback candidates when no explicit important-character registry exists.
- Treat `max_parallel_subagents` only as a runtime dispatch limit.

The GM may call any registered important actor. Actor calls to unregistered actors remain filtered or rejected according to existing control-plane policy.

Expected tests:

- More than two registered important characters all receive context packets.
- A GM call to the third or later registered character is accepted.
- Parallelism limits constrain dispatch batch size, not registration.

## P1: Mainline Actor Batch Scheduling

The mainline GM loop should turn `actor_calls` and `parallel_groups` into validated dispatch batches.

Batch construction rules:

- Calls in the same valid `parallel_group` may be dispatched together.
- Calls not in a valid group are dispatched serially in GM order.
- Calls for the same actor are always serial.
- Calls that target actors reserved by active side threads are rejected before dispatch.
- Calls with dialogue, perception, or explicit dependency links that can affect another call must be serial.
- Invalid parallel declarations are downgraded to serial and recorded as routing warnings unless they indicate a permission violation.

Batch merge rules:

- All actor outputs in one safe batch are validated independently.
- Batch-visible events are merged after all outputs complete.
- Dialogue transfers and perception continuations generated by a batch are scheduled after the batch, not injected into a still-running batch.
- `source_call_id` and trace ordering remain deterministic.

Expected tests:

- Independent actors in a valid group are dispatched in one batch.
- Same-actor calls are forced into serial batches.
- Dialogue target dependencies force serial continuation.
- subGM-reserved actor calls are rejected.
- Trace output records actual batch decisions and downgraded parallel groups.

## P2: Visibility Proof and Actor Projection

Actor-facing information must be selected by structured visibility rules, not by broad `world_visible` inclusion alone.

Visible events should support these fields:

- `scene_id`
- `location`
- `time_window`
- `visible_to`
- `sensory_channels`
- `source_actor`
- `target_actor`
- `visibility_basis`

`actor_calls` should include a compact `visibility_basis` explaining why the actor can perceive, receive, or reasonably infer the situation described in the second-person prompt.

Projection rules:

- Hidden facts, GM-only notes, user-instruction facts, and world truth remain unavailable to actors unless an in-world disclosure event makes them visible.
- Public events are visible only when they are marked public or when their recipient/location/sensory metadata matches the actor.
- Private dialogue is visible to the speaker, target, and explicitly listed witnesses only.
- Perception feedback must describe evidence available to the actor, not hidden causality or authorial intent.
- If visibility cannot be proven, the information stays GM-only.

The visibility guard remains useful as a final text redaction layer, but the primary defense becomes projection-time selection plus schema validation.

Expected tests:

- A character in another location does not receive local visual events.
- A public broadcast can reach all configured recipients.
- Private dialogue reaches only the target and explicit witnesses.
- Hidden facts do not leak through `actor_calls[].prompt`, `actor_calls[].reason`, `visibility_basis`, or visible events.

## P3: Perception and Dialogue Closure

Perception requests should produce a required GM feedback continuation.

Flow:

1. Actor emits `perceive_request`.
2. The loop records a pending perception request with actor, source call, requested channel, and visible query text.
3. The next GM step must either answer with visible sensory feedback or explicitly mark that no further actor continuation is needed.
4. When feedback is provided, the originating actor is automatically re-called with the feedback unless the GM stops for a real player decision or the turn ends.

Important-character dialogue transfer should become structured:

- `speaker`
- `target`
- `exact_visible_words`
- `delivery_channel`
- `visible_tone_or_action`
- `source_call_id`

GM and Story must not invent core responses for important characters when a valid actor call is possible. They may only frame the scene, transfer visible dialogue, or stop at a waiting point.

Expected tests:

- Perception feedback automatically schedules a continuation call for the requesting actor.
- GM can explicitly close a perception request without continuation when justified.
- Dialogue transfer preserves visible wording and drops private intent.
- Duplicate dialogue transfers are deduplicated.
- Transfer limit exhaustion stops safely with a clear stop reason.

## Bounded Custom Actor Actions

A fully open `other` event type would weaken validation. Instead, actors should get a structured extension event, such as `custom_action` or `intent`.

Required fields:

- `category`
- `visible_content`
- `requires_gm_resolution`
- `risk_level`
- `target`

Rules:

- Hidden private reasoning cannot be placed in visible fields.
- High-risk or irreversible player-character actions must stop for the real player.
- Character-agent custom actions can resist the intended plot when supported by that character's memory and goals, but GM still resolves world consequences.

Expected tests:

- Valid custom actions enter the trace.
- Hidden-marker content in visible custom-action fields is rejected.
- Player-agent critical custom actions trigger `stop_for_player_decision`.

## Two-Stage Actor Memory Jobs

Memory should remain actor-perspective and self-owned, but organization should happen after delivery.

Stage 1: delivery-time ingestion

- Approved Story output is delivered normally after Critic passes.
- Actor `memory_delta` and `goal_update` events are ingested into `recent.md` as they are today.
- Delivery is not rolled back solely because post-round organization fails.

Stage 2: post-round actor memory jobs

- Every actor that participated in the delivered turn receives a `post_round_memory_job`.
- The job input contains only that actor's context-safe material: its actor outputs, visible events, approved dialogue transfers, own recent memory, and own goals.
- The job may update `short_term.md`, `key_memories.md`, `long_term.md`, and `goals.json`.
- The job must not update profile, background, personality, body facts, authoritative settings, hidden facts, or another actor's memory.

Failure handling:

- A failed post-round job marks the run as `degraded_memory_state`.
- The next `round_prepare` must either retry pending jobs or surface the degraded state instead of silently continuing.
- Already delivered player-facing prose remains delivered.

Expected tests:

- Only participating actors receive jobs.
- Jobs reject GM-only and hidden markers.
- Jobs reject profile/background/personality/body-fact writes.
- Failed jobs do not delete delivered prose.
- Next-round preparation detects pending or failed memory jobs.

## Write Authority Boundaries

Role-state mutation authority should be explicit:

- Preprocess may persist player-authoritative input corrections, hidden settings, important-character declarations, and minimal initial memory or goal patches.
- Main GM may update local world state, apply important-character promotions, and create or control subGM side threads.
- Actor agents may update only their own memory and goals through validated event types.
- Story may output narrative-backed suggestions, but cannot directly persist actor memory, goals, profile, background, or hidden facts.
- Delivery and memory validators decide what becomes durable state.

This prevents Story from turning literary composition into authoritative character cognition.

## subGM Boundary Proof and Merge Discipline

The current subGM model should be retained with additional proof fields.

subGM scene beats and events should carry:

- `thread_id`
- `boundary_id`
- `scene_id`
- `time_window`
- `location`
- `visibility_basis`

Rules:

- subGM can only advance its assigned boundary.
- Actor calls must target existing important characters listed in `allowed_characters` and not listed in `forbidden_characters`.
- Active side-thread reservations prevent the same actor from participating in the mainline or another active side thread.
- `promotion_requests` and `boundary_requests` remain requests for main-GM arbitration.
- Unmerged side-thread facts remain off-screen material and do not become player-character visible facts.
- Main GM decides which side-thread facts are merged, exposed, paused, accelerated, or closed.

Expected tests:

- Out-of-boundary beats are rejected or mark the side thread blocked.
- Player actor calls from subGM are rejected.
- Non-merged side-thread facts do not become mainline actor-visible facts.
- Merge decisions are source-backed by side-thread outputs or messages.

## GM Hardening Layers

GM behavior should be enforced in three layers:

1. Prompt layer: clearer `.claude/skills` instructions for actor routing, visibility policy, perception feedback, dialogue transfer, promotion policy, and subGM boundary control.
2. Schema layer: required fields for actor-call visibility proof, safe parallel grouping, side-thread boundary proof, and structured continuation decisions.
3. Validator layer: rejection or downgrade for hidden-fact hints, unsupported visibility, invalid actor participation points, illegal parallel groups, missing dialogue transfer, and missing perception continuation.

This is necessary because prompt-only rules cannot reliably prevent subtle hidden-fact leakage or authorial framing.

## Implementation Order

The work should be split into small implementation plans:

1. P0: Important-character registration and dispatch-limit decoupling.
2. P1: Mainline actor batch scheduling and actual parallel dispatch.
3. P2: Visibility proof model and stricter actor projection.
4. P3: Perception/dialogue closure and post-round memory jobs.

subGM boundary proof and GM skill hardening can be added alongside P1 and P2 when their touched files overlap.

## Acceptance Criteria

- Full unit test suite passes with no live model calls.
- Deterministic control-plane smoke passes.
- More than two important characters can be registered and called in one round.
- Mainline actor parallel groups execute as batches only when safe.
- Actor packets contain no global hidden context and only include visibility-proven events.
- Perception requests and important-character dialogue transfers produce explicit continuations or safe stops.
- Participating actors receive post-round memory jobs with actor-perspective-only inputs.
- subGM outputs remain scoped to assigned boundaries and require main-GM arbitration for promotions or boundary changes.
- README and relevant non-superpowers docs are updated only if implementation changes commands, architecture, or user-facing workflow.

## Risks and Mitigations

- Risk: Visibility metadata may make GM outputs more verbose.
  - Mitigation: keep fields compact and use defaults only for explicitly public events.
- Risk: Parallel dispatch can create nondeterministic trace ordering.
  - Mitigation: merge batch outputs in deterministic call order and preserve `source_call_id`.
- Risk: Post-round memory jobs can delay the next turn.
  - Mitigation: do not block delivered prose, but require next-round detection and retry/reporting.
- Risk: Validators may initially reject useful creative outputs.
  - Mitigation: add routing warnings and safe serial downgrades where possible; reserve hard rejection for permission, hidden-leak, and state-corruption risks.

## Open Decisions Resolved

- The project should prioritize enforceable control-plane changes over prompt-only style changes.
- `max_parallel_subagents` is a concurrency setting, not an important-character count.
- Story should not directly persist role cognition.
- A bounded `custom_action` or `intent` event is safer than an unrestricted `other` event.
- subGM remains omniscient but strictly scoped to assigned side-thread boundaries.
