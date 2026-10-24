import threading
import time
import copy
import logging
from .http import HttpClient
from .locking import ProcessLock
from .sources import RobloxSource, SteamSource, resolve_game


class Collector:
    def __init__(self, db):
        self.db = db
        self.client = HttpClient()
        self.lock = threading.Lock()
        self.busy_lock = threading.Lock()
        self.process_lock = ProcessLock(db.path+".lock")
        self.run_id = None
        self.last_progress_save = 0
        self.stop = threading.Event()
        self.state = {"running": False, "message": "Готов к сбору", "saved": {"roblox": 0, "steam": 0}, "errors": [], "feeds": []}
        runs = db.runs()
        self.next_scan = (runs[0]["finished"] or time.time())+db.settings()["interval_minutes"]*60 if runs else time.time()

    def status(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        if not state["running"] and self.process_lock.occupied():
            runs = self.db.runs()
            if runs:
                state = {**state, **runs[0]["result"], "running": True}
        return {**state, "next_scan": self.next_scan if self.db.settings()["auto_scan"] else None}

    def report(self, kind, message):
        with self.lock:
            if kind == "saved":
                self.state["saved"][message] += 1
            elif kind == "progress":
                self.state["message"] = message
            elif kind == "error":
                self.state["errors"].append(str(message))
            elif kind == "feed":
                self.state["feeds"].append(message)
            if self.run_id and time.time()-self.last_progress_save > 5:
                self.last_progress_save = time.time()
                self.db.progress_run(self.run_id, copy.deepcopy(self.state))
        if kind == "error":
            logging.warning("Source: %s", message)

    def start(self, added=None):
        if not self.busy_lock.acquire(blocking=False):
            return False
        if not self.process_lock.acquire():
            self.busy_lock.release()
            return False
        with self.lock:
            self.state = {"running": True, "message": "Поиск кандидатов…", "started": time.time(),
                          "saved": {"roblox": 0, "steam": 0}, "errors": [], "feeds": []}
        threading.Thread(target=self._run, args=(added,), daemon=True).start()
        return True

    def _run(self, added):
        run_id = None
        try:
            run_id = self.db.start_run()
            self.run_id = run_id
            settings = self.db.settings()
            sources = {"roblox": RobloxSource(self.client, self.db), "steam": SteamSource(self.client, self.db)}
            if added:
                platform, app_id = resolve_game(self.client, added)
                if platform == "steam":
                    if not sources[platform].collect_one(app_id, {"feeds": ["manual"]}, settings, self.report):
                        raise ValueError("В Steam поддерживаются игры; DLC и другие типы не добавлены")
                    self.report("saved", platform)
                else:
                    sources[platform].collect({**settings, "roblox_limit": 0}, [{"platform": platform, "app_id": app_id}], self.report)
                self.db.edit(f"{platform}:{app_id}", {"watched": True})
            else:
                catalog = self.db.games()
                # Prioritize every watchlist item. Limit automatic carryover to 300/platform.
                tracked = [g for g in catalog if g["watched"]]
                for platform in sources:
                    tracked += [g for g in catalog if g["platform"] == platform and not g["watched"] and g["metadata"].get("last_discovered", g["first_seen"]) > time.time()-14*86400][:300]
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    tasks = [pool.submit(source.collect, settings, tracked, self.report) for source in sources.values()]
                    for future in tasks:
                        try:
                            future.result()
                        except Exception as exc:
                            self.report("error", str(exc))
        except Exception as exc:
            self.report("error", str(exc))
        finally:
            with self.lock:
                saved = sum(self.state["saved"].values())
                self.state["message"] = f"Сохранено {saved} игр" if saved else "Данные не получены — откройте журнал источников"
                self.state["finished"] = time.time()
                # Keep running true until the run and next schedule are persisted.
                result = {key: value for key, value in self.state.items() if key != "running"}
            try:
                if run_id:
                    self.db.finish_run(run_id, result)
            finally:
                with self.lock:
                    self.next_scan = time.time()+self.db.settings()["interval_minutes"]*60
                    self.state["running"] = False
                    self.run_id = None
                self.process_lock.release()
                self.busy_lock.release()

    def schedule(self):
        def loop():
            while not self.stop.wait(5):
                if self.db.settings()["auto_scan"] and time.time() >= self.next_scan:
                    self.start()
        threading.Thread(target=loop, daemon=True).start()
