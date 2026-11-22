"""تحميل صور الألعاب (لكارت المشاركة) مع كاش على الهارد."""
import hashlib

from PIL import Image

from . import api
from .storage import DIR

CACHE = DIR / "img"


def raw(url):
    path = CACHE / (hashlib.md5(url.encode()).hexdigest() + ".bin")
    try:
        return path.read_bytes()
    except OSError:
        pass
    data = api.fetch_bytes(url)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass
    return data


def cover(img, size):
    """بيقصّ الصورة لتملا المقاس بالظبط من غير تشويه."""
    w, h = size
    img = img.convert("RGB")
    k = max(w / img.width, h / img.height)
    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))
