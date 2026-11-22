import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dealhunter import api, storage

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def epic_el(id_, title, start, end, pct=0, slug="game-x", upcoming=False, price=5999):
    offer = {"startDate": start.isoformat(), "endDate": end.isoformat(), "discountSetting": {"discountPercentage": pct}}
    block = [{"promotionalOffers": [offer]}]
    return {"id": id_, "title": title, "productSlug": slug,
            "keyImages": [{"type": "Thumbnail", "url": "http://t/img.jpg"}, {"type": "OfferImageWide", "url": "http://w/img.jpg"}],
            "price": {"totalPrice": {"originalPrice": price, "currencyInfo": {"decimals": 2}}},
            "promotions": {"promotionalOffers": [] if upcoming else block, "upcomingPromotionalOffers": block if upcoming else []}}


class T(unittest.TestCase):
    def test_epic(self):
        els = [epic_el("a", "Free Now", NOW - timedelta(days=1), NOW + timedelta(days=6)),
               epic_el("b", "Next Week", NOW + timedelta(days=6), NOW + timedelta(days=13), upcoming=True),
               epic_el("c", "Just 50% off", NOW - timedelta(days=1), NOW + timedelta(days=2), pct=50),
               {"id": "d", "title": "No promo", "promotions": None}]
        cur, up = api.parse_epic({"data": {"Catalog": {"searchStore": {"elements": els}}}}, NOW)
        self.assertEqual([g["title"] for g in cur], ["Free Now"])
        self.assertEqual([g["title"] for g in up], ["Next Week"])
        self.assertEqual(cur[0]["price"], 59.99)
        self.assertEqual(cur[0]["image"], "http://w/img.jpg")
        self.assertTrue(cur[0]["url"].endswith("/p/game-x"))
        with self.assertRaises(api.ApiError):
            api.parse_epic({"oops": 1}, NOW)

    def test_countdown(self):
        self.assertEqual(api.remaining_text(NOW + timedelta(days=1, hours=3, minutes=5), NOW), "فاضل يوم و3 ساعات")
        self.assertEqual(api.remaining_text(NOW + timedelta(hours=2, minutes=10), NOW), "فاضل ساعتين و10 دقايق")
        self.assertEqual(api.remaining_text(NOW + timedelta(minutes=5), NOW), "فاضل 5 دقايق")
        self.assertEqual(api.remaining_text(NOW - timedelta(seconds=1), NOW), "العرض خلص")

    def deal(self, **kw):
        d = {"dealID": "X%2B1", "gameID": "10", "title": "Cyberpunk 2077", "salePrice": "14.99", "normalPrice": "59.99",
             "savings": "75.0", "storeID": "1", "steamAppID": "1091500", "thumb": "http://thumb"}
        d.update(kw)
        return d

    def test_deals_filters(self):
        ds = [api.parse_deal(self.deal()), api.parse_deal(self.deal(dealID="2", salePrice="3.99", normalPrice="9.99", savings="60")),
              api.parse_deal(self.deal(dealID="3", salePrice="7", normalPrice="12", savings="41", steamAppID="0"))]
        self.assertTrue(ds[0]["image"].endswith("/1091500/header.jpg"))
        self.assertEqual(ds[2]["image"], "http://thumb")
        self.assertEqual(len(api.apply_filter(ds, "الكل")), 3)
        self.assertEqual([d["id"] for d in api.apply_filter(ds, "AAA")], ["X%2B1"])
        self.assertEqual([d["id"] for d in api.apply_filter(ds, "أقل من 5$")], ["2"])
        self.assertEqual(len(api.apply_filter(ds, "ألعاب رخيصة")), 2)
        self.assertTrue(api.is_historic_low(ds[0], {"10": 14.6}))
        self.assertFalse(api.is_historic_low(ds[0], {"10": 9.0}))

    def test_steam_free(self):
        rows = [self.deal(dealID="f1", gameID="1", title="Free Keep", salePrice="0.00", normalPrice="19.99", savings="100.0"),
                self.deal(dealID="f2", gameID="2", title="F2P Always", salePrice="0.00", normalPrice="0.00", savings="0"),
                self.deal(dealID="f3", gameID="3", title="Paid", salePrice="4.99", normalPrice="19.99", savings="75")]
        out = api.parse_steam_free(rows)
        self.assertEqual([d["title"] for d in out], ["Free Keep"])
        self.assertTrue(out[0]["url"].startswith("https://store.steampowered.com/app/"))
        self.assertEqual(out[0]["store"], "Steam")

    def test_wishlist_match(self):
        rows = [self.deal(), self.deal(dealID="9", title="Cyberpunk 2077 Soundtrack", savings="30")]
        self.assertEqual(api.match_wishlist("cyberpunk", rows)["savings"], 75.0)
        self.assertIsNone(api.match_wishlist("GTA 6", rows))
        self.assertIsNone(api.match_wishlist("cyberpunk", [self.deal(savings="5")]))

    def test_storage_saved_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage.DIR = Path(tmp)
            self.assertTrue(storage.add_saved(59.99, "claimed", "a"))
            self.assertFalse(storage.add_saved(59.99, "claimed", "a"))
            self.assertTrue(storage.add_saved(10, "bought", "x"))
            self.assertEqual(storage.get("saved_total"), 69.99)


if __name__ == "__main__":
    unittest.main()
