package amem

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	amemv1 "recommend-radio/backend-go/internal/amem/gen/amemv1"
)

type Client interface {
	RecordBehavior(ctx context.Context, payload map[string]any) error
	RecordProfileStatement(ctx context.Context, userID string, scene string, description string, profile map[string]any, source string) (map[string]any, error)
	MusicProfile(ctx context.Context, userID string, scene string) (map[string]any, error)
}

func NewClient(transport string, baseURL string, grpcAddr string, timeout time.Duration) (Client, error) {
	switch strings.ToLower(strings.TrimSpace(transport)) {
	case "", "noop":
		return NoopClient{}, nil
	case "http":
		if baseURL == "" {
			return NoopClient{}, nil
		}
		return NewHTTPClient(baseURL, timeout), nil
	case "grpc":
		if grpcAddr == "" {
			grpcAddr = "127.0.0.1:9090"
		}
		return NewGRPCClient(grpcAddr, timeout)
	default:
		return nil, ErrUnsupportedTransport{Transport: transport}
	}
}

type ErrUnsupportedTransport struct {
	Transport string
}

func (e ErrUnsupportedTransport) Error() string {
	return "unsupported AMEM transport: " + e.Transport
}

type NoopClient struct{}

func (NoopClient) RecordBehavior(context.Context, map[string]any) error { return nil }
func (NoopClient) RecordProfileStatement(context.Context, string, string, string, map[string]any, string) (map[string]any, error) {
	return map[string]any{"accepted": false, "memoryIds": []string{}, "source": "noop"}, nil
}
func (NoopClient) MusicProfile(context.Context, string, string) (map[string]any, error) {
	return map[string]any{
		"positive_topics":         map[string]float64{},
		"negative_topics":         map[string]float64{},
		"preferred_uploaders":     map[string]float64{},
		"avoid_uploaders":         map[string]float64{},
		"blocked_uploaders":       map[string]float64{},
		"mood_weights":            map[string]float64{},
		"recent_intents":          []string{},
		"positive_interest_texts": []string{},
		"negative_interest_texts": []string{},
		"same_uploader_limit":     2,
		"exploration_ratio":       0.35,
		"evidence_memory_ids":     []string{},
		"confidence":              0,
		"source":                  "noop",
	}, nil
}

type HTTPClient struct {
	baseURL string
	client  *http.Client
}

func NewHTTPClient(baseURL string, timeout time.Duration) *HTTPClient {
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	return &HTTPClient{baseURL: strings.TrimRight(baseURL, "/"), client: &http.Client{Timeout: timeout}}
}

func (c *HTTPClient) RecordBehavior(ctx context.Context, payload map[string]any) error {
	if c.baseURL == "" {
		return nil
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/amem/behavior", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (c *HTTPClient) RecordProfileStatement(ctx context.Context, userID string, scene string, description string, profile map[string]any, source string) (map[string]any, error) {
	if c.baseURL == "" {
		return NoopClient{}.RecordProfileStatement(ctx, userID, scene, description, profile, source)
	}
	body, err := json.Marshal(map[string]any{
		"userId": userID, "scene": scene, "description": description, "profile": profile, "source": source,
	})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/amem/profile/music/statement", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func (c *HTTPClient) MusicProfile(ctx context.Context, userID string, scene string) (map[string]any, error) {
	if c.baseURL == "" {
		return NoopClient{}.MusicProfile(ctx, userID, scene)
	}
	values := url.Values{}
	values.Set("userId", userID)
	values.Set("scene", scene)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/api/amem/profile/music?"+values.Encode(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, err
	}
	return payload, nil
}

type GRPCClient struct {
	conn     *grpc.ClientConn
	client   amemv1.AmemServiceClient
	timeout  time.Duration
	fallback NoopClient
}

func NewGRPCClient(addr string, timeout time.Duration) (*GRPCClient, error) {
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return newGRPCClient(conn, timeout), nil
}

