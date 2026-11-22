import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dealhunter import alerts, api, db, giveaways, notify, steam, steamstore, storage
from dealhunter.engine import Engine, in_quiet


def deal(id_="d1", sale=10.0, savings=80.0, title="Cyberpunk 2077", **kw):
    d = {"id": id_, "game_id": "10", "title": title, "sale": sale, "normal": 50.0, "savings": savings, "store": "Steam",
         "image": None, "url": "u", "metacritic": 0, "app": 1091500}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        storage.DIR = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestAlerts(Base):
    def item(self, **kw):
        return alerts.normalize({"title": "Cyberpunk", **kw})

    def test_qualifies(self):
        self.assertTrue(alerts.qualifies(self.item(mode="75"), deal()))
        self.assertFalse(alerts.qualifies(self.item(mode="75"), deal(savings=60)))
        self.assertFalse(alerts.qualifies(self.item(mode="free"), deal()))
        self.assertTrue(alerts.qualifies(self.item(mode="free"), deal(sale=0.0, savings=100)))
        self.assertFalse(alerts.qualifies(self.item(max_price=5), deal()))

    def test_repeat_modes(self):
        d, t = deal(), 1000.0
        n, last = alerts.decide(self.item(repeat="once"), d, None, t)
        self.assertTrue(n)
        self.assertFalse(alerts.decide(self.item(repeat="once"), d, last, t + 99999)[0])
        self.assertTrue(alerts.decide(self.item(repeat="change"), deal(sale=8.0), last, t + 10)[0])
        self.assertFalse(alerts.decide(self.item(repeat="change"), d, last, t + 10)[0])
        self.assertFalse(alerts.decide(self.item(repeat="6h"), d, last, t + 3600)[0])
        self.assertTrue(alerts.decide(self.item(repeat="6h"), d, last, t + 6 * 3600)[0])
        self.assertTrue(alerts.decide(self.item(repeat="always"), d, last, t + 1)[0])
        self.assertEqual(alerts.decide(self.item(), None, last, t), (False, None))  # العرض خلص = تصفير

    def test_wishlist_storage(self):
        self.assertTrue(alerts.add_item("Hades", mode="free", repeat="always"))
        self.assertFalse(alerts.add_item("hades"))
        alerts.update_item("Hades", repeat="6h")
        self.assertEqual(alerts.items()[0]["repeat"], "6h")
        storage.put("wishlist", ["Old string item"])
        self.assertEqual(alerts.items()[0]["mode"], "any")
        alerts.remove_item("Old string item")
        self.assertEqual(alerts.items(), [])


class TestDb(Base):
    def test_episodes_and_stats(self):
        t = time.time()
        self.assertTrue(db.log_event("Epic", "a", "Game A", "free", 0.0, 30, 100, "u", t))
        self.assertFalse(db.log_event("Epic", "a", "Game A", "free", 0.0, 30, 100, "u", t + 3600))  # نفس العرض
        self.assertTrue(db.log_event("Epic", "a", "Game A", "free", 0.0, 30, 100, "u", t + 10 * 86400))  # عرض جديد
        db.log_event("Steam", "b", "Game B", "free", 0.0, 20, 100, "u", t)
        db.log_event("Steam", "c", "Game C", "deal", 5.0, 50, 90, "u", t)
        db.log_event("Steam", "c", "Game C", "deal", 7.0, 50, 86, "u", t)
        s = db.summary()
        self.assertEqual((s["free_n"], s["deal_n"], s["free_value"]), (3, 2, 80))
        self.assertEqual(db.top_free()[0]["title"], "Game A")
        self.assertEqual(db.top_free()[0]["n"], 2)
        self.assertEqual(db.top_deals_count()[0]["n"], 2)
        self.assertEqual(db.top_deals_best()[0]["best"], 90)
        self.assertEqual(len(db.recent(kind="free", store="Steam")), 1)
        self.assertEqual(len(db.recent(search="game a")), 2)
        out = os.path.join(self.tmp.name, "h.csv")
        self.assertEqual(db.export_csv(out), 5)
        db.clear()
        self.assertEqual(db.summary()["free_n"], 0)


