---
name: rp-delivery
description: Use when a critic-approved RP response must be mirrored to response.txt and handed to the runtime pipeline.
---

## RP Delivery

Delivery is a mechanical final step. It must not rewrite prose, alter player input, or add new plot.

## Preconditions

- `story.output.json` exists and its `content` field matches the response tag contract.
- `critic.report.json` exists with `decision` set to `pass`, or the orchestrator has explicitly chosen a documented fallback.
- Any `<character_dialogues>` entries correspond to actual subagent outputs.
- No image/UI job is still blocking text delivery.

## Delivery Steps

1. Confirm `.agent_runs/<round>/manifest.json` points to the expected outputs.
2. Run:

```powershell
python "{ROOT}/skills/round_deliver.py" "<card_folder>" "{ROOT}"
```

3. `round_deliver.py` validates GM/player/character/story/critic artifacts, rebuilds `story.input.json` if needed, mirrors `story.output.json.content` to `skills/styles/response.txt`, invokes `handler.py`, ingests subagent memory deltas, and marks the manifest `delivered`.
4. If `round_deliver.py` returns `{"action":"retry"}`, pass the reason back to the orchestrator and do not fabricate success.
5. If delivery succeeds, optional `rp-assets-ui` work may continue asynchronously.

Only the orchestrator/main agent performs these steps. Subagents never write `skills/styles/response.txt`.
