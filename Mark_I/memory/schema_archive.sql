-- Layer 1 raw archive index: one row per (day, stream) file, plus content-addressed blobs.
CREATE TABLE IF NOT EXISTS archive_files (
  path TEXT PRIMARY KEY, stream TEXT NOT NULL, day TEXT NOT NULL,
  records INTEGER NOT NULL, first_ts TEXT NOT NULL, last_ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archive_blobs (
  sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, bytes INTEGER NOT NULL,
  name TEXT, session TEXT, created_at TEXT NOT NULL);
