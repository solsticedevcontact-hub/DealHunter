"""عروض Steam الرسمية (Specials) — بتدّي وقت انتهاء الخصم، فنعرض عداد تنازلي حقيقي."""
from . import api

URL = "https://store.steampowered.com/api/featuredcategories?cc=us&l=english"


def parse_specials(data):
    try:
        items = data["specials"]["items"]
    except (KeyError, TypeError) as ex:
        raise api.ApiError("شكل رد Steam مش مفهوم") from ex
    out = {}
    for i in items:
        try:
            app, off = int(i["id"]), int(i.get("discount_percent") or 0)
            orig, final = (i.get("original_price") or 0) / 100, (i.get("final_price") or 0) / 100
            if off <= 0 or orig <= 0:
                continue
            out[app] = {"id": f"steam:{app}", "game_id": f"S{app}", "title": i.get("name", "؟"), "sale": final, "normal": orig,
                        "savings": float(off), "store": "Steam", "image": i.get("header_image") or i.get("large_capsule_image"),
                        "url": f"https://store.steampowered.com/app/{app}", "metacritic": 0, "app": app,
                        "ends": i.get("discount_expiration")}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch():
    data, same = api.get_json_ex(URL, conditional=True)
    return parse_specials(data), same
