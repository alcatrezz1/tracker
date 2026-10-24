import math
import re
import statistics
import time
from datetime import datetime, timezone

# Explicit rules, not a claim that a model has played these games.
MECHANICS = {
    "horror": ("Хоррор", [1667, 1721], ["horror", "scary", "haunted", "хоррор", "страш"], "Короткий кооперативный эпизод: общая задача, преследователь, случайные события."),
    "survival": ("Выживание", [1662], ["survival", "survive", "выжив"], "Цикл на 5–10 минут: добыча → улучшение убежища → опасная ночь."),
    "coop": ("Кооператив", [1685, 3843], ["co-op", "cooperative", "teamwork", "кооператив"], "Общая цель для 2–4 игроков, разные роли и быстрый повтор раунда."),
    "tycoon": ("Тайкун / бизнес", [12472], ["tycoon", "management", "shop simulator", "supermarket", "restaurant", "business", "тайкун", "магазин"], "Одна понятная профессия: обслужить → заработать → расширить; совместный бизнес."),
    "collection": ("Коллекционирование", [916648], ["collect", "pets", "hatch", "egg", "brainrot", "коллекц", "питом"], "Сбор существ, видимая редкость, альбом и обмен. Проверить темп выдачи наград."),
    "incremental": ("Инкрементальный рост", [615955, 560542], ["incremental", "idle", "clicker", "rebirth", "кликер"], "Короткий цикл добычи с визуальным ростом, автоматизацией и новым слоем прогрессии."),
    "obby": ("Обби / платформер", [1625, 5379, 5395, 5537, 3877], ["obby", "platformer", "parkour", "паркур", "платформер"], "Одна необычная физическая механика, короткие этапы и совместное прохождение."),
    "roguelike": ("Рогалик", [1716, 3959, 42804], ["roguelike", "rogue-lite", "roguelite", "рогалик"], "Забеги на 8–12 минут с выбором улучшений и случайными комбинациями."),
    "farming": ("Ферма / уют", [87918, 4520, 97376], ["farming", "garden", "cozy", "grow a", "ферм", "уют"], "Вырастить → собрать → открыть новое; персональный участок и визиты друзей."),
    "physics": ("Физика / песочница", [3968, 3810], ["physics", "ragdoll", "sandbox", "песочниц", "физик"], "Один физический инструмент с неожиданными взаимодействиями и быстрым рестартом."),
    "pvp": ("Соревнование / PvP", [1775, 3878], ["pvp", "competitive", "battleground", "сражен"], "Короткая арена, легко читаемые способности и понятная цель матча."),
    "extraction": ("Эвакуация / риск", [1199779], ["extraction", "extract", "эвакуац"], "Собрать добычу и успеть выйти; риск потери противопоставить безопасному возврату."),
    "social": ("Социальная игра", [745697], ["roleplay", "role play", "social deduction", "hangout", "ролевая"], "Понятные социальные роли, совместные события и пространство для самовыражения."),
    "tower_defense": ("Защита базы / башни", [1645], ["tower defense", "tower defence", "защита баш", "защита базы"], "Волны противников, выбор башен и общая экономика команды; короткая карта с развилками."),
    "auto_battler": ("Автобаттлер", [1084988], ["auto battler", "autobattler", "автобаттлер"], "Собрать отряд, выбрать синергии и проверить его в коротком автоматическом бою."),
}


def classify(meta):
    text = " ".join(str(value or "") for value in [meta.get("name"), meta.get("description"), *(meta.get("genres") or []), *(meta.get("categories") or [])]).lower()
    tags = set(meta.get("tag_ids", []))
    result = []
    for key, (label, ids, words, idea) in MECHANICS.items():
        evidence = [f"Steam tag {tag}" for tag in ids if tag in tags]
        for word in words:
            # Prefix match only for Russian stems; English matches full words/phrases.
            pattern = re.escape(word) if re.search("[а-я]", word) else r"(?<!\w)"+re.escape(word)+r"(?!\w)"
            if re.search(pattern, text):
                evidence.append(word)
        if evidence:
            result.append({"id": key, "label": label, "evidence": evidence, "idea": idea})
    return result


