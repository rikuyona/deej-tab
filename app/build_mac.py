"""Mac 版 deej-tab.app を作る (Mac の上で実行する)

    python3 -m venv .venv-mac
    .venv-mac/bin/pip install -r requirements-mac.txt
    .venv-mac/bin/python build_mac.py                 補助プログラムと deej-tab.app を作って zip にする
    .venv-mac/bin/python build_mac.py --helper-only   補助プログラムだけ作る (python deej_tab.py で試す時)

いるもの: Xcode のコマンドラインツール (xcode-select --install。swiftc を使う)

できるもの:
  mac/build/deej-tab-helper                 補助プログラム (Apple シリコン・Intel の両方入り)
  build/dist/deej-tab.app                   アプリ
  ../release/deej-tab-<版>-mac.zip          配布用
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
HELPER_SRC = os.path.join(HERE, "mac", "deej-tab-helper.swift")
HELPER_DIR = os.path.join(HERE, "mac", "build")
HELPER = os.path.join(HELPER_DIR, "deej-tab-helper")
RELEASE = os.path.join(os.path.dirname(HERE), "release")
MIN_MACOS = "14.2"      # Process Tap (アプリごとの音量) が使えるのはこの版から
BUNDLE_ID = "io.github.rikuyona.deej-tab"


def run(cmd, **kw):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def build_helper():
    os.makedirs(HELPER_DIR, exist_ok=True)
    parts = []
    for arch in ("arm64", "x86_64"):
        out = os.path.join(HELPER_DIR, f"deej-tab-helper-{arch}")
        run(["xcrun", "swiftc", "-O", "-swift-version", "5", "-target", f"{arch}-apple-macos{MIN_MACOS}",
             "-o", out, HELPER_SRC])
        parts.append(out)
    run(["lipo", "-create", "-output", HELPER] + parts)
    for p in parts:
        os.remove(p)
    run(["codesign", "--force", "--sign", "-", HELPER])
    print("\n補助プログラム:", HELPER)


def build_app():
    sys.path.insert(0, HERE)
    import deej_tab
    from PIL import Image
    os.makedirs(BUILD, exist_ok=True)

    icon = os.path.join(BUILD, "deej-tab.icns")
    imgs = [deej_tab.app_icon(s) for s in (16, 32, 64, 128, 256, 512, 1024)]
    imgs[-1].save(icon, format="ICNS", append_images=imgs[:-1])

    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "deej-tab",
        "--icon", icon,
        "--osx-bundle-identifier", BUNDLE_ID,
        "--add-data", os.path.join(HERE, "ui.html") + os.pathsep + ".",
        "--add-binary", HELPER + os.pathsep + ".",
        # pystray は実行時に使う部品を選ぶので、Mac 用を明示する
        "--hidden-import", "pystray._darwin",
        "--hidden-import", "macsys",
        "--collect-submodules", "serial.urlhandler",
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "deej_tab.py",
    ], cwd=HERE)

    app = os.path.join(BUILD, "dist", "deej-tab.app")
    plist_path = os.path.join(app, "Contents", "Info.plist")
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleName": "deej-tab",
        "CFBundleDisplayName": "deej-tab",
        "CFBundleShortVersionString": deej_tab.VERSION,
        "CFBundleVersion": deej_tab.VERSION,
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSUIElement": True,              # Dock に出さず、メニューバーにだけ出す
        "NSHighResolutionCapable": True,
        # アプリごとの音量 (Process Tap) を使う時に macOS が出す確認の文言
        "NSAudioCaptureUsageDescription":
            "アプリごとの音量を変えるために、アプリの音を受け取って音量をかけ直します。録音や保存はしません。 / "
            "deej-tab adjusts each app's volume by passing its audio through. Nothing is recorded or saved.",
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)
    # Info.plist を書き換えたので署名し直す (自己署名。配布用の Apple の署名はしていない)
    run(["codesign", "--force", "--deep", "--sign", "-", app])

    os.makedirs(RELEASE, exist_ok=True)
    zip_path = os.path.join(RELEASE, f"deej-tab-{deej_tab.VERSION}-mac.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    run(["ditto", "-c", "-k", "--keepParent", app, zip_path])
    print("\nできました:", app)
    print("配布用:", zip_path)


def main():
    if sys.platform != "darwin":
        sys.exit("Mac の上で実行してください")
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper-only", action="store_true", help="補助プログラムだけ作る")
    args = ap.parse_args()
    build_helper()
    if not args.helper_only:
        build_app()


if __name__ == "__main__":
    main()
