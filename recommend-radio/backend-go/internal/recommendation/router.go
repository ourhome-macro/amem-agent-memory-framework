package recommendation

import (
	"strings"
)

const (
	DecisionPass   = "PASS"
	DecisionReject = "REJECT"
	DecisionBorder = "BORDER"
)

type CandidateScore struct {
	SourceRelevance  float64 `json:"sourceRelevance"`
	Semantic         float64 `json:"semantic"`
	Profile          float64 `json:"profile"`
	NegativePenalty  float64 `json:"negativePenalty"`
	DiversityPenalty float64 `json:"diversityPenalty"`
	Total            float64 `json:"total"`
}

type CandidateRoute struct {
	Decision      string         `json:"decision"`
	Reason        string         `json:"reason"`
	LLMEvaluation map[string]any `json:"llmEvaluation,omitempty"`
	Score         CandidateScore `json:"score"`
}

type CandidateRouter struct {
	cfg Config
}

func NewCandidateRouter(cfg Config) CandidateRouter {
	return CandidateRouter{cfg: cfg}
}

func (r CandidateRouter) Route(profile MusicProfile, candidate Candidate, decision PrefilterDecision) CandidateRoute {
	if reason := hardNegativeReason(profile, candidate); reason != "" {
		return CandidateRoute{Decision: DecisionReject, Reason: reason, Score: scoreCandidate(candidate, decision, -1)}
	}
	if decision.Enabled {
		if decision.NegativeSimilarity >= r.cfg.NegativeRejectThreshold {
			return CandidateRoute{Decision: DecisionReject, Reason: "negative semantic similarity is too high", Score: scoreCandidate(candidate, decision, 0)}
		}
		if len(profile.PositiveInterestTexts) > 0 && decision.PositiveSimilarity < r.cfg.PositiveRejectThreshold {
			return CandidateRoute{Decision: DecisionReject, Reason: "positive semantic similarity is too low", Score: scoreCandidate(candidate, decision, 0)}
		}
		if decision.PositiveSimilarity >= r.cfg.PositivePassThreshold && decision.NegativeSimilarity < r.cfg.NegativePassMax {
			return CandidateRoute{Decision: DecisionPass, Reason: "positive semantic match passed and negative match stayed low", Score: scoreCandidate(candidate, decision, 0)}
		}
		return CandidateRoute{Decision: DecisionBorder, Reason: "candidate needs borderline evaluation", Score: scoreCandidate(candidate, decision, 0)}
	}
	return CandidateRoute{Decision: DecisionPass, Reason: "embedding prefilter disabled", Score: scoreCandidate(candidate, decision, 0)}
}

func hardNegativeReason(profile MusicProfile, candidate Candidate) string {
	text := strings.ToLower(candidate.Text)
	for _, values := range []map[string]float64{profile.NegativeTopics, profile.AvoidUploaders, profile.BlockedUploaders} {
		for key := range values {
			key = strings.ToLower(strings.TrimSpace(key))
			if key != "" && strings.Contains(text, key) {
				return "hard negative profile term matched: " + key
			}
		}
	}
	return ""
}

func scoreCandidate(candidate Candidate, decision PrefilterDecision, hardPenalty float64) CandidateScore {
	semantic := decision.PositiveSimilarity - decision.NegativeSimilarity
	if !decision.Enabled {
		semantic = 0.35
	}
	profileScore := 0.0
	if len(decision.MatchedPositiveInterests) > 0 {
		profileScore = 0.2
	}
	negativePenalty := decision.NegativeSimilarity
	if hardPenalty < 0 {
		negativePenalty = 1
	}
	total := candidate.SourceRelevance*0.35 + semantic*0.45 + profileScore - negativePenalty*0.25
	if hardPenalty < 0 {
		total = -1
	}
	return CandidateScore{
		SourceRelevance: candidate.SourceRelevance,
		Semantic:        clamp(semantic, -1, 1),
		Profile:         profileScore,
		NegativePenalty: clamp(negativePenalty, 0, 1),
		Total:           clamp(total, -1, 1),
	}
}
