"""فحص حقيقي للواجهة: بيشغّل السيرفر ببيانات تجريبية، ويفتحه في Chromium بدون شاشة، ويصوّر كل الصفحات
ويفشل لو فيه أي خطأ JavaScript. التشغيل:  python tools/ui_check.py [مجلد_الصور]"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dealhunter import alerts, db, storage  # noqa: E402

storage.DIR = Path(tempfile.mkdtemp())
from dealhunter.engine import Engine  # noqa: E402
from dealhunter.notify import Notifier  # noqa: E402
from dealhunter.server import Server  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
OUT.mkdir(parents=True, exist_ok=True)
now = time.time()
IMG = "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/header.jpg"

eng = Engine(Notifier())
eng.data["epic_cur"] = [
    {"id": "Epic:1", "dbkey": "1", "title": "Hades II", "store": "Epic", "url": "https://store.epicgames.com/x", "image": IMG.format(1145350),
     "normal": 29.99, "end_ts": now + 3 * 86400 + 5000, "start_ts": now - 86400, "note": "", "app": None}]
eng.data["epic_up"] = [{"title": "Next Week Game", "image": None, "normal": 19.99, "start_ts": now + 3 * 86400, "end_ts": now + 9 * 86400}]
eng.data["steam_free"] = [{"id": "Steam:9", "dbkey": "9", "title": "Steam Free Game", "store": "Steam", "url": "https://store.steampowered.com/app/9",
                           "image": None, "normal": 19.99, "end_ts": None, "start_ts": None, "note": "", "app": 9}]
eng.data["giveaways"] = [{"id": "GP:5", "dbkey": "GP:5", "title": "Itch Indie Gem", "store": "itch.io", "url": "https://x", "image": None,
                          "normal": 9.99, "end_ts": now + 40000, "start_ts": None, "note": "Login and claim", "app": None}]
stores = ["Steam", "GOG", "Epic", "Humble", "Fanatical", "GMG"]
eng.data["deals"] = [{"id": f"d{i}", "game_id": str(100 + i), "title": f"Great Game Number {i} <b>x</b>", "sale": 4.99 + i, "normal": 59.99,
                      "savings": 90.0 - i, "store": stores[i % 6], "image": None, "url": "https://x", "metacritic": 70 + i % 20, "app": 1000 + i,
                      "ends": now + 86400 if i % 3 == 0 else None} for i in range(40)]
eng.data["lows"] = {"100": 5.0}
db.log_event("Epic", "1", "Hades II", "free", 0.0, 29.99, 100.0, "u", now - 40 * 86400)
db.log_event("Steam", "100", "Great Game", "deal", 4.99, 59.99, 91.0, "u", now)
alerts.add_item("Cyberpunk 2077", "75", "6h", 10)
alerts.add_item("Elden Ring", "50", "change", None, 1245620)
eng.data["wl_status"] = {"Cyberpunk 2077": eng.data["deals"][0]}
storage.put("saved_total", 123.45)
for n in eng.src.values():
    n.update(ok=True, last=now - 300, next=now + 1500)
eng.src["giveaways"].update(ok=False, err="HTTP 503")

srv = Server(eng, Notifier(), port=0).start()
errors = []
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1320, "height": 880})
    pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
    pg.goto(srv.url)
    pg.wait_for_selector(".hero", timeout=8000)
    for name in ("home", "free", "deals", "wish", "stats", "hist", "set"):
        pg.evaluate(f"go('{name}')")
        pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / f"{name}.png"))
    # تفاعلات
    pg.evaluate("go('deals')")
    pg.fill("#q", "number 3")
    pg.wait_for_timeout(300)
    n = pg.locator("#view .card").count()
    assert n >= 1, "البحث مرجّعش نتايج"
    assert "<b>x</b>" in pg.locator("#view .card h3").first.inner_text(), "العنوان لازم يتعرض كنص مش HTML"
    pg.fill("#q", "")
    pg.evaluate("go('wish')")
    pg.fill("#view input[type=text]", "Test Game")
    pg.click("text=إضافة")
    pg.wait_for_timeout(600)
    assert any(i["title"] == "Test Game" for i in alerts.items()), "إضافة للأمنيات مشتغلتش"
    # موبايل/نافذة ضيقة
    pg.set_viewport_size({"width": 700, "height": 800})
    pg.evaluate("go('home')")
    pg.wait_for_timeout(400)
    pg.screenshot(path=str(OUT / "narrow.png"))
    # الوصول من غير توكن لازم يترفض
    ctx = b.new_context()
    r = ctx.new_page().goto(f"http://127.0.0.1:{srv.port}/")
    assert r.status == 403, f"من غير توكن لازم 403 (جه {r.status})"
    r2 = ctx.request.get(f"http://127.0.0.1:{srv.port}/api/state")
    assert r2.status == 401, f"API من غير توكن لازم 401 (جه {r2.status})"
    b.close()
srv.stop()
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("UI check OK — الصور في", OUT)
