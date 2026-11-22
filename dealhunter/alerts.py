"""قواعد التنبيه لكل لعبة في الويش ليست: نوع الخصم + التكرار."""
from . import api, storage

MODES = {"any": ("أي خصم", 10, False), "50": ("خصم 50%+", 50, False), "75": ("خصم 75%+", 75, False),
         "free": ("لما تبقى ببلاش بس", 100, True)}
REPEATS = {"once": "مرة واحدة", "change": "كل ما السعر يتغير", "6h": "كل 6 ساعات", "always": "كل فحص"}


def normalize(item):
    if isinstance(item, str):
        item = {"title": item}
    mode = item.get("mode") if item.get("mode") in MODES else "any"
    rep = item.get("repeat") if item.get("repeat") in REPEATS else "change"
    mp = item.get("max_price")
    return {"title": item.get("title", "").strip(), "mode": mode, "repeat": rep,
            "max_price": float(mp) if mp not in (None, "") else None, "app": item.get("app")}


def qualifies(item, deal):
    _, min_off, free_only = MODES[item["mode"]]
    if free_only:
        return deal["sale"] == 0
    if deal["savings"] < min_off:
        return False
    return item["max_price"] is None or deal["sale"] <= item["max_price"]


def decide(item, deal, last, now):
    """(نبعت إشعار؟, الحالة الجديدة). الحالة None = العرض مش مستوفي الشروط، نصفّر."""
    if not deal or not qualifies(item, deal):
        return False, None
    key = f"{deal['id']}:{deal['sale']:.2f}"
    fresh = {"key": key, "ts": now}
    if not last:
        return True, fresh
    rep = item["repeat"]
    if rep == "once":
        return False, last
    if rep == "change":
        return (True, fresh) if last["key"] != key else (False, last)
    if rep == "6h":
        return (True, fresh) if now - last["ts"] >= 6 * 3600 else (False, last)
    return True, fresh  # always


def free_match(item, g):
    """هل لعبة مجانية (Epic/Steam) هي نفس لعبة الويش ليست؟"""
    if item["app"] and g.get("app") == item["app"]:
        return True
    return api.title_match(item["title"], g["title"])


def pseudo_deal(g):
    return {"id": g["id"], "sale": 0.0, "savings": 100.0, "title": g["title"], "store": g["store"],
            "url": g["url"], "normal": g["normal"], "app": g.get("app")}


# ---- تعديل الويش ليست في التخزين ----
def items():
    return [normalize(i) for i in storage.get("wishlist")]


def add_item(title, mode="any", repeat="change", max_price=None, app=None):
    it = normalize({"title": title, "mode": mode, "repeat": repeat, "max_price": max_price, "app": app})
    if not it["title"]:
        return False
    lst = items()
    if any(i["title"].lower() == it["title"].lower() for i in lst):
        return False
    storage.put("wishlist", lst + [it])
    return True


def update_item(title, **changes):
    storage.put("wishlist", [{**i, **changes} if i["title"] == title else i for i in items()])


def remove_item(title):
    storage.put("wishlist", [i for i in items() if i["title"] != title])
