package main

import (
	"context"
	"crypto/subtle"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	_ "modernc.org/sqlite"
)

const (
	defaultUserID    = "legacy-owner"
	maxReplayEvents  = 500
	subscriberBuffer = 64
)

type Config struct {
	Bind          string
	DBPath        string
	InternalToken string
	AuthMode      string
	AuthVerifyURL string
	Retention     time.Duration
}

type EventInput struct {
	TaskID    string          `json:"taskId"`
	SessionID string          `json:"sessionId"`
	UserID    string          `json:"userId"`
	Type      string          `json:"type"`
	Status    string          `json:"status"`
	Payload   json.RawMessage `json:"payload"`
}

type Event struct {
	ID        int64           `json:"eventId"`
	TaskID    string          `json:"taskId"`
	SessionID string          `json:"sessionId"`
	UserID    string          `json:"userId"`
	Type      string          `json:"type"`
	Status    string          `json:"status,omitempty"`
	Payload   json.RawMessage `json:"payload"`
	CreatedAt string          `json:"createdAt"`
}

type Store struct {
	db *sql.DB
}

func openStore(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	for _, statement := range []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA busy_timeout=5000",
		`CREATE TABLE IF NOT EXISTS agent_tasks (
			task_id TEXT PRIMARY KEY,
			session_id TEXT NOT NULL,
			user_id TEXT NOT NULL,
			status TEXT NOT NULL,
			error TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS dialogue_events (
			event_id INTEGER PRIMARY KEY AUTOINCREMENT,
			task_id TEXT NOT NULL,
			session_id TEXT NOT NULL,
			user_id TEXT NOT NULL,
			event_type TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT '',
			payload_json BLOB NOT NULL,
			created_at TEXT NOT NULL
		)`,
		`CREATE INDEX IF NOT EXISTS idx_dialogue_events_replay
			ON dialogue_events(user_id, session_id, event_id)`,
	} {
		if _, err := db.Exec(statement); err != nil {
			db.Close()
			return nil, err
		}
	}
	return &Store{db: db}, nil
}

func (s *Store) Append(ctx context.Context, input EventInput) (Event, error) {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Event{}, err
	}
	defer tx.Rollback()
	status := input.Status
	if status == "" {
		status = "running"
	}
	_, err = tx.ExecContext(ctx, `
		INSERT INTO agent_tasks(task_id, session_id, user_id, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(task_id) DO UPDATE SET
			session_id=excluded.session_id,
			user_id=excluded.user_id,
			status=excluded.status,
			updated_at=excluded.updated_at`,
		input.TaskID, input.SessionID, input.UserID, status, now, now,
	)
	if err != nil {
		return Event{}, err
	}
	result, err := tx.ExecContext(ctx, `
		INSERT INTO dialogue_events(
			task_id, session_id, user_id, event_type, status, payload_json, created_at
		) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		input.TaskID, input.SessionID, input.UserID, input.Type, input.Status, []byte(input.Payload), now,
	)
	if err != nil {
		return Event{}, err
	}
	id, err := result.LastInsertId()
	if err != nil {
		return Event{}, err
	}
	if err := tx.Commit(); err != nil {
		return Event{}, err
	}
	return Event{
		ID: id, TaskID: input.TaskID, SessionID: input.SessionID,
		UserID: input.UserID, Type: input.Type, Status: input.Status,
		Payload: input.Payload, CreatedAt: now,
	}, nil
}

func (s *Store) ListAfter(
	ctx context.Context,
	userID string,
	sessionID string,
	afterID int64,
) ([]Event, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT event_id, task_id, session_id, user_id, event_type, status, payload_json, created_at
		FROM dialogue_events
		WHERE user_id=? AND session_id=? AND event_id>?
		ORDER BY event_id ASC LIMIT ?`,
		userID, sessionID, afterID, maxReplayEvents,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]Event, 0)
	for rows.Next() {
		var event Event
		if err := rows.Scan(
			&event.ID, &event.TaskID, &event.SessionID, &event.UserID,
			&event.Type, &event.Status, &event.Payload, &event.CreatedAt,
		); err != nil {
			return nil, err
		}
		result = append(result, event)
	}
	return result, rows.Err()
}

func (s *Store) LatestID(ctx context.Context, userID string, sessionID string) (int64, error) {
	var value sql.NullInt64
	err := s.db.QueryRowContext(
		ctx,
		"SELECT MAX(event_id) FROM dialogue_events WHERE user_id=? AND session_id=?",
		userID,
		sessionID,
	).Scan(&value)
	if err != nil || !value.Valid {
		return 0, err
	}
	return value.Int64, nil
}

func (s *Store) Ready(ctx context.Context) error {
	return s.db.PingContext(ctx)
}

