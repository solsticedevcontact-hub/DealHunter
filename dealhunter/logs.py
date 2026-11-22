"""ملف سجل (log) للأخطاء عشان نعرف إيه اللي بوظ لو حاجة مشتغلتش."""
import logging
from logging.handlers import RotatingFileHandler

from . import storage

log = logging.getLogger("dealhunter")


def path():
    return storage.DIR / "log.txt"


def setup():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        storage.DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(path(), maxBytes=200_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    except OSError:
        log.addHandler(logging.NullHandler())
