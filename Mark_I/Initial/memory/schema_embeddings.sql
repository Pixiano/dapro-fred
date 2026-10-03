-- One vector per (event, embedding model). A model swap just embeds again; old vectors stay until cleared.
CREATE TABLE IF NOT EXISTS embeddings (
  event_id INTEGER NOT NULL REFERENCES events(id), model TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY(event_id, model));
-- Keyword side of hybrid search (SQLite FTS5, stdlib). Names and exact terms are where vectors are weakest.
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(content);
CREATE TRIGGER IF NOT EXISTS events_fts_ins AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content); END;
INSERT INTO events_fts(rowid, content) SELECT id, content FROM events WHERE id NOT IN (SELECT rowid FROM events_fts);
