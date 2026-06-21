# Agent Message Runtime Refactor Design

## Purpose

The current RP runtime already has a multi-agent file mailbox, isolated actor packets, subGM side threads, interaction traces, critic gates, and deterministic smoke coverage. The remaining architectural problem is that Python still owns too much of the creative turn flow. The main goal of this refactor is to increase agent autonomy and collaboration while keeping Python responsible for protocol safety, visibility, auditability, persistence, and delivery gates.

This is an intentionally deep refactor. It changes the runtime axis from a fixed workflow into a message-driven agent runtime:

- Agents collaborate through structured messages and intents.
- Python validates, routes, projects, snapshots, materializes artifacts, and gates delivery.
- Python does not decide creative story progression details except where a policy boundary requires an accept, reject, rollback, or block decision.

## Non-Goals

- Do not remove player raw-input authority. `.player_inputs.jsonl` remains the source of truth for user-authored input.
- Do not allow production code to infer user-input semantics from fixed keywords, substrings, or regex matches.
- Do not let free-form agent messages bypass projection, visibility checks, schema validation, trace recording, or critic delivery gates.
- Do not introduce compatibility layers for obsolete generated game files or old runtime APIs.
- Do not turn the project into a backend LLM API scheduler. Claude Code remains the live orchestrator.

## Recommended Approach

Use a message bus and intent dispatcher as the new first-class runtime model.

Python remains the protocol kernel. It owns:

- Agent message persistence and ACL checks.
- Intent acceptance, rejection, execution, and status transitions.
- Projection and visibility gates for actor-facing content.
- Schema validation for all accepted intents and materialized artifacts.
- Interaction trace and source-call audit data.
- Per-turn snapshots and rollback execution.
- Story, critic, delivery, memory, and lifecycle gates.

Agents own:

- Deciding who they need to consult.
- Asking for projection, actor reaction, story writing, repair, side-thread work, or system handling.
- Negotiating story and critic repairs within allowed channels.
- Producing creative and analytical content inside strict output contracts.

## Runtime Layout

Each `.agent_runs/<round>/` directory becomes a round message space:

```text
.agent_runs/<round>/
  manifest.json
  input.json
  messages.jsonl
  inboxes/
    input_analyst.jsonl
    gm.jsonl
    story.jsonl
    critic.jsonl
    player.jsonl
    character_<safe_id>.jsonl
    subgm_<thread_id>.jsonl
    assets_ui.jsonl
    main_agent.jsonl
  intents/
    pending/
    accepted/
    rejected/
    completed/
  artifacts/
    gm.output.json
    actor.outputs.json
    story.input.json
    story.output.json
    critic.report.json
  trace/
    interaction.trace.json
  snapshots/
```

Migration can keep legacy root artifact paths for a short transition, but new code should treat `messages`, `intents`, and `artifacts` as the authoritative runtime surface. The transition paths are for incremental verification, not for old-save compatibility.

## Message Envelope

All agent-to-agent communication uses one normalized envelope:

```json
{
  "id": "msg_000001",
  "round_id": "round-000001",
  "created_at": "2026-06-21T12:00:00+08:00",
  "from": "gm",
  "to": ["story"],
  "type": "message",
  "visibility": "gm_only|story_facing|actor_facing|public",
  "thread_id": "",
  "source_call_id": "",
  "reply_to": "",
  "payload": {},
  "status": "queued|delivered|rejected|consumed",
  "policy": {
    "requires_projection": false,
    "requires_trace": true
  }
}
```

Rules:

- `messages.jsonl` is append-only.
- `inboxes/*.jsonl` are indexes derived from accepted messages.
- Messages with `actor_facing` visibility must be projected before delivery to player or character agents.
- Messages with rejected ACL or schema status stay audit-visible but are not delivered to inboxes.
- `source_call_id` links messages, intents, actor responses, and trace events.

## Core Message Types

Start with a small stable set:

- `message`: ordinary agent communication.
- `request_actor`: GM or subGM asks a player or character actor for reaction, perception, dialogue, or action.
- `request_projection`: asks the projection gate to prepare actor-visible content.
- `projected_message`: projection gate output, ready for an actor inbox.
- `actor_response`: player or character response to an approved actor request.
- `request_story`: GM declares the raw turn material is ready for story composition.
- `story_response`: story agent returns polished story output.
- `critic_review`: critic agent returns pass, revise, or block.
- `repair_request`: critic, story, GM, or main agent requests a bounded repair path.
- `snapshot_request`: asks control plane to create or restore a snapshot.
- `system_request`: input analyst asks the main agent to consider code, tooling, UI, or workflow changes.
- `asset_request`: story or main agent asks assets-ui to generate images or save-level UI changes asynchronously.

