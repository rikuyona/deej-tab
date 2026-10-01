"""結合テスト: 本物のサーバーを空いているポートで立て、仮のシリアル (socket://) から値を流し、
拡張機能・設定画面と同じ WebSocket でやり取りして確かめる。動いている deej-tab には触らない。

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import yaml
from websockets.asyncio.client import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSerial:
    """Nano の代わり。values を 20ms ごとに送る"""

    def __init__(self):
        self.values = [0] * 6
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            conn, _ = self.srv.accept()
            try:
                while True:
                    conn.sendall(("|".join(map(str, self.values)) + "\n").encode())
                    time.sleep(0.02)
            except OSError:
                conn.close()


class FakeCatalog:
    PNG = b"\x89PNG\r\n\x1a\nfake"

    def list(self):
        return [{"exe": "discord.exe", "name": "Discord", "audio": True, "window": True,
                 "recent": True, "installed": True}]

    def icon(self, exe):
        return self.PNG if exe == "discord.exe" else None


class Client:
    """WebSocket のクライアント。届いたメッセージを貯めて、条件に合うものを待てる"""

    def __init__(self, ws):
        self.ws = ws
        self.msgs = []
        self.cursor = 0   # 前に待って見つかったメッセージの次 (古いメッセージに当たらないように)
        self.task = asyncio.ensure_future(self._read())

    async def _read(self):
        async for raw in self.ws:
            self.msgs.append(json.loads(raw))

    async def wait(self, pred, timeout=3.0, after=None):
        """pred に合うメッセージを待つ。after を省くと、前回見つかったものより後だけを見る"""
        start = self.cursor if after is None else after
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for i in range(start, len(self.msgs)):
                if pred(self.msgs[i]):
                    self.cursor = i + 1
                    return self.msgs[i]
            await asyncio.sleep(0.02)
        raise AssertionError(f"届きませんでした。届いたもの: {self.msgs[start:][-8:]}")

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serial = FakeSerial()
        cls.port = free_port()
        fd, cls.path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"""slider_mapping:
  0: tab.1
  1: discord.exe
  2: master
  3: tab.2
