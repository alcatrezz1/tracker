from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DEFAULTS = {
    "interval_minutes": 60,
    "steam_limit": 25,
    "roblox_limit": 250,
    "recent_days": 90,
    "min_players": 100,
    "growth_alert_pct": 30,
    "country": "US",
    "device": "computer",
    "auto_scan": True,
}


def validate_settings(values, existing=None):
    settings = dict(existing or DEFAULTS)
    ranges = {"interval_minutes": (15, 1440), "steam_limit": (5, 100),
              "roblox_limit": (25, 1000), "recent_days": (1, 730),
              "min_players": (1, 1000000), "growth_alert_pct": (1, 10000)}
    for key, value in values.items():
        if key in ranges:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key}: нужно целое число")
            low, high = ranges[key]
            if not low <= value <= high:
                raise ValueError(f"{key}: допустимо от {low} до {high}")
        elif key == "country":
            if not isinstance(value, str) or len(value) != 2 or not value.isascii() or not value.isalpha():
                raise ValueError("country: двухбуквенный код страны, например US")
            value = value.upper()
        elif key == "device":
            if value not in ("computer", "low_end_phone", "high_end_phone", "low_end_tablet", "high_end_tablet", "console"):
                raise ValueError("Некорректное устройство")
        elif key == "auto_scan":
            if not isinstance(value, bool):
                raise ValueError("auto_scan: нужно true или false")
        else:
            raise ValueError(f"Неизвестная настройка: {key}")
        settings[key] = value
    return settings
