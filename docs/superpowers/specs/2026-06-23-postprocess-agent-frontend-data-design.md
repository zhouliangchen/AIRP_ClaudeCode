# Postprocess Agent Frontend Data Design

## Status

Drafted on 2026-06-23 after the critic-owned quality refactor was merged to `master`.

This spec adds a postprocess agent between critic approval and mechanical delivery. It is focused on frontend data that is outside the main narrative prose.

## Purpose

The current runtime still lets the story agent provide both narrative prose and frontend support data through `<summary>` and `<options>` tags. `state.js` also owns `quest` and other panel fields without a stable agent-level producer.

The new design separates responsibilities:

- story agent writes only deliverable prose and source-backed character dialogue;
- critic agent reviews only the approved story body and narrative quality;
- postprocess agent writes frontend data needed after a critic pass;
- delivery remains a mechanical executor and schema gate.

## Non-Goals

- Do not move progress bar or reply-state ownership to postprocess. `round_state.py` remains authoritative for `progress.json`.
- Do not let postprocess rewrite story prose.
- Do not let critic request, shape, or validate summary/options/current-goal requirements.
- Do not make assets/UI generation block text delivery.
- Do not infer player intent from fixed keyword, substring, or regex rules.
- Do not preserve old `<summary>` and `<options>` story tags as long-term production contracts.

## Target Turn Flow

```text
input analysis
  -> GM / projection / actor / subGM dispatcher loop
  -> story agent
  -> critic agent
  -> postprocess agent
  -> delivery gate
  -> handler.py / content.js / state.js / frontend
```

Detailed flow:

1. Story composes `story.output.json`.
2. Critic reviews story body only and writes `critic.report.json`.
3. If critic passes, dispatcher creates `run_postprocess`.
4. Postprocess reads the accepted narrative artifacts, current frontend state, current UI manifest/assets context, and any pending postprocess repair queue.
5. Postprocess writes `artifacts/postprocess.output.json` and root `postprocess.output.json`.
6. Delivery validates:
   - critic decision is `pass`;
   - postprocess core data is present and schema-valid;
   - UI extension failures are recorded as nonblocking repair work.
7. Delivery mirrors story prose to `response.txt`, invokes `handler.py`, and applies postprocess frontend data.

## Agent Boundaries

### Story Agent

Owns:

- `<content>` main prose;
- source-backed `<character_dialogues>` or `character_dialogues` metadata;
- story-only metadata.

Does not own:

- `<summary>`;
- `<options>`;
- current goal / quest;
- status panels;
- UI extension data.

Target `story.output.json`:

```json
{
  "content": "<content>...</content><character_dialogues>[...]</character_dialogues>",
  "character_dialogues": [],
  "metadata": {
    "round_id": "round-000001"
  }
}
```

### Critic Agent

Owns narrative quality only:

- prose quality;
- style and word-count checks;
- perspective;
- player authority;
- hidden-fact leakage in visible prose and character dialogue;
- continuity and repair routing for story/GM/actor/subGM/system-code failures.

Does not own:

- summary quality;
- action option generation;
- current goal generation;
- frontend panel data;
- UI extension repair requirements.

Critic should not include postprocess instructions in `critic.report.json`. If postprocess fails, that failure is handled by delivery/postprocess repair policy, not by critic.

### Postprocess Agent

Owns all frontend support data outside the main story body:

- story summary;
- next action suggestions;
- current goal / quest;
- frontend status panel fields;
- UI extension data for current `ui_manifest.json`, `.beautify_template.html`, generated assets, or assets/UI tasks;
- nonblocking repair records for failed UI extension data.

Postprocess does not rewrite story prose. If it detects a story problem, it may only report a postprocess-blocking dependency failure; it must not edit `story.output.json`.

## Postprocess Output Contract

`postprocess.output.json` must be a JSON object:

```json
{
  "schema_version": 1,
  "core": {
    "summary": "One concise player-visible recap of the delivered turn.",
    "options": [
      {
        "label": "Look closer at the pendant",
        "source": "postprocess",
        "requires_confirmation": false
      }
    ],
    "current_goal": "Confirm what changed after the pendant reacted.",
    "state_patch": {
      "quest": "Confirm what changed after the pendant reacted."
    }
  },
  "ui_extensions": {
    "status_panels": {},
    "custom_cards": {},
    "asset_bindings": {}
  },
  "ui_extension_status": {
    "status": "ok",
    "issues": []
  },
  "repair_requests": [],
  "metadata": {
    "round_id": "round-000001",
    "source": "postprocess"
  }
}
```