## Intent Model

Messages are communication. Intents are executable requests.

Intent files live under `intents/pending/` until the dispatcher accepts or rejects them:

```json
{
  "id": "intent_000001",
  "round_id": "round-000001",
  "created_at": "2026-06-21T12:00:00+08:00",
  "requested_by": "gm",
  "type": "dispatch_actor|project_message|create_subgm|snapshot|rollback|materialize_story_input|deliver",
  "source_message_id": "msg_000003",
  "payload": {},
  "policy": {
    "risk": "low|medium|high|critical",
    "requires_human": false,
    "requires_self_repair_mode": "off|analysis_only|limited|full"
  }
}
```

The dispatcher writes a result:

```json
{
  "intent_id": "intent_000001",
  "status": "accepted|rejected|completed|blocked",
  "reason": "",
  "outputs": {},
  "trace_refs": []
}
```

This separates agent initiative from control-plane execution. Agents can ask for powerful actions, but only the dispatcher can execute them.

## ACL And Authority Rules

These rules are hard boundaries:

- `player` and `character:*` may send only to `gm` or an allowed `subGM:*`.
- `player` cannot trigger critical actions directly. If GM judges an actor response as critical, the turn stops at a real player decision point.
- `gm` may send to any story-runtime agent, but actor-facing messages must go through projection.
- `subGM:*` may send to `gm`, allowed characters in its boundary, and projection. It cannot create major characters, promote characters, include the player, change its own boundary, or spawn another subGM.
- `input_analyst` may send semantic routing and system requests, but cannot execute rollback, code changes, or file mutation outside validated input-analysis application.
- `story` may request projection checks for player-perspective filtering and may send repair questions to `gm` or `critic`.
- `critic` is the only delivery quality gate. It can request story-only repair, round-progression rollback, or a blocked human decision according to self-repair policy.
- `assets_ui` runs after text delivery or in a nonblocking async lane. It must not delay story delivery.
- `main_agent` handles code, tooling, workflow, and explicitly approved system actions.

## Projection Gate

Projection becomes a first-class gate, not a helper hidden inside actor dispatch.

Flow:

1. GM or subGM sends `request_actor`.
2. Dispatcher converts it into `request_projection`.
3. Projection reads only the allowed world state, actor state, memory, visible events, and visibility basis.
4. Projection writes `projected_message`.
5. Dispatcher delivers `projected_message` to the actor inbox and records trace linkage.

Projection may ask GM or subGM for a rewrite if the message has insufficient visibility basis or contains actor-forbidden data. The final actor packet is still created by deterministic projection code and visibility guards.

## Turn Data Flow

Target flow after the refactor:

1. Browser records raw player input and pending display.
2. `round_prepare.py` creates the round message space and initial messages for `input_analyst` and `gm`.
3. Input analyst writes validated input-analysis output and optional messages or system requests.
4. GM drives main story progression by sending messages and intents.
5. Dispatcher processes pending intents:
   - project actor-facing messages,
   - dispatch actor calls,
   - run subGM threads,
   - materialize trace events,
   - enforce ACL and reservation rules.
6. GM requests story composition once main and relevant side-thread material is ready.
7. Story writes polished output from `story.input.json` materialized from messages, trace, and artifacts.
8. Critic reviews the story.
9. Dispatcher either accepts delivery, routes repair, restores snapshot, or blocks.
10. `round_deliver.py` handles frontend delivery, memory ingestion, post-round memory jobs, and lifecycle cleanup.

## Snapshot And Rollback

Before each player input is processed, the runtime creates a snapshot of the active card state:

- `chat_log.json`
- `.player_inputs.jsonl` reference position, not rewritten content
- memory files
- `.card_data.json`
- `.initvar.json`
- UI manifest and save-level UI files
- `.agent_runs/current` pointer
- relevant runtime state files

Rollback requests are intents. They may come from player edit APIs, critic repair, or input analyst conflict analysis, but execution belongs to the dispatcher.

Rollback modes:

- `story_only`: discard story and critic artifacts, keep GM and actor progression.
- `round_progression`: discard GM, actor, subGM, story, and critic derived artifacts for the current round.
- `historical_branch`: restore a prior snapshot, archive later derived branches, and resubmit revised player input.
- `blocked`: record the request but require human decision.

## Artifact Materialization

Artifacts remain useful because tests, delivery, and manual debugging depend on stable files.

The new runtime materializes artifacts from messages and completed intents:

- `gm.output.json` from accepted GM progression messages and GM intents.
- `actor.outputs.json` from actor responses linked to approved projected actor requests.
- `story.input.json` from visible story-facing messages, trace summaries, side-thread summaries, memory deltas, and player input authority.
- `story.output.json` from story response.
- `critic.report.json` from critic review.

