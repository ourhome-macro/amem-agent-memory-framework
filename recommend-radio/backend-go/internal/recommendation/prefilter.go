package recommendation

import (
	"context"
	"strings"
)

type PrefilterDecision struct {
	Mode                     string   `json:"mode"`
	Enabled                  bool     `json:"enabled"`
	PositiveSimilarity       float64  `json:"positiveSimilarity"`
	NegativeSimilarity       float64  `json:"negativeSimilarity"`
	Passed                   bool     `json:"passed"`
	WouldFilter              bool     `json:"wouldFilter"`
	MatchedPositiveInterests []string `json:"matchedPositiveInterests"`
	MatchedNegativeInterests []string `json:"matchedNegativeInterests"`
	Error                    string   `json:"error,omitempty"`
}

type EmbeddingPrefilter struct {
	embedder Embedder
	cfg      Config
}

func NewEmbeddingPrefilter(embedder Embedder, cfg Config) EmbeddingPrefilter {
	return EmbeddingPrefilter{embedder: embedder, cfg: cfg}
}

func (p EmbeddingPrefilter) Evaluate(ctx context.Context, profile MusicProfile, candidates []Candidate) []PrefilterDecision {
	decisions := make([]PrefilterDecision, len(candidates))
	mode := strings.ToLower(strings.TrimSpace(p.cfg.PrefilterMode))
	if mode == "" {
		mode = "shadow"
	}
	if p.embedder == nil || !p.embedder.Enabled() {
		for i := range decisions {
			decisions[i] = PrefilterDecision{Mode: mode, Enabled: false, Passed: true}
		}
		return decisions
	}
	positiveTexts := dedupeStrings(profile.PositiveInterestTexts, 12)
	negativeTexts := dedupeStrings(profile.NegativeInterestTexts, 12)
	texts := make([]string, 0, len(positiveTexts)+len(negativeTexts)+len(candidates))
	texts = append(texts, positiveTexts...)
	texts = append(texts, negativeTexts...)
	for _, candidate := range candidates {
		texts = append(texts, candidate.Text)
	}
	vectors, err := p.embedder.Embed(ctx, texts)
	if err != nil {
		for i := range decisions {
			decisions[i] = PrefilterDecision{Mode: mode, Enabled: true, Passed: true, Error: err.Error()}
		}
		return decisions
	}
	positiveVectors := vectors[:len(positiveTexts)]
	negativeVectors := vectors[len(positiveTexts) : len(positiveTexts)+len(negativeTexts)]
	candidateVectors := vectors[len(positiveTexts)+len(negativeTexts):]
	for i, vector := range candidateVectors {
		pos, posMatches := bestSimilarity(vector, positiveVectors, positiveTexts)
		neg, negMatches := bestSimilarity(vector, negativeVectors, negativeTexts)
		wouldFilter := (len(positiveTexts) > 0 && pos < p.cfg.PositiveRejectThreshold) || neg >= p.cfg.NegativeRejectThreshold
		passed := true
		if mode == "enforce" {
			passed = !wouldFilter
		}
		decisions[i] = PrefilterDecision{
			Mode: mode, Enabled: true, PositiveSimilarity: pos, NegativeSimilarity: neg,
			Passed: passed, WouldFilter: wouldFilter,
			MatchedPositiveInterests: posMatches, MatchedNegativeInterests: negMatches,
		}
	}
	return decisions
}

func bestSimilarity(candidate []float64, interests [][]float64, labels []string) (float64, []string) {
	best := -1.0
	matches := []string{}
	for i, interest := range interests {
		score := cosine(candidate, interest)
		if score > best {
			best = score
			matches = matches[:0]
			if i < len(labels) && strings.TrimSpace(labels[i]) != "" {
				matches = append(matches, labels[i])
			}
			continue
		}
		if score == best && i < len(labels) && strings.TrimSpace(labels[i]) != "" {
			matches = append(matches, labels[i])
		}
	}
	if best < 0 {
		return 0, matches
	}
	return best, dedupeStrings(matches, 3)
}
