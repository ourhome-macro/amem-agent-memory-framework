package recommendation

import (
	"fmt"
	"sort"
	"strings"
)

type MusicProfile struct {
	PositiveTopics        map[string]float64 `json:"positive_topics"`
	NegativeTopics        map[string]float64 `json:"negative_topics"`
	PreferredUploaders    map[string]float64 `json:"preferred_uploaders"`
	AvoidUploaders        map[string]float64 `json:"avoid_uploaders"`
	BlockedUploaders      map[string]float64 `json:"blocked_uploaders"`
	MoodWeights           map[string]float64 `json:"mood_weights"`
	RecentIntents         []string           `json:"recent_intents"`
	PositiveInterestTexts []string           `json:"positive_interest_texts"`
	NegativeInterestTexts []string           `json:"negative_interest_texts"`
	SameUploaderLimit     int                `json:"same_uploader_limit"`
	ExplorationRatio      float64            `json:"exploration_ratio"`
	EvidenceMemoryIDs     []string           `json:"evidence_memory_ids"`
	Confidence            float64            `json:"confidence"`
	Source                string             `json:"source"`
	Raw                   map[string]any     `json:"-"`
}

func ProfileFromMap(raw map[string]any) MusicProfile {
	profile := MusicProfile{
		PositiveTopics:        floatMap(raw["positive_topics"]),
		NegativeTopics:        floatMap(raw["negative_topics"]),
		PreferredUploaders:    floatMap(raw["preferred_uploaders"]),
		AvoidUploaders:        floatMap(raw["avoid_uploaders"]),
		BlockedUploaders:      floatMap(raw["blocked_uploaders"]),
		MoodWeights:           floatMap(raw["mood_weights"]),
		RecentIntents:         stringList(raw["recent_intents"]),
		PositiveInterestTexts: stringList(raw["positive_interest_texts"]),
		NegativeInterestTexts: stringList(raw["negative_interest_texts"]),
		SameUploaderLimit:     intFromAny(raw["same_uploader_limit"]),
		ExplorationRatio:      floatFromAny(raw["exploration_ratio"]),
		EvidenceMemoryIDs:     stringList(raw["evidence_memory_ids"]),
		Confidence:            floatFromAny(raw["confidence"]),
		Source:                strings.TrimSpace(fmt.Sprint(raw["source"])),
		Raw:                   raw,
	}
	if len(profile.PositiveInterestTexts) == 0 {
		profile.PositiveInterestTexts = buildPositiveInterestTexts(profile)
	}
	if len(profile.NegativeInterestTexts) == 0 {
		profile.NegativeInterestTexts = buildNegativeInterestTexts(profile)
	}
	return profile
}

func (p MusicProfile) ToMap() map[string]any {
	out := map[string]any{}
	for key, value := range p.Raw {
		out[key] = value
	}
	out["positive_topics"] = p.PositiveTopics
	out["negative_topics"] = p.NegativeTopics
	out["preferred_uploaders"] = p.PreferredUploaders
	out["avoid_uploaders"] = p.AvoidUploaders
	out["blocked_uploaders"] = p.BlockedUploaders
	out["mood_weights"] = p.MoodWeights
	out["recent_intents"] = p.RecentIntents
	out["positive_interest_texts"] = p.PositiveInterestTexts
	out["negative_interest_texts"] = p.NegativeInterestTexts
	out["same_uploader_limit"] = p.SameUploaderLimit
	out["exploration_ratio"] = p.ExplorationRatio
	out["evidence_memory_ids"] = p.EvidenceMemoryIDs
	out["confidence"] = p.Confidence
	out["source"] = p.Source
	return out
}

func buildPositiveInterestTexts(profile MusicProfile) []string {
	texts := []string{}
	topics := topKeys(profile.PositiveTopics, 8)
	moods := topKeys(profile.MoodWeights, 6)
	uploaders := topKeys(profile.PreferredUploaders, 4)
	if len(topics) > 0 || len(moods) > 0 {
		parts := []string{}
		if len(topics) > 0 {
			parts = append(parts, "偏好的音乐主题: "+strings.Join(topics, "、"))
		}
		if len(moods) > 0 {
			parts = append(parts, "偏好的收听氛围: "+strings.Join(moods, "、"))
		}
		texts = append(texts, "用户适合推荐"+strings.Join(parts, "；"))
	}
	for _, intent := range profile.RecentIntents {
		texts = append(texts, "用户近期音乐搜索意图: "+intent)
	}
	for _, uploader := range uploaders {
		texts = append(texts, "用户偏好的歌手或UP主: "+uploader)
	}
	return dedupeStrings(texts, 12)
}

func buildNegativeInterestTexts(profile MusicProfile) []string {
	texts := []string{}
	for _, topic := range topKeys(profile.NegativeTopics, 8) {
		texts = append(texts, "用户不适合推荐的音乐主题: "+topic)
	}
	for _, uploader := range topKeys(profile.AvoidUploaders, 4) {
		texts = append(texts, "用户应减少推荐的歌手或UP主: "+uploader)
	}
	for _, uploader := range topKeys(profile.BlockedUploaders, 4) {
		texts = append(texts, "用户明确屏蔽的歌手或UP主: "+uploader)
	}
	return dedupeStrings(texts, 12)
}

func topKeys(values map[string]float64, limit int) []string {
	type pair struct {
		key   string
		score float64
	}
	items := make([]pair, 0, len(values))
	for key, score := range values {
		key = strings.TrimSpace(key)
		if key != "" {
			items = append(items, pair{key: key, score: score})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].score == items[j].score {
			return items[i].key < items[j].key
		}
		return items[i].score > items[j].score
	})
	out := make([]string, 0, min(limit, len(items)))
	for i, item := range items {
		if i >= limit {
			break
		}
		out = append(out, item.key)
	}
	return out
}

func dedupeStrings(values []string, limit int) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		if len([]rune(value)) > 240 {
			value = string([]rune(value)[:240])
		}
		out = append(out, value)
		if len(out) >= limit {
			break
		}
	}
	return out
}

func floatMap(value any) map[string]float64 {
	out := map[string]float64{}
	switch typed := value.(type) {
	case map[string]float64:
		for key, score := range typed {
			if strings.TrimSpace(key) != "" {
				out[key] = clamp(score, 0, 1)
			}
		}
	case map[string]any:
		for key, score := range typed {
			if strings.TrimSpace(key) != "" {
				out[key] = clamp(floatFromAny(score), 0, 1)
			}
		}
	}
	return out
}

func stringList(value any) []string {
	out := []string{}
	switch typed := value.(type) {
	case []string:
		for _, item := range typed {
			if strings.TrimSpace(item) != "" {
				out = append(out, strings.TrimSpace(item))
			}
		}
	case []any:
		for _, item := range typed {
			text := strings.TrimSpace(fmt.Sprint(item))
			if text != "" {
				out = append(out, text)
			}
		}
	}
	return out
}

func clamp(value float64, low float64, high float64) float64 {
	if value < low {
		return low
	}
	if value > high {
		return high
	}
	return value
}
