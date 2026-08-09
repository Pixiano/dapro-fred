"""read_timer() reads the scheduler's own jobstore, so it has to stay
correct against that table's real shape and not disturb it."""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def make_db(tmp, rows):
    """The apscheduler_jobs table as SQLAlchemyJobStore actually creates it."""
    path = tmp / f"reminders{len(list(tmp.glob('*.sqlite')))}.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE apscheduler_jobs ("
        "id VARCHAR(191) NOT NULL PRIMARY KEY, "
        "next_run_time FLOAT, job_state BLOB NOT NULL)"
    )
    con.executemany(
        "INSERT INTO apscheduler_jobs VALUES (?, ?, ?)",
        [(i, t, b"pickled") for i, t in rows],
    )
    con.commit()
    con.close()
    return path


def read(monkeypatch, tmp, rows):
    monkeypatch.setattr(server, "SCHEDULER_DB", make_db(tmp, rows))
    return server.read_timer()


def test_no_db_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SCHEDULER_DB", tmp_path / "nope.sqlite")
    assert server.read_timer() is None


def test_no_timers_only_other_jobs(tmp_path, monkeypatch):
    now = time.time()
    rows = [("reminder_1_4", now + 60), ("workout_mon", now + 900)]
    assert read(monkeypatch, tmp_path, rows) is None


def test_soonest_timer_wins(tmp_path, monkeypatch):
    now = time.time()
    rows = [("timer_9", now + 600), ("timer_3", now + 120), ("workout_mon", now + 5)]
    left = read(monkeypatch, tmp_path, rows)
    assert 115 < left < 121, left


def test_a_fired_timer_reads_zero_not_negative(tmp_path, monkeypatch):
    """APScheduler leaves the row briefly after firing — a count-up would
    look like a stopwatch nobody started."""
    assert read(monkeypatch, tmp_path, [("timer_1", time.time() - 30)]) == 0.0


def test_paused_job_with_null_next_run_time_is_ignored(tmp_path, monkeypatch):
    rows = [("timer_1", None), ("timer_2", time.time() + 45)]
    left = read(monkeypatch, tmp_path, rows)
    assert 40 < left < 46, left
    assert read(monkeypatch, tmp_path, [("timer_1", None)]) is None


def test_it_opens_read_only(tmp_path, monkeypatch):
    """The scheduler is actively using this file; the HUD must not be able
    to write it or hold a lock on it."""
    path = make_db(tmp_path, [("timer_1", time.time() + 60)])
    monkeypatch.setattr(server, "SCHEDULER_DB", path)
    assert server.read_timer() is not None

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.execute("DELETE FROM apscheduler_jobs")
        raise AssertionError("read_timer's connection mode allows writes")
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()


def test_a_corrupt_db_is_not_fatal(tmp_path, monkeypatch):
    """The HUD is a spectator — a bad jobstore hides the timer, it does
    not take the whole /state endpoint down."""
    bad = tmp_path / "reminders.sqlite"
    bad.write_bytes(b"not a database")
    monkeypatch.setattr(server, "SCHEDULER_DB", bad)
    assert server.read_timer() is None
