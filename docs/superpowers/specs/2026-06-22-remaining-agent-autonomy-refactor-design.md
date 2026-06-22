# Remaining Agent Autonomy Refactor Design

## Status

Drafted on 2026-06-22 after the message runtime and dispatcher workflow replacement plans were completed.

This spec consolidates the remaining refactor work from `docs/重构建议.md` and the previous architecture plans. It assumes these completed foundations are already available:

- `agent_messages.py` provides append-only messages, ACL checks, and inbox indexes.
- `agent_intents.py` provides executable intent lifecycle state.
- `agent_dispatcher.py` drives the live `/rp` path from pending intents.
- `agent_workflow.py` is removed from live next-action decisions.
- `artifacts/` is the authoritative control-plane artifact store for story and critic flow.
- snapshots, repair intents, rollback routing, schema-v2 progress, lifecycle cleanup, context versioning, and deterministic smoke coverage exist.

## Purpose

The remaining goal is not to replace another fixed workflow layer. That has already happened. The remaining goal is to make agent collaboration itself dispatcher-native.

The current runtime has a dispatcher-first main chain, but `run_gm_turn` still invokes the existing GM loop as a broad helper. That helper already contains mature behavior for projection, actor batches, subGM side threads, perception continuation, dialogue transfer, visibility proof, trace writes, and actor context versioning. The problem is boundary placement: those interactions are still mostly internal to the GM loop instead of being first-class messages and intents that the dispatcher can schedule, audit, retry, block, or extend.

The refactor must also reduce long-term code volume and control-flow duplication. New dispatcher-native paths should not simply wrap old workflow paths forever. Each phase should either delete the replaced branch, shrink it into a focused helper, or mark it as a temporary compatibility path with tests proving it is not the default live route.

This design turns the remaining work into one coordinated refactor:

1. Make projection, actor dispatch, and subGM dispatch real dispatcher executors.
2. Move assets/UI and system-improvement requests into nonblocking message/intent lanes.
3. Remove remaining control-plane root artifact and legacy stage assumptions where they still affect behavior or documentation.
4. Structurally simplify old loop and artifact code as dispatcher-native paths take over.
5. Verify the whole runtime with deterministic smoke plus a manual five-turn `/rp` acceptance run.

## Design Options Considered

### Option A: Finish Only Missing Dispatcher Executors

Implement `request_projection`, `run_actor`, and `run_subgm_thread` in `agent_dispatcher.py`, but leave assets, system requests, docs cleanup, and manual acceptance for later.

This is the fastest path to visible autonomy gains. The risk is that remaining non-text agents and improvement queues keep using ad hoc side channels, so the architecture remains partially split.

### Option B: One Consolidated Remaining-Refactor Program

Use one overarching spec and then implement it in staged plans: dispatcher-native collaboration first, then async assets/system request lanes, then cleanup and acceptance.

This is the recommended approach. The remaining work shares the same runtime contracts, so one spec prevents duplicated or conflicting definitions while still allowing implementation plans to stay small.

### Option C: Split Into Three Independent Specs

Create separate specs for dispatcher-native collaboration, assets/system agents, and cleanup/verification.

This gives tighter documents but weakens interface consistency. It is only preferable if implementation must be delegated to unrelated teams. For this repository, the shared dispatcher/message/artifact boundary is more important than document granularity.

## Recommended Architecture

Keep `agent_dispatcher.py` as the only live next-action engine, but reduce the size of the `run_gm_turn` executor.

Use a "replace then remove" discipline:

- Extract stable domain helpers only when multiple executors need them or when extraction lets an old monolithic branch disappear.
- Avoid adding permanent adapter layers whose only purpose is preserving old root-artifact or fixed-loop behavior.
- After an executor becomes authoritative, remove the old decision branch in the same phase or record a specific follow-up cleanup task.
- Prefer smaller modules with explicit contracts over one growing dispatcher or one growing turn-loop file.

The target split is:

