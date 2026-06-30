import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AssetsFrontendManifestTests(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "skills" / "styles" / "index.html").read_text(encoding="utf-8")

    def test_story_body_does_not_render_asset_job_notices(self):
        self.assertNotIn("renderGeneratedAssetNotices(contentEl)", self.index)
        self.assertNotIn("function renderGeneratedAssetNotices", self.index)

    def test_top_status_uses_compact_agent_and_pending_asset_fields(self):
        self.assertNotIn("reply-progress-bar", self.index)
        self.assertNotIn("reply-progress-fill", self.index)
        self.assertIn("agent-status-current", self.index)
        self.assertIn("asset-pending-count", self.index)
        self.assertIn("assets.pending_job_count", self.index)

    def test_story_inline_assets_are_filtered_by_turn_round_id(self):
        self.assertIn("function getCurrentRoundId", self.index)
        self.assertIn("window.CURRENT_ROUND_ID", self.index)
        self.assertIn(".turn-wrap[data-round-id]", self.index)
        self.assertIn("turn.getAttribute('data-round-id')", self.index)
        self.assertIn("renderGeneratedAssetStrip(target, roundId, assets)", self.index)
        self.assertIn("a.display_policy === 'story_inline'", self.index)
        self.assertIn("a.round_id === roundId", self.index)
        self.assertNotIn("!roundId ||", self.index)


if __name__ == "__main__":
    unittest.main()
