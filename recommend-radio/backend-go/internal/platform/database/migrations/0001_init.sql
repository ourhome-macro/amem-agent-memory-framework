CREATE TABLE IF NOT EXISTS app_users (
  id varchar(191) PRIMARY KEY,
  display_name varchar(255) NOT NULL,
  email varchar(255) NULL,
  role varchar(32) NOT NULL DEFAULT 'user',
  status varchar(32) NOT NULL DEFAULT 'active',
  created_at datetime(3) NOT NULL,
  updated_at datetime(3) NOT NULL,
  last_login_at datetime(3) NULL,
  UNIQUE KEY idx_app_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_sessions (
  token_hash varchar(191) PRIMARY KEY,
  user_id varchar(191) NOT NULL,
  csrf_token varchar(191) NOT NULL,
  created_at datetime(3) NOT NULL,
  expires_at datetime(3) NOT NULL,
  last_seen_at datetime(3) NOT NULL,
  revoked_at datetime(3) NULL,
  user_agent_hash varchar(191) NULL,
  CONSTRAINT fk_app_sessions_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  KEY idx_app_sessions_user_expires (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bili_accounts (
  user_id varchar(191) NOT NULL,
  provider varchar(32) NOT NULL DEFAULT 'bilibili',
  cookie_encrypted text NULL,
  refresh_token_encrypted text NULL,
  user_mid bigint NULL,
  user_name varchar(255) NULL,
  user_face text NULL,
  cookie_updated_at datetime(3) NULL,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, provider),
  CONSTRAINT fk_bili_accounts_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tracks (
  track_id varchar(191) PRIMARY KEY,
  bvid varchar(32) NOT NULL,
  cid bigint NULL,
  title varchar(512) NOT NULL,
  owner varchar(255) NOT NULL DEFAULT '',
  owner_mid bigint NULL,
  cover text NULL,
  duration int NOT NULL DEFAULT 0,
  play_count bigint NOT NULL DEFAULT 0,
  published_at varchar(64) NULL,
  page int NULL,
  page_title varchar(512) NULL,
  source varchar(64) NOT NULL DEFAULT 'bili',
  raw_json json NULL,
  updated_at datetime(3) NOT NULL,
  KEY idx_tracks_bvid_cid (bvid, cid),
  KEY idx_tracks_owner_mid (owner_mid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS likes (
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  created_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, track_id),
  CONSTRAINT fk_likes_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_likes_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_likes_created_at (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recent (
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  last_played_at datetime(3) NOT NULL,
  play_count int NOT NULL DEFAULT 1,
  position_ms int NOT NULL DEFAULT 0,
  listen_ms int NOT NULL DEFAULT 0,
  completed tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, track_id),
  CONSTRAINT fk_recent_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_recent_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_recent_last_played_at (user_id, last_played_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS track_reviews (
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  rating int NOT NULL DEFAULT 0,
  mood varchar(128) NOT NULL DEFAULT '',
  note text NOT NULL,
  visibility varchar(32) NOT NULL DEFAULT 'private',
  created_at datetime(3) NOT NULL,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, track_id),
  CONSTRAINT fk_track_reviews_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_track_reviews_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_track_reviews_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playlists (
  user_id varchar(191) NOT NULL,
  id varchar(191) NOT NULL,
  name varchar(255) NOT NULL,
  cover text NULL,
  source_type varchar(64) NOT NULL DEFAULT 'user-created',
  source_bvid varchar(32) NULL,
  created_at datetime(3) NOT NULL,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, id),
  CONSTRAINT fk_playlists_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  KEY idx_playlists_created_at (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playlist_items (
  user_id varchar(191) NOT NULL,
  playlist_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  position int NOT NULL,
  added_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, playlist_id, track_id),
  CONSTRAINT fk_playlist_items_playlist FOREIGN KEY (user_id, playlist_id) REFERENCES playlists(user_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_playlist_items_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_playlist_items_playlist_position (user_id, playlist_id, position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playback_sessions (
  user_id varchar(191) NOT NULL,
  session_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  started_at datetime(3) NOT NULL,
  ended_at datetime(3) NULL,
  last_position_ms int NOT NULL DEFAULT 0,
  listen_ms int NOT NULL DEFAULT 0,
  completed tinyint(1) NOT NULL DEFAULT 0,
  skipped tinyint(1) NOT NULL DEFAULT 0,
  last_event varchar(64) NOT NULL,
  PRIMARY KEY (user_id, session_id),
  CONSTRAINT fk_playback_sessions_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_playback_sessions_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_playback_sessions_track_started (user_id, track_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playback_recent (
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  last_played_at datetime(3) NOT NULL,
  position_ms int NOT NULL DEFAULT 0,
  listen_ms int NOT NULL DEFAULT 0,
  completed tinyint(1) NOT NULL DEFAULT 0,
  skipped tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, track_id),
  CONSTRAINT fk_playback_recent_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_playback_recent_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_playback_recent_last_played_at (user_id, last_played_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS playback_events (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  user_id varchar(191) NOT NULL,
  session_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  event varchar(64) NOT NULL,
  position_ms int NOT NULL DEFAULT 0,
  listen_ms int NOT NULL DEFAULT 0,
  completed tinyint(1) NOT NULL DEFAULT 0,
  created_at datetime(3) NOT NULL,
  CONSTRAINT fk_playback_events_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_playback_events_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_playback_events_track_created (user_id, track_id, created_at),
  KEY idx_playback_events_session_id (user_id, session_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recommendation_events (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  event varchar(64) NOT NULL,
  scene varchar(64) NOT NULL,
  source varchar(64) NOT NULL,
  reason varchar(512) NOT NULL,
  score double NOT NULL DEFAULT 0,
  created_at datetime(3) NOT NULL,
  CONSTRAINT fk_recommendation_events_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_recommendation_events_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_recommendation_events_user_created (user_id, created_at),
  KEY idx_recommendation_events_track_event (user_id, track_id, event, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recommendation_history (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  user_id varchar(191) NOT NULL,
  track_id varchar(191) NOT NULL,
  recommended_at datetime(3) NOT NULL,
  clicked tinyint(1) NOT NULL DEFAULT 0,
  played_seconds int NOT NULL DEFAULT 0,
  completed tinyint(1) NOT NULL DEFAULT 0,
  liked tinyint(1) NOT NULL DEFAULT 0,
  skipped tinyint(1) NOT NULL DEFAULT 0,
  scene varchar(64) NOT NULL,
  source varchar(64) NOT NULL,
  score double NOT NULL DEFAULT 0,
  reason varchar(512) NOT NULL,
  CONSTRAINT fk_recommendation_history_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_recommendation_history_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  KEY idx_recommendation_history_user_recent (user_id, recommended_at),
  KEY idx_recommendation_history_feedback (user_id, track_id, skipped, completed, liked)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recommendation_traces (
  trace_id varchar(191) PRIMARY KEY,
  user_id varchar(191) NOT NULL,
  scene varchar(64) NOT NULL,
  profile_trace_id varchar(191) NOT NULL DEFAULT '',
  agent_trace_id varchar(191) NOT NULL DEFAULT '',
  payload_json json NOT NULL,
  created_at datetime(3) NOT NULL,
  CONSTRAINT fk_recommendation_traces_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  KEY idx_recommendation_traces_user_scene_created (user_id, scene, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS settings (
  user_id varchar(191) NOT NULL,
  `key` varchar(191) NOT NULL,
  value text NOT NULL,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, `key`),
  CONSTRAINT fk_settings_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS player_queue_state (
  user_id varchar(191) PRIMARY KEY,
  current_index int NOT NULL DEFAULT -1,
  play_mode varchar(32) NOT NULL DEFAULT 'order',
  updated_at datetime(3) NOT NULL,
  CONSTRAINT fk_player_queue_state_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS player_queue_items (
  user_id varchar(191) NOT NULL,
  position int NOT NULL,
  track_id varchar(191) NOT NULL,
  added_at datetime(3) NOT NULL,
  PRIMARY KEY (user_id, position),
  CONSTRAINT fk_player_queue_items_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  CONSTRAINT fk_player_queue_items_track FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS behavior_events (
  event_id varchar(191) PRIMARY KEY,
  user_id varchar(191) NOT NULL,
  event_type varchar(64) NOT NULL,
  track_id varchar(191) NULL,
  scene varchar(64) NOT NULL DEFAULT '',
  payload_json json NOT NULL,
  created_at datetime(3) NOT NULL,
  CONSTRAINT fk_behavior_events_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  KEY idx_behavior_events_user_created (user_id, created_at),
  KEY idx_behavior_events_type_created (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS outbox_events (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  event_id varchar(191) NOT NULL,
  topic varchar(191) NOT NULL,
  payload_json json NOT NULL,
  status varchar(32) NOT NULL DEFAULT 'pending',
  attempts int NOT NULL DEFAULT 0,
  last_error text NULL,
  next_retry_at datetime(3) NOT NULL,
  published_at datetime(3) NULL,
  created_at datetime(3) NOT NULL,
  updated_at datetime(3) NOT NULL,
  UNIQUE KEY idx_outbox_event_id (event_id),
  KEY idx_outbox_status_retry (status, next_retry_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS consumer_idempotency (
  consumer_name varchar(191) NOT NULL,
  event_id varchar(191) NOT NULL,
  processed_at datetime(3) NOT NULL,
  PRIMARY KEY (consumer_name, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