- `run_gm_turn`: dispatches GM, validates GM output, persists GM progression, records GM-origin messages, applies allowed GM state changes, and creates follow-up intents.
- `request_projection`: materializes actor-safe projected content from a GM/subGM actor request.
- `run_actor`: dispatches `player` or `character:*` from projected inbox content only, validates output, appends actor response messages, records trace, and updates actor artifacts.
- `run_subgm_thread`: dispatches or resumes one bounded side thread, mirrors accepted messages into the common bus, updates side-thread artifacts, and creates follow-up work for actor calls or GM arbitration.
- `compose_story`, `review_critic`, `repair_request`, `rollback_request`, and `deliver_round`: remain dispatcher executors, with only targeted cleanup.
- `assets_task`: becomes an asynchronous, nonblocking executor.
- `system_request`: becomes an auditable request lane for code, prompt, workflow, or tooling changes. It records and routes suggestions, but never silently edits source during normal gameplay.

Python remains the protocol kernel. Agents may ask for work by writing messages and intents, but Python alone executes projection, actor dispatch, rollback, delivery, file mutation, asset generation, and source-improvement side effects.

## Current Gaps To Close

### Gap 1: Declared Intent Types Without Executors

`agent_dispatcher.py` declares `request_projection`, `run_actor`, and `run_subgm_thread`, but current dispatcher execution does not handle them as first-class paths. They should no longer fall through to `executor_not_wired`.

### Gap 2: GM Loop Still Owns Too Much Collaboration

The existing GM loop has valuable tested logic, but it performs projection, actor dispatch, subGM scheduling, and final loop aggregation inside one helper call. The refactor should extract reusable units rather than rewrite the behavior:

- projection packet preparation,
- actor packet dispatch and validation,
- actor response persistence,
- actor batch selection and merge rules,
- subGM ready-thread dispatch,
- trace event writes,
- GM completion checks.

The initial implementation may keep a compatibility executor for complex GM-loop behavior, but the dispatcher smoke must prove at least one full path through explicit projection and actor intents.

That compatibility executor must be treated as temporary. Once equivalent dispatcher-native coverage exists, the old broad GM-loop branch should be reduced to reusable validation, trace, batching, and packet-building helpers rather than remaining as a second orchestration path.

### Gap 3: Async Assets/UI Is Not In The Runtime

`assets_task` is currently a reserved or unsupported intent. `assets-ui agent` from `docs/重构建议.md` should become an asynchronous lane that can generate images or update save-level UI after text delivery.

Text delivery must not wait for image generation or UI hot update. Assets failures should mark assets state as failed/degraded without rolling back delivered story.

### Gap 4: System Improvement Queue Is Not A First-Class Agent Lane

Critic can write `system_iteration_suggestion` into `.agent_runs/improvement_queue.jsonl`. That is useful but passive. The runtime should define how `input_analyst`, `critic`, or `main_agent` create `system_request` messages and how those requests are triaged.

Normal gameplay must not auto-apply source changes. A system request may become:

- recorded only,
- surfaced for user approval,
- converted into an implementation-plan request outside gameplay,
- rejected as out of scope.

### Gap 5: Root Artifact Mirrors Still Exist At Some Boundaries

Root run files still exist for delivery, memory, and compatibility boundaries. That is acceptable when explicitly exported after validation, but control-plane reads should continue moving toward `artifacts/`.

The cleanup goal is not to delete every root file reference immediately. The goal is to make every remaining root read intentional, documented, and covered by tests proving it is a delivery or legacy consumer boundary rather than a dispatcher decision source.

## Runtime Contracts

### Messages

Messages remain append-only and audit-visible. New message types should use the existing envelope and ACL model:

- `request_projection`
- `projected_message`
- `actor_response`
- `subgm_status`
- `asset_request`
- `asset_result`
- `system_request`
- `system_result`

Actor-facing messages to `player` or `character:*` still require projection. Actor-to-actor direct messages remain rejected. `subGM:*` messages remain scoped to GM, projection, story, and allowed actors through projection.

### Intents

The supported intent set should become:

- `analyze_input`
- `run_gm_turn`
- `request_projection`
- `run_actor`
- `run_subgm_thread`
- `compose_story`
- `review_critic`
- `repair_request`
- `rollback_request`
- `deliver_round`
- `assets_task`
- `system_request`

Every executor result must include:

- stable `intent_id`,
- `intent_type`,
- `status`,
- created message IDs,
- created intent IDs,
- artifact paths,
- structured block or degradation reason.

Follow-up creation must remain idempotent by `source_intent_id` or `source_message_id`.

### Artifacts

Authoritative control-plane artifacts live under `artifacts/`:

