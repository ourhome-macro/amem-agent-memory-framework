package behavior

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"recommend-radio/backend-go/internal/library"
	"recommend-radio/backend-go/internal/platform/model"
	"recommend-radio/backend-go/internal/platform/respond"
)

var allowedEvents = map[string]bool{
	"play": true, "skip": true, "complete": true, "like": true,
	"recommendation.exposed": true, "recommendation.clicked": true,
	"recommendation.served": true, "recommendation.generated": true,
	"search.performed": true, "profile.updated": true,
	"candidate.prefiltered": true, "search_intent.yield_updated": true,
}

type EventInput struct {
	EventID  string         `json:"event_id"`
	EventID2 string         `json:"eventId"`
	UserID   string         `json:"userId"`
	Type     string         `json:"event"`
	Type2    string         `json:"type"`
	TrackID  string         `json:"trackId"`
	Scene    string         `json:"scene"`
	Payload  map[string]any `json:"payload"`
}

type Service struct {
	db      *gorm.DB
	library *library.Service
	topic   string
}

func NewService(db *gorm.DB, libraryService *library.Service, topic string) *Service {
	if topic == "" {
		topic = "radio_behavior_v1"
	}
	return &Service{db: db, library: libraryService, topic: topic}
}

func (s *Service) Record(ctx context.Context, userID string, input EventInput) (map[string]any, error) {
	eventID := firstNonEmpty(input.EventID, input.EventID2)
	if eventID == "" {
		return nil, respond.BadRequest("event_id is required")
	}
	eventType := normalizeEventType(firstNonEmpty(input.Type, input.Type2))
	if !allowedEvents[eventType] {
		return nil, respond.BadRequest("unsupported behavior event")
	}
	if input.UserID != "" {
		userID = input.UserID
	}
	if userID == "" {
		userID = model.LegacyOwnerUserID
	}
	scene := strings.TrimSpace(input.Scene)
	payload := input.Payload
	if payload == nil {
		payload = map[string]any{}
	}
	payload["event_id"] = eventID
	payload["event"] = eventType
	if input.TrackID != "" {
		payload["trackId"] = input.TrackID
	}
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	var trackID *string
	if strings.TrimSpace(input.TrackID) != "" {
		value := strings.TrimSpace(input.TrackID)
		trackID = &value
	}
	now := time.Now().UTC()
	err = s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		behavior := model.BehaviorEvent{
			EventID: eventID, UserID: userID, EventType: eventType, TrackID: trackID,
			Scene: scene, PayloadJSON: payloadJSON, CreatedAt: now,
		}
		if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&behavior).Error; err != nil {
			return err
		}
		outbox := model.OutboxEvent{
			EventID: eventID, Topic: s.topic, PayloadJSON: payloadJSON, Status: "pending",
			NextRetryAt: now, CreatedAt: now, UpdatedAt: now,
		}
		return tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&outbox).Error
	})
	if err != nil {
		return nil, err
	}
	return map[string]any{"eventId": eventID, "event": eventType, "accepted": true}, nil
}

func normalizeEventType(value string) string {
	switch strings.TrimSpace(strings.ToLower(value)) {
	case "shown", "exposed", "recommendation.exposed":
		return "recommendation.exposed"
	case "accepted", "clicked", "recommendation.clicked":
		return "recommendation.clicked"
	case "served", "recommendation.served":
		return "recommendation.served"
	case "generated", "recommendation.generated":
		return "recommendation.generated"
	case "search", "searched", "search.performed":
		return "search.performed"
	case "profile.updated":
		return "profile.updated"
	case "candidate.prefiltered":
		return "candidate.prefiltered"
	case "search_intent.yield_updated":
		return "search_intent.yield_updated"
	case "played", "play":
		return "play"
	case "skipped", "skip":
		return "skip"
	case "completed", "complete":
		return "complete"
	case "liked", "like":
		return "like"
	default:
		return value
	}
}

