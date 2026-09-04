package auth

import (
	"net/http"
	"testing"
)

func TestStatusFromBiliCode(t *testing.T) {
	tests := map[int]string{
		0:     "confirmed",
		86038: "expired",
		86090: "scanned",
		86101: "waiting",
		99999: "unknown",
	}
	for code, want := range tests {
		if got := statusFromBiliCode(code); got != want {
			t.Fatalf("statusFromBiliCode(%d)=%q, want %q", code, got, want)
		}
	}
}

func TestCookieHeaderFromResponse(t *testing.T) {
	resp := &http.Response{Header: http.Header{}}
	resp.Header.Add("Set-Cookie", "SESSDATA=session; Path=/; Domain=.bilibili.com")
	resp.Header.Add("Set-Cookie", "DedeUserID=123; Path=/; Domain=.bilibili.com")
	resp.Header.Add("Set-Cookie", "bili_jct=csrf; Path=/; Domain=.bilibili.com")

	got := cookieHeaderFromResponse(resp)
	want := "SESSDATA=session; DedeUserID=123; bili_jct=csrf"
	if got != want {
		t.Fatalf("cookieHeaderFromResponse()=%q, want %q", got, want)
	}
}

func TestNormalizeUserProfile(t *testing.T) {
	profile := normalizeUserProfile(map[string]any{
		"mid":   float64(123),
		"uname": "tester",
		"face":  "https://example.test/face.png",
		"level_info": map[string]any{
			"current_level": float64(6),
		},
		"vip": map[string]any{
			"type": float64(2),
		},
	})
	if profile.MID != 123 || profile.Name != "tester" || profile.Face == "" {
		t.Fatalf("unexpected profile: %#v", profile)
	}
	if profile.Level == nil || *profile.Level != 6 {
		t.Fatalf("unexpected level: %#v", profile.Level)
	}
	if profile.VIPType == nil || *profile.VIPType != 2 {
		t.Fatalf("unexpected vip type: %#v", profile.VIPType)
	}
}