def comparison(history, field, seconds):
    if not history or history[-1].get(field) is None:
        return None
    latest = history[-1]
    candidates = [x for x in history[:-1] if x.get(field) is not None and abs(x["ts"]-(latest["ts"]-seconds)) <= max(300, seconds*.25)]
    if not candidates:
        return None
    baseline = min(candidates, key=lambda x: abs(x["ts"]-(latest["ts"]-seconds)))
    if field == "wishlist_rank" and baseline.get("rank_scope") != latest.get("rank_scope"):
        return None
    if field == "reviews" and baseline.get("review_scope") != latest.get("review_scope"):
        return None
    before, after = baseline[field], latest[field]
    return {"before": before, "after": after, "delta": after-before,
            "pct": round((after-before)/before*100, 1) if before > 0 else None,
            "hours": round((latest["ts"]-baseline["ts"])/3600, 2), "baseline_ts": baseline["ts"]}


def age_days(value, now):
    try:
        stamp = datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()
        return max(0, (now-stamp)/86400) if stamp <= now else None
    except (ValueError, TypeError):
        return None


def enrich(row, history, settings, now=None):
    now = now or time.time()
    meta = row["metadata"]
    latest = history[-1] if history else {}
    effective_date = row.get("release_override") or meta.get("release_date")
    age = age_days(effective_date, now)
    created_proxy = meta.get("date_kind") == "project_created" and not row.get("release_override")
    changes = {label: comparison(history, "ccu", seconds) for label, seconds in (("1h", 3600), ("24h", 86400), ("7d", 604800))}
    daily = changes["24h"]
    ccu = latest.get("ccu")
    stale_hours = (now-latest.get("ts", 0))/3600
    stale = stale_hours > max(3, settings["interval_minutes"]/60*2)
    recent = age is not None and age <= settings["recent_days"] and not meta.get("upcoming")
    recent_chart = "Up-and-Coming" in meta.get("feeds", [])
    quality = latest.get("positive_pct")
    volume = min(1, math.log10(max(0, ccu or 0)+1)/5)
    freshness = max(0, 1-(age/settings["recent_days"])) if recent else 0
    momentum = 0
    if daily:
        # Absolute gain and damped relative gain must both be meaningful.
        gain = max(0, daily["delta"])
        damped = gain/max(100, daily["before"])
        momentum = min(1, damped/2) * min(1, gain/500)
    quality_component = max(0, ((quality or 50)-50)/50)
    score = 100*(.40*volume + .35*momentum + .15*freshness + .10*quality_component)
    rank = latest.get("wishlist_rank")
    rank_change = comparison(history, "wishlist_rank", 86400)
    if meta.get("upcoming"):
        score = (80/max(1, rank)**.30 if rank else 0) + (min(20, max(0, -rank_change["delta"])*2) if rank_change else 0)
    sustained_points = [x for x in history if latest.get("ts", 0)-6*3600 <= x["ts"] <= latest.get("ts", 0) and x.get("ccu") is not None]
    sustained = bool(daily and daily["delta"] > 0 and len(sustained_points) >= 3 and sustained_points[-1]["ts"]-sustained_points[-3]["ts"] >= 2*3600 and all(x["ccu"] > daily["before"] for x in sustained_points[-3:]))
    breakout = bool(not stale and daily and daily["pct"] is not None and daily["pct"] >= settings["growth_alert_pct"] and daily["delta"] >= settings["min_players"] and (ccu or 0) >= settings["min_players"])
    reasons = []
    if meta.get("upcoming") and rank:
        reasons.append(f"№{rank} в выборке Most Wishlisted ({settings['country']}, игры)")
    if recent:
        reasons.append(f"{int(age)} дн. с {'создания проекта' if created_proxy else 'релиза'}")
    if recent_chart:
        reasons.append("В подборке Roblox Up-and-Coming")
    if daily:
        reasons.append(f"Онлайн: {daily['delta']:+,} за {daily['hours']:g} ч".replace(",", " "))
    warnings = []
    if created_proxy:
        warnings.append("Дата создания проекта Roblox; фактический запуск неизвестен")
    if not daily and not meta.get("upcoming"):
        warnings.append("Недостаточно истории для сравнения за сутки")
    if meta.get("upcoming"):
        warnings.append("Позиция в рейтинге — косвенный спрос; число wishlist недоступно")
    if stale:
        warnings.append(f"Замер устарел: {stale_hours:.1f} ч назад")
    return {**meta, "key": row["key"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
            "watched": bool(row["watched"]), "note": row["note"], "release_override": row.get("release_override"),
            "effective_date": effective_date, "age_days": round(age, 1) if age is not None else None,
            "date_is_proxy": created_proxy, "recent": recent, "recent_chart": recent_chart,
            "latest": latest, "stale": stale, "growth": changes,
            "rank_change": rank_change, "review_growth": comparison(history, "reviews", 86400),
            "score": round(score, 1), "breakout": breakout, "sustained": sustained,
            "mechanics": classify(meta), "reasons": reasons, "warnings": warnings,
            "samples": len(history), "sparkline": [{"ts": p["ts"], "ccu": p.get("ccu")} for p in history[-48:]]}


def meta_summary(games, settings):
    result = []
    fresh = [g for g in games if not g["stale"]]
    for key, (label, _, _, idea) in MECHANICS.items():
        group = [g for g in fresh if any(m["id"] == key for m in g["mechanics"])]
        if not group:
            continue
        stats = {}
        for platform in ("roblox", "steam"):
            subset = [g for g in group if g["platform"] == platform]
            released = [g for g in subset if not g["upcoming"]]
            qualifying = [g for g in released if (g["recent"] or g["recent_chart"]) and (g["latest"].get("ccu") or 0) >= settings["min_players"]]
            changes = [g["growth"]["24h"]["pct"] for g in released if g["growth"]["24h"] and g["growth"]["24h"]["pct"] is not None]
            stats[platform] = {"games": len(subset), "new_hits": len(qualifying),
                               "growing": sum(g["breakout"] for g in subset),
                               "upcoming": sum(g["upcoming"] for g in subset),
                               "ccu": sum(g["latest"].get("ccu") or 0 for g in released),
                               "median_growth": round(statistics.median(changes), 1) if changes else None,
                               "growth_sample": len(changes)}
        evidence = sorted(group, key=lambda g: (g["breakout"], g["recent"], g["score"]), reverse=True)[:6]
        signals = sum(s["new_hits"]+2*s["growing"]+s["upcoming"] for s in stats.values())
        result.append({"id": key, "label": label, "stats": stats, "signals": signals, "idea": idea,
                       "examples": [{"key": g["key"], "name": g["name"], "platform": g["platform"]} for g in evidence],
                       "cross_platform": bool(stats["roblox"]["new_hits"] and (stats["steam"]["new_hits"] or stats["steam"]["upcoming"]))})
    return sorted(result, key=lambda m: (m["cross_platform"], m["signals"]), reverse=True)


def dashboard(db):
    settings = db.settings()
    histories = db.histories()
    now = time.time()
    games = [enrich(row, histories.get(row["key"], []), settings, now) for row in db.games()]
    games.sort(key=lambda g: g["score"], reverse=True)
    return {"games": games, "meta": meta_summary(games, settings), "settings": settings, "runs": db.runs(), "generated_at": now,
            "counts": {"total": len(games), "roblox": sum(g["platform"] == "roblox" for g in games),
                       "steam": sum(g["platform"] == "steam" for g in games), "breakouts": sum(g["breakout"] for g in games),
                       "new_hits": sum((g["recent"] or g["recent_chart"]) and not g["stale"] and (g["latest"].get("ccu") or 0) >= settings["min_players"] for g in games)}}
