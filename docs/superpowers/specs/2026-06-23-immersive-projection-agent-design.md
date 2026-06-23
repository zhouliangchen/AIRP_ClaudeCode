# Immersive Projection Agent Design

## Status

Drafted on 2026-06-23 after reviewing the current projection runtime, actor context shape, and two failure scenarios:

- a hidden vampire truth where different actors should receive different in-world labels,
- a former-hero scenario where a paladin's false belief must remain subjectively true to that paladin.

This spec assumes the dispatcher-native message and intent runtime already exists. It does not replace the whole control plane. It narrows the deterministic projection layer to non-semantic protocol guarantees and adds an LLM-driven projection agent for semantic actor-facing text review and editing.

## Purpose

The runtime should support objective world truth and actor-subjective experience at the same time.

GM and subGM agents may know objective archive-level facts, worldbook data, imported card data, and branch history. Player and character actors must receive only immersive first-person or second-person context that matches what they remember, believe, perceive, or are told inside the world.

The current implementation is too coarse for this. It can redact hidden phrases and filter events, but it cannot safely transform "vampire" into "black-robed figure" for one actor while preserving "vampire" for another. It also exposes `misconceptions` as an actor-facing field, which tells the actor agent that a belief is false from an omniscient perspective.

The target design makes projection an agentic semantic review step while keeping a minimal deterministic protocol gate.

## Requirements

### Objective World Authority

GM owns archive-level objective world knowledge for the save:

- imported character-card and worldbook facts,
- user-authored objective setup after input analysis,
- GM-maintained world state and branch history,
- subGM side-thread results accepted by GM,
- prior delivered story and actor-visible interaction traces.

These objective files and generated state must participate in per-round snapshots and rollback with the same authority as current runtime artifacts, player inputs, character files, and history files.

subGM may read objective data needed for its assigned side-thread boundary, but subGM world-state changes should be represented as side-thread deltas or requests. GM or the control plane merges accepted deltas into the save-level objective world state.

### Subjective Actor Knowledge

Actor memory and belief are subjective knowledge. They are authoritative only for that actor's point of view, not for objective world truth.

The runtime must allow subjective knowledge to be incomplete or wrong:

- Bob may only know that a black-robed figure stood near Alice's door.
- Alice may personally remember that the figure is a vampire.
- A paladin may sincerely believe that an old hero is cursed.
- The former hero may know the curse accusation was royal slander.

The actor must not be told which of its beliefs are misconceptions. Actor-facing context must not include omniscient labels such as `misconceptions`, `objective_truth`, `gm_only`, `projection_review`, or `belief_is_false`.

### Immersive Actor Context

Player and character context must be rendered as in-world first-person or second-person natural language.

Allowed actor-facing content:

- "You remember..."
- "You believe..."
- "You were taught..."
- "You can see..."
- "The person before you appears..."
- "Your body feels..."
- "Your current goal is..."

Forbidden actor-facing content:

- schema labels that reveal the runtime,
- omniscient truth corrections,
- `misconceptions`,
- GM notes,
- projection audit comments,
- user-instruction summaries,
- hidden fact names that the actor cannot know,
- another actor's private thoughts or memory.

Structured files may still exist internally. The actor-facing packet must render them into immersive text before dispatch.

### Projection Agent

Add an LLM-driven projection agent between GM/subGM actor requests and player/character actor dispatch.

The projection agent receives:

- the GM/subGM natural-language actor message,
- the target actor identity,
- the actor's rendered subjective context and raw internal actor memory when needed,
- relevant objective world context,
- the source call envelope,
- prior visible events needed for continuity.

It returns one of four decisions:

- `pass`: the original actor-facing message is already safe and immersive.
- `edited`: a small local edit preserves GM/subGM intent while matching actor knowledge.
- `needs_rewrite`: the message cannot be safely repaired locally; GM/subGM must rewrite it.
- `blocked`: the request is invalid, unsafe, targets the wrong actor, or cannot be reconciled.

Projection edits should be local and semantic-preserving. Examples:

