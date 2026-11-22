"""الشبكة + Epic Games + CheapShark (خصومات من ~10 متاجر موثوقة).

الطبقة دي موفّرة للنت والمعالج: بتطلب gzip، وبتستعمل ETag/If-Modified-Since (لو السيرفر رد 304 بنرجّع
النسخة المحفوظة من غير ما نحمّل حاجة)، وبتحاول مرة تانية لو حصل خطأ شبكة عابر.
"""
import difflib
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import storage

UA = {"User-Agent": "Mozilla/5.0 DealHunter/3.0 (+desktop; personal use)"}
STORES = {"1": "Steam", "7": "GOG", "25": "Epic", "15": "Fanatical"}  # بتتحدّث تلقائي من /stores
TRUSTED = (("steam", "Steam"), ("gog", "GOG"), ("epic", "Epic"), ("humble", "Humble"), ("fanatical", "Fanatical"),
           ("greenman", "GMG"), ("gamesplanet", "Gamesplanet"), ("gamersgate", "GamersGate"),
           ("origin", "EA/Origin"), ("uplay", "Ubisoft"), ("blizzard", "Blizzard"))
EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=US&allowCountries=US"
CS = "https://www.cheapshark.com/api/1.0"
FREE_PAGE = "https://store.epicgames.com/en-US/free-games"
FILTERS = ("الكل", "ألعاب رخيصة", "AAA", "أقل من 5$")


class ApiError(Exception):
    pass


def _cache_files(url):
    base = storage.DIR / "http" / hashlib.md5(url.encode()).hexdigest()
    return base.with_suffix(".bin"), base.with_suffix(".meta")


def _load_cache(url):
    body_f, meta_f = _cache_files(url)
    try:
        return json.loads(meta_f.read_text(encoding="utf-8")), body_f.read_bytes()
    except Exception:
        return None


def _save_cache(url, headers, body):
    etag, lm = headers.get("ETag"), headers.get("Last-Modified")
    if not (etag or lm):
        return
    body_f, meta_f = _cache_files(url)
    try:
        body_f.parent.mkdir(parents=True, exist_ok=True)
        body_f.write_bytes(body)
        meta_f.write_text(json.dumps({"etag": etag, "lm": lm}), encoding="utf-8")
    except OSError:
        pass


def fetch_ex(url, timeout=12, headers=None, data=None, conditional=False, retries=1):
    """بيرجّع (body, not_modified). conditional=True ← بيستعمل كاش ETag على الهارد."""
    h = {**UA, "Accept-Encoding": "gzip", **(headers or {})}
    cached = _load_cache(url) if conditional and data is None else None
    if cached:
        if cached[0].get("etag"):
            h["If-None-Match"] = cached[0]["etag"]
        if cached[0].get("lm"):
            h["If-Modified-Since"] = cached[0]["lm"]
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
                body = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                if conditional and data is None:
                    _save_cache(url, r.headers, body)
                return body, False
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return cached[1], True
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(5, float(e.headers.get("Retry-After") or 1.5)))
                last = e
                continue
            raise ApiError(f"HTTP {e.code}") from e
        except Exception as e:  # noqa
            last = e
            if attempt < retries:
                time.sleep(1.0)
                continue
    raise ApiError(str(last)) from last


def fetch_bytes(url, timeout=12):
    return fetch_ex(url, timeout)[0]


def get_json_ex(url, timeout=12, conditional=False):
    body, same = fetch_ex(url, timeout, conditional=conditional)
    try:
        return json.loads(body.decode("utf-8")), same
    except Exception as e:
        raise ApiError("رد مش مفهوم: " + str(e)[:80]) from e


def get_json(url, timeout=12):
    return get_json_ex(url, timeout)[0]


def refresh_stores():
    """بيجيب أسماء المتاجر من CheapShark ويحدّث STORES بالمتاجر الموثوقة بس. بيرجع قايمة IDs."""
    try:
        rows = get_json(f"{CS}/stores", timeout=10)
    except ApiError:
        return trusted_ids()
    found = {}
    for r in rows:
        if str(r.get("isActive", 1)) != "1":
            continue
        name = (r.get("storeName") or "").lower()
        for key, label in TRUSTED:
            if key in name:
                found[str(r["storeID"])] = label
                break
    if found:
        STORES.clear()
        STORES.update(found)
    return trusted_ids()


