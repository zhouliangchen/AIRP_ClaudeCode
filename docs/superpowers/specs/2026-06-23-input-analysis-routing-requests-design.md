# Input Analysis Routing Requests Design

## Purpose

User instructions can ask the system to perform capabilities that are not part of the normal story-generation path: update UI assets, generate images, consult on retcons, adjust save-folder character data, or request source-code changes. These requests are not caused by runtime failure, so they must not be governed by `selfRepairMode`.

This design adds a controlled routing layer to `input_analysis.output.json`. The input analyst can express user-requested follow-up work as structured `routing_requests[]`. Python control-plane code validates those requests, writes audit records, and creates safe intents or messages. Agents still do not directly write authoritative artifacts, arbitrary save files, or source code.

## Non-goals

- Do not let production code infer request semantics from player text with keywords, substrings, or regexes.
- Do not allow input analyst output to directly edit source code.
- Do not allow arbitrary save-folder file writes in the first implementation.
- Do not make UI/image work block story text delivery.
- Do not merge user-requested feature routing with critic/delivery self-repair policy.

## Current Context

The repository already has most of the required control-plane primitives:

- `input_analysis.py` validates `input_analysis.output.json`.
- `input_analysis_apply.py` applies validated analysis and rebuilds routed agent packets.
- `agent_dispatcher.py` executes pending intents.
- Existing supported intent types include `assets_task`, `rollback_request`, and `system_request`.
- Existing source-code self-repair is gated by `selfRepairMode == "full"` and `allowSourceCodeSelfRepair == true` because it is initiated by critic repair routing.

The missing piece is a user-requested routing schema and a safe bridge from validated input analysis to control-plane intents/messages.

## Data Contract

`input_analysis.output.json` will gain a top-level `routing_requests` array. It is required for schema version 1 after this change. Fallback analysis must write an empty array.

Each item has this shape:

```json
{
  "id": "route-001",
  "type": "assets_ui_task",
  "source_channel": "user_instruction",
  "summary": "Create a rainy street illustration for the current scene.",
  "target": "assets-ui",
  "payload": {
    "kind": "scene",
    "target": "scene_illustration",
    "prompt": "rainy street illustration"
  },
  "requires_authorization": false,
  "authorization_gate": "none",
  "evidence": {
    "semantic_unit_ids": ["unit-001"],
    "raw_excerpt": "Please create a rainy street image."
  }
}
```

Allowed `type` values:

- `assets_ui_task`
- `story_retcon_consult`
- `card_data_edit`
- `source_feature_request`

Allowed `source_channel` values:

- `user_instruction`
- `role_input`
- `raw_input`

Allowed `authorization_gate` values:

- `none`
- `allowSourceCodeSelfRepair`

Validation rules:

- `id`, `type`, `source_channel`, `summary`, `target`, `payload`, `requires_authorization`, `authorization_gate`, and `evidence` are required.
- `id`, `summary`, and `target` must be non-empty strings.
- `payload` and `evidence` must be objects.
- `requires_authorization` must be a boolean.
- `source_feature_request` must use `authorization_gate: "allowSourceCodeSelfRepair"` and `requires_authorization: true`.
- Non-source request types must use `authorization_gate: "none"` unless a later schema explicitly adds more gates.
- `evidence.raw_excerpt` must be a non-empty string.
- `evidence.semantic_unit_ids`, when present, must be a list of strings.
- The validator does not decide whether the user text contains a particular request. It only checks the model-produced structure.

## Prompt Contract

The input analyst prompt will include `routing_requests: []` in the JSON contract and explain:

- Use routing requests only for explicit user-requested system actions or save/world operations.
- Do not use routing requests for ordinary player actions that GM/story can handle.
- Do not classify runtime failures as routing requests.
- For source-code implementation requests, output `type: "source_feature_request"` and `authorization_gate: "allowSourceCodeSelfRepair"`.
- For image or UI data work, output `type: "assets_ui_task"`.
- For requested retcon, rollback, or replay discussion, output `type: "story_retcon_consult"`.
- For requested character/save data edits, output `type: "card_data_edit"`.

## Control-Plane Flow

The flow after validated input analysis becomes:

```text
analyze_input intent
-> input_analysis_apply.apply_current_run()
-> validated analysis + routed_input + routing_requests
-> append analysis_applied message
-> create routing follow-up intents/messages
-> create normal run_gm_turn follow-up
```

The normal story path remains available. Routing requests add controlled side work; they do not replace GM/story execution unless a specific request requires a rollback flow.

## Request Execution Mapping

### `assets_ui_task`

Create an existing `assets_task` intent.

Payload mapping:

- `kind`: from `payload.kind`, default `scene`
- `target`: from `payload.target`, default request id
- `prompt`: from `payload.prompt`, default `summary`
- `source`: `input_analysis.routing_requests.<id>`

