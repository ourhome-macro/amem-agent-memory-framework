package behavior

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"recommend-radio/backend-go/internal/amem"
	"recommend-radio/backend-go/internal/platform/model"
)

const (
	AMEMConsumerName           = "radio-amem-consumer"
	RecommendationConsumerName = "radio-recommendation-consumer"
	MetricsConsumerName        = "radio-metrics-consumer"
)

type ConsumerProcessor struct {
	db   *gorm.DB
	amem amem.Client
}

func NewConsumerProcessor(db *gorm.DB, amemClient amem.Client) *ConsumerProcessor {
	if amemClient == nil {
		amemClient = amem.NoopClient{}
	}
	return &ConsumerProcessor{db: db, amem: amemClient}
}

func (p *ConsumerProcessor) Process(ctx context.Context, consumerName string, topic string, body []byte) error {
	event, err := parseConsumerEvent(body)
	if err != nil {
		return err
	}
	switch consumerName {
	case AMEMConsumerName:
		return p.processAMEM(ctx, event)
	case RecommendationConsumerName:
		return p.processRecommendation(ctx, event)
	case MetricsConsumerName:
		return p.processMetrics(ctx, event)
	default:
		return fmt.Errorf("unknown consumer: %s", consumerName)
	}
}

func (p *ConsumerProcessor) RecordDLQ(ctx context.Context, consumerName string, topic string, messageID string, deliveryAttempt int, body []byte, cause error) error {
	eventID := eventIDFromBody(body)
	if eventID == "" {
		eventID = messageID
	}
	if eventID == "" {
		eventID = fmt.Sprintf("unknown:%d", time.Now().UnixNano())
	}
	message := ""
	if cause != nil {
		message = cause.Error()
	}
	return p.db.WithContext(ctx).Clauses(clause.OnConflict{DoNothing: true}).Create(&model.ConsumerDLQEvent{
		ConsumerName: consumerName, EventID: eventID, Topic: topic, MessageID: messageID,
		DeliveryAttempt: deliveryAttempt, ErrorMessage: message, PayloadJSON: append([]byte{}, body...),
		CreatedAt: time.Now().UTC(),
	}).Error
}

func (p *ConsumerProcessor) processAMEM(ctx context.Context, event consumerEvent) error {
	if !event.amemRelevant() {
		return MarkConsumerProcessed(ctx, p.db, AMEMConsumerName, event.EventID, nil)
	}
	processed, err := ConsumerProcessed(ctx, p.db, AMEMConsumerName, event.EventID)
	if err != nil || processed {
		return err
	}
	if err := p.amem.RecordBehavior(ctx, event.Payload); err != nil {
		return err
	}
	return MarkConsumerProcessed(ctx, p.db, AMEMConsumerName, event.EventID, nil)
}

func (p *ConsumerProcessor) processMetrics(ctx context.Context, event consumerEvent) error {
	return MarkConsumerProcessed(ctx, p.db, MetricsConsumerName, event.EventID, func(tx *gorm.DB) error {
		now := time.Now().UTC()
		counter := model.BehaviorMetricCounter{
			Day: now.Format("2006-01-02"), EventType: event.Event, Scene: event.Scene,
			Source: event.Source, Count: int64(event.metricCount()), UpdatedAt: now,
		}
		return tx.Clauses(clause.OnConflict{
			Columns: []clause.Column{{Name: "day"}, {Name: "event_type"}, {Name: "scene"}, {Name: "source"}},
			DoUpdates: clause.Assignments(map[string]any{
				"count":      gorm.Expr("count + ?", event.metricCount()),
				"updated_at": now,
			}),
		}).Create(&counter).Error
	})
}

