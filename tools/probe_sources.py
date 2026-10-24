"""Run manually to inspect public upstream schemas. Never needs account cookies."""
import concurrent.futures
import json
import urllib.request
from pathlib import Path

URLS = {
    "steam_reviews": "https://store.steampowered.com/appreviews/730?json=1&filter=all&language=all&purchase_type=all&num_per_page=1",
    "roblox_content": "https://apis.roblox.com/explore-api/v1/get-sort-content?sortId=up-and-coming&device=computer&country=US&sessionId=dc74cb91-7f43-4549-af0f-d710be5371c4",
    "roblox_sorts": "https://apis.roblox.com/explore-api/v1/get-sorts?device=computer&country=US&sessionId=dc74cb91-7f43-4549-af0f-d710be5371c4",
    "roblox_games": "https://games.roblox.com/v1/games?universeIds=994732206",
    "steam_new": "https://store.steampowered.com/search/results/?start=0&count=10&filter=popularnew&category1=998&cc=us&l=english&infinite=1",
    "steam_wishlist": "https://store.steampowered.com/search/results/?start=0&count=10&filter=popularwishlist&category1=998&cc=us&l=english&infinite=1",
    "steam_players": "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=730",
    "steam_details": "https://store.steampowered.com/api/appdetails?appids=730&cc=us&l=english",
}


def probe(item):
    name, url = item
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "GameTrendRadar/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.load(response)
        folder = Path(__file__).resolve().parent.parent / "data" / "probes"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return name, str(data)[:3500]
    except Exception as exc:
        return name, str(exc)


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for name, result in pool.map(probe, URLS.items()):
            print(name, ascii(result))
