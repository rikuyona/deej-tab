"""Chrome 拡張機能のアイコンを作る (アプリと同じ app_icon() で描く)

    app\\.venv\\Scripts\\python tools\\make_ext_icons.py

extension/icons/ に icon{16,32,48,128}.png (通常) と icon{16,32}-off.png
(deej-tab 未起動時にツールバーに出す、つまみが灰色のもの) を書く。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from deej_tab import app_icon  # noqa: E402

OUT = os.path.join(ROOT, "extension", "icons")
os.makedirs(OUT, exist_ok=True)
for size in (16, 32, 48, 128):
    app_icon(size).save(os.path.join(OUT, f"icon{size}.png"))
for size in (16, 32):
    app_icon(size, lit=False).save(os.path.join(OUT, f"icon{size}-off.png"))
print("書き出しました:", OUT)
