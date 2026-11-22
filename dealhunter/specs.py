"""مواصفات الجهاز + "هل اللعبة هتشتغل عندي؟".

- الاكتشاف: رام / كارت شاشة / VRAM / معالج / مساحة فاضية (ويندوز بالـ registry، من غير برامج إضافية).
- المتطلبات: من Steam (appdetails) — بتتجاب عند الطلب بس وبتتخزّن 30 يوم.
- المقارنة: الرام والمساحة دقيقين. كارت الشاشة والمعالج **تقدير** بجدول درجات تقريبية (مش benchmark حقيقي)،
  فالنتيجة بتتقال "على الأغلب" والمستخدم يقدر يعدّل مواصفاته يدوي.
"""
import ctypes
import html as htmllib
import json
import os
import platform
import re
import shutil
import string
import subprocess
import sys
import threading
import time

from . import api, storage

REQ_URL = "https://store.steampowered.com/api/appdetails?appids={}&filters=pc_requirements&cc=us&l=english"
TTL_OK, TTL_NONE = 30 * 86400, 3 * 86400

# ---------------- جداول الدرجات (نسبي: GTX 1060 = 100) ----------------
NV = {"710": 6, "730": 8, "1030": 30, "650": 25, "650 ti": 35, "660": 45, "750": 38, "750 ti": 42, "760": 55, "770": 70, "780": 85,
      "950": 60, "960": 70, "970": 105, "980": 125, "980 ti": 160, "1050": 55, "1050 ti": 70, "1060": 100, "1070": 140, "1070 ti": 160,
      "1080": 180, "1080 ti": 230, "1630": 45, "1650": 90, "1650 super": 115, "1660": 120, "1660 super": 135, "1660 ti": 140,
      "2060": 160, "2060 super": 185, "2070": 195, "2070 super": 215, "2080": 235, "2080 super": 245, "2080 ti": 290,
      "3050": 130, "3060": 190, "3060 ti": 250, "3070": 290, "3070 ti": 320, "3080": 380, "3080 ti": 430, "3090": 450, "3090 ti": 480,
      "4060": 230, "4060 ti": 280, "4070": 340, "4070 super": 390, "4070 ti": 430, "4070 ti super": 470, "4080": 560, "4080 super": 580,
      "4090": 760, "5050": 220, "5060": 290, "5060 ti": 340, "5070": 420, "5070 ti": 520, "5080": 640, "5090": 900,
      "470": 55, "480": 65, "550 ti": 35, "560": 50, "560 ti": 58, "570": 60, "580": 70}
AMD_RX = {"460": 60, "470": 95, "480": 105, "550": 45, "560": 60, "570": 95, "580": 110, "590": 125, "5500 xt": 100, "5600 xt": 150,
          "5700": 190, "5700 xt": 215, "6400": 90, "6500 xt": 105, "6600": 190, "6600 xt": 215, "6650 xt": 225, "6700": 260,
          "6700 xt": 290, "6750 xt": 305, "6800": 380, "6800 xt": 430, "6900 xt": 470, "6950 xt": 500, "7600": 230, "7600 xt": 260,
          "7700 xt": 340, "7800 xt": 400, "7900 gre": 430, "7900 xt": 500, "7900 xtx": 560, "9060 xt": 300, "9070": 480, "9070 xt": 560}
AMD_OLD = {"r7 240": 8, "r7 250": 20, "r7 260x": 45, "r9 270": 70, "r9 270x": 75, "r9 280": 85, "r9 280x": 90, "r9 290": 130,
           "r9 290x": 140, "r9 380": 90, "r9 390": 140, "hd 7770": 35, "hd 7850": 50, "hd 7870": 65, "hd 7970": 90}
INTEL = {"hd 4000": 10, "hd 4600": 14, "hd 520": 16, "hd 530": 18, "uhd 620": 20, "uhd 630": 25, "uhd 730": 30, "uhd 750": 35,
         "uhd 770": 40, "iris xe": 55, "arc a380": 90, "arc a580": 150, "arc a750": 200, "arc a770": 230, "arc b570": 210, "arc b580": 250}
AMD_IGPU = {"780m": 110, "680m": 85, "760m": 70, "740m": 50, "vega 11": 45, "vega 10": 40, "vega 8": 30, "vega 7": 28}
INTEGRATED_WORDS = ("intel", "uhd", "iris", "vega", "graphics 4000", "radeon graphics", "radeon(tm) graphics")


def _clean(s):
    s = re.sub(r"[®™]|\(r\)|\(tm\)", "", (s or "").lower())
    return " ".join(s.split())


