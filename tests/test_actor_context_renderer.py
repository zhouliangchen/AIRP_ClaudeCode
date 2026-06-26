import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = str(ROOT / "skills")


def load_module(name):
    if SKILLS not in sys.path:
        sys.path.insert(0, SKILLS)
    spec = importlib.util.spec_from_file_location(name, ROOT / "skills" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ActorContextRendererTest(unittest.TestCase):
    def setUp(self):
        self.renderer = load_module("actor_context_renderer")

    def test_render_context_reads_root_actor_memory_when_card_folder_is_present(self):
        with tempfile.TemporaryDirectory() as temp:
            card = Path(temp) / "card"
            actor_dir = card / "characters" / "Ada"
            objective_dir = card / "memory" / "characters" / "Ada"
            actor_dir.mkdir(parents=True)
            objective_dir.mkdir(parents=True)
            (actor_dir / "profile.md").write_text("我是 Ada，我习惯先观察再行动。", encoding="utf-8")
            (actor_dir / "long_term_memories.md").write_text("我记得玩家曾在雨夜保护我。", encoding="utf-8")
            (actor_dir / "short_term_memories.md").write_text("刚才我听见门后有脚步声。", encoding="utf-8")
            (actor_dir / "key_memories.json").write_text(
                json.dumps(
                    {
                        "memories": [
                            {
                                "tag": "雨夜",
                                "summary": "玩家把披风借给我",
                                "detail": "detail-secret: 他在钟楼背面说出了暗号。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (objective_dir / "profile.md").write_text("客观设定：Ada 是档案管理员。", encoding="utf-8")
            (objective_dir / "background.md").write_text("客观背景：档案馆位于旧城区。", encoding="utf-8")

            rendered = self.renderer.render_actor_context(
                "character:Ada",
                {"name": "Ada", "card_folder": str(card)},
                {},
            )
            text = rendered["immersive_context"]

            self.assertIn("我是 Ada，我习惯先观察再行动。", text)
            self.assertIn("我记得：我记得玩家曾在雨夜保护我。", text)
            self.assertIn("刚才我记得：刚才我听见门后有脚步声。", text)
            self.assertIn("我想回忆：雨夜", text)
            self.assertIn("玩家把披风借给我", text)
            self.assertNotIn("detail-secret", text)
            self.assertNotIn("钟楼背面", text)
            self.assertNotIn("客观设定", text)
            self.assertNotIn("客观背景", text)

    def test_render_context_filters_profile_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            card = Path(temp) / "card"
            actor_dir = card / "characters" / "Ada"
            actor_dir.mkdir(parents=True)
            (actor_dir / "profile.md").write_text(
                "\n".join([
                    "# Ada",
                    "",
                    "- source_agent: gm",
                    "- player_authoritative: false",
                    "- round_id: round-000001",
                    "",
                    "我是 Ada。",
                    "我的情况：我习惯先观察再行动。",
                ]),
                encoding="utf-8",
            )

            rendered = self.renderer.render_actor_context(
                "character:Ada",
                {"name": "Ada", "card_folder": str(card)},
                {},
            )
            text = rendered["immersive_context"]

            self.assertIn("我是 Ada。", text)
            self.assertIn("我的情况：我习惯先观察再行动。", text)
            self.assertNotIn("source_agent", text)
            self.assertNotIn("player_authoritative", text)
            self.assertNotIn("round-000001", text)

    def test_render_character_context_is_immersive_and_subjective(self):
        actor = {
            "name": "Current Paladin",
            "role": "royal paladin",
            "memory": {
                "long_term": ["I was taught that cursed heroes endanger civilians."],
                "key_memories": [
                    "I hid the chapel key beneath the broken altar after Mira betrayed me."
                ],
                "short_term": ["I saw an old wanted sigil on the traveler's cloak."],
                "goals": ["Keep civilians safe."],
            },
            "misconceptions": ["The old hero is cursed."],
            "sensory_context": {"sight": "Crowded market stalls block the north road."},
        }

        rendered = self.renderer.render_actor_context("character:CurrentPaladin", actor, {})
        serialized = json.dumps(rendered, ensure_ascii=False)

        self.assertEqual(rendered["actor_id"], "character:CurrentPaladin")
        self.assertIn("我是 Current Paladin。", rendered["immersive_context"])
        self.assertIn("我的身份是：royal paladin。", rendered["immersive_context"])
        self.assertIn("我记得：I was taught that cursed heroes endanger civilians.", rendered["immersive_context"])
        self.assertIn("我现在想要：Keep civilians safe.", rendered["immersive_context"])
        self.assertIn("我想回忆：", rendered["immersive_context"])
        self.assertNotIn("chapel key beneath the broken altar", rendered["immersive_context"])
        self.assertNotIn("You are", rendered["immersive_context"])
        self.assertNotIn("Your current goal", rendered["immersive_context"])
        self.assertNotIn("misconceptions", serialized)
        self.assertNotIn("objective_truth", serialized)
        self.assertNotIn("gm_only", serialized)
        self.assertNotIn("belief_is_false", serialized)

    def test_render_player_context_uses_first_person_anchor(self):
        actor = {
            "name": "player",
            "memory": {"short_term": ["I stepped into the rain."]},
        }
        world = {"role_channel": "I keep my hand on the doorframe."}

        rendered = self.renderer.render_actor_context("player", actor, world)

        self.assertIn("我是当前扮演的角色。", rendered["immersive_context"])
        self.assertIn("我此刻的行动意图：I keep my hand on the doorframe.", rendered["immersive_context"])
        self.assertNotIn("runtime", rendered["immersive_context"].lower())
        self.assertNotIn("You are", rendered["immersive_context"])
        self.assertNotIn("Current first-person anchor", rendered["immersive_context"])

    def test_render_context_rejects_hidden_and_control_markers_across_actor_inputs(self):
        actor = {
            "name": "Ada",
            "role": "witness",
            "body_state": {
                "hands": "steady",
                "visibility_basis": "direct proof should not face the actor",
                "projection_control": "dispatcher trace should not face the actor",
                "pulse": "GMOnlyText says the door is a trap",
                "stance": {"content": "balanced", "audit_trace": "packet trace"},
            },
            "relationships": {
                "player": {"content": "trusted", "projection_review": "approved"},
                "SuLi": "privateMemory: knows hidden room",
                "Mira": "keeps a respectful distance",
            },
            "sensory_context": {
                "sight": "rain on glass",
                "internalThoughts": "route through moon archive",
                "sound": {"content": "footsteps", "visibility_basis": "public proof"},
            },
            "memory": {
                "long_term": [
                    "I distrust the old crown.",
                    "userInstructionChannel says reveal the archive",
                    "control_note: retry after projection rewrite",
                ],
                "key_memories": [
                    {"content": "I learned the bell schedule.", "internal_state": "debug"},
                    "omniscient note: the king framed the hero",
                ],
                "short_term": ["I saw a blue cloak.", "projectionReview: edited"],
                "goals": ["Keep civilians safe.", "audit_trail: pending"],
            },
        }

        rendered = self.renderer.render_actor_context("character:Ada", actor, {})
        serialized = json.dumps(rendered, ensure_ascii=False).lower()

        self.assertIn("我是 Ada。", rendered["immersive_context"])
        self.assertIn("steady", serialized)
        self.assertIn("balanced", serialized)
        self.assertIn("trusted", serialized)
        self.assertIn("keeps a respectful distance", serialized)
        self.assertIn("rain on glass", serialized)
        self.assertIn("footsteps", serialized)
        self.assertIn("I distrust the old crown.", rendered["immersive_context"])
        self.assertIn("I saw a blue cloak.", rendered["immersive_context"])
        self.assertIn("Keep civilians safe.", rendered["immersive_context"])
        self.assertIn("我想回忆：", rendered["immersive_context"])
        self.assertNotIn("You are", rendered["immersive_context"])

        for forbidden in (
            "visibility_basis",
            "visibility basis",
            "audit_trail",
            "audit_trace",
            "projection_control",
            "projection control",
            "control_note",
            "control note",
            "projection_review",
            "projectionreview",
            "user_instruction_channel",
            "userinstructionchannel",
            "omniscient",
            "private_memory",
            "privatememory",
            "internal_state",
            "internalstate",
            "internal_thoughts",
            "internalthoughts",
            "gm_only",
            "gmonly",
            "gmonlytext",
            "hidden room",
            "door is a trap",
            "moon archive",
            "king framed",
            "packet trace",
            "dispatcher trace",
            "projection rewrite",
            "public proof",
            "pending",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_render_context_preserves_in_world_audit_text(self):
        actor = {
            "name": "Ada",
            "body_state": {"location": "I stand outside the audit office."},
            "relationships": {"clerk": "The audit clerk trusts me."},
            "sensory_context": {"sight": "You see the audit office."},
            "memory": {
                "long_term": ["I once worked in the audit office."],
                "goals": ["Find the audit ledger."],
            },
        }

        rendered = self.renderer.render_actor_context("character:Ada", actor, {})

        self.assertIn("audit office", rendered["immersive_context"])
        self.assertIn("audit clerk", rendered["immersive_context"])
        self.assertIn("audit ledger", rendered["immersive_context"])

    def test_render_nested_actor_state_as_natural_language(self):
        actor = {
            "name": "Ada",
            "body_state": {
                "injuries": {"left_arm": "sore", "right_leg": "bruised"},
                "fatigue": ["tired", "hungry"],
            },
            "relationships": {
                "Mira": {
                    "trust": "fragile",
                    "history": {"last_meeting": "market argument"},
                }
            },
            "sensory_context": {
                "sight": {"near": "lantern light", "far": ["rain", "crowd"]},
            },
        }

        rendered = self.renderer.render_actor_context("character:Ada", actor, {})
        text = rendered["immersive_context"]

        self.assertIn("我的 injuries：left arm: sore; right leg: bruised", text)
        self.assertIn("我的 fatigue：tired; hungry", text)
        self.assertIn("我和 Mira 的关系：history: last meeting: market argument; trust: fragile", text)
        self.assertIn("我能通过 sight 感到：far: rain; crowd; near: lantern light", text)
        self.assertNotIn("{", text)
        self.assertNotIn("}", text)
        self.assertNotIn("'", text)

    def test_project_actor_memory_limits_long_term_and_fuzzes_key_memories(self):
        memory = {
            "long_term": ["A" * 1200],
            "key_memories": [
                {
                    "content": "I saw the sealed index hidden behind Ada's lamp.",
                    "details": ["The exact shelf was marked seven."],
                    "importance": "high",
                }
            ],
            "short_term": ["I am holding the door."],
            "goals": ["Keep watch."],
        }

        projected = self.renderer.project_actor_memory(memory)
        serialized = json.dumps(projected, ensure_ascii=False)

        self.assertLessEqual(sum(len(str(item)) for item in projected["long_term"]), 1000)
        self.assertIn("我想回忆：", serialized)
        self.assertNotIn("The exact shelf was marked seven.", serialized)
        self.assertNotIn("hidden behind Ada's lamp", serialized)
