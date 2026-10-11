-- Mark I memory, Phase 6 personal model (spec sections 12, 36; design: Documentation/2026/10/04).
CREATE TABLE IF NOT EXISTS model_items (
  id INTEGER PRIMARY KEY, category TEXT NOT NULL, text TEXT NOT NULL, confidence REAL NOT NULL,
  tier TEXT NOT NULL, source TEXT NOT NULL,                 -- source: inference | analysis | user (user items are locked)
  inference_id INTEGER REFERENCES inferences(id), status TEXT NOT NULL DEFAULT 'active',  -- active | demoted | removed
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_item_log (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES model_items(id),
  version INTEGER, change TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_versions (id INTEGER PRIMARY KEY, week TEXT NOT NULL, mode TEXT NOT NULL, summary TEXT,
  built_at TEXT NOT NULL, UNIQUE(week, mode));
CREATE TABLE IF NOT EXISTS model_embeddings (item_id INTEGER NOT NULL REFERENCES model_items(id), model TEXT NOT NULL,
  text TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY(item_id, model));