### Core Fields

Core fields are delivery-blocking:

- `core.summary`: nonempty string, player-visible, no hidden facts or prompt analysis.
- `core.options`: nonempty array unless the turn is terminal; each item must have a nonempty `label`.
- `core.current_goal`: nonempty string.
- `core.state_patch`: optional object limited to frontend state fields that are safe to update, initially `quest`, `stage`, `time`, `location`, `env`, and `actions`.

Delivery must block if core fields are missing, malformed, or unsafe.

### UI Extension Fields

UI extension fields are nonblocking:

- `ui_extensions.status_panels`;
- `ui_extensions.custom_cards`;
- `ui_extensions.asset_bindings`;
- future keys declared by `ui_manifest.json`.

If these fields are malformed or cannot satisfy the active UI/assets manifest, delivery may still proceed, but the failure must produce an async repair record.

## Player Critical Action Option Rule

When the player agent ends the round by attempting a high-risk or critical action, that action must become a fixed option in `core.options`.

Sources include:

- player-agent `custom_action` with `risk_level` of `high` or `critical`;
- player-agent `stop_for_player_decision`;
- dispatcher/player-decision trace evidence that records a proposed action requiring real player confirmation.

The option should be explicit and confirmation-oriented:

```json
{
  "label": "Confirm action: push open the sealed door",
  "source": "player_agent_critical_action",
  "requires_confirmation": true
}
```

Postprocess may add other options, but it must not omit this fixed option.

Python should derive only deterministic evidence that such an action exists from structured artifacts. It must not infer critical player actions from free-text keyword matching.

## Async UI Extension Repair

UI extension data failure is nonblocking but not silent.

When `ui_extensions` fail validation or cannot satisfy current UI/assets needs:

1. Delivery still proceeds if core data is valid.
2. Runtime writes a repair item to the current run, for example:
   - `artifacts/postprocess_repairs/<repair_id>.json`;
   - and/or card-level `.agent_runs/postprocess_repair_queue.jsonl`.
3. Next round preparation exposes pending repair items to postprocess.
4. Postprocess attempts to complete pending UI extension data using the latest story state, UI manifest, assets task records, and generated asset metadata.
5. Repair completion updates the repair item status to `completed`.
6. Repeated failure keeps the item `pending` or `degraded`, but does not block story delivery unless the failure affects core fields.

Repair item shape:

```json
{
  "schema_version": 1,
  "id": "postprocess-repair-...",
  "round_id": "round-000001",
  "status": "pending",
  "scope": "ui_extensions",
  "reason": "missing data for new relationship panel",
  "required_keys": ["ui_extensions.status_panels.relationships"],
  "source_artifacts": [
    "artifacts/postprocess.output.json",
    "artifacts/assets_tasks/intent-000004.json"
  ],
  "attempts": 1
}
```

## Delivery Gate Changes

Delivery must require:

- `story.output.json` exists and passes existing story schema;
- `critic.report.json.decision == "pass"`;
- `postprocess.output.json` exists;
- postprocess core schema is valid;
- player critical action fixed-option rule is satisfied when structured evidence exists.

Delivery must not require:

- successful UI extension data when core data is valid;
- progress bar data from postprocess;
- story-owned `<summary>` or `<options>` tags.

Delivery should pass structured postprocess data to `handler.py` rather than asking handler to parse summary/options from story prose tags.

## Handler And Frontend Consumption

`handler.py` should consume postprocess data directly:

- `core.summary` -> `window.SUMMARY_TEXT`;
- `core.options` -> `window.TURN_OPTIONS`;
- `core.state_patch.quest` or `core.current_goal` -> `STATE.quest`;
- `ui_extensions` -> a new frontend data object, for example `window.POSTPROCESS_UI`.

For migration:

- tests may keep temporary fallback coverage for existing `<summary>` / `<options>` fixtures;
- production live path should prefer `postprocess.output.json`;
- once postprocess is required in delivery, story tags should no longer be the authoritative source for summary/options.

