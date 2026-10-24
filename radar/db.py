import json
import sqlite3
import time
from contextlib import contextmanager
from .config import DATA, DEFAULTS


class Database:
    def __init__(self, path=None):
        self.path = str(path or DATA / "radar.sqlite3")
        from pathlib import Path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS games (
              key TEXT PRIMARY KEY, platform TEXT NOT NULL, app_id TEXT NOT NULL,
              metadata TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
              watched INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', release_override TEXT);
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY, game_key TEXT NOT NULL REFERENCES games(key),
              ts REAL NOT NULL, metrics TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS snapshot_game_time ON snapshots(game_key, ts);
            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY, started REAL NOT NULL, finished REAL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, ts REAL NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (json.dumps(DEFAULTS),))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def settings(self, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(value),))
            return {**DEFAULTS, **json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])}

    def cache_get(self, key, ttl):
        with self.connect() as db:
            row = db.execute("SELECT * FROM cache WHERE key=? AND ts>?", (key, time.time()-ttl)).fetchone()
            return json.loads(row["value"]) if row else None

    def cache_put(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(value)))

    def save(self, meta, metrics, ts=None):
        ts = time.time() if ts is None else ts
        key = f"{meta['platform']}:{meta['app_id']}"
        with self.connect() as db:
            db.execute("""INSERT INTO games(key,platform,app_id,metadata,first_seen,last_seen)
                VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET metadata=excluded.metadata,last_seen=excluded.last_seen""",
                (key, meta["platform"], str(meta["app_id"]), json.dumps(meta), ts, ts))
            # A click on refresh must not create artificial evidence of sustained growth.
            last = db.execute("SELECT ts FROM snapshots WHERE game_key=? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
            if not last or ts-last[0] >= 600:
                db.execute("INSERT INTO snapshots(game_key,ts,metrics) VALUES (?,?,?)", (key, ts, json.dumps(metrics)))

    def games(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM games ORDER BY last_seen DESC").fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def histories(self, days=8):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM snapshots WHERE ts>=? ORDER BY ts", (time.time()-days*86400,)).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row["game_key"], []).append({"ts": row["ts"], **json.loads(row["metrics"])})
        # Keep the latest observation visible even for games no longer being scanned.
        with self.connect() as db:
            latest = db.execute("SELECT s.* FROM snapshots s JOIN (SELECT game_key, MAX(ts) ts FROM snapshots GROUP BY game_key) x ON s.game_key=x.game_key AND s.ts=x.ts").fetchall()
        for row in latest:
            if row["game_key"] not in result:
                result[row["game_key"]] = [{"ts": row["ts"], **json.loads(row["metrics"])}]
        return result

    def history(self, key, days=30):
        with self.connect() as db:
            rows = db.execute("SELECT ts,metrics FROM snapshots WHERE game_key=? AND ts>=? ORDER BY ts", (key, time.time()-days*86400)).fetchall()
        return [{"ts": row["ts"], **json.loads(row["metrics"])} for row in rows]

    def edit(self, key, values):
        allowed = {"watched", "note", "release_override"}
        if not values or set(values)-allowed:
            raise ValueError("Допустимы watched, note, release_override")
        if "watched" in values and not isinstance(values["watched"], bool):
            raise ValueError("watched: нужно true или false")
        if "note" in values and (not isinstance(values["note"], str) or len(values["note"]) > 10000):
            raise ValueError("Заметка: максимум 10000 символов")
        if values.get("release_override"):
            from datetime import date
            date.fromisoformat(values["release_override"])
            if values["release_override"] > date.today().isoformat():
                raise ValueError("Дата запуска не может быть в будущем")
        if "release_override" in values and values["release_override"] == "":
            values["release_override"] = None
        with self.connect() as db:
            found = db.execute("SELECT key FROM games WHERE key=?", (key,)).fetchone()
            if not found:
                raise ValueError("Игра не найдена")
            sql = ",".join(f"{field}=?" for field in values)
            db.execute(f"UPDATE games SET {sql} WHERE key=?", (*values.values(), key))

    def start_run(self):
        with self.connect() as db:
            return db.execute("INSERT INTO runs(started,result) VALUES (?,?)", (time.time(), '{}')).lastrowid

    def finish_run(self, run_id, result):
        with self.connect() as db:
            db.execute("UPDATE runs SET finished=?,result=? WHERE id=?", (time.time(), json.dumps(result), run_id))

    def progress_run(self, run_id, result):
        with self.connect() as db:
            db.execute("UPDATE runs SET result=? WHERE id=? AND finished IS NULL", (json.dumps(result), run_id))

    def runs(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10").fetchall()
        return [{**dict(row), "result": json.loads(row["result"])} for row in rows]
