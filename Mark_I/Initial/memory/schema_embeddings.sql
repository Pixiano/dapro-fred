-- One vector per (event, embedding model). A model swap just embeds again; old vectors stay until cleared.
CREATE TABLE IF NOT EXISTS embeddings (
  event_id INTEGER NOT NULL REFERENCES events(id), model TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY(event_id, model));
