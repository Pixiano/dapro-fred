-- Mark I memory, Phase 7 monthly + yearly consolidation (design: Documentation/2026/10/04/phase7_monthly_yearly_design.md).
CREATE TABLE IF NOT EXISTS chapters (         -- named periods of life; end_day NULL = still open
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT, description TEXT,
  source TEXT NOT NULL DEFAULT 'model', updated_at TEXT NOT NULL);   -- source: model | user (user chapters are locked)
CREATE TABLE IF NOT EXISTS chapter_log (id INTEGER PRIMARY KEY, chapter_id INTEGER NOT NULL REFERENCES chapters(id),
  period TEXT, change TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turning_points (
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, title TEXT NOT NULL, description TEXT, kind TEXT, period TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turning_point_evidence (turning_point_id INTEGER NOT NULL REFERENCES turning_points(id),
  event_id INTEGER NOT NULL REFERENCES events(id), PRIMARY KEY(turning_point_id, event_id));
CREATE TABLE IF NOT EXISTS period_runs (      -- kind monthly | yearly; period = YYYY-MM or the anchor day
  kind TEXT NOT NULL, period TEXT NOT NULL, model TEXT, status TEXT NOT NULL, detail TEXT, finished_at TEXT NOT NULL,
  PRIMARY KEY(kind, period));
CREATE TABLE IF NOT EXISTS period_digests (   -- kind monthly | yearly | yearly_draft
  kind TEXT NOT NULL, period TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind, period));
CREATE TABLE IF NOT EXISTS year_notes (       -- one per month of a yearly window, written by the month passes
  window TEXT NOT NULL, month TEXT NOT NULL, note TEXT NOT NULL, PRIMARY KEY(window, month));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- private DB only (yearly anchor lives here)
