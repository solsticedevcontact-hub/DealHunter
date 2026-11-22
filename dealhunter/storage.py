"""إعدادات وبيانات المستخدم في ~/.dealhunter (آمنة بين الخيوط)."""
import copy
import json
import threading
from pathlib import Path

DIR = Path.home() / ".dealhunter"
_lock = threading.RLock()
DEFAULTS = {
    "wishlist": [], "seen_free": None, "seen_free_steam": None, "saved_total": 0.0, "claimed": [], "bought": [],
    "interval": 30, "notify": True, "notify_free": True, "notify_deals": True, "notify_wishlist": True,
    "min_discount_alert": 85, "asked_autostart": False, "seeded": False, "notified_deals": [], "wl_last": {},
    "steam": {"id": "", "name": "", "key": "", "last_sync": 0}, "owned": [],
    # --- v3 ---
    "mode": "normal", "quiet": {"on": False, "from": "00:00", "to": "08:00"},
    "sources": {"epic": True, "giveaways": True, "steam_free": True, "deals": True, "steam_specials": True},
    "discord": "", "telegram": {"token": "", "chat": ""}, "gid_map": {}, "seen_free3": None,
}


def _file():
    return DIR / "data.json"


def _read():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {**copy.deepcopy(DEFAULTS), **d}


def _write(d):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_file())  # كتابة ذرّية: لو الجهاز فصل في نص الكتابة الملف القديم يفضل سليم
    except Exception:
        pass


def get(key):
    with _lock:
        return _read()[key]


def put(key, value):
    with _lock:
        d = _read()
        d[key] = value
        _write(d)


def update(key, fn):
    """تعديل ذرّي: fn(القيمة القديمة) ← الجديدة."""
    with _lock:
        d = _read()
        d[key] = fn(d[key])
        _write(d)
        return d[key]


def add_saved(amount, kind, item_id):
    """بيزوّد عداد الفلوس مرة واحدة بس لكل عنصر. بيرجع True لو اتحسب."""
    with _lock:
        d = _read()
        if item_id in d[kind]:
            return False
        d[kind].append(item_id)
        d["saved_total"] = round(d["saved_total"] + amount, 2)
        _write(d)
        return True
