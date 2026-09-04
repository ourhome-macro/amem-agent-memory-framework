package model

import (
	"encoding/json"
	"time"
)

const LegacyOwnerUserID = "legacy-owner"

type AppUser struct {
	ID          string     `gorm:"primaryKey;column:id"`
	DisplayName string     `gorm:"column:display_name"`
	Email       *string    `gorm:"column:email"`
	Role        string     `gorm:"column:role"`
	Status      string     `gorm:"column:status"`
	CreatedAt   time.Time  `gorm:"column:created_at"`
	UpdatedAt   time.Time  `gorm:"column:updated_at"`
	LastLoginAt *time.Time `gorm:"column:last_login_at"`
}

func (AppUser) TableName() string { return "app_users" }

type BiliAccount struct {
	UserID                string     `gorm:"primaryKey;column:user_id"`
	Provider              string     `gorm:"primaryKey;column:provider"`
	CookieEncrypted       *string    `gorm:"column:cookie_encrypted"`
	RefreshTokenEncrypted *string    `gorm:"column:refresh_token_encrypted"`
	UserMID               *int64     `gorm:"column:user_mid"`
	UserName              string     `gorm:"column:user_name"`
	UserFace              string     `gorm:"column:user_face"`
	CookieUpdatedAt       *time.Time `gorm:"column:cookie_updated_at"`
	UpdatedAt             time.Time  `gorm:"column:updated_at"`
}

func (BiliAccount) TableName() string { return "bili_accounts" }

type AuthQRSession struct {
	UserID    string     `gorm:"primaryKey;column:user_id"`
	QRCodeKey string     `gorm:"primaryKey;column:qrcode_key"`
	URL       string     `gorm:"column:url"`
	Status    string     `gorm:"column:status"`
	Message   *string    `gorm:"column:message"`
	CreatedAt time.Time  `gorm:"column:created_at"`
	UpdatedAt time.Time  `gorm:"column:updated_at"`
	ExpiresAt *time.Time `gorm:"column:expires_at"`
}

func (AuthQRSession) TableName() string { return "auth_qr_sessions" }

type Track struct {
	TrackID     string          `gorm:"primaryKey;column:track_id" json:"trackId"`
	BVID        string          `gorm:"column:bvid" json:"bvid"`
	CID         *int64          `gorm:"column:cid" json:"cid,omitempty"`
	Title       string          `gorm:"column:title" json:"title"`
	Owner       string          `gorm:"column:owner" json:"owner"`
	OwnerMID    *int64          `gorm:"column:owner_mid" json:"ownerMid,omitempty"`
	Cover       string          `gorm:"column:cover" json:"cover"`
	Duration    int             `gorm:"column:duration" json:"duration"`
	PlayCount   int64           `gorm:"column:play_count" json:"playCount"`
	PublishedAt *string         `gorm:"column:published_at" json:"publishedAt,omitempty"`
	Page        *int            `gorm:"column:page" json:"page,omitempty"`
	PageTitle   *string         `gorm:"column:page_title" json:"pageTitle,omitempty"`
	Source      string          `gorm:"column:source" json:"source"`
	RawJSON     json.RawMessage `gorm:"column:raw_json" json:"-"`
	UpdatedAt   time.Time       `gorm:"column:updated_at" json:"-"`
}

func (Track) TableName() string { return "tracks" }

