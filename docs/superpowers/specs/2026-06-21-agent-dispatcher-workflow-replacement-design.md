# Agent Dispatcher Workflow Replacement Design

## Purpose

The current RP runtime has a message bus, intent files, projection gates, snapshots, repair intents, and deterministic smoke coverage. However, the live control plane still behaves like a fixed workflow: `agent_workflow.py`, `rp_generate_cli.py`, and related artifact checks decide whether the system should dispatch input analysis, GM, actors, story, critic, repair, or delivery.

This design replaces that stage-driven workflow with a dispatcher-first runtime. `.agent_runs/<round>/` becomes the authoritative runtime state, and pending intents become the only source of executable next actions.

The user-selected scope is intentionally aggressive:

- Switch the live `/rp` main path to a central dispatcher.
- Remove `agent_workflow.py` from next-action decisions.
- Stop treating root artifacts as authoritative compatibility paths.
- Keep MCP or external tool wrappers out of this phase.

## Goals

- Improve agent autonomy by letting agents request collaboration through messages and intents instead of hard-coded workflow stages.
- Make `messages.jsonl`, `inboxes/`, `intents/`, and `artifacts/` the control-plane spine.
- Keep Python authoritative for ACL, projection, schema, trace, snapshot, rollback, repair policy, and final delivery gates.
- Make dispatcher execution repeatable and auditable.
- Delete or sharply shrink workflow-era branches that duplicate dispatcher behavior.
- Preserve current safety guarantees for raw player input, hidden information, actor isolation, subGM boundaries, critic gates, and rollback evidence.

## Non-Goals

- Do not implement MCP wrappers in this phase.
- Do not add compatibility layers for obsolete generated game files or old runtime protocols.
- Do not let agents directly mutate authoritative files outside messages, intents, and validated artifacts.
- Do not replace Claude Code with a backend LLM API scheduler.
- Do not relax visibility, projection, schema, critic, or raw player input rules.
- Do not make assets or image generation block text delivery.

## Recommended Architecture

Use a central dispatcher as the only live next-action engine.

Add `skills/agent_dispatcher.py`. It reads one round runtime and processes pending intents:

- `messages.jsonl`
- `inboxes/`
- `intents/pending|accepted|rejected|completed|blocked/`
- `artifacts/`
- `manifest.json`
- existing trace, snapshot, side-thread, and memory files

The dispatcher accepts an intent, executes the matching operation through existing focused modules, writes validated artifacts, updates trace and manifest status summaries, then completes or blocks the intent. It may create follow-up intents when the current action naturally produces more work.

`manifest.stage` no longer drives control flow. It becomes a status summary for UI, diagnostics, and smoke output. The next executable action comes only from pending intents.

## Module Boundaries

`agent_messages.py` remains responsible for:

- message normalization,
- append-only log writes,
- ACL checks,
- inbox indexes,
- rejected-message audit rows.

`agent_intents.py` remains responsible for:

- intent normalization,
- ID allocation,
- state directories,
- lifecycle transitions,
- result records.

`agent_dispatcher.py` becomes responsible for:

- selecting pending intents,
- executing supported intent types,
- enforcing idempotency,
- calling existing execution helpers,
- writing `artifacts/`,
- creating follow-up intents,
- blocking stalled or invalid runtimes.

`agent_turn_loop.py` becomes an execution helper for GM/actor/subGM progression. It no longer owns the top-level round loop.

`agent_outputs.py` remains responsible for story input assembly, artifact validation, critic delivery gate support, repair history, and system improvement queue records, but its primary read/write surface is `artifacts/`.

`rp_generate_cli.py` becomes a dispatcher driver:

1. Ensure a prepared round exists.
2. Ensure initial intent exists when needed.
3. Call `agent_dispatcher.dispatch_next(...)` until the run is delivered, blocked, or stalled.
4. Return the dispatcher result shape.

`round_prepare.py` creates the runtime, snapshot, initial `input_received` message, and initial `analyze_input` intent. It does not precompute a fixed workflow.

`round_deliver.py` keeps final frontend delivery, memory ingestion, post-round memory jobs, and lifecycle cleanup. It does not decide story, critic, repair, or rollback routing.

`agent_workflow.py` is removed from the live path. The preferred implementation is to delete it and its tests. If deletion creates too much churn for one implementation pass, it may be replaced with a narrow read-only diagnostic helper, but it must not expose `advise_next_actions()` or return executable `next_action` decisions.

