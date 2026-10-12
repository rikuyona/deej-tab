"""ファームウェアをコンパイルして、deej-tab が書き込む HEX を作る

    app\\.venv\\Scripts\\python tools\\build_firmware.py

firmware/deej-6ch-led/deej-6ch-led.hex を書き換える (リポジトリに入れる)。.ino を直したら必ず作り直す
(テスト test_bundled_hex_is_current が、HEX の中に .ino の FW_VERSION があるかで確かめる)。
arduino-cli は PATH か %LOCALAPPDATA%/deej-tab/arduino-cli にあるものを使う。
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKETCH = os.path.join(ROOT, "firmware", "deej-6ch-led")
HEX = os.path.join(SKETCH, "deej-6ch-led.hex")
FQBN = "arduino:avr:nano:cpu=atmega328old"   # どちらのブートローダーでもプログラムの領域は同じ


def find_cli():
    cli = shutil.which("arduino-cli")
    if cli:
        return cli
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "deej-tab", "arduino-cli",
                         "arduino-cli.exe" if sys.platform == "win32" else "arduino-cli")
    if os.path.exists(local):
        return local
    sys.exit("arduino-cli がありません")


def main():
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([find_cli(), "compile", "--fqbn", FQBN, "--output-dir", out, SKETCH], check=True)
        shutil.copyfile(os.path.join(out, "deej-6ch-led.ino.hex"), HEX)
    print(f"できました: {HEX}")


if __name__ == "__main__":
    main()
