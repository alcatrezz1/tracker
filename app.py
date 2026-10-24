"""python app.py — dashboard; python app.py --collect — one collection for a scheduler."""
import argparse
import logging
import time
import webbrowser
from radar.config import DATA
from radar.db import Database
from radar.service import Collector
from radar.server import make_server


def main():
    parser = argparse.ArgumentParser(description="Game Trend Radar — Roblox & Steam")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-auto", action="store_true", help="Skip scheduler during this session")
    parser.add_argument("--collect", action="store_true", help="Collect once and exit")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    logging.basicConfig(filename=DATA / "server.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    db = Database()
    collector = Collector(db)
    if args.collect:
        if not collector.start():
            print("Collection already running in another process.")
            raise SystemExit(3)
        while collector.status()["running"]:
            time.sleep(.5)
        state = collector.status()
        print(f"Saved: {state['saved']}; errors: {len(state['errors'])}")
        raise SystemExit(0 if sum(state["saved"].values()) and not state["errors"] else 2)
    try:
        server = make_server(db, collector, args.port)
    except OSError as exc:
        print(f"Cannot open port {args.port}: {exc}. Try --port 8766")
        raise SystemExit(1)
    if not args.no_auto:
        collector.schedule()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Game Trend Radar: {url}\nKeep this window open. Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