- Bob receives "a black-robed figure" instead of "a vampire" when Bob has no in-world basis for the vampire label.
- Alice receives "the vampire from your childhood" when her private memory supports that recognition.
- The paladin receives "the cursed hero" if that is the paladin's sincere training or belief, even if objective world truth says the hero is not cursed.

The projection agent may negotiate with GM/subGM through a rewrite request when local editing would change the narrative intent too much.

### Minimal Deterministic Gate

The deterministic gate must remain, but it should no longer perform semantic projection by deleting, broadly redacting, or guessing actor-visible labels.

Its responsibility is limited to protocol and safety invariants:

- target actor id matches the request,
- source call id is preserved,
- sender is allowed to request projection,
- only `projection` can deliver actor-facing messages to player/character inboxes,
- projected output is attached to the correct source message,
- forbidden control fields are not exposed to actor prompts,
- generated artifacts remain JSON-valid and traceable,
- projection decisions are recorded in artifacts and message metadata,
- retry, rollback, and fanout behavior remain deterministic,
- delivery and memory boundaries still have auditable provenance.

If the deterministic gate detects a protocol violation, it blocks. If it detects a semantic concern that requires judgement, it should route to projection agent or GM rewrite rather than inventing its own semantic edit.

## Design Options Considered

### Option A: Pure Deterministic Projection With Better Rules

Keep projection entirely in Python and add more structured visibility and alias rules.

This is testable but brittle. It would require the code to understand story semantics such as "vampire", "black-robed figure", "cursed hero", and "royal slander". That conflicts with the project's semantic input policy and would not scale to arbitrary RP settings.

### Option B: Replace All Projection Gates With LLM Projection

Delete deterministic projection and let an LLM projection agent decide all routing and safety.

This improves semantic flexibility but loses the current runtime's strongest guarantees. Actor ids, source call ids, message ACLs, retry behavior, trace provenance, and rollback boundaries become harder to verify. A projection model error could route information to the wrong actor or corrupt control-plane artifacts.

### Option C: LLM Projection Agent With Minimal Deterministic Protocol Gate

Use LLM projection for semantic review and local editing, while Python keeps only non-semantic protocol guarantees.

This is the recommended approach. It solves subjective-label and false-belief scenarios without hard-coding story concepts, and it preserves the control-plane properties needed for testing, rollback, and dispatcher-native execution.

## Recommended Architecture

### New Projection Flow

1. GM or subGM creates an actor request with:
   - target actor id,
   - source call id,
   - natural-language message,
   - optional short intent note for projection only,
   - relevant location/time/sensory hints where available.

2. Dispatcher creates `request_projection`.

3. Projection runtime builds a projection review packet:
   - objective world context for the scene,
   - target actor subjective memory and rendered actor context,
   - visible recent events for the actor,
   - GM/subGM requested message,
   - source envelope.

4. Projection agent reviews the request and returns `pass`, `edited`, `needs_rewrite`, or `blocked`.

5. Minimal deterministic gate validates the projection result envelope.

6. If `pass` or `edited`, dispatcher creates `run_actor` from the final actor-facing natural-language message.

7. If `needs_rewrite`, dispatcher creates a GM/subGM rewrite intent with the projection feedback.

8. If `blocked`, the request is blocked with a structured reason.

### Actor Context Rendering

Introduce an actor context renderer that produces immersive text from internal files.

Internal data may remain structured:

- memory files,
- goals,
- body state,
- relationships,
- recent visible events,
- sensory context,
- subjective beliefs,
- current scene affordances.

The actor-facing output should be natural language:

```text
You remember the royal academy teaching that cursed heroes endanger civilians.
You can see the old wanted sigil on the traveler's cloak.
You believe this person may be one of the cursed heroes from the old war.
The market is crowded behind you.
```

The renderer must not expose whether a belief is objectively true or false.

### Subjective Belief Storage

Remove actor-facing `misconceptions`.

Represent actor beliefs in memory using ordinary first-person language. Existing memory sections can hold these beliefs, or a new internal field may be introduced if it is rendered before actor dispatch. The actor-facing prompt must not call the field `misconceptions`.

Examples:

- `I was taught that cursed heroes spread the Demon King's corruption.`
- `I believe the black-robed traveler is dangerous.`
- `I remember seeing a vampire when I was a child.`

