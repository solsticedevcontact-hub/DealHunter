import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dealhunter.tray import make_icon

img = make_icon(256)
img.save(os.path.join(os.path.dirname(__file__), "..", "assets", "icon.ico"),
         sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("icon.ico ready")
