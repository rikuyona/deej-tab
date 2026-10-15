"""軽さと落ちにくさのテスト: シリアルのまとめ読み・スレッドどうしのぶつかり・エラーで止まらないこと・
起動時の確認・exe から外した Pillow の部品"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = """slider_mapping:
  0: tab.1
  1: discord.exe
  2: master
com_port: socket://127.0.0.1:9
"""


def make_config(text=BASE):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    c = d.Config(path)
    c.load()
    return c


class FakePort:
    """chunks を 1 つずつ届けるポート。読んだ回数を数える。
    1 つ読み切ると、次の chunk は次に SerialLines.read() を呼んだ時に届いたことにする"""

    def __init__(self, chunks):
        self.chunks = [bytes(c) for c in chunks]
        self.reads = 0
        self.drained = False   # 今届いている chunk を読み切った

    def arrive(self):
        self.drained = False

    @property
    def in_waiting(self):
        return 0 if self.drained or not self.chunks else len(self.chunks[0])

    def read(self, n):
        self.reads += 1
        if not self.chunks or self.drained:
            return b""
        c = self.chunks[0]
        out, rest = c[:n], c[n:]
        if rest:
            self.chunks[0] = rest
        else:
            self.chunks.pop(0)
            self.drained = True
        return out


def read(r):
    """次の chunk が届いてから読む"""
    r.port.arrive()
    return r.read()


class SerialLinesTest(unittest.TestCase):
    def test_reads_everything_waiting_at_once(self):
        data = b"1|2|3|4|5|6\r\n" * 10
        port = FakePort([data])
        got, lines = read(d.SerialLines(port))
        self.assertTrue(got)
        self.assertEqual(lines, ["1|2|3|4|5|6"] * 10)
        self.assertEqual(port.reads, 1)   # 1 バイトずつではなく、届いている分をまとめて 1 回で読む

    def test_reads_everything_when_in_waiting_is_only_0_or_1(self):
        # socket:// (仮スライダー) の in_waiting は届いているかどうかしか返さない
        class SocketLike(FakePort):
            @property
            def in_waiting(self):
                return 1 if self.chunks else 0

        port = SocketLike([b"1|2|3|4|5|6\n" * 3])
        self.assertEqual(read(d.SerialLines(port)), (True, ["1|2|3|4|5|6"] * 3))

    def test_joins_line_split_across_reads(self):
        r = d.SerialLines(FakePort([b"512|30", b"0|7\r", b"\n1|2"]))
        self.assertEqual(read(r), (True, []))
        self.assertTrue(r.buf)
        self.assertEqual(read(r), (True, []))
        self.assertEqual(read(r), (True, ["512|300|7"]))
        self.assertTrue(r.buf)   # "1|2" は次の分とつなぐ

    def test_nothing_arrived(self):
        r = d.SerialLines(FakePort([]))
        self.assertEqual(read(r), (False, []))
        self.assertFalse(r.buf)

    def test_drops_runaway_data_without_newline(self):
        r = d.SerialLines(FakePort([b"x" * (d.SERIAL_LINE_MAX + 1)]))
        self.assertEqual(read(r), (True, []))
        self.assertFalse(r.buf)

    def test_partial_wait(self):
        r = d.SerialLines(FakePort([b"1023|541|538|522|536|566\r\n", b"1023|5"]))
        read(r)
        self.assertIsNone(r.partial_wait())   # 行の切れ目で届いた (Windows): 待たない
        self.assertEqual(r.line_len, 26)
        read(r)
        # 残り 20 バイト ÷ 960 バイト/秒 (9600bps)
        self.assertAlmostEqual(r.partial_wait(), 20 / 960)
        r.buf = bytearray(b"x" * 30)          # 前の行より長くなっても、下限で少しだけ待つ
        self.assertEqual(r.partial_wait(), d.SERIAL_WAIT_RANGE[0])

    def test_id_and_values_in_one_read(self):
        r = d.SerialLines(FakePort([b"0|0\r\n@ID|deej-6ch-led|1.3|1E5F90CC\r\n1|1|1|1|1|1\r\n"]))
        self.assertEqual(read(r)[1], ["0|0", "@ID|deej-6ch-led|1.3|1E5F90CC", "1|1|1|1|1|1"])


class FakeNano:
    """socket:// で値の行を送り続ける (20ms ごと)。lines を変えると次から送る行が変わる"""

    def __init__(self, line="512|512|512|512|512|512"):
        self.line = line
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.closed = False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.closed:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            try:
                while not self.closed:
                    conn.sendall((self.line + "\r\n").encode())
                    time.sleep(0.02)
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        self.closed = True
        self.srv.close()


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class SerialThreadTest(unittest.TestCase):
    """本物の serial_thread を仮の Nano (socket://) につないで動かす"""

    def setUp(self):
        self.nano = FakeNano()
        self.c = make_config(BASE.replace("socket://127.0.0.1:9", f"socket://127.0.0.1:{self.nano.port}")
                             + "led_mode: off\n")
        self.app = d.DeejTab(self.c, d.DummyAudio, system=False)
        self.app.broadcast = lambda m: None
        self.app.broadcast_ui = lambda m: None
        self.thread = threading.Thread(target=self.app.serial_thread, daemon=True)

    def tearDown(self):
        self.app.stop.set()
        self.thread.join(3)
        self.nano.close()

    def test_lines_reach_handle_line(self):
        self.thread.start()
        self.assertTrue(wait_for(lambda: self.app.raw_values == [512] * 6))
        self.assertTrue(self.app.serial_state["connected"])

    def test_error_in_one_line_does_not_stop_the_thread(self):
        real = self.app.handle_line
        calls = {"n": 0}

        def flaky(line, audio):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise RuntimeError("テスト用のエラー")
            real(line, audio)

        self.app.handle_line = flaky
        with self.assertLogs("deej-tab", "ERROR") as logs:
            self.thread.start()
            self.assertTrue(wait_for(lambda: self.app.raw_values == [512] * 6))
        # 同じエラーは 1 回だけ残す
        self.assertEqual(sum("届いた行を処理できません" in m for m in logs.output), 1)
        self.assertTrue(self.thread.is_alive())

    def test_unexpected_error_reconnects(self):
        real = self.app.glide_tick
        calls = {"n": 0}

        def broken(audio):
            calls["n"] += 1
            if calls["n"] == 5:
                raise RuntimeError("テスト用のエラー")
            real(audio)

        self.app.glide_tick = broken
        with self.assertLogs("deej-tab", "ERROR") as logs:
            self.thread.start()
            self.assertTrue(wait_for(lambda: calls["n"] > 5))
        self.assertTrue(any("つなぎ直します" in m for m in logs.output))
        self.nano.line = "100|100|100|100|100|100"
        self.assertTrue(wait_for(lambda: self.app.raw_values == [100] * 6))
        self.assertTrue(self.thread.is_alive())