## Runtime Layout

The authoritative round layout is:

```text
.agent_runs/<round>/
  manifest.json
  input.json
  input.raw.json
  messages.jsonl
  inboxes/
  intents/
    pending/
    accepted/
    rejected/
    completed/
    blocked/
  artifacts/
    input_analysis.output.json
    gm.output.json
    actor.outputs.json
    story.input.json
    story.output.json
    critic.report.json
  side_threads/
  memory_summaries/
  post_round_memory_jobs/
  repair_history.jsonl
  interaction.trace.json
```

Root run artifacts are not authoritative. The implementation should remove compatibility reads such as "try root path, then artifacts path." If a boundary still requires a root file temporarily, the dispatcher may export it as a final delivery side effect, and tests must assert it is not used for control decisions.

## Supported Intent Types

### `analyze_input`

Validates and applies `input_analysis.output.json` for the current run. On completion it writes `artifacts/input_analysis.output.json`, appends an `analysis_applied` message, and creates follow-up intents based on validated analysis.

Typical follow-up:

- `run_gm_turn` for normal role-channel progression.
- `rollback_request` for explicit validated conflict repair.
- `system_request` message to `main_agent` for out-of-band code or system work, without executing code changes in this phase.

### `run_gm_turn`

Runs the main GM progression executor. It writes or updates `artifacts/gm.output.json`, appends GM messages, updates `interaction.trace.json`, and creates follow-up intents for projection, actors, subGM threads, story composition, or player-decision stop.

GM may request actor contact, subGM work, story composition, rollback, or repair, but the dispatcher executes those requests.

### `request_projection`

Projects GM/subGM actor-facing content before any actor can read it. It validates visibility basis, hidden phrase absence, target actor, source call linkage, and actor memory boundaries.

Success creates a `projected_message` and a `run_actor` intent. Failure blocks or rejects the source intent with a structured reason.

### `run_actor`

Runs `player` or `character:*` only from projected inbox content. It writes an `actor_response` message, updates trace, and updates `artifacts/actor.outputs.json`.

The player agent still cannot finalize critical actions. If a player actor response is high-risk, critical, or judged by GM to require real player consent, dispatcher marks a player-decision state instead of persisting that action as committed story progression.

### `run_subgm_thread`

Runs or resumes one bounded subGM side thread. It updates side-thread state and artifacts, mirrors accepted messages into the common round bus, and respects actor reservations.

SubGM cannot create or promote important characters, include the player, change its own boundary, spawn another subGM, or write direct memory/profile mutations. Requests outside its authority become messages to GM or blocked intents.

### `compose_story`

Materializes `artifacts/story.input.json` from validated artifacts, visible trace, story-facing messages, side-thread summaries, player input authority, and memory deltas. Then it dispatches story generation and writes `artifacts/story.output.json`.

Story does not read GM-only messages or unprojected actor-facing material.

### `review_critic`

Runs critic review against `artifacts/story.output.json` and `artifacts/story.input.json`, writes `artifacts/critic.report.json`, and routes the result.

Follow-up:

- `deliver_round` for `pass`.
- `repair_request` for allowed `revise`.
- `rollback_request` for allowed round-progression repair.
- blocked runtime for disallowed or exhausted repair.

### `repair_request`

Records critic/story/GM repair as an executable intent. It normalizes `repair_routing`, checks `selfRepairMode`, and creates either `compose_story`, `rollback_request`, or a blocked result.

Repair intent source messages must be linked by `source_message_id`. Orphan repair messages are invalid.

### `rollback_request`

Executes snapshot restore or scoped artifact cleanup. It is the only path that can restore snapshots.

Supported modes:

- `story_only`: discard story and critic artifacts; keep GM, actor, subGM, and trace progression.
- `round_progression`: restore current-round pre-progression state; recreate `run_gm_turn`.
- `historical_branch`: restore an earlier branch snapshot and archive later derived runtime state.

If rollback fails, the runtime is blocked and no downstream GM/story/critic intent may be created.

### `deliver_round`

Confirms critic pass, exports any files required by legacy frontend delivery boundaries, invokes final delivery, updates manifest summary to delivered, schedules post-round memory jobs, and triggers lifecycle cleanup.

This is the only place where root delivery files may be written as boundary exports.

