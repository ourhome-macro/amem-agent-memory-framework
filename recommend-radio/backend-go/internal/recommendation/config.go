package recommendation

import "time"

type Config struct {
	EmbeddingEnabled        bool
	EmbeddingBaseURL        string
	EmbeddingAPIKeyEnv      string
	EmbeddingModel          string
	EmbeddingDimensions     int
	EmbeddingTimeout        time.Duration
	EmbeddingCacheTTL       time.Duration
	PrefilterMode           string
	PositivePassThreshold   float64
	PositiveRejectThreshold float64
	NegativeRejectThreshold float64
	NegativePassMax         float64
	LLMEvaluatorEnabled     bool
}

func DefaultConfig() Config {
	return Config{
		EmbeddingEnabled: true, EmbeddingModel: "bge-m3", EmbeddingDimensions: 1024,
		EmbeddingTimeout: 8 * time.Second, EmbeddingCacheTTL: 168 * time.Hour,
		PrefilterMode: "shadow", PositivePassThreshold: 0.70,
		PositiveRejectThreshold: 0.20, NegativeRejectThreshold: 0.65,
		NegativePassMax: 0.30, LLMEvaluatorEnabled: false,
	}
}
