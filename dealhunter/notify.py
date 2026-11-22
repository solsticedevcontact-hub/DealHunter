"""قنوات الإشعارات: إشعار ويندوز + Discord Webhook + Telegram Bot (كلها اختيارية وبتتجرّب بزرار)."""
import json
import re
import threading

from . import api, storage
from .logs import log

DISCORD_RE = re.compile(r"^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")
TG_TOKEN_RE = re.compile(r"^\d{6,12}:[\w-]{30,}$")
TG_CHAT_RE = re.compile(r"^(-?\d+|@\w{4,})$")


def valid_discord(url):
    return bool(DISCORD_RE.match(url or ""))


def valid_telegram(token, chat):
    return bool(TG_TOKEN_RE.match(token or "") and TG_CHAT_RE.match(str(chat or "")))


def _post_json(url, payload):
    api.fetch_ex(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, retries=0)


def send_discord(webhook, title, msg, url=None, image=None):
    emb = {"title": title[:250], "description": msg[:1800], "color": 0x22E678}
    if url:
        emb["url"] = url
    if image:
        emb["image"] = {"url": image}
    _post_json(webhook, {"username": "صايد الفرصة 🎯", "embeds": [emb]})


def send_telegram(token, chat, title, msg, url=None):
    text = f"{title}\n{msg}" + (f"\n{url}" if url else "")
    _post_json(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": text[:3900]})


class Notifier:
    """toast = دالة بتعرض إشعار ويندوز (من الـ tray)."""

    def __init__(self, toast=None):
        self.toast = toast

    def channels(self):
        tg = storage.get("telegram")
        return {"toast": bool(storage.get("notify")), "discord": valid_discord(storage.get("discord")),
                "telegram": valid_telegram(tg.get("token"), tg.get("chat"))}

    def send(self, title, msg, url=None, image=None, only=None):
        """بيبعت في خيط لوحده عشان مفيش حاجة تعطّل الفحص. بيرجع الخيط (للاختبار)."""
        ch = self.channels()
        if only:
            ch = {k: v and k == only for k, v in ch.items()}
            if only == "toast":
                ch["toast"] = True

        def run():
            if ch["toast"] and self.toast:
                try:
                    self.toast(title, msg)
                except Exception:  # noqa
                    log.exception("toast failed")
            if ch["discord"]:
                try:
                    send_discord(storage.get("discord"), title, msg, url, image)
                except Exception as e:  # noqa
                    log.warning("discord: %s", e)
            if ch["telegram"]:
                try:
                    tg = storage.get("telegram")
                    send_telegram(tg["token"], tg["chat"], title, msg, url)
                except Exception as e:  # noqa
                    log.warning("telegram: %s", e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test(self, channel):
        """بيجرّب قناة واحدة ويرجّع (نجح؟, رسالة). متزامن عشان الواجهة تعرض النتيجة."""
        title, msg = "🎯 صايد الفرصة", "الإشعارات شغالة تمام ✅"
        try:
            if channel == "discord":
                if not valid_discord(storage.get("discord")):
                    return False, "رابط الـ Webhook مش صحيح"
                send_discord(storage.get("discord"), title, msg)
            elif channel == "telegram":
                tg = storage.get("telegram")
                if not valid_telegram(tg.get("token"), tg.get("chat")):
                    return False, "التوكن أو الـ Chat ID مش صح"
                send_telegram(tg["token"], tg["chat"], title, msg)
            else:
                if not self.toast:
                    return False, "إشعارات ويندوز مش متاحة (الـ tray مش شغال)"
                self.toast(title, msg)
        except Exception as e:  # noqa
            return False, str(e)[:140]
        return True, "اتبعت ✅"
