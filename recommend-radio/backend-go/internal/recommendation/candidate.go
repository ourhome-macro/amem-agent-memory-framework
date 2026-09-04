package recommendation

import (
	"fmt"
	"strings"

	"recommend-radio/backend-go/internal/platform/model"
)

type Candidate struct {
	Track           model.Track    `json:"track"`
	SourceQuery     string         `json:"sourceQuery"`
	SourceWeight    float64        `json:"sourceWeight"`
	SourceRank      int            `json:"sourceRank"`
	SourceRelevance float64        `json:"sourceRelevance"`
	Text            string         `json:"text"`
	Enrichment      map[string]any `json:"enrichment"`
}

type CandidateEnricher struct{}

func NewCandidateEnricher() CandidateEnricher {
	return CandidateEnricher{}
}

func (CandidateEnricher) Enrich(track model.Track, intent SearchIntent, rank int) Candidate {
	textParts := []string{
		"标题: " + track.Title,
		"UP主: " + track.Owner,
	}
	if track.PageTitle != nil && strings.TrimSpace(*track.PageTitle) != "" {
		textParts = append(textParts, "分P标题: "+strings.TrimSpace(*track.PageTitle))
	}
	if track.Duration > 0 {
		textParts = append(textParts, fmt.Sprintf("时长: %d秒", track.Duration))
	}
	if intent.Query != "" {
		textParts = append(textParts, "来源搜索词: "+intent.Query)
	}
	sourceRelevance := intent.Weight
	if rank > 0 {
		sourceRelevance = sourceRelevance * (1.0 / (1.0 + float64(rank-1)*0.08))
	}
	return Candidate{
		Track: track, SourceQuery: intent.Query, SourceWeight: intent.Weight,
		SourceRank: rank, SourceRelevance: clamp(sourceRelevance, 0, 1),
		Text: strings.Join(nonEmpty(textParts), "\n"),
		Enrichment: map[string]any{
			"title": track.Title, "owner": track.Owner, "duration": track.Duration,
			"sourceQuery": intent.Query, "sourceRank": rank,
		},
	}
}

func nonEmpty(values []string) []string {
	out := []string{}
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			out = append(out, value)
		}
	}
	return out
}
