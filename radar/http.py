import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime


class HttpClient:
    """Global per-host pacing, bounded retries, no login or access-control bypass."""
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = {}

    def get(self, url, params=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).hostname
        for attempt in range(3):
            with self.lock:
                now = time.monotonic()
                slot = max(now, self.next_request.get(host, now))
                self.next_request[host] = slot + 0.4
            time.sleep(max(0, slot-time.monotonic()))
            request = urllib.request.Request(url, headers={"User-Agent": "GameTrendRadar/1.0 (public market research)", "Accept": "application/json,text/html", "Accept-Language": "en-US,en;q=0.9"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f"{host}: HTTP {exc.code}") from exc
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = parsedate_to_datetime(retry_after).timestamp()-time.time()
                    except (ValueError, TypeError):
                        delay = 2 ** (attempt+1)
                if delay > 60:
                    raise RuntimeError(f"{host}: ограничение запросов, повторить через {round(delay)} с") from exc
                with self.lock:
                    self.next_request[host] = max(self.next_request.get(host, 0), time.monotonic()+max(1, delay))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"{host}: {exc}") from exc
                time.sleep(2 ** attempt)
            except (json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"{host}: источник вернул неожиданный формат") from exc
