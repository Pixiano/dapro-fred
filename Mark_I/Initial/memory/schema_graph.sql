-- Mark I memory, Phase 4 temporal graph (spec sections 10, 21). valid_to NULL = still true.
CREATE TABLE IF NOT EXISTS relationships (
  id INTEGER PRIMARY KEY, subject_id INTEGER NOT NULL REFERENCES entities(id), relation TEXT NOT NULL,
  object_id INTEGER NOT NULL REFERENCES entities(id), valid_from TEXT NOT NULL, valid_to TEXT,
  confidence REAL NOT NULL DEFAULT 1.0, source_id INTEGER REFERENCES sources(id));
CREATE INDEX IF NOT EXISTS ix_rel_subject ON relationships(subject_id, relation, valid_to);
CREATE INDEX IF NOT EXISTS ix_rel_object ON relationships(object_id);
CREATE TABLE IF NOT EXISTS entity_aliases (   -- other spellings of one entity (alias = lowercase, trimmed)
  kind TEXT NOT NULL, alias TEXT NOT NULL, entity_id INTEGER NOT NULL REFERENCES entities(id),
  PRIMARY KEY(kind, alias));
CREATE TABLE IF NOT EXISTS entity_merges (    -- a merged entity is kept and marked, never deleted (reversible)
  entity_id INTEGER PRIMARY KEY REFERENCES entities(id), into_id INTEGER NOT NULL REFERENCES entities(id),
  merged_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS merge_moves (      -- what a merge moved, so unmerge can put every row back
  id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES entities(id), tbl TEXT NOT NULL, key TEXT NOT NULL, extra TEXT);
CREATE TABLE IF NOT EXISTS event_entities (   -- tags: which entities an event is about (an event can have several)
  event_id INTEGER NOT NULL REFERENCES events(id), entity_id INTEGER NOT NULL REFERENCES entities(id),
  PRIMARY KEY(event_id, entity_id));
CREATE INDEX IF NOT EXISTS ix_tag_entity ON event_entities(entity_id);