### `assets_task`

Reserved for future async assets-ui work. This phase records it as unsupported or not yet implemented. It must not block `deliver_round`.

## Dispatcher Selection And Idempotency

The dispatcher processes pending intents in deterministic ID order unless a future implementation adds explicit priority. Completed, blocked, rejected, and accepted intents are not re-executed.

Each dispatcher result includes:

```json
{
  "ok": true,
  "status": "completed|blocked|stalled|delivered",
  "intent_id": "intent_000001",
  "intent_type": "run_gm_turn",
  "created_intents": ["intent_000002"],
  "created_messages": ["msg_000003"],
  "artifacts": ["artifacts/gm.output.json"],
  "reason": ""
}
```

Idempotency rules:

- A completed intent is never executed again.
- Follow-up intent creation uses source intent IDs or source message IDs to avoid duplicate repair, rollback, projection, or delivery intents.
- Re-running dispatcher after a crash either resumes from pending intents or reports a blocked/stalled state with evidence.
- Stalled means no pending intent exists and the runtime is neither delivered nor intentionally blocked. Stalled is converted into a blocked manifest state with a missing-next-action diagnostic.

## Data Flow

The target live flow is:

1. Browser records raw player input and pending display.
2. `round_prepare.py` creates snapshot, round runtime, `input_received` message, and `analyze_input` intent.
3. `rp_generate_cli.py` calls `dispatch_next()` repeatedly.
4. `analyze_input` applies validated input analysis and creates GM/system/rollback follow-up work.
5. `run_gm_turn` advances main progression and creates projection, actor, subGM, story, or decision intents.
6. `request_projection` creates actor-safe inbox messages.
7. `run_actor` records actor responses and trace events.
8. `run_subgm_thread` advances side scenes under GM boundaries.
9. `compose_story` materializes story input and story output.
10. `review_critic` passes, repairs, rolls back, or blocks.
11. `deliver_round` performs final frontend delivery and post-round cleanup.

The main loop exits only when dispatcher reports delivered, blocked, or stalled.

## Error Handling

All failures are structured and audit-visible.

Common reasons:

- `acl_rejected`
- `projection_required`
- `visibility_rejected`
- `schema_rejected`
- `artifact_missing`
- `artifact_invalid`
- `agent_call_failed`
- `invalid_agent_json`
- `reservation_conflict`
- `snapshot_missing`
- `rollback_failed`
- `self_repair_mode_blocks_route`
- `critic_retry_limit`
- `unsupported_intent_type`
- `dispatcher_stalled`

Rules:

- Schema, ACL, and projection failures reject or block the specific intent and do not create downstream work.
- Agent call failures block the intent and set the runtime blocked unless the intent type has an allowed retry policy.
- Critic revise/block always records repair evidence before generating repair or rollback work.
- Rollback failure blocks the runtime immediately.
- Delivery cannot run unless critic pass is present in `artifacts/critic.report.json`.
- Blocked states preserve messages, intents, artifacts, and trace evidence.

## Old Layer Removal

The implementation should remove or rewrite these workflow-era assumptions:

- `agent_workflow.advise_next_actions()` must not be called by live code.
- Tests should not assert `dispatch_agent_outputs`, `build_story_input`, or other workflow next-action strings.
- `manifest.expected_outputs` must not decide control flow. If retained, it is documentation/status only.
- Root `gm.output.json`, `actor.outputs.json`, `story.input.json`, `story.output.json`, and `critic.report.json` must not be read as authoritative inputs.
- `agent_outputs.py` fallback reads from root artifacts should be removed or limited to explicit final delivery export tests.
- `rp_generate_cli.py` should not directly orchestrate fixed stage order except to bootstrap the dispatcher loop.
- README, CLAUDE, and AGENTS must describe dispatcher-first runtime and remove claims that `agent_workflow.py` provides next-action control.

## Testing Strategy

Add and update tests without live model calls.

New `tests/test_agent_dispatcher.py` covers:

- pending intent selection,
- accept/complete/block/reject transitions,
- follow-up intent creation,
- unsupported intent blocking,
- artifact writes under `artifacts/`,
- idempotent repeated dispatch,
- stalled runtime blocking.

Update `tests/test_rp_generate_cli.py` to prove:

- live round driver calls dispatcher,
- `agent_workflow.advise_next_actions()` is not called,
- dispatcher delivered returns successful run result,
- dispatcher blocked returns blocked result with reason.