The system should prefer current-format artifacts only. Do not add long-lived adapters for obsolete formats unless explicitly requested.

## Error Handling

Every rejected message or intent must be visible in audit files with a structured reason.

Common failures:

- `acl_rejected`: sender is not allowed to contact target.
- `projection_required`: actor-facing message was sent without projection.
- `visibility_rejected`: hidden data or insufficient visibility basis.
- `schema_rejected`: invalid message, intent, or artifact payload.
- `reservation_conflict`: actor is already reserved by an active subGM side thread.
- `snapshot_missing`: rollback target is unavailable.
- `self_repair_mode_blocks_route`: policy disallows requested repair.
- `critic_retry_limit`: repair loop exceeded configured bounds.

Blocked states must preserve all evidence and avoid partial delivery.

## Testing Strategy

Add focused tests with no live model calls:

- Message envelope append, read, inbox indexing, and malformed JSON rejection.
- ACL matrix for input analyst, GM, subGM, story, critic, player, character, assets-ui, and main agent.
- Projection-required enforcement for all actor-facing routes.
- Intent dispatcher accept, reject, complete, and blocked transitions.
- GM `request_actor` to projected actor inbox to actor response to trace.
- Character-to-character dialogue transfer through GM or subGM, not direct actor-to-actor delivery.
- subGM side-thread migration to common messages.
- Critic repair as `repair_request`, including story-only and round-progression rollback.
- Snapshot creation and rollback, including branch submit.
- Story input materialization from messages and trace.
- Deterministic control-plane smoke over the new runtime.
- Preservation of raw player input.

Existing visibility and hidden-phrase tests should remain active and be moved only when the new modules own their behavior.

## Migration Plan

### Phase 1: Message Bus Foundation

Create `skills/agent_messages.py` with:

- Envelope normalization.
- Append-only message log.
- Inbox indexing.
- ACL validation.
- Audit records for rejected messages.

No existing behavior changes in this phase.

### Phase 2: Intent Dispatcher

Create `skills/agent_intents.py` with:

- Intent creation.
- Pending, accepted, rejected, completed, and blocked directories.
- Dispatcher result records.
- Tests for actor dispatch, projection, subGM commands, repair, and rollback request intents.

### Phase 3: Projection As Mandatory Route

Refactor actor-facing delivery:

- GM and subGM no longer directly produce actor packets.
- `request_actor` must create `request_projection`.
- `agent_projection.py` remains deterministic and becomes the required packet materializer.

### Phase 4: Message-Driven GM And Actor Loop

Replace the center of `agent_turn_loop.py` with a dispatcher loop:

- Read pending intents and deliverable messages.
- Dispatch GM, projection, actor, and subGM work as needed.
- Stop only on delivery-ready, player-decision, max-steps, or blocked states.

This phase can delete or greatly shrink the current fixed while-loop code after tests cover equivalent behavior.

### Phase 5: Story, Critic, Repair, And Rollback

Refactor story and critic flow:

- Story is requested through `request_story`.
- Critic writes `critic_review`.
- Revise and block become `repair_request` intents.
- `selfRepairMode` determines whether dispatcher executes story-only repair, round-progression rollback, or blocks.
- Snapshot and rollback become standard dispatcher capabilities.

### Phase 6: Documentation And Acceptance

Update README, CLAUDE, AGENTS, and relevant docs after behavior changes are implemented.

Acceptance commands:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_messages.py skills/agent_intents.py skills/agent_turn_loop.py skills/agent_outputs.py skills/rp_generate_cli.py skills/round_prepare.py skills/input_analysis_apply.py
python skills/start_server.py .
```

Manual acceptance still requires a blank-folder `/rp` run for at least five player turns, including important-character dialogue boxes, progress updates, hot UI or image refresh, LAN access, and player decision stops.

## Open Decisions

These are implementation decisions, not design blockers:

- Whether inbox files are fully materialized JSONL indexes or generated on demand from `messages.jsonl`.
- Whether `artifacts/` becomes the only artifact location immediately or legacy root paths are mirrored for one migration phase.
- Whether the first dispatcher loop is a new module or a replacement inside `agent_turn_loop.py`.
- Whether MCP tools are added after the Python message APIs stabilize. The recommended order is Python API first, MCP wrapper second.

## Self-Review

- No placeholders or deferred requirements are left in this design.
- The design keeps the approved boundary: Python does not own creative flow details, but it owns protocol gates and execution rights.
- The scope is large but decomposed into independently testable phases.
- The most dangerous freedom, direct actor-to-actor or unprojected GM-to-actor messaging, is explicitly rejected.
