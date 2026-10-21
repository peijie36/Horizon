"""Offline checks for quota filling from already-scored reserve stories."""

import io
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from rich.console import Console

from src.models import (
    AIConfig,
    CategoryGroupConfig,
    ClassificationResult,
    ContentAnalysis,
    ContentItem,
    Config,
    DigestConfig,
    ProcessingConfig,
    ProcessingResult,
    ProfileSettingsConfig,
    SourceType,
    SourcesConfig,
)
from src.orchestrator import HorizonOrchestrator


def make_item(item_id, score, category="ai", profile="tech-news"):
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title=item_id,
        url=f"https://example.com/{item_id}",
        published_at=datetime.now(timezone.utc),
        metadata={"category": category},
        profile=profile,
        processing=ProcessingResult(
            classification=ClassificationResult(profile=profile, method="source_override"),
            analysis=ContentAnalysis(score=score, reason="test", summary=item_id),
        ),
    )


class DigestReserveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orchestrator = HorizonOrchestrator.__new__(HorizonOrchestrator)
        self.orchestrator.config = SimpleNamespace(
            digest=DigestConfig(
                fill_from_reserve=True,
                max_items=4,
                category_groups={
                    "ai": CategoryGroupConfig(limit=2, categories=["ai"]),
                    "finance": CategoryGroupConfig(limit=2, categories=["finance"]),
                },
            ),
            processing=ProcessingConfig(profile_settings={
                "tech-news": ProfileSettingsConfig(threshold=7),
                "tech-blog": ProfileSettingsConfig(threshold=4, topic_dedup=False),
            }),
            sources=SimpleNamespace(twitter=None),
        )
        self.orchestrator.console = Console(file=io.StringIO())
        self.orchestrator.profiles = SimpleNamespace(default_profile="tech-news")

        async def unchanged(items, *, log=True):
            return items

        self.orchestrator.merge_topic_duplicates = AsyncMock(side_effect=unchanged)

    async def select(self, items, *, topic_dedup=False, threshold=None):
        return await self.orchestrator.select_digest_items(
            items, topic_dedup=topic_dedup, threshold=threshold, log=False
        )

    async def test_reserves_fill_only_their_own_category_in_score_order(self):
        items = [
            make_item("ai-top", 10), make_item("ai-second", 9),
            make_item("ai-extra", 8), make_item("finance-top", 8, "finance"),
            make_item("reserve-low", 2, "finance"),
            make_item("reserve-high", 6, "finance"),
        ]
        result = await self.select(items)
        self.assertEqual([x.id for x in result.items], [
            "ai-top", "ai-second", "finance-top", "reserve-high"
        ])
        self.assertEqual(result.balanced_digest.group_counts, {"ai": 2, "finance": 2})
        self.assertEqual(result.threshold_count, 4)
        self.assertEqual(result.reserve_count, 1)
        self.assertEqual(result.topic_dedup_count, result.eligible_count)

    async def test_full_categories_do_no_extra_reserve_dedup(self):
        items = [make_item("top", 9), make_item("second", 8), make_item("unused", 6)]
        result = await self.select(items, topic_dedup=True)
        self.assertEqual([x.id for x in result.items], ["top", "second"])
        self.assertEqual(result.reserve_count, 0)
        calls = self.orchestrator.merge_topic_duplicates.await_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual([x.id for x in calls[0].args[0]], ["top", "second"])

    async def test_duplicate_reserve_is_replaced_by_next_ranked_story(self):
        async def remove_duplicate(items, *, log=True):
            return [x for x in items if x.id != "duplicate"]

        self.orchestrator.merge_topic_duplicates.side_effect = remove_duplicate
        result = await self.select([
            make_item("top", 9), make_item("duplicate", 6),
            make_item("replacement", 5), make_item("unused", 1),
        ], topic_dedup=True)
        self.assertEqual([x.id for x in result.items], ["top", "replacement"])
        self.assertEqual(result.reserve_count, 1)
        self.assertEqual(result.topic_dedup_removed, 1)
        self.assertEqual(result.topic_dedup_count, 2)
        self.assertEqual(result.eligible_count, 2)
        calls = self.orchestrator.merge_topic_duplicates.await_args_list
        self.assertEqual([[x.id for x in c.args[0]] for c in calls], [
            ["top"], ["top", "duplicate"], ["top", "replacement"]
        ])

    async def test_above_threshold_beats_higher_scoring_reserve_from_other_profile(self):
        result = await self.select([
            make_item("primary", 4, profile="tech-blog"),
            make_item("reserve", 6), make_item("lower-reserve", 5),
        ])
        self.assertEqual([x.id for x in result.items], ["primary", "reserve"])

    async def test_all_below_threshold_fill_slots_but_failed_analyses_do_not(self):
        missing = make_item("missing", 10)
        missing.processing = None
        result = await self.select([
            make_item("failed", None), missing,
            make_item("low", 1), make_item("zero", 0),
            make_item("unknown-category", 6, "world"),
        ])
        self.assertEqual([x.id for x in result.items], ["low", "zero"])
        self.assertEqual(result.reserve_count, 2)
        self.assertEqual(result.balanced_digest.group_counts.get("finance", 0), 0)

    async def test_missing_score_is_rejected_even_without_a_threshold(self):
        self.orchestrator.config.processing.profile_settings = {}
        self.assertFalse(self.orchestrator.passes_profile_filter(make_item("failed", None)))
        self.assertTrue(self.orchestrator.passes_profile_filter(make_item("valid", 0)))

    async def test_final_cap_preserves_primary_stories_before_reserves(self):
        self.orchestrator.config.digest.max_items = 1
        result = await self.select([
            make_item("primary", 4, profile="tech-blog"),
            make_item("reserve", 6, "finance"),
        ], topic_dedup=True)
        self.assertEqual([x.id for x in result.items], ["primary"])
        self.assertEqual(result.reserve_count, 0)
        self.assertEqual(self.orchestrator.merge_topic_duplicates.await_count, 0)

    async def test_disabled_mode_preserves_strict_thresholds(self):
        self.orchestrator.config.digest.fill_from_reserve = False
        result = await self.select([make_item("top", 9), make_item("reserve", 6)])
        self.assertEqual([x.id for x in result.items], ["top"])

    async def test_runtime_threshold_override_applies_to_reserve_priority(self):
        result = await self.select([
            make_item("top", 9), make_item("reserve", 7), make_item("unused", 6),
        ], threshold=8)
        self.assertEqual([x.id for x in result.items], ["top", "reserve"])
        self.assertEqual(result.threshold_count, 1)
        self.assertEqual(result.reserve_count, 1)

    async def test_reserves_do_not_turn_an_unlimited_group_into_an_extra_digest(self):
        self.orchestrator.config.digest.category_groups = {}
        result = await self.select([make_item("top", 9), make_item("unused", 6)])
        self.assertEqual([x.id for x in result.items], ["top"])

    async def test_default_group_with_a_limit_can_use_reserves(self):
        self.orchestrator.config.digest.default_group_limit = 1
        result = await self.select([make_item("best", 6, "world"), make_item("unused", 5, "world")])
        self.assertEqual([x.id for x in result.items], ["best"])
        self.assertEqual(result.reserve_count, 1)

    async def test_reserve_profile_can_disable_topic_dedup(self):
        result = await self.select([
            make_item("reserve", 3, profile="tech-blog"),
        ], topic_dedup=True)
        self.assertEqual([x.id for x in result.items], ["reserve"])
        self.orchestrator.merge_topic_duplicates.assert_not_awaited()

    async def test_reanalysis_can_demote_a_primary_into_the_reserve_pool(self):
        async def demote(items):
            items[0].processing.analysis.score = 1

        self.orchestrator._expand_twitter_discussion = demote
        result = await self.select([
            make_item("demoted", 9), make_item("primary", 8),
            make_item("reserve", 6),
        ])
        self.assertEqual([x.id for x in result.items], ["primary", "reserve"])
        self.assertEqual(result.reserve_count, 1)
        self.assertEqual(result.topic_dedup_count - result.eligible_count, 1)

    async def test_exhausted_duplicate_reserves_leave_a_slot_empty(self):
        async def only_primary(items, *, log=True):
            return [x for x in items if x.id == "top"]

        self.orchestrator.merge_topic_duplicates.side_effect = only_primary
        result = await self.select([
            make_item("top", 9), make_item("duplicate-one", 6),
            make_item("duplicate-two", 5),
        ], topic_dedup=True)
        self.assertEqual([x.id for x in result.items], ["top"])
        self.assertEqual(result.topic_dedup_removed, 2)
        self.assertEqual(result.topic_dedup_count, 1)

    async def test_run_enriches_only_final_primary_and_reserve_choices(self):
        config = Config(
            ai=AIConfig(provider="openai", model="test", api_key_env="TEST_KEY", languages=[]),
            sources=SourcesConfig(),
            processing=self.orchestrator.config.processing,
            digest=self.orchestrator.config.digest,
        )
        # Use the real orchestrator flow, with every external stage mocked.
        orchestrator = HorizonOrchestrator(config, SimpleNamespace(), console=Console(file=io.StringIO()))
        items = [
            make_item("ai-one", 9), make_item("ai-two", 8),
            make_item("finance-primary", 9, "finance"),
            make_item("finance-reserve", 6, "finance"),
            make_item("unused", 5, "finance"), make_item("failed", None, "finance"),
        ]
        orchestrator.fetch_all_sources = AsyncMock(return_value=items)
        orchestrator.analyze_items = AsyncMock(return_value=items)
        orchestrator._expand_twitter_discussion = AsyncMock()
        orchestrator.enrich_items = AsyncMock()

        async def unchanged(items, *, log=True):
            return items

        orchestrator.merge_topic_duplicates = AsyncMock(side_effect=unchanged)
        # An empty language list keeps this check from writing briefing files.
        await orchestrator.run()
        selected = orchestrator.enrich_items.await_args.args[0]
        self.assertEqual({x.id for x in selected}, {
            "ai-one", "ai-two", "finance-primary", "finance-reserve"
        })
        self.assertEqual(orchestrator.analyze_items.await_count, 1)

    async def test_mcp_uses_reserves_and_reports_nonnegative_filter_counts(self):
        from src.mcp.service import HorizonPipelineService

        items = [make_item("primary", 9), make_item("reserve", 6), make_item("unused", 5)]
        with tempfile.TemporaryDirectory() as directory:
            service = HorizonPipelineService(runs_root=Path(directory))
            service.run_store.create_run("reserve-check")
            with (
                patch.object(service, "_load_stage_items", return_value=(items, None)),
                patch.object(service, "_orchestrator", return_value=self.orchestrator),
            ):
                result = await service.filter_items(run_id="reserve-check", topic_dedup=False)
            self.assertEqual(result["kept"], 2)
            self.assertEqual(result["reserve_count"], 1)
            self.assertEqual(result["removed_after_reanalysis"], 0)
            self.assertEqual(result["removed_by_balanced_digest"], 0)
            saved = service.run_store.load_items("reserve-check", "filtered")
            self.assertEqual([x["id"] for x in saved], ["primary", "reserve"])


if __name__ == "__main__":
    unittest.main()
