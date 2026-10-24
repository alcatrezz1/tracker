import concurrent.futures
import html
import json
import re
import time
import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser


def parse_release(value):
    """Never turn a quarter/year/Coming soon into a made-up exact launch date."""
    for pattern in ("%b %d, %Y", "%d %b, %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=timezone.utc).date().isoformat()
        except (ValueError, AttributeError):
            pass
    return None


class SteamRows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a" and "search_result_row" in classes:
            app_id = attrs.get("data-ds-appid", "").split(",")[0]
            self.row = {"app_id": app_id, "title": "", "release_text": "", "tag_ids": []} if app_id.isdigit() else None
            if self.row:
                try:
                    self.row["tag_ids"] = json.loads(attrs.get("data-ds-tagids", "[]"))
                except ValueError:
                    pass
        if not self.row:
            return
        if "title" in classes:
            self.field = "title"
        elif "search_released" in classes:
            self.field = "release_text"
        if tag == "img" and not self.row.get("image"):
            self.row["image"] = attrs.get("src", "")
        tooltip = html.unescape(attrs.get("data-tooltip-html", ""))
        match = re.search(r"(\d+)% of the ([\d,]+) user reviews", tooltip)
        if match:
            self.row["review_pct"] = int(match[1])
            self.row["review_count"] = int(match[2].replace(",", ""))

    def handle_data(self, data):
        if self.row and self.field:
            self.row[self.field] += data

    def handle_endtag(self, tag):
        if tag in ("span", "div"):
            self.field = None
        if tag == "a" and self.row:
            self.row["title"] = self.row["title"].strip()
            self.row["release_text"] = self.row["release_text"].strip()
            self.rows.append(self.row)
            self.row = None


def steam_search(client, feed, limit, country):
    rows = []
    while len(rows) < limit:
        params = {"start": len(rows), "count": min(50, limit-len(rows)), "filter": feed,
                  "category1": 998, "cc": country.lower(), "l": "english", "infinite": 1}
        if feed == "newreleases":
            params["sort_by"] = "Released_DESC"
        data = client.get("https://store.steampowered.com/search/results/", params)
        if not data.get("success") or not isinstance(data.get("results_html"), str):
            raise RuntimeError(f"Steam {feed}: изменился формат выдачи")
        parser = SteamRows()
        parser.feed(data["results_html"])
        if not parser.rows:
            if not rows:
                raise RuntimeError(f"Steam {feed}: пустая выдача; возможно, изменился формат")
            break
        previous = {row["app_id"] for row in rows}
        fresh = [row for row in parser.rows if row["app_id"] not in previous]
        if not fresh:
            break
        for row in fresh:
            row["rank"] = len(rows)+1
            rows.append(row)
        if len(parser.rows) < params["count"]:
            break
    return rows[:limit]


class SteamSource:
    name = "steam"

    def __init__(self, client, db):
        self.client, self.db = client, db
        self.review_lock = threading.Lock()
        self.reviews_unavailable = bool(db.cache_get("steam-review-api-unavailable", 3600))

    def discover(self, settings, report):
        found = {}
        feeds = ("popularnew", "newreleases", "popularwishlist", "topsellers")
        for feed in feeds:
            try:
                rows = steam_search(self.client, feed, settings["steam_limit"], settings["country"])
                report("feed", f"Steam / {feed}: {len(rows)}")
                for row in rows:
                    item = found.setdefault(row["app_id"], {**row, "feeds": [], "wishlist_rank": None})
                    item["feeds"].append(feed)
                    if feed == "popularwishlist":
                        item["wishlist_rank"] = row["rank"]
            except Exception as exc:
                report("error", str(exc))
        return found

    def collect_one(self, app_id, discovery, settings, report):
        cache_key = f"steam-details:{settings['country']}:{app_id}"
        details = self.db.cache_get(cache_key, 12*3600)
        if details is None:
            response = self.client.get("https://store.steampowered.com/api/appdetails", {"appids": app_id, "cc": settings["country"].lower(), "l": "english"})
            result = response.get(str(app_id), {})
            if not result.get("success"):
                raise RuntimeError(f"Steam {app_id}: карточка недоступна в выбранном регионе")
            details = result["data"]
            self.db.cache_put(cache_key, details)
        if details.get("type") != "game":
            return False
        upcoming = bool(details.get("release_date", {}).get("coming_soon"))
        release_text = details.get("release_date", {}).get("date", "")
        meta = {
            "platform": "steam", "app_id": str(app_id), "name": details["name"],
            "description": html.unescape(re.sub("<[^>]+>", " ", details.get("short_description", ""))),
            "url": f"https://store.steampowered.com/app/{app_id}/",
            "image": details.get("header_image", ""), "creator": ", ".join(details.get("developers", [])),
            "release_date": None if upcoming else parse_release(release_text), "release_text": release_text,
            "date_kind": "steam_release", "upcoming": upcoming,
            "genres": [x["description"] for x in details.get("genres", [])],
            "categories": [x["description"] for x in details.get("categories", [])],
            "tag_ids": discovery.get("tag_ids", []), "feeds": discovery.get("feeds", []),
            "price": details.get("price_overview", {}).get("final_formatted", "Free" if details.get("is_free") else None),
            "last_discovered": discovery.get("last_discovered", time.time()),
            "scope": f"Steam Store · {settings['country']} · category1=998",
        }
        metrics = {"ccu": None, "wishlist_rank": discovery.get("wishlist_rank"),
                   "rank_scope": f"{settings['country']}:games:popularwishlist", "reviews": discovery.get("review_count"), "positive_pct": discovery.get("review_pct"),
                   "review_scope": f"search:english:{settings['country']}" if discovery.get("review_count") is not None else None}
        if not upcoming:
            try:
                response = self.client.get("https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": app_id}).get("response", {})
                if response.get("result") == 1 and isinstance(response.get("player_count"), int):
                    metrics["ccu"] = response["player_count"]
                else:
                    report("error", f"Steam {app_id}: онлайн недоступен (result={response.get('result')})")
            except Exception as exc:
                report("error", f"Steam {app_id}, онлайн: {exc}")
            if metrics["reviews"] is None:
                # If this optional endpoint is unavailable, one failure opens a one-hour
                # circuit. Other games still use explicitly scoped search-row summaries.
                with self.review_lock:
                    if not self.reviews_unavailable:
                        try:
                            response = self.client.get(f"https://store.steampowered.com/appreviews/{app_id}", {"json": 1, "filter": "all", "language": "all", "purchase_type": "all", "num_per_page": 1})
                            summary = response.get("query_summary", {})
                            total = summary.get("total_reviews")
                            if total is not None:
                                metrics["reviews"] = total
                                metrics["positive_pct"] = round(100*summary.get("total_positive", 0)/total, 1) if total else None
                                metrics["review_scope"] = "api:all_languages:all_purchases"
                        except Exception as exc:
                            self.reviews_unavailable = True
                            self.db.cache_put("steam-review-api-unavailable", True)
                            report("error", f"Steam: API отзывов недоступен на час; используем сводки витрины, где доступны. {exc}")
        self.db.save(meta, metrics)
        return True

    def collect(self, settings, tracked, report):
        found = self.discover(settings, report)
        # Follow watched games and a bounded recent catalog even after they leave charts.
        for game in tracked:
            if game["platform"] == "steam":
                meta = game["metadata"]
                found.setdefault(game["app_id"], {"feeds": ["tracking"], "tag_ids": meta.get("tag_ids", []), "last_discovered": meta.get("last_discovered", game["first_seen"])})
        total = len(found)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self.collect_one, app_id, item, settings, report): app_id for app_id, item in found.items()}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    if future.result():
                        report("saved", "steam")
                except Exception as exc:
                    report("error", f"Steam {futures[future]}: {exc}")
                report("progress", f"Steam: {index} / {total}")


