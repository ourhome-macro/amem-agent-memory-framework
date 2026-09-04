package library

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"recommend-radio/backend-go/internal/bili"
	"recommend-radio/backend-go/internal/platform/model"
	"recommend-radio/backend-go/internal/platform/respond"
)

type Service struct {
	db *gorm.DB
}

func NewService(db *gorm.DB) *Service {
	return &Service{db: db}
}

func (s *Service) EnsureLegacyUser(ctx context.Context) error {
	now := time.Now().UTC()
	user := model.AppUser{
		ID:          model.LegacyOwnerUserID,
		DisplayName: "Legacy Owner",
		Role:        "admin",
		Status:      "active",
		CreatedAt:   now,
		UpdatedAt:   now,
	}
	return s.db.WithContext(ctx).Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "id"}},
		DoUpdates: clause.AssignmentColumns([]string{"updated_at"}),
	}).Create(&user).Error
}

func (s *Service) UpsertTrack(ctx context.Context, tx *gorm.DB, track model.Track) error {
	if tx == nil {
		tx = s.db
	}
	now := time.Now().UTC()
	if track.TrackID == "" {
		track.TrackID = bili.MakeTrackID(track.BVID, track.CID)
	}
	if track.Source == "" {
		track.Source = "bili"
	}
	track.UpdatedAt = now
	return tx.WithContext(ctx).Clauses(clause.OnConflict{
		Columns: []clause.Column{{Name: "track_id"}},
		DoUpdates: clause.AssignmentColumns([]string{
			"bvid", "cid", "title", "owner", "owner_mid", "cover", "duration",
			"play_count", "published_at", "page", "page_title", "source", "raw_json", "updated_at",
		}),
	}).Create(&track).Error
}

func (s *Service) GetTrack(ctx context.Context, trackID string) (model.Track, error) {
	var track model.Track
	err := s.db.WithContext(ctx).Where("track_id = ?", trackID).First(&track).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return model.Track{}, respond.NotFound("Track not found: " + trackID)
	}
	return track, err
}

func (s *Service) AddRecent(ctx context.Context, userID string, track model.Track, positionMS int, listenMS int, completed bool) error {
	now := time.Now().UTC()
	return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := s.UpsertTrack(ctx, tx, track); err != nil {
			return err
		}
		recent := model.Recent{
			UserID: userID, TrackID: track.TrackID, LastPlayedAt: now,
			PlayCount: 1, PositionMS: max(positionMS, 0), ListenMS: max(listenMS, 0), Completed: completed,
		}
		return tx.Clauses(clause.OnConflict{
			Columns: []clause.Column{{Name: "user_id"}, {Name: "track_id"}},
			DoUpdates: clause.Assignments(map[string]any{
				"last_played_at": recent.LastPlayedAt,
				"play_count":     gorm.Expr("play_count + 1"),
				"position_ms":    recent.PositionMS,
				"listen_ms":      gorm.Expr("GREATEST(listen_ms, ?)", recent.ListenMS),
				"completed":      recent.Completed,
			}),
		}).Create(&recent).Error
	})
}

func (s *Service) ListRecent(ctx context.Context, userID string, limit int) ([]model.Track, error) {
	limit = clamp(limit, 1, 200)
	var rows []model.Recent
	if err := s.db.WithContext(ctx).Preload("Track").
		Where("user_id = ?", userID).
		Order("last_played_at DESC").
		Limit(limit).
		Find(&rows).Error; err != nil {
		return nil, err
	}
	out := make([]model.Track, 0, len(rows))
	for _, row := range rows {
		track := row.Track
		out = append(out, track)
	}
	return out, nil
}

func (s *Service) ClearRecent(ctx context.Context, userID string) error {
	return s.db.WithContext(ctx).Where("user_id = ?", userID).Delete(&model.Recent{}).Error
}