def is_integrated(name):
    n = _clean(name)
    return (("intel" in n and "arc" not in n) or "iris" in n or "vega" in n or n in ("amd radeon graphics", "radeon graphics")
            or bool(re.search(r"radeon\s*\d{3}m", n)))


def gpu_score(name):
    """درجة تقريبية لكارت الشاشة (None لو مش معروف)."""
    n = _clean(name)
    if not n:
        return None
    mult = 0.85 if re.search(r"laptop|max-q|mobile", n) else 1.0
    m = re.search(r"\b(?:gtx|rtx|gt)\s*(\d{3,4})(?:\s*(ti super|ti|super))?", n)
    if m:
        key = m.group(1) + (" " + m.group(2) if m.group(2) else "")
        v = NV.get(key) or NV.get(m.group(1))
        return v * mult if v else None
    m = re.search(r"\brx\s*(\d{3,4})(?:\s*(xtx|xt|gre))?", n)
    if m:
        key = m.group(1) + (" " + m.group(2) if m.group(2) else "")
        v = AMD_RX.get(key) or AMD_RX.get(m.group(1))
        return v * mult if v else None
    m = re.search(r"\b(r[579])\s*(\d{3}x?)", n)
    if m:
        v = AMD_OLD.get(f"{m.group(1)} {m.group(2)}")
        return v * mult if v else None
    m = re.search(r"\bradeon\s*(\d{3}m)\b", n)
    if m and AMD_IGPU.get(m.group(1)):
        return AMD_IGPU[m.group(1)] * mult
    m = re.search(r"\bvega\s*(\d+)", n)
    if m and AMD_IGPU.get("vega " + m.group(1)):
        return AMD_IGPU["vega " + m.group(1)]
    m = re.search(r"\barc\s*(?:graphics\s*)?([ab]\d{3})", n)
    if m:
        return INTEL.get("arc " + m.group(1))
    if re.search(r"iris\s*xe", n):
        return INTEL["iris xe"]
    m = re.search(r"\buhd\s*(?:graphics\s*)?(\d{3})", n)
    if m:
        return INTEL.get("uhd " + m.group(1))
    m = re.search(r"\bhd\s*(?:graphics\s*)?(\d{3,4})", n)
    if m:
        key = "hd " + m.group(1)
        return (AMD_OLD if "radeon" in n else INTEL).get(key)
    return None


INTEL_GEN = {2: 50, 3: 55, 4: 62, 5: 65, 6: 72, 7: 78, 8: 90, 9: 98, 10: 105, 11: 120, 12: 145, 13: 165, 14: 175}
RYZEN_SERIES = {1: 70, 2: 80, 3: 100, 4: 105, 5: 115, 6: 125, 7: 140, 8: 140, 9: 165}
CLASS_MULT = {"3": 0.85, "5": 1.0, "7": 1.1, "9": 1.2}


def cpu_score(name):
    """درجة تقريبية للمعالج (None لو مش معروف)."""
    n = _clean(name)
    m = re.search(r"\bi([3579])[- ](\d{4,5})", n)
    if m:
        num = int(m.group(2))
        gen = num // 1000 if num < 10000 else num // 1000
        base = INTEL_GEN.get(gen)
        return base * CLASS_MULT[m.group(1)] if base else None
    m = re.search(r"core\s*ultra\s*([3579])\s*(\d{3})", n)
    if m:
        return 150 * CLASS_MULT[m.group(1)]
    m = re.search(r"ryzen\s*([3579])\s*(\d{4})", n)
    if m:
        base = RYZEN_SERIES.get(int(m.group(2)) // 1000)
        return base * CLASS_MULT[m.group(1)] if base else None
    if re.search(r"\bfx[- ]?\d{4}", n):
        return 70
    if re.search(r"core\s*2|core2", n):
        return 30
    if re.search(r"pentium|celeron", n):
        return 28
    if re.search(r"athlon", n):
        return 35
    return None


# ---------------- اكتشاف الجهاز ----------------
def _win_ram_gb():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    ms = MS()
    ms.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return ms.ullTotalPhys / 1024 ** 3


def _win_cpu():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
        return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())


def _win_gpus():
    """[(الاسم, VRAM بالجيجا)] من الـ registry. بيتجاهل محركات العرض الافتراضية."""
    import winreg
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11cd-bfc1-08002be10318}"
    out = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                    vram = 0.0
                    for val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            raw = winreg.QueryValueEx(k, val)[0]
                            raw = int.from_bytes(raw, "little") if isinstance(raw, (bytes, bytearray)) else int(raw)
                            vram = raw / 1024 ** 3
                            break
                        except (OSError, ValueError):
                            continue
            except OSError:
                continue
            if re.search(r"basic display|remote|virtual|mirror|indirect", name, re.I):
                continue
            out.append((name, round(vram, 1)))
    return out


