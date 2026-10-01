"""deej-tab.exe を作る (PyInstaller)

    .venv\\Scripts\\python build.py

できた exe は app フォルダ (config.yaml の隣) に置く。
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
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "deej-tab",
        "--icon", icon,
        "--version-file", version,
        "--add-data", os.path.join(HERE, "ui.html") + os.pathsep + ".",
        # pystray は実行時に使う部品を選ぶので、Windows 用を明示する
        "--hidden-import", "pystray._win32",
        # pyserial の socket:// (仮スライダー) なども実行時に読み込まれるので入れておく
        "--collect-submodules", "serial.urlhandler",
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "deej_tab.py",
    ], check=True)

    exe = os.path.join(HERE, "deej-tab.exe")
    try:
        shutil.copy2(os.path.join(BUILD, "dist", "deej-tab.exe"), exe)
    except PermissionError:
        sys.exit("\ndeej-tab.exe が起動中のため置き換えられません。トレイのアイコンから終了して、もう一度実行してください。")
    print("\nできました:", exe)


if __name__ == "__main__":
    main()
