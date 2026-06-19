# Important Character Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple important-character context registration from the actor parallelism limit so every registered important character can receive an isolated context and be called by the GM.

**Architecture:** Keep the existing `round_prepare -> agent_packets -> agent_turn_loop` file-mailbox path. This plan changes only the registration-side behavior: `round_prepare.build_character_contexts()` must generate packets for all explicitly registered important characters, while `max_parallel_subagents` remains a runtime scheduling limit for later actor-batch work.

**Tech Stack:** Python standard library, `unittest`, JSON artifacts under `.agent_runs/<round>/`, Markdown documentation.

---

## Scope

This plan implements P0 from `docs/superpowers/specs/2026-06-19-rp-control-plane-hardening-design.md`.

Included:

- Generate context packets for every explicitly registered `character_orchestration.major` character.
- Preserve blank `_self` bootstrap behavior.
- Preserve passive card-structure fallback behavior when no explicit important-character registry exists.
- Add regression coverage that a third-or-later registered character receives context files and can be called by the GM.
- Update README wording so the documented architecture distinguishes registration from parallel dispatch.

Deferred to later plans:

- P1 mainline actor batch scheduling and actual parallel dispatch.
- P2 visibility proof and stricter actor projection.
- P3 perception/dialogue closure and post-round memory jobs.
- subGM boundary proof hardening beyond existing reservation behavior.

## File Structure

- Modify `skills/round_prepare.py`: remove the context-generation slice that currently uses `max_parallel_subagents` as a character-count cap.
- Modify `tests/test_round_prepare_helpers.py`: add unit tests for explicit major-character context generation, blank `_self`, and fallback behavior.
- Modify `tests/test_agent_packets.py`: add integration coverage that prepared run directories materialize all registered character context and prompt files.
- Modify `tests/test_agent_turn_loop.py`: add integration coverage that a GM call to a third registered character is accepted when contexts came from `round_prepare`.
- Modify `README.md`: clarify that all registered important characters get isolated contexts and only runtime dispatch is capped.

## Task 1: Round Prepare Unit Tests

**Files:**
- Modify: `tests/test_round_prepare_helpers.py`
- Read: `skills/round_prepare.py:352-401`

- [ ] **Step 1: Add failing tests for explicit major-character generation**

Append these tests to `RoundPrepareHelperTest` in `tests/test_round_prepare_helpers.py`:

```python
    def test_build_character_contexts_keeps_all_explicit_major_characters_when_parallel_cap_is_two(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            for name in ("Ada", "Bert", "Cora"):
                char_dir = card / "memory" / "characters" / name
                char_dir.mkdir(parents=True)
                (char_dir / "profile.md").write_text(f"{name} profile.", encoding="utf-8")

            result = round_prepare.build_character_contexts(
                card,
                {
                    "character_orchestration": {
                        "major": ["Ada", "Bert", "Cora"],
                        "max_parallel_subagents": 2,
                    }
                },
                {},
                [],
                "Ada looks toward the door.",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["Ada", "Bert", "Cora"],
        )
        self.assertEqual(result["characters"][0]["scene_relevance"], "high")
        self.assertEqual(result["characters"][1]["scene_relevance"], "normal")
        self.assertEqual(result["characters"][2]["profile_summary"], "Cora profile.")

    def test_build_character_contexts_keeps_blank_self_and_all_explicit_major_characters(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            result = round_prepare.build_character_contexts(
                card,
                {
                    "mode": "blank_bootstrap",
                    "character_orchestration": {
                        "major": ["Ada", "Bert", "Cora"],
                        "max_parallel_subagents": 2,
                    },
                },
                {},
                [],
                "Bert listens.",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["_self", "Ada", "Bert", "Cora"],
        )
        self.assertEqual(result["characters"][0]["scene_relevance"], "high")
        self.assertEqual(result["characters"][2]["scene_relevance"], "high")

    def test_build_character_contexts_keeps_passive_card_structure_fallback_small(self):
        round_prepare = _load_round_prepare()
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            result = round_prepare.build_character_contexts(
                card,
                {"character_orchestration": {"max_parallel_subagents": 2}},
                {"characters": {"Ada": {}, "Bert": {}, "Cora": {}}},
                [],
                "",
            )

        self.assertEqual(
            [item["name"] for item in result["characters"]],
            ["Ada", "Bert"],
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m unittest tests.test_round_prepare_helpers.RoundPrepareHelperTest -v
```

Expected before implementation:

- `test_build_character_contexts_keeps_all_explicit_major_characters_when_parallel_cap_is_two` fails because only `Ada` and `Bert` are present.
- `test_build_character_contexts_keeps_blank_self_and_all_explicit_major_characters` fails because the sliced list omits later major characters.
- Existing `test_grep_reference_section_returns_indexed_section_content` still passes.

## Task 2: Remove the Registration Cap

