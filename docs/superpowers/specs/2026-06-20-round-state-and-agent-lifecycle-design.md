# Round State Machine and Agent Lifecycle Design

## Status

Confirmed by the user on 2026-06-20.

The approved direction is Scheme A: a unified round state machine plus actor context version invalidation. The frontend should show a hybrid progress model: a stable high-level phase in the main UI and optional detail for the currently active agent, subGM thread, actor call, retry attempt, or blocking reason. Actor profile/background updates use the conservative strategy: do not interrupt an already-running player or character call; force the next call for that actor to rebuild its context from the latest persisted files.

## Background

The project already has the major parts of a structured RP control plane:

- round_prepare.py creates the per-round .agent_runs/<round>/ mailbox and initial context.
- input_analysis_apply.py validates and applies semantic player-input analysis.
- rp_generate_cli.py dispatches input analyst, GM loop, Story, Critic, and delivery.
- agent_turn_loop.py runs the bounded GM/player/character loop and delegates runnable subGM side threads.
- subgm_threads.py persists side-thread state, messages, reservations, and GM commands.
- round_deliver.py gates approved output, mirrors story output to response.txt, invokes handler.py, and schedules post-round memory work.
- handler.py already writes skills/styles/progress.json, and the frontend polls /api/progress.

The current progress model is useful but loose. progress.json is a free-form stage, label, percent, and detail record. This makes it easy for different pipeline steps to drift into inconsistent state names and makes the frontend unable to reliably distinguish normal progress, retry, blocked state, agent activity, side-thread activity, and terminal completion.

The current agent model is also mostly stateless per dispatch. Claude Code local_agent calls are not long-lived processes owned by the Python runtime; they are one-off invocations that write structured artifacts. Therefore, the design should not pretend that every player/character/subGM is a durable process that can always be killed and restarted. Runtime cleanup should close file-level leases, pause unfinished side-thread execution, and ensure the next dispatch uses fresh context.

## Goals

- Represent the round pipeline as an explicit state machine with stable state IDs.
- Keep frontend progress real-time and predictable without coupling UI text to scattered backend strings.
- Preserve the current file-mailbox architecture and current-format-only policy.
- Show high-level phase in the main progress UI and structured details in an expandable detail area.
- Close all active subagent runtime activity after a delivered turn without falsely marking unfinished side stories as completed.
- Ensure player/character agent calls after a profile/background/context update load the latest persisted actor information.
- Avoid unsafe interruption of already-running Claude Code local agent calls.
- Add deterministic tests without live model calls.

## Non-Goals

- Do not introduce a separate background process supervisor for Claude Code local agents.
- Do not add compatibility support for obsolete .agent_runs/ formats.
- Do not mark unfinished subGM side stories as narratively completed merely because a turn was delivered.
- Do not force every internal state to appear in the compact frontend progress badge.
- Do not replace the existing manifest stage history; the state machine should complement and normalize it.
- Do not implement frontend visual redesign beyond the state/progress display required by this spec.

## Design Overview

The implementation should add two small control-plane modules:

1. round_state.py
   - Owns the round state enum, transition helpers, progress JSON schema, labels, percentages, terminal markers, and manifest/progress synchronization helpers.
   - Replaces scattered ad-hoc progress writes with named state transitions.

2. agent_lifecycle.py
   - Owns end-of-round agent cleanup, active subGM pause/closure semantics, actor context version snapshots, and actor context invalidation checks.
   - Does not attempt OS-level cancellation of already-running Claude Code local agent calls.

The main flow stays the same:

browser submit
  -> round_prepare
  -> input analyst
  -> input_analysis_apply
  -> GM loop
     -> actor calls
     -> subGM side-thread calls
  -> story
  -> critic
  -> round_deliver
  -> handler
  -> post-round memory scheduling/ingestion
  -> agent lifecycle cleanup
  -> complete

## Round State Machine

### State Model

Each progress record should use a stable state ID and a high-level phase. Example shape:

{
  "schema_version": 2,
  "state": "gm_loop.actor_dispatch",
  "phase": "gm_loop",
  "label": "角色行动中",
  "percent": 48,
  "run_id": "round-000006",
  "terminal": false,
  "detail": {
    "agent": "character:Ada",
    "actor_call_id": "call-character-Ada-1",
    "batch_id": "batch-1-1",
    "attempt": 1
  },
  "updated_at": "2026-06-20T18:58:14+08:00"
}

state is for machines and tests. label is for the user. detail is structured and optional.

### Required States

Initial and input states:

- idle
- input.received
- round.preparing
- input_analysis.awaiting
- input_analysis.running
- input_analysis.applying
- input_analysis.applied

