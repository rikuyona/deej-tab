"""Arduino Nano にファームウェアを書き込む (Arduino IDE・avrdude なし)

Nano のブートローダー (Optiboot / 古い ATmegaBOOT) は STK500 v1 で話すので、pyserial で直接書き込む。
新しいブートローダーは 115200bps、互換品に多い古いブートローダー (Old Bootloader) は 57600bps なので、順に試す。
書き込むのはプログラムの領域だけで、EEPROM (LED の光り方・端の位置・ID) とブートローダーは触らない。
書いたあと読み返して照合する。

    python flasher.py COM6 firmware.hex   (確認用)
"""

import sys
import time

import serial

# STK500 v1
STK_OK = 0x10
STK_INSYNC = 0x14
CRC_EOP = 0x20
STK_GET_SYNC = 0x30
STK_SET_DEVICE = 0x42
STK_ENTER_PROGMODE = 0x50
STK_LEAVE_PROGMODE = 0x51
STK_LOAD_ADDRESS = 0x55
STK_PROG_PAGE = 0x64
STK_READ_PAGE = 0x74
STK_READ_SIGN = 0x75

SIGNATURE_328P = bytes([0x1E, 0x95, 0x0F])   # ATmega328P (Nano)
PAGE_SIZE = 128                               # バイト
APP_SIZE = 30720                              # 32KB からブートローダー (古い方 2KB) を除いた大きさ
BAUDS = (115200, 57600)                       # 新しいブートローダー → 古いブートローダー


class FlashError(Exception):
    pass


def parse_hex(text):
    """Intel HEX を読んで、アドレス 0 からのバイト列にする (すき間は 0xFF)"""
    data = {}
    base = 0
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise FlashError(f"HEX の {n} 行目が読めません")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or len(raw) != raw[0] + 5 or sum(raw) & 0xFF:
            raise FlashError(f"HEX の {n} 行目が壊れています")
        count, addr, kind, body = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:4 + raw[0]]
        if kind == 0:
            for i, b in enumerate(body):
                data[base + addr + i] = b
        elif kind == 1:
            break
        elif kind == 2:
            base = ((body[0] << 8) | body[1]) << 4
        elif kind == 4:
            base = ((body[0] << 8) | body[1]) << 16
    if not data:
        raise FlashError("HEX が空です")
    size = max(data) + 1
    if size > APP_SIZE:
        raise FlashError("ファームウェアが大きすぎます")
    image = bytearray(b"\xff" * size)
    for a, b in data.items():
        image[a] = b
    return bytes(image)


class Stk500:
    def __init__(self, port):
        self.port = port

    def command(self, body, reply_len=0, timeout=0.5):
        """コマンドを送り、INSYNC + 返事 + OK を受け取る。返事を返す"""
        self.port.write(bytes(body) + bytes([CRC_EOP]))
        self.port.timeout = timeout
        head = self.port.read(1)
        if head != bytes([STK_INSYNC]):
            raise FlashError("ブートローダーが応答しません")
        data = self.port.read(reply_len) if reply_len else b""
        tail = self.port.read(1)
        if len(data) != reply_len or tail != bytes([STK_OK]):
            raise FlashError("ブートローダーの返事が途中で切れました")
        return data

    def sync(self, tries=8):
        for _ in range(tries):
            self.port.reset_input_buffer()
            try:
                self.command([STK_GET_SYNC], timeout=0.2)
                return True
            except FlashError:
                pass
        return False


def reset_board(port):
    """DTR を下げて上げ、Nano をリセットする (ブートローダーが起動する)"""
    port.dtr = False
    port.rts = False
    time.sleep(0.25)
    port.dtr = True
    port.rts = True
    time.sleep(0.05)
    port.reset_input_buffer()


def open_bootloader(device):
    """ブートローダーと話せるようにして (Serial, Stk500, 速さ) を返す"""
    for baud in BAUDS:
        try:
            port = serial.Serial(device, baudrate=baud, timeout=0.2, write_timeout=1)
        except serial.SerialException:
            raise FlashError(f"{device} を開けません。ポートが合っているか、ほかのアプリ（Arduino IDE など）が使っていないか確かめてください")
        try:
            reset_board(port)
            stk = Stk500(port)
            if stk.sync():
                return port, stk, baud
        except Exception:
            pass
        port.close()
    raise FlashError("ブートローダーが応答しません。ポートが合っているか、ほかのアプリがポートを使っていないか確かめてください")


def flash(device, image, progress=None):
    """image (parse_hex の結果) を device の Nano に書き込み、読み返して照合する。
    progress(0〜100) を書き込み・照合の進み具合で呼ぶ。速さ (bps) を返す"""
    report = progress or (lambda p: None)
    port, stk, baud = open_bootloader(device)
    try:
        sig = stk.command([STK_READ_SIGN], 3)
        if sig != SIGNATURE_328P:
            raise FlashError(f"Nano (ATmega328P) ではないようです (シグネチャ {sig.hex()})")
        # 書き込む部品の情報。Optiboot は読み飛ばすが、古いブートローダーのために avrdude と同じ値を送る
        stk.command([STK_SET_DEVICE, 0x86, 0, 0, 1, 1, 1, 1, 3, 0xFF, 0xFF, 0xFF, 0xFF,
                     0, PAGE_SIZE, 4, 0, 0, 0, 0x80, 0])
        stk.command([STK_ENTER_PROGMODE])
        pages = [(a, image[a:a + PAGE_SIZE]) for a in range(0, len(image), PAGE_SIZE)]
        total = len(pages) * 2
        for n, (addr, chunk) in enumerate(pages):
            chunk = chunk + b"\xff" * (PAGE_SIZE - len(chunk))
            word = addr // 2
            stk.command([STK_LOAD_ADDRESS, word & 0xFF, word >> 8])
            stk.command([STK_PROG_PAGE, 0, PAGE_SIZE, ord("F")] + list(chunk), timeout=1.0)
            report(int((n + 1) * 100 / total))
        for n, (addr, chunk) in enumerate(pages):
            word = addr // 2
            stk.command([STK_LOAD_ADDRESS, word & 0xFF, word >> 8])
            got = stk.command([STK_READ_PAGE, 0, PAGE_SIZE, ord("F")], PAGE_SIZE, timeout=1.0)
            if got[:len(chunk)] != chunk:
                raise FlashError(f"書き込んだ内容が合いません (アドレス {addr:#06x})。もう一度書き込んでください")
            report(int((len(pages) + n + 1) * 100 / total))
        stk.command([STK_LEAVE_PROGMODE])   # ブートローダーを抜けて、書いたファームウェアが起動する
        return baud
    finally:
        port.close()


if __name__ == "__main__":
    dev, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="ascii") as f:
        img = parse_hex(f.read())
    print(f"{len(img)} バイト")
    b = flash(dev, img, lambda p: print(f"\r{p:3d}%", end="", flush=True))
    print(f"\n書き込みました ({b}bps)")
