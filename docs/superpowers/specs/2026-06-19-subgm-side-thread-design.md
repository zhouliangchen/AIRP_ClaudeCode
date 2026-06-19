# subGM Side-Thread Design

## Terminology

- `subGM` is the scoped side-thread runner that replaces the old `gm_assistant` concept.
- A side thread is a GM-created branch under one main round. It is identified by a safe `thread_id`.
- The main GM owns the primary round, all player-facing decisions, character promotions, and final merge choices.
- A side thread may be omniscient inside its assigned boundary, but it is not globally authoritative outside that boundary.

## Authority Model

The main GM may start, message, accelerate, pause, resume, merge, or close side threads through `subgm_commands`.

A subGM can produce side-thread scene beats, events, world-state deltas, messages to GM, story notes, promotion requests, boundary requests, and a next resume point. A subGM cannot directly create or promote important characters, spawn another subGM, include the player character in side-thread actor calls, or change its own boundary. Promotion and boundary changes stay as requests for main-GM arbitration.

## Lifecycle

The implemented lifecycle is file-backed:

- `start` creates a running side thread.
- `message` appends a GM-to-subGM message.
- `accelerate` marks urgency as accelerated and appends a message.
- `pause` marks the side thread inactive and releases character reservations.
- `resume` rechecks character reservations and returns the side thread to running.
- `merge` marks the side thread for GM merge while reserving its characters.
- `close` marks the side thread completed and releases reservations.

`subgm_turn_loop.run_ready_side_threads` runs currently runnable side threads in deterministic sorted order, with parallel workers only when reservations do not overlap. A side thread can finish as `completed`, `paused`, `blocked`, `needs_gm`, or `max_steps`.

## Message Model

Messages are append-only JSONL records in each side thread. GM messages are written by `apply_gm_commands`; subGM messages are written by `append_subgm_message` or by the side-thread loop when subGM output changes status, scene beats, or resume point.

GM can read all side-thread messages through `load_messages_for_gm`, sorted by timestamp, thread id, and sequence. Thread summaries expose status, title, boundary, objective, allowed and forbidden characters, last scene beats, next resume point, and urgency.

## Character Occupancy Rules

Active side threads reserve their `allowed_characters`. The main GM cannot call an actor reserved by an active side thread, and two active side threads cannot reserve the same character. Paused and completed threads release reservations.

Side threads cannot allow `player`. SubGM actor calls must target existing important character contexts, must be within `allowed_characters`, must not be in `forbidden_characters`, and must use side-thread-safe character call ids.

## Artifact Layout

Side-thread artifacts live under:

```text
.agent_runs/<round>/side_threads/<thread_id>/
```

Implemented files include:

- `state.json` for lifecycle state, boundaries, reservations, urgency, resume point, and history.
- `messages.jsonl` for GM-to-subGM and subGM-to-GM messages.
- `interaction.trace.json` for the side-thread trace.
- `subgm.output.json` for the latest validated subGM output.
- `actor.outputs.json` when the side thread calls allowed character agents.

The main round continues to own `gm.output.json`, `actor.outputs.json`, `story.input.json`, `story.output.json`, `critic.report.json`, and final delivery.

## Story and Critic Handling

Side-thread traces and compact summaries are gathered into story-facing input through existing agent output assembly. Story can use side-thread material only as available source context; the main delivery path still goes through Story output and Critic approval.

Visibility guards apply to side-thread summaries, traces, subGM outputs, messages, and actor outputs. Hidden phrases and forbidden GM-only markers must not leak into actor-facing or story-facing side-thread artifacts.

## Remaining Limits

- The runner is deterministic and bounded, but live-model orchestration still depends on Claude Code prompts and file contracts.
- Side-thread merge policy is represented by state and messages; final narrative merge remains a main-GM and Story responsibility.
- Boundary and promotion requests are preserved for GM arbitration; automatic approval is not implemented.
- Cross-thread conflicts are handled through character reservations, not full physical simulation.
- The control-plane smoke covers one completed side thread and one paused side thread, but it is not a substitute for multi-turn live RP validation.