def trusted_ids():
    return ",".join(sorted(STORES, key=int))


# ---------------- Epic ----------------
def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _epic_item(e):
    tp = (e.get("price") or {}).get("totalPrice") or {}
    dec = (tp.get("currencyInfo") or {}).get("decimals", 2)
    slug = next((m.get("pageSlug") for m in e.get("offerMappings") or [] if m.get("pageSlug")), None) \
        or e.get("productSlug") or e.get("urlSlug")
    slug = slug.split("/")[0] if slug else None
    url = FREE_PAGE if not slug or "mystery" in slug else f"https://store.epicgames.com/en-US/p/{slug}"
    imgs = {i.get("type"): i.get("url") for i in e.get("keyImages") or [] if i.get("url")}
    image = next((imgs[k] for k in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall") if k in imgs),
                 next(iter(imgs.values()), None))
    return {"id": e.get("id") or e.get("title"), "title": e.get("title", "؟"),
            "price": (tp.get("originalPrice") or 0) / 10 ** dec, "url": url, "image": image}


def parse_epic(data, now=None):
    """بيرجع (المجانية دلوقتي, الجاية)."""
    now = now or datetime.now(timezone.utc)
    try:
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError) as ex:
        raise ApiError("شكل رد Epic مش مفهوم") from ex
    cur, up = {}, {}
    for el in elements:
        promos = el.get("promotions") or {}
        item = _epic_item(el)
        for key, bucket in (("promotionalOffers", cur), ("upcomingPromotionalOffers", up)):
            for block in promos.get(key) or []:
                for o in block.get("promotionalOffers") or []:
                    if (o.get("discountSetting") or {}).get("discountPercentage") != 0:
                        continue
                    s, e_ = _dt(o["startDate"]), _dt(o["endDate"])
                    if (key == "promotionalOffers" and s <= now < e_) or (key != "promotionalOffers" and s > now):
                        bucket[item["id"]] = {**item, "start": s, "end": e_}
    return sorted(cur.values(), key=lambda x: x["end"]), sorted(up.values(), key=lambda x: x["start"])


def epic_free():
    return parse_epic(get_json_ex(EPIC_URL, conditional=True)[0])


def _unit(n, one, two, few, many):
    return one if n == 1 else two if n == 2 else f"{n} {few}" if 3 <= n <= 10 else f"{n} {many}"


def remaining_text(end, now=None):
    secs = int(((end - (now or datetime.now(timezone.utc))).total_seconds()))
    if secs <= 0:
        return "العرض خلص"
    d, h, m = secs // 86400, secs % 86400 // 3600, secs % 3600 // 60
    parts = []
    if d:
        parts.append(_unit(d, "يوم", "يومين", "أيام", "يوم"))
    if h:
        parts.append(_unit(h, "ساعة", "ساعتين", "ساعات", "ساعة"))
    if m and not d:
        parts.append(_unit(m, "دقيقة", "دقيقتين", "دقايق", "دقيقة"))
    return "فاضل " + " و".join(parts or ["أقل من دقيقة"])


# ---------------- CheapShark ----------------
def parse_deal(d):
    app = d.get("steamAppID")
    image = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app}/header.jpg" if app and app != "0" else d.get("thumb")
    return {"id": d["dealID"], "game_id": d.get("gameID"), "title": d.get("title", "؟"),
            "sale": float(d["salePrice"]), "normal": float(d["normalPrice"]), "savings": float(d["savings"]),
            "store": STORES.get(str(d.get("storeID")), "متجر"), "image": image,
            "url": "https://www.cheapshark.com/redirect?dealID=" + urllib.parse.quote(d["dealID"]),
            "metacritic": int(d.get("metacriticScore") or 0),
            "app": int(app) if app and app != "0" else None}


def parse_steam_free(rows):
    """ألعاب Steam اللي سعرها بقى صفر (Free to keep) من نتايج CheapShark."""
    out = {}
    for r in rows:
        try:
            d = parse_deal(r)
        except (KeyError, ValueError):
            continue
        if d["sale"] == 0 and d["normal"] > 0:
            app = r.get("steamAppID")
            if app and app != "0":
                d["url"] = f"https://store.steampowered.com/app/{app}"
            d["store"] = "Steam"
            out[d["game_id"] or d["id"]] = d
    return sorted(out.values(), key=lambda d: -d["normal"])