GM reads objective world knowledge and actor subjective memory together. GM uses objective truth for world simulation, and subjective memory for actor-facing wording.

### Projection Agent Prompt Contract

The projection agent is not a final narrator and does not run the actor.

It must:

- preserve GM/subGM narrative intent when safe,
- edit only the actor-facing message,
- keep the target actor immersed,
- choose labels supported by actor memory, perception, or in-world reports,
- avoid revealing objective truth when the actor lacks access,
- avoid revealing that a belief is a misconception,
- return concise feedback when rewrite is needed,
- never modify objective world files directly,
- never modify actor memory directly.

### Minimal Structured Envelope

Natural language is used for GM/subGM/projection/actor content, but control-plane envelopes remain structured.

The envelope should include:

- `source_agent`,
- `target_actor_id`,
- `source_call_id`,
- `projection_decision`,
- `final_actor_message`,
- `projection_feedback` when needed,
- `source_message_id`,
- `created_message_id`,
- retry/fanout metadata where applicable.

Actors see only `final_actor_message` plus their immersive context. They do not see projection feedback or source metadata.

## Error Handling

Projection result handling:

- `pass`: continue to actor dispatch.
- `edited`: write projection artifact, continue to actor dispatch.
- `needs_rewrite`: create a GM/subGM rewrite intent and do not dispatch actor.
- `blocked`: mark projection intent blocked and record reason.

Projection runtime failures should be fail-closed:

- model call failure blocks or retries according to existing dispatcher retry policy,
- invalid projection JSON blocks with `projection_output_invalid`,
- missing source call id blocks with `projection_source_invalid`,
- target actor mismatch blocks with `projection_actor_mismatch`,
- unsafe control-field exposure blocks with `projection_protocol_violation`.

## Testing Strategy

Add focused unit and smoke coverage:

- actor context rendering does not expose `misconceptions` or GM-only labels,
- actor context rendering is first-person or second-person natural language,
- projection can change "vampire" to "black-robed figure" for an actor without vampire knowledge,
- projection preserves "vampire" for an actor whose private memory supports it,
- projection preserves a paladin's cursed-hero belief without revealing it is false,
- projection asks GM/subGM for rewrite when local edit would change intent,
- deterministic gate rejects actor id and source call id mismatches,
- deterministic gate permits semantic edits that preserve protocol envelope,
- dispatcher creates `run_actor` only from projected final actor messages,
- control-plane smoke covers `run_gm_turn -> request_projection -> run_actor`.

No live model call should be required for unit tests. Use deterministic projection fixtures or a fake projection dispatcher.

## Documentation Updates

Update:

- `README.md`: describe projection agent and immersive actor contexts.
- `docs/重构建议.md`: replace the old statement that projection is only deterministic packet shaping.
- `.claude/skills/rp-context-projector.md`: describe projection agent duties and remove actor-facing `misconceptions`.
- `.claude/skills/rp-gm-agent.md`: require actor requests to use actor-subjective labels when available.
- `.claude/skills/rp-subgm-agent.md`: same rule within side-thread boundaries.
- `.claude/skills/rp-character-agent.md` and `.claude/skills/rp-player-agent.md`: actor agents receive only immersive context, not runtime labels.

## Non-Goals

This spec does not:

- change frontend UI behavior,
- change image generation,
- change provider/model configuration,
- let projection modify objective world truth,
- let projection modify actor memory,
- remove dispatcher, messages, intents, snapshots, rollback, or artifact provenance,
- make free-form agent chat the control-plane source of truth.

## Acceptance Criteria

The implementation is complete when:

- actor-facing packets no longer expose a `misconceptions` field,
- actor prompts receive immersive natural-language context,
- GM/subGM actor requests pass through projection agent before actor dispatch,
- projection artifacts record `pass`, `edited`, `needs_rewrite`, or `blocked`,
- deterministic gate is reduced to protocol validation and no longer performs broad semantic deletion as the projection strategy,
- vampire/black-robed-figure and cursed-hero false-belief fixtures pass,
- full unit tests pass,
- deterministic control-plane smoke passes,
- relevant docs are updated to match the new boundaries.
