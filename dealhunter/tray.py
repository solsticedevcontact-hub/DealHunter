"""أيقونة الـ System Tray + إشعارات ويندوز."""
from PIL import Image, ImageDraw

try:
    import pystray
except Exception:  # pragma: no cover
    pystray = None


def make_icon(size=256):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), s // 5, fill="#0f1115")
    c, w = s // 2, max(2, s // 16)
    for r, col in ((0.40, "#22e678"), (0.24, "#22e678")):
        d.ellipse((c - s * r, c - s * r, c + s * r, c + s * r), outline=col, width=w)
    d.ellipse((c - s * 0.08, c - s * 0.08, c + s * 0.08, c + s * 0.08), fill="#ff4050")
    for a, b in (((c, s * 0.08), (c, s * 0.30)), ((c, s * 0.70), (c, s * 0.92)),
                 ((s * 0.08, c), (s * 0.30, c)), ((s * 0.70, c), (s * 0.92, c))):
        d.line((a, b), fill="#22e678", width=w)
    return img


class Tray:
    available = pystray is not None

    def __init__(self, on_open, on_check, on_quit):
        self.icon = None
        self.cbs = (on_open, on_check, on_quit)

    def start(self):
        if not self.available:
            return False
        o, c, q = self.cbs
        menu = pystray.Menu(
            pystray.MenuItem("فتح صايد الفرصة", lambda *_: o(), default=True),
            pystray.MenuItem("افحص دلوقتي", lambda *_: c()),
            pystray.MenuItem("خروج", lambda *_: q()),
        )
        try:
            self.icon = pystray.Icon("DealHunter", make_icon(64), "صايد الفرصة 🎯", menu)
            self.icon.run_detached()
            return True
        except Exception:
            self.icon = None
            return False

    def notify(self, title, msg):
        try:
            if self.icon:
                self.icon.notify(msg, title)
        except Exception:
            pass

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
