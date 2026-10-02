-- Mark I memory, Phase 1 foundation (spec: FRED_Memory_Overhaul.md sections 21, 40).
-- Times are ISO-8601 UTC text. valid_to NULL = still true.
CREATE TABLE IF NOT EXISTS sources (          -- provenance: where anything came from
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(kind, name));
CREATE TABLE IF NOT EXISTS entity_states (    -- state history, never overwritten
  id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES entities(id),
  field TEXT NOT NULL, value TEXT, valid_from TEXT NOT NULL, valid_to TEXT,
  confidence REAL NOT NULL DEFAULT 1.0, source_id INTEGER REFERENCES sources(id));
CREATE INDEX IF NOT EXISTS ix_states_open ON entity_states(entity_id, field, valid_to);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL,
  entity_id INTEGER REFERENCES entities(id), content TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5, confidence REAL NOT NULL DEFAULT 1.0,
  source_id INTEGER REFERENCES sources(id), created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS daily_logs (
  day TEXT PRIMARY KEY, summary TEXT NOT NULL, updated_at TEXT NOT NULL);
