"""سجل كل الألعاب المجانية والخصومات (SQLite) + الإحصائيات."""
import contextlib
import csv
import sqlite3
import threading
import time

from . import storage

_lock = threading.RLock()
GAP = 3 * 86400  # لو اللعبة اختفت أكتر من 3 أيام ورجعت = عرض جديد
SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, game_key TEXT, title TEXT, kind TEXT,
  sale REAL, normal REAL, savings REAL, url TEXT, first_seen REAL, last_seen REAL, times_seen INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_game ON events(store, game_key, kind);
"""


@contextlib.contextmanager
def _db():
    storage.DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        c = sqlite3.connect(str(storage.DIR / "history.db"), timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.executescript(SCHEMA)
            yield c
            c.commit()
        finally:
            c.close()


def log_event(store, game_key, title, kind, sale, normal, savings, url, now=None):
    """بيسجّل العرض. بيرجع True لو ده عرض جديد (مش نفس العرض اللي لسه شغال)."""
    now = now or time.time()
    gk = str(game_key)
    with _db() as c:
        row = c.execute("SELECT id,last_seen FROM events WHERE store=? AND game_key=? AND kind=? AND ABS(sale-?)<0.005 "
                        "ORDER BY last_seen DESC LIMIT 1", (store, gk, kind, sale)).fetchone()
        if row and now - row["last_seen"] <= GAP:
            c.execute("UPDATE events SET last_seen=?, times_seen=times_seen+1, normal=?, savings=? WHERE id=?",
                      (now, normal, savings, row["id"]))
            return False
        c.execute("INSERT INTO events(store,game_key,title,kind,sale,normal,savings,url,first_seen,last_seen) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (store, gk, title, kind, sale, normal, savings, url, now, now))
        return True


def recent(limit=200, kind=None, store=None, search=None):
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if store:
        sql += " AND store=?"
        args.append(store)
    if search:
        sql += " AND title LIKE ?"
        args.append(f"%{search}%")
    sql += " ORDER BY first_seen DESC LIMIT ?"
    args.append(limit)
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def top_free(limit=10):
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT title, store, COUNT(*) n, MAX(normal) normal FROM events WHERE kind='free' "
            "GROUP BY store, game_key ORDER BY n DESC, normal DESC LIMIT ?", (limit,))]


def top_deals_count(limit=10):
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT title, store, COUNT(*) n, MAX(savings) best FROM events WHERE kind='deal' AND savings>=75 "
            "GROUP BY store, game_key ORDER BY n DESC, best DESC LIMIT ?", (limit,))]


def top_deals_best(limit=10):
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT title, store, MAX(savings) best, MIN(sale) low, MAX(normal) normal FROM events WHERE kind='deal' "
            "GROUP BY store, game_key ORDER BY best DESC, normal DESC LIMIT ?", (limit,))]


def summary():
    with _db() as c:
        r = c.execute("SELECT SUM(kind='free') free_n, COALESCE(SUM(CASE WHEN kind='free' THEN normal END),0) free_value, "
                      "SUM(kind='deal') deal_n, COUNT(DISTINCT store||game_key) games, MIN(first_seen) since FROM events").fetchone()
        by_store = {x["store"]: x["n"] for x in c.execute("SELECT store, COUNT(*) n FROM events WHERE kind='free' GROUP BY store")}
    return {"free_n": r["free_n"] or 0, "free_value": r["free_value"] or 0.0, "deal_n": r["deal_n"] or 0,
            "games": r["games"] or 0, "since": r["since"], "free_by_store": by_store}


def monthly(months=6):
    with _db() as c:
        rows = c.execute("SELECT strftime('%Y-%m', first_seen, 'unixepoch', 'localtime') m, "
                         "SUM(kind='free') f, SUM(kind='deal') d FROM events GROUP BY m ORDER BY m DESC LIMIT ?", (months,)).fetchall()
    return [(r["m"], r["f"] or 0, r["d"] or 0) for r in reversed(rows)]


def export_csv(path):
    rows = recent(limit=1_000_000)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["التاريخ", "النوع", "اللعبة", "المتجر", "السعر الجديد", "السعر الأصلي", "الخصم %", "الرابط"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(r["first_seen"])), "ببلاش" if r["kind"] == "free" else "خصم",
                        r["title"], r["store"], r["sale"], r["normal"], round(r["savings"]), r["url"]])
    return len(rows)


def clear():
    with _db() as c:
        c.execute("DELETE FROM events")