Update `tests/test_control_plane_smoke.py` to prove:

- smoke begins from `analyze_input`,
- all major work is represented by completed intents,
- messages include projection and actor response,
- final state reaches `deliver_round` completed,
- no workflow next-action advice is required.

Update `tests/test_agent_outputs.py` to prove:

- `artifacts/story.input.json` is authoritative,
- root mirror files do not drive assembly,
- critic repair creates linked messages and intents from artifact paths.

Regression tests must cover:

- unprojected actor-facing content cannot run actor,
- actor-to-actor direct messages are rejected,
- rollback failure prevents downstream GM/story intents,
- no pending intent before delivery causes blocked stalled state,
- subGM cannot promote characters or include player,
- root artifact files with conflicting content are ignored.

Final verification:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_dispatcher.py skills/agent_messages.py skills/agent_intents.py skills/agent_outputs.py skills/agent_turn_loop.py skills/subgm_threads.py skills/subgm_turn_loop.py skills/rp_generate_cli.py skills/round_prepare.py skills/round_deliver.py
```

Manual acceptance after implementation still requires a blank-folder `/rp` run for at least five turns, including important-character dialogue, player-decision stops, progress updates, and successful frontend delivery.

## Migration Plan

### Phase 1: Dispatcher Foundation

Create `agent_dispatcher.py` with deterministic pending-intent selection, lifecycle transitions, no-op unsupported intent blocking, and stalled runtime detection. Add focused unit tests before wiring live code.

### Phase 2: Artifact Authority Shift

Move story input, story output, critic report, GM output, and actor output reads/writes to `artifacts/`. Remove root fallback reads. Keep root exports only if a final delivery boundary still needs them.

### Phase 3: Live Driver Switch

Refactor `rp_generate_cli.py` so it calls dispatcher until delivered, blocked, or stalled. Remove calls to `agent_workflow.advise_next_actions()`.

### Phase 4: Intent Executors

Implement executor functions for `analyze_input`, `run_gm_turn`, `request_projection`, `run_actor`, `run_subgm_thread`, `compose_story`, `review_critic`, `repair_request`, `rollback_request`, and `deliver_round`, using existing modules wherever possible.

### Phase 5: Workflow Layer Removal

Delete or reduce `agent_workflow.py` to read-only diagnostics. Update or delete tests that encode workflow next-action behavior. Update documentation.

### Phase 6: Smoke And Full Verification

Rewrite control-plane smoke to validate dispatcher evidence: completed intents, message types, artifact authority, projection, rollback/repair behavior, delivery, memory jobs, and lifecycle cleanup.

## Risks And Controls

- Risk: Implementation touches many tests and files at once.
  Control: keep dispatcher executors small and test each intent type independently.

- Risk: Removing root artifact fallback breaks final delivery.
  Control: isolate any required root export in `deliver_round` and test that control-plane reads ignore root conflicts.

- Risk: Agent autonomy bypasses safety gates.
  Control: agents only create messages/intents; dispatcher alone writes authoritative artifacts and executes rollback/delivery.

- Risk: Dispatcher becomes a new monolith.
  Control: each intent executor should be a small function with a typed input contract and focused tests.

- Risk: Stalled runtime hides missing follow-up logic.
  Control: no pending intent before delivery is a blocked diagnostic state, not success.

## Documentation Updates

After behavior changes land:

- `README.md` should describe dispatcher-first runtime and `artifacts/` authority.
- `CLAUDE.md` should tell Claude Code to drive rounds by dispatcher results, not workflow advice.
- `AGENTS.md` should list `agent_dispatcher.py` as the runtime execution spine.
- Remove documentation that says `agent_workflow.py` decides next actions.
- Mention MCP wrappers are intentionally deferred until Python dispatcher APIs stabilize.

## Self-Review

- Placeholder scan: no TODO, TBD, or unnamed future requirement remains.
- Consistency check: the design consistently treats pending intents as the only executable next-action source.
- Scope check: the design is large but focused on one migration: replacing workflow next-action control with dispatcher-first runtime.
- Ambiguity check: root artifacts are explicitly non-authoritative; any root file writing is only a final delivery boundary export.
- Safety check: actor-facing delivery, rollback, repair, critic pass, and raw player input authority remain gated by Python.