func (s *Store) Prune(ctx context.Context, before time.Time) error {
	cutoff := before.UTC().Format(time.RFC3339Nano)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, "DELETE FROM dialogue_events WHERE created_at < ?", cutoff); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, "DELETE FROM agent_tasks WHERE updated_at < ?", cutoff); err != nil {
		return err
	}
	return tx.Commit()
}

type Broker struct {
	mu          sync.Mutex
	subscribers map[string]map[chan Event]struct{}
}

func newBroker() *Broker {
	return &Broker{subscribers: make(map[string]map[chan Event]struct{})}
}

func streamKey(userID, sessionID string) string {
	return userID + "\x00" + sessionID
}

func (b *Broker) Subscribe(userID, sessionID string) (<-chan Event, func()) {
	key := streamKey(userID, sessionID)
	channel := make(chan Event, subscriberBuffer)
	b.mu.Lock()
	if b.subscribers[key] == nil {
		b.subscribers[key] = make(map[chan Event]struct{})
	}
	b.subscribers[key][channel] = struct{}{}
	b.mu.Unlock()
	var once sync.Once
	return channel, func() {
		once.Do(func() {
			b.mu.Lock()
			if _, ok := b.subscribers[key][channel]; ok {
				delete(b.subscribers[key], channel)
				close(channel)
			}
			if len(b.subscribers[key]) == 0 {
				delete(b.subscribers, key)
			}
			b.mu.Unlock()
		})
	}
}

func (b *Broker) Publish(event Event) {
	key := streamKey(event.UserID, event.SessionID)
	b.mu.Lock()
	defer b.mu.Unlock()
	for channel := range b.subscribers[key] {
		select {
		case channel <- event:
		default:
			// Force a slow client to reconnect and replay from SQLite.
			delete(b.subscribers[key], channel)
			close(channel)
		}
	}
	if len(b.subscribers[key]) == 0 {
		delete(b.subscribers, key)
	}
}

type Server struct {
	config     Config
	store      *Store
	broker     *Broker
	httpClient *http.Client
	logger     *slog.Logger
}

func newServer(config Config, store *Store, logger *slog.Logger) *Server {
	return &Server{
		config:     config,
		store:      store,
		broker:     newBroker(),
		httpClient: &http.Client{Timeout: 5 * time.Second},
		logger:     logger,
	}
}

func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(gin.Recovery())
	router.GET("/health/live", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "live"})
	})
	router.GET("/health/ready", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
		defer cancel()
		if err := s.store.Ready(ctx); err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"status": "unavailable"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "ready"})
	})
	router.POST("/internal/events", s.publishEvent)
	router.GET("/api/agent/events", s.streamEvents)
	return router
}

func (s *Server) publishEvent(c *gin.Context) {
	if !secureEqual(c.GetHeader("Authorization"), "Bearer "+s.config.InternalToken) {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid internal token"})
		return
	}
	var input EventInput
	if err := c.ShouldBindJSON(&input); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid event payload"})
		return
	}
	input.TaskID = strings.TrimSpace(input.TaskID)
	input.SessionID = strings.TrimSpace(input.SessionID)
	input.UserID = strings.TrimSpace(input.UserID)
	input.Type = normalizeEventType(input.Type)
	if input.TaskID == "" || input.SessionID == "" || input.UserID == "" || input.Type == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "taskId, sessionId, userId and type are required"})
		return
	}
	if len(input.Payload) == 0 || string(input.Payload) == "null" {
		input.Payload = json.RawMessage(`{}`)
	}
	event, err := s.store.Append(c.Request.Context(), input)
	if err != nil {
		s.logger.Error("persist event failed", "error", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "event persistence failed"})
		return
	}
	s.broker.Publish(event)
	c.JSON(http.StatusAccepted, gin.H{"eventId": event.ID})
}

