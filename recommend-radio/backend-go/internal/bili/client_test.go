package bili

import (
	"net/url"
	"testing"
)

func TestTrackIDIncludesCIDWhenPresent(t *testing.T) {
	cid := int64(123)
	if got := MakeTrackID("bvABC", &cid); got != "bili:BVABC:cid:123" {
		t.Fatalf("unexpected track id: %s", got)
	}
	if got := MakeTrackID("BVABC", nil); got != "bili:BVABC" {
		t.Fatalf("unexpected track id without cid: %s", got)
	}
}

func TestSelectAudioStreamHonorsRequestedQuality(t *testing.T) {
	streams := []map[string]any{
		{"id": float64(30216), "bandwidth": float64(64000), "baseUrl": "low"},
		{"id": float64(30280), "bandwidth": float64(192000), "baseUrl": "high"},
	}
	selected := selectAudioStream(streams, "64k")
	if got := stringFromAny(selected["baseUrl"]); got != "low" {
		t.Fatalf("expected 64k stream, got %s", got)
	}
	selected = selectAudioStream(streams, "auto")
	if got := stringFromAny(selected["baseUrl"]); got != "high" {
		t.Fatalf("expected highest stream, got %s", got)
	}
}

func TestNormalizeAudioInfoDoesNotCacheBytes(t *testing.T) {
	info, err := normalizeAudioInfo(map[string]any{
		"timelength": float64(180000),
		"dash": map[string]any{
			"audio": []any{
				map[string]any{
					"id": float64(30280), "bandwidth": float64(192000),
					"baseUrl":   "https://cdn.example/audio.m4s",
					"backupUrl": []any{"https://cdn.example/backup.m4s"},
					"codecs":    "mp4a.40.2",
				},
			},
		},
	}, "192k")
	if err != nil {
		t.Fatal(err)
	}
	if info.URL == "" || len(info.BackupURLs) != 1 {
		t.Fatalf("expected stream URLs only, got %#v", info)
	}
	if info.Duration != 180 || info.Codec != "aac" || info.ActualQuality != "192k" {
		t.Fatalf("unexpected audio normalization: %#v", info)
	}
}

func TestPlayURLUsesLegacyEndpoint(t *testing.T) {
	if playURL != "https://api.bilibili.com/x/player/playurl" {
		t.Fatalf("playurl endpoint must stay on the legacy non-WBI API, got %s", playURL)
	}
}

func TestSignWBIProducesSignature(t *testing.T) {
	params := url.Values{"bvid": {"BV1xx"}, "cid": {"123"}}
	signed := signWBI(params, "0123456789abcdef0123456789abcdef", "abcdef0123456789abcdef0123456789", 100)
	if signed.Get("wts") != "100" {
		t.Fatalf("missing wts: %s", signed.Encode())
	}
	if len(signed.Get("w_rid")) != 32 {
		t.Fatalf("missing md5 signature: %s", signed.Get("w_rid"))
	}
}
