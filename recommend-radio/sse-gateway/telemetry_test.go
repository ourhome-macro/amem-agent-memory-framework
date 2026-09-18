package main

import (
	"context"
	"go.opentelemetry.io/otel"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestTelemetryExportsToOTLPTracePath(t *testing.T) {
	paths := make(chan string, 2)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths <- r.URL.Path
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", server.URL)
	previous := otel.GetTracerProvider()
	defer otel.SetTracerProvider(previous)
	shutdown, err := setupTelemetry(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	_, span := otel.Tracer("test").Start(context.Background(), "sse.delivery")
	span.End()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case path := <-paths:
		if path != "/v1/traces" {
			t.Fatalf("unexpected OTLP path %q", path)
		}
	default:
		t.Fatal("no span was exported")
	}
}
