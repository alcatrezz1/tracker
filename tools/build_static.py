"""Build the read-only GitHub Pages copy of the dashboard.

python tools/build_static.py --out site [--keep-days 35] [--interval-minutes 60]

Reads data/radar.sqlite3, prunes old snapshots, and writes:
  site/index.html, app.js, style.css   copied from web/, static mode switched on
  site/data/dashboard.json             same payload as /api/dashboard
  site/data/status.json                last collection run, like /api/status
  site/data/history/<key>.json         per-game history, like /api/history
  site/data/export.csv                 same table as /api/export.csv
"""
import argparse
import csv
import io
import json
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.analysis import dashboard  # noqa: E402
from radar.config import DATA  # noqa: E402
from radar.db import Database  # noqa: E402

HISTORY_DAYS = 30  # longest period the history chart offers


def file_key(key):
    # Must match fileKey() in web/app.js.
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def prune(path, keep_days):
    """Drop snapshots older than keep_days, keeping each game's latest one, then compact."""
    cutoff = time.time() - keep_days * 86400
    connection = sqlite3.connect(path)
    try:
        removed = connection.execute(
            """DELETE FROM snapshots WHERE ts < ? AND id NOT IN (
                 SELECT s.id FROM snapshots s JOIN (SELECT game_key, MAX(ts) ts FROM snapshots GROUP BY game_key) x
                 ON s.game_key = x.game_key AND s.ts = x.ts)""", (cutoff,)).rowcount
        connection.execute("DELETE FROM cache WHERE ts < ?", (time.time() - 7 * 86400,))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
    finally:
        connection.close()
    return removed


def csv_export(data):
    # Mirrors /api/export.csv in radar/server.py.
    stream = io.StringIO(newline="")
    columns = ["key", "name", "platform", "upcoming", "date", "date_is_proxy", "ccu", "measured_at_utc", "growth_24h_pct",
               "growth_24h_delta", "actual_window_hours", "wishlist_rank", "reviews", "positive_pct", "score", "stale",
               "mechanics", "url", "note"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for g in data["games"]:
        daily = g["growth"]["24h"] or {}
        values = {"key": g["key"], "name": g["name"], "platform": g["platform"], "upcoming": g["upcoming"],
                  "date": g["effective_date"], "date_is_proxy": g["date_is_proxy"], "ccu": g["latest"].get("ccu"),
                  "measured_at_utc": datetime.fromtimestamp(g["latest"]["ts"], timezone.utc).isoformat() if g["latest"].get("ts") else None,
                  "growth_24h_pct": daily.get("pct"), "growth_24h_delta": daily.get("delta"), "actual_window_hours": daily.get("hours"),
                  "wishlist_rank": g["latest"].get("wishlist_rank"), "reviews": g["latest"].get("reviews"),
                  "positive_pct": g["latest"].get("positive_pct"), "score": g["score"], "stale": g["stale"],
                  "mechanics": ", ".join(m["label"] for m in g["mechanics"]), "url": g["url"], "note": g["note"]}
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in values.items()})
    return stream.getvalue().encode("utf-8-sig")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site")
    parser.add_argument("--db", default=str(DATA / "radar.sqlite3"))
    parser.add_argument("--keep-days", type=int, default=35)
    parser.add_argument("--interval-minutes", type=int, default=60, help="How often the scheduled workflow runs")
    args = parser.parse_args()

    if not Path(args.db).exists():
        raise SystemExit(f"No database at {args.db}")
    removed = prune(args.db, args.keep_days)
    db = Database(args.db)
    data = dashboard(db)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "history").mkdir(parents=True)

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    html = html.replace("<html lang=\"ru\">", "<html lang=\"ru\" data-static=\"1\">", 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    for name in ("app.js", "style.css"):
        shutil.copy2(ROOT / "web" / name, out / name)
    (out / ".nojekyll").touch()

    write_json(out / "data" / "dashboard.json", data)
    for game in data["games"]:
        write_json(out / "data" / "history" / f"{file_key(game['key'])}.json", db.history(game["key"], HISTORY_DAYS))

    runs = db.runs()
    last = runs[0] if runs else None
    result = last["result"] if last else {}
    status = {"saved": {"roblox": 0, "steam": 0}, "errors": [], "feeds": [], **result, "running": False,
              "message": result.get("message") or "Данных ещё нет",
              "finished": (last or {}).get("finished"),
              "next_scan": ((last or {}).get("finished") or time.time()) + args.interval_minutes * 60,
              "generated_at": data["generated_at"]}
    write_json(out / "data" / "status.json", status)
    (out / "data" / "export.csv").write_bytes(csv_export(data))

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Built {out}: {len(data['games'])} games, {size / 1e6:.1f} MB, pruned {removed} old snapshots")


if __name__ == "__main__":
    main()
