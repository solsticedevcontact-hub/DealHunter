"""المحرك: جدولة ذكية لكل مصدر لوحده (مش دورة واحدة تضرب كل حاجة كل مرة).

- كل مصدر ليه ميعاده: Epic / ألعاب مجانية / خصومات / أمنيات — وبيتأجّل لوحده (backoff) لو فشل.
- طلبات الشبكة بتستعمل ETag + gzip، والأمنيات بتتفحص بطلب واحد لكل 25 لعبة.
- تقييم الأمنيات والإشعارات بيحصل من غير نت أصلاً كل ما أي مصدر يتحدّث.
- وضع التوفير بيضاعف الفواصل، وساعات الهدوء بتأجّل الإشعارات (مش بتضيّعها).
"""
import json
import random
import re
import threading
import time

from . import __version__, alerts, api, db, giveaways, steam, steamstore, storage
from .logs import log

MODES = {"eco": ("توفير", 2.0), "normal": ("متوازن", 1.0), "fast": ("سريع", 0.5)}
BASE_MIN = {"epic": 30, "giveaways": 30, "steam_free": 30, "deals": 60, "wishlist": 60}
LABELS = {"epic": "Epic Games", "giveaways": "GamerPower (Epic/GOG/itch/Prime/Ubisoft…)", "steam_free": "Steam (مجاني)",
          "deals": "الخصومات (10 متاجر + Steam)", "wishlist": "الأمنيات"}
ORDER = ("epic", "giveaways", "steam_free", "deals", "wishlist")
FREE_SOURCES = ("epic", "giveaways", "steam_free")
MAX_BACKOFF = 4 * 3600
MAX_HELD = 30


def norm(t):
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", (t or "").lower())


def _ts(dt):
    return dt.timestamp() if dt else None


def _hm(s, default):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def in_quiet(now=None):
    q = storage.get("quiet")
    if not q.get("on"):
        return False
    t = time.localtime(now or time.time())
    cur, a, b = t.tm_hour * 60 + t.tm_min, _hm(q.get("from"), 0), _hm(q.get("to"), 480)
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def empty_data():
    return {"epic_cur": [], "epic_up": [], "giveaways": [], "steam_free": [], "deals": [], "lows": {}, "wl_deals": {}}


