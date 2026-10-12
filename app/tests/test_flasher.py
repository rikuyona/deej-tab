"""ファームウェアの書き込み (flasher.py)。本物の Nano の代わりに、STK500 で話す偽のブートローダーを使う"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402
import flasher  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKETCH = os.path.join(ROOT, "firmware", "deej-6ch-led")

# コマンドごとの長さ (CRC_EOP まで。0x64 / 0x74 は中身の長さが足される)
LENGTHS = {0x30: 2, 0x42: 22, 0x50: 2, 0x51: 2, 0x75: 2, 0x55: 4, 0x64: 5, 0x74: 5}


class FakeBootloader:
    """baud の速さでだけ答える ATmega328P のブートローダー"""
    baud = 115200
    corrupt = False
    last = None

    def __init__(self, device, baudrate, **kw):
        self.ok = baudrate == FakeBootloader.baud
        self.flash = bytearray(b"\xff" * 32768)
        self.inbuf, self.out = bytearray(), bytearray()
        self.addr, self.timeout, self.dtr, self.rts = 0, 0.2, True, True
        self.left = False
        FakeBootloader.last = self

    def write(self, data):
        self.inbuf += data
        while self.inbuf:
            cmd = self.inbuf[0]
            n = LENGTHS.get(cmd, 2)
            if cmd in (0x64, 0x74):
                if len(self.inbuf) < 4:
                    return
                size = (self.inbuf[1] << 8) | self.inbuf[2]
                n = 5 + size if cmd == 0x64 else 5
            if len(self.inbuf) < n:
                return
            body, self.inbuf = bytes(self.inbuf[:n]), self.inbuf[n:]
            if self.ok:
                self.handle(body)

    def handle(self, body):
        cmd, reply = body[0], b""
        if cmd == 0x75:
            reply = flasher.SIGNATURE_328P
        elif cmd == 0x55:
            self.addr = (body[1] | (body[2] << 8)) * 2
        elif cmd == 0x64:
            size = (body[1] << 8) | body[2]
            self.flash[self.addr:self.addr + size] = body[4:4 + size]
        elif cmd == 0x74:
            size = (body[1] << 8) | body[2]
            reply = bytes(self.flash[self.addr:self.addr + size])
            if FakeBootloader.corrupt and self.addr == 0:
                reply = bytes([reply[0] ^ 0xFF]) + reply[1:]
        elif cmd == 0x51:
            self.left = True
        self.out += bytes([flasher.STK_INSYNC]) + reply + bytes([flasher.STK_OK])

    def read(self, n):
        data, self.out = bytes(self.out[:n]), self.out[n:]
        return data

    def reset_input_buffer(self):
        self.out.clear()

    def close(self):
        pass


def sample_hex():
    # 300 バイト (3 ページにまたがる) を 16 バイトずつの行で
    data = bytes((i * 7) & 0xFF for i in range(300))
    lines = []
    for a in range(0, len(data), 16):
        chunk = data[a:a + 16]
        rec = bytes([len(chunk), a >> 8, a & 0xFF, 0]) + chunk
        lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    return "\n".join(lines), data


class FlasherTest(unittest.TestCase):
    def setUp(self):
        self.saved = (flasher.serial.Serial, flasher.time.sleep)
        flasher.serial.Serial = FakeBootloader
        flasher.time.sleep = lambda s: None
        FakeBootloader.baud, FakeBootloader.corrupt = 115200, False
        self.addCleanup(self.restore)

    def restore(self):
        flasher.serial.Serial, flasher.time.sleep = self.saved

    def test_parse_hex(self):
        text, data = sample_hex()
        self.assertEqual(flasher.parse_hex(text), data)
        with self.assertRaises(flasher.FlashError):
            flasher.parse_hex(text.replace(":10000000", ":10000001", 1))   # チェックサムが合わない

    def test_flash_new_bootloader(self):
        text, data = sample_hex()
        progress = []
        self.assertEqual(flasher.flash("COM6", flasher.parse_hex(text), progress.append), 115200)
        self.assertEqual(bytes(FakeBootloader.last.flash[:len(data)]), data)
        self.assertEqual(progress[-1], 100)
        self.assertTrue(FakeBootloader.last.left)   # 書いたファームウェアを起動する

    def test_flash_old_bootloader(self):
        # 互換品に多い古いブートローダーは 57600bps。115200 で答えなければ 57600 で試す
        FakeBootloader.baud = 57600
        text, data = sample_hex()
        self.assertEqual(flasher.flash("COM6", flasher.parse_hex(text)), 57600)
        self.assertEqual(bytes(FakeBootloader.last.flash[:len(data)]), data)

    def test_no_bootloader(self):
        FakeBootloader.baud = 9600   # どちらの速さでも答えない
        with self.assertRaises(flasher.FlashError):
            flasher.flash("COM6", flasher.parse_hex(sample_hex()[0]))

    def test_verify_mismatch(self):
        FakeBootloader.corrupt = True
        with self.assertRaises(flasher.FlashError):
            flasher.flash("COM6", flasher.parse_hex(sample_hex()[0]))


class BundledFirmwareTest(unittest.TestCase):
    def test_bundled_hex_is_current(self):
        # .ino を直したら tools/build_firmware.py で HEX を作り直す。HEX の中に .ino の FW_VERSION があるかで確かめる
        path, version = d.bundled_firmware()
        with open(os.path.join(SKETCH, "deej-6ch-led.ino"), encoding="utf-8") as f:
            self.assertEqual(version, d.FW_VERSION_RE.search(f.read()).group(1))
        with open(path, encoding="ascii") as f:
            image = flasher.parse_hex(f.read())
        self.assertIn(version.encode() + b"\x00", image)
        self.assertIn(b"deej-6ch-led\x00", image)


if __name__ == "__main__":
    unittest.main()
