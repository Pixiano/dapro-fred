-- Mark I memory: maintenance proposals (curation tools). Agents propose; code applies only what the tier allows; Vatsal confirms the rest.
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, reason TEXT, confidence REAL NOT NULL, tier TEXT NOT NULL,
  agent TEXT, run TEXT, status TEXT NOT NULL DEFAULT 'proposed',   -- proposed | applied (auto, undoable) | confirmed | rejected | undone | expired
  result TEXT, asks INTEGER NOT NULL DEFAULT 0, last_asked TEXT, reported INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS retired_aliases (  -- a wrong nickname leaves the live alias table but stays on file (undo puts it back)
  kind TEXT NOT NULL, alias TEXT NOT NULL, entity_id INTEGER NOT NULL REFERENCES entities(id), reason TEXT, at TEXT NOT NULL,
  restored INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind, alias, at));
CREATE TABLE IF NOT EXISTS maintenance_runs (day TEXT PRIMARY KEY, model TEXT, status TEXT NOT NULL, detail TEXT, summary TEXT, finished_at TEXT NOT NULL);
