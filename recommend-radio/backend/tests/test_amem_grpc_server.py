import sys
from pathlib import Path

import grpc

ROOT = Path(__file__).resolve().parents[1]
GEN_DIR = ROOT / "amem_gen"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(GEN_DIR) not in sys.path:
    sys.path.insert(0, str(GEN_DIR))

from amem.v1 import amem_pb2, amem_pb2_grpc
from amem_grpc_server import AmemGrpcService, build_server
from music_profile import MusicProfile


class FakeProjection:
    def __init__(self) -> None:
        self.profile = MusicProfile(
            positive_topics={"Vocaloid": 0.91},
            negative_topics={"Noise": 0.4},
            preferred_uploaders={"1001": 0.8},
            mood_weights={"calm": 0.7},
            recent_intents=["fresh vocaloid"],
            positive_interest_texts=["quiet vocaloid interest"],
            negative_interest_texts=["no noisy tracks"],
            same_uploader_limit=2,
            exploration_ratio=0.35,
            evidence_memory_ids=["mem-1"],
            confidence=0.82,
            source="test-projector",
        )
        self.memories = []
        self.trace_id = "profile:user:home:1"


class FakeProjector:
    def __init__(self) -> None:
        self.cleared = []

    def project(self, **_kwargs):
        return FakeProjection()

    def clear_cache(self, user_id=None, scene=None):
        self.cleared.append((user_id, scene))


class FakeBridge:
    enabled = True

    def record_behavior(self, payload):
        return {
            "eventId": payload["event_id"],
            "memoryIds": ["mem-behavior-1"],
        }

    def record_profile_statement(self, *, user_id, description, profile, source):
        assert user_id == "user"
        assert "华语流行乐" in profile.positive_topics
        assert description
        assert source == "test"
        return {
            "eventId": "profile-event-1",
            "memoryIds": ["mem-profile-1", "mem-profile-2"],
        }


def test_amem_grpc_server_methods_over_generated_stub():
    projector = FakeProjector()
    server = build_server(AmemGrpcService(bridge=FakeBridge(), projector=projector))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = amem_pb2_grpc.AmemServiceStub(channel)

            health = stub.Health(amem_pb2.HealthRequest(), timeout=2)
            assert health.status == "serving"

            profile = stub.GetMusicProfile(
                amem_pb2.GetMusicProfileRequest(user_id="user", scene="home"),
                timeout=2,
            )
            assert profile.positive_topics["Vocaloid"] == 0.91
            assert profile.preferred_uploaders["1001"] == 0.8
            assert profile.same_uploader_limit == 2
            assert profile.evidence_memory_ids == ["mem-1"]
            assert profile.source == "test-projector"
            assert profile.positive_interest_texts == ["quiet vocaloid interest"]
            assert profile.negative_interest_texts == ["no noisy tracks"]

            explanation = stub.ExplainRecommendation(
                amem_pb2.ExplainRecommendationRequest(
                    user_id="user",
                    scene="home",
                    candidate_track_ids=["track-1", "track-2"],
                    trace_id="trace-1",
                ),
                timeout=2,
            )
            assert explanation.trace_id == "trace-1"
            assert explanation.evidence_memory_ids == ["mem-1"]
            assert "Vocaloid" in explanation.reasons["track-1"]

            behavior = stub.RecordBehavior(
                amem_pb2.RecordBehaviorRequest(
                    event_id="evt-1",
                    user_id="user",
                    event="play",
                    scene="playback",
                    track_id="BV1:2",
                    payload_json=b'{"event_id":"evt-1","event":"play"}',
                ),
                timeout=2,
            )
            assert behavior.accepted is True
            assert behavior.amem_event_id == "evt-1"
            assert behavior.memory_ids == ["mem-behavior-1"]

            statement = stub.RecordProfileStatement(
                amem_pb2.RecordProfileStatementRequest(
                    user_id="user",
                    scene="home",
                    description="偏爱华语流行乐",
                    profile_json=b'{"positive_topics":{"\xe5\x8d\x8e\xe8\xaf\xad\xe6\xb5\x81\xe8\xa1\x8c\xe4\xb9\x90":0.96}}',
                    source="test",
                ),
                timeout=2,
            )
            assert statement.accepted is True
            assert statement.amem_event_id == "profile-event-1"
            assert statement.memory_ids == ["mem-profile-1", "mem-profile-2"]
            assert projector.cleared == [("user", "home")]
    finally:
        server.stop(0)
