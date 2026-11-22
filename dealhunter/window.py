"""بيفتح الواجهة في نافذة تطبيق مستقلة (Edge/Chrome بوضع --app) من غير شريط عنوان.
النافذة ليها بروفايل لوحدها فبتقفل تماماً أول ما تقفلها — يعني مفيش استهلاك لما مش بتستعملها."""
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import storage


def find_browser():
    cands = []
    if os.name == "nt":
        for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
            base = os.environ.get(var)
            if base:
                cands += [Path(base) / "Microsoft/Edge/Application/msedge.exe", Path(base) / "Google/Chrome/Application/chrome.exe"]
    else:
        cands = [shutil.which(n) for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    return next((str(c) for c in cands if c and Path(c).exists()), None)


def open_window(url):
    """بيرجع Popen لو اتفتحت نافذة مستقلة، أو None لو اتفتح المتصفح العادي."""
    exe = find_browser()
    if exe:
        try:
            return subprocess.Popen([exe, f"--app={url}", "--window-size=1320,880", f"--user-data-dir={storage.DIR / 'webprofile'}",
                                     "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                                     "--disable-background-networking", "--disable-component-update"])
        except OSError:
            pass
    webbrowser.open(url)
    return None
