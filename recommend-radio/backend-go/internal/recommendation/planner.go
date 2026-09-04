package recommendation

import "strings"

type SearchIntent struct {
	Query          string   `json:"query"`
	Source         string   `json:"source"`
	Weight         float64  `json:"weight"`
	ProfileSignals []string `json:"profileSignals"`
}

type SearchIntentPlanner struct{}

func NewSearchIntentPlanner() SearchIntentPlanner {
	return SearchIntentPlanner{}
}

func (SearchIntentPlanner) Plan(profile MusicProfile, limit int) []SearchIntent {
	if limit <= 0 {
		limit = 6
	}
	out := []SearchIntent{}
	add := func(query string, source string, weight float64, signals ...string) {
		query = normalizeQuery(query)
		if query == "" || containsIntent(out, query) || len(out) >= limit {
			return
		}
		out = append(out, SearchIntent{
			Query: query, Source: source, Weight: clamp(weight, 0.05, 1),
			ProfileSignals: dedupeStrings(signals, 8),
		})
	}
	for _, intent := range profile.RecentIntents {
		add(intent, "recent_intent", 0.95, intent)
	}
	for _, mood := range topKeys(profile.MoodWeights, 4) {
		for _, topic := range topKeys(profile.PositiveTopics, 4) {
			add(mood+" "+topic+" 歌单", "mood_topic", 0.88, mood, topic)
		}
	}
	for _, topic := range topKeys(profile.PositiveTopics, 6) {
		if strings.Contains(strings.ToLower(topic), "r&b") {
			add(topic+" 慢歌", "positive_topic", 0.82, topic)
			continue
		}
		add(topic+" 音乐", "positive_topic", 0.80, topic)
	}
	for _, mood := range topKeys(profile.MoodWeights, 6) {
		add(mood+" 华语 歌单", "mood", 0.76, mood)
	}
	if len(out) == 0 {
		add("华语流行 音乐", "fallback", 0.40)
		add("治愈 华语 歌单", "fallback", 0.35)
		add("安静 音乐", "fallback", 0.30)
	}
	return out
}

func containsIntent(values []SearchIntent, query string) bool {
	for _, value := range values {
		if value.Query == query {
			return true
		}
	}
	return false
}

func normalizeQuery(value string) string {
	return strings.Join(strings.Fields(strings.TrimSpace(value)), " ")
}