- `input_analysis.output.json`
- `gm.output.json`
- `actor.outputs.json`
- `story.input.json`
- `story.output.json`
- `critic.report.json`
- optional `assets/*.json`
- optional `system_requests/*.json`

Root files may be exported only for existing delivery, memory, or frontend consumers. New dispatcher logic must not prefer root files over artifacts.

## Dispatcher-Native Collaboration Flow

The target turn flow is:

1. `analyze_input` applies semantic input analysis and creates `run_gm_turn`.
2. `run_gm_turn` dispatches GM once, validates output, records GM trace/material, and creates follow-up intents:
   - `request_projection` for each actor call,
   - `run_subgm_thread` for ready side threads,
   - `compose_story` only when no required actor/subGM work remains,
   - player-decision stop when the GM or actor output requires real player consent,
   - `rollback_request` or `repair_request` when validated policy requires it.
3. `request_projection` reads the source actor request and produces one `projected_message`.
4. `run_actor` dispatches the projected actor packet, records `actor_response`, updates `artifacts/actor.outputs.json`, and creates either:
   - `run_gm_turn` for GM continuation,
   - another `request_projection` for dialogue/perception continuation,
   - player-decision stop,
   - or no follow-up if other pending work already exists.
5. `run_subgm_thread` advances one bounded side thread and may create projection/actor intents for allowed characters or messages to GM for arbitration.
6. When all progression intents are resolved, `compose_story` and `review_critic` run.
7. `deliver_round` delivers text, schedules memory jobs, triggers lifecycle cleanup, and creates nonblocking `assets_task` intents when requested.

The dispatcher should remain deterministic. If priority is needed, it should be explicit and tested. Until then, pending intent ID order is the stable rule.

## Player Decision Semantics

The player agent may propose actions, dialogue, memory deltas, or perceptions, but it cannot commit high-risk, irreversible, or plot-critical player-character actions.

When `run_actor` receives a player output that GM policy or validator marks as critical:

- the output remains audit-visible,
- the proposed action may become one of the next player suggestions,
- the runtime stops at a real player-decision state,
- no downstream story/delivery intent may treat the action as committed progression.

This preserves the distinction between simulated player participation and real player authority.

## subGM Semantics

`run_subgm_thread` should preserve the existing subGM authority model:

- subGM can read omniscient context needed for its assigned boundary.
- subGM cannot include the player character.
- subGM cannot promote important characters.
- subGM cannot spawn other subGM threads.
- subGM cannot change its own boundary.
- subGM cannot mutate profile, background, personality, body facts, or hidden settings.
- subGM actor calls must target allowed registered important characters and must pass projection.

Active character reservations must remain authoritative across mainline and side-thread work. If a side thread is paused by lifecycle cleanup, its active reservations are released but its narrative state is not marked complete.

## Assets/UI Lane

`assets_task` is an async lane, not part of text delivery.

Inputs:

- approved story output,
- current card metadata,
- optional scene/portrait/UI request from story, critic, GM, or main agent,
- current `.card_assets.json` and `ui_manifest.json`.

Allowed outputs:

- generated image records under `generated/images/`,
- updates to `.card_assets.json`,
- save-level UI manifest updates,
- asset result messages to `assets_ui`, `main_agent`, and optionally `story`.

Rules:

- `deliver_round` may create assets intents after successful text delivery.
- The dispatcher may execute assets intents in a later poll or async command.
- Assets failures record `asset_failed` or `asset_degraded` evidence and do not alter delivered story.
- Image generation must continue using the existing `image_generate.py` adapter and local ignored API configuration.

## System Request Lane

`system_request` is for code, prompt, workflow, tool, or architecture changes requested by input analyst, critic, story, GM, or main agent.

It should not directly edit source in a normal `/rp` turn. Instead, the dispatcher records a structured request:

```json
{
  "kind": "code|prompt|workflow|tooling|documentation",
  "summary": "",
  "evidence": {},
  "risk": "low|medium|high|critical",
  "requires_user_approval": true
}
```

The handling result may be:

- `recorded`: appended to improvement queue only.
- `blocked`: rejected by policy or missing evidence.
- `needs_user_review`: surfaced for explicit user approval.
- `planned`: converted outside gameplay into a future spec/plan.

This keeps autonomous suggestions useful without letting gameplay silently rewrite the runtime.

## Error Handling

