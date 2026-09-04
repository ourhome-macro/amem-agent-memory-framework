CREATE TABLE IF NOT EXISTS search_intent_stats (
  query varchar(191) NOT NULL,
  scene varchar(64) NOT NULL DEFAULT '',
  source varchar(64) NOT NULL DEFAULT '',
  searched_count bigint NOT NULL DEFAULT 0,
  prefilter_passed bigint NOT NULL DEFAULT 0,
  recommended_count bigint NOT NULL DEFAULT 0,
  clicked_count bigint NOT NULL DEFAULT 0,
  completed_count bigint NOT NULL DEFAULT 0,
  skipped_count bigint NOT NULL DEFAULT 0,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (query, scene, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS recommendation_candidates (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  trace_id varchar(191) NOT NULL,
  user_id varchar(191) NOT NULL,
  scene varchar(64) NOT NULL DEFAULT '',
  track_id varchar(191) NOT NULL,
  source_query varchar(191) NOT NULL DEFAULT '',
  source_relevance double NOT NULL DEFAULT 0,
  positive_similarity double NOT NULL DEFAULT 0,
  negative_similarity double NOT NULL DEFAULT 0,
  router_decision varchar(32) NOT NULL DEFAULT '',
  final_score double NOT NULL DEFAULT 0,
  payload_json json NOT NULL,
  created_at datetime(3) NOT NULL,
  KEY idx_recommendation_candidates_trace (trace_id),
  KEY idx_recommendation_candidates_track (track_id),
  KEY idx_recommendation_candidates_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
