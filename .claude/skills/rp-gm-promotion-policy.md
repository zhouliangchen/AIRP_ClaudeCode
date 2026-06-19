---
name: rp-gm-promotion-policy
description: Use when deciding whether an entity becomes an important character with independent actor routing.
---

## RP GM Promotion Policy

Use this policy when converting a person, creature, organization representative, or recurring NPC into an important character with independent actor handling.

## Authority

Allowed promotion sources: preprocess, gm

subGM agents must not create or promote important characters. Actor, story, critic, subGM, and other side-thread discoveries are only requests to the main GM. The main GM must decide whether the entity is important enough, currently scene-relevant, and safe to route as an independent actor.

Future subGM side threads may emit only request records for main GM review:

```json
{
  "type": "promotion_request",
  "candidate_name": "Side NPC",
  "reason": "why main GM should consider promotion",
  "source_agent": "subGM:thread-1"
}
```

This record must not be applied directly. Only the main GM may turn it into a promotion record with `source_agent: "gm"`.

Legacy `gm_assistant:*` sources are an old alias/backdoor and must be rejected with the current subGM promotion boundary.

## Criteria

Promote only when independent agency matters to the next scene: the entity has a distinct voice, durable goal, memory, relationship, or decision-making role that cannot be represented as background NPC narration.

Do not promote temporary set dressing, crowds, one-line vendors, hidden observers, or entities whose main purpose is to reveal GM-only facts. Promotion must not be used to bypass visibility policy.
