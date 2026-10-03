-- Mark I memory, Phase 8 deep self-analysis (design: Documentation/2026/10/05/phase8_self_analysis_design.md).
CREATE TABLE IF NOT EXISTS self_analyses (      -- one report per run; period = quarter end day (quarterly) or day@time (on request)
  id INTEGER PRIMARY KEY, period TEXT NOT NULL, kind TEXT NOT NULL, model TEXT, status TEXT NOT NULL, summary TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS self_claims (        -- section: evidence | inference | uncertain | speculation
  id INTEGER PRIMARY KEY, analysis_id INTEGER NOT NULL REFERENCES self_analyses(id), section TEXT NOT NULL, text TEXT NOT NULL,
  confidence REAL NOT NULL, blind_spot INTEGER NOT NULL DEFAULT 0, counter_events TEXT, status TEXT NOT NULL DEFAULT 'active',
  item_id INTEGER REFERENCES model_items(id));  -- item_id: the personal-model item this claim became
CREATE TABLE IF NOT EXISTS self_claim_evidence (claim_id INTEGER NOT NULL REFERENCES self_claims(id),
  event_id INTEGER NOT NULL REFERENCES events(id), PRIMARY KEY(claim_id, event_id));
