package recommendation

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"os"
	"strings"
	"time"

	"recommend-radio/backend-go/internal/platform/cache"
)

type Embedder interface {
	Embed(ctx context.Context, texts []string) ([][]float64, error)
	Enabled() bool
}

type EmbeddingClient struct {
	httpClient *http.Client
	cache      cache.JSONCache
	baseURL    string
	apiKey     string
	model      string
	ttl        time.Duration
	enabled    bool
}

func NewEmbeddingClient(cfg Config, jsonCache cache.JSONCache) *EmbeddingClient {
	timeout := cfg.EmbeddingTimeout
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	apiKey := ""
	if cfg.EmbeddingAPIKeyEnv != "" {
		apiKey = strings.TrimSpace(os.Getenv(cfg.EmbeddingAPIKeyEnv))
	}
	enabled := cfg.EmbeddingEnabled && cfg.EmbeddingBaseURL != "" && apiKey != "" && cfg.EmbeddingModel != ""
	if jsonCache == nil {
		jsonCache = cache.NoopJSONCache{}
	}
	return &EmbeddingClient{
		httpClient: &http.Client{Timeout: timeout},
		cache:      jsonCache, baseURL: strings.TrimRight(cfg.EmbeddingBaseURL, "/"),
		apiKey: apiKey, model: cfg.EmbeddingModel, ttl: cfg.EmbeddingCacheTTL,
		enabled: enabled,
	}
}

func (c *EmbeddingClient) Enabled() bool {
	return c != nil && c.enabled
}

func (c *EmbeddingClient) Embed(ctx context.Context, texts []string) ([][]float64, error) {
	out := make([][]float64, len(texts))
	if !c.Enabled() {
		return out, nil
	}
	missingTexts := []string{}
	missingIndexes := []int{}
	for i, text := range texts {
		if strings.TrimSpace(text) == "" {
			continue
		}
		var vector []float64
		err := c.cache.GetJSON(ctx, c.cacheKey(text), &vector)
		if err == nil && len(vector) > 0 {
			out[i] = vector
			continue
		}
		if err != nil && !errors.Is(err, cache.ErrMiss) {
			// Cache failure should not disable recommendations; fetch from provider.
		}
		missingTexts = append(missingTexts, text)
		missingIndexes = append(missingIndexes, i)
	}
	if len(missingTexts) == 0 {
		return out, nil
	}
	vectors, err := c.fetch(ctx, missingTexts)
	if err != nil {
		return out, err
	}
	for i, vector := range vectors {
		if i >= len(missingIndexes) {
			break
		}
		index := missingIndexes[i]
		out[index] = vector
		if len(vector) > 0 && c.ttl > 0 {
			_ = c.cache.SetJSON(ctx, c.cacheKey(texts[index]), vector, c.ttl)
		}
	}
	return out, nil
}

func (c *EmbeddingClient) fetch(ctx context.Context, texts []string) ([][]float64, error) {
	body, err := json.Marshal(map[string]any{"model": c.model, "input": texts})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, errors.New("embedding provider returned non-2xx status")
	}
	var payload struct {
		Data []struct {
			Index     int       `json:"index"`
			Embedding []float64 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, err
	}
	out := make([][]float64, len(texts))
	for position, item := range payload.Data {
		index := item.Index
		if index < 0 || index >= len(out) {
			index = position
		}
		out[index] = item.Embedding
	}
	return out, nil
}

func (c *EmbeddingClient) cacheKey(text string) string {
	sum := sha256.Sum256([]byte(c.model + "\x00" + strings.TrimSpace(text)))
	return "recommend-radio:embedding:" + c.model + ":" + hex.EncodeToString(sum[:])
}

func cosine(a []float64, b []float64) float64 {
	if len(a) == 0 || len(b) == 0 || len(a) != len(b) {
		return 0
	}
	var dot, an, bn float64
	for i := range a {
		dot += a[i] * b[i]
		an += a[i] * a[i]
		bn += b[i] * b[i]
	}
	if an == 0 || bn == 0 {
		return 0
	}
	return clamp(dot/(math.Sqrt(an)*math.Sqrt(bn)), -1, 1)
}
