---
name: rp-delivery
description: Use when RP story and critic artifacts must pass the delivery gate or record a repair/block decision.
---

## RP Delivery

Delivery is a mechanical gate. It must not rewrite prose, alter player input, or add new plot.

## Contract

- Verify `story.output.json` exists.
- Verify `critic.report.json` exists.
- Use `{ROOT}/skills/round_deliver.py` for delivery.
- Invoke the gate even when the critic decision is `revise` or `block`.
- Let `round_deliver.py` record `repair_history.jsonl`, append any `system_iteration_suggestion`, and return `retry` or `blocked` for failed gates.
- Let `round_deliver.py` mirror approved content to `skills/styles/response.txt` only when the critic passes.
- Let `handler.py` update `chat_log.json`, clear pending state, rebuild `content.js`, update `state.js`, and notify the frontend.
- Preserve `<character_dialogues>` and `source="subagent"` metadata.

Run the gate from a resolved repository root and card folder:

```powershell
python "{ROOT}/skills/round_deliver.py" "<card_folder>" "{ROOT}"
```

## Result Handling

- `pass`: text was delivered. Continue waiting for player input.
- `retry`: repair the rejected story or agent artifacts using critic instructions, rebuild `story.output.json` and `critic.report.json`, then run the gate again.
- `blocked`: stop automatic repair and surface the terminal reason before any project-level prompt or code changes.

Do not run delivery from an unknown directory or rely on implicit repository resolution. Resolve `{ROOT}` and the card folder explicitly.

Delivery must never be blocked by optional image generation or per-card UI work.

Only the orchestrator or main agent performs delivery. Subagents never write `skills/styles/response.txt`.
