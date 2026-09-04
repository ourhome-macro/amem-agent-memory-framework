package recommendation

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"golang.org/x/sync/errgroup"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"recommend-radio/backend-go/internal/amem"
	"recommend-radio/backend-go/internal/bili"
	"recommend-radio/backend-go/internal/library"
	"recommend-radio/backend-go/internal/platform/cache"
	"recommend-radio/backend-go/internal/platform/model"
)

type Service struct {
	db        *gorm.DB
	bili      bili.Gateway
	library   *library.Service
	amem      amem.Client
	cfg       Config
	planner   SearchIntentPlanner
	enricher  CandidateEnricher
	prefilter EmbeddingPrefilter
	router    CandidateRouter
	ranker    MMRRanker
}

func NewService(db *gorm.DB, gateway bili.Gateway, libraryService *library.Service, amemClient amem.Client) *Service {
	return NewServiceWithConfig(db, gateway, libraryService, amemClient, DefaultConfig(), cache.NoopJSONCache{}, nil)
}

func NewServiceWithConfig(db *gorm.DB, gateway bili.Gateway, libraryService *library.Service, amemClient amem.Client, cfg Config, jsonCache cache.JSONCache, embedder Embedder) *Service {
	if amemClient == nil {
		amemClient = amem.NoopClient{}
	}
	defaults := DefaultConfig()
	if cfg.EmbeddingTimeout == 0 {
		cfg.EmbeddingTimeout = defaults.EmbeddingTimeout
	}
	if cfg.EmbeddingCacheTTL == 0 {
		cfg.EmbeddingCacheTTL = defaults.EmbeddingCacheTTL
	}
	if cfg.PrefilterMode == "" {
		cfg.PrefilterMode = defaults.PrefilterMode
	}
	if cfg.PositivePassThreshold == 0 {
		cfg.PositivePassThreshold = defaults.PositivePassThreshold
	}
	if cfg.NegativePassMax == 0 {
		cfg.NegativePassMax = defaults.NegativePassMax
	}
	if cfg.NegativeRejectThreshold == 0 {
		cfg.NegativeRejectThreshold = defaults.NegativeRejectThreshold
	}
	if embedder == nil {
		embedder = NewEmbeddingClient(cfg, jsonCache)
	}
	return &Service{
		db: db, bili: gateway, library: libraryService, amem: amemClient, cfg: cfg,
		planner: NewSearchIntentPlanner(), enricher: NewCandidateEnricher(),
		prefilter: NewEmbeddingPrefilter(embedder, cfg), router: NewCandidateRouter(cfg),
		ranker: NewMMRRanker(),
	}
}

