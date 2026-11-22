"""كارت مشاركة اللعبة المجانية (PNG) + نص الرسالة لديسكورد."""
import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from . import DISCORD_URL, images

RAQM = features.check("raqm") and features.check("fribidi")
if not RAQM:
    try:
        import arabic_reshaper
        try:
            from bidi import get_display
        except ImportError:
            from bidi.algorithm import get_display
    except ImportError:
        arabic_reshaper = None


def _font(size):
    for n in ("segoeuib.ttf", "tahoma.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text(d, xy, t, size, fill, anchor="ra", max_w=None, **kw):
    while max_w and size > 24 and _font(size).getlength(t) > max_w:
        size -= 4  # بيصغّر الخط لحد ما الاسم يكفي في مكانه
    if RAQM:
        d.text(xy, t, font=_font(size), fill=fill, anchor=anchor, direction="rtl", language="ar", **kw)
    else:
        if arabic_reshaper is not None:
            t = get_display(arabic_reshaper.reshape(t))
        d.text(xy, t, font=_font(size), fill=fill, anchor=anchor, **kw)


def message(item):
    price = f"${item['price']:.2f}" if item["price"] else "غالية"
    return f"🎁 لعبة ببلاش على {item.get('store', 'Epic')}: {item['title']} (كانت {price})\nخدها قبل ما العرض يخلص: {item['url']}"


def make_card(item, path):
    W, H = 1200, 675
    try:
        art = images.cover(Image.open(io.BytesIO(images.raw(item["image"]))), (W, H))
    except Exception:
        art = Image.new("RGB", (W, H), "#181b22")
    bg = art.filter(ImageFilter.GaussianBlur(14)).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), (8, 10, 20, 170)))
    d = ImageDraw.Draw(bg)
    pic = images.cover(art, (520, 292))
    bg.paste(pic, (60, 90))
    d.rounded_rectangle((58, 88, 582, 386), 10, outline="#22e678", width=4)
    R = W - 60
    _text(d, (R, 60), "صايد الفرصة  •  DealHunter", 34, "#8b93a7")
    _text(d, (R, 120), item["title"], 66, "#ffffff", max_w=520)
    _text(d, (R, 230), "ببلاش دلوقتي!", 74, "#22e678")
    if item["price"]:
        _text(d, (R, 330), f"كانت ${item['price']:.2f}", 44, "#ff6b7a")
    _text(d, (R, 460), "الحق خدها قبل ما العرض يخلص", 38, "#e5e7eb")
    d.text((60, H - 70), DISCORD_URL.replace("https://", ""), font=_font(28), fill="#5865f2", anchor="la")
    bg.convert("RGB").save(path)
    return path
