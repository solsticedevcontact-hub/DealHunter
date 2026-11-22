"""تشغيل البرنامج مع الويندوز (HKCU\\...\\Run) - مفيش صلاحيات أدمن."""
import os
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "DealHunter"


def command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"  # من غير نافذة سودا
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}" --minimized'


def is_enabled():
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except Exception:
        return False


def set_enabled(on):
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False
