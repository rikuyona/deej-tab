"""deej-tab.exe を作る (PyInstaller)

    .venv\\Scripts\\python build.py

できた exe と _internal フォルダは app フォルダ (config.yaml の隣) に置く。
1 ファイルの exe (onefile) にしないのは、起動のたびに中身を一時フォルダに展開して 0.7 秒ほど遅くなり、
展開する親と本体の 2 つのプロセスが動くため (設定画面を開くたびにも展開していた)
"""

import os
import shutil
import subprocess
import sys

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041104b0', [
      StringStruct('FileDescription', 'deej-tab 音量ミキサー'),
      StringStruct('ProductName', 'deej-tab'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('InternalName', 'deej-tab'),
      StringStruct('OriginalFilename', 'deej-tab.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0411, 1200])]),
  ]
)
"""

# 入れない部品: Pillow の AVIF・WebP・フォント (FreeType)・色の管理・ImageMath・Tk。
# アイコンを描いて ICO・PNG にするだけなので使わない。どれもなくても Pillow は動く (AVIF だけで 8MB ある)
EXCLUDE = ("PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms", "PIL._imagingmath", "PIL._imagingtk")

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")


def main():
    os.chdir(HERE)
    os.makedirs(BUILD, exist_ok=True)

    # アイコン: トレイと同じ絵を 16〜256px の全サイズで
    sys.path.insert(0, HERE)
    import deej_tab
    icon = os.path.join(BUILD, "deej-tab.ico")
    deej_tab.save_icon(icon)

    # バージョン情報 (エクスプローラーのプロパティ・タスクマネージャーの名前に出る)
    version = os.path.join(BUILD, "version.txt")
    with open(version, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(v=deej_tab.VERSION, t=tuple(map(int, deej_tab.VERSION.split("."))) + (0,)))

    subprocess.run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "deej-tab",
        "--icon", icon,
        "--version-file", version,
        "--add-data", os.path.join(HERE, "ui.html") + os.pathsep + ".",
        # 設定画面から書き込むファームウェア (HEX と、版を読む .ino)
        "--add-data", os.path.join(HERE, "..", "firmware", "deej-6ch-led", "deej-6ch-led.hex") + os.pathsep + "firmware",
        "--add-data", os.path.join(HERE, "..", "firmware", "deej-6ch-led", "deej-6ch-led.ino") + os.pathsep + "firmware",
        # pystray は実行時に使う部品を選ぶので、Windows 用を明示する
        "--hidden-import", "pystray._win32",
        # pyserial の socket:// (仮スライダー) なども実行時に読み込まれるので入れておく
        "--collect-submodules", "serial.urlhandler",
        *[a for m in EXCLUDE for a in ("--exclude-module", m)],
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "deej_tab.py",
    ], check=True)

    dist = os.path.join(BUILD, "dist", "deej-tab")
    exe = os.path.join(HERE, "deej-tab.exe")
    try:
        shutil.copy2(os.path.join(dist, "deej-tab.exe"), exe)
    except PermissionError:
        sys.exit("\ndeej-tab.exe が起動中のため置き換えられません。トレイのアイコンから終了して、もう一度実行してください。")
    # 前の版の部品が残らないよう、_internal は消してから置く
    internal = os.path.join(HERE, "_internal")
    shutil.rmtree(internal, ignore_errors=True)
    shutil.copytree(os.path.join(dist, "_internal"), internal)
    print("\nできました:", exe, "(と _internal フォルダ)")


if __name__ == "__main__":
    main()
