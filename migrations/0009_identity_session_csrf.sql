ALTER TABLE sessions ADD COLUMN csrf_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_sessions_user_expires
ON sessions(user_id, expires_at);