func (s *Service) RemoveRecent(ctx context.Context, userID string, bvid string, cid *int64) error {
	trackID := bili.MakeTrackID(bvid, cid)
	return s.db.WithContext(ctx).Where("user_id = ? AND track_id = ?", userID, trackID).Delete(&model.Recent{}).Error
}

func (s *Service) AddLike(ctx context.Context, userID string, track model.Track) error {
	now := time.Now().UTC()
	return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := s.UpsertTrack(ctx, tx, track); err != nil {
			return err
		}
		like := model.Like{UserID: userID, TrackID: track.TrackID, CreatedAt: now}
		return tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&like).Error
	})
}

func (s *Service) ListLikes(ctx context.Context, userID string) ([]model.Track, error) {
	var rows []model.Like
	if err := s.db.WithContext(ctx).Preload("Track").
		Where("user_id = ?", userID).
		Order("created_at DESC").
		Find(&rows).Error; err != nil {
		return nil, err
	}
	out := make([]model.Track, 0, len(rows))
	for _, row := range rows {
		out = append(out, row.Track)
	}
	return out, nil
}

func (s *Service) RemoveLike(ctx context.Context, userID string, bvid string, cid *int64) (int64, error) {
	trackID := bili.MakeTrackID(bvid, cid)
	result := s.db.WithContext(ctx).Where("user_id = ? AND track_id = ?", userID, trackID).Delete(&model.Like{})
	return result.RowsAffected, result.Error
}

func (s *Service) SaveReview(ctx context.Context, userID string, track model.Track, rating int, mood string, note string) (map[string]any, error) {
	now := time.Now().UTC()
	if rating < 0 || rating > 5 {
		return nil, respond.BadRequest("rating must be between 0 and 5")
	}
	err := s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := s.UpsertTrack(ctx, tx, track); err != nil {
			return err
		}
		review := model.TrackReview{
			UserID: userID, TrackID: track.TrackID, Rating: rating, Mood: mood, Note: note,
			Visibility: "private", CreatedAt: now, UpdatedAt: now,
		}
		return tx.Clauses(clause.OnConflict{
			Columns: []clause.Column{{Name: "user_id"}, {Name: "track_id"}},
			DoUpdates: clause.Assignments(map[string]any{
				"rating": rating, "mood": mood, "note": note, "visibility": "private", "updated_at": now,
			}),
		}).Create(&review).Error
	})
	if err != nil {
		return nil, err
	}
	return s.GetReview(ctx, userID, track.BVID, track.CID)
}

func (s *Service) GetReview(ctx context.Context, userID string, bvid string, cid *int64) (map[string]any, error) {
	trackID := bili.MakeTrackID(bvid, cid)
	var review model.TrackReview
	err := s.db.WithContext(ctx).Preload("Track").
		Where("user_id = ? AND track_id = ?", userID, trackID).
		First(&review).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return reviewPayload(review), nil
}

func (s *Service) DeleteReview(ctx context.Context, userID string, bvid string, cid *int64) error {
	trackID := bili.MakeTrackID(bvid, cid)
	return s.db.WithContext(ctx).Where("user_id = ? AND track_id = ?", userID, trackID).Delete(&model.TrackReview{}).Error
}

func (s *Service) ListPlaylists(ctx context.Context, userID string) ([]map[string]any, error) {
	var playlists []model.Playlist
	if err := s.db.WithContext(ctx).Where("user_id = ?", userID).Order("created_at DESC").Find(&playlists).Error; err != nil {
		return nil, err
	}
	out := make([]map[string]any, 0, len(playlists))
	for _, playlist := range playlists {
		tracks, err := s.playlistTracks(ctx, userID, playlist.ID)
		if err != nil {
			return nil, err
		}
		out = append(out, playlistPayload(playlist, tracks))
	}
	return out, nil
}