def _powershell(cmd):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=12, creationflags=flags)
    return r.stdout.strip()


def _win_gpus_ps():
    out = _powershell("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + '|' + $_.AdapterRAM }")
    res = []
    for line in out.splitlines():
        name, _, ram = line.partition("|")
        if name.strip() and not re.search(r"basic display|remote|virtual", name, re.I):
            res.append((name.strip(), round(int(ram) / 1024 ** 3, 1) if ram.strip().isdigit() else 0.0))
    return res


def _disk_free_gb():
    best = 0.0
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            try:
                if os.path.exists(root) and ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    best = max(best, shutil.disk_usage(root).free / 1024 ** 3)
            except OSError:
                continue
    else:
        best = shutil.disk_usage(os.path.expanduser("~")).free / 1024 ** 3
    return round(best, 1)


def pick_gpu(gpus):
    """أقوى كارت (الديسكريت بيكسب على المدمج)."""
    if not gpus:
        return "", 0.0
    return max(gpus, key=lambda g: (not is_integrated(g[0]), gpu_score(g[0]) or 0, g[1]))


def detect():
    """بيرجع dict بمواصفات الجهاز. أي حاجة مقدرش يعرفها بتفضل فاضية/0 (والمستخدم يكمّلها يدوي)."""
    spec = {"cpu": "", "cores": os.cpu_count() or 0, "gpu": "", "vram_gb": 0.0, "ram_gb": 0.0, "disk_gb": 0.0,
            "os": f"{platform.system()} {platform.release()}", "ts": time.time()}
    try:
        if sys.platform == "win32":
            spec["ram_gb"] = round(_win_ram_gb(), 1)
            try:
                spec["cpu"] = _win_cpu()
            except OSError:
                spec["cpu"] = platform.processor()
            try:
                gpus = _win_gpus()
            except OSError:
                gpus = []
            if not gpus:
                try:
                    gpus = _win_gpus_ps()
                except Exception:  # noqa
                    gpus = []
        else:
            gpus = []
            try:
                for line in open("/proc/meminfo", encoding="utf-8"):
                    if line.startswith("MemTotal"):
                        spec["ram_gb"] = round(int(line.split()[1]) / 1024 ** 2, 1)
                for line in open("/proc/cpuinfo", encoding="utf-8"):
                    if line.lower().startswith("model name"):
                        spec["cpu"] = line.split(":", 1)[1].strip()
                        break
            except OSError:
                pass
            if shutil.which("lspci"):
                out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=8).stdout
                gpus = [(l.split(": ", 1)[-1], 0.0) for l in out.splitlines() if re.search(r"VGA|3D controller", l)]
        spec["gpu"], spec["vram_gb"] = pick_gpu(gpus)
        spec["disk_gb"] = _disk_free_gb()
    except Exception:  # noqa
        pass
    return spec


def effective(detected, override):
    """المواصفات المستخدمة = المكتشفة + أي تعديل يدوي."""
    s = dict(detected or {})
    for k in ("cpu", "gpu"):
        if override.get(k):
            s[k] = str(override[k])[:120]
    for k in ("ram_gb", "vram_gb", "disk_gb"):
        if override.get(k) not in (None, ""):
            s[k] = float(override[k])
    return s


# ---------------- المتطلبات ----------------
FIELD_KEYS = (("cpu", r"processor|cpu"), ("ram", r"memory|ram"), ("gpu", r"graphics|video card|gpu"), ("disk", r"storage|hard drive|disk space|hdd"))


