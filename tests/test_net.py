import gzip
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dealhunter import api, giveaways, steam, steamstore, storage  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        storage.DIR = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestNetLayer(Base):
    def test_gzip_etag_and_304(self):
        hits = {"full": 0, "304": 0}
        payload = json.dumps({"hello": "عالم"}).encode()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("If-None-Match") == '"v1"':
                    hits["304"] += 1
                    self.send_response(304)
                    self.end_headers()
                    return
                hits["full"] += 1
                body = gzip.compress(payload)
                self.send_response(200)
                self.send_header("ETag", '"v1"')
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/x"
        try:
            d1, same1 = api.get_json_ex(url, conditional=True)
            d2, same2 = api.get_json_ex(url, conditional=True)
        finally:
            srv.shutdown()
        srv.server_close()
        self.assertEqual((d1, same1), ({"hello": "عالم"}, False))
        self.assertEqual((d2, same2), ({"hello": "عالم"}, True))   # 304 ← النسخة المحفوظة، من غير تحميل
        self.assertEqual(hits, {"full": 1, "304": 1})

    def test_http_error_becomes_apierror(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(api.ApiError) as c:
                api.fetch_ex(f"http://127.0.0.1:{srv.server_address[1]}/", retries=0)
        finally:
            srv.shutdown()
        srv.server_close()
        self.assertIn("404", str(c.exception))


class TestSources(Base):
    def test_giveaways_parse(self):
        now = time.mktime((2026, 11, 22, 12, 0, 0, 0, 0, 0))
        rows = [
            {"id": 1, "title": "Hades II (Epic Games) Giveaway", "worth": "$29.99", "platforms": "PC, Epic Games Store", "type": "Game", "status": "Active",
             "end_date": "2099-01-01 23:59:00", "open_giveaway_url": "https://gp/open/1", "image": "https://i/1.jpg", "instructions": "Login\nclaim"},
            {"id": 2, "title": "Old One Giveaway", "worth": "N/A", "platforms": "PC, Steam", "type": "Game", "status": "Active", "end_date": "2001-01-01 00:00:00"},
            {"id": 3, "title": "Some DLC Giveaway", "worth": "$1", "platforms": "PC", "type": "DLC", "status": "Active", "end_date": "N/A"},
            {"id": 4, "title": "Forever Free", "worth": "N/A", "platforms": "PC, itch.io", "type": "Game", "status": "Active", "end_date": "N/A"},
            {"oops": True},
        ]
        out = giveaways.parse(rows, now)
        self.assertEqual([g["title"] for g in out], ["Hades II", "Forever Free"])
        self.assertEqual((out[0]["store"], out[0]["normal"], out[0]["url"]), ("Epic", 29.99, "https://gp/open/1"))
        self.assertEqual(out[0]["note"], "Login claim")
        self.assertIsNone(out[1]["end_ts"])
        self.assertEqual(out[1]["store"], "itch.io")
        with self.assertRaises(api.ApiError):
            giveaways.parse({"status": 0})

    def test_steam_specials(self):
        data = {"specials": {"items": [
            {"id": 10, "name": "Half Off", "discount_percent": 50, "original_price": 2000, "final_price": 1000, "discount_expiration": 1800000000,
             "header_image": "https://h/10.jpg"},
            {"id": 11, "name": "No Discount", "discount_percent": 0, "original_price": 500, "final_price": 500}, {"id": "x"}]}}
        out = steamstore.parse_specials(data)
        self.assertEqual(list(out), [10])
        self.assertEqual((out[10]["sale"], out[10]["normal"], out[10]["savings"], out[10]["ends"]), (10.0, 20.0, 50.0, 1800000000))
        with self.assertRaises(api.ApiError):
            steamstore.parse_specials({})

    def test_stores_refresh_keeps_trusted_only(self):
        orig, saved = api.get_json, dict(api.STORES)
        api.get_json = lambda url, timeout=12: [
            {"storeID": "1", "storeName": "Steam", "isActive": 1}, {"storeID": "11", "storeName": "Humble Store", "isActive": 1},
            {"storeID": "3", "storeName": "GreenManGaming", "isActive": 1}, {"storeID": "99", "storeName": "Shady Keys Ltd", "isActive": 1},
            {"storeID": "7", "storeName": "GOG", "isActive": 0}]
        try:
            ids = api.refresh_stores()
            self.assertEqual(ids, "1,3,11")
            self.assertEqual(api.STORES["11"], "Humble")
        finally:
            api.get_json = orig
            api.STORES.clear()
            api.STORES.update(saved)

    def test_wishlist_batching_and_gid_cache(self):
        calls = []

        def fake(url, timeout=12):
            calls.append(url)
            if "steamAppID=1091500" in url:
                return [{"gameID": "10", "external": "Cyberpunk 2077"}]
            if "title=" in url:
                return [{"gameID": "20", "external": "Hades II"}, {"gameID": "21", "external": "Hades"}]
            if "/games?ids=" in url:
                return {"10": {"info": {"title": "Cyberpunk 2077", "steamAppID": "1091500"}, "cheapestPriceEver": {"price": "14.99"},
                               "deals": [{"storeID": "1", "dealID": "D1", "price": "20.00", "retailPrice": "59.99", "savings": "66.6"},
                                         {"storeID": "7", "dealID": "D2", "price": "14.99", "retailPrice": "59.99", "savings": "75.0"},
                                         {"storeID": "666", "dealID": "D3", "price": "1.00", "retailPrice": "59.99", "savings": "98"}]},
                        "20": {"info": {"title": "Hades II", "steamAppID": "0", "thumb": "http://t"}, "cheapestPriceEver": {"price": "20"}, "deals": []}}
            raise AssertionError(url)

        orig, stores = api.get_json, dict(api.STORES)
        api.get_json = fake
        api.STORES.clear()
        api.STORES.update({"1": "Steam", "7": "GOG"})
        items = [{"title": "Cyberpunk 2077", "app": 1091500}, {"title": "hades ii", "app": None}]
        gid_map = {}
        try:
            out, lows = api.wishlist_deals(items, gid_map)
            first = len(calls)
            out2, _ = api.wishlist_deals(items, gid_map)
        finally:
            api.get_json = orig
            api.STORES.clear()
            api.STORES.update(stores)
        self.assertEqual(first, 3)                       # 2 للـ GameID (مرة واحدة) + طلب واحد للتلاتة
        self.assertEqual(len(calls) - first, 1)           # تاني مرة: طلب واحد بس
        d = out["Cyberpunk 2077"]
        self.assertEqual((d["sale"], d["store"], d["id"]), (14.99, "GOG", "D2"))   # أرخص عرض في متجر موثوق (666 اتتجاهل)
        self.assertIsNone(out["hades ii"])
        self.assertEqual(lows["10"], 14.99)
        self.assertEqual(out2["Cyberpunk 2077"]["sale"], 14.99)


class TestDealUrl(unittest.TestCase):
    def test_no_double_encoding_and_steam_direct(self):
        enc = "AbC%2BdEf%2FgH%3D"      # dealID زي ما CheapShark بترجّعه (متشفّر أصلاً)
        self.assertEqual(api.deal_url(enc, "GOG", None), "https://www.cheapshark.com/redirect?dealID=" + enc)
        self.assertEqual(api.deal_url("raw+id=", "GOG", None), "https://www.cheapshark.com/redirect?dealID=raw%2Bid%3D")
        self.assertEqual(api.deal_url(enc, "Steam", "1091500"), "https://store.steampowered.com/app/1091500")
        self.assertTrue(api.deal_url(enc, "Steam", "0").startswith("https://www.cheapshark.com/redirect"))


class TestSteamLogin(Base):
    def test_openid_verification(self):
        ret = "http://127.0.0.1:1/steam/callback?state=%s"
        sid = "76561198000000000"

        def params(state, **over):
            p = {"openid.claimed_id": f"https://steamcommunity.com/openid/id/{sid}", "openid.return_to": ret % state,
                 "openid.mode": "id_res", "openid.sig": "x", "openid.signed": "a"}
            p.update(over)
            return p

        orig = api.fetch_ex
        api.fetch_ex = lambda url, **kw: (b"ns:http://specs.openid.net/auth/2.0\nis_valid:true\n", False)
        try:
            st = steam.new_state()
            self.assertEqual(steam.verify_login(params(st), st, ret % st), sid)
            with self.assertRaises(steam.SteamError):          # نفس الـ state مينفعش يتستعمل مرتين
                steam.verify_login(params(st), st, ret % st)
            st = steam.new_state()
            with self.assertRaises(steam.SteamError):          # return_to متغيّر
                steam.verify_login(params(st, **{"openid.return_to": "http://evil/"}), st, ret % st)
            st = steam.new_state()
            with self.assertRaises(steam.SteamError):          # claimed_id مش من Steam
                steam.verify_login(params(st, **{"openid.claimed_id": "https://evil.com/openid/id/" + sid}), st, ret % st)
            api.fetch_ex = lambda url, **kw: (b"is_valid:false\n", False)   # Steam رفضت التوقيع
            st = steam.new_state()
            with self.assertRaises(steam.SteamError):
                steam.verify_login(params(st), st, ret % st)
        finally:
            api.fetch_ex = orig

    def test_owned_public(self):
        orig = api.fetch_bytes
        api.fetch_bytes = lambda url, timeout=12: b"<gamesList><games><game><appID>10</appID></game><game><appID>20</appID></game></games></gamesList>"
        try:
            self.assertEqual(steam.owned_public("76561198000000000"), {10, 20})
            api.fetch_bytes = lambda url, timeout=12: b"<response><error><![CDATA[private]]></error></response>"
            with self.assertRaises(steam.SteamError):
                steam.owned_public("76561198000000000")
        finally:
            api.fetch_bytes = orig


if __name__ == "__main__":
    unittest.main()