All failures remain structured and audit-visible.

New or clarified reasons:

- `executor_not_wired`: only allowed during implementation, not after this refactor is accepted.
- `projection_source_missing`: projection intent cannot find its source request.
- `projected_message_missing`: actor dispatch has no projected message.
- `actor_dispatch_failed`: model call or actor schema validation failed.
- `subgm_dispatch_failed`: side-thread runner failed or returned invalid output.
- `asset_failed`: assets task failed after text delivery.
- `system_request_policy_blocked`: request cannot be acted on in gameplay.
- `root_artifact_boundary_violation`: control-plane logic attempted to read root mirror as authoritative.

Blocked progression intents must prevent story/delivery follow-ups. Degraded post-delivery intents, such as assets, must not roll back delivered text.

## Testing Strategy

All tests should avoid live model calls.

### Dispatcher Tests

Add or extend `tests/test_agent_dispatcher.py` for:

- `request_projection` creates `projected_message` and `run_actor`.
- `run_actor` requires a projected message.
- `run_actor` writes actor response message and updates `artifacts/actor.outputs.json`.
- `run_actor` creates GM continuation or player-decision stop according to output.
- `run_subgm_thread` advances one ready thread and records side-thread evidence.
- unsupported declared intent types no longer exist after implementation.
- stalled runtime remains blocked with diagnostic evidence.
- root artifact reads are rejected in dispatcher-owned paths.

### Message And ACL Tests

Extend `tests/test_agent_messages.py` for:

- `asset_request` and `system_request` routing.
- actor-to-actor direct rejection.
- subGM-to-forbidden-target rejection.
- projection-required enforcement for all actor-facing targets.

### GM/Actor Extraction Tests

Keep existing `agent_turn_loop` behavior covered while extracting helpers:

- parallel actor batch decisions remain deterministic.
- perception continuation still schedules the origin actor.
- structured dialogue transfer still preserves only visible words/tone/channel.
- context versioning remains attached before dispatch.
- stale returned context warnings remain audit-visible.

### Assets/System Tests

Add tests for:

- `assets_task` is created after delivery when requested.
- assets failure marks degraded state without removing delivered response.
- `system_request` records structured queue entry and requires user approval for source changes.
- duplicate system requests are deduplicated by source message or fingerprint.

### Smoke And E2E Tests

Update deterministic smoke so the completed intent chain includes:

- `analyze_input`
- `run_gm_turn`
- `request_projection`
- `run_actor`
- optional `run_subgm_thread`
- `compose_story`
- `review_critic`
- `deliver_round`

Manual acceptance remains required:

- blank-folder `/rp` run for at least five player turns,
- important-character dialogue boxes,
- projection-safe actor packets,
- player-decision stop,
- subGM side-thread creation and pause/resume evidence,
- progress updates,
- text delivery,
- nonblocking asset/UI refresh when configured.

## Migration Plan

Each phase has two outputs:

- a behavior output: the new dispatcher-native capability works and is tested;
- a simplification output: the replaced old path is deleted, narrowed, or explicitly quarantined as temporary.

### Phase 1: Extract Collaboration Helpers

Move reusable pieces out of `agent_turn_loop.py` and `subgm_turn_loop.py` without changing behavior:

- projection request materialization,
- actor packet dispatch/validation,
- actor output persistence,
- trace append helpers,
- side-thread dispatch wrapper.

The existing tests should pass before dispatcher behavior is changed. This phase should not create a large generic utility module. If a helper is used by only one future executor and does not remove complexity, keep it local until the boundary is proven.

### Phase 2: Implement Projection And Actor Executors

Wire `request_projection` and `run_actor` in `agent_dispatcher.py`.

The first acceptance target is one deterministic path where GM creates actor work, dispatcher projects it, actor responds, and the dispatcher resumes GM or proceeds to story.

### Phase 3: Implement subGM Executor

Wire `run_subgm_thread` as a dispatcher executor for one bounded side thread at a time.

Existing ready-thread batch execution may remain as a helper, but the dispatcher must own lifecycle state, intent completion, message mirroring, and blocked/degraded results.

### Phase 4: Shrink `run_gm_turn`

Refactor `run_gm_turn` so it no longer consumes all actor and subGM work internally. It should create follow-up intents and return. Temporary compatibility mode is allowed only behind explicit tests and must not be the default final path.