def steam_free():
    return parse_steam_free(get_json(f"{CS}/deals?storeID=1&upperPrice=0&onSale=1&pageSize=30&sortBy=recent"))


def cheap_deals(min_savings=50):
    base = f"{CS}/deals?storeID={trusted_ids()}&onSale=1&upperPrice=60&pageSize=60&sortBy="
    best = {}
    for q in ("Deal+Rating", "Metacritic&metacritic=70", "Recent"):
        try:
            rows = get_json(base + q)
        except ApiError:
            if not best:
                raise
            continue
        for r in rows:
            try:
                d = parse_deal(r)
            except (KeyError, ValueError):
                continue
            if d["savings"] >= min_savings and d["sale"] > 0:  # المجاني ليه قسم لوحده
                k = d["game_id"] or d["id"]
                if k not in best or d["sale"] < best[k]["sale"]:
                    best[k] = d
    return sorted(best.values(), key=lambda d: -d["savings"])


def apply_filter(deals, name):
    if name == "ألعاب رخيصة":
        return [d for d in deals if d["sale"] < 10]
    if name == "AAA":
        return [d for d in deals if d["normal"] >= 40]
    if name == "أقل من 5$":
        return [d for d in deals if d["sale"] < 5]
    return list(deals)


def historic_lows(game_ids):
    """{game_id: أقل سعر في التاريخ} (اختياري - لو فشل بيرجع اللي لقاه)."""
    out = {}
    ids = [i for i in game_ids if i]
    for i in range(0, len(ids), 25):
        try:
            data = get_json(f"{CS}/games?ids={','.join(ids[i:i + 25])}")
            for gid, v in data.items():
                out[gid] = float(v["cheapestPriceEver"]["price"])
        except Exception:
            continue
    return out


def is_historic_low(deal, lows):
    low = lows.get(deal["game_id"])
    return low is not None and deal["sale"] <= low * 1.03


def title_match(q, name):
    q, n = q.strip().lower(), name.lower()
    return len(q) >= 3 and (q in n or difflib.SequenceMatcher(None, q, n).ratio() >= 0.75)


def match_wishlist(title, rows):
    """بيختار أنسب صفقة لاسم اللعبة من نتايج CheapShark (أو None)."""
    q = title.strip().lower()
    best = None
    for r in rows:
        try:
            d = parse_deal(r)
        except (KeyError, ValueError):
            continue
        if title_match(q, d["title"]) and d["savings"] >= 10 and (best is None or d["savings"] > best["savings"]):
            best = d
    return best


def lookup_item(item):
    """أحسن صفقة حالية للعبة في الويش ليست (بالـ App ID لو متاح، وإلا بالاسم)."""
    if item.get("app"):
        rows = get_json(f"{CS}/deals?steamAppID={item['app']}&onSale=1&storeID={trusted_ids()}&pageSize=10")
        ds = []
        for r in rows:
            try:
                ds.append(parse_deal(r))
            except (KeyError, ValueError):
                pass
        ds = [d for d in ds if d["savings"] >= 1]
        return min(ds, key=lambda d: d["sale"]) if ds else None
    return wishlist_lookup(item["title"])


# ---------------- الويش ليست بأقل عدد طلبات ----------------
GID_RETRY = 86400


def _best_title(title, rows):
    q = title.strip().lower()
    for r in rows:
        if (r.get("external") or "").lower() == q:
            return r
    best, score = None, 0.0
    for r in rows:
        sc = difflib.SequenceMatcher(None, q, (r.get("external") or "").lower()).ratio()
        if sc > score:
            best, score = r, sc
    return best if best and score >= 0.75 else None


def resolve_gid(item, gid_map, now=None):
    """GameID بتاع CheapShark للعبة (بيتحفظ في gid_map فمبيتطلبش تاني). None لو مش موجودة."""
    now = now or time.time()
    key = str(item["app"]) if item.get("app") else "t:" + item["title"].strip().lower()
    hit = gid_map.get(key)
    if hit and (hit.get("gid") or now - hit.get("ts", 0) < GID_RETRY):
        return hit.get("gid")
    if item.get("app"):
        rows = get_json(f"{CS}/games?steamAppID={int(item['app'])}")
        gid = str(rows[0]["gameID"]) if rows else None
    else:
        rows = get_json(f"{CS}/games?title={urllib.parse.quote(item['title'].strip())}&limit=10")
        r = _best_title(item["title"], rows)
        gid = str(r["gameID"]) if r else None
    gid_map[key] = {"gid": gid, "ts": now}
    return gid