type Dispatcher struct {
	db        *gorm.DB
	publisher Publisher
	logger    *slog.Logger
	workers   int
	stop      chan struct{}
	done      chan struct{}
}

func NewDispatcher(db *gorm.DB, publisher Publisher, logger *slog.Logger, workers int) *Dispatcher {
	if workers <= 0 {
		workers = 4
	}
	if publisher == nil {
		publisher = NoopPublisher{}
	}
	return &Dispatcher{db: db, publisher: publisher, logger: logger, workers: workers, stop: make(chan struct{}), done: make(chan struct{})}
}

func (d *Dispatcher) Start(ctx context.Context) {
	jobs := make(chan model.OutboxEvent, d.workers*2)
	for i := 0; i < d.workers; i++ {
		go d.worker(ctx, jobs)
	}
	go d.poll(ctx, jobs)
}

func (d *Dispatcher) Stop() {
	close(d.stop)
	<-d.done
}

func (d *Dispatcher) poll(ctx context.Context, jobs chan<- model.OutboxEvent) {
	defer close(d.done)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-d.stop:
			return
		case <-ticker.C:
			var events []model.OutboxEvent
			err := d.db.WithContext(ctx).
				Where("status = ? AND next_retry_at <= ?", "pending", time.Now().UTC()).
				Order("id ASC").Limit(d.workers * 4).Find(&events).Error
			if err != nil {
				d.logger.Warn("outbox poll failed", "error", err)
				continue
			}
			for _, event := range events {
				select {
				case jobs <- event:
				case <-ctx.Done():
					return
				case <-d.stop:
					return
				}
			}
		}
	}
}

func (d *Dispatcher) worker(ctx context.Context, jobs <-chan model.OutboxEvent) {
	for {
		select {
		case <-ctx.Done():
			return
		case event := <-jobs:
			d.publishOne(ctx, event)
		}
	}
}

func (d *Dispatcher) publishOne(ctx context.Context, event model.OutboxEvent) {
	if err := d.publisher.Publish(ctx, event.Topic, event.EventID, event.PayloadJSON); err != nil {
		d.markFailed(ctx, event, err)
		return
	}
	now := time.Now().UTC()
	_ = d.db.WithContext(ctx).Model(&model.OutboxEvent{}).Where("id = ? AND status = ?", event.ID, "pending").
		Updates(map[string]any{"status": "published", "published_at": now, "updated_at": now}).Error
}

func (d *Dispatcher) markFailed(ctx context.Context, event model.OutboxEvent, err error) {
	message := err.Error()
	attempts := event.Attempts + 1
	status := "pending"
	if attempts >= 10 {
		status = "dlq"
	}
	next := time.Now().UTC().Add(time.Duration(attempts*attempts) * time.Second)
	_ = d.db.WithContext(ctx).Model(&model.OutboxEvent{}).Where("id = ?", event.ID).
		Updates(map[string]any{"status": status, "attempts": attempts, "last_error": message, "next_retry_at": next, "updated_at": time.Now().UTC()}).Error
}

func MarkConsumerProcessed(ctx context.Context, db *gorm.DB, consumerName string, eventID string, fn func(*gorm.DB) error) error {
	now := time.Now().UTC()
	return db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		record := model.ConsumerIdempotency{ConsumerName: consumerName, EventID: eventID, ProcessedAt: now}
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&record)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 0 {
			return nil
		}
		if fn == nil {
			return nil
		}
		return fn(tx)
	})
}

func ConsumerProcessed(ctx context.Context, db *gorm.DB, consumerName string, eventID string) (bool, error) {
	var count int64
	err := db.WithContext(ctx).Model(&model.ConsumerIdempotency{}).
		Where("consumer_name = ? AND event_id = ?", consumerName, eventID).
		Count(&count).Error
	return count > 0, err
}

func IsDuplicate(err error) bool {
	return errors.Is(err, gorm.ErrDuplicatedKey)
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
