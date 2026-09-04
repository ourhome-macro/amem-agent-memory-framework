package app

import (
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/gin-gonic/gin"

	"recommend-radio/backend-go/internal/bili"
	"recommend-radio/backend-go/internal/platform/model"
	"recommend-radio/backend-go/internal/platform/respond"
)

func bindTrack(c *gin.Context) (model.Track, error) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		return model.Track{}, respond.BadRequest("invalid JSON")
	}
	track := trackFromMap(payload)
	if track.BVID == "" && c.Param("bvid") != "" {
		track.BVID = c.Param("bvid")
	}
	if track.TrackID == "" {
		track.TrackID = bili.MakeTrackID(track.BVID, track.CID)
	}
	return track, nil
}

func trackFromMap(payload map[string]any) model.Track {
	cid := optionalInt64(payload["cid"])
	ownerMID := optionalInt64(first(payload, "ownerMid", "owner_mid"))
	page := optionalInt(payload["page"])
	pageTitle := optionalString(first(payload, "pageTitle", "page_title"))
	publishedAt := optionalString(first(payload, "publishedAt", "published_at"))
	track := model.Track{
		TrackID:     stringAny(first(payload, "trackId", "track_id")),
		BVID:        stringAny(payload["bvid"]),
		CID:         cid,
		Title:       stringAny(payload["title"]),
		Owner:       stringAny(payload["owner"]),
		OwnerMID:    ownerMID,
		Cover:       stringAny(payload["cover"]),
		Duration:    intAny(payload["duration"]),
		PlayCount:   int64Any(first(payload, "playCount", "play_count")),
		PublishedAt: publishedAt,
		Page:        page,
		PageTitle:   pageTitle,
		Source:      stringAny(payload["source"]),
	}
	if track.Source == "" {
		track.Source = "bili"
	}
	if raw, err := json.Marshal(payload); err == nil {
		track.RawJSON = raw
	}
	if track.TrackID == "" && track.BVID != "" {
		track.TrackID = bili.MakeTrackID(track.BVID, cid)
	}
	return track
}

func tracksFromAny(value any) []model.Track {
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	out := make([]model.Track, 0, len(items))
	for _, item := range items {
		if row, ok := item.(map[string]any); ok {
			track := trackFromMap(row)
			if track.BVID != "" && track.Title != "" {
				out = append(out, track)
			}
		}
	}
	return out
}

func first(payload map[string]any, keys ...string) any {
	for _, key := range keys {
		if value, ok := payload[key]; ok {
			return value
		}
	}
	return nil
}

func stringAny(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func intAny(value any) int {
	return int(int64Any(value))
}

func int64Any(value any) int64 {
	switch typed := value.(type) {
	case int64:
		return typed
	case int:
		return int64(typed)
	case float64:
		return int64(typed)
	case string:
		out, _ := strconv.ParseInt(typed, 10, 64)
		return out
	default:
		return 0
	}
}

func boolAny(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		return typed == "true" || typed == "1"
	default:
		return false
	}
}

func optionalInt64(value any) *int64 {
	if value == nil || value == "" {
		return nil
	}
	out := int64Any(value)
	return &out
}

func optionalInt(value any) *int {
	if value == nil || value == "" {
		return nil
	}
	out := intAny(value)
	return &out
}

func optionalString(value any) *string {
	if value == nil {
		return nil
	}
	out := stringAny(value)
	if out == "" {
		return nil
	}
	return &out
}