func newGRPCClient(conn *grpc.ClientConn, timeout time.Duration) *GRPCClient {
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	return &GRPCClient{
		conn:     conn,
		client:   amemv1.NewAmemServiceClient(conn),
		timeout:  timeout,
		fallback: NoopClient{},
	}
}

func (c *GRPCClient) RecordBehavior(ctx context.Context, payload map[string]any) error {
	if payload == nil {
		payload = map[string]any{}
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	_, err = c.client.RecordBehavior(callCtx, &amemv1.RecordBehaviorRequest{
		EventId:     stringFromMap(payload, "event_id", "eventId"),
		UserId:      stringFromMap(payload, "userId", "user_id"),
		Event:       stringFromMap(payload, "event"),
		Scene:       stringFromMap(payload, "scene"),
		TrackId:     stringFromMap(payload, "trackId", "track_id"),
		PayloadJson: body,
	})
	return err
}

func (c *GRPCClient) RecordProfileStatement(ctx context.Context, userID string, scene string, description string, profile map[string]any, source string) (map[string]any, error) {
	if profile == nil {
		profile = map[string]any{}
	}
	body, err := json.Marshal(profile)
	if err != nil {
		return nil, err
	}
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	resp, err := c.client.RecordProfileStatement(callCtx, &amemv1.RecordProfileStatementRequest{
		UserId:      userID,
		Scene:       scene,
		Description: description,
		ProfileJson: body,
		Source:      source,
	})
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"accepted":    resp.Accepted,
		"eventId":     resp.AmemEventId,
		"memoryIds":   append([]string{}, resp.MemoryIds...),
		"memoryCount": len(resp.MemoryIds),
		"source":      "amem-grpc",
	}, nil
}

func (c *GRPCClient) MusicProfile(ctx context.Context, userID string, scene string) (map[string]any, error) {
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	resp, err := c.client.GetMusicProfile(callCtx, &amemv1.GetMusicProfileRequest{
		UserId: userID,
		Scene:  scene,
	})
	if err != nil {
		profile, fallbackErr := c.fallback.MusicProfile(ctx, userID, scene)
		if fallbackErr != nil {
			return nil, fallbackErr
		}
		profile["source"] = "grpc_unavailable"
		return profile, nil
	}
	return musicProfileToMap(resp), nil
}

func (c *GRPCClient) Health(ctx context.Context) error {
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	_, err := c.client.Health(callCtx, &amemv1.HealthRequest{})
	return err
}

func (c *GRPCClient) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}

func musicProfileToMap(profile *amemv1.MusicProfileResponse) map[string]any {
	if profile == nil {
		return map[string]any{}
	}
	return map[string]any{
		"positive_topics":         copyFloatMap(profile.PositiveTopics),
		"negative_topics":         copyFloatMap(profile.NegativeTopics),
		"preferred_uploaders":     copyFloatMap(profile.PreferredUploaders),
		"avoid_uploaders":         copyFloatMap(profile.AvoidUploaders),
		"blocked_uploaders":       copyFloatMap(profile.BlockedUploaders),
		"mood_weights":            copyFloatMap(profile.MoodWeights),
		"recent_intents":          append([]string{}, profile.RecentIntents...),
		"positive_interest_texts": append([]string{}, profile.PositiveInterestTexts...),
		"negative_interest_texts": append([]string{}, profile.NegativeInterestTexts...),
		"same_uploader_limit":     int(profile.SameUploaderLimit),
		"exploration_ratio":       profile.ExplorationRatio,
		"evidence_memory_ids":     append([]string{}, profile.EvidenceMemoryIds...),
		"confidence":              profile.Confidence,
		"source":                  profile.Source,
	}
}

func copyFloatMap(in map[string]float64) map[string]float64 {
	out := make(map[string]float64, len(in))
	for key, value := range in {
		out[key] = value
	}
	return out
}

func stringFromMap(payload map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := payload[key]; ok && value != nil {
			return strings.TrimSpace(fmt.Sprint(value))
		}
	}
	return ""
}