class TestSteam(Base):
    def test_parse_input(self):
        self.assertEqual(steam.parse_input("76561198000000000"), ("id", "76561198000000000"))
        self.assertEqual(steam.parse_input("https://steamcommunity.com/id/solstice/"), ("vanity", "solstice"))
        self.assertEqual(steam.parse_input("https://steamcommunity.com/profiles/76561198000000000/"), ("id", "76561198000000000"))
        with self.assertRaises(steam.SteamError):
            steam.parse_input("!!")

    def test_link_and_sync(self):
        xml = b"<profile><steamID64>76561198000000000</steamID64><steamID><![CDATA[Solstice]]></steamID><privacyState>public</privacyState></profile>"
        orig = (api.fetch_bytes, steam.wishlist, steam.owned, steam.game_info)
        api.fetch_bytes = lambda url, timeout=12: xml
        steam.wishlist = lambda sid: [1, 2, 3]
        steam.owned = lambda sid, key: {3}
        steam.game_info = lambda app: (f"Game {app}", str(app * 10))
        eng = Engine(FakeNotifier())
        try:
            info, res = eng.link_account("solstice", "KEY")
        finally:
            api.fetch_bytes, steam.wishlist, steam.owned, steam.game_info = orig
        self.assertEqual(info["name"], "Solstice")
        self.assertEqual(res, {"wishlist": 3, "added": 2, "owned": 1})
        self.assertEqual(storage.get("owned"), [3])
        self.assertEqual(sorted(i["app"] for i in alerts.items()), [1, 2])
        self.assertEqual(storage.get("gid_map")["1"]["gid"], "10")   # الـ GameID اتحفظ فمفيش طلب تاني
        eng.unlink_account()
        self.assertEqual(storage.get("steam")["id"], "")


class FakeNotifier:
    def __init__(self):
        self.sent, self.toast = [], None

    def send(self, title, msg, url=None, image=None, only=None):
        self.sent.append((title, msg))

    def channels(self):
        return {"toast": True, "discord": False, "telegram": False}


