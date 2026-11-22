import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dealhunter import api, specs, storage  # noqa: E402
from dealhunter.engine import Engine  # noqa: E402

HTML_MIN = ('<strong>Minimum:</strong><br><ul class="bb_ul"><li><strong>OS:</strong> Windows 10 64-bit<br></li>'
            '<li><strong>Processor:</strong> Intel Core i5-3570K or AMD FX-8310<br></li><li><strong>Memory:</strong> 8 GB RAM<br></li>'
            '<li><strong>Graphics:</strong> NVIDIA GeForce GTX 780 or AMD Radeon RX 470<br></li>'
            '<li><strong>Storage:</strong> 70 GB available space<br></li></ul>')
HTML_REC = ('<strong>Recommended:</strong><br><ul class="bb_ul"><li><strong>Processor:</strong> Intel Core i7-4790 or Ryzen 3 3200G<br></li>'
            '<li><strong>Memory:</strong> 12 GB RAM<br></li><li><strong>Graphics:</strong> NVIDIA GeForce GTX 1060 6GB or Radeon RX 580<br></li>'
            '<li><strong>Storage:</strong> 70 GB SSD<br></li></ul>')


def pc(**kw):
    base = {"cpu": "Intel(R) Core(TM) i5-8400 CPU @ 2.80GHz", "gpu": "NVIDIA GeForce GTX 1060 6GB", "vram_gb": 6.0, "ram_gb": 16.0, "disk_gb": 200.0}
    base.update(kw)
    return base


class TestScoring(unittest.TestCase):
    def test_gpu_scores(self):
        s = specs.gpu_score
        self.assertEqual(s("NVIDIA GeForce GTX 1060 6GB"), 100)
        self.assertGreater(s("NVIDIA GeForce RTX 3060 Ti"), s("NVIDIA GeForce RTX 3060"))
        self.assertGreater(s("GeForce GTX 1660 Super"), s("GeForce GTX 1660"))
        self.assertEqual(s("AMD Radeon RX 580 Series"), 110)
        self.assertGreater(s("AMD Radeon RX 7900 XTX"), s("AMD Radeon RX 7900 XT"))
        self.assertEqual(s("Intel(R) UHD Graphics 630"), 25)
        self.assertLess(s("NVIDIA GeForce RTX 3060 Laptop GPU"), s("NVIDIA GeForce RTX 3060"))
        self.assertIsNone(s("Some Unknown Adapter"))
        self.assertNotEqual(s("GeForce GTX 580"), s("Radeon RX 580"))   # نفس الرقم، كارتين مختلفين
        self.assertIsNone(s("DirectX 11 compatible"))

    def test_cpu_scores(self):
        s = specs.cpu_score
        self.assertGreater(s("Intel Core i7-12700K"), s("Intel Core i5-8400"))
        self.assertGreater(s("Intel Core i5-8400"), s("Intel Core i5-3570K"))
        self.assertGreater(s("AMD Ryzen 7 5800X"), s("AMD Ryzen 5 1600"))
        self.assertEqual(s("AMD FX-8350"), 70)
        self.assertIsNone(s("Quad core 3GHz"))

    def test_pick_gpu_prefers_discrete(self):
        self.assertEqual(specs.pick_gpu([("Intel(R) UHD Graphics 630", 0.1), ("NVIDIA GeForce GTX 1650", 4.0)])[0], "NVIDIA GeForce GTX 1650")
        self.assertEqual(specs.pick_gpu([]), ("", 0.0))