GM and actor loop states:

- gm_loop.starting
- gm_loop.gm_dispatch
- gm_loop.subgm_dispatch
- gm_loop.actor_batch
- gm_loop.actor_dispatch
- gm_loop.waiting_player_decision
- gm_loop.completed
- gm_loop.retrying

Story, critic, and delivery states:

- story.running
- story.preflight_repair
- critic.running
- critic.revise
- critic.blocked
- delivery.validating
- delivery.retrying
- delivery.delivering
- delivery.failed

Memory and cleanup states:

- memory.finalizing
- memory.post_round_scheduling
- agent_lifecycle.cleanup
- complete
- blocked
- error

The enum can grow, but all new states must be declared in round_state.py before use.

### Transition Policy

The first implementation should validate obvious illegal transitions without overfitting every branch:

- Terminal states: complete, blocked, error.
- complete can only be written after delivery has succeeded.
- blocked can be written by critic/delivery/system gates.
- error can be written by exception handlers.
- Repair states may transition back to their owning phase.
- Repeated writes of the same state are allowed to refresh detail.

Manifest stage remains the artifact-level source of truth for .agent_runs/<round>/. round_state.py should write progress JSON and, when appropriate, append manifest progress entries using existing agent_run.append_manifest_stage.

## Frontend Progress Display

The frontend should continue polling /api/progress, but it should treat schema_version: 2 specially:

- Compact main badge:
  - high-level label
  - progress bar based on percent
  - visible for active, retry, blocked, error, and recently completed states

- Detail area:
  - current state
  - active agent
  - active subgm_thread_id
  - active actor_call_id or batch_id
  - retry attempt and max attempts
  - block/error detail

The UI should remain backward-compatible with old progress.json while tests and new code use schema v2.

## End-of-Round Subagent Cleanup

The phrase "close all unclosed subagents" maps to two runtime concepts:

1. Claude Code local agent invocation
   - One-off dispatch call.
   - Python normally has no reliable cancellation handle after dispatch starts.
   - Cleanup should mark runtime bookkeeping as closed, but not attempt process-level kill.

2. subGM side thread
   - Durable story-side state under .agent_runs/<round>/side_threads/<thread_id>/.
   - May be running, merging, needs_gm, blocked, max_steps, paused, or completed.
   - If the turn is delivered while a thread is still active, the active runtime should be closed, but the side story should not be falsely completed.

Recommended behavior after successful delivery:

- Add agent_lifecycle.cleanup_round_agents(card_folder, run_dir, reason="delivered").
- For active side threads whose status is running, merging, needs_gm, blocked, or max_steps:
  - append a GM-visible lifecycle message explaining round-end cleanup;
  - set status to paused;
  - set next_resume_point if absent, for example "resume when the main GM schedules this side thread in a later round";
  - release active character reservations because paused is not active.
- Leave completed threads unchanged.
- Leave already paused threads unchanged except for optional audit history.
- Write cleanup result into manifest:

{
  "agent_lifecycle_cleanup": {
    "status": "complete",
    "reason": "delivered",
    "paused_side_threads": ["side_gate_noise"],
    "already_terminal": ["side_suli_rooftop"],
    "closed_invocations": ["story", "critic"]
  }
}

This satisfies the operational requirement that no subagent remains running after delivery, while preserving unfinished side-story continuity.

## Actor Context Versioning and Invalidation

### Context Version

Every player/character dispatch should carry an actor context version. The version is computed from actor-facing context inputs:

- actor ID
- profile files, such as profile.md and profile.json
- actor memory files, such as long_term.md, key_memories.md, short_term.md, and goals.json
- current per-round character context packet
- GM prompt or actor-call visibility basis

The exact version can be a SHA-256 hash over normalized text and JSON inputs. It should be written into the actor packet:

{
  "actor_id": "character:Ada",
  "context_version": {
    "hash": "sha256:...",
    "source_paths": [
      "memory/characters/Ada/profile.json",
      "memory/characters/Ada/long_term.md"
    ],
    "computed_at": "2026-06-20T18:58:14+08:00"
  }
}

### Conservative Invalidation Strategy

The user selected the conservative strategy:

- Do not interrupt an actor call that is already running.
- Accept the result of the already-running call even if profile files changed during that call.
- Before any later call to the same actor, recompute context version and rebuild the packet from latest persisted files.
- Record a lifecycle warning if a call returned with a stale version, but do not discard it.

This model avoids unreliable cancellation and half-written artifacts.

### Update Sources

Context invalidation should be triggered by these update paths:

- input_analysis_apply.py applying important-character or world updates.
- character_promotions.apply_promotions() writing or preserving character profile files.
- handler.evolve_blank_profile() updating _self profile data.
- post-round actor memory ingestion updating structured memory.
- any future explicit profile/background/personality update helper.