class TestEngine(Base):
    def setUp(self):
        super().setUp()
        self.orig = (api.epic_free, api.steam_free, api.cheap_deals, api.historic_lows, api.wishlist_deals, giveaways.fetch, steamstore.fetch)
        self.epic = [{"id": "e1", "title": "Epic Freebie", "price": 29.99, "url": "eu", "image": None}]
        self.sfree, self.gp = [], []
        self.deals = [deal("x1", 4.0, 92.0, "Big Sale Game", game_id="77", app=5)]
        self.wl = {}
        api.epic_free = lambda: (self.epic, [])
        api.steam_free = lambda: self.sfree
        api.cheap_deals = lambda: self.deals
        api.historic_lows = lambda ids: {"77": 4.0}
        api.wishlist_deals = lambda items, gid_map: ({i["title"]: self.wl.get(i["title"]) for i in items}, {})
        giveaways.fetch = lambda: (self.gp, False)
        steamstore.fetch = lambda: ({}, False)
        self.n = FakeNotifier()
        self.eng = Engine(self.n)

    def tearDown(self):
        (api.epic_free, api.steam_free, api.cheap_deals, api.historic_lows, api.wishlist_deals, giveaways.fetch, steamstore.fetch) = self.orig
        super().tearDown()

    def test_cycles(self):
        e, n = self.eng, self.n
        e.run_due(force_all=True)
        self.assertEqual(n.sent, [])           # أول مرة: بيسجّل بس من غير إشعارات
        self.assertTrue(all(s["ok"] for s in e.src.values()))
        self.assertEqual(db.summary()["free_n"], 1)
        # لعبة Epic جديدة + Steam جديدة + خصم جديد + لعبتين من الويش ليست
        self.epic.append({"id": "e2", "title": "Hades II", "price": 25.0, "url": "eu2", "image": None})
        self.sfree = [deal("s1", 0.0, 100.0, "Steam Free Game", game_id="99", normal=19.99, app=7)]
        for i in range(2, 5):
            self.deals.append(deal(f"x{i}", 3.0, 95.0, f"Another Deal {i}", game_id=str(80 + i), app=i))
        alerts.add_item("Cyberpunk", mode="75", repeat="always")
        alerts.add_item("hades", mode="free", repeat="once")
        self.wl = {"Cyberpunk": deal("c1", 12.0, 80.0)}
        n.sent.clear()
        e.run_due(force_all=True)
        text = " | ".join(m for _, m in n.sent)
        self.assertIn("Hades II", text)
        self.assertIn("Cyberpunk", text)
        self.assertIn("1 عروض تانية", text)
        self.assertEqual(len(n.sent), 6)            # 2 من الويش ليست + 3 عادية + ملخّص
        self.assertEqual(text.count("Hades II"), 1)  # من غير تكرار
        n.sent.clear()
        e.run_due(force_all=True)                    # "always" بيبعت تاني، و"once" لأ
        msgs = " | ".join(m for _, m in n.sent)
        self.assertIn("Cyberpunk", msgs)
        self.assertNotIn("Hades II", msgs)
        self.assertEqual(db.summary()["free_n"], 3)

    def test_free_sources_merge_and_dedupe(self):
        self.epic = [{"id": "e1", "title": "Hades II", "price": 25.0, "url": "eu", "image": None}]
        self.gp = [{"id": "GP:1", "title": "Hades II", "store": "Epic", "url": "g", "image": None, "normal": 25.0, "end_ts": 5.0, "start_ts": None, "note": "",
                    "dbkey": "GP:1", "app": None},
                   {"id": "GP:2", "title": "Itch Gem", "store": "itch.io", "url": "g2", "image": None, "normal": 5.0, "end_ts": None, "start_ts": None,
                    "note": "", "dbkey": "GP:2", "app": None}]
        self.eng.run_due(force_all=True)
        titles = [g["title"] for g in self.eng.merged_free()]
        self.assertEqual(sorted(titles), ["Hades II", "Itch Gem"])  # نفس اللعبة من مصدرين = مرة واحدة
        self.assertEqual(db.summary()["free_n"], 2)

    def test_failure_backoff_and_isolation(self):
        def boom():
            raise api.ApiError("HTTP 503")
        api.epic_free = boom
        self.eng.run_due(force_all=True)
        s = self.eng.src
        self.assertFalse(s["epic"]["ok"])
        self.assertTrue(s["deals"]["ok"])           # فشل مصدر مبيوقّفش الباقي
        self.assertGreater(s["epic"]["next"], time.time() + 100)
        self.assertEqual(s["epic"]["fails"], 1)

    def test_disabled_source_is_skipped(self):
        calls = []
        api.cheap_deals = lambda: calls.append(1) or []
        storage.put("sources", {"epic": True, "giveaways": True, "steam_free": True, "deals": False, "steam_specials": True})
        self.eng.run_due(force_all=True)
        self.assertEqual(calls, [])

    def test_not_due_means_no_network(self):
        calls = []
        api.cheap_deals = lambda: calls.append(1) or []
        self.eng.run_due()          # أول مرة: كله مستحق
        self.eng.run_due()          # تاني مرة على طول: مفيش حاجة مستحقة
        self.assertEqual(len(calls), 1)

    def test_quiet_hours_hold_then_release(self):
        e, n = self.eng, self.n
        e.run_due(force_all=True)
        t = time.localtime()
        storage.put("quiet", {"on": True, "from": f"{t.tm_hour:02d}:00", "to": f"{(t.tm_hour + 2) % 24:02d}:00"})
        self.assertTrue(in_quiet())
        self.epic.append({"id": "e2", "title": "Hades II", "price": 25.0, "url": "eu2", "image": None})
        n.sent.clear()
        e.run_due(force_all=True)
        self.assertEqual(n.sent, [])
        self.assertEqual(len(e.held), 1)             # اتأجّل مش اتمسح
        storage.put("quiet", {"on": False, "from": "00:00", "to": "08:00"})
        e.run_due()
        self.assertEqual(len(n.sent), 1)
        self.assertEqual(e.held, [])

    def test_state_snapshot_roundtrip(self):
        self.eng.run_due(force_all=True)
        st = self.eng.state()
        self.assertEqual(st["free"][0]["title"], "Epic Freebie")
        self.assertEqual(st["deals"][0]["low"], True)         # 4.0 == أقل سعر تاريخي
        self.assertNotIn("discord", str(st["settings"]).replace("has_discord", ""))
        again = Engine(FakeNotifier())                        # بيحمّل من الكاش فالواجهة تفتح فوراً
        self.assertEqual(again.data["deals"][0]["title"], "Big Sale Game")


class TestNotify(Base):
    def test_validators(self):
        self.assertTrue(notify.valid_discord("https://discord.com/api/webhooks/123456/abc-DEF_9"))
        self.assertFalse(notify.valid_discord("https://evil.com/api/webhooks/123/abc"))
        self.assertFalse(notify.valid_discord("http://discord.com/api/webhooks/123/abc"))
        self.assertTrue(notify.valid_telegram("123456789:" + "A" * 35, "-100123"))
        self.assertTrue(notify.valid_telegram("123456789:" + "A" * 35, "@mychannel"))
        self.assertFalse(notify.valid_telegram("nope", "1"))

    def test_discord_payload(self):
        seen = {}
        orig = api.fetch_ex
        api.fetch_ex = lambda url, **kw: seen.update(url=url, body=kw["data"]) or (b"", False)
        try:
            notify.send_discord("https://discord.com/api/webhooks/1/a", "T", "msg", "https://u", "https://i.png")
        finally:
            api.fetch_ex = orig
        import json
        emb = json.loads(seen["body"])["embeds"][0]
        self.assertEqual((emb["title"], emb["url"], emb["image"]["url"]), ("T", "https://u", "https://i.png"))


if __name__ == "__main__":
    unittest.main()