class TrickleTest(unittest.TestCase):
    """Mac の実機のように 1 バイトずつ届いても、1 行あたり 1 回ほどしかループを回らない (前は 1.78 回)"""

    def test_about_one_loop_per_line(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        stop = threading.Event()

        def nano():
            conn, _ = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            start, i = time.perf_counter(), 0
            try:
                while not stop.is_set():
                    for b in b"1023|541|538|522|536|566\r\n":
                        # 9600bps と同じ速さ: i バイト目は送り始めてから i / 960 秒に送る
                        i += 1
                        wait = start + i / 960 - time.perf_counter()
                        if wait > 0:
                            time.sleep(wait)
                        conn.sendall(bytes([b]))
            except OSError:
                pass
            finally:
                conn.close()

        threading.Thread(target=nano, daemon=True).start()
        c = make_config(BASE.replace("socket://127.0.0.1:9", f"socket://127.0.0.1:{srv.getsockname()[1]}")
                        + "led_mode: off\n")
        app = d.DeejTab(c, d.DummyAudio, system=False)
        app.broadcast = lambda m: None
        app.broadcast_ui = lambda m: None
        cnt = {"reads": 0, "lines": 0}
        orig = d.SerialLines.read

        def counting(reader):
            got, lines = orig(reader)
            cnt["reads"] += 1
            cnt["lines"] += len(lines)
            return got, lines

        with mock.patch.object(d.SerialLines, "read", counting):
            th = threading.Thread(target=app.serial_thread, daemon=True)
            th.start()
            time.sleep(4)
            app.stop.set()
            th.join(3)
        stop.set()
        srv.close()
        self.assertGreater(cnt["lines"], 40)
        self.assertLess(cnt["reads"] / cnt["lines"], 1.4)   # 直す前は 1.78 回


class RaceTest(unittest.TestCase):
    """設定画面・トレイ・ショートカットのスレッドが、シリアルのスレッドが使っている途中のものを変えても落ちない"""

    def setUp(self):
        self.c = make_config()
        self.app = d.DeejTab(self.c, d.DummyAudio, system=False)
        self.app.broadcast = lambda m: None
        self.app.broadcast_ui = lambda m: None
        self.audio = d.DummyAudio()

    def test_switch_profile_leaves_values_to_serial_thread(self):
        self.c.add_profile("B")
        self.app.handle_line("300|300|300", self.audio)
        self.app.switch_profile("B")
        # 別のスレッドからは空にしない (シリアルのスレッドが values[idx] を読んでいる途中のことがある)
        self.assertEqual(len(self.app.values), 3)
        self.assertTrue(self.app.reapply)
        self.app.handle_line("300|300|300", self.audio)
        self.assertFalse(self.app.reapply)
        self.assertEqual(len(self.app.values), 3)

    def test_glide_survives_replaced_dict(self):
        self.c.save(glide=0.1)
        self.app.applied = {0: 0.0, 1: 0.0}
        self.app.gliding = {0: 0.5, 1: 0.5}
        self.app._glide_t = time.monotonic() - 10   # 十分たったので、どちらも目標に着く
        real = self.app.apply

        def apply_and_replace(idx, v, audio):
            real(idx, v, audio)
            self.app.gliding = {}   # 設定画面のスレッドが一時停止などで入れ替えた

        self.app.apply = apply_and_replace
        self.app.glide_tick(self.audio)   # 前は 2 本目で KeyError になった
        self.assertEqual(self.app.applied, {0: 0.5, 1: 0.5})

    def test_picked_up_when_wait_was_removed(self):
        self.app.pickup = {}
        self.assertTrue(self.app.picked_up(1, 0.3))   # 前は KeyError

    def test_ui_levels_copies_pickup(self):
        self.app.pickup = {0: 0.5}
        self.assertEqual(self.app.ui_levels()["pickup"], {"0": 0.5})


class UiPumpTest(unittest.TestCase):
    def setUp(self):
        self.app = d.DeejTab(make_config(), d.DummyAudio, system=False)
        self.app.catalog = None
        self.sent = []
        self.app._broadcast_ui = self.sent.append

    def run_pump(self, body):
        async def main():
            task = asyncio.ensure_future(self.app.ui_pump())
            try:
                await body()
            finally:
                task.cancel()
        asyncio.run(main())

    def test_sleeps_while_no_settings_window(self):
        calls = {"n": 0}
        real = self.app.ui_levels

        def counting():
            calls["n"] += 1
            return real()

        self.app.ui_levels = counting

        async def body():
            await asyncio.sleep(0.3)
            self.assertEqual(calls["n"], 0)   # 開いていない間は何もしない
            self.app.ui_clients.add(object())
            self.app.ui_joined.set()
            await asyncio.sleep(0.3)
            self.assertGreater(calls["n"], 0)

        self.run_pump(body)

    def test_keeps_running_after_error(self):
        real = self.app.ui_levels
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("dictionary changed size during iteration")
            return real()

        self.app.ui_levels = flaky
        self.app.ui_clients.add(object())

        async def body():
            await asyncio.sleep(0.4)

        with self.assertLogs("deej-tab", "ERROR"):
            self.run_pump(body)
        self.assertGreater(calls["n"], 2)
        self.assertTrue(any('"type": "levels"' in m for m in self.sent))


class AlreadyRunningTest(unittest.TestCase):
    def test_free_port_is_fast(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        start = time.monotonic()
        self.assertFalse(d.already_running(port))
        if sys.platform == "win32":
            # 前は待ち受けのないポートにつなぎに行き、1 秒待たされていた
            self.assertLess(time.monotonic() - start, 0.5)

    def test_listening_port_is_not_free(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertFalse(d.port_free(s.getsockname()[1]))


@unittest.skipUnless(sys.platform == "win32", "exe は Windows で作る")
class ExcludedPillowTest(unittest.TestCase):
    """exe から外した Pillow の部品がなくても、アイコンを作れる"""

    def test_icons_without_excluded_modules(self):
        sys.path.insert(0, APP_DIR)
        import build
        script = (
            "import sys, os, tempfile\n"
            f"for m in {list(build.EXCLUDE)!r}: sys.modules[m] = None\n"   # None にすると import が失敗する
            f"sys.path.insert(0, {APP_DIR!r})\n"
            "import deej_tab, appcatalog\n"
            "from PIL import Image\n"
            "Image.init()\n"
            "path = os.path.join(tempfile.mkdtemp(), 'i.ico')\n"
            "deej_tab.save_icon(path)\n"
            "assert os.path.getsize(path) > 1000\n"
            "assert deej_tab.icon_bytes('/favicon.ico')[:4] == b'\\x00\\x00\\x01\\x00'\n"
            "assert deej_tab.icon_bytes('/icon.png')[:4] == b'\\x89PNG'\n"
            "assert deej_tab.tray_image(False).size == (64, 64)\n"
            "assert (appcatalog.icon_png(sys.executable) or b'\\x89PNG')[:4] == b'\\x89PNG'\n"
            f"assert all(sys.modules.get(m) is None for m in {list(build.EXCLUDE)!r})\n"
            "print('ok')\n"
        )
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
