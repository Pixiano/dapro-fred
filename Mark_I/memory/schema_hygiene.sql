-- Mark I memory, hygiene: nothing is deleted. Cold events are left out of normal search; the archive keeps all.
CREATE TABLE IF NOT EXISTS cold_events (event_id INTEGER PRIMARY KEY REFERENCES events(id), since TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_recalls (event_id INTEGER PRIMARY KEY REFERENCES events(id), last_recalled TEXT NOT NULL);
