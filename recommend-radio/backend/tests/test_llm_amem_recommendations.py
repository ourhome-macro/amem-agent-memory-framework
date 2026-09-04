import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amem_bridge import AmemBridge
from music_profile import MusicProfile, RelevantMemory
from profile_projector import (
    NvidiaOpenAIChatClient,
    ProfileProjector,
    RecommendationOpenAIChatClient,
    _default_llm_client,
)
from profile_statement_service import ProfileStatementService
from discovery_planner import DiscoveryPlanner, TAG_SEARCH_SUFFIX
from recommendation_service import RecommendationCandidate, RecommendationService
from request_spec import RequestInterpreter, RequestSpec
from models import Track


VALID_BVID = "BV1Q541167Qg"


class FakeLLM:
    def __init__(self, content: str):
        self.content = content

    def complete(self, *, system_prompt: str, user_prompt: str):
        return type("Response", (), {"content": self.content})()


class FakeRetriever:
    def __init__(self, memories):
        self.memories = memories

    def retrieve_memories(self, user_id: str, scene: str, *, limit: int = 12):
        return self.memories[:limit]


class FlakyLLM:
    def __init__(self, first_error: Exception, content: str):
        self.first_error = first_error
        self.content = content
        self.calls = 0

    def complete(self, *, system_prompt: str, user_prompt: str):
        self.calls += 1
        if self.calls == 1:
            raise self.first_error
        return type("Response", (), {"content": self.content})()


class SequenceLLM:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def complete(self, *, system_prompt: str, user_prompt: str):
        self.calls += 1
        content = self.contents[min(self.calls - 1, len(self.contents) - 1)]
        return type("Response", (), {"content": content})()


class FakeBiliClient:
    def __init__(self, tracks):
        self.tracks = tracks
        self.search_keywords = []

    def search(self, keyword, page=1, page_size=20):
        self.search_keywords.append(keyword)
        return self.tracks[:page_size]

    def list_user_tracks(self, mid, page=1, page_size=24, order="click"):
        return {"tracks": []}


class AgentSearchBiliClient:
    def __init__(self):
        self.search_keywords = []

    def search(self, keyword, page=1, page_size=20):
        self.search_keywords.append(keyword)
        query_offset = 100 + len(self.search_keywords) * 100
        return [
            Track(
                bvid=f"BV1Q54116{query_offset + index:03d}",
                cid=query_offset + index,
                title=f"{keyword} Agent candidate {index}",
                owner=f"{keyword} UP {index}",
                owner_mid=900000 + query_offset + index,
                duration=240,
            )
            for index in range(1, 4)
        ]

    def list_user_tracks(self, mid, page=1, page_size=24, order="click"):
        return {"tracks": []}


