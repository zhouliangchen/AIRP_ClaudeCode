import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    skills_dir = str(ROOT / "skills")
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentPromptsTest(unittest.TestCase):
    def setUp(self):
        self.agent_prompts = load_module("agent_prompts")

    def _write_actor_files(
        self,
        card: Path,
        actor_name: str,
        *,
        profile: str,
        long_term: str = "",
        key_memories: list[dict] | None = None,
        short_term: str = "",
    ) -> None:
        actor_name = "_self" if actor_name == "player" else actor_name
        actor_dir = card / "characters" / actor_name
        actor_dir.mkdir(parents=True)
        (actor_dir / "profile.md").write_text(profile, encoding="utf-8")
        (actor_dir / "long_term_memories.md").write_text(long_term, encoding="utf-8")
        (actor_dir / "key_memories.json").write_text(
            json.dumps({"memories": key_memories or []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (actor_dir / "short_term_memories.md").write_text(short_term, encoding="utf-8")

    def test_subgm_prompt_references_skill_and_forbids_gm_authority(self):
        prompt = self.agent_prompts.subgm_prompt_text({"thread_id": "side_a"})

        self.assertIn(".claude/skills/rp-subgm-agent.md", prompt)
        self.assertIn('"thread_id": "side_a"', prompt)
        self.assertIn("no `character_promotions`", prompt)
        self.assertIn("no `subgm_commands`", prompt)
        self.assertIn("no player participation", prompt)

    def test_subgm_prompt_contract_requires_actor_call_visibility_basis(self):
        prompt = self.agent_prompts.subgm_prompt_text({"thread_id": "side_a"})

        self.assertIn('"visibility_basis"', prompt)
        self.assertIn('"mode": "direct"', prompt)
        self.assertIn('"summary": "why this actor can perceive or receive this side prompt"', prompt)
        self.assertIn("Every `actor_calls[]` item must include valid per-call `visibility_basis.mode` and `visibility_basis.summary`", prompt)

    def test_gm_prompt_contract_requires_actor_call_visibility_basis_mode(self):
        prompt = self.agent_prompts._gm_prompt({})

        self.assertIn('"visibility_basis"', prompt)
        self.assertIn('"mode": "direct"', prompt)
        self.assertIn('"summary": "why this actor can perceive or receive this prompt"', prompt)
        self.assertIn("Every `actor_calls[]` item must include valid per-call `visibility_basis.mode` and `visibility_basis.summary`", prompt)

    def test_gm_prompt_forbids_complete_with_unresolved_active_subgm_threads(self):
        prompt = self.agent_prompts._gm_prompt({})

        self.assertIn("must not set `stop_reason` to `complete` while active subGM side threads remain", prompt)
        self.assertIn("message, accelerate, pause, merge, or close", prompt)

    def test_postprocess_prompt_defines_frontend_data_contract(self):
        prompt = self.agent_prompts.build_postprocess_prompt({
            "postprocess_context": {
                "story.input.json": {"round_id": "round-000001"},
                "current_state": {"quest": "Old goal"},
            }
        })

        self.assertIn("postprocess.output.json", prompt)
        self.assertIn("core.summary", prompt)
        self.assertIn("core.options", prompt)
        self.assertIn("core.current_goal", prompt)
        self.assertIn("mvu.commands", prompt)
        self.assertIn("Do not rewrite story prose", prompt)
        self.assertIn("Do not write progress.json", prompt)
        self.assertIn("Runtime Input JSON", prompt)
        self.assertIn('"quest": "Old goal"', prompt)

    def test_story_prompt_assigns_frontend_data_to_postprocess(self):
        prompt = self.agent_prompts._story_prompt({})

        self.assertIn("Do not write `<summary>` or `<options>` in `story.output.json`", prompt)
        self.assertIn("postprocess owns summary, options, current goal, and frontend data", prompt)
        self.assertIn("Do not write `<UpdateVariable>`", prompt)
        self.assertIn("postprocess owns MVU variable update commands", prompt)
        self.assertIn("do not narrate that action as actually performed", prompt)

    def test_critic_prompt_does_not_require_story_summary_or_options_tags(self):
        prompt = self.agent_prompts._critic_prompt({})

        self.assertIn("does not require `<summary>` or `<options>`", prompt)
        self.assertIn("postprocess owns `core.summary` and `core.options`", prompt)
        self.assertIn(
            "Do not require `<summary>` or `<options>` in `story.output.json`; postprocess owns frontend summary/options",
            prompt,
        )
        self.assertIn("Explicit player-action polarity", prompt)
        self.assertIn("hard-fail if `story_output.content` narrates a player action as completed", prompt)

    def test_write_round_prompts_compacts_gm_prompt_raw_channel_duplicates(self):
        role = "我叫雨蒙，一名普通的高一男生。至少在今天早上之前"
        instruction = "作品基调：日式轻小说风格，青春活力，严肃和欢乐交织的校园非日常喜剧。"
        gm_packet = {
            "agent": "gm",
            "role_channel": role,
            "user_instruction_channel": instruction,
            "components": [
                {"channel": "role", "text": role},
                {"channel": "user_instruction", "text": instruction},
            ],
            "world_state": {
                "role_channel": role,
                "user_instruction_channel": instruction,
                "components": [
                    {"channel": "role", "text": role},
                    {"channel": "user_instruction", "text": instruction},
                ],
                "input_analysis": {
                    "schema_version": 1,
                    "round_id": "round-000001",
                    "analysis_mode": "ai",
                    "source_integrity": {"raw_text_sha256": "hash"},
                    "semantic_units": [
                        {
                            "id": "su-001",
                            "type": "style_guidance",
                            "text": instruction,
                            "raw_excerpt": instruction,
                            "derived_summary": "Use a lively school light-novel tone.",
                            "visibility": "gm_only",
                        }
                    ],
                    "routing": {
                        "role_channel": role,
                        "user_instruction_channel": instruction,
                    },
                    "world_updates": {},
                    "narrative_directives": {},
                },
                "runtime_settings": {"style": "轻松活泼", "wordCount": 4000},
                "style_profile": {"name": "轻松活泼", "content": "short"},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "round-000001"
            self.agent_prompts.write_round_prompts(
                run_dir,
                gm_packet,
                {"agent": "player"},
                {},
                runtime_settings_payload={"style": "轻松活泼", "wordCount": 4000},
            )
            prompt = (run_dir / "prompts" / "gm.prompt.md").read_text(encoding="utf-8")

        self.assertEqual(prompt.count(role), 1)
        self.assertEqual(prompt.count(instruction), 1)
        self.assertNotIn('"components"', prompt)
        self.assertNotIn('"raw_excerpt"', prompt)
        self.assertNotIn('"text": "' + instruction, prompt)
        self.assertIn("Use a lively school light-novel tone.", prompt)

    def test_projection_prompt_contains_contract_context_and_no_reveal_rule(self):
        prompt = self.agent_prompts.projection_prompt_text(
            {
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
                "requested_actor_message": "You learn Alice is secretly a vampire.",
                "actor_context": (
                    "You only saw Alice avoid the sun and heard townsfolk call her dangerous."
                ),
            }
        )

        self.assertIn("Projection Agent Prompt", prompt)
        self.assertIn('"decision"', prompt)
        self.assertIn('"final_actor_message"', prompt)
        self.assertIn("character:Bob", prompt)
        self.assertIn("call-bob-1", prompt)
        self.assertIn("avoid the sun", prompt)
        self.assertIn("Do not reveal objective truth to the target actor", prompt)
        self.assertIn("Do not tell the actor that a belief is false", prompt)
        self.assertIn("Only the natural-language `final_actor_message` can be delivered to the actor", prompt)
        self.assertIn("never deliver context packets", prompt)
        self.assertNotIn("false_belief", prompt)

    def test_projection_prompt_includes_existing_skill_body(self):
        prompt = self.agent_prompts.projection_prompt_text(
            {
                "target_actor_id": "character:Bob",
                "source_call_id": "call-bob-1",
            }
        )

        self.assertIn(".claude/skills/rp-projection-agent.md", prompt)
        self.assertNotIn("(missing skill file:", prompt)

    def test_input_analyst_prompt_prefers_capability_requests(self):
        prompt = self.agent_prompts._input_analyst_prompt({
            "round_id": "round-000001",
            "source_integrity": {
                "raw_text_sha256": "raw",
                "role_text_sha256": "role",
                "user_instruction_text_sha256": "instruction",
            },
        })

        self.assertIn('"capability_requests": []', prompt)
        self.assertIn("capability_requests[]", prompt)
        self.assertIn("assets.generate_image", prompt)
        self.assertIn("source.change_request -> main-agent", prompt)
        self.assertIn("assets.generate_image -> assets-ui", prompt)
        self.assertIn("card.patch_data -> card-data", prompt)
        self.assertNotIn("Allowed `routing_requests[].type` values", prompt)

    def test_actor_prompts_keep_role_guidance_immersive_and_first_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            self._write_actor_files(card, "player", profile="我是当前扮演的角色。\n")
            self._write_actor_files(card, "Ada", profile="我是 Ada。\n")
            player_prompt = self.agent_prompts._player_prompt(
                {"card_folder": str(card), "actor_id": "player"}
            )
            character_prompt = self.agent_prompts._character_prompt(
                {"card_folder": str(card), "actor_id": "character:Ada", "character_name": "Ada"}
            )

        for prompt in (player_prompt, character_prompt):
            self.assertIn("我是", prompt)
            self.assertIn("我直接用自然语言对刚刚与我说话的人回应", prompt)
            self.assertTrue(
                "我的行动方式" in prompt or "我的独立视角" in prompt,
                prompt,
            )
            for forbidden in (
                '"events"',
                "custom_action",
                "perceive_request",
                "visible_content",
                "stop_for_player_decision",
                "Required Output Contract",
                "JSON 输出契约",
            ):
                self.assertNotIn(forbidden, prompt)
            self.assertNotIn("Skill reference", prompt)
            self.assertNotIn(".claude/skills/", prompt)
            self.assertNotIn("actor.outputs.json", prompt)
            self.assertNotIn("Claude Code", prompt)
            self.assertNotIn("file mailbox", prompt)
            self.assertNotIn("runtime loop", prompt)
            self.assertNotIn("GM resolution", prompt)
            self.assertNotIn("prompts, files", prompt)

    def test_actor_prompts_are_generated_from_unified_template_not_skill_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            self._write_actor_files(card, "player", profile="我是当前扮演的角色。\n")
            self._write_actor_files(card, "Ada", profile="我是 Ada。\n")
            player_prompt = self.agent_prompts._player_prompt(
                {
                    "card_folder": str(card),
                    "actor_id": "player",
                    "gm_prompt": "你听见走廊外有人停下脚步。",
                }
            )
            character_prompt = self.agent_prompts._character_prompt(
                {
                    "card_folder": str(card),
                    "actor_id": "character:Ada",
                    "character_name": "Ada",
                    "gm_prompt": "你看见门缝里透出微光。",
                }
            )

        for prompt in (player_prompt, character_prompt):
            self.assertIn("我直接用自然语言对刚刚与我说话的人回应。", prompt)
            self.assertIn("我想回忆：xxx", prompt)
            self.assertIn("现在：", prompt)
            self.assertTrue(
                "你听见走廊外有人停下脚步。" in prompt
                or "你看见门缝里透出微光。" in prompt,
                prompt,
            )
            self.assertNotIn("```markdown", prompt)
            self.assertNotIn("刚刚对我说的话：", prompt)
            self.assertNotIn("我能感知到的内容：", prompt)
            self.assertNotIn("我此刻延续的第一人称意图：", prompt)
            self.assertNotIn("我延续角色通道输入中已经发生或正在发生的第一人称意图", prompt)
            self.assertNotIn("我是一个独立的重要角色，真正活在当前处境里", prompt)

    def test_actor_prompt_injects_actor_context_sections_without_placeholder_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            self._write_actor_files(
                card,
                "Ada",
                profile="我是Ada，是档案室的见证者。\n我说话谨慎，会先确认门外动静。\n",
                long_term="我长期记得雨夜档案室的钥匙声。\n",
                key_memories=[
                    {
                        "tag": "雨夜档案",
                        "summary": "我记得档案室门后有潮湿纸味。",
                        "detail": "完整细节只应在主动回忆后注入。",
                    }
                ],
                short_term="刚才我听见门后有两下敲门声。\n",
            )
            prompt = self.agent_prompts._character_prompt(
                {
                    "card_folder": str(card),
                    "actor_id": "character:Ada",
                    "character_name": "Ada",
                    "gm_prompt": "你听见门外有人压低声音说：Ada，开门。",
                }
            )

        for placeholder in (
            "# 基本设定注入点",
            "# 长期记忆注入点",
            "# 重点记忆注入点",
            "# 短期记忆注入点",
            "# GM当前消息注入点",
        ):
            self.assertNotIn(placeholder, prompt)
        self.assertIn("我是Ada，是档案室的见证者。", prompt)
        self.assertIn("我说话谨慎，会先确认门外动静。", prompt)
        self.assertIn("我长期记得雨夜档案室的钥匙声。", prompt)
        self.assertIn("我想回忆：雨夜档案；我记得档案室门后有潮湿纸味", prompt)
        self.assertNotIn("完整细节只应在主动回忆后注入", prompt)
        self.assertIn("刚才我听见门后有两下敲门声。", prompt)
        self.assertIn("你听见门外有人压低声音说：Ada，开门。", prompt)
        self.assertNotIn("刚刚对我说的话：", prompt)
        self.assertNotIn("我能感知到的内容：", prompt)
        self.assertNotIn("我此刻延续的第一人称意图：", prompt)

    def test_actor_prompt_prefers_persisted_character_files_over_packet_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            actor_dir = card / "characters" / "Ada_File"
            actor_dir.mkdir(parents=True)
            profile_text = (
                "我是Ada_File，我用第一人称记住自己的来历。\n\n"
                "我只相信档案原件，说话时会把门外的动静先听清楚。\n"
            )
            (actor_dir / "profile.md").write_text(profile_text, encoding="utf-8")
            (actor_dir / "long_term_memories.md").write_text("文件长期记忆：我守过旧档案室。\n", encoding="utf-8")
            (actor_dir / "key_memories.json").write_text(
                (
                    '{\n'
                    '  "memories": [\n'
                    '    {"tag": "文件重点", "summary": "我记得封条颜色。", '
                    '"detail": "文件重点详情不应常驻。"}\n'
                    '  ]\n'
                    '}\n'
                ),
                encoding="utf-8",
            )
            (actor_dir / "short_term_memories.md").write_text("文件短期记忆：刚才有人敲门。\n", encoding="utf-8")

            prompt = self.agent_prompts._character_prompt(
                {
                    "card_folder": str(card),
                    "actor_id": "character:Ada_File",
                    "character_name": "Wrong Name",
                    "self_knowledge": {"name": "Packet Name", "profile": "packet 基本设定不应注入"},
                    "memory": {
                        "long_term": ["packet 长期记忆不应注入"],
                        "key_memories": [
                            {
                                "tag": "packet重点",
                                "summary": "packet 重点摘要不应注入",
                                "detail": "packet 重点详情不应注入",
                            }
                        ],
                        "short_term": ["packet 短期记忆不应注入"],
                        "goals": ["packet 当前目标不应注入"],
                    },
                    "immersive_context": "packet 感知内容不应作为基本设定注入。",
                    "role_channel_anchor": "packet 行动锚点不应注入。",
                    "gm_prompt": "GM审核后的最近一次消息。",
                }
            )

        self.assertIn("# 我的行动提示：Ada_File", prompt)
        self.assertIn("我是 Ada_File。", prompt)
        self.assertIn(profile_text.strip(), prompt)
        self.assertIn("文件长期记忆：我守过旧档案室。", prompt)
        self.assertIn("我想回忆：文件重点；我记得封条颜色", prompt)
        self.assertIn("文件短期记忆：刚才有人敲门。", prompt)
        self.assertIn("GM审核后的最近一次消息。", prompt)
        for forbidden in (
            "Wrong Name",
            "Packet Name",
            "packet 基本设定不应注入",
            "packet 长期记忆不应注入",
            "packet 重点摘要不应注入",
            "packet 重点详情不应注入",
            "packet 短期记忆不应注入",
            "packet 当前目标不应注入",
            "packet 感知内容不应作为基本设定注入",
            "packet 行动锚点不应注入",
            "文件重点详情不应常驻",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_write_round_prompts_uses_card_folder_argument_for_actor_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            run_dir = card / ".agent_runs" / "round-000001"
            card.mkdir(parents=True)
            self._write_actor_files(card, "player", profile="我是存档里的玩家自述。\n")
            self._write_actor_files(card, "Ada", profile="我是存档里的Ada。\n")

            self.agent_prompts.write_round_prompts(
                run_dir,
                {"agent": "gm"},
                {"agent": "player", "actor_id": "player"},
                {"Ada": {"agent": "character", "actor_id": "character:Ada", "character_name": "Ada"}},
                card_folder=card,
            )

            player_prompt = (run_dir / "prompts" / "player.prompt.md").read_text(encoding="utf-8")
            character_prompt = (
                run_dir / "prompts" / "characters" / "Ada.prompt.md"
            ).read_text(encoding="utf-8")

        self.assertIn("我是存档里的玩家自述。", player_prompt)
        self.assertIn("我是存档里的Ada。", character_prompt)

    def test_actor_prompt_has_no_legacy_packet_context_fallback_builder(self):
        source = (ROOT / "skills" / "agent_prompts.py").read_text(encoding="utf-8")

        self.assertNotIn("def _actor_basic_lines", source)
        self.assertNotIn("def _immersive_context_lines", source)
        self.assertNotIn("self_knowledge", source)
        self.assertNotIn("role_channel_anchor", source)

    def test_character_prompt_uses_character_folder_display_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            self._write_actor_files(card, "Ada_Zero_", profile="我是 Ada Zero。\n")
            prompt = self.agent_prompts._character_prompt(
                {
                    "card_folder": str(card),
                    "actor_id": "character:Ada_Zero_",
                    "character_name": "Wrong Name",
                    "self_knowledge": {"name": "Ada/Zero?"},
                    "immersive_context": "packet 内容不应注入。",
                }
            )

        self.assertIn("# 我的行动提示：Ada_Zero_", prompt)
        self.assertIn("我是 Ada_Zero_。", prompt)
        self.assertIn("我是 Ada Zero。", prompt)
        self.assertNotIn("Wrong Name", prompt)
        self.assertNotIn("Ada/Zero?", prompt)
        self.assertNotIn("packet 内容不应注入。", prompt)
        self.assertNotIn("character:Ada_Zero_", prompt)


if __name__ == "__main__":
    unittest.main()
