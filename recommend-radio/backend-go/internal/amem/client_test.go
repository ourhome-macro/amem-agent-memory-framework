package amem

import (
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"

	amemv1 "recommend-radio/backend-go/internal/amem/gen/amemv1"
)

func TestGRPCClientMusicProfile(t *testing.T) {
	client, cleanup := newTestGRPCClient(t, amemv1.MusicProfileResponse{
		PositiveTopics:     map[string]float64{"vocaloid": 0.9},
		PreferredUploaders: map[string]float64{"123": 0.8},
		RecentIntents:      []string{"fresh"},
		SameUploaderLimit:  3,
		ExplorationRatio:   0.25,
		EvidenceMemoryIds:  []string{"mem-1"},
		Confidence:         0.77,
		Source:             "amem-grpc",
	})
	defer cleanup()

	profile, err := client.MusicProfile(context.Background(), "user-1", "home")
	if err != nil {
		t.Fatalf("MusicProfile returned error: %v", err)
	}
	if profile["source"] != "amem-grpc" {
		t.Fatalf("source mismatch: %v", profile["source"])
	}
	if profile["same_uploader_limit"] != 3 {
		t.Fatalf("same_uploader_limit mismatch: %v", profile["same_uploader_limit"])
	}
	topics := profile["positive_topics"].(map[string]float64)
	if topics["vocaloid"] != 0.9 {
		t.Fatalf("positive topic mismatch: %v", topics)
	}
}

func TestGRPCClientMusicProfileFallback(t *testing.T) {
	conn, err := grpc.NewClient("127.0.0.1:1", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	client := newGRPCClient(conn, time.Millisecond)
	defer client.Close()

	profile, err := client.MusicProfile(context.Background(), "user-1", "home")
	if err != nil {
		t.Fatalf("fallback should not return error: %v", err)
	}
	if profile["source"] != "grpc_unavailable" {
		t.Fatalf("fallback source mismatch: %v", profile["source"])
	}
}

func TestNewClientTransportSelection(t *testing.T) {
	client, err := NewClient("noop", "", "", time.Second)
	if err != nil {
		t.Fatalf("noop client failed: %v", err)
	}
	if _, ok := client.(NoopClient); !ok {
		t.Fatalf("expected NoopClient, got %T", client)
	}

	client, err = NewClient("http", "http://amem.local/", "", time.Second)
	if err != nil {
		t.Fatalf("http client failed: %v", err)
	}
	if _, ok := client.(*HTTPClient); !ok {
		t.Fatalf("expected HTTPClient, got %T", client)
	}

	if _, err := NewClient("bad", "", "", time.Second); err == nil {
		t.Fatal("expected unsupported transport error")
	}
}

type testAMEMServer struct {
	amemv1.UnimplementedAmemServiceServer
	profile          amemv1.MusicProfileResponse
	behaviorPayload  map[string]any
	statementRequest *amemv1.RecordProfileStatementRequest
}

func (s *testAMEMServer) GetMusicProfile(context.Context, *amemv1.GetMusicProfileRequest) (*amemv1.MusicProfileResponse, error) {
	return &s.profile, nil
}

func (s *testAMEMServer) RecordBehavior(_ context.Context, req *amemv1.RecordBehaviorRequest) (*amemv1.RecordBehaviorResponse, error) {
	_ = json.Unmarshal(req.PayloadJson, &s.behaviorPayload)
	return &amemv1.RecordBehaviorResponse{Accepted: true, AmemEventId: req.EventId, MemoryIds: []string{"mem-1"}}, nil
}

func (s *testAMEMServer) RecordProfileStatement(_ context.Context, req *amemv1.RecordProfileStatementRequest) (*amemv1.RecordProfileStatementResponse, error) {
	s.statementRequest = req
	return &amemv1.RecordProfileStatementResponse{Accepted: true, AmemEventId: "profile-event-1", MemoryIds: []string{"mem-profile-1"}}, nil
}

func TestGRPCClientRecordBehavior(t *testing.T) {
	server := &testAMEMServer{}
	client, cleanup := newTestGRPCClientWithServer(t, server)
	defer cleanup()

	err := client.RecordBehavior(context.Background(), map[string]any{
		"event_id": "evt-1", "userId": "user-1", "event": "play", "scene": "playback", "trackId": "BV1:2",
	})
	if err != nil {
		t.Fatalf("RecordBehavior returned error: %v", err)
	}
	if server.behaviorPayload["event_id"] != "evt-1" {
		t.Fatalf("payload was not forwarded: %v", server.behaviorPayload)
	}
}

func TestGRPCClientRecordProfileStatement(t *testing.T) {
	server := &testAMEMServer{}
	client, cleanup := newTestGRPCClientWithServer(t, server)
	defer cleanup()

	result, err := client.RecordProfileStatement(context.Background(), "user-1", "home", "偏爱华语流行乐", map[string]any{
		"positive_topics": map[string]float64{"华语流行乐": 0.96},
	}, "test")
	if err != nil {
		t.Fatalf("RecordProfileStatement returned error: %v", err)
	}
	if result["eventId"] != "profile-event-1" {
		t.Fatalf("event id mismatch: %v", result)
	}
	if server.statementRequest == nil || server.statementRequest.UserId != "user-1" {
		t.Fatalf("statement request was not forwarded: %#v", server.statementRequest)
	}
	var profile map[string]any
	if err := json.Unmarshal(server.statementRequest.ProfileJson, &profile); err != nil {
		t.Fatalf("profile json invalid: %v", err)
	}
	if _, ok := profile["positive_topics"]; !ok {
		t.Fatalf("profile was not forwarded: %v", profile)
	}
}

func newTestGRPCClient(t *testing.T, profile amemv1.MusicProfileResponse) (*GRPCClient, func()) {
	return newTestGRPCClientWithServer(t, &testAMEMServer{profile: profile})
}

func newTestGRPCClientWithServer(t *testing.T, amemServer *testAMEMServer) (*GRPCClient, func()) {
	t.Helper()

	listener := bufconn.Listen(1024 * 1024)
	server := grpc.NewServer()
	amemv1.RegisterAmemServiceServer(server, amemServer)
	go func() {
		_ = server.Serve(listener)
	}()

	ctx := context.Background()
	conn, err := grpc.DialContext(ctx, "bufnet",
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) {
			return listener.Dial()
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("DialContext failed: %v", err)
	}
	client := newGRPCClient(conn, time.Second)
	return client, func() {
		_ = client.Close()
		server.Stop()
		_ = listener.Close()
	}
}
