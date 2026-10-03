-- Mark I memory, Phase 5 weekly meta-agent (spec sections 11, 14.3; design: Documentation/2026/10/04).
CREATE TABLE IF NOT EXISTS inferences (
  id INTEGER PRIMARY KEY, statement TEXT NOT NULL, category TEXT NOT NULL, confidence REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL, valid_from TEXT, valid_to TEXT,
  last_reviewed_week TEXT, support_weeks INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS inference_evidence (
  inference_id INTEGER NOT NULL REFERENCES inferences(id), event_id INTEGER NOT NULL REFERENCES events(id),
  role TEXT NOT NULL, PRIMARY KEY(inference_id, event_id, role));
CREATE TABLE IF NOT EXISTS inference_alternatives (inference_id INTEGER NOT NULL REFERENCES inferences(id), text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inference_log (    -- every change, nothing is overwritten silently
  id INTEGER PRIMARY KEY, inference_id INTEGER NOT NULL REFERENCES inferences(id), week TEXT NOT NULL,
  change TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_rulings (item_id INTEGER PRIMARY KEY REFERENCES review_queue(id),
  ruling TEXT NOT NULL, week TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS weekly_runs (week TEXT PRIMARY KEY, model TEXT, status TEXT NOT NULL, detail TEXT, finished_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS weekly_digests (week TEXT PRIMARY KEY, summary TEXT NOT NULL, created_at TEXT NOT NULL);
