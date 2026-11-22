"""GamerPower: ألعاب مجانية (Giveaways) من Epic / Steam / GOG / itch.io / Prime Gaming / Ubisoft وغيرهم.
API مجاني من غير مفتاح، وبيرجّع الألعاب الكاملة بس (مش DLC ولا Loot)."""
import re
import time
from datetime import datetime, timezone

from . import api

URL = "https://www.gamerpower.com/api/giveaways?platform=pc&type=game&sort-by=date"
STORE_KEYS = (("epic", "Epic"), ("steam", "Steam"), ("gog", "GOG"), ("itch", "itch.io"), ("ubisoft", "Ubisoft"),
              ("prime", "Prime Gaming"), ("humble", "Humble"), ("origin", "EA/Origin"), ("ea app", "EA/Origin"),
              ("battle", "Blizzard"), ("drm", "DRM-Free"))


def store_of(platforms):
    p = (platforms or "").lower()
    for key, label in STORE_KEYS:
        if key in p:
            return label
    return "PC"


def clean_title(t):
    t = re.sub(r"\s*\([^)]*\)\s*(giveaway)?\s*$", "", t or "", flags=re.I)
    return re.sub(r"\s+giveaway\s*$", "", t, flags=re.I).strip() or (t or "؟")


def _ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _worth(w):
    m = re.search(r"([\d.]+)", w or "")
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def parse(rows, now=None):
    now = now or time.time()
    out = []
    if not isinstance(rows, list):
        raise api.ApiError("شكل رد GamerPower مش مفهوم")
    for r in rows:
        try:
            if (r.get("status") or "Active") != "Active" or (r.get("type") or "Game") != "Game":
                continue
            end = _ts(r.get("end_date"))
            if end is not None and end <= now:
                continue
            out.append({"id": f"GP:{r['id']}", "title": clean_title(r.get("title")), "store": store_of(r.get("platforms")),
                        "url": r.get("open_giveaway_url") or r.get("gamerpower_url"), "image": r.get("image") or r.get("thumbnail"),
                        "normal": _worth(r.get("worth")), "end_ts": end, "start_ts": _ts(r.get("published_date")),
                        "note": (r.get("instructions") or "")[:160].replace("\r", " ").replace("\n", " ")})
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def fetch():
    rows, same = api.get_json_ex(URL, conditional=True)
    return parse(rows), same