**Files:**
- Modify: `skills/round_prepare.py:379-401`
- Test: `tests/test_round_prepare_helpers.py`

- [ ] **Step 1: Replace the capped loop**

In `skills/round_prepare.py`, replace this line:

```python
    for name in major[: max(1, int(orchestration.get("max_parallel_subagents", 2) or 2))]:
```

with:

```python
    # `max_parallel_subagents` is a runtime dispatch limit, not a registration cap.
    # Every explicitly registered important character needs an isolated context so
    # the GM can call that character when the scene creates a participation point.
    for name in major:
```

- [ ] **Step 2: Run the focused tests and verify pass**

Run:

```powershell
python -m unittest tests.test_round_prepare_helpers.RoundPrepareHelperTest -v
```

Expected after implementation:

```text
OK
```

- [ ] **Step 3: Commit the unit-level change**

Run:

```powershell
git add skills/round_prepare.py tests/test_round_prepare_helpers.py
git commit -m "fix: 解除重要角色上下文数量上限"
```

## Task 3: Agent Packet Integration Coverage

**Files:**
- Modify: `tests/test_agent_packets.py`
- Read: `skills/agent_packets.py:567-624`

- [ ] **Step 1: Add a failing integration test for generated character files**

Add this test method to `AgentPacketTest` in `tests/test_agent_packets.py`:

```python
    def test_prepare_agent_run_materializes_all_registered_major_character_contexts(self):
        round_prepare = _load_round_prepare()
        card_data = {
            "title": "All Registered Characters",
            "character_orchestration": {
                "major": ["Ada", "Bert", "Cora"],
                "max_parallel_subagents": 2,
            },
        }
        contexts = round_prepare.build_character_contexts(
            self.card,
            card_data,
            {},
            [],
            "I enter the archive.",
        )

        result = self.agent_packets.prepare_agent_run(
            self.card,
            user_text="I enter the archive.",
            chat_log=[],
            card_data=card_data,
            character_contexts=contexts,
            turn_index=0,
        )

        run_dir = Path(result["run_dir"])
        self.assertEqual(
            [item["name"] for item in contexts["characters"]],
            ["Ada", "Bert", "Cora"],
        )
        for name in ("Ada", "Bert", "Cora"):
            safe = self.agent_run.safe_name(name)
            self.assertTrue((run_dir / "characters" / f"{safe}.context.json").exists())
            self.assertTrue((run_dir / "prompts" / "characters" / f"{safe}.prompt.md").exists())

        input_json = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [item["name"] for item in input_json["character_contexts"]["characters"]],
            ["Ada", "Bert", "Cora"],
        )
```

- [ ] **Step 2: Run the integration test and verify failure before Task 2 or pass after Task 2**

If Task 2 has not been implemented, run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest.test_prepare_agent_run_materializes_all_registered_major_character_contexts -v
```

Expected before Task 2 implementation:

- The assertion for `["Ada", "Bert", "Cora"]` fails because `Cora` is missing.

If Task 2 has already been implemented, run the same command and expect:

```text
OK
```

- [ ] **Step 3: Run existing nearby agent-packet tests**

Run:

```powershell
python -m unittest tests.test_agent_packets.AgentPacketTest -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Commit the integration test**

Run:

```powershell
git add tests/test_agent_packets.py
git commit -m "test: 覆盖全部重要角色上下文产物"
```

## Task 4: GM Loop Integration Coverage

**Files:**
- Modify: `tests/test_agent_turn_loop.py`
- Read: `skills/agent_turn_loop.py:40-67`
- Read: `skills/agent_turn_loop.py:570-613`

- [ ] **Step 1: Add a regression test for calling the third registered actor**

Add this test method to `AgentTurnLoopTest` in `tests/test_agent_turn_loop.py`:

```python
    def test_gm_can_call_third_actor_from_round_prepare_registered_contexts(self):
        round_prepare = load_module("round_prepare")
        card_data = {
            "character_orchestration": {
                "major": ["Ada", "Bert", "Cora"],
                "max_parallel_subagents": 2,
            }
        }
        contexts = round_prepare.build_character_contexts(
            self.run_dir.parent,
            card_data,
            {},
            [],
            "Cora hears the archive bell.",
        )
        payload = self.agent_run.read_json(self.run_dir / "input.json")
        payload["character_contexts"] = contexts
        self.agent_run.write_json(self.run_dir / "input.json", payload)

        actor_packets = []

        def dispatch(agent_key, packet):
            if agent_key == "gm":
                return {
                    "agent": "gm",
                    "scene_beats": [{"content": "The archive bell rings once."}],
                    "events": [],
                    "actor_calls": [{
                        "call_id": "call-character-Cora-1",
                        "actor_id": "character:Cora",
                        "prompt": "You hear the archive bell and notice the player at the door.",
                        "reason": "Cora is the important character who can perceive this moment.",
                    }],
                    "parallel_groups": [],
                    "world_state_delta": [],
                    "decision_point": None,
                    "stop_reason": "complete",
                }
            self.assertEqual(agent_key, "character:Cora")
            actor_packets.append(json_copy(packet))
            return {
                "agent": "character",
                "agent_id": "character:Cora",
                "character_name": "Cora",
                "events": [{"type": "action", "target": "", "content": "I step closer to the archive door."}],
                "stop_reason": "continue",
            }

        result = self.agent_turn_loop.run_interactive_loop(self.run_dir, dispatch, max_steps=1)

        self.assertEqual(result["called_actors"], ["character:Cora"])
        self.assertEqual(len(actor_packets), 1)
        self.assertEqual(actor_packets[0]["actor_id"], "character:Cora")
```

