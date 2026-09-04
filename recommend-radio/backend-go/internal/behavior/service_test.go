package behavior

import "testing"

func TestNormalizeEventTypeMapsFrontendEvents(t *testing.T) {
	cases := map[string]string{
		"shown":    "recommendation.exposed",
		"clicked":  "recommendation.clicked",
		"played":   "play",
		"skipped":  "skip",
		"complete": "complete",
	}
	for input, want := range cases {
		if got := normalizeEventType(input); got != want {
			t.Fatalf("%s => %s, want %s", input, got, want)
		}
	}
}
