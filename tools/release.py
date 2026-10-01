"""GitHub の Releases に載せる zip を release/ に作る

    app\\.venv\\Scripts\\python tools\\release.py          今ある deej-tab.exe と case/out の STL を使う
    app\\.venv\\Scripts\\python tools\\release.py --build  先に deej-tab.exe を作り直す

できるもの (release/):
  deej-tab-<版>-windows.zip        deej-tab.exe
  deej-tab-extension-<版>.zip      Chrome 拡張機能 (展開して「パッケージ化されていない拡張機能を読み込む」)
  deej-tab-firmware-<版>.zip       Nano 用ファームウェア
  deej-tab-case-stl.zip            ケース・ツマミの STL
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
OUT = os.path.join(ROOT, "release")


def app_version():
    with open(os.path.join(APP, "deej_tab.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)


def ext_version():
    with open(os.path.join(ROOT, "extension", "manifest.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


def make_zip(name, files):
    """files: [(元のパス, zip の中の名前)]"""
    path = os.path.join(OUT, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in files:
            z.write(src, arc)
    print(f"  {name}  ({os.path.getsize(path) / 1e6:.1f} MB, {len(files)} ファイル)")


def tree(folder, prefix):
    """フォルダの中身を prefix/ の下に入れる (__pycache__ は除く)"""
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            src = os.path.join(dirpath, fn)
            files.append((src, os.path.join(prefix, os.path.relpath(src, folder))))
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="先に deej-tab.exe を作り直す")
    args = ap.parse_args()

    if args.build:
        subprocess.run([sys.executable, os.path.join(APP, "build.py")], check=True)

    exe = os.path.join(APP, "deej-tab.exe")
    if not os.path.exists(exe):
        sys.exit("app/deej-tab.exe がありません。--build を付けて実行してください")
    stls = sorted(glob.glob(os.path.join(ROOT, "case", "out", "*.stl")))
    if not stls:
        sys.exit("case/out に STL がありません。case フォルダで case.py を実行してください")

    v, ev = app_version(), ext_version()
    os.makedirs(OUT, exist_ok=True)
    print(f"release/ に作ります (アプリ {v} / 拡張機能 {ev})")
    make_zip(f"deej-tab-{v}-windows.zip", [(exe, "deej-tab/deej-tab.exe")])
    make_zip(f"deej-tab-extension-{ev}.zip", tree(os.path.join(ROOT, "extension"), "deej-tab-extension"))
    make_zip(f"deej-tab-firmware-{v}.zip", tree(os.path.join(ROOT, "firmware"), "firmware"))
    make_zip("deej-tab-case-stl.zip", [(p, os.path.join("case", os.path.basename(p))) for p in stls])


if __name__ == "__main__":
    main()
