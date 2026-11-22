"""فحص سريع إن كود الواجهة ما فيهوش أخطاء برمجية، بدون شاشة (بيستبدل customtkinter بنسخة وهمية)."""
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealhunter.ui import ui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class W:
    def __init__(self, *a, **kw):
        self.values = kw.get("values")
        self.v = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **kw: W(**kw) if name in ("add",) else None

    def set(self, v): self.v = v
    def get(self):
        if self.v is not None:
            return self.v
        return self.values[0] if self.values else ""
    def winfo_children(self): return []
    def winfo_exists(self): return True
    def after(self, *a, **kw): return None
    def grid_columnconfigure(self, *a, **kw): return None
    def pack(self, *a, **kw): return None
    def grid(self, *a, **kw): return None


ctk = types.ModuleType("customtkinter")
for n in ("CTk", "CTkFrame", "CTkLabel", "CTkButton", "CTkEntry", "CTkSwitch", "CTkOptionMenu", "CTkSegmentedButton",
          "CTkTabview", "CTkScrollableFrame", "CTkProgressBar", "CTkImage", "CTkFont"):
    setattr(ctk, n, type(n, (W,), {}))
ctk.set_appearance_mode = lambda *_: None
sys.modules["customtkinter"] = ctk
try:
    import tkinter.messagebox  # noqa
except Exception:
    tk = types.ModuleType("tkinter")
    tk.messagebox = types.SimpleNamespace(showinfo=print, showerror=print, showwarning=print, askyesno=lambda *a: True)
    tk.filedialog = types.SimpleNamespace(asksaveasfilename=lambda **k: "")
    sys.modules.update({"tkinter": tk, "tkinter.messagebox": tk.messagebox, "tkinter.filedialog": tk.filedialog})
    tk.__dict__.update(messagebox=tk.messagebox, filedialog=tk.filedialog)

from dealhunter import alerts, db, images, storage, worker  # noqa: E402

storage.DIR = Path(tempfile.mkdtemp())
images.load = lambda *a, **k: None
worker.Worker.loop = lambda self: None
ui.messagebox.showinfo = lambda *a, **k: print("INFO:", a[1][:60])

app = ui.App(minimized=False)
now = datetime.now(timezone.utc)
app.render_free([{"id": "e", "title": "Epic Game", "price": 29.99, "url": "u", "image": None, "start": now, "end": now + timedelta(days=3)}],
                [{"title": "Next", "start": now + timedelta(days=3), "end": now + timedelta(days=9)}])
app.steam_list = [{"id": "s", "game_id": "1", "title": "Steam Free", "normal": 19.99, "app": 5, "image": None, "url": "u"}]
app.render_steam()
app.deals = [{"id": "d", "game_id": "2", "title": "Deal", "sale": 5.0, "normal": 50.0, "savings": 90.0, "store": "Steam", "image": None, "url": "u", "app": 5}]
app.render_deals()
app._set_filter("AAA")
alerts.add_item("Cyberpunk", "75", "6h", 10)
app.wl_status = {"Cyberpunk": app.deals[0]}
app.render_wishlist()
db.log_event("Epic", "e", "Epic Game", "free", 0.0, 29.99, 100.0, "u")
db.log_event("Steam", "2", "Deal", "deal", 5.0, 50.0, 90.0, "u")
app.render_stats()
app.render_history()
app.refresh_steam_status()
app.w_entry = W()
app._on_tab()
app.on_status(True, "ok")
while not app.q.empty():
    done, res = app.q.get_nowait()
    done(*res)
print("UI smoke test OK")
