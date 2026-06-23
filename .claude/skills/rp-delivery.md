---
name: rp-delivery
description: Use when a critic-approved RP response must be mirrored to response.txt and handed to the runtime pipeline.
---

## RP Delivery

Delivery is a mechanical final step after critic approval. It must not rewrite prose, alter player input, add new plot, or make narrative quality decisions such as word count, perspective, style, or tone.

## Preconditions

- `story.output.json` exists and its `content` field matches the response tag contract.
- `critic.report.json` exists. `decision="pass"` is required for successful frontend delivery; `revise` / `block` reports still run through the artifact gate to record retry state and repair intents.
- Postprocess is required before delivery. Delivery must see valid `postprocess.output.json.core` before it mirrors story prose to the frontend.
- Any `<character_dialogues>` entries correspond to actual subagent outputs.
- Image/UI jobs are optional follow-up work. Pending, deferred, failed, or unconfigured asset work must not block critic-approved text delivery.
- Invalid UI extension data is nonblocking only when a repair record is written for the next round, including a repair artifact and `.agent_runs/postprocess_repair_queue.jsonl` entry.

## Delivery Steps

1. Confirm `.agent_runs/<round>/manifest.json` points to the expected outputs.
2. Run:

```powershell
python "{ROOT}/skills/round_deliver.py" "<card_folder>" "{ROOT}"
```

3. `round_deliver.py` performs mechanical artifact checks, rebuilds `story.input.json` if needed, validates `postprocess.output.json.core`, mirrors critic-approved `story.output.json.content` to `skills/styles/response.txt`, invokes `handler.py`, ingests subagent memory deltas plus scheduled `memory_summaries/*.summary.json`, and marks the manifest `delivered`. It does not reject prose for length, perspective, style, NSFW, or other narrative quality concerns; those belong to the critic stage. Summary ingestion failures are reported as `agent_memory_error` without blocking an already approved text delivery.
4. If `round_deliver.py` returns `{"action":"retry"}`, pass the reason back to the orchestrator and do not fabricate success. If it returns `{"action":"blocked"}`, stop automatic delivery/repair and surface the terminal reason for manual intervention. Critic `revise` / `block` reports are recorded in `repair_history.jsonl`; on those recorded reports, non-empty `system_iteration_suggestion` entries are appended to `.agent_runs/improvement_queue.jsonl`.
5. If delivery succeeds, optional `rp-assets-ui` work may continue asynchronously. Dispatcher-level `assets_task` records are nonblocking status artifacts/messages only and are never prerequisites for `deliver_round`. Pending postprocess UI-extension repairs are surfaced in the next round context; do not silently ignore them.

After approved delivery, post-round actor memory jobs may be scheduled for participating actors. Missing or failed post-round jobs mark `post_round_memory_jobs.status` as `pending` or `degraded_memory_state`; they must not remove already delivered prose. The next round surfaces degraded memory state instead of silently ignoring it.

Only the orchestrator/main agent performs these steps. Subagents never write `skills/styles/response.txt`.
