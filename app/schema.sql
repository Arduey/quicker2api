-- API 分发平台 建表脚本
-- 由安装向导自动执行，使用 IF NOT EXISTS，可安全重复执行。

-- 注：管理员不再是独立表，而是 users 表里 is_admin = 1 的用户。
-- 这样管理员天然拥有积分、API Key、调用记录，只是多几个管理入口。
CREATE TABLE IF NOT EXISTS users (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  email VARCHAR(190) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  nickname VARCHAR(64) NOT NULL DEFAULT '',
  points INT NOT NULL DEFAULT 0,
  api_key VARCHAR(64) NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  is_admin TINYINT NOT NULL DEFAULT 0,
  email_verified TINYINT NOT NULL DEFAULT 1,
  created_at INT NOT NULL DEFAULT 0,
  last_login_at INT NOT NULL DEFAULT 0,
  last_login_ip VARCHAR(64) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_email (email),
  KEY idx_user_admin (is_admin),
  UNIQUE KEY uk_user_apikey (api_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS apis (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  slug VARCHAR(64) NOT NULL,
  name VARCHAR(100) NOT NULL,
  description TEXT,
  action VARCHAR(200) NOT NULL DEFAULT '',
  wait_flag TINYINT NOT NULL DEFAULT 1,
  max_wait_ms INT NOT NULL DEFAULT 30000,
  txt_flag TINYINT NOT NULL DEFAULT 0,
  cost_points INT NOT NULL DEFAULT 0,
  fields_json MEDIUMTEXT,
  call_count BIGINT NOT NULL DEFAULT 0,
  success_count BIGINT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  created_at INT NOT NULL DEFAULT 0,
  updated_at INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_api_slug (slug),
  KEY idx_api_status (status, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS api_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id INT UNSIGNED NOT NULL DEFAULT 0,
  user_email VARCHAR(190) NOT NULL DEFAULT '',
  api_id INT UNSIGNED NOT NULL DEFAULT 0,
  api_slug VARCHAR(64) NOT NULL DEFAULT '',
  api_name VARCHAR(100) NOT NULL DEFAULT '',
  params_json MEDIUMTEXT,
  response_text MEDIUMTEXT,
  is_success TINYINT NOT NULL DEFAULT 0,
  cost_points INT NOT NULL DEFAULT 0,
  refunded TINYINT NOT NULL DEFAULT 0,
  http_status INT NOT NULL DEFAULT 0,
  time_cost_ms INT NOT NULL DEFAULT 0,
  error_message VARCHAR(500) NOT NULL DEFAULT '',
  ip VARCHAR(64) NOT NULL DEFAULT '',
  created_at INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_log_user (user_id, id),
  KEY idx_log_api (api_id, id),
  KEY idx_log_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS redeem_codes (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  points INT NOT NULL DEFAULT 0,
  batch VARCHAR(64) NOT NULL DEFAULT '',
  status TINYINT NOT NULL DEFAULT 0,
  used_by INT UNSIGNED NOT NULL DEFAULT 0,
  used_by_email VARCHAR(190) NOT NULL DEFAULT '',
  used_at INT NOT NULL DEFAULT 0,
  expires_at INT NOT NULL DEFAULT 0,
  note VARCHAR(200) NOT NULL DEFAULT '',
  created_at INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_redeem_code (code),
  KEY idx_redeem_batch (batch),
  KEY idx_redeem_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS email_codes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  email VARCHAR(190) NOT NULL,
  code VARCHAR(16) NOT NULL,
  purpose VARCHAR(24) NOT NULL DEFAULT 'register',
  expires_at INT NOT NULL DEFAULT 0,
  last_sent_at INT NOT NULL DEFAULT 0,
  ip VARCHAR(64) NOT NULL DEFAULT '',
  created_at INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_code_email (email, purpose, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS settings (
  k VARCHAR(64) NOT NULL,
  v MEDIUMTEXT,
  updated_at INT NOT NULL DEFAULT 0,
  PRIMARY KEY (k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
