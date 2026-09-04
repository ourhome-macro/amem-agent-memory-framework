package recommendation

import (
	"sort"
	"strings"
)

type RankedCandidate struct {
	Candidate  Candidate         `json:"candidate"`
	Prefilter  PrefilterDecision `json:"prefilter"`
	Route      CandidateRoute    `json:"route"`
	FinalScore float64           `json:"finalScore"`
}

type MMRRanker struct {
	lambda float64
}

func NewMMRRanker() MMRRanker {
	return MMRRanker{lambda: 0.76}
}

func (r MMRRanker) Rank(values []RankedCandidate, limit int) []RankedCandidate {
	if limit <= 0 || limit > len(values) {
		limit = len(values)
	}
	pool := append([]RankedCandidate{}, values...)
	sort.Slice(pool, func(i, j int) bool {
		if pool[i].Route.Score.Total == pool[j].Route.Score.Total {
			return pool[i].Candidate.Track.TrackID < pool[j].Candidate.Track.TrackID
		}
		return pool[i].Route.Score.Total > pool[j].Route.Score.Total
	})
	selected := []RankedCandidate{}
	for len(pool) > 0 && len(selected) < limit {
		bestIndex := 0
		bestScore := -2.0
		for i, item := range pool {
			diversityPenalty := maxSimilarity(item, selected)
			mmrScore := r.lambda*item.Route.Score.Total - (1-r.lambda)*diversityPenalty
			if mmrScore > bestScore {
				bestScore = mmrScore
				bestIndex = i
			}
		}
		item := pool[bestIndex]
		item.Route.Score.DiversityPenalty = maxSimilarity(item, selected)
		item.FinalScore = clamp(bestScore, -1, 1)
		selected = append(selected, item)
		pool = append(pool[:bestIndex], pool[bestIndex+1:]...)
	}
	return selected
}

func maxSimilarity(item RankedCandidate, selected []RankedCandidate) float64 {
	best := 0.0
	for _, existing := range selected {
		if item.Candidate.Track.OwnerMID != nil && existing.Candidate.Track.OwnerMID != nil && *item.Candidate.Track.OwnerMID == *existing.Candidate.Track.OwnerMID {
			best = maxFloat(best, 0.85)
		}
		if strings.EqualFold(item.Candidate.Track.Owner, existing.Candidate.Track.Owner) && item.Candidate.Track.Owner != "" {
			best = maxFloat(best, 0.75)
		}
		best = maxFloat(best, jaccard(item.Candidate.Text, existing.Candidate.Text))
	}
	return best
}

func jaccard(a string, b string) float64 {
	left := tokenSet(a)
	right := tokenSet(b)
	if len(left) == 0 || len(right) == 0 {
		return 0
	}
	intersect := 0
	for token := range left {
		if right[token] {
			intersect++
		}
	}
	union := len(left) + len(right) - intersect
	if union == 0 {
		return 0
	}
	return float64(intersect) / float64(union)
}

func tokenSet(value string) map[string]bool {
	fields := strings.Fields(strings.ToLower(value))
	out := map[string]bool{}
	for _, field := range fields {
		field = strings.Trim(field, "：:，,。.!?;；/\\[](){}")
		if len([]rune(field)) >= 2 {
			out[field] = true
		}
	}
	return out
}

func maxFloat(a float64, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