class TestRequirements(unittest.TestCase):
    def test_parse_fields(self):
        f = specs.parse_fields(HTML_MIN)
        self.assertEqual(f["ram"], "8 GB RAM")
        self.assertIn("GTX 780", f["gpu"])
        self.assertEqual(f["disk"], "70 GB available space")
        self.assertIsNone(specs.parse_fields(""))
        self.assertIsNone(specs.parse_fields("<strong>Minimum:</strong><br>Nothing useful"))

    def test_verdicts(self):
        req = {"min": specs.parse_fields(HTML_MIN), "rec": specs.parse_fields(HTML_REC)}
        self.assertEqual(specs.evaluate(pc(), req)["level"], "great")
        weak = specs.evaluate(pc(gpu="NVIDIA GeForce GTX 970", vram_gb=4.0, ram_gb=8.0), req)
        self.assertEqual(weak["level"], "ok")                       # بيحقق الحد الأدنى بس
        low = specs.evaluate(pc(ram_gb=4.0), req)
        self.assertEqual(low["level"], "no")
        self.assertIn("الرام 4GB", low["reasons"][0])
        self.assertEqual(specs.evaluate(pc(gpu="Intel(R) UHD Graphics 620"), req)["level"], "no")
        self.assertEqual(specs.evaluate(pc(disk_gb=20.0), req)["level"], "no")
        self.assertEqual(specs.evaluate(pc(), {"min": None, "rec": None})["level"], "unknown")
        self.assertEqual(specs.evaluate({}, req)["level"], "unknown")   # مفيش مواصفات جهاز

    def test_override_wins(self):
        eff = specs.effective({"gpu": "Intel UHD 630", "ram_gb": 8.0}, {"gpu": "RTX 3060", "ram_gb": 32})
        self.assertEqual((eff["gpu"], eff["ram_gb"]), ("RTX 3060", 32.0))

    def test_detect_returns_sane_values(self):
        d = specs.detect()
        self.assertGreater(d["ram_gb"], 0)
        self.assertGreater(d["disk_gb"], 0)


class FakeNotifier:
    toast = None

    def send(self, *a, **k):
        pass

    def channels(self):
        return {"toast": True, "discord": False, "telegram": False}


class TestScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        storage.DIR = Path(self.tmp.name)
        specs.cache.reset_for_tests()
        self.orig = api.get_json

    def tearDown(self):
        api.get_json = self.orig
        specs.cache.reset_for_tests()
        self.tmp.cleanup()

    def test_fetch_requirements(self):
        api.get_json = lambda url, timeout=12: {"10": {"success": True, "data": {"pc_requirements": {"minimum": HTML_MIN, "recommended": HTML_REC}}}}
        r = specs.fetch_requirements(10)
        self.assertTrue(r["min"] and r["rec"])
        api.get_json = lambda url, timeout=12: {"11": {"success": True, "data": {"pc_requirements": []}}}   # Steam بترجّع [] لو مفيش
        self.assertEqual(specs.fetch_requirements(11), {"min": None, "rec": None})
        api.get_json = lambda url, timeout=12: {"12": {"success": False}}
        self.assertEqual(specs.fetch_requirements(12), {"min": None, "rec": None})

    def test_engine_scan_caches_and_reports(self):
        calls = []

        def fake(url, timeout=12):
            app = url.split("appids=")[1].split("&")[0]
            calls.append(app)
            return {app: {"success": True, "data": {"pc_requirements": {"minimum": HTML_MIN, "recommended": HTML_REC}}}}

        api.get_json = fake
        storage.put("pc_detected", pc())
        eng = Engine(FakeNotifier())
        eng.stop.wait = lambda t=None: False       # من غير انتظار في الاختبار
        self.assertTrue(eng.scan_requirements([10, 20, 10]))
        for _ in range(100):
            if not eng.scan["running"]:
                break
            time.sleep(0.05)
        self.assertEqual(sorted(calls), ["10", "20"])             # من غير تكرار
        self.assertEqual(eng.state()["canrun"]["10"]["level"], "great")
        calls.clear()
        self.assertTrue(eng.scan_requirements([10, 20]))           # اتخزنوا: مفيش طلبات تانية
        self.assertEqual(calls, [])
        self.assertTrue(Path(storage.DIR / "reqs.json").exists())

    def test_scan_stops_after_repeated_errors(self):
        def boom(url, timeout=12):
            raise api.ApiError("HTTP 429")
        api.get_json = boom
        eng = Engine(FakeNotifier())
        eng.stop.wait = lambda t=None: False
        eng.scan_requirements(list(range(1, 11)))
        for _ in range(100):
            if not eng.scan["running"]:
                break
            time.sleep(0.05)
        self.assertLessEqual(eng.scan["done"], 3)
        self.assertIn("429", eng.scan["error"])


if __name__ == "__main__":
    unittest.main()