func (s *Service) List(ctx context.Context, userID string, scene string, limit int) (map[string]any, error) {
	if scene == "" {
		scene = "home"
	}
	if limit <= 0 || limit > 50 {
		limit = 8
	}
	profileMap, _ := s.amem.MusicProfile(ctx, userID, "music_recommendation")
	profile := ProfileFromMap(profileMap)
	intents := s.planner.Plan(profile, 6)
	candidates, searchTrace, err := s.searchCandidates(ctx, intents, limit*4)
	if err != nil {
		return nil, err
	}
	prefilterDecisions := s.prefilter.Evaluate(ctx, profile, candidates)
	routes := make([]CandidateRoute, len(candidates))
	routed := make([]RankedCandidate, 0, len(candidates))
	for i, candidate := range candidates {
		decision := PrefilterDecision{Mode: s.cfg.PrefilterMode, Passed: true}
		if i < len(prefilterDecisions) {
			decision = prefilterDecisions[i]
		}
		route := s.router.Route(profile, candidate, decision)
		routes[i] = route
		if strings.EqualFold(s.cfg.PrefilterMode, "enforce") && route.Decision == DecisionReject {
			continue
		}
		routed = append(routed, RankedCandidate{
			Candidate: candidate, Prefilter: decision, Route: route, FinalScore: route.Score.Total,
		})
	}
	ranked := s.ranker.Rank(routed, limit)
	items := make([]map[string]any, 0, min(limit, len(ranked)))
	now := time.Now().UTC()
	traceID := fmt.Sprintf("recommend:%s:%s:%d", userID, scene, time.Now().UnixMilli())
	s.recordCandidateTelemetry(ctx, traceID, userID, scene, candidates, prefilterDecisions, routes, ranked, now)
	for _, rankedCandidate := range ranked {
		track := rankedCandidate.Candidate.Track
		_ = s.library.UpsertTrack(ctx, nil, track)
		item := map[string]any{
			"track": track, "score": rankedCandidate.FinalScore, "source": "agent_search",
			"reason":         rankedCandidate.Route.Reason,
			"profileSignals": append([]string{}, profile.PositiveInterestTexts...),
			"sourceQuery":    rankedCandidate.Candidate.SourceQuery,
		}
		items = append(items, item)
		_ = s.db.WithContext(ctx).Clauses(clause.OnConflict{DoNothing: true}).Create(&model.RecommendationEvent{
			UserID: userID, TrackID: track.TrackID, Event: "shown", Scene: scene,
			Source: "agent_search", Reason: rankedCandidate.Route.Reason,
			Score: rankedCandidate.FinalScore, CreatedAt: now,
		}).Error
		_ = s.db.WithContext(ctx).Create(&model.RecommendationHistory{
			UserID: userID, TrackID: track.TrackID, RecommendedAt: now, Scene: scene,
			Source: "agent_search", Score: rankedCandidate.FinalScore,
			Reason: rankedCandidate.Route.Reason,
		}).Error
	}
	tracePayload, _ := json.Marshal(map[string]any{
		"musicProfile":       profile.ToMap(),
		"searchIntents":      intents,
		"rawCandidateCount":  len(candidates),
		"agent":              searchTrace,
		"enrichedCandidates": candidates,
		"embeddingPrefilter": prefilterDecisions,
		"candidateRouter":    routes,
		"candidateScores":    candidateScores(routes),
		"mmrRanking":         ranked,
		"finalResults":       items,
	})
	_ = s.db.WithContext(ctx).Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "trace_id"}},
		DoUpdates: clause.AssignmentColumns([]string{"payload_json", "created_at"}),
	}).Create(&model.RecommendationTrace{
		TraceID: traceID, UserID: userID, Scene: scene, PayloadJSON: tracePayload, CreatedAt: now,
	}).Error
	return map[string]any{
		"scene": scene, "items": items, "profile": profile.ToMap(), "debugTraceId": traceID,
		"telemetry": map[string]any{
			"searchIntents": intents, "rawCandidateCount": len(candidates),
			"prefilterDecisions": prefilterDecisions,
		},
	}, nil
}

func (s *Service) recordCandidateTelemetry(ctx context.Context, traceID string, userID string, scene string, candidates []Candidate, decisions []PrefilterDecision, routes []CandidateRoute, ranked []RankedCandidate, now time.Time) {
	rankedIDs := map[string]bool{}
	for _, item := range ranked {
		rankedIDs[item.Candidate.Track.TrackID] = true
	}
	for i, candidate := range candidates {
		decision := PrefilterDecision{}
		if i < len(decisions) {
			decision = decisions[i]
		}
		route := CandidateRoute{}
		if i < len(routes) {
			route = routes[i]
		}
		payload, _ := json.Marshal(map[string]any{
			"candidate": candidate, "prefilter": decision, "route": route,
		})
		_ = s.db.WithContext(ctx).Create(&model.RecommendationCandidate{
			TraceID: traceID, UserID: userID, Scene: scene, TrackID: candidate.Track.TrackID,
			SourceQuery: candidate.SourceQuery, SourceRelevance: candidate.SourceRelevance,
			PositiveSimilarity: decision.PositiveSimilarity, NegativeSimilarity: decision.NegativeSimilarity,
			RouterDecision: route.Decision, FinalScore: route.Score.Total,
			PayloadJSON: payload, CreatedAt: now,
		}).Error
	}
	stats := map[string]struct {
		searched    int64
		passed      int64
		recommended int64
		source      string
	}{}
	for i, candidate := range candidates {
		value := stats[candidate.SourceQuery]
		value.searched++
		value.source = "agent_search"
		passed := true
		if i < len(routes) && routes[i].Decision == DecisionReject {
			passed = false
		}
		if passed {
			value.passed++
		}
		if rankedIDs[candidate.Track.TrackID] {
			value.recommended++
		}
		stats[candidate.SourceQuery] = value
	}
	for query, stat := range stats {
		if strings.TrimSpace(query) == "" {
			continue
		}
		row := model.SearchIntentStat{
			Query: query, Scene: scene, Source: stat.source,
			SearchedCount: stat.searched, PrefilterPassed: stat.passed,
			RecommendedCount: stat.recommended, UpdatedAt: now,
		}
		_ = s.db.WithContext(ctx).Clauses(clause.OnConflict{
			Columns: []clause.Column{{Name: "query"}, {Name: "scene"}, {Name: "source"}},
			DoUpdates: clause.Assignments(map[string]any{
				"searched_count":    gorm.Expr("searched_count + ?", stat.searched),
				"prefilter_passed":  gorm.Expr("prefilter_passed + ?", stat.passed),
				"recommended_count": gorm.Expr("recommended_count + ?", stat.recommended),
				"updated_at":        now,
			}),
		}).Create(&row).Error
	}
}