com_port: socket://127.0.0.1:{cls.serial.port}
websocket_port: {cls.port}
""")
        config = d.Config(cls.path)
        config.load()
        cls.config = config
        cls.app = d.DeejTab(config, d.DummyAudio, FakeCatalog(), system=False)
        threading.Thread(target=lambda: asyncio.run(cls.app.run()), daemon=True).start()
        for _ in range(100):
            if d.already_running(cls.port):
                break
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.app.stop.set()

    def run_async(self, coro):
        return asyncio.run(asyncio.wait_for(coro, 20))

    async def ext(self):
        return Client(await connect(f"ws://127.0.0.1:{self.port}/"))

    async def ui(self):
        return Client(await connect(f"ws://127.0.0.1:{self.port}/ui", origin=f"http://127.0.0.1:{self.port}"))

    def set_serial(self, *vals):
        self.serial.values = list(vals) + [0] * (6 - len(vals))

    def raw_config(self):
        with open(self.path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    # ------------------------------------------------------------ テスト

    def test_01_health_and_pages(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as r:
            self.assertEqual(r.status, 200)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn("deej-tab", r.read().decode("utf-8"))

    def test_02_appicon_same_origin_only(self):
        url = f"http://127.0.0.1:{self.port}/appicon/discord.exe"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Sec-Fetch-Site": "same-origin"})) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.read(), FakeCatalog.PNG)
        for bad_url, site in ((url, "cross-site"), (url.replace("discord", "nothing"), "same-origin"),
                              (url.replace("discord.exe", "..%2F..%2Fx.exe"), "same-origin")):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(bad_url, headers={"Sec-Fetch-Site": site}))
            self.assertEqual(e.exception.code, 404)

    def test_03_extension_gets_slots_state_and_volume(self):
        async def go():
            ext = await self.ext()
            await ext.wait(lambda m: m == {"type": "slots", "slots": [1, 2]})
            await ext.wait(lambda m: m["type"] == "state" and m["enabled"] is True)
            self.set_serial(1023)
            await ext.wait(lambda m: m == {"type": "volume", "slot": 1, "value": 1.0})
            self.set_serial(512)
            await ext.wait(lambda m: m == {"type": "volume", "slot": 1, "value": 0.5})
            await ext.ws.close()
        self.run_async(go())

    def test_04_ui_rejects_other_origins(self):
        async def go():
            ws = await connect(f"ws://127.0.0.1:{self.port}/ui", origin="https://evil.example")
            with self.assertRaises(Exception):
                await asyncio.wait_for(ws.recv(), 3)
            self.assertEqual(ws.close_code, 1008)
        self.run_async(go())

    def test_05_ui_initial_messages(self):
        async def go():
            ui = await self.ui()
            cfg = await ui.wait(lambda m: m["type"] == "config")
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["sliders"]["0"], {"invert": False, "invert_own": False,
                                                   "max_volume": 100, "unity_position": None,
                                                   "glide": None})
            await ui.wait(lambda m: m["type"] == "status")
            lists = await ui.wait(lambda m: m["type"] == "lists")
            self.assertEqual(lists["apps"][0]["name"], "Discord")
            self.set_serial(1023, 0, 300)
            lv = await ui.wait(lambda m: m["type"] == "levels" and m["values"][:1] == [1.0])
            self.assertEqual(len(lv["volumes"]), len(lv["values"]))
            await ui.ws.close()
        self.run_async(go())

    def test_06_max_volume_boost_reaches_extension(self):
        async def go():
            ext, ui = await self.ext(), await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            self.set_serial(1023)
            await ext.wait(lambda m: (m["type"] == "state" and m["values"].get("1") == 1.0)
                           or m == {"type": "volume", "slot": 1, "value": 1.0})
            n = len(ext.msgs)
            await ui.send({"type": "set_slider", "index": 0, "max_volume": 200})
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["sliders"]["0"]["max_volume"] == 200)
            self.assertIsNone(cfg["sliders"]["0"]["unity_position"])
            # 設定を変えたら適用し直す (スライダーを動かさなくても 200% が届く)
            await ext.wait(lambda m: m == {"type": "volume", "slot": 1, "value": 2.0}, after=n)
            # 100% の位置を 80% に → 80% の所で 100%
            await ui.send({"type": "set_slider", "index": 0, "unity_position": 80})
            await ui.wait(lambda m: m["type"] == "config" and m["sliders"]["0"]["unity_position"] == 80)
            self.set_serial(round(0.8 * 1023) + 1)
            await ext.wait(lambda m: m == {"type": "volume", "slot": 1, "value": 1.0}, after=n)
            self.assertEqual(self.raw_config()["slider_options"][0], {"max_volume": 200, "unity_position": 80})
            # 範囲外は収める・100% に戻したら項目ごと消える
            await ui.send({"type": "set_slider", "index": 0, "max_volume": 9999})
            await ui.wait(lambda m: m["type"] == "config" and m["sliders"]["0"]["max_volume"] == 400)
            await ui.send({"type": "set_slider", "index": 0, "max_volume": 100, "unity_position": None})
            await ui.wait(lambda m: m["type"] == "config" and m["sliders"]["0"]["max_volume"] == 100)
            self.assertNotIn("slider_options", self.raw_config())
            await ext.ws.close()
            await ui.ws.close()
        self.run_async(go())

    def test_07_pause_and_resume(self):
        async def go():
            ext, ui = await self.ext(), await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            self.set_serial(300)
            await ext.wait(lambda m: m.get("type") == "volume" and m["slot"] == 1)
            await ui.send({"type": "set_enabled", "value": False})
            await ext.wait(lambda m: m == {"type": "enabled", "value": False})
            await ui.wait(lambda m: m["type"] == "config" and m["enabled"] is False)
            self.assertFalse(self.raw_config()["enabled"])
            n = len(ext.msgs)
            self.set_serial(900)
            # 一時停止中も設定画面には位置が出る
            await ui.wait(lambda m: m["type"] == "levels" and m["values"][0] == 0.87)
            await asyncio.sleep(0.3)
            self.assertFalse([m for m in ext.msgs[n:] if m["type"] == "volume"], "一時停止中に音量が送られた")
            # 再開したら今の位置で適用し直す
            await ui.send({"type": "set_enabled", "value": True})
            await ext.wait(lambda m: m == {"type": "enabled", "value": True}, after=n)
            await ext.wait(lambda m: m == {"type": "volume", "slot": 1, "value": 0.87}, after=n)
            # 後から接続した拡張機能にも一時停止が伝わる
            await ui.send({"type": "set_enabled", "value": False})
            await ui.wait(lambda m: m["type"] == "config" and m["enabled"] is False)
            ext2 = await self.ext()
            await ext2.wait(lambda m: m["type"] == "state" and m["enabled"] is False)
            await ui.send({"type": "set_enabled", "value": True})
            await ui.wait(lambda m: m["type"] == "config" and m["enabled"] is True)
            for c in (ext, ext2, ui):
                await c.ws.close()
        self.run_async(go())

    def test_08_invert_per_slider_and_bulk(self):
        async def go():
            ui = await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            self.set_serial(1023, 1023, 1023)
            await ui.send({"type": "set_slider", "index": 2, "invert": True})
            await ui.wait(lambda m: m["type"] == "config" and m["sliders"]["2"]["invert"])
            await ui.wait(lambda m: m["type"] == "levels" and m["values"][:3] == [1.0, 1.0, 0.0])
            # 一括反転: 全部そろう (スライダー 3 は二重に反転して元に戻ったりしない)
            await ui.send({"type": "set_option", "key": "invert_sliders", "value": True})
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["options"]["invert_sliders"])
            self.assertTrue(all(s["invert"] for s in cfg["sliders"].values()))
            self.assertFalse(any(s["invert_own"] for s in cfg["sliders"].values()))
            await ui.wait(lambda m: m["type"] == "levels" and m["values"][:3] == [0.0, 0.0, 0.0])
            # 全体がオンの時に 1 本だけオフ → そのスライダーだけ元の向き
            await ui.send({"type": "set_slider", "index": 1, "invert": False})
            cfg = await ui.wait(lambda m: m["type"] == "config" and not m["sliders"]["1"]["invert"])
            self.assertTrue(cfg["sliders"]["1"]["invert_own"])
            await ui.wait(lambda m: m["type"] == "levels" and m["values"][:3] == [0.0, 1.0, 0.0])
            await ui.send({"type": "set_option", "key": "invert_sliders", "value": False})
            cfg = await ui.wait(lambda m: m["type"] == "config" and not m["options"]["invert_sliders"])
            self.assertFalse(any(s["invert"] for s in cfg["sliders"].values()))
            await ui.ws.close()
        self.run_async(go())

    def test_09_targets_move_and_validation(self):
        async def go():
            ui, ext = await self.ui(), await self.ext()
            await ui.wait(lambda m: m["type"] == "config")
            await ui.send({"type": "set_targets", "index": 4, "targets": ["Discord.exe", "tab.5"]})
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["mapping"].get("4"))
            self.assertEqual(cfg["mapping"]["4"], ["discord.exe", "tab.5"])
            self.assertNotIn("discord.exe", cfg["mapping"].get("1", []))   # 他のスライダーから移った
            await ext.wait(lambda m: m == {"type": "slots", "slots": [1, 2, 5]})
            n = len(ui.msgs)
            for bad in (["../x.exe"], ["tab.0"], ["notepad"]):
                await ui.send({"type": "set_targets", "index": 4, "targets": bad})
                await ui.wait(lambda m: m["type"] == "error", after=n)
                n = len(ui.msgs)
            await ui.send({"type": "set_targets", "index": 9, "targets": []})
            await ui.wait(lambda m: m["type"] == "error", after=n)
            await ui.send({"type": "no_such_command"})
            await ui.wait(lambda m: m["type"] == "error", after=n + 1)
            # 元に戻す
            await ui.send({"type": "set_targets", "index": 1, "targets": ["discord.exe"]})
            await ui.send({"type": "set_targets", "index": 4, "targets": []})
            await ui.wait(lambda m: m["type"] == "config" and not m["mapping"].get("4"))
            await ui.ws.close()
            await ext.ws.close()
        self.run_async(go())

    def test_09b_second_ui_gets_levels_without_movement(self):
        # 1 つ目の画面がつながっていてスライダーが止まっていても、後から開いた画面にメーターが出る
        async def go():
            self.set_serial(700, 100, 200)
            first = await self.ui()
            await first.wait(lambda m: m["type"] == "levels" and m["values"][:1] == [0.68])
            await asyncio.sleep(0.2)
            second = await self.ui()
            await second.wait(lambda m: m["type"] == "levels" and m["values"][:1] == [0.68], timeout=1)
            await first.ws.close()
            await second.ws.close()
        self.run_async(go())

    def test_10_config_file_edit_is_picked_up(self):
        async def go():
            ext = await self.ext()
            await ext.wait(lambda m: m["type"] == "slots")
            raw = self.raw_config()
            raw["slider_mapping"][5] = "tab.3"
            time.sleep(0.05)   # mtime が変わるように
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw, f, allow_unicode=True)
            await ext.wait(lambda m: m["type"] == "slots" and 3 in m["slots"], timeout=5)
            await ext.ws.close()
        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