## Dispatcher Changes

Add a supported intent type:

```text
run_postprocess
```

Critic pass flow changes from:

```text
review_critic -> deliver_round
```

to:

```text
review_critic -> run_postprocess -> deliver_round
```

`run_postprocess` should:

- dispatch `postprocess`;
- write `artifacts/postprocess.output.json`;
- export root `postprocess.output.json`;
- create `deliver_round` only after core validation succeeds;
- block with a clear postprocess reason when core validation fails;
- record nonblocking UI extension repair items when extension validation fails.

## Prompt And Skill Changes

Add `.claude/skills/rp-postprocess-agent.md`.

Update:

- `rp-story-agent.md` to remove summary/options ownership;
- `rp-critic-agent.md` to state that critic does not review or request summary/options/current-goal/UI data;
- `rp-delivery.md` to require postprocess core validation before frontend delivery;
- `rp-orchestrator.md` to include the new postprocess step after critic pass.

## Error Handling

### Missing Postprocess Output

Block delivery with `postprocess_missing`.

### Invalid Core Data

Block delivery with `postprocess_core_invalid`. Repair route should be postprocess-only, not story/critic.

### Missing Critical Action Option

Block delivery with `postprocess_missing_player_action_option` when structured trace evidence proves the player agent proposed a high-risk/critical action.

### Invalid UI Extensions

Deliver story if core is valid. Record `postprocess_ui_extension_repair_pending` and surface repair metadata in manifest/debug artifacts.

### Stale Repair Queue Items

If an old UI repair no longer applies because UI manifest/assets changed, postprocess may mark it `superseded` with evidence.

## Testing Strategy

Add focused tests for:

- critic pass creates `run_postprocess`, not direct `deliver_round`;
- `run_postprocess` creates `deliver_round` only when core data is valid;
- missing `summary`, empty `options`, or missing `current_goal` blocks delivery;
- UI extension validation failure records async repair but does not block delivery;
- next round context exposes pending postprocess repair items;
- player high/critical custom action must appear as a fixed option;
- story prompt no longer requires `<summary>` or `<options>`;
- critic prompt says frontend data is out of critic scope;
- handler consumes postprocess output for `SUMMARY_TEXT`, `TURN_OPTIONS`, and `STATE.quest`;
- control-plane smoke includes `run_postprocess` between `review_critic` and `deliver_round`.

Verification commands:

```powershell
python -m unittest discover -s tests -v
python skills/control_plane_smoke.py --repo .
python -m py_compile skills/agent_dispatcher.py skills/agent_outputs.py skills/agent_schemas.py skills/agent_prompts.py skills/handler.py skills/round_deliver.py skills/round_prepare.py skills/control_plane_smoke.py
```

## Migration Plan

Implement in phases:

1. Add postprocess schema/helper and tests.
2. Add postprocess prompt/skill and dispatcher intent.
3. Change critic pass routing to `run_postprocess`.
4. Make delivery require postprocess core.
5. Move handler summary/options/state updates to postprocess output.
6. Remove story-owned summary/options requirements from prompts and live tests.
7. Add async UI extension repair queue.
8. Update README and relevant docs.

During migration, fixture tests may still include old story tags, but live delivery should use postprocess as soon as `run_postprocess` is required.

## Risks And Controls

- Risk: adding an agent increases latency.
  - Control: postprocess is narrow, structured, and runs only after critic pass.

- Risk: postprocess duplicates critic quality review.
  - Control: prompt and schema explicitly forbid prose review and rewriting.

- Risk: UI extension repair queue grows stale.
  - Control: each repair item has status, attempts, source artifacts, and `superseded` handling.

- Risk: delivery becomes a subjective UI judge.
  - Control: delivery validates schema and deterministic critical-action coverage only; postprocess owns generation.

## Self-Review

- Placeholder scan: no TODO or TBD remains.
- Consistency check: critic owns only prose review; postprocess owns frontend data; delivery owns mechanical validation.
- Scope check: this is one coherent refactor centered on a single new agent and output contract.
- Ambiguity check: core data blocks delivery; UI extensions do not block but must enter async repair.
- Safety check: no production semantic inference from raw player text is introduced.
