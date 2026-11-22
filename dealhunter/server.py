"""سيرفر محلي (127.0.0.1 بس) بيقدّم الواجهة + API بسيط. محمي بتوكن عشوائي لكل تشغيل
وبفحص Host، فمفيش موقع تاني على النت يقدر يكلّمه."""
import json
import re
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from . import alerts, api, autostart, db, notify, share, steam, storage
from .engine import MODES, in_quiet
from .logs import log, path as log_path

PREFERRED_PORT = 47654
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CSP = ("default-src 'none'; img-src 'self' https: data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SRC_KEYS = ("epic", "giveaways", "steam_free", "deals", "steam_specials")


def resource(rel):
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
    p = Path(base) / rel
    return p if p.exists() else Path(__file__).resolve().parent / Path(rel).name


class ApiFail(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _num(v, lo, hi, default=None):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


class Server:
    def __init__(self, engine, notifier, on_quit=None, port=None):
        self.engine, self.notifier, self.on_quit = engine, notifier, on_quit or (lambda: None)
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        self.port = port
        self.html = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    # ---------------- تشغيل ----------------
    def start(self):
        outer = self
        handler = type("H", (_Handler,), {"app": outer})
        ThreadingHTTPServer.daemon_threads = True
        for port in ([self.port] if self.port is not None else [PREFERRED_PORT, 0]):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            raise OSError("مقدرتش أفتح بورت محلي")
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def page(self):
        if self.html is None:
            self.html = resource("dealhunter/web/index.html").read_bytes()
        return self.html

    # ---------------- GET ----------------
    def get_state(self, q, body):
        if q.get("rev") == str(self.engine.rev):
            return {"same": True, "rev": self.engine.rev}
        st = self.engine.state()
        st["autostart"] = autostart.is_enabled()
        st["asked_autostart"] = storage.get("asked_autostart")
        st["windows"] = sys.platform == "win32"
        return st

    def get_stats(self, q, body):
        return {"summary": db.summary(), "top_free": db.top_free(), "top_deals": db.top_deals_count(),
                "best": db.top_deals_best(), "monthly": db.monthly()}

    def get_history(self, q, body):
        kind = {"free": "free", "deal": "deal"}.get(q.get("kind"))
        return {"rows": db.recent(limit=int(_num(q.get("limit"), 1, 1000, 300)), kind=kind,
                                  store=q.get("store") or None, search=(q.get("q") or "").strip() or None)}

    def get_search(self, q, body):
        try:
            return {"results": api.search_games(q.get("q", ""))}
        except api.ApiError as e:
            raise ApiFail("البحث مش شغال دلوقتي: " + str(e)[:80], 502)

    def get_diagnose(self, q, body):
        return {"rows": [{"name": n, "ok": ok, "detail": d} for n, ok, d in api.diagnose()]}

    def get_log(self, q, body):
        try:
            data = log_path().read_bytes()[-12000:].decode("utf-8", "ignore")
        except OSError:
            data = "(السجل فاضي)"
        return {"text": data}

    # ---------------- POST ----------------
    def post_check(self, q, b):
        self.engine.check_now(*[n for n in (b.get("sources") or []) if isinstance(n, str)])
        return {"ok": True}

    def post_open(self, q, b):
        url = str(b.get("url") or "")
        if not re.match(r"^https?://[^\s]+$", url):
            raise ApiFail("رابط مش صالح")
        webbrowser.open(url)
        return {"ok": True}

    def post_quit(self, q, b):
        threading.Thread(target=lambda: (time.sleep(0.3), self.on_quit()), daemon=True).start()
        return {"ok": True}

    def post_mark(self, q, b):
        kind = b.get("kind")
        if kind not in ("claimed", "bought") or not b.get("id"):
            raise ApiFail("طلب مش صحيح")
        amount = _num(b.get("amount"), 0, 5000, 0)
        added = storage.add_saved(amount, kind, str(b["id"]))
        self.engine.touch()
        return {"ok": True, "added": added, "saved": storage.get("saved_total")}

    def post_wishlist_add(self, q, b):
        title = str(b.get("title") or "").strip()[:120]
        if len(title) < 2:
            raise ApiFail("اكتب اسم اللعبة")
        mode, rep = b.get("mode", "any"), b.get("repeat", "change")
        if mode not in alerts.MODES or rep not in alerts.REPEATS:
            raise ApiFail("إعداد تنبيه مش صحيح")
        app = int(b["app"]) if str(b.get("app") or "").isdigit() else None
        if not alerts.add_item(title, mode, rep, _num(b.get("max_price"), 0, 5000), app):
            raise ApiFail("اللعبة دي موجودة في قايمتك أصلاً")
        gid = str(b.get("gid") or "")
        if gid.isdigit():
            def put(m):
                m[str(app) if app else "t:" + title.lower()] = {"gid": gid, "ts": time.time()}
                return m
            storage.update("gid_map", put)
        self.engine.check_now("wishlist")
        self.engine.touch()
        return {"ok": True}

    def post_wishlist_update(self, q, b):
        title = str(b.get("title") or "")
        ch = {}
        if b.get("mode") in alerts.MODES:
            ch["mode"] = b["mode"]
        if b.get("repeat") in alerts.REPEATS:
            ch["repeat"] = b["repeat"]
        if "max_price" in b:
            ch["max_price"] = _num(b["max_price"], 0, 5000)
        alerts.update_item(title, **ch)
        self.engine.check_now("wishlist")
        self.engine.touch()
        return {"ok": True}

    def post_wishlist_remove(self, q, b):
        alerts.remove_item(str(b.get("title") or ""))
        self.engine.touch()
        return {"ok": True}

    def post_settings(self, q, b):
        for k in ("notify", "notify_free", "notify_deals", "notify_wishlist"):
            if k in b:
                storage.put(k, bool(b[k]))
        if b.get("mode") in MODES:
            storage.put("mode", b["mode"])
        if "min_discount_alert" in b:
            storage.put("min_discount_alert", int(_num(b["min_discount_alert"], 30, 95, 85)))
        if isinstance(b.get("quiet"), dict):
            qt = b["quiet"]
            if not (HHMM.match(str(qt.get("from"))) and HHMM.match(str(qt.get("to")))):
                raise ApiFail("وقت مش صحيح (HH:MM)")
            storage.put("quiet", {"on": bool(qt.get("on")), "from": qt["from"], "to": qt["to"]})
        if isinstance(b.get("sources"), dict):
            cur = storage.get("sources")
            cur.update({k: bool(v) for k, v in b["sources"].items() if k in SRC_KEYS})
            storage.put("sources", cur)
        if "discord" in b:
            url = str(b["discord"] or "").strip()
            if url and not notify.valid_discord(url):
                raise ApiFail("رابط Discord Webhook مش صحيح")
            storage.put("discord", url)
        if isinstance(b.get("telegram"), dict):
            old = storage.get("telegram")
            token = str(b["telegram"].get("token") if b["telegram"].get("token") is not None else old.get("token", "")).strip()
            chat = str(b["telegram"].get("chat", old.get("chat", ""))).strip()
            if token or chat:
                if not notify.valid_telegram(token, chat):
                    raise ApiFail("توكن تيليجرام أو Chat ID مش صح")
            storage.put("telegram", {"token": token, "chat": chat})
        if "steam_key" in b:
            storage.put("steam", {**storage.get("steam"), "key": str(b["steam_key"] or "").strip()[:64]})
        self.engine.touch()
        self.engine.wake.set()
        return {"ok": True}

    def post_autostart(self, q, b):
        ok = autostart.set_enabled(bool(b.get("on")))
        storage.put("asked_autostart", True)
        self.engine.touch()
        return {"ok": ok}

    def post_notify_test(self, q, b):
        ok, msg = self.notifier.test(b.get("channel", "toast"))
        return {"ok": ok, "message": msg}

    def post_steam_login(self, q, b):
        state = steam.new_state()
        ret = f"http://127.0.0.1:{self.port}/steam/callback?state={state}"
        webbrowser.open(steam.login_url(ret, f"http://127.0.0.1:{self.port}"))
        return {"ok": True}

    def post_steam_manual(self, q, b):
        try:
            info, res = self.engine.link_account(str(b.get("text") or ""), str(b.get("key") or ""))
        except (steam.SteamError, api.ApiError) as e:
            raise ApiFail(str(e))
        return {"ok": True, "name": info["name"], "result": res}

    def post_steam_sync(self, q, b):
        if not storage.get("steam")["id"]:
            raise ApiFail("اربط حساب Steam الأول")
        return {"ok": self.engine.sync_steam_bg()}

    def post_steam_unlink(self, q, b):
        self.engine.unlink_account()
        return {"ok": True}

    def post_history_clear(self, q, b):
        db.clear()
        self.engine.touch()
        return {"ok": True}

    # ---------------- Steam callback ----------------
    def steam_callback(self, query):
        params = dict(parse_qsl(query, keep_blank_values=True))
        state = params.get("state", "")
        try:
            sid = steam.verify_login(params, state, f"http://127.0.0.1:{self.port}/steam/callback?state={state}")
            info = self.engine.link_by_id(sid)
            self.engine.sync_steam_bg()
            return True, f"اتربط حساب Steam: {info['name'] or sid}" + ("" if info["public"] else " (البروفايل Private، خلّيه Public عشان الأمنيات تتقرا)")
        except (steam.SteamError, api.ApiError) as e:
            return False, str(e)


PAGE = """<!doctype html><meta charset=utf-8><title>صايد الفرصة</title><body style="font-family:Segoe UI,Tahoma,sans-serif;background:#0b0d12;color:#e8ecf4;display:grid;place-items:center;height:100vh;margin:0" dir=rtl>
<div style="text-align:center;max-width:520px;padding:32px;border-radius:20px;background:#151922;border:1px solid #232a38"><div style="font-size:54px">{icon}</div><h2>{msg}</h2><p style="color:#8b93a7">ارجع لبرنامج صايد الفرصة، تقدر تقفل الصفحة دي.</p></div>"""


class _Handler(BaseHTTPRequestHandler):
    app = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- مساعدات ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}")

    def _authed(self, q):
        t = self.headers.get("X-Token") or q.get("t") or ""
        return secrets.compare_digest(t, self.app.token)

    def _route(self, method):
        u = urlparse(self.path)
        q = dict(parse_qsl(u.query))
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        path = u.path
        try:
            if method == "GET" and path == "/":
                if not self._authed(q):
                    return self._send(403, "افتح البرنامج من الأيقونة اللي جنب الساعة".encode("utf-8"), "text/plain; charset=utf-8")
                return self._send(200, self.app.page(), "text/html; charset=utf-8")
            if method == "GET" and path == "/steam/callback":
                ok, msg = self.app.steam_callback(u.query)
                return self._send(200 if ok else 400, PAGE.format(icon="✅" if ok else "⚠️", msg=_esc(msg)).encode("utf-8"),
                                  "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            if not self._authed(q):
                return self._send(401, {"error": "unauthorized"})
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 65536:
                    return self._send(413, {"error": "too large"})
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(body, dict):
                    raise ApiFail("body لازم يبقى object")
            name = path[5:]
            if method == "GET" and name.startswith("share/"):
                return self._share(unquote(name[6:]).removesuffix(".png"))
            if method == "GET" and name == "export.csv":
                return self._export()
            fn = getattr(self.app, f"{method.lower()}_{name.replace('/', '_').replace('.', '_')}", None)
            if not fn:
                return self._send(404, {"error": "not found"})
            return self._send(200, fn(q, body))
        except ApiFail as e:
            return self._send(e.code, {"error": str(e)})
        except json.JSONDecodeError:
            return self._send(400, {"error": "JSON مش صحيح"})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as e:  # noqa
            log.exception("api error %s", path)
            return self._send(500, {"error": "خطأ داخلي: " + str(e)[:100]})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    # ---- ملفات ----
    def _share(self, key):
        g = next((g for g in self.app.engine.merged_free() if g["key"] == key), None)
        if not g:
            raise ApiFail("اللعبة مش موجودة", 404)
        out = storage.DIR / "share"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "card.png"
        share.make_card({"title": g["title"], "price": g["normal"], "url": g["url"], "image": g.get("image"), "store": g["store"]}, str(p))
        self._send(200, p.read_bytes(), "image/png")

    def _export(self):
        p = storage.DIR / "export.csv"
        db.export_csv(str(p))
        self._send(200, p.read_bytes(), "text/csv; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="dealhunter-history.csv"'})


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