type Like struct {
	UserID    string    `gorm:"primaryKey;column:user_id"`
	TrackID   string    `gorm:"primaryKey;column:track_id"`
	CreatedAt time.Time `gorm:"column:created_at"`
	Track     Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (Like) TableName() string { return "likes" }

type Recent struct {
	UserID       string    `gorm:"primaryKey;column:user_id"`
	TrackID      string    `gorm:"primaryKey;column:track_id"`
	LastPlayedAt time.Time `gorm:"column:last_played_at"`
	PlayCount    int       `gorm:"column:play_count"`
	PositionMS   int       `gorm:"column:position_ms"`
	ListenMS     int       `gorm:"column:listen_ms"`
	Completed    bool      `gorm:"column:completed"`
	Track        Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (Recent) TableName() string { return "recent" }

type TrackReview struct {
	UserID     string    `gorm:"primaryKey;column:user_id"`
	TrackID    string    `gorm:"primaryKey;column:track_id"`
	Rating     int       `gorm:"column:rating"`
	Mood       string    `gorm:"column:mood"`
	Note       string    `gorm:"column:note"`
	Visibility string    `gorm:"column:visibility"`
	CreatedAt  time.Time `gorm:"column:created_at"`
	UpdatedAt  time.Time `gorm:"column:updated_at"`
	Track      Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (TrackReview) TableName() string { return "track_reviews" }

type Playlist struct {
	UserID     string    `gorm:"primaryKey;column:user_id"`
	ID         string    `gorm:"primaryKey;column:id"`
	Name       string    `gorm:"column:name"`
	Cover      *string   `gorm:"column:cover"`
	SourceType string    `gorm:"column:source_type"`
	SourceBVID *string   `gorm:"column:source_bvid"`
	CreatedAt  time.Time `gorm:"column:created_at"`
	UpdatedAt  time.Time `gorm:"column:updated_at"`
}

func (Playlist) TableName() string { return "playlists" }

type PlaylistItem struct {
	UserID     string    `gorm:"primaryKey;column:user_id"`
	PlaylistID string    `gorm:"primaryKey;column:playlist_id"`
	TrackID    string    `gorm:"primaryKey;column:track_id"`
	Position   int       `gorm:"column:position"`
	AddedAt    time.Time `gorm:"column:added_at"`
	Track      Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (PlaylistItem) TableName() string { return "playlist_items" }

type PlayerQueueState struct {
	UserID       string    `gorm:"primaryKey;column:user_id"`
	CurrentIndex int       `gorm:"column:current_index"`
	PlayMode     string    `gorm:"column:play_mode"`
	UpdatedAt    time.Time `gorm:"column:updated_at"`
}

func (PlayerQueueState) TableName() string { return "player_queue_state" }

type PlayerQueueItem struct {
	UserID   string    `gorm:"primaryKey;column:user_id"`
	Position int       `gorm:"primaryKey;column:position"`
	TrackID  string    `gorm:"column:track_id"`
	AddedAt  time.Time `gorm:"column:added_at"`
	Track    Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (PlayerQueueItem) TableName() string { return "player_queue_items" }

type PlaybackSession struct {
	UserID         string     `gorm:"primaryKey;column:user_id"`
	SessionID      string     `gorm:"primaryKey;column:session_id"`
	TrackID        string     `gorm:"column:track_id"`
	StartedAt      time.Time  `gorm:"column:started_at"`
	EndedAt        *time.Time `gorm:"column:ended_at"`
	LastPositionMS int        `gorm:"column:last_position_ms"`
	ListenMS       int        `gorm:"column:listen_ms"`
	Completed      bool       `gorm:"column:completed"`
	Skipped        bool       `gorm:"column:skipped"`
	LastEvent      string     `gorm:"column:last_event"`
}

func (PlaybackSession) TableName() string { return "playback_sessions" }

type PlaybackRecent struct {
	UserID       string    `gorm:"primaryKey;column:user_id"`
	TrackID      string    `gorm:"primaryKey;column:track_id"`
	LastPlayedAt time.Time `gorm:"column:last_played_at"`
	PositionMS   int       `gorm:"column:position_ms"`
	ListenMS     int       `gorm:"column:listen_ms"`
	Completed    bool      `gorm:"column:completed"`
	Skipped      bool      `gorm:"column:skipped"`
	Track        Track     `gorm:"foreignKey:TrackID;references:TrackID"`
}

func (PlaybackRecent) TableName() string { return "playback_recent" }

type PlaybackEvent struct {
	ID         uint64    `gorm:"primaryKey;column:id"`
	UserID     string    `gorm:"column:user_id"`
	SessionID  string    `gorm:"column:session_id"`
	TrackID    string    `gorm:"column:track_id"`
	Event      string    `gorm:"column:event"`
	PositionMS int       `gorm:"column:position_ms"`
	ListenMS   int       `gorm:"column:listen_ms"`
	Completed  bool      `gorm:"column:completed"`
	CreatedAt  time.Time `gorm:"column:created_at"`
}

func (PlaybackEvent) TableName() string { return "playback_events" }

type RecommendationEvent struct {
	ID        uint64    `gorm:"primaryKey;column:id"`
	UserID    string    `gorm:"column:user_id"`
	TrackID   string    `gorm:"column:track_id"`
	Event     string    `gorm:"column:event"`
	Scene     string    `gorm:"column:scene"`
	Source    string    `gorm:"column:source"`
	Reason    string    `gorm:"column:reason"`
	Score     float64   `gorm:"column:score"`
	CreatedAt time.Time `gorm:"column:created_at"`
}

func (RecommendationEvent) TableName() string { return "recommendation_events" }

type RecommendationHistory struct {
	ID            uint64    `gorm:"primaryKey;column:id"`
	UserID        string    `gorm:"column:user_id"`
	TrackID       string    `gorm:"column:track_id"`
	RecommendedAt time.Time `gorm:"column:recommended_at"`
	Clicked       bool      `gorm:"column:clicked"`
	PlayedSeconds int       `gorm:"column:played_seconds"`
	Completed     bool      `gorm:"column:completed"`
	Liked         bool      `gorm:"column:liked"`
	Skipped       bool      `gorm:"column:skipped"`
	Scene         string    `gorm:"column:scene"`
	Source        string    `gorm:"column:source"`
	Score         float64   `gorm:"column:score"`
	Reason        string    `gorm:"column:reason"`
}

func (RecommendationHistory) TableName() string { return "recommendation_history" }

type RecommendationTrace struct {
	TraceID        string          `gorm:"primaryKey;column:trace_id"`
	UserID         string          `gorm:"column:user_id"`
	Scene          string          `gorm:"column:scene"`
	ProfileTraceID string          `gorm:"column:profile_trace_id"`
	AgentTraceID   string          `gorm:"column:agent_trace_id"`
	PayloadJSON    json.RawMessage `gorm:"column:payload_json"`
	CreatedAt      time.Time       `gorm:"column:created_at"`
}

func (RecommendationTrace) TableName() string { return "recommendation_traces" }

type Setting struct {
	UserID    string    `gorm:"primaryKey;column:user_id"`
	Key       string    `gorm:"primaryKey;column:key"`
	Value     string    `gorm:"column:value"`
	UpdatedAt time.Time `gorm:"column:updated_at"`
}

func (Setting) TableName() string { return "settings" }

type BehaviorEvent struct {
	EventID     string          `gorm:"primaryKey;column:event_id"`
	UserID      string          `gorm:"column:user_id"`
	EventType   string          `gorm:"column:event_type"`
	TrackID     *string         `gorm:"column:track_id"`
	Scene       string          `gorm:"column:scene"`
	PayloadJSON json.RawMessage `gorm:"column:payload_json"`
	CreatedAt   time.Time       `gorm:"column:created_at"`
}

func (BehaviorEvent) TableName() string { return "behavior_events" }

type OutboxEvent struct {
	ID          uint64          `gorm:"primaryKey;column:id"`
	EventID     string          `gorm:"column:event_id"`
	Topic       string          `gorm:"column:topic"`
	PayloadJSON json.RawMessage `gorm:"column:payload_json"`
	Status      string          `gorm:"column:status"`
	Attempts    int             `gorm:"column:attempts"`
	LastError   *string         `gorm:"column:last_error"`
	NextRetryAt time.Time       `gorm:"column:next_retry_at"`
	PublishedAt *time.Time      `gorm:"column:published_at"`
	CreatedAt   time.Time       `gorm:"column:created_at"`
	UpdatedAt   time.Time       `gorm:"column:updated_at"`
}

func (OutboxEvent) TableName() string { return "outbox_events" }

type ConsumerIdempotency struct {
	ConsumerName string    `gorm:"primaryKey;column:consumer_name"`
	EventID      string    `gorm:"primaryKey;column:event_id"`
	ProcessedAt  time.Time `gorm:"column:processed_at"`
}

func (ConsumerIdempotency) TableName() string { return "consumer_idempotency" }

type ConsumerDLQEvent struct {
	ID              uint64          `gorm:"primaryKey;column:id"`
	ConsumerName    string          `gorm:"column:consumer_name"`
	EventID         string          `gorm:"column:event_id"`
	Topic           string          `gorm:"column:topic"`
	MessageID       string          `gorm:"column:message_id"`
	DeliveryAttempt int             `gorm:"column:delivery_attempt"`
	ErrorMessage    string          `gorm:"column:error_message"`
	PayloadJSON     json.RawMessage `gorm:"column:payload_json"`
	CreatedAt       time.Time       `gorm:"column:created_at"`
}

func (ConsumerDLQEvent) TableName() string { return "consumer_dlq_events" }

type BehaviorMetricCounter struct {
	Day       string    `gorm:"primaryKey;column:day"`
	EventType string    `gorm:"primaryKey;column:event_type"`
	Scene     string    `gorm:"primaryKey;column:scene"`
	Source    string    `gorm:"primaryKey;column:source"`
	Count     int64     `gorm:"column:count"`
	UpdatedAt time.Time `gorm:"column:updated_at"`
}

func (BehaviorMetricCounter) TableName() string { return "behavior_metric_counters" }

type AsyncTaskEvent struct {
	EventID     string          `gorm:"primaryKey;column:event_id"`
	EventType   string          `gorm:"column:event_type"`
	UserID      string          `gorm:"column:user_id"`
	Scene       string          `gorm:"column:scene"`
	Source      string          `gorm:"column:source"`
	PayloadJSON json.RawMessage `gorm:"column:payload_json"`
	CreatedAt   time.Time       `gorm:"column:created_at"`
}

func (AsyncTaskEvent) TableName() string { return "async_task_events" }

type SearchIntentStat struct {
	Query            string    `gorm:"primaryKey;column:query"`
	Scene            string    `gorm:"primaryKey;column:scene"`
	Source           string    `gorm:"primaryKey;column:source"`
	SearchedCount    int64     `gorm:"column:searched_count"`
	PrefilterPassed  int64     `gorm:"column:prefilter_passed"`
	RecommendedCount int64     `gorm:"column:recommended_count"`
	ClickedCount     int64     `gorm:"column:clicked_count"`
	CompletedCount   int64     `gorm:"column:completed_count"`
	SkippedCount     int64     `gorm:"column:skipped_count"`
	UpdatedAt        time.Time `gorm:"column:updated_at"`
}

func (SearchIntentStat) TableName() string { return "search_intent_stats" }

type RecommendationCandidate struct {
	ID                 uint64          `gorm:"primaryKey;column:id"`
	TraceID            string          `gorm:"column:trace_id"`
	UserID             string          `gorm:"column:user_id"`
	Scene              string          `gorm:"column:scene"`
	TrackID            string          `gorm:"column:track_id"`
	SourceQuery        string          `gorm:"column:source_query"`
	SourceRelevance    float64         `gorm:"column:source_relevance"`
	PositiveSimilarity float64         `gorm:"column:positive_similarity"`
	NegativeSimilarity float64         `gorm:"column:negative_similarity"`
	RouterDecision     string          `gorm:"column:router_decision"`
	FinalScore         float64         `gorm:"column:final_score"`
	PayloadJSON        json.RawMessage `gorm:"column:payload_json"`
	CreatedAt          time.Time       `gorm:"column:created_at"`
}

func (RecommendationCandidate) TableName() string { return "recommendation_candidates" }