func (s *Server) streamEvents(c *gin.Context) {
	sessionID := strings.TrimSpace(c.Query("sessionId"))
	if sessionID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "sessionId is required"})
		return
	}
	userID, err := s.authenticate(c.Request)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "authentication required"})
		return
	}
	afterID := parseEventID(c.GetHeader("Last-Event-ID"))
	if queryID := parseEventID(c.Query("lastEventId")); queryID > afterID {
		afterID = queryID
	}
	if afterID == 0 {
		afterID, err = s.store.LatestID(c.Request.Context(), userID, sessionID)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "event cursor lookup failed"})
			return
		}
	}

	events, unsubscribe := s.broker.Subscribe(userID, sessionID)
	defer unsubscribe()
	replay, err := s.store.ListAfter(c.Request.Context(), userID, sessionID, afterID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "event replay failed"})
		return
	}

	c.Header("Content-Type", "text/event-stream; charset=utf-8")
	c.Header("Cache-Control", "no-cache, no-transform")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)
	fmt.Fprint(c.Writer, "retry: 3000\n\n")
	c.Writer.Flush()

	lastSent := afterID
	for _, event := range replay {
		if event.ID <= lastSent {
			continue
		}
		if err := writeSSE(c.Writer, event); err != nil {
			return
		}
		lastSent = event.ID
		c.Writer.Flush()
	}

	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-c.Request.Context().Done():
			return
		case event, ok := <-events:
			if !ok {
				return
			}
			if event.ID <= lastSent {
				continue
			}
			if err := writeSSE(c.Writer, event); err != nil {
				return
			}
			lastSent = event.ID
			c.Writer.Flush()
		case <-heartbeat.C:
			if _, err := fmt.Fprintf(c.Writer, ": heartbeat %d\n\n", time.Now().Unix()); err != nil {
				return
			}
			c.Writer.Flush()
		}
	}
}

func (s *Server) authenticate(request *http.Request) (string, error) {
	if strings.EqualFold(s.config.AuthMode, "disabled") || s.config.AuthVerifyURL == "" {
		return defaultUserID, nil
	}
	req, err := http.NewRequestWithContext(request.Context(), http.MethodGet, s.config.AuthVerifyURL, nil)
	if err != nil {
		return "", err
	}
	if cookie := request.Header.Get("Cookie"); cookie != "" {
		req.Header.Set("Cookie", cookie)
	}
	response, err := s.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", errors.New("session verification failed")
	}
	var payload struct {
		Success bool `json:"success"`
		Data    struct {
			Authenticated bool `json:"authenticated"`
			User          struct {
				ID string `json:"id"`
			} `json:"user"`
		} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return "", err
	}
	if !payload.Success || !payload.Data.Authenticated || payload.Data.User.ID == "" {
		return "", errors.New("unauthenticated")
	}
	return payload.Data.User.ID, nil
}

func writeSSE(writer http.ResponseWriter, event Event) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(
		writer,
		"id: %d\nevent: %s\ndata: %s\n\n",
		event.ID,
		event.Type,
		payload,
	)
	return err
}

func normalizeEventType(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" || len(value) > 64 {
		return ""
	}
	for _, character := range value {
		if !(character == '_' || character == '-' || character >= 'a' && character <= 'z' || character >= '0' && character <= '9') {
			return ""
		}
	}
	return value
}

func parseEventID(value string) int64 {
	result, _ := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if result < 0 {
		return 0
	}
	return result
}

func secureEqual(left, right string) bool {
	if len(left) != len(right) || left == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(left), []byte(right)) == 1
}

func loadConfig() Config {
	return Config{
		Bind:          env("SSE_BIND", "0.0.0.0:8080"),
		DBPath:        env("SSE_DB_PATH", "./data/sse-events.sqlite3"),
		InternalToken: env("SSE_INTERNAL_TOKEN", "local-sse-token"),
		AuthMode:      env("AUTH_MODE", "disabled"),
		AuthVerifyURL: env("SSE_AUTH_VERIFY_URL", "http://backend:5000/api/session/me"),
		Retention:     time.Duration(envInt("SSE_EVENT_RETENTION_DAYS", 7)) * 24 * time.Hour,
	}
}

func env(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value, err := strconv.Atoi(env(name, strconv.Itoa(fallback)))
	if err != nil || value < 1 {
		return fallback
	}
	return value
}

func main() {
	config := loadConfig()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	store, err := openStore(config.DBPath)
	if err != nil {
		logger.Error("open event store failed", "error", err)
		os.Exit(1)
	}
	defer store.db.Close()
	maintenanceCtx, stopMaintenance := context.WithCancel(context.Background())
	defer stopMaintenance()
	go func() {
		ticker := time.NewTicker(time.Hour)
		defer ticker.Stop()
		for {
			select {
			case <-maintenanceCtx.Done():
				return
			case <-ticker.C:
				ctx, cancel := context.WithTimeout(maintenanceCtx, 10*time.Second)
				err := store.Prune(ctx, time.Now().Add(-config.Retention))
				cancel()
				if err != nil {
					logger.Warn("SSE event pruning failed", "error", err)
				}
			}
		}
	}()

	httpServer := &http.Server{
		Addr:              config.Bind,
		Handler:           newServer(config, store, logger).Router(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       75 * time.Second,
	}
	go func() {
		logger.Info("SSE gateway listening", "address", config.Bind)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("SSE gateway stopped", "error", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(ctx); err != nil {
		logger.Error("SSE gateway shutdown failed", "error", err)
	}
}