func (s *Service) CreatePlaylist(ctx context.Context, userID string, name string, tracks []model.Track, sourceType string, sourceBVID *string) (map[string]any, error) {
	if name == "" {
		return nil, respond.BadRequest("name is required")
	}
	if sourceType == "" {
		sourceType = "user-created"
	}
	now := time.Now().UTC()
	playlist := model.Playlist{
		UserID: userID, ID: newID("playlist"), Name: name, SourceType: sourceType,
		SourceBVID: sourceBVID, CreatedAt: now, UpdatedAt: now,
	}
	err := s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&playlist).Error; err != nil {
			return err
		}
		return s.replacePlaylistItems(ctx, tx, userID, playlist.ID, tracks)
	})
	if err != nil {
		return nil, err
	}
	storedTracks, err := s.playlistTracks(ctx, userID, playlist.ID)
	if err != nil {
		return nil, err
	}
	return playlistPayload(playlist, storedTracks), nil
}

func (s *Service) GetPlaylist(ctx context.Context, userID string, playlistID string) (map[string]any, error) {
	var playlist model.Playlist
	err := s.db.WithContext(ctx).Where("user_id = ? AND id = ?", userID, playlistID).First(&playlist).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, respond.NotFound("Playlist not found")
	}
	if err != nil {
		return nil, err
	}
	tracks, err := s.playlistTracks(ctx, userID, playlistID)
	if err != nil {
		return nil, err
	}
	return playlistPayload(playlist, tracks), nil
}

func (s *Service) UpdatePlaylist(ctx context.Context, userID string, playlistID string, name string) (map[string]any, error) {
	if name == "" {
		return nil, respond.BadRequest("name is required")
	}
	if err := s.db.WithContext(ctx).Model(&model.Playlist{}).
		Where("user_id = ? AND id = ?", userID, playlistID).
		Updates(map[string]any{"name": name, "updated_at": time.Now().UTC()}).Error; err != nil {
		return nil, err
	}
	return s.GetPlaylist(ctx, userID, playlistID)
}

func (s *Service) DeletePlaylist(ctx context.Context, userID string, playlistID string) error {
	return s.db.WithContext(ctx).Where("user_id = ? AND id = ?", userID, playlistID).Delete(&model.Playlist{}).Error
}

func (s *Service) ReplacePlaylistItems(ctx context.Context, userID string, playlistID string, tracks []model.Track) (map[string]any, error) {
	err := s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		return s.replacePlaylistItems(ctx, tx, userID, playlistID, tracks)
	})
	if err != nil {
		return nil, err
	}
	return s.GetPlaylist(ctx, userID, playlistID)
}

func (s *Service) AddPlaylistItems(ctx context.Context, userID string, playlistID string, tracks []model.Track) error {
	return s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		now := time.Now().UTC()
		var count int64
		if err := tx.Model(&model.PlaylistItem{}).Where("user_id = ? AND playlist_id = ?", userID, playlistID).Count(&count).Error; err != nil {
			return err
		}
		for index, track := range tracks {
			if err := s.UpsertTrack(ctx, tx, track); err != nil {
				return err
			}
			item := model.PlaylistItem{
				UserID: userID, PlaylistID: playlistID, TrackID: track.TrackID,
				Position: int(count) + index, AddedAt: now,
			}
			if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&item).Error; err != nil {
				return err
			}
		}
		return tx.Model(&model.Playlist{}).Where("user_id = ? AND id = ?", userID, playlistID).Update("updated_at", now).Error
	})
}

func (s *Service) GetQueue(ctx context.Context, userID string) (map[string]any, error) {
	var state model.PlayerQueueState
	if err := s.db.WithContext(ctx).Where("user_id = ?", userID).First(&state).Error; err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, err
	}
	var items []model.PlayerQueueItem
	if err := s.db.WithContext(ctx).Preload("Track").Where("user_id = ?", userID).Order("position ASC").Find(&items).Error; err != nil {
		return nil, err
	}
	queue := make([]model.Track, 0, len(items))
	for _, item := range items {
		queue = append(queue, item.Track)
	}
	index := state.CurrentIndex
	if len(queue) == 0 {
		index = -1
	}
	mode := state.PlayMode
	if mode == "" {
		mode = "order"
	}
	return map[string]any{"queue": queue, "currentIndex": index, "playMode": mode, "updatedAt": state.UpdatedAt}, nil
}