- [ ] **Step 2: Run the regression test**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest.test_gm_can_call_third_actor_from_round_prepare_registered_contexts -v
```

Expected before Task 2 implementation:

- `result["called_actors"]` is `[]` because `Cora` was not registered in `character_contexts`.

Expected after Task 2 implementation:

```text
OK
```

- [ ] **Step 3: Run the existing turn-loop suite**

Run:

```powershell
python -m unittest tests.test_agent_turn_loop.AgentTurnLoopTest -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Commit the loop regression test**

Run:

```powershell
git add tests/test_agent_turn_loop.py
git commit -m "test: 验证GM可调用全部已注册重要角色"
```

## Task 5: README Architecture Wording

**Files:**
- Modify: `README.md`
- Read: `README.md`, section `## 核心角色 subagent`

- [ ] **Step 1: Update the important-character paragraph**

In `README.md`, find the paragraph that currently starts with:

```markdown
Claude Code 工作流会在场景强相关时最多并行调用 2 个核心角色 subagent
```

Replace that paragraph with:

```markdown
每轮会为已注册的重要角色生成隔离上下文；`max_parallel_subagents` 只限制运行时同一批次最多并行调度多少角色，不限制已注册重要角色的上下文数量。Claude Code 工作流会在场景强相关时最多并行调用配置允许数量的核心角色 subagent，让它们只从角色自身立场返回反应、隐藏意图、行动/台词候选、变量建议和记忆 delta。GM 可读取完整剧情与用户指令；player/character 只读取第一人称投影上下文，不接触 GM 隐藏事实。
```

- [ ] **Step 2: Verify README contains the new distinction**

Run:

```powershell
python -c "from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert '只限制运行时同一批次最多并行调度多少角色' in text; assert '不限制已注册重要角色的上下文数量' in text"
```

Expected:

- Command exits with code `0`.

- [ ] **Step 3: Commit the documentation update**

Run:

```powershell
git add README.md
git commit -m "docs: 明确重要角色注册不受并行上限限制"
```

## Task 6: Final Verification

**Files:**
- Verify: `skills/round_prepare.py`
- Verify: `tests/test_round_prepare_helpers.py`
- Verify: `tests/test_agent_packets.py`
- Verify: `tests/test_agent_turn_loop.py`
- Verify: `README.md`

- [ ] **Step 1: Run py_compile for touched runtime files**

Run:

```powershell
python -m py_compile skills/round_prepare.py
```

Expected:

```text
```

The command should exit with code `0` and no output.

- [ ] **Step 2: Run focused test suites**

Run:

```powershell
python -m unittest tests.test_round_prepare_helpers.RoundPrepareHelperTest tests.test_agent_packets.AgentPacketTest tests.test_agent_turn_loop.AgentTurnLoopTest -v
```

Expected:

```text
OK
```

- [ ] **Step 3: Run full unit suite**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Run deterministic control-plane smoke**

Run:

```powershell
python skills/control_plane_smoke.py --repo .
```

Expected:

- JSON output includes `"ok": true`.

- [ ] **Step 5: Run acceptance py_compile set**

Run:

```powershell
python -m py_compile skills/agent_workflow.py skills/control_plane_smoke.py skills/agent_outputs.py skills/agent_prompts.py skills/round_prepare.py skills/input_analysis.py skills/input_analysis_apply.py skills/character_registry.py skills/rp_generate_cli.py skills/self_repair.py
```

Expected:

```text
```

The command should exit with code `0` and no output.

- [ ] **Step 6: Check git status**

Run:

```powershell
git status --short
```

Expected:

```text
```

The command should print nothing after all planned commits are made.

## Plan Self-Review

Spec coverage:

- P0 explicit major-character registration is covered by Tasks 1 and 2.
- Prepared run character files are covered by Task 3.
- GM call acceptance for a third registered character is covered by Task 4.
- README synchronization is covered by Task 5.
- Full verification is covered by Task 6.

Intentional gaps:

- P1 actor batch scheduling, P2 visibility proof, and P3 perception/dialogue/memory jobs are separate implementation plans because they touch independent control-plane subsystems and require their own test design.

Completion scan:

- This plan contains only concrete steps.