def game_deal(gid, v, allowed=None):
    """أرخص عرض في المتاجر الموثوقة من رد /games?ids= (بيرجع dict زي parse_deal أو None)."""
    info, best = v.get("info") or {}, None
    allowed = set(allowed.split(",")) if isinstance(allowed, str) else allowed
    for dl in v.get("deals") or []:
        if allowed and str(dl.get("storeID")) not in allowed:
            continue
        try:
            if best is None or float(dl["price"]) < float(best["price"]):
                best = dl
        except (KeyError, ValueError):
            continue
    if not best:
        return None
    app = info.get("steamAppID")
    return {"id": best["dealID"], "game_id": str(gid), "title": info.get("title", "؟"), "sale": float(best["price"]),
            "normal": float(best["retailPrice"]), "savings": float(best["savings"]),
            "store": STORES.get(str(best["storeID"]), "متجر"),
            "image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app}/header.jpg" if app and app != "0" else info.get("thumb"),
            "url": "https://www.cheapshark.com/redirect?dealID=" + urllib.parse.quote(best["dealID"]),
            "metacritic": 0, "app": int(app) if app and app != "0" else None}


def wishlist_deals(items, gid_map):
    """{title: أحسن عرض أو None} + {gid: أقل سعر تاريخي}. طلب واحد لكل 25 لعبة بدل طلب لكل لعبة."""
    gids, by_gid = {}, {}
    for it in items:
        try:
            gid = resolve_gid(it, gid_map)
        except ApiError:
            continue
        gids[it["title"]] = gid
        if gid:
            by_gid.setdefault(gid, []).append(it["title"])
    out, lows, ids = {t: None for t in gids}, {}, sorted(by_gid)
    allowed = trusted_ids()
    for i in range(0, len(ids), 25):
        data = get_json(f"{CS}/games?ids={','.join(ids[i:i + 25])}")
        for gid, v in data.items():
            try:
                lows[gid] = float(v["cheapestPriceEver"]["price"])
            except (KeyError, ValueError, TypeError):
                pass
            d = game_deal(gid, v, allowed)
            for t in by_gid.get(gid, []):
                out[t] = d
    return out, lows


def search_games(q, limit=8):
    """اقتراحات أسماء للويش ليست."""
    q = q.strip()
    if len(q) < 2:
        return []
    rows = get_json(f"{CS}/games?title={urllib.parse.quote(q)}&limit={int(limit)}")
    out, seen = [], set()
    for r in rows:
        name = r.get("external") or ""
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        app = r.get("steamAppID")
        out.append({"title": name, "gid": str(r.get("gameID")), "thumb": r.get("thumb"),
                    "app": int(app) if app and app != "0" else None, "cheapest": float(r.get("cheapest") or 0)})
    return out


def diagnose():
    """فحص اتصال بالمصادر التلاتة. بيرجع [(اسم, نجح?, تفاصيل)]."""
    checks = (("Epic Games", lambda: f"{len(epic_free()[0])} لعبة مجانية دلوقتي"),
              ("CheapShark (الخصومات)", lambda: f"{len(get_json(f'{CS}/deals?pageSize=5'))} صفقة وصلت"),
              ("GamerPower (جيف أواي)", lambda: f"{len(get_json('https://www.gamerpower.com/api/giveaways?platform=pc&type=game'))} عرض مجاني"),
              ("Steam", lambda: "بيرد" if fetch_bytes("https://steamcommunity.com/id/gabelogannewell/?xml=1") else "رد فاضي"))
    out = []
    for name, fn in checks:
        try:
            out.append((name, True, fn()))
        except Exception as e:  # noqa
            out.append((name, False, str(e)[:120]))
    return out


def wishlist_lookup(title):
    q = urllib.parse.quote(title.strip())
    return match_wishlist(title, get_json(f"{CS}/deals?title={q}&onSale=1&storeID={trusted_ids()}&pageSize=15"))