class RobloxSource:
    name = "roblox"

    def __init__(self, client, db):
        self.client, self.db = client, db

    def collect(self, settings, tracked, report):
        found = {}
        params = {"device": settings["device"], "country": settings["country"], "sessionId": str(uuid.uuid4())}
        try:
            response = self.client.get("https://apis.roblox.com/explore-api/v1/get-sorts", params) if settings["roblox_limit"] else {"sorts": []}
            sorts = response.get("sorts")
            if not isinstance(sorts, list):
                raise RuntimeError("Roblox: изменился формат подборок")
            game_sorts = [s for s in sorts if s.get("contentType") == "Games"]
            game_sorts.sort(key=lambda s: {"up-and-coming": 0, "top-trending": 1, "top-playing-now": 2}.get(s.get("sortId"), 3))
            for sort in game_sorts:
                games = list(sort.get("games", []))
                # Chart contents can be larger than the small preview in get-sorts.
                if sort.get("sortId") in ("up-and-coming", "top-trending", "top-playing-now"):
                    try:
                        content = self.client.get("https://apis.roblox.com/explore-api/v1/get-sort-content", {**params, "sortId": sort["sortId"]})
                        games += content.get("games", [])
                    except Exception as exc:
                        report("error", f"Roblox {sort['sortId']}: {exc}; использован preview")
                count = 0
                for item in games:
                    app_id = str(item.get("universeId", ""))
                    if not app_id.isdigit() or item.get("isSponsored"):
                        continue
                    if app_id not in found and len(found) >= settings["roblox_limit"]:
                        continue
                    entry = found.setdefault(app_id, {"feeds": []})
                    label = sort.get("sortDisplayName") or sort.get("sortId", "chart")
                    if label not in entry["feeds"]:
                        entry["feeds"].append(label)
                    count += 1
                report("feed", f"Roblox / {sort.get('sortDisplayName')}: {count}")
            if not found and settings["roblox_limit"]:
                report("error", "Roblox: подборки не вернули игр; проверьте формат API")
        except Exception as exc:
            report("error", f"Roblox discovery: {exc}")
        for game in tracked:
            if game["platform"] == "roblox":
                found.setdefault(game["app_id"], {"feeds": ["tracking"], "last_discovered": game.get("metadata", {}).get("last_discovered", game.get("first_seen", time.time()))})
        ids = list(found)
        for offset in range(0, len(ids), 50):
            batch = ids[offset:offset+50]
            try:
                payload = self.client.get("https://games.roblox.com/v1/games", {"universeIds": ",".join(batch)})
                games = payload.get("data")
                if not isinstance(games, list):
                    raise RuntimeError("Roblox: изменился формат карточек")
            except Exception as exc:
                report("error", str(exc))
                continue
            votes, images = {}, {}
            try:
                votes = {str(x["id"]): x for x in self.client.get("https://games.roblox.com/v1/games/votes", {"universeIds": ",".join(batch)}).get("data", [])}
            except Exception as exc:
                report("error", f"Roblox likes: {exc}")
            try:
                images = {str(x["targetId"]): x.get("imageUrl", "") for x in self.client.get("https://thumbnails.roblox.com/v1/games/icons", {"universeIds": ",".join(batch), "size": "150x150", "format": "Png", "isCircular": "false"}).get("data", [])}
            except Exception as exc:
                report("error", f"Roblox icons: {exc}")
            for game in games:
                app_id = str(game["id"])
                vote = votes.get(app_id, {})
                likes, dislikes = vote.get("upVotes"), vote.get("downVotes")
                total = (likes or 0)+(dislikes or 0)
                meta = {"platform": "roblox", "app_id": app_id, "name": game["name"],
                        "description": game.get("description") or "", "creator": (game.get("creator") or {}).get("name", ""),
                        "url": f"https://www.roblox.com/games/{game['rootPlaceId']}",
                        "place_id": game["rootPlaceId"], "image": images.get(app_id, ""),
                        "release_date": game.get("created", "")[:10] or None,
                        "release_text": game.get("created", "")[:10], "date_kind": "project_created",
                        "updated": game.get("updated"), "upcoming": False,
                        "last_discovered": found[app_id].get("last_discovered", time.time()),
                        "genres": [game[k] for k in ("genre_l1", "genre_l2", "genre") if game.get(k)],
                        "tag_ids": [], "categories": [], "feeds": found[app_id]["feeds"],
                        "scope": f"Roblox Charts · {settings['country']} · {settings['device']} (Top Playing Now: global)"}
                metrics = {"ccu": game.get("playing"), "visits": game.get("visits"),
                           "favorites": game.get("favoritedCount"), "likes": likes, "dislikes": dislikes,
                           "positive_pct": round(100*likes/total, 1) if total and likes is not None else None,
                           "wishlist_rank": None}
                self.db.save(meta, metrics)
                report("saved", "roblox")
            report("progress", f"Roblox: {min(offset+50, len(ids))} / {len(ids)}")


def resolve_game(client, value):
    value = value.strip()
    match = re.search(r"(?:https?://)?(?:www\.)?roblox\.com/games/(\d+)", value)
    if match:
        data = client.get(f"https://apis.roblox.com/universes/v1/places/{match[1]}/universe")
        app_id = str(data.get("universeId", ""))
        if not app_id.isdigit():
            raise ValueError("Не удалось определить universeId")
        return "roblox", app_id
    match = re.search(r"(?:https?://)?store\.steampowered\.com/app/(\d+)", value)
    if match:
        return "steam", match[1]
    match = re.fullmatch(r"(roblox|steam):(\d{1,18})", value)
    if match:
        return match[1], match[2]
    raise ValueError("Вставьте ссылку Roblox / Steam либо roblox:UNIVERSE_ID / steam:APP_ID")
