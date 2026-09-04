CREATE TABLE IF NOT EXISTS auth_qr_sessions (
  user_id varchar(191) NOT NULL,
  qrcode_key varchar(191) NOT NULL,
  url text NOT NULL,
  status varchar(32) NOT NULL DEFAULT 'waiting',
  message text NULL,
  created_at datetime(3) NOT NULL,
  updated_at datetime(3) NOT NULL,
  expires_at datetime(3) NULL,
  PRIMARY KEY (user_id, qrcode_key),
  CONSTRAINT fk_auth_qr_sessions_user FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
  KEY idx_auth_qr_sessions_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
