import csv
import io
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from .analysis import dashboard
from .config import ROOT, validate_settings


def make_server(db, collector, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logging.debug(format, *args)

        def respond(self, data, status=200, content_type="application/json; charset=utf-8", headers=None):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.valid_host():
                return self.respond({"error": "Invalid Host"}, 403)
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/health":
                    return self.respond({"ok": True, "version": "1.0.0", "root": str(ROOT)})
                if path == "/api/dashboard":
                    return self.respond(dashboard(db))
                if path == "/api/status":
                    return self.respond(collector.status())
                if path == "/api/history":
                    query = parse_qs(parsed.query)
                    days = max(1, min(365, int(query.get("days", [30])[0])))
                    return self.respond(db.history(query.get("key", [""])[0], days))
                if path == "/api/export.csv":
                    data = dashboard(db)
                    stream = io.StringIO(newline="")
                    columns = ["key", "name", "platform", "upcoming", "date", "date_is_proxy", "ccu", "measured_at_utc", "growth_24h_pct", "growth_24h_delta", "actual_window_hours", "wishlist_rank", "reviews", "positive_pct", "score", "stale", "mechanics", "url", "note"]
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    from datetime import datetime, timezone
                    for g in data["games"]:
                        daily = g["growth"]["24h"] or {}
                        values = {"key": g["key"], "name": g["name"], "platform": g["platform"], "upcoming": g["upcoming"],
                                  "date": g["effective_date"], "date_is_proxy": g["date_is_proxy"], "ccu": g["latest"].get("ccu"),
                                  "measured_at_utc": datetime.fromtimestamp(g["latest"]["ts"], timezone.utc).isoformat() if g["latest"].get("ts") else None,
                                  "growth_24h_pct": daily.get("pct"), "growth_24h_delta": daily.get("delta"), "actual_window_hours": daily.get("hours"),
                                  "wishlist_rank": g["latest"].get("wishlist_rank"), "reviews": g["latest"].get("reviews"),
                                  "positive_pct": g["latest"].get("positive_pct"), "score": g["score"], "stale": g["stale"],
                                  "mechanics": ", ".join(m["label"] for m in g["mechanics"]), "url": g["url"], "note": g["note"]}
                        # Prevent spreadsheet formula execution from third-party game names.
                        writer.writerow({k: "'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in values.items()})
                    return self.respond(stream.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="game-trends.csv"'})
                files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path in files:
                    name, content_type = files[path]
                    return self.respond((ROOT / "web" / name).read_bytes(), content_type=content_type)
                self.respond({"error": "Не найдено"}, 404)
            except (ValueError, TypeError) as exc:
                logging.exception("Invalid dashboard data or request")
                self.respond({"error": str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.exception("Request failed")
                self.respond({"error": "Внутренняя ошибка. См. data/server.log"}, 500)

        def do_POST(self):
            if not self.valid_host():
                return self.respond({"error": "Invalid Host"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
                return self.respond({"error": "Invalid Origin"}, 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond({"error": "Требуется application/json"}, 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 100000:
                    return self.respond({"error": "Слишком большой запрос"}, 413)
                values = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(values, dict):
                    raise ValueError("Ожидался JSON-объект")
                if self.path == "/api/scan":
                    return self.respond({"started": collector.start()})
                if self.path == "/api/settings":
                    settings = validate_settings(values, db.settings())
                    db.settings(settings)
                    collector.next_scan = time_now()+settings["interval_minutes"]*60
                    return self.respond(settings)
                if self.path == "/api/game":
                    key = values.pop("key", "")
                    db.edit(key, values)
                    return self.respond({"ok": True})
                if self.path == "/api/add":
                    value = values.get("url", "")
                    if not isinstance(value, str) or len(value) > 1000:
                        raise ValueError("Некорректная ссылка")
                    # Validate the shape before starting a job (network resolution happens in worker).
                    import re
                    if not re.search(r"(?:roblox\.com/games/|steampowered\.com/app/|^(?:roblox|steam):)\d+", value):
                        raise ValueError("Нужна ссылка на игру Roblox / Steam или platform:ID")
                    return self.respond({"started": collector.start(added=value)})
                self.respond({"error": "Не найдено"}, 404)
            except (ValueError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                logging.exception("Mutation failed")
                self.respond({"error": "Ошибка сохранения. См. data/server.log"}, 500)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def time_now():
    import time
    return time.time()
