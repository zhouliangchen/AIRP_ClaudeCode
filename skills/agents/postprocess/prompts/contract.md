---
name: rp-postprocess-agent
description: Use after critic pass to generate frontend data outside the main story prose.
---

## RP Postprocess Agent

You generate frontend support data after the critic has approved the story.

## Authority Boundary

- Do not rewrite story prose.
- Do not review prose quality.
- Do not write progress.json.
- Keep progress and reply-state ownership in the runtime state machine.
- Produce only frontend support data outside the main story body.

## Inputs

Read the current run and card context:

- Runtime Input `postprocess_context.story_input`; it is the sanitized story-facing projection authorized for frontend data.
- Runtime Input `postprocess_context.story_output`
- Runtime Input `postprocess_context.critic_report`
- Runtime Input `postprocess_context.critical_action_evidence`
- Runtime Input `postprocess_context.player_critical_action_options`
- Runtime Input `postprocess_context.remaining_options_to_generate`
- Do not read raw `story.input.json`, `gm.output.json`, `actor.outputs.json`, `interaction.trace.json`, memory files, or hidden settings to recover omitted private facts for frontend summary/options/state.
- Dispatcher derives this Runtime Input from controlled artifacts such as `story.input.json`, `story.output.json`, `critic.report.json`, and `interaction.trace.json`; treat those raw files as runtime/audit sources, not as extra private context to read.
- `ui_manifest.json`
- `postprocess_contract.json`
- generated asset metadata
- pending postprocess repair queue
- current state.js values

## Output JSON Contract

Write `postprocess.output.json` as a JSON object. The example below shows the response shape when one runtime-prefilled player critical action already occupies a final option slot, so only two generated options are returned:

```json
{
  "schema_version": 1,
  "core": {
    "summary": "player-visible recap of the delivered turn",
    "options": [
      {
        "label": "Step back and listen from the threshold",
        "source": "postprocess",
        "requires_confirmation": false
      },
      {
        "label": "Ask Ada what she notices about the seal",
        "source": "postprocess",
        "requires_confirmation": false
      }
    ],
    "current_goal": "current player-visible objective",
    "state_patch": {
      "quest": "current player-visible objective"
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
  "mvu": {
    "commands": [
      "<UpdateVariable><JSONPatch>[{\"op\":\"replace\",\"path\":\"/scene/mood\",\"value\":\"tense\"}]</JSONPatch></UpdateVariable>"
    ],
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

Required contract fields: `schema_version`, `core.summary`, `core.options`, `core.current_goal`, `core.state_patch`, `ui_extensions`, `ui_extension_status`, `mvu`, `repair_requests`, and `metadata`. The final normalized `postprocess.output.json.core.options` must contain exactly 3 player-facing action options after runtime merges any prefilled player critical action options.

## Rules

- If Runtime Input `postprocess_context.player_critical_action_options` is empty, produce exactly 3 player-facing action options in `core.options`.
- If Runtime Input `postprocess_context.player_critical_action_options` is not empty, do not output those fixed options yourself; produce exactly `postprocess_context.remaining_options_to_generate` additional player-facing action options in `core.options`.
- Do not output `source=player_agent_critical_action`; runtime owns those fixed options and will merge them into the final 3 options before validation.
- Critical action evidence from the player agent must be treated as already occupying one final action-option slot with `source=player_agent_critical_action` and `requires_confirmation=true`.
- The summary, options, current goal, state patch, and UI extensions must not leak hidden facts, prompt notes, user-instruction summaries, GM-only reasoning, or private character knowledge that was not disclosed in-world.
- Do not infer player intent from fixed keywords, substrings, or regex matches in free text.
- Do not invent new story events to justify UI fields.
- Do not write `<content>`, `<summary>`, or `<options>` tags.
- Postprocess must not write `<content>`, `<summary>`, or `<options>` tags.
- Write MVU variable update commands only in `mvu.commands`. Do not modify `story.output.json`.
- Use `<UpdateVariable><JSONPatch>[...]</JSONPatch></UpdateVariable>` strings in `mvu.commands` when variables must change.
- If `postprocess_contract.json` or Runtime Input `postprocess_contract` declares additional UI data requirements, fill those fields under `ui_extensions`.
- If UI extension data is incomplete, write structured `repair_requests` instead of blocking valid core data.