class LlmAmemRecommendationTests(unittest.TestCase):
    def test_amem_bridge_records_raw_event_and_keeps_same_window_preference_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=1,
                title="Vocaloid Miku Song",
                owner="Miku UP",
                owner_mid=100,
            )

            for _ in range(3):
                bridge.record_behavior(
                    {
                        "userId": "user-a",
                        "event": "completed",
                        "track": track.to_dict(),
                    }
                )

            events = bridge.handle.runtime.event_store.list_events()
            records = bridge.handle.runtime.memory_store.list_records()

            self.assertEqual(len(events), 3)
            self.assertEqual(records, [])

    def test_amem_bridge_keeps_rap_freestyle_as_semantic_negative_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=32,
                title="Rap Freestyle Beat",
                owner="Rap UP",
                owner_mid=2002,
            )

            bridge.record_behavior(
                {
                    "userId": "user-a",
                    "event": "skipped",
                    "track": track.to_dict(),
                }
            )
            records = bridge.handle.runtime.memory_store.list_records()
            self.assertEqual(records, [])

    def test_amem_bridge_does_not_extract_rap_from_trap(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=33,
                title="Vocaloid trap beat",
                owner="Miku UP",
                owner_mid=100,
            )

            bridge.record_behavior(
                {
                    "userId": "user-a",
                    "event": "skipped",
                    "track": track.to_dict(),
                }
            )
            contents = [record.content for record in bridge.handle.runtime.memory_store.list_records()]

            self.assertFalse(any("topic: Rap" in content for content in contents))

    def test_amem_bridge_ignores_generic_music_topic_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=31,
                title="Miku Vocaloid playlist music song",
                owner="Miku UP",
                owner_mid=100,
            )

            bridge.record_behavior(
                {
                    "userId": "user-a",
                    "event": "completed",
                    "track": track.to_dict(),
                }
            )
            self.assertEqual(bridge.handle.runtime.memory_store.list_records(), [])

    def test_amem_bridge_extracts_music_entities_from_chinese_pop_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=34,
                title="陈奕迅 《最佳损友》",
                owner="华语音乐精选",
                owner_mid=2046693818,
            )

            bridge.record_behavior(
                {
                    "userId": "user-a",
                    "event": "completed",
                    "track": track.to_dict(),
                }
            )
            records = bridge.handle.runtime.memory_store.list_records()
            self.assertEqual(records, [])

    def test_amem_bridge_private_review_writes_track_entities_as_working_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            track = Track(
                bvid=VALID_BVID,
                cid=35,
                title="【初音未来】VOCALOID 治愈新曲",
                owner="Miku Producer",
                owner_mid=1001,
            )

            bridge.record_behavior(
                {
                    "userId": "user-a",
                    "event": "track_reviewed",
                    "rating": 5,
                    "mood": "calm",
                    "note": "loopable vocaloid song",
                    "track": track.to_dict(),
                }
            )
            records = bridge.handle.runtime.memory_store.list_records()
            self.assertEqual(records, [])

    def test_profile_projector_accepts_llm_json_and_keeps_evidence_ids(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
            )
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {"Vocaloid": 0.9},
                  "negative_topics": {"Rap": 0.4},
                  "preferred_uploaders": {"100": 0.8},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {"治愈": 0.7},
                  "recent_intents": ["Vocaloid 新曲"],
                  "same_uploader_limit": 2,
                  "exploration_ratio": 0.5,
                  "evidence_memory_ids": ["m1", "not-real"],
                  "confidence": 0.8
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(projection.profile.positive_topics["Vocaloid"], 0.9)
        self.assertEqual(projection.profile.evidence_memory_ids, ["m1"])
        self.assertEqual(projection.profile.same_uploader_limit, 2)

    def test_profile_projector_retries_empty_llm_response(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
            )
        ]
        llm = FlakyLLM(
            RuntimeError("empty assistant message"),
            """
            {
              "positive_topics": {"Vocaloid": 0.9},
              "negative_topics": {},
              "preferred_uploaders": {},
              "avoid_uploaders": {},
              "blocked_uploaders": {},
              "mood_weights": {},
              "recent_intents": [],
              "same_uploader_limit": 0,
              "exploration_ratio": 0,
              "evidence_memory_ids": ["m1"],
              "confidence": 0.8
            }
            """,
        )
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=llm,
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(llm.calls, 2)
        self.assertEqual(projection.profile.source, "llm")
        self.assertEqual(projection.profile.positive_topics["Vocaloid"], 0.9)

    def test_profile_projector_retries_invalid_json_response(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
            )
        ]
        llm = SequenceLLM(
            [
                "not json",
                """
                {
                  "positive_topics": {"Vocaloid": 0.9},
                  "negative_topics": {},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1"],
                  "confidence": 0.8
                }
                """,
            ]
        )
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=llm,
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(llm.calls, 2)
        self.assertEqual(projection.profile.source, "llm")
        self.assertEqual(projection.profile.positive_topics["Vocaloid"], 0.9)

    def test_profile_projector_repairs_placeholder_topic_keys(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
                salience=0.9,
                confidence=0.8,
            ),
            RelevantMemory(
                memory_id="m2",
                content="User recently skipped multiple tracks for topic: Rap.",
                layer="working",
                memory_type="belief",
                salience=0.7,
                confidence=0.6,
            ),
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {"topic": 0.9},
                  "negative_topics": {"actual_extracted_topic_name": 0.8},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1", "m2"],
                  "confidence": 0.7
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(projection.profile.source, "llm")
        self.assertEqual(projection.profile.positive_topics, {"Vocaloid": 0.9})
        self.assertEqual(projection.profile.negative_topics, {"Rap": 0.8})

    def test_profile_projector_drops_placeholder_intents_and_aligns_memory_evidence(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
                salience=0.84,
                confidence=0.84,
            ),
            RelevantMemory(
                memory_id="m2",
                content="User shows recent negative music signal for topic: Rap.",
                layer="working",
                memory_type="belief",
                salience=0.68,
                confidence=0.64,
            ),
            RelevantMemory(
                memory_id="m3",
                content="User rated music mood 'calm' as positive with rating 5.",
                layer="working",
                memory_type="belief",
                salience=0.8,
                confidence=0.82,
            ),
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {},
                  "negative_topics": {"Vocaloid": 0.72, "Rap": 0.68},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": ["search intent"],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1", "m2", "m3"],
                  "confidence": 0.7
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(projection.profile.recent_intents, [])
        self.assertEqual(projection.profile.positive_topics, {"Vocaloid": 0.84})
        self.assertEqual(projection.profile.negative_topics, {"Rap": 0.68})
        self.assertEqual(projection.profile.mood_weights, {"calm": 0.82})

    def test_profile_projector_keeps_mood_only_memory_out_of_positive_topics(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User rated music mood '青春' as positive with rating 5.",
                layer="working",
                memory_type="belief",
                salience=0.8,
                confidence=0.82,
                metadata={"signal": "mood", "mood": "青春"},
            )
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {"青春": 0.82},
                  "negative_topics": {},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {"青春": 0.82},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1"],
                  "confidence": 0.7
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(projection.profile.positive_topics, {})
        self.assertEqual(projection.profile.mood_weights, {"青春": 0.82})

    def test_profile_projector_drops_keyword_only_negative_topics(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User shows recent negative music signal for topic: 主办方发声.",
                layer="working",
                memory_type="belief",
                salience=0.5,
                confidence=0.48,
                metadata={"signal": "negative_topic", "topic": "主办方发声", "entityKind": "keyword"},
            ),
            RelevantMemory(
                memory_id="m2",
                content="User shows recent negative music signal for topic: Rap.",
                layer="working",
                memory_type="belief",
                salience=0.5,
                confidence=0.48,
                metadata={"signal": "negative_topic", "topic": "Rap", "entityKind": "genre"},
            ),
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {},
                  "negative_topics": {"主办方发声": 0.48, "Rap": 0.48},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1", "m2"],
                  "confidence": 0.7
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertNotIn("主办方发声", projection.profile.negative_topics)
        self.assertIn("Rap", projection.profile.negative_topics)

    def test_profile_projector_prefers_explicit_negative_statement_over_positive_conflict(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User explicitly states a music preference for topic: Rap.",
                layer="working",
                memory_type="belief",
                salience=0.82,
                confidence=0.82,
                metadata={"signal": "profile_statement_positive_topic", "topic": "Rap"},
            ),
            RelevantMemory(
                memory_id="m2",
                content="User explicitly states a negative music preference for topic: Rap.",
                layer="working",
                memory_type="belief",
                salience=0.86,
                confidence=0.86,
                metadata={"signal": "profile_statement_negative_topic", "topic": "Rap"},
            ),
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {"Rap": 0.82},
                  "negative_topics": {"Rap": 0.86},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1", "m2"],
                  "confidence": 0.8
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertNotIn("Rap", projection.profile.positive_topics)
        self.assertEqual(projection.profile.negative_topics["Rap"], 0.86)

    def test_music_profile_accepts_llm_array_score_maps(self):
        profile = MusicProfile.from_dict(
            {
                "positive_topics": [{"topic": "Vocaloid", "confidence": 0.9}],
                "negative_topics": [{"name": "Rap", "weight": 0.7}],
                "preferred_uploaders": [{"owner_mid": 100, "score": 0.8}],
                "confidence": 0.75,
            },
            source="llm",
        )

        self.assertEqual(profile.positive_topics["Vocaloid"], 0.9)
        self.assertEqual(profile.negative_topics["Rap"], 0.7)
        self.assertEqual(profile.preferred_uploaders["100"], 0.8)

    def test_recommendation_event_preserves_private_review_payload_for_amem(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            service = RecommendationService(
                db_path=Path(tmp) / "test.sqlite3",
                bili_client=FakeBiliClient([]),
                amem_bridge=bridge,
            )
            track = Track(
                bvid=VALID_BVID,
                cid=30,
                title="Calm Vocaloid Song",
                owner="Miku UP",
                owner_mid=100,
            )
            service.library.upsert_track(track)

            response = service.record_event(
                {
                    "trackId": track.track_id,
                    "event": "track_reviewed",
                    "rating": 5,
                    "mood": "calm",
                    "note": "loopable",
                }
            )
            records = bridge.handle.runtime.memory_store.list_records()

            self.assertNotIn("behaviorPayload", response)
            self.assertEqual(records, [])

    def test_profile_projector_falls_back_on_invalid_llm_json(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User shows recent negative music signal for topic: Rap.",
                layer="working",
                memory_type="belief",
            )
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM("not json"),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile(positive_topics={"Vocaloid": 0.7}),
        )

        self.assertEqual(projection.profile.source, "fallback")
        self.assertIn("Vocaloid", projection.profile.positive_topics)
        self.assertIn("Rap", projection.profile.negative_topics)

    def test_profile_projector_falls_back_on_empty_llm_profile_with_memories(self):
        memories = [
            RelevantMemory(
                memory_id="m1",
                content="User has a stable music preference for topic: Vocaloid.",
                layer="core",
                memory_type="belief",
            )
        ]
        projector = ProfileProjector(
            FakeRetriever(memories),
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {},
                  "negative_topics": {},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": ["m1"],
                  "confidence": 0
                }
                """
            ),
            enabled=True,
            ttl_seconds=0,
        )

        projection = projector.project(
            user_id="user-a",
            scene="home",
            fallback_profile=MusicProfile.empty(),
        )

        self.assertEqual(projection.profile.source, "llm")
        self.assertIn("Vocaloid", projection.profile.positive_topics)

    def test_default_llm_client_supports_nvidia_provider_config(self):
        with patch.dict(
            os.environ,
            {
                "RECOMMEND_LLM_PROVIDER": "nvidia",
                "RECOMMEND_LLM_BASE_URL": "https://integrate.api.nvidia.com/v1",
                "RECOMMEND_LLM_API_KEY_ENV": "NVIDIA_API_KEY",
                "RECOMMEND_LLM_MODEL": "deepseek-ai/deepseek-v4-pro",
                "RECOMMEND_LLM_TIMEOUT_SECONDS": "9",
                "RECOMMEND_LLM_TEMPERATURE": "1",
                "RECOMMEND_LLM_TOP_P": "0.95",
                "RECOMMEND_LLM_MAX_TOKENS": "512",
            },
            clear=False,
        ):
            client = _default_llm_client()

        self.assertIsInstance(client, NvidiaOpenAIChatClient)
        self.assertEqual(client.base_url, "https://integrate.api.nvidia.com/v1")
        self.assertEqual(client.api_key_env, "NVIDIA_API_KEY")
        self.assertEqual(client.model, "deepseek-ai/deepseek-v4-pro")
        self.assertEqual(client.timeout_seconds, 9)
        self.assertEqual(client.top_p, 0.95)
        self.assertEqual(client.max_tokens, 512)
        self.assertTrue(client.json_response)

    def test_default_llm_client_supports_deepseek_provider_config(self):
        with patch.dict(
            os.environ,
            {
                "RECOMMEND_LLM_PROVIDER": "deepseek",
                "RECOMMEND_LLM_MODEL": "deepseek-v4-flash",
                "RECOMMEND_LLM_TIMEOUT_SECONDS": "9",
                "RECOMMEND_LLM_TEMPERATURE": "0.2",
                "RECOMMEND_LLM_TOP_P": "0.95",
                "RECOMMEND_LLM_MAX_TOKENS": "512",
            },
            clear=False,
        ):
            client = _default_llm_client()

        self.assertIsInstance(client, RecommendationOpenAIChatClient)
        self.assertEqual(client.base_url, "https://api.deepseek.com")
        self.assertEqual(client.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(client.model, "deepseek-v4-flash")
        self.assertEqual(client.timeout_seconds, 9)
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.top_p, 0.95)
        self.assertEqual(client.max_tokens, 512)
        self.assertTrue(client.json_response)

    def test_discovery_planner_uses_positive_topics_and_excludes_negative_query_terms(self):
        planner = DiscoveryPlanner(search_budget=2)
        plan = planner.plan(
            profile=MusicProfile(
                positive_topics={"Vocaloid": 0.9, "Rap": 0.8},
                negative_topics={"Rap": 0.7},
                recent_intents=["Vocaloid new songs"],
            ),
            scene="home",
            request_spec=RequestSpec(),
        )

        self.assertEqual(len(plan.search_queries), 2)
        self.assertTrue(any("Vocaloid" in query for query in plan.search_queries))
        self.assertFalse(any(query.casefold().startswith("rap") for query in plan.search_queries))

    def test_discovery_planner_combines_topic_and_mood_in_search_queries(self):
        planner = DiscoveryPlanner(search_budget=3)
        plan = planner.plan(
            profile=MusicProfile(
                positive_topics={"Vocaloid": 0.9, "Miku": 0.85},
                mood_weights={"calm": 0.82},
            ),
            scene="home",
            request_spec=RequestSpec(),
        )

        self.assertNotIn(f"calm {TAG_SEARCH_SUFFIX}", plan.search_queries)
        self.assertEqual(plan.search_queries, ["Vocaloid calm", "Vocaloid chill", "初音未来 calm"])
        self.assertTrue(any("Vocaloid" in query and "calm" in query for query in plan.search_queries))
        self.assertTrue(any("Vocaloid" in query and "chill" in query for query in plan.search_queries))
        self.assertTrue(any("初音未来" in query or "Miku" in query for query in plan.search_queries))

    def test_discovery_planner_blocked_terms_use_word_boundaries(self):
        planner = DiscoveryPlanner(search_budget=2)
        plan = planner.plan(
            profile=MusicProfile(
                positive_topics={"Trap": 0.8, "Rap": 0.7},
                negative_topics={"Rap": 0.6},
            ),
            scene="home",
            request_spec=RequestSpec(),
        )

        self.assertEqual(plan.search_queries, [f"Trap {TAG_SEARCH_SUFFIX}"])

    def test_discovery_planner_avoids_duplicate_topic_mood_query_terms(self):
        planner = DiscoveryPlanner(search_budget=1)
        plan = planner.plan(
            profile=MusicProfile(
                positive_topics={"青春": 0.82},
                mood_weights={"青春": 0.82},
            ),
            scene="home",
            request_spec=RequestSpec(),
        )

        self.assertEqual(plan.search_queries, ["青春 青春"])

    def test_discovery_planner_prioritizes_request_scope_over_profile_topics(self):
        planner = DiscoveryPlanner(search_budget=1)
        plan = planner.plan(
            profile=MusicProfile(positive_topics={"Vocaloid": 0.9}),
            scene="home",
            request_spec=RequestInterpreter().interpret("来点欧美的歌"),
        )

        self.assertEqual(plan.search_queries, ["欧美流行 英文"])
        self.assertTrue(plan.request_first)

    def test_recommendation_validation_filters_uploaders_but_not_negative_topics(self):
        service = RecommendationService(
            db_path=Path(tempfile.mkdtemp()) / "test.sqlite3",
            bili_client=FakeBiliClient([]),
        )
        rap = RecommendationCandidate(
            track=Track(bvid=VALID_BVID, cid=20, title="Rap Song", owner="Rap UP", owner_mid=201).to_dict(),
            score=5,
            source="discovery_search",
            reason="test",
        )
        blocked = RecommendationCandidate(
            track=Track(bvid=VALID_BVID, cid=21, title="Vocaloid", owner="Blocked", owner_mid=999).to_dict(),
            score=9,
            source="discovery_search",
            reason="test",
        )

        selected = service.validate_and_finalize(
            [blocked, rap],
            profile=MusicProfile(negative_topics={"Rap": 1.0}, blocked_uploaders={"999": 1.0}),
            legacy_profile=type("Legacy", (), {
                "skipped_track_ids": set(),
                "recently_heard_track_ids": set(),
                "recently_recommended_track_ids": set(),
            })(),
            limit=2,
            scene="home",
        )

        self.assertEqual([item.track["trackId"] for item in selected], [rap.track["trackId"]])

    def test_discovery_admits_candidates_before_recommendation_serves_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RecommendationService(
                db_path=Path(tmp) / "test.sqlite3",
                bili_client=AgentSearchBiliClient(),
            )
            seed = Track(
                bvid=VALID_BVID,
                cid=40,
                title="Miku Vocaloid playlist",
                owner="Miku Producer",
                owner_mid=1001,
            )
            service.library.upsert_track(seed)
            service.library.add_recent(seed, completed=True)
            service.library.save_review(seed, rating=5, mood="calm", note="loopable")

            legacy_profile = service._load_user_profile()
            profile = MusicProfile(
                positive_topics={"Vocaloid": 0.84},
                mood_weights={"calm": 0.82},
                preferred_uploaders={"1001": 0.75},
                source="llm",
            )

            discovery = service.discovery_service.discover_now(
                profile=profile,
                scene="home",
                limit=5,
                request_spec=RequestSpec(),
            )
            result = service.list_recommendations(scene="home", limit=5)

            self.assertTrue(any(query.startswith("Vocaloid ") for query in discovery["queries"]))
            self.assertGreaterEqual(discovery["admitted"], 6)
            self.assertGreaterEqual(len(result["items"]), 5)
            self.assertTrue(all(item["source"] in {"discovery_search", "explore"} for item in result["items"]))

    def test_recommendation_service_persists_debug_trace_and_profile_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RecommendationService(
                db_path=Path(tmp) / "test.sqlite3",
                bili_client=AgentSearchBiliClient(),
                profile_projector=type("Projector", (), {
                    "project": lambda _self, **_kwargs: type("Projection", (), {
                        "profile": MusicProfile(
                            positive_topics={"Vocaloid": 0.84},
                            mood_weights={"calm": 0.82},
                            source="llm",
                        ),
                        "memories": [],
                        "trace_id": "profile:test",
                    })(),
                })(),
            )
            seed = Track(
                bvid=VALID_BVID,
                cid=50,
                title="Miku Vocaloid playlist",
                owner="Miku Producer",
                owner_mid=1001,
            )
            service.library.upsert_track(seed)
            service.library.add_recent(seed, completed=True)
            service.library.save_review(seed, rating=5, mood="calm", note="loopable")

            service.discovery_service.discover_now(
                profile=MusicProfile(positive_topics={"Vocaloid": 0.84}, mood_weights={"calm": 0.82}),
                scene="home",
                limit=5,
                request_spec=RequestSpec(),
            )

            result = service.list_recommendations(scene="home", limit=5)
            trace = service.latest_debug_trace(scene="home")
            analysis = service.music_profile_analysis(scene="home")

            self.assertTrue(result["debugTraceId"].startswith("recommend:"))
            self.assertTrue(trace["available"])
            self.assertEqual(trace["traceId"], result["debugTraceId"])
            self.assertIn("memoryRetrieval", trace)
            self.assertIn("musicProfile", trace)
            self.assertIn("candidatePool", trace)
            self.assertNotIn(f"calm {TAG_SEARCH_SUFFIX}", trace["candidatePool"]["searchQueries"])
            self.assertTrue(trace["finalResults"])
            self.assertEqual(analysis["scene"], "home")
            self.assertIn("profile", analysis)
            self.assertIn("summary", analysis)

    def test_recommendation_service_backfills_music_entity_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            service = RecommendationService(
                db_path=Path(tmp) / "test.sqlite3",
                bili_client=FakeBiliClient([]),
                amem_bridge=bridge,
            )
            track = Track(
                bvid=VALID_BVID,
                cid=60,
                title="陈奕迅 《最佳损友》",
                owner="华语音乐精选",
                owner_mid=2046693818,
            )
            service.library.add_recent(track, completed=True)
            service.library.add_like(track)
            service.library.save_review(track, rating=5, mood="温柔", note="粤语老歌")

            result = service.backfill_music_memories(limit=20)
            contents = [record.content for record in bridge.handle.runtime.memory_store.list_records()]

            self.assertGreater(result["eventsRecorded"], 0)
            self.assertEqual(result["memoryCount"], 0)
            self.assertEqual(contents, [])

    def test_profile_statement_rules_extract_user_description(self):
        service = ProfileStatementService(None, enabled=False)
        profile, source = service._extract(
            "华语流行音乐忠实粉丝，钟爱周王陶林天王，张惠妹，孙燕姿这些天后的歌曲，"
            "喜欢rnb，同时喜欢一些欧美流行，不喜欢rap，有时候喜欢听摇滚乐"
        )

        self.assertEqual(source, "rules")
        self.assertIn("华语流行", profile.positive_topics)
        self.assertIn("R&B", profile.positive_topics)
        self.assertIn("欧美流行", profile.positive_topics)
        self.assertIn("摇滚", profile.positive_topics)
        self.assertIn("周杰伦", profile.positive_topics)
        self.assertIn("张惠妹", profile.positive_topics)
        self.assertEqual(profile.negative_topics, {"Rap": 0.86})

    def test_profile_statement_llm_extraction_is_corrected_by_negative_rules(self):
        service = ProfileStatementService(
            None,
            llm_client=FakeLLM(
                """
                {
                  "positive_topics": {"Rap": 0.7, "华语流行": 0.8},
                  "negative_topics": {},
                  "preferred_uploaders": {},
                  "avoid_uploaders": {},
                  "blocked_uploaders": {},
                  "mood_weights": {},
                  "recent_intents": [],
                  "same_uploader_limit": 0,
                  "exploration_ratio": 0,
                  "evidence_memory_ids": [],
                  "confidence": 0.7
                }
                """
            ),
        )

        profile, source = service._extract("华语流行音乐忠实粉丝，喜欢rnb，不喜欢rap")

        self.assertEqual(source, "llm+rules")
        self.assertNotIn("Rap", profile.positive_topics)
        self.assertEqual(profile.negative_topics["Rap"], 0.86)
        self.assertIn("R&B", profile.positive_topics)

    def test_recommendation_service_submits_profile_statement_to_amem(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            service = RecommendationService(
                db_path=Path(tmp) / "test.sqlite3",
                bili_client=FakeBiliClient([]),
                amem_bridge=bridge,
            )
            service.profile_statement_service = ProfileStatementService(bridge, enabled=False)

            result = service.submit_profile_statement(
                "华语流行音乐忠实粉丝，喜欢rnb和欧美流行，不喜欢rap"
            )
            contents = [record.content for record in bridge.handle.runtime.memory_store.list_records()]

            self.assertGreater(len(result["memoryIds"]), 0)
            self.assertTrue(any("topic: 华语流行" in content for content in contents))
            self.assertTrue(any("topic: R&B" in content for content in contents))
            self.assertTrue(any("topic: Rap" in content for content in contents))
            self.assertIn("analysis", result)

    def test_amem_retrieval_prioritizes_profile_statement_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = AmemBridge(Path(tmp) / "amem.sqlite3")
            user_id = "user-a"
            for index in range(4):
                bridge.record_behavior(
                    {
                        "userId": user_id,
                        "event": "skipped",
                        "track": Track(
                            bvid=VALID_BVID,
                            cid=100 + index,
                            title=f"J-Pop 温柔 candidate {index}",
                            owner="Noise UP",
                        ).to_dict(),
                    }
                )

            service = ProfileStatementService(bridge, enabled=False)
            service.submit(user_id=user_id, description="华语流行音乐忠实粉丝，喜欢rnb，不喜欢rap")

            memories = bridge.retrieve_memories(user_id, "home", limit=4)
            signals = [memory.metadata.get("signal") for memory in memories]

            self.assertTrue(signals)
            self.assertTrue(all(str(signal).startswith("profile_statement_") for signal in signals[:3]))


if __name__ == "__main__":
    unittest.main()