func (s *Service) LatestTrace(ctx context.Context, userID string, scene string) (map[string]any, error) {
	var trace model.RecommendationTrace
	err := s.db.WithContext(ctx).Where("user_id = ? AND scene = ?", userID, scene).Order("created_at DESC").First(&trace).Error
	if err != nil {
		return map[string]any{"traceId": nil, "scene": scene, "available": false, "message": "No recommendation trace has been recorded for this scene."}, nil
	}
	var payload map[string]any
	_ = json.Unmarshal(trace.PayloadJSON, &payload)
	payload["traceId"] = trace.TraceID
	payload["scene"] = trace.Scene
	payload["available"] = true
	payload["createdAt"] = trace.CreatedAt
	return payload, nil
}

func (s *Service) RecordEvent(ctx context.Context, userID string, payload map[string]any) (map[string]any, error) {
	trackID := strings.TrimSpace(stringFromAny(payload["trackId"]))
	event := strings.TrimSpace(strings.ToLower(stringFromAny(payload["event"])))
	if event == "shown" {
		event = "recommendation.exposed"
	}
	if event == "clicked" || event == "accepted" {
		event = "recommendation.clicked"
	}
	now := time.Now().UTC()
	if trackID != "" && s.trackExists(ctx, trackID) {
		if err := s.db.WithContext(ctx).Create(&model.RecommendationEvent{
			UserID: userID, TrackID: trackID, Event: event, Scene: stringFromAny(payload["scene"]),
			Source: stringFromAny(payload["source"]), Reason: stringFromAny(payload["reason"]),
			Score: floatFromAny(payload["score"]), CreatedAt: now,
		}).Error; err != nil {
			return nil, err
		}
	}
	return map[string]any{"event": event, "accepted": true}, nil
}

func (s *Service) trackExists(ctx context.Context, trackID string) bool {
	var count int64
	err := s.db.WithContext(ctx).Model(&model.Track{}).Where("track_id = ?", trackID).Count(&count).Error
	return err == nil && count > 0
}

func (s *Service) searchCandidates(ctx context.Context, intents []SearchIntent, limit int) ([]Candidate, map[string]any, error) {
	if len(intents) == 0 {
		return nil, map[string]any{"searchIntents": []SearchIntent{}, "agentCandidateCount": 0}, nil
	}
	group, groupCtx := errgroup.WithContext(ctx)
	results := make(chan intentSearchResult, len(intents))
	for _, intent := range intents {
		intent := intent
		group.Go(func() error {
			tracks, err := s.bili.Search(groupCtx, intent.Query, 1, min(max((limit/len(intents))*2, 8), 20))
			if err != nil {
				return nil
			}
			select {
			case results <- intentSearchResult{Intent: intent, Tracks: tracks}:
				return nil
			case <-groupCtx.Done():
				return groupCtx.Err()
			}
		})
	}
	if err := group.Wait(); err != nil {
		return nil, nil, err
	}
	close(results)
	seen := map[string]bool{}
	out := make([]Candidate, 0, limit)
	queryYields := []map[string]any{}
	for result := range results {
		queryYields = append(queryYields, map[string]any{
			"query": result.Intent.Query, "searchedCount": len(result.Tracks),
		})
		for rank, track := range result.Tracks {
			if track.TrackID == "" || seen[track.TrackID] {
				continue
			}
			seen[track.TrackID] = true
			out = append(out, s.enricher.Enrich(track, result.Intent, rank+1))
			if len(out) >= limit {
				break
			}
		}
	}
	return out, map[string]any{"searchIntents": intents, "queryYields": queryYields, "agentCandidateCount": len(out)}, nil
}

type intentSearchResult struct {
	Intent SearchIntent
	Tracks []model.Track
}

func candidateScores(routes []CandidateRoute) []CandidateScore {
	out := make([]CandidateScore, 0, len(routes))
	for _, route := range routes {
		out = append(out, route.Score)
	}
	return out
}

func stringFromAny(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func floatFromAny(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case float32:
		return float64(typed)
	case int:
		return float64(typed)
	case int32:
		return float64(typed)
	case int64:
		return float64(typed)
	default:
		return 0
	}
}

func intFromAny(value any) int {
	switch typed := value.(type) {
	case int:
		return typed
	case int32:
		return int(typed)
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	default:
		return 0
	}
}

func min(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}
