-- Mark I memory, Phase 3 daily consolidation (spec sections 14.2, 21).
CREATE TABLE IF NOT EXISTS consolidation_runs (
  day TEXT PRIMARY KEY, model TEXT, status TEXT NOT NULL, detail TEXT, finished_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_queue (     -- contradictions + unresolved questions for later consolidation
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', source_id INTEGER REFERENCES sources(id));