class Engine:
    def __init__(self, notifier):
        self.notifier = notifier
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.force = set()
        self.rev = 1
        self.held = []
        self.data = empty_data()
        self.src = {n: {"next": 0.0, "fails": 0, "ok": None, "last": 0.0, "err": ""} for n in ORDER}
        self.syncing = False
        self._load_snapshot()

    # ---------------- حالة + كاش ----------------
    def touch(self):
        with self.lock:
            self.rev += 1

    def _snap_file(self):
        return storage.DIR / "snapshot.json"

    def _load_snapshot(self):
        try:
            snap = json.loads(self._snap_file().read_text(encoding="utf-8"))
            self.data = {**empty_data(), **snap.get("data", {})}
            for n, s in (snap.get("src") or {}).items():
                if n in self.src:
                    self.src[n].update({k: s.get(k, self.src[n][k]) for k in ("ok", "last", "err")})
        except Exception:  # noqa
            pass

    def _save_snapshot(self):
        try:
            storage.DIR.mkdir(parents=True, exist_ok=True)
            tmp = self._snap_file().with_suffix(".tmp")
            with self.lock:
                tmp.write_text(json.dumps({"data": self.data, "src": self.src}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._snap_file())
        except OSError:
            pass

    # ---------------- الحلقة ----------------
    def interval(self, name):
        mult = MODES.get(storage.get("mode"), MODES["normal"])[1]
        return BASE_MIN[name] * 60 * mult

    def enabled(self, name):
        return name == "wishlist" or bool(storage.get("sources").get(name, True))

    def loop(self):
        while not self.stop.is_set():
            try:
                self.run_due()
            except Exception:  # noqa
                log.exception("cycle failed")
            nxt = [s["next"] for n, s in self.src.items() if self.enabled(n)]
            wait = max(5.0, min(nxt) - time.time()) if nxt else 600.0
            self.wake.wait(min(wait, 600.0))
            self.wake.clear()

    def check_now(self, *names):
        with self.lock:
            self.force.update(names or ORDER)
        self.wake.set()

    def run_due(self, force_all=False):
        """بيشغّل المصادر اللي معادها جه بس، وبعدها يقيّم الأمنيات ويبعت الإشعارات."""
        now = time.time()
        msgs, wmsgs, ran = [], [], set()
        if self.daily_sync_due():
            self.sync_steam_bg()
        with self.lock:
            forced, self.force = set(self.force), set()
        for name in ORDER:
            if not self.enabled(name):
                continue
            s = self.src[name]
            if not (force_all or name in forced or now >= s["next"]):
                continue
            try:
                getattr(self, "task_" + name)(now, msgs)
                s.update(ok=True, last=time.time(), fails=0, err="", next=time.time() + self.interval(name) * random.uniform(0.95, 1.1))
            except api.ApiError as e:
                s["fails"] += 1
                s.update(ok=False, err=str(e)[:140], next=time.time() + min(MAX_BACKOFF, 120 * 2 ** (s["fails"] - 1)))
                log.warning("%s: %s", name, e)
            except Exception as e:  # noqa
                s["fails"] += 1
                s.update(ok=False, err="خطأ داخلي: " + str(e)[:100], next=time.time() + 600)
                log.exception("task %s failed", name)
            ran.add(name)
        if ran & set(FREE_SOURCES):
            self._process_free(now, msgs)
        if ran:
            self._eval_wishlist(now, wmsgs)
            self._flush(msgs, wmsgs)
            self.touch()
            self._save_snapshot()
        elif self.held and not in_quiet():
            self._flush([], [])

    # ---------------- المصادر ----------------
    def task_epic(self, now, msgs):
        cur, up = api.epic_free()
        with self.lock:
            self.data["epic_cur"] = [{"id": "Epic:" + str(g["id"]), "dbkey": str(g["id"]), "title": g["title"], "store": "Epic",
                                      "url": g["url"], "image": g.get("image"), "normal": g["price"], "end_ts": _ts(g.get("end")),
                                      "start_ts": _ts(g.get("start")), "note": "", "app": None} for g in cur]
            self.data["epic_up"] = [{"title": g["title"], "image": g.get("image"), "normal": g.get("price", 0),
                                     "start_ts": _ts(g.get("start")), "end_ts": _ts(g.get("end"))} for g in up]

    def task_giveaways(self, now, msgs):
        items, _same = giveaways.fetch()
        with self.lock:
            self.data["giveaways"] = [{**g, "dbkey": g["id"], "app": None} for g in items]

    def task_steam_free(self, now, msgs):
        sf = api.steam_free()
        with self.lock:
            self.data["steam_free"] = [{"id": "Steam:" + str(d["game_id"] or d["id"]), "dbkey": str(d["game_id"] or d["id"]),
                                       "title": d["title"], "store": "Steam", "url": d["url"], "image": d["image"],
                                       "normal": d["normal"], "end_ts": None, "start_ts": None, "note": "", "app": d["app"]}
                                      for d in sf]

    def task_deals(self, now, msgs):
        deals = api.cheap_deals()
        if storage.get("sources").get("steam_specials", True):
            try:
                spec, _same = steamstore.fetch()
                by_app = {d["app"]: d for d in deals if d["app"] and d["store"] == "Steam"}
                for app, sp in spec.items():
                    if app in by_app:
                        by_app[app]["ends"] = sp["ends"]
                    elif sp["savings"] >= 50 and sp["sale"] > 0:
                        deals.append(sp)
                deals.sort(key=lambda d: -d["savings"])
            except api.ApiError as e:  # مصدر إضافي: فشله مبيوقّفش الباقي
                log.warning("steam specials: %s", e)
        lows = api.historic_lows([d["game_id"] for d in deals[:30] if not str(d["game_id"]).startswith("S")])
        with self.lock:
            self.data["deals"] = deals
            self.data["lows"].update(lows)
        for d in deals:
            db.log_event(d["store"], d["game_id"] or d["id"], d["title"], "deal", d["sale"], d["normal"], d["savings"], d["url"], now)
        self._deal_alerts(deals, lows, set(storage.get("owned")), msgs)

    def task_wishlist(self, now, msgs):
        items = alerts.items()
        owned = set(storage.get("owned"))
        todo = [i for i in items if not (i["app"] and i["app"] in owned)]
        if not todo:
            with self.lock:
                self.data["wl_deals"] = {}
            return
        gid_map = storage.get("gid_map")
        out, lows = api.wishlist_deals(todo, gid_map)
        storage.put("gid_map", gid_map)
        with self.lock:
            self.data["wl_deals"] = out
            self.data["lows"].update(lows)

    # ---------------- المجاني: دمج + تسجيل + إشعارات ----------------
    def merged_free(self):
        out, seen = [], set()
        with self.lock:
            pool = self.data["epic_cur"] + self.data["steam_free"] + self.data["giveaways"]
        for g in pool:
            n = norm(g["title"])
            if n in seen:
                continue
            seen.add(n)
            out.append({**g, "key": f"{g['store']}:{n}"})
        out.sort(key=lambda g: (g["end_ts"] is None, g["end_ts"] or 0))
        return out

    def _process_free(self, now, msgs):
        owned = set(storage.get("owned"))
        seen = storage.get("seen_free3")
        seeded = seen is not None
        seen = list(seen or [])
        have = set(seen)
        for g in self.merged_free():
            db.log_event(g["store"], g["dbkey"], g["title"], "free", 0.0, g["normal"], 100.0, g["url"], now)
            if g["key"] in have:
                continue
            seen.append(g["key"])
            have.add(g["key"])
            if seeded and storage.get("notify_free") and g.get("app") not in owned:
                was = f" (كانت ${g['normal']:.2f})" if g["normal"] else ""
                msgs.append((f"🎁 لعبة ببلاش على {g['store']}!", f"يا بطل، {g['title']} بقت مجانية دلوقتي{was}! الحق خدها",
                             g["title"], g["url"], g.get("image")))
        storage.put("seen_free3", seen[-800:])

    def _deal_alerts(self, deals, lows, owned, msgs):
        thr = storage.get("min_discount_alert")
        done = storage.get("notified_deals")
        seen, seeded = set(done), storage.get("seeded")
        for d in deals:
            key = f"{d['game_id']}:{d['sale']:.2f}"
            if d["savings"] < thr or key in seen:
                continue
            done.append(key)
            seen.add(key)
            if seeded and storage.get("notify_deals") and d["app"] not in owned:
                fire = " 🔥 أقل سعر في التاريخ" if api.is_historic_low(d, lows) else ""
                msgs.append(("🔥 خصم خرافي!", f"{d['title']}: -{d['savings']:.0f}% وبقت ${d['sale']:.2f} على {d['store']}{fire}",
                             d["title"], d["url"], d.get("image")))
        storage.put("notified_deals", done[-500:])
        storage.put("seeded", True)

    # ---------------- الأمنيات (تقييم محلي من غير نت) ----------------
    def _eval_wishlist(self, now, wmsgs):
        owned = set(storage.get("owned"))
        free = self.merged_free()
        last_all = storage.get("wl_last")
        status = {}
        items = alerts.items()
        with self.lock:
            wl_deals = dict(self.data["wl_deals"])
        for it in items:
            if it["app"] and it["app"] in owned:
                status[it["title"]] = None
                continue
            deal = wl_deals.get(it["title"])
            cands = ([deal] if deal else []) + [alerts.pseudo_deal(g) for g in free if alerts.free_match(it, g)]
            best = min(cands, key=lambda d: d["sale"]) if cands else None
            status[it["title"]] = best
            notify, new_last = alerts.decide(it, best, last_all.get(it["title"]), now)
            if new_last is None:
                last_all.pop(it["title"], None)
            else:
                last_all[it["title"]] = new_last
            if notify and storage.get("notify_wishlist") and best:
                what = "بقت ببلاش 🎁" if best["sale"] == 0 else f"عليها خصم -{best['savings']:.0f}% وبقت ${best['sale']:.2f}"
                wmsgs.append(("⭐ لعبة من قايمتك!", f"{best['title']} {what} على {best['store']}", best["title"],
                              best.get("url"), best.get("image")))
        storage.put("wl_last", last_all)
        with self.lock:
            self.data["wl_status"] = status

    # ---------------- إرسال الإشعارات ----------------
    def _flush(self, msgs, wmsgs):
        wset = {m[2].lower() for m in wmsgs}
        msgs = [m for m in msgs if m[2].lower() not in wset]  # من غير تكرار لو اللعبة في قايمتك
        out = wmsgs + msgs[:3]
        extra = len(msgs) - 3
        if in_quiet():
            self.held = (self.held + out + ([("🎯 صايد الفرصة", f"وفيه {extra} عروض تانية", "", None, None)] if extra > 0 else []))[-MAX_HELD:]
            return
        if self.held:
            out = self.held + out
            self.held = []
        for t, m, _k, url, img in out:
            self.notifier.send(t, m, url, img)
        if extra > 0:
            self.notifier.send("🎯 صايد الفرصة", f"وفيه {extra} عروض تانية، افتح البرنامج تشوفها")

    # ---------------- حساب Steam ----------------
    def link_by_id(self, steamid, key=""):
        info = steam.profile_info("id", steamid)
        storage.put("steam", {"id": info["id"], "name": info["name"], "key": (key or "").strip(), "last_sync": 0})
        self.touch()
        return info

    def link_account(self, text, key=""):
        kind, value = steam.parse_input(text)
        info = steam.profile_info(kind, value)
        if not info["public"]:
            raise steam.SteamError("البروفايل Private: خلّيه Public من Steam ← Edit Profile ← Privacy Settings")
        storage.put("steam", {"id": info["id"], "name": info["name"], "key": (key or "").strip(), "last_sync": 0})
        self.touch()
        return info, self.sync_steam()

    def unlink_account(self):
        storage.put("steam", {"id": "", "name": "", "key": "", "last_sync": 0})
        storage.put("owned", [])
        self.touch()

    def sync_steam(self):
        """بيستورد الويش ليست ويحدّث المكتبة. بيرجع ملخص أو None لو مفيش حساب."""
        st = storage.get("steam")
        if not st["id"]:
            return None
        wl = steam.wishlist(st["id"])
        owned = None
        for fn in ((lambda: steam.owned(st["id"], st["key"])) if st["key"] else None, lambda: steam.owned_public(st["id"])):
            if fn is None:
                continue
            try:
                owned = fn()
                break
            except (steam.SteamError, api.ApiError) as e:
                log.info("owned lookup: %s", e)
        have = {i["app"] for i in alerts.items() if i["app"]}
        gid_map, added = storage.get("gid_map"), 0
        for app in wl:
            if app in have or (owned and app in owned) or added >= 100:
                continue
            title, gid = steam.game_info(app)
            if gid:
                gid_map[str(app)] = {"gid": gid, "ts": time.time()}
            if alerts.add_item(title, mode="50", repeat="change", app=app):
                added += 1
            if self.stop.wait(0.2):
                break
        storage.put("gid_map", gid_map)
        if owned is not None:
            storage.put("owned", sorted(owned))
        storage.put("steam", {**storage.get("steam"), "last_sync": time.time()})
        self.touch()
        if added:
            self.check_now("wishlist")
        return {"wishlist": len(wl), "added": added, "owned": None if owned is None else len(owned)}

    def sync_steam_bg(self):
        """نسخة في خيط لوحدها (الواجهة مبتستناش). بترجع False لو فيه مزامنة شغالة."""
        with self.lock:
            if self.syncing:
                return False
            self.syncing = True

        def run():
            try:
                self.sync_steam()
            except Exception as e:  # noqa
                log.warning("steam sync: %s", e)
            finally:
                with self.lock:
                    self.syncing = False
                self.touch()

        threading.Thread(target=run, daemon=True).start()
        return True

    def daily_sync_due(self):
        st = storage.get("steam")
        return bool(st["id"]) and time.time() - st.get("last_sync", 0) > 86400

    # ---------------- لقطة للواجهة ----------------
    def state(self):
        owned = set(storage.get("owned"))
        with self.lock:
            lows = dict(self.data["lows"])
            deals = [{**d, "low": api.is_historic_low(d, lows), "owned": bool(d["app"] and d["app"] in owned)}
                     for d in self.data["deals"]]
            wl_status = dict(self.data.get("wl_status", {}))
            up = list(self.data["epic_up"])
            src = {n: dict(s) for n, s in self.src.items()}
        free = [{**g, "owned": bool(g.get("app") and g["app"] in owned)} for g in self.merged_free()]
        wl = []
        for it in alerts.items():
            wl.append({**it, "status": wl_status.get(it["title"]), "owned": bool(it["app"] and it["app"] in owned)})
        st = storage.get("steam")
        tg = storage.get("telegram")
        return {
            "rev": self.rev, "now": time.time(), "version": __version__, "free": free, "upcoming": up, "deals": deals,
            "wishlist": wl, "summary": db.summary(), "saved": storage.get("saved_total"),
            "claimed": storage.get("claimed"), "bought": storage.get("bought"),
            "sources": {n: {"label": LABELS[n], "enabled": self.enabled(n), **{k: src[n][k] for k in ("ok", "last", "err", "next")}}
                        for n in ORDER},
            "steam": {"linked": bool(st["id"]), "name": st["name"], "id": st["id"], "last_sync": st.get("last_sync", 0),
                      "owned": len(owned), "has_key": bool(st["key"]), "syncing": self.syncing},
            "settings": {"mode": storage.get("mode"), "notify": storage.get("notify"), "notify_free": storage.get("notify_free"),
                         "notify_deals": storage.get("notify_deals"), "notify_wishlist": storage.get("notify_wishlist"),
                         "min_discount_alert": storage.get("min_discount_alert"), "quiet": storage.get("quiet"),
                         "sources": storage.get("sources"), "has_discord": bool(storage.get("discord")),
                         "has_telegram": bool(tg.get("token")), "telegram_chat": tg.get("chat", "")},
            "channels": self.notifier.channels(), "quiet_now": in_quiet(), "held": len(self.held),
            "stores": sorted(set(d["store"] for d in deals)),
        }
