package recommendation

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"recommend-radio/backend-go/internal/platform/cache"

	"recommend-radio/backend-go/internal/platform/model"
)

type fakeEmbedder struct{}

func (fakeEmbedder) Enabled() bool { return true }

func (fakeEmbedder) Embed(_ context.Context, texts []string) ([][]float64, error) {
	out := make([][]float64, len(texts))
	for i, text := range texts {
		lower := strings.ToLower(text)
		switch {
		case strings.Contains(text, "游戏混剪") || strings.Contains(lower, "edm"):
			out[i] = []float64{0, 1}
		case strings.Contains(text, "安静") || strings.Contains(text, "治愈") || strings.Contains(text, "华语"):
			out[i] = []float64{1, 0}
		default:
			out[i] = []float64{0.4, 0.6}
		}
	}
	return out, nil
}

func testProfile() MusicProfile {
	profile := MusicProfile{
		PositiveTopics: map[string]float64{"华语流行": 0.9},
		NegativeTopics: map[string]float64{"游戏混剪": 0.8, "EDM": 0.7},
		MoodWeights:    map[string]float64{"安静": 0.9, "治愈": 0.8},
		RecentIntents:  []string{"安静 华语 歌单"},
	}
	profile.PositiveInterestTexts = buildPositiveInterestTexts(profile)
	profile.NegativeInterestTexts = buildNegativeInterestTexts(profile)
	return profile
}

func TestSearchIntentPlannerBuildsQuietMandopopQueries(t *testing.T) {
	intents := NewSearchIntentPlanner().Plan(testProfile(), 6)
	queries := []string{}
	for _, intent := range intents {
		queries = append(queries, intent.Query)
	}
	joined := strings.Join(queries, "\n")
	if !strings.Contains(joined, "安静 华语 歌单") {
		t.Fatalf("expected recent intent query, got %v", queries)
	}
	if !strings.Contains(joined, "华语流行") || !strings.Contains(joined, "治愈") {
		t.Fatalf("expected topic and mood queries, got %v", queries)
	}
}

func TestEmbeddingPrefilterSplitsPositiveAndNegativeSignals(t *testing.T) {
	cfg := DefaultConfig()
	cfg.PrefilterMode = "enforce"
	prefilter := NewEmbeddingPrefilter(fakeEmbedder{}, cfg)
	candidates := []Candidate{
		{Track: model.Track{TrackID: "quiet", Title: "夜晚安静华语歌单"}, Text: "标题: 夜晚安静华语歌单"},
		{Track: model.Track{TrackID: "game", Title: "高燃 EDM 游戏混剪"}, Text: "标题: 高燃 EDM 游戏混剪"},
	}
	decisions := prefilter.Evaluate(context.Background(), testProfile(), candidates)

	if !decisions[0].Passed || decisions[0].PositiveSimilarity < 0.9 || decisions[0].NegativeSimilarity > 0.1 {
		t.Fatalf("quiet candidate should pass with high positive similarity: %+v", decisions[0])
	}
	if decisions[1].Passed || decisions[1].NegativeSimilarity < 0.9 {
		t.Fatalf("game candidate should be filtered by negative similarity: %+v", decisions[1])
	}
}

func TestCandidateRouterRejectsHardNegative(t *testing.T) {
	cfg := DefaultConfig()
	route := NewCandidateRouter(cfg).Route(
		testProfile(),
		Candidate{Track: model.Track{TrackID: "game"}, Text: "标题: 高燃 EDM 游戏混剪"},
		PrefilterDecision{Enabled: true, PositiveSimilarity: 0.8, NegativeSimilarity: 0.9},
	)
	if route.Decision != DecisionReject {
		t.Fatalf("expected reject, got %+v", route)
	}
}

func TestMMRRankerPenalizesSameUploader(t *testing.T) {
	ownerMID := int64(42)
	values := []RankedCandidate{
		{Candidate: Candidate{Track: model.Track{TrackID: "a", OwnerMID: &ownerMID, Owner: "same"}, Text: "安静 华语"}, Route: CandidateRoute{Score: CandidateScore{Total: 0.9}}},
		{Candidate: Candidate{Track: model.Track{TrackID: "b", OwnerMID: &ownerMID, Owner: "same"}, Text: "安静 华语"}, Route: CandidateRoute{Score: CandidateScore{Total: 0.88}}},
		{Candidate: Candidate{Track: model.Track{TrackID: "c", Owner: "other"}, Text: "治愈 华语"}, Route: CandidateRoute{Score: CandidateScore{Total: 0.82}}},
	}
	ranked := NewMMRRanker().Rank(values, 2)
	if len(ranked) != 2 {
		t.Fatalf("expected 2 ranked candidates, got %d", len(ranked))
	}
	if ranked[0].Candidate.Track.TrackID != "a" || ranked[1].Candidate.Track.TrackID != "c" {
		t.Fatalf("expected MMR to diversify away from same uploader, got %+v", ranked)
	}
}

func TestEmbeddingClientUsesCache(t *testing.T) {
	t.Setenv("TEST_BGE_KEY", "secret")
	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{{"index": 0, "embedding": []float64{1, 0}}},
		})
	}))
	defer server.Close()
	client := NewEmbeddingClient(Config{
		EmbeddingEnabled: true, EmbeddingBaseURL: server.URL, EmbeddingAPIKeyEnv: "TEST_BGE_KEY",
		EmbeddingModel: "bge-m3", EmbeddingCacheTTL: time.Hour, EmbeddingTimeout: time.Second,
	}, newMemoryCache())

	first, err := client.Embed(context.Background(), []string{"安静 华语"})
	if err != nil || len(first[0]) != 2 {
		t.Fatalf("first embedding failed: vectors=%v err=%v", first, err)
	}
	second, err := client.Embed(context.Background(), []string{"安静 华语"})
	if err != nil || len(second[0]) != 2 {
		t.Fatalf("second embedding failed: vectors=%v err=%v", second, err)
	}
	if atomic.LoadInt32(&calls) != 1 {
		t.Fatalf("expected one provider call, got %d", calls)
	}
}

type memoryCache struct {
	values map[string]any
}

func newMemoryCache() *memoryCache {
	return &memoryCache{values: map[string]any{}}
}

func (c *memoryCache) GetJSON(_ context.Context, key string, target any) error {
	value, ok := c.values[key]
	if !ok {
		return cache.ErrMiss
	}
	payload, _ := json.Marshal(value)
	return json.Unmarshal(payload, target)
}

func (c *memoryCache) SetJSON(_ context.Context, key string, value any, _ time.Duration) error {
	c.values[key] = value
	return nil
}

func (c *memoryCache) Close() error { return nil }
