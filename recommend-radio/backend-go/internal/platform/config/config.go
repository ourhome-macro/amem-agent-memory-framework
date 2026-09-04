package config

import (
	"log/slog"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr                          string
	MySQLDSN                          string
	RedisAddr                         string
	RedisPassword                     string
	RedisDB                           int
	BiliTimeout                       time.Duration
	BiliCachePrefix                   string
	MaxMediaStreams                   int64
	StreamTimeout                     time.Duration
	OutboxWorkers                     int
	OutboxTopic                       string
	RocketMQEndpoint                  string
	RocketMQEnabled                   bool
	RocketMQConsumersEnabled          bool
	ConsumerWorkers                   int
	ConsumerDLQThreshold              int
	AMEMTransport                     string
	AMEMBaseURL                       string
	AMEMGRPCAddr                      string
	AMEMTimeout                       time.Duration
	RecommendEmbeddingEnabled         bool
	RecommendEmbeddingBaseURL         string
	RecommendEmbeddingAPIKeyEnv       string
	RecommendEmbeddingModel           string
	RecommendEmbeddingDimensions      int
	RecommendEmbeddingTimeout         time.Duration
	RecommendEmbeddingCacheTTL        time.Duration
	RecommendPrefilterMode            string
	RecommendPrefilterPositivePass    float64
	RecommendPrefilterPositiveReject  float64
	RecommendPrefilterNegativeReject  float64
	RecommendPrefilterNegativePassMax float64
	RecommendLLMEvaluatorEnabled      bool
	ShutdownTimeout                   time.Duration
	LogLevel                          slog.Level
}

func Load() Config {
	return Config{
		HTTPAddr:                          env("GO_BACKEND_ADDR", ":5000"),
		MySQLDSN:                          env("MYSQL_DSN", "radio:radio@tcp(127.0.0.1:3306)/bili_radio?charset=utf8mb4&parseTime=true&loc=Local"),
		RedisAddr:                         env("REDIS_ADDR", "127.0.0.1:6379"),
		RedisPassword:                     env("REDIS_PASSWORD", ""),
		RedisDB:                           envInt("REDIS_DB", 0),
		BiliTimeout:                       envDuration("BILI_TIMEOUT", 10*time.Second),
		BiliCachePrefix:                   env("BILI_CACHE_PREFIX", "recommend-radio:bili"),
		MaxMediaStreams:                   int64(envInt("MAX_MEDIA_STREAMS", 32)),
		StreamTimeout:                     envDuration("STREAM_TIMEOUT", 5*time.Minute),
		OutboxWorkers:                     envInt("OUTBOX_WORKERS", 4),
		OutboxTopic:                       env("OUTBOX_TOPIC", "radio_behavior_v1"),
		RocketMQEndpoint:                  env("ROCKETMQ_ENDPOINT", "127.0.0.1:8081"),
		RocketMQEnabled:                   envBool("ROCKETMQ_ENABLED", false),
		RocketMQConsumersEnabled:          envBool("ROCKETMQ_CONSUMERS_ENABLED", false),
		ConsumerWorkers:                   envInt("MQ_CONSUMER_WORKERS", 4),
		ConsumerDLQThreshold:              envInt("MQ_CONSUMER_DLQ_THRESHOLD", 16),
		AMEMTransport:                     strings.ToLower(env("AMEM_TRANSPORT", "noop")),
		AMEMBaseURL:                       strings.TrimRight(env("AMEM_BASE_URL", ""), "/"),
		AMEMGRPCAddr:                      env("AMEM_GRPC_ADDR", ""),
		AMEMTimeout:                       envDuration("AMEM_TIMEOUT", 2*time.Second),
		RecommendEmbeddingEnabled:         envBool("RECOMMEND_EMBEDDING_ENABLED", true),
		RecommendEmbeddingBaseURL:         strings.TrimRight(env("RECOMMEND_EMBEDDING_BASE_URL", ""), "/"),
		RecommendEmbeddingAPIKeyEnv:       env("RECOMMEND_EMBEDDING_API_KEY_ENV", "BGE_M3_API_KEY"),
		RecommendEmbeddingModel:           env("RECOMMEND_EMBEDDING_MODEL", "bge-m3"),
		RecommendEmbeddingDimensions:      envInt("RECOMMEND_EMBEDDING_DIMENSIONS", 1024),
		RecommendEmbeddingTimeout:         envDuration("RECOMMEND_EMBEDDING_TIMEOUT", 8*time.Second),
		RecommendEmbeddingCacheTTL:        envDuration("RECOMMEND_EMBEDDING_CACHE_TTL", 168*time.Hour),
		RecommendPrefilterMode:            strings.ToLower(env("RECOMMEND_PREFILTER_MODE", "shadow")),
		RecommendPrefilterPositivePass:    envFloat("RECOMMEND_PREFILTER_POSITIVE_PASS", 0.70),
		RecommendPrefilterPositiveReject:  envFloat("RECOMMEND_PREFILTER_POSITIVE_REJECT", 0.20),
		RecommendPrefilterNegativeReject:  envFloat("RECOMMEND_PREFILTER_NEGATIVE_REJECT", 0.65),
		RecommendPrefilterNegativePassMax: envFloat("RECOMMEND_PREFILTER_NEGATIVE_PASS_MAX", 0.30),
		RecommendLLMEvaluatorEnabled:      envBool("RECOMMEND_LLM_EVALUATOR_ENABLED", false),
		ShutdownTimeout:                   envDuration("SHUTDOWN_TIMEOUT", 10*time.Second),
		LogLevel:                          envLogLevel("LOG_LEVEL", slog.LevelInfo),
	}
}

func env(key string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func envInt(key string, fallback int) int {
	value, err := strconv.Atoi(env(key, ""))
	if err != nil {
		return fallback
	}
	return value
}

func envBool(key string, fallback bool) bool {
	switch strings.ToLower(env(key, "")) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func envFloat(key string, fallback float64) float64 {
	value, err := strconv.ParseFloat(env(key, ""), 64)
	if err != nil {
		return fallback
	}
	return value
}

func envDuration(key string, fallback time.Duration) time.Duration {
	raw := env(key, "")
	if raw == "" {
		return fallback
	}
	if duration, err := time.ParseDuration(raw); err == nil {
		return duration
	}
	if seconds, err := strconv.Atoi(raw); err == nil {
		return time.Duration(seconds) * time.Second
	}
	return fallback
}

func envLogLevel(key string, fallback slog.Level) slog.Level {
	switch strings.ToLower(env(key, "")) {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	case "info":
		return slog.LevelInfo
	default:
		return fallback
	}
}
