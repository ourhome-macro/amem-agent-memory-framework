package bili

import (
	"context"

	"recommend-radio/backend-go/internal/platform/model"
)

type Track = model.Track

type VideoDetail struct {
	Track Track   `json:"track"`
	Pages []Track `json:"pages"`
}

type AudioStreamInfo struct {
	URL                     string   `json:"url"`
	BackupURLs              []string `json:"backupUrls"`
	Duration                int      `json:"duration"`
	Bitrate                 int64    `json:"bitrate"`
	SampleRate              int      `json:"sampleRate"`
	SampleRateCompat        int      `json:"sample_rate"`
	Channels                int      `json:"channels"`
	Quality                 string   `json:"quality"`
	ActualQuality           string   `json:"actualQuality"`
	ActualQualityCompat     string   `json:"actual_quality"`
	Codec                   string   `json:"codec"`
	Fallback                bool     `json:"fallback"`
	StreamID                int      `json:"streamId"`
	AvailableAudioQualities []string `json:"availableAudioQualities"`
}

type CoverInfo map[string]any
type TrackIntro map[string]any
type TrackSubtitles map[string]any
type TrackChapters map[string]any
type TrackComments map[string]any

type FavoriteFolder struct {
	MediaID       int64  `json:"mediaId"`
	ID            int64  `json:"id"`
	FID           *int64 `json:"fid,omitempty"`
	MID           int64  `json:"mid"`
	Title         string `json:"title"`
	Cover         string `json:"cover"`
	MediaCount    int    `json:"mediaCount"`
	Attr          int    `json:"attr"`
	FavoriteState int    `json:"favoriteState"`
}

type UserProfile struct {
	MID     int64  `json:"mid"`
	Name    string `json:"name"`
	Face    string `json:"face"`
	Sign    string `json:"sign,omitempty"`
	Level   int    `json:"level,omitempty"`
	VIPType int    `json:"vipType,omitempty"`
}

type UpTracksResult struct {
	MID      int64       `json:"mid"`
	Page     int         `json:"page"`
	PageSize int         `json:"pageSize"`
	Order    string      `json:"order"`
	Total    int         `json:"total"`
	HasMore  bool        `json:"hasMore"`
	Profile  UserProfile `json:"profile"`
	Tracks   []Track     `json:"tracks"`
}

type Gateway interface {
	Search(ctx context.Context, keyword string, page int, pageSize int) ([]Track, error)
	ParseInput(input string) (string, bool)
	GetVideoDetail(ctx context.Context, bvid string) (VideoDetail, error)
	ResolveCID(ctx context.Context, bvid string, cid *int64) (string, int64, error)
	GetPlayURL(ctx context.Context, bvid string, cid int64, quality string) (AudioStreamInfo, error)
	RefreshPlayURL(ctx context.Context, bvid string, cid int64, quality string) (AudioStreamInfo, error)
	GetCoverInfo(ctx context.Context, bvid string, cid *int64) (CoverInfo, error)
	GetVideoIntro(ctx context.Context, bvid string, cid *int64) (TrackIntro, error)
	GetTrackSubtitles(ctx context.Context, bvid string, cid *int64) (TrackSubtitles, error)
	GetTrackChapters(ctx context.Context, bvid string, cid *int64) (TrackChapters, error)
	GetTrackComments(ctx context.Context, bvid string, page int, pageSize int) (TrackComments, error)
	ListUserTracks(ctx context.Context, mid int64, page int, pageSize int, order string) (UpTracksResult, error)
}
