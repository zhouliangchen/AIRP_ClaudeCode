import importlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
if str(SKILLS) not in sys.path:
    sys.path.insert(0, str(SKILLS))


MODULE_ALIASES = {
    "actor_memory_store": "agents.actor.memory_store",
    "actor_context_renderer": "agents.actor.context_renderer",
    "actor_recall_artifacts": "agents.actor.recall_artifacts",
    "agent_actor_batches": "agents.actor.batches",
    "agent_actor_runtime": "agents.actor.runtime",
    "agent_memory": "agents.actor.memory",
    "agent_memory_model": "agents.actor.memory_model",
    "player_decision_evidence": "agents.gm.player_decision_evidence",
    "subgm_threads": "agents.subgm.threads",
    "subgm_turn_loop": "agents.subgm.turn_loop",
    "agent_projection": "agents.projection.context",
    "projection_agent": "agents.projection.agent",
    "agent_visibility": "agents.projection.visibility",
    "agent_visibility_guard": "agents.projection.visibility_guard",
    "postprocess_outputs": "agents.postprocess.outputs",
    "assets_ui_agent": "agents.assets_ui.agent",
    "assets_ui_runtime": "agents.assets_ui.runtime",
    "asset_job_queue": "agents.assets_ui.job_queue",
    "agent_prompts": "agents.shared.prompts",
    "agent_schemas": "agents.shared.schemas",
    "agent_interactions": "agents.shared.interactions",
    "input_analysis": "agents.input_analyst.analysis",
    "agent_run": "runtime.agent_run",
    "agent_messages": "runtime.agent_messages",
    "agent_intents": "runtime.agent_intents",
    "agent_snapshots": "runtime.agent_snapshots",
    "agent_lifecycle": "runtime.agent_lifecycle",
    "agent_outputs": "runtime.agent_outputs",
    "agent_packets": "runtime.agent_packets",
    "agent_runtime_pump": "runtime.agent_runtime_pump",
    "agent_turn_loop": "runtime.agent_turn_loop",
    "round_runtime": "runtime.round_runtime",
    "round_state": "runtime.round_state",
    "runtime_settings": "runtime.runtime_settings",
    "hidden_settings": "runtime.hidden_settings",
    "self_repair": "runtime.self_repair",
    "io_utils": "runtime.io_utils",
    "response_parser": "runtime.response_parser",
    "write_memory": "runtime.write_memory",
    "token_stats": "runtime.token_stats",
    "capability_registry": "capabilities.registry",
    "capability_executors": "capabilities.executors",
    "input_routing_requests": "capabilities.input_routing_requests",
    "replay_capabilities": "capabilities.replay",
    "replay_executor": "capabilities.replay_executor",
    "retcon_replay": "capabilities.retcon",
    "llm_provider": "llm.provider",
    "llm_runner": "llm.runner",
    "llm_settings": "llm.settings",
    "model_debug": "llm.model_debug",
    "handler": "frontend.handler",
    "import_card": "importing.card",
    "match_worldbook": "importing.worldbook",
    "mvu_check": "importing.mvu_check",
    "mvu_engine": "domain.mvu_engine",
    "objective_world": "domain.objective_world",
    "character_registry": "domain.character_registry",
    "character_promotions": "domain.character_promotions",
}


def load_repo_module(name):
    module_name = MODULE_ALIASES.get(name, name)
    module = importlib.import_module(module_name)
    return importlib.reload(module)