func (s *Service) SaveQueue(ctx context.Context, userID string, tracks []model.Track, currentIndex int, playMode string) (map[string]any, error) {
	if playMode == "" {
		playMode = "order"
	}
	now := time.Now().UTC()
	err := s.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Where("user_id = ?", userID).Delete(&model.PlayerQueueItem{}).Error; err != nil {
			return err
		}
		for index, track := range tracks {
			if err := s.UpsertTrack(ctx, tx, track); err != nil {
				return err
			}
			if err := tx.Create(&model.PlayerQueueItem{UserID: userID, Position: index, TrackID: track.TrackID, AddedAt: now}).Error; err != nil {
				return err
			}
		}
		state := model.PlayerQueueState{UserID: userID, CurrentIndex: currentIndex, PlayMode: playMode, UpdatedAt: now}
		return tx.Clauses(clause.OnConflict{
			Columns:   []clause.Column{{Name: "user_id"}},
			DoUpdates: clause.AssignmentColumns([]string{"current_index", "play_mode", "updated_at"}),
		}).Create(&state).Error
	})
	if err != nil {
		return nil, err
	}
	return s.GetQueue(ctx, userID)
}

func (s *Service) replacePlaylistItems(ctx context.Context, tx *gorm.DB, userID string, playlistID string, tracks []model.Track) error {
	now := time.Now().UTC()
	if err := tx.Where("user_id = ? AND playlist_id = ?", userID, playlistID).Delete(&model.PlaylistItem{}).Error; err != nil {
		return err
	}
	for index, track := range tracks {
		if err := s.UpsertTrack(ctx, tx, track); err != nil {
			return err
		}
		if err := tx.Create(&model.PlaylistItem{UserID: userID, PlaylistID: playlistID, TrackID: track.TrackID, Position: index, AddedAt: now}).Error; err != nil {
			return err
		}
	}
	return tx.Model(&model.Playlist{}).Where("user_id = ? AND id = ?", userID, playlistID).Update("updated_at", now).Error
}

func (s *Service) playlistTracks(ctx context.Context, userID string, playlistID string) ([]model.Track, error) {
	var items []model.PlaylistItem
	if err := s.db.WithContext(ctx).Preload("Track").Where("user_id = ? AND playlist_id = ?", userID, playlistID).Order("position ASC").Find(&items).Error; err != nil {
		return nil, err
	}
	tracks := make([]model.Track, 0, len(items))
	for _, item := range items {
		tracks = append(tracks, item.Track)
	}
	return tracks, nil
}

func reviewPayload(review model.TrackReview) map[string]any {
	return map[string]any{
		"trackId":    review.TrackID,
		"bvid":       review.Track.BVID,
		"cid":        review.Track.CID,
		"rating":     review.Rating,
		"mood":       review.Mood,
		"note":       review.Note,
		"visibility": review.Visibility,
		"createdAt":  review.CreatedAt,
		"updatedAt":  review.UpdatedAt,
	}
}

func playlistPayload(playlist model.Playlist, tracks []model.Track) map[string]any {
	return map[string]any{
		"id": playlist.ID, "name": playlist.Name, "cover": playlist.Cover,
		"sourceType": playlist.SourceType, "sourceBvid": playlist.SourceBVID,
		"tracks": tracks, "createdAt": playlist.CreatedAt, "updatedAt": playlist.UpdatedAt,
	}
}

func newID(prefix string) string {
	var bytes [8]byte
	_, _ = rand.Read(bytes[:])
	return prefix + "-" + hex.EncodeToString(bytes[:])
}

func clamp(value int, minValue int, maxValue int) int {
	if value < minValue {
		return minValue
	}
	if value > maxValue {
		return maxValue
	}
	return value
}

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}
