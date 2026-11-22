import http.client
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dealhunter import alerts, api, steam, storage  # noqa: E402
from dealhunter.engine import Engine  # noqa: E402
from dealhunter.server import Server  # noqa: E402


class FakeNotifier:
    toast = None

    def send(self, *a, **k):
        pass

    def channels(self):
        return {"toast": True, "discord": False, "telegram": False}

    def test(self, ch):
        return True, "ok"


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        storage.DIR = Path(self.tmp.name)
        self.eng = Engine(FakeNotifier())
        self.eng.sync_steam_bg = lambda: True
        self.srv = Server(self.eng, FakeNotifier(), port=0).start()

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        if token:
            req.add_header("X-Token", self.srv.token)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def j(self, *a, **k):
        code, raw = self.call(*a, **k)
        return code, json.loads(raw)

    def test_auth_and_host(self):
        self.assertEqual(self.call("GET", "/", token=False)[0], 403)
        code, page = self.call("GET", f"/?t={self.srv.token}", token=False)
        self.assertEqual(code, 200)
        self.assertIn("صايد الفرصة".encode(), page)
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/state", token=True)[0], 200)
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port)   # DNS-rebinding: Host غريب
        c.request("GET", "/api/state", headers={"Host": "evil.com", "X-Token": self.srv.token})
        self.assertEqual(c.getresponse().status, 403)

    def test_state_rev_short_circuit(self):
        _, st = self.j("GET", "/api/state")
        _, same = self.j("GET", f"/api/state?rev={st['rev']}")
        self.assertEqual(same, {"same": True, "rev": st["rev"]})

    def test_settings_validation_and_secret_masking(self):
        self.assertEqual(self.j("POST", "/api/settings", {"mode": "eco"})[0], 200)
        self.assertEqual(storage.get("mode"), "eco")
        self.assertEqual(self.j("POST", "/api/settings", {"discord": "https://evil.com/x"})[0], 400)
        hook = "https://discord.com/api/webhooks/123456/SECRETtoken"
        self.assertEqual(self.j("POST", "/api/settings", {"discord": hook})[0], 200)
        code, raw = self.call("GET", "/api/state")
        self.assertNotIn(b"SECRETtoken", raw)
        self.assertTrue(json.loads(raw)["settings"]["has_discord"])
        self.assertEqual(self.j("POST", "/api/settings", {"quiet": {"on": True, "from": "25:99", "to": "08:00"}})[0], 400)
        self.assertEqual(self.j("POST", "/api/settings", {"sources": {"deals": False, "hack": True}})[0], 200)
        self.assertEqual(storage.get("sources")["deals"], False)
        self.assertNotIn("hack", storage.get("sources"))

    def test_wishlist_and_mark(self):
        self.assertEqual(self.j("POST", "/api/wishlist/add", {"title": "Hades II", "mode": "75", "app": 1145350, "gid": "20"})[0], 200)
        self.assertEqual(self.j("POST", "/api/wishlist/add", {"title": "hades ii"})[0], 400)   # مكرر
        self.assertEqual(self.j("POST", "/api/wishlist/add", {"title": "X", "mode": "bad"})[0], 400)
        self.assertEqual(storage.get("gid_map")["1145350"]["gid"], "20")
        self.j("POST", "/api/wishlist/update", {"title": "Hades II", "max_price": "9.5", "repeat": "6h"})
        it = alerts.items()[0]
        self.assertEqual((it["max_price"], it["repeat"]), (9.5, "6h"))
        _, r1 = self.j("POST", "/api/mark", {"kind": "claimed", "id": "k", "amount": 10})
        _, r2 = self.j("POST", "/api/mark", {"kind": "claimed", "id": "k", "amount": 10})
        self.assertEqual((r1["added"], r2["added"], r2["saved"]), (True, False, 10.0))   # بيتحسب مرة واحدة
        self.j("POST", "/api/wishlist/remove", {"title": "Hades II"})
        self.assertEqual(alerts.items(), [])

    def test_open_rejects_bad_urls(self):
        self.assertEqual(self.j("POST", "/api/open", {"url": "file:///etc/passwd"})[0], 400)
        self.assertEqual(self.j("POST", "/api/open", {"url": "javascript:alert(1)"})[0], 400)

    def test_pc_endpoints(self):
        self.assertEqual(self.j("POST", "/api/pc/scan", {"apps": []})[0], 400)
        code, r = self.j("POST", "/api/pc/save", {"gpu": "RTX 3060", "ram_gb": "32", "cpu": "", "vram_gb": "abc"})
        self.assertEqual(code, 200)
        self.assertEqual(storage.get("pc_override"), {"gpu": "RTX 3060", "ram_gb": 32.0})   # القيم الغلط/الفاضية بتتتجاهل
        self.assertEqual(r["spec"]["gpu"], "RTX 3060")
        _, st = self.j("GET", "/api/state")
        self.assertIn("canrun", st)
        self.assertIn("spec", st["pc"])

    def test_unknown_and_bad_body(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.j("POST", "/api/settings", [1, 2])[0], 400)

    def test_steam_callback_links_account(self):
        orig = api.fetch_ex
        xml = b"<profile><steamID64>76561198000000000</steamID64><steamID><![CDATA[Solstice]]></steamID><privacyState>public</privacyState></profile>"
        api.fetch_ex = lambda url, timeout=12, headers=None, data=None, conditional=False, retries=1: ((b"is_valid:true", False) if data else (xml, False))
        try:
            state = steam.new_state()
            ret = f"http://127.0.0.1:{self.srv.port}/steam/callback?state={state}"
            q = urllib.parse.urlencode({"state": state, "openid.mode": "id_res", "openid.return_to": ret,
                                        "openid.claimed_id": "https://steamcommunity.com/openid/id/76561198000000000"})
            req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}/steam/callback?{q}")
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
            self.assertEqual(storage.get("steam")["id"], "76561198000000000")
            with self.assertRaises(urllib.error.HTTPError) as e:     # إعادة نفس الرابط (replay) مرفوضة
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)
        finally:
            api.fetch_ex = orig


if __name__ == "__main__":
    unittest.main()