The existing dispatcher behavior defers external asset generation and writes `artifacts/assets_tasks/<intent_id>.json`. It remains non-blocking.

### `source_feature_request`

Read current runtime settings from the run manifest or normalized settings payload.

- If `allowSourceCodeSelfRepair` is true, create a `system_request` intent.
- If `allowSourceCodeSelfRepair` is false, do not create an executable `system_request`; instead write an authorization-required audit artifact/message.

This path does not require `selfRepairMode == "full"` because it is a user-requested feature path, not critic self-repair.

The created `system_request` payload must mark:

- `reason: "user_requested_source_feature"`
- `authorization_gate: "allowSourceCodeSelfRepair"`
- `selfRepairMode_required: false`
- `source: "input_analysis.routing_requests"`
- original request id, summary, target, payload, and evidence

The existing `system_request` executor still blocks with `system_request_requires_main_agent`, which is acceptable: source work must be performed by the main agent workflow, not the dispatcher.

### `story_retcon_consult`

First implementation creates a GM/story-facing message and an audit artifact. It may create a `rollback_request` only when the request payload explicitly provides a safe rollback scope compatible with existing rollback executor fields.

This avoids inventing a parallel retcon mechanism. Later implementation can add a dedicated consult executor if needed.

### `card_data_edit`

First implementation writes an audit artifact/message and does not write arbitrary files.

Allowed immediate safe behavior:

- Preserve the request for GM/input-analysis review.
- Let existing safe paths handle already-supported character promotions or world updates.

Disallowed in the first implementation:

- Direct writes to arbitrary files under the save folder.
- Direct writes to character memory files outside existing validated memory/update code.

## Audit Artifacts and Messages

For every routing request, write an artifact under:

```text
artifacts/input_routing_requests/<request_id>.json
```

The artifact records:

- schema version
- request id/type
- status
- requested_by
- source round
- authorization result
- created intent ids
- created message ids
- original request payload

Statuses:

- `queued`
- `deferred`
- `authorization_required`
- `audit_only`
- `blocked`

Messages should be appended to the existing message bus so GM/main-agent/assets can see relevant requests through normal inbox projections.

## Authorization Policy

`selfRepairMode` is ignored for user-requested routing requests.

`allowSourceCodeSelfRepair` is required for every `source_feature_request`. If false:

- no executable `system_request` is created;
- an artifact and message record `authorization_required`;
- the main story path can continue unless future policy explicitly makes this blocking.

If true:

- a `system_request` intent is created;
- dispatcher will still block that intent as requiring main-agent workflow;
- the block is expected and auditable, not a self-repair failure.

## Error Handling

- Invalid `routing_requests` schema blocks input analysis apply.
- Unknown request type blocks input analysis apply.
- Failed intent/message creation blocks `analyze_input` because control-plane state would otherwise become inconsistent.
- Unauthorized source requests do not block `analyze_input`; they produce authorization-required audit output.
- Audit-only card data requests do not block `analyze_input`.
- Assets tasks remain non-blocking after they are queued.

## Testing Plan

Unit tests:

- `input_analysis.validate_input_analysis` accepts a valid `routing_requests[]`.
- It rejects unknown request types, missing fields, invalid authorization gates, and source requests without `allowSourceCodeSelfRepair` gate metadata.
- Fallback analysis includes an empty `routing_requests[]`.
- `input_analysis_apply.apply_current_run()` preserves and returns `routing_requests`.

Dispatcher tests:

- `analyze_input` creates `assets_task` for `assets_ui_task`.
- `analyze_input` creates authorization-required audit output for source requests when `allowSourceCodeSelfRepair` is false.
- `analyze_input` creates `system_request` for source requests when `allowSourceCodeSelfRepair` is true, even when `selfRepairMode` is not `full`.
- `card_data_edit` writes audit-only output and does not modify arbitrary card files.
- `story_retcon_consult` writes GM/story-facing request output and does not bypass existing snapshot/rollback controls.

Regression tests:

- No production keyword/regex semantic inference is introduced.
- Existing input-analysis fixtures without routing requests are updated or normalized consistently.
- Existing postprocess and delivery tests continue to pass.

## Documentation Updates

Update:

- `README.md`, to describe user-requested capability routing and the distinction from self-repair.
- `docs/重构建议.md`, if needed, to align current implementation notes after code lands.

Do not update `docs/superpowers/**` again during implementation unless the design itself changes.

## Acceptance Criteria

- User instruction channel requests can be represented as validated routing requests.
- Assets/UI requests create non-blocking assets tasks.
- Source feature requests are independent of `selfRepairMode`.
- Source feature requests still require `allowSourceCodeSelfRepair`.
- Save/character data edit requests are auditable and safe, with no arbitrary file writes.
- The normal story path remains functional.
- Relevant tests pass.
