-- Phase 2: one vector per event (semantic index). Rebuildable from events at any time.
CREATE TABLE IF NOT EXISTS embeddings (
  event_id INTEGER PRIMARY KEY REFERENCES events(id), model TEXT NOT NULL, vec BLOB NOT NULL);