func (p *ConsumerProcessor) processRecommendation(ctx context.Context, event consumerEvent) error {
	return MarkConsumerProcessed(ctx, p.db, RecommendationConsumerName, event.EventID, func(tx *gorm.DB) error {
		now := time.Now().UTC()
		if event.isAsyncLog() {
			if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&model.AsyncTaskEvent{
				EventID: event.EventID, EventType: event.Event, UserID: event.UserID,
				Scene: event.Scene, Source: event.Source, PayloadJSON: event.Raw, CreatedAt: now,
			}).Error; err != nil {
				return err
			}
		}
		if event.TrackID == "" {
			return nil
		}
		updates := map[string]any{}
		switch event.Event {
		case "recommendation.clicked":
			updates["clicked"] = true
		case "play":
			updates["played_seconds"] = gorm.Expr("GREATEST(played_seconds, ?)", secondsFromPayload(event.Payload))
		case "skip":
			updates["skipped"] = true
		case "complete":
			updates["completed"] = true
		case "like":
			updates["liked"] = true
		}
		if len(updates) == 0 {
			return nil
		}
		return tx.Model(&model.RecommendationHistory{}).
			Where("user_id = ? AND track_id = ?", event.UserID, event.TrackID).
			Updates(updates).Error
	})
}

type consumerEvent struct {
	EventID string
	UserID  string
	Event   string
	TrackID string
	Scene   string
	Source  string
	Payload map[string]any
	Raw     []byte
}

func (e consumerEvent) amemRelevant() bool {
	switch e.Event {
	case "play", "skip", "complete", "like", "recommendation.exposed", "recommendation.clicked", "profile.updated":
		return true
	default:
		return false
	}
}

func (e consumerEvent) isAsyncLog() bool {
	switch e.Event {
	case "search.performed", "recommendation.generated", "recommendation.served", "profile.updated", "candidate.prefiltered", "search_intent.yield_updated":
		return true
	default:
		return false
	}
}

func (e consumerEvent) metricCount() int {
	for _, key := range []string{"itemCount", "item_count", "resultCount", "result_count"} {
		if count := intFromPayload(e.Payload, key); count > 0 {
			return count
		}
	}
	if items, ok := e.Payload["items"].([]any); ok && len(items) > 0 {
		return len(items)
	}
	return 1
}

func parseConsumerEvent(body []byte) (consumerEvent, error) {
	payload := map[string]any{}
	if err := json.Unmarshal(body, &payload); err != nil {
		return consumerEvent{}, err
	}
	eventID := firstString(payload, "event_id", "eventId")
	if eventID == "" {
		return consumerEvent{}, fmt.Errorf("event_id is required")
	}
	eventType := normalizeEventType(firstString(payload, "event", "type"))
	if eventType == "" {
		return consumerEvent{}, fmt.Errorf("event is required")
	}
	userID := firstString(payload, "userId", "user_id")
	if userID == "" {
		userID = model.LegacyOwnerUserID
	}
	return consumerEvent{
		EventID: eventID, UserID: userID, Event: eventType,
		TrackID: firstString(payload, "trackId", "track_id"),
		Scene:   firstString(payload, "scene"),
		Source:  firstString(payload, "source"),
		Payload: payload, Raw: append([]byte{}, body...),
	}, nil
}

func eventIDFromBody(body []byte) string {
	payload := map[string]any{}
	if err := json.Unmarshal(body, &payload); err != nil {
		return ""
	}
	return firstString(payload, "event_id", "eventId")
}

func firstString(payload map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := payload[key]; ok && value != nil {
			return strings.TrimSpace(fmt.Sprint(value))
		}
	}
	return ""
}

func intFromPayload(payload map[string]any, keys ...string) int {
	for _, key := range keys {
		switch value := payload[key].(type) {
		case float64:
			return int(value)
		case int:
			return value
		case json.Number:
			out, _ := value.Int64()
			return int(out)
		}
	}
	return 0
}

func secondsFromPayload(payload map[string]any) int {
	if seconds := intFromPayload(payload, "playedSeconds", "played_seconds"); seconds > 0 {
		return seconds
	}
	return intFromPayload(payload, "listenMs", "listen_ms") / 1000
}
