CREATE TABLE IF NOT EXISTS consumer_dlq_events (
  id bigint PRIMARY KEY AUTO_INCREMENT,
  consumer_name varchar(191) NOT NULL,
  event_id varchar(191) NOT NULL,
  topic varchar(191) NOT NULL,
  message_id varchar(191) NOT NULL DEFAULT '',
  delivery_attempt int NOT NULL DEFAULT 0,
  error_message text NOT NULL,
  payload_json json NOT NULL,
  created_at datetime(3) NOT NULL,
  UNIQUE KEY idx_consumer_dlq_consumer_event (consumer_name, event_id),
  KEY idx_consumer_dlq_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS behavior_metric_counters (
  day date NOT NULL,
  event_type varchar(64) NOT NULL,
  scene varchar(64) NOT NULL DEFAULT '',
  source varchar(64) NOT NULL DEFAULT '',
  count bigint NOT NULL DEFAULT 0,
  updated_at datetime(3) NOT NULL,
  PRIMARY KEY (day, event_type, scene, source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS async_task_events (
  event_id varchar(191) PRIMARY KEY,
  event_type varchar(64) NOT NULL,
  user_id varchar(191) NOT NULL,
  scene varchar(64) NOT NULL DEFAULT '',
  source varchar(64) NOT NULL DEFAULT '',
  payload_json json NOT NULL,
  created_at datetime(3) NOT NULL,
  KEY idx_async_task_type_created (event_type, created_at),
  KEY idx_async_task_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
