package playback

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"recommend-radio/backend-go/internal/bili"
)

func TestCandidateURLsDedupesPrimaryAndBackups(t *testing.T) {
	urls := candidateURLs(bili.AudioStreamInfo{
		URL:        "https://cdn.example/a.m4s",
		BackupURLs: []string{"https://cdn.example/a.m4s", "https://cdn.example/b.m4s", ""},
	})
	if len(urls) != 2 {
		t.Fatalf("expected two unique urls, got %#v", urls)
	}
}

func TestProxyHeaderAllowedKeepsRangeHeaders(t *testing.T) {
	for _, header := range []string{"Content-Range", "Accept-Ranges", "Content-Length", "ETag"} {
		if !proxyHeaderAllowed(header) {
			t.Fatalf("expected %s to be proxied", header)
		}
	}
	if proxyHeaderAllowed("Set-Cookie") {
		t.Fatal("Set-Cookie must not be proxied from Bilibili CDN")
	}
}

func TestTryOpenUsesBrowserVideoHeadersAndRange(t *testing.T) {
	var gotUserAgent string
	var gotReferer string
	var gotOrigin string
	var gotRange string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUserAgent = r.Header.Get("User-Agent")
		gotReferer = r.Header.Get("Referer")
		gotOrigin = r.Header.Get("Origin")
		gotRange = r.Header.Get("Range")
		w.Header().Set("Content-Range", "bytes 0-3/8")
		w.WriteHeader(http.StatusPartialContent)
		_, _ = w.Write([]byte("test"))
	}))
	defer server.Close()

	service := NewService(nil, 1, time.Minute)
	resp, status, err := service.tryOpen(context.Background(), "BV1abc", bili.AudioStreamInfo{URL: server.URL}, "bytes=0-3")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if status != http.StatusPartialContent {
		t.Fatalf("expected 206, got %d", status)
	}
	if !strings.Contains(gotUserAgent, "Chrome/") {
		t.Fatalf("expected browser user-agent, got %q", gotUserAgent)
	}
	if gotReferer != "https://www.bilibili.com/video/BV1abc" {
		t.Fatalf("unexpected referer: %q", gotReferer)
	}
	if gotOrigin != "https://www.bilibili.com" {
		t.Fatalf("unexpected origin: %q", gotOrigin)
	}
	if gotRange != "bytes=0-3" {
		t.Fatalf("range was not forwarded: %q", gotRange)
	}
}
