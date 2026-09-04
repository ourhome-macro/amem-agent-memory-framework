package behavior

import "testing"

func TestParseConsumerEventNormalizesAliases(t *testing.T) {
	event, err := parseConsumerEvent([]byte(`{
		"eventId":"evt-1",
		"userId":"user-1",
		"event":"shown",
		"trackId":"BV1:2",
		"scene":"home",
		"source":"agent_search"
	}`))
	if err != nil {
		t.Fatalf("parseConsumerEvent returned error: %v", err)
	}
	if event.EventID != "evt-1" || event.Event != "recommendation.exposed" || event.TrackID != "BV1:2" {
		t.Fatalf("unexpected event: %+v", event)
	}
}

func TestConsumerEventMetricCount(t *testing.T) {
	event := consumerEvent{Payload: map[string]any{"itemCount": float64(8)}}
	if got := event.metricCount(); got != 8 {
		t.Fatalf("metricCount mismatch: %d", got)
	}
	event = consumerEvent{Payload: map[string]any{"items": []any{"a", "b", "c"}}}
	if got := event.metricCount(); got != 3 {
		t.Fatalf("items metricCount mismatch: %d", got)
	}
	event = consumerEvent{Payload: map[string]any{}}
	if got := event.metricCount(); got != 1 {
		t.Fatalf("default metricCount mismatch: %d", got)
	}
}

func TestSecondsFromPayload(t *testing.T) {
	if got := secondsFromPayload(map[string]any{"playedSeconds": float64(42)}); got != 42 {
		t.Fatalf("playedSeconds should already be seconds, got %d", got)
	}
	if got := secondsFromPayload(map[string]any{"listenMs": float64(42000)}); got != 42 {
		t.Fatalf("listenMs should convert to seconds, got %d", got)
	}
}