This is the main structural simplification phase. Remove or collapse old branches that:

- execute projection and actor dispatch as hidden side effects of one GM-loop call,
- assemble story readiness from implicit loop completion instead of pending intents,
- duplicate dispatcher decisions in `rp_generate_cli.py`, `agent_turn_loop.py`, or delivery helpers,
- mirror root artifacts before they are needed by a delivery or memory boundary.

### Phase 5: Add Assets And System Request Lanes

Implement `assets_task` and `system_request` as nonblocking lanes.

Assets may mutate generated assets and UI manifests after delivery. System requests may record, surface, or plan work, but not silently edit source.

### Phase 6: Cleanup Root Artifact Boundaries And Docs

Audit root artifact references and update documentation:

- root reads are either removed or documented as delivery/memory boundaries,
- README/CLAUDE/AGENTS describe dispatcher-native projection/actor/subGM,
- stale workflow-era sections are corrected.

Also add a code-size and branch-reduction review:

- identify modules whose responsibilities became narrower after the refactor,
- delete obsolete compatibility helpers,
- remove tests that only preserve old fixed-flow behavior,
- keep regression tests for behavior and safety, not for old implementation shape.

### Phase 7: Verification And Manual Acceptance

Run:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_dispatcher.py skills/agent_messages.py skills/agent_intents.py skills/agent_outputs.py skills/agent_turn_loop.py skills/subgm_threads.py skills/subgm_turn_loop.py skills/rp_generate_cli.py skills/round_prepare.py skills/round_deliver.py skills/control_plane_smoke.py
```

Then perform the manual five-turn `/rp` acceptance run described above.

## Estimated Remaining Work

Approximate remaining effort after the completed 2026-06-21 plans:

- Dispatcher-native projection/actor/subGM collaboration: 45-55%.
- Async assets/UI and system request lanes: 25-35%.
- Root-boundary cleanup, docs, smoke expansion, and manual acceptance: 15-20%.

The recommended next implementation plan should cover Phase 1 through Phase 4 first. Assets/system lanes can follow once dispatcher-native collaboration is stable.

## Risks And Controls

- Risk: extracting `agent_turn_loop.py` behavior regresses already-tested projection, batch, perception, or dialogue rules.
  Control: extract helpers first with no behavior changes, then wire dispatcher executors behind focused tests.

- Risk: dispatcher becomes too large.
  Control: keep executor functions thin and move domain logic to focused helper modules once boundaries are proven.

- Risk: the refactor increases code size by keeping old and new paths alive.
  Control: every implementation plan must include cleanup tasks for the old path it replaces, and final verification must search for obsolete branch names, compatibility helpers, and root-artifact authority reads.

- Risk: async assets modify UI files after delivery in a way that breaks the frontend.
  Control: assets tasks write manifest-backed records and never block or roll back delivered text.

- Risk: system requests become accidental self-modifying code.
  Control: normal gameplay records and surfaces requests only; source edits require explicit user-approved implementation flow.

- Risk: root artifact compatibility hides authority bugs.
  Control: add conflict tests that root mirrors are ignored by dispatcher-owned paths.

## Documentation Updates

After implementation, update:

- `README.md`: clarify dispatcher-native projection, actor, subGM, assets, and system request lanes.
- `CLAUDE.md`: remove stale root-artifact and fixed-step descriptions where they conflict with dispatcher-native execution.
- `AGENTS.md`: list the new executor boundaries and manual acceptance checks.
- Existing superpowers specs should remain historical records; do not rewrite old specs except to add a short superseded-note if a future plan explicitly requires it.

## Self-Review

- Placeholder scan: no TODO, TBD, or unnamed requirement remains.
- Consistency check: the design consistently treats dispatcher pending intents as the executable source of truth.
- Scope check: this is intentionally a remaining-work umbrella spec, but the migration plan identifies a smaller first implementation plan covering dispatcher-native collaboration.
- Ambiguity check: root artifacts are allowed only as delivery or legacy consumer boundaries, not dispatcher authority.
- Safety check: actor-facing delivery still requires projection, player authority remains protected, subGM authority remains scoped, and source modifications require explicit user-approved work outside normal gameplay.
- Maintainability check: the design requires replaced old paths to be deleted, narrowed, or quarantined so the refactor does not grow permanent duplicate orchestration code.