def parse_fields(raw):
    """HTML متطلبات Steam ← {cpu, ram, gpu, disk} نصوص. None لو مفيش حاجة مفهومة."""
    if not raw or not isinstance(raw, str):
        return None
    t = re.sub(r"<br\s*/?>|</li>|</p>", "\n", raw, flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", "", t))
    out = {}
    for line in t.splitlines():
        m = re.match(r"\s*([A-Za-z ]{2,20}?)\s*:\s*(.+)", line)
        if not m:
            continue
        label, val = m.group(1).lower(), m.group(2).strip()
        for key, pat in FIELD_KEYS:
            if key not in out and re.fullmatch(pat, label.strip()):
                out[key] = val[:300]
    return out or None


def _gb(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|tb)\b", text or "", re.I)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return v / 1024 if u == "mb" else v * 1024 if u == "tb" else v


def _vram(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*gb\s*(?:of\s*)?(?:dedicated\s*)?(?:vram|video)", text or "", re.I) or \
        re.search(r"(?:vram|video memory)[^\d]{0,12}(\d+(?:\.\d+)?)\s*gb", text or "", re.I)
    return float(m.group(1)) if m else None


def _best_alt(text, scorer):
    """بدائل زي "GTX 1060 or RX 580": يكفي تحقق أي بديل ← ناخد أقل درجة معروفة."""
    scores = [s for s in (scorer(p) for p in re.split(r"\bor\b|/|,|;|\||\band\b", text or "", flags=re.I)) if s]
    return min(scores) if scores else None


def compare(spec, f):
    """بيرجع (مشاكل[], عدد المقارنات اللي اتعملت فعلاً)."""
    issues, done = [], 0
    ram = _gb(f.get("ram"))
    if ram and spec.get("ram_gb"):
        done += 1
        if spec["ram_gb"] + 0.6 < ram:
            issues.append(f"الرام {spec['ram_gb']:.0f}GB والمطلوب {ram:g}GB")
    g_req, g_have = _best_alt(f.get("gpu"), gpu_score), gpu_score(spec.get("gpu"))
    if g_req and g_have:
        done += 1
        if g_have < 0.9 * g_req:
            issues.append("كارت الشاشة أضعف من المطلوب")
    vr = _vram(f.get("gpu"))
    if vr and spec.get("vram_gb") and not is_integrated(spec.get("gpu", "")) and spec["vram_gb"] + 0.3 < vr:
        done += 1
        issues.append(f"الـ VRAM {spec['vram_gb']:g}GB والمطلوب {vr:g}GB")
    c_req, c_have = _best_alt(f.get("cpu"), cpu_score), cpu_score(spec.get("cpu"))
    if c_req and c_have:
        done += 1
        if c_have < 0.85 * c_req:
            issues.append("المعالج أضعف من المطلوب")
    disk = _gb(f.get("disk"))
    if disk and spec.get("disk_gb"):
        done += 1
        if spec["disk_gb"] < disk:
            issues.append(f"المساحة الفاضية {spec['disk_gb']:.0f}GB والمطلوب {disk:g}GB")
    return issues, done


def evaluate(spec, req):
    """req = {"min": fields|None, "rec": fields|None} ← {"level": great|ok|no|unknown, "reasons": [...]}"""
    if not req or not req.get("min"):
        return {"level": "unknown", "reasons": ["مفيش متطلبات متسجلة للعبة دي"]}
    if not (spec.get("ram_gb") or spec.get("gpu")):
        return {"level": "unknown", "reasons": ["مواصفات جهازك مش معروفة، اكتبها يدوي"]}
    bad, done = compare(spec, req["min"])
    if bad:
        return {"level": "no", "reasons": bad}
    if not done:
        return {"level": "unknown", "reasons": ["مقدرتش أقارن المتطلبات دي"]}
    rec = req.get("rec")
    if rec:
        weak, rdone = compare(spec, rec)
        if weak:
            return {"level": "ok", "reasons": ["بتشتغل على الحد الأدنى: " + "، ".join(weak)]}
    return {"level": "great", "reasons": ["جهازك بيحقق المتطلبات"]}


# ---------------- كاش المتطلبات ----------------
class ReqCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None

    def _file(self):
        return storage.DIR / "reqs.json"

    def _load(self):
        if self.data is None:
            try:
                self.data = json.loads(self._file().read_text(encoding="utf-8"))
            except Exception:  # noqa
                self.data = {}
        return self.data

    def get(self, app):
        with self.lock:
            return self._load().get(str(app))

    def fresh(self, app, now=None):
        e = self.get(app)
        now = now or time.time()
        return bool(e) and now - e.get("ts", 0) < (TTL_OK if e.get("min") else TTL_NONE)

    def put(self, app, entry):
        with self.lock:
            self._load()[str(app)] = {**entry, "ts": time.time()}

    def save(self):
        with self.lock:
            try:
                storage.DIR.mkdir(parents=True, exist_ok=True)
                tmp = self._file().with_suffix(".tmp")
                tmp.write_text(json.dumps(self._load(), ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._file())
            except OSError:
                pass

    def reset_for_tests(self):
        self.data = None


cache = ReqCache()


def fetch_requirements(app):
    """بيجيب متطلبات لعبة واحدة من Steam. بيرفع ApiError لو الشبكة فشلت (ومبيتخزّنش)."""
    data = api.get_json(REQ_URL.format(int(app)), timeout=12)
    node = data.get(str(app)) if isinstance(data, dict) else None
    if not node or not node.get("success"):
        return {"min": None, "rec": None}
    pc = (node.get("data") or {}).get("pc_requirements")
    if not isinstance(pc, dict):
        return {"min": None, "rec": None}
    return {"min": parse_fields(pc.get("minimum")), "rec": parse_fields(pc.get("recommended"))}