The first implementation does not need a global file watcher. It can recompute the version immediately before each actor dispatch. That makes stale contexts impossible for future calls even when updates happen through existing file writes.

## Runtime Integration Points

### handler.py

- Keep write_progress() as a compatibility wrapper.
- Add schema v2 support through round_state.write_progress_state() or equivalent.
- Preserve /api/progress response shape for old clients while returning v2 fields when available.

### round_prepare.py

- Replace direct free-form progress calls with state machine transitions:
  - round.preparing
  - input_analysis.awaiting

### rp_generate_cli.py

- Write progress around:
  - input analyst dispatch
  - input analysis apply
  - GM loop start/completion
  - story dispatch
  - story preflight repair
  - critic dispatch
  - delivery retry

### agent_turn_loop.py

- Write progress around:
  - GM dispatch
  - main actor batch dispatch
  - individual actor dispatch detail
  - player decision stop
  - GM loop completion
- Rebuild actor context immediately before dispatch.
- Attach context version to actor packets.

### subgm_turn_loop.py

- Write progress around:
  - runnable side-thread batch
  - individual subGM dispatch
  - side-thread actor calls
  - side-thread terminal or paused state

### round_deliver.py

- Write progress through:
  - delivery validation
  - frontend delivery
  - memory finalization
  - post-round memory scheduling/ingestion
  - lifecycle cleanup
  - completion
- Call agent_lifecycle.cleanup_round_agents() after successful delivery and memory scheduling/ingestion has been attempted.

## Error Handling

- If progress writing fails, the pipeline should continue and log or return a warning where practical.
- If lifecycle cleanup fails after successful delivery, do not remove delivered prose. Mark post-delivery cleanup as degraded in manifest and progress.
- If context version computation fails for one actor, dispatch should fail before model invocation rather than silently using unknown profile state.
- If a side-thread cleanup status write fails, preserve the existing side-thread files and report the failure in agent_lifecycle_cleanup.failed.

## Testing Plan

Add focused tests with no live model calls:

- round_state tests:
  - known states produce stable progress JSON;
  - terminal state rules are enforced;
  - old handler.read_progress() compatibility remains.

- frontend tests:
  - index.html renders schema v2 progress labels;
  - detail fields for agent, subGM thread, actor call, and retry are visible or available in the progress DOM.

- lifecycle tests:
  - delivered round pauses active side threads;
  - completed side threads remain completed;
  - paused side threads remain paused;
  - active reservations are released after cleanup;
  - cleanup result is written to manifest.

- actor context tests:
  - actor packets include context_version;
  - modifying a character profile before the next actor call changes context hash;
  - already-returned actor output is not discarded under conservative mode;
  - next call receives updated profile text.

- pipeline tests:
  - deterministic control_plane_smoke.py observes key state transitions;
  - rp_generate_cli.run_round() writes progress for input analyst, GM loop, Story, Critic, delivery, and cleanup;
  - delivery retry writes retry state with attempt detail.

## Documentation Updates

When implemented, update:

- README.md
  - describe schema v2 progress state machine and hybrid frontend display;
  - clarify round-end subagent cleanup semantics;
  - clarify actor context versioning and conservative invalidation.

- CLAUDE.md
  - update the data-flow paragraph to mention state machine progress and lifecycle cleanup.

- .claude/skills/rp-orchestrator.md
  - mention that progress is state-machine controlled and that delivery cleanup closes runtime subagent activity.

Do not update unrelated docs/superpowers/** files unless a future approved spec/plan requires it.

## Acceptance Criteria

- All backend progress updates use declared state IDs or compatibility wrappers.
- Frontend can display high-level and detailed progress from schema v2.
- Successful delivery leaves no active subGM side thread status in the current run.
- Unfinished subGM story state is paused, not incorrectly completed.
- Actor calls after profile/background/memory updates use fresh actor context.
- Already-running actor calls are not interrupted by profile updates.
- Deterministic tests cover progress, cleanup, and context invalidation.
- Full test suite, control-plane smoke, and py_compile checks pass before implementation is claimed complete.

## Spec Self-Review

- Placeholder scan: no unresolved placeholder markers remain.
- Consistency check: the design treats Claude Code local agents as one-off dispatches and subGM side threads as durable file state, avoiding contradictory lifecycle semantics.
- Scope check: this is a single implementation project with three bounded parts: state machine progress, lifecycle cleanup, and actor context invalidation.
- Ambiguity check: "close unclosed subagents" is defined operationally as closing runtime activity and pausing unfinished side-thread state after delivery.
