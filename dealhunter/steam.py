"""ربط حساب Steam (قراءة بس): الويش ليست + المكتبة. مفيش باسورد ولا تسجيل دخول."""
import re
import secrets
import time
import urllib.parse

from . import api


class SteamError(Exception):
    pass


def parse_input(text):
    t = (text or "").strip()
    if re.fullmatch(r"\d{17}", t):
        return "id", t
    m = re.search(r"steamcommunity\.com/profiles/(\d{17})", t)
    if m:
        return "id", m.group(1)
    m = re.search(r"steamcommunity\.com/id/([^/?#\s]+)", t)
    if m:
        return "vanity", m.group(1)
    if re.fullmatch(r"[\w\-]{2,32}", t):
        return "vanity", t
    raise SteamError("اكتب رابط البروفايل أو الـ SteamID64 (17 رقم)")


def profile_info(kind, value):
    url = f"https://steamcommunity.com/{'profiles' if kind == 'id' else 'id'}/{value}/?xml=1"
    xml = api.fetch_bytes(url).decode("utf-8", "ignore")
    m = re.search(r"<steamID64>(\d{17})</steamID64>", xml)
    if not m:
        raise SteamError("مالقيتش البروفايل ده، اتأكد من الرابط")
    name = re.search(r"<steamID><!\[CDATA\[(.*?)\]\]></steamID>", xml, re.S)
    priv = re.search(r"<privacyState>(\w+)</privacyState>", xml)
    return {"id": m.group(1), "name": name.group(1) if name else "", "public": priv.group(1) == "public" if priv else True}


def wishlist(steamid):
    data = api.get_json(f"https://api.steampowered.com/IWishlistService/GetWishlist/v1/?steamid={steamid}")
    items = (data.get("response") or {}).get("items") or []
    return [int(i["appid"]) for i in items if "appid" in i]


def owned(steamid, key):
    data = api.get_json("https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
                        f"?key={key}&steamid={steamid}&include_played_free_games=0")
    games = (data.get("response") or {}).get("games")
    if games is None:
        raise SteamError("مقدرتش أجيب المكتبة: خلّي Game details على Public واتأكد من الـ API Key")
    return {int(g["appid"]) for g in games}


def owned_public(steamid):
    """مكتبة الألعاب من غير API Key (لو Game details على Public)."""
    xml = api.fetch_bytes(f"https://steamcommunity.com/profiles/{steamid}/games?tab=all&xml=1").decode("utf-8", "ignore")
    if "<error>" in xml or "<gamesList" not in xml:
        raise SteamError("مقدرتش أقرا المكتبة: خلّي Game details على Public في إعدادات الخصوصية")
    return {int(a) for a in re.findall(r"<appID>(\d+)</appID>", xml)}


def game_info(app):
    """(الاسم, gameID في CheapShark) — بيتنادى مرة واحدة لكل لعبة."""
    try:
        rows = api.get_json(f"{api.CS}/games?steamAppID={app}")
        if rows:
            return rows[0].get("external") or f"Steam #{app}", str(rows[0].get("gameID") or "") or None
    except api.ApiError:
        pass
    return f"Steam #{app}", None


def game_title(app):
    return game_info(app)[0]


# ---------- تسجيل الدخول الرسمي "Sign in through Steam" (OpenID) ----------
# الباسورد بيتكتب في صفحة Steam نفسها، والبرنامج مبيشوفوش أبداً: بياخد الـ SteamID العام بس.
OPENID = "https://steamcommunity.com/openid/login"
_CLAIMED = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")
_pending = {}  # state -> expiry


def new_state():
    now = time.time()
    for k in [k for k, v in _pending.items() if v < now]:
        _pending.pop(k, None)
    st = secrets.token_urlsafe(16)
    _pending[st] = now + 300
    return st


def login_url(return_to, realm):
    q = {"openid.ns": "http://specs.openid.net/auth/2.0", "openid.mode": "checkid_setup",
         "openid.return_to": return_to, "openid.realm": realm,
         "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
         "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select"}
    return OPENID + "?" + urllib.parse.urlencode(q)


def verify_login(params, state, expected_return_to):
    """params: query اللي Steam رجّعها. بيتأكد من التوقيع عند Steam نفسها. بيرجع SteamID64 أو بيرفع SteamError."""
    if _pending.pop(state, 0) < time.time():
        raise SteamError("الطلب انتهى أو مش مظبوط، جرّب تاني")
    m = _CLAIMED.match(params.get("openid.claimed_id", ""))
    if not m or params.get("openid.return_to") != expected_return_to or params.get("openid.mode") != "id_res":
        raise SteamError("رد Steam مش سليم")
    check = {k: v for k, v in params.items() if k.startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    body, _ = api.fetch_ex(OPENID, data=urllib.parse.urlencode(check).encode(), retries=0,
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
    if b"is_valid:true" not in body:
        raise SteamError("Steam مأكّدتش تسجيل الدخول")
    return m.group(1)
