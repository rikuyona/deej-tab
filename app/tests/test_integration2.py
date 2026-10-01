"""結合テスト (1.2): 偽の OBS (obs-websocket v5) と、LED の指示を受け取る仮の Nano を相手に通しで確かめる"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

import yaml
from websockets.asyncio.server import serve

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deej_tab as d  # noqa: E402
import leds  # noqa: E402
import obs  # noqa: E402
from test_integration import Client, FakeCatalog, free_port  # noqa: E402

PASSWORD = "secret-pw"


class FakeNano:
    """値を送りつつ、PC から届いた @L / @V の行を貯める"""

    def __init__(self):
        self.values = [0] * 6
        self.received = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            conn, _ = self.srv.accept()
            conn.settimeout(0.02)
            buf = b""
            try:
                while True:
                    conn.sendall(("|".join(map(str, self.values)) + "\n").encode())
                    try:
                        buf += conn.recv(4096)
                    except socket.timeout:
                        pass
                    *lines, buf = buf.split(b"\n")
                    self.received += [x.decode() for x in lines]
            except OSError:
                conn.close()

    def last(self, kind):
        for line in reversed(self.received):
            p = leds.parse_line(line)
            if p and p[0] == kind:
                return p
        return None


class FakeObs:
    """obs-websocket v5 の必要なところだけ: 認証・GetInputList・GetInputVolume・SetInputVolume・イベント"""

    def __init__(self):
        self.inputs = {"マイク": 1.0, "デスクトップ音声": 0.5}
        self.non_audio = ["画面キャプチャ"]
        self.sets = []
        self.port = free_port()
        self.ready = threading.Event()
        self.clients = set()
        threading.Thread(target=lambda: asyncio.run(self.main()), daemon=True).start()
        self.ready.wait(5)

    async def main(self):
        self.loop = asyncio.get_running_loop()
        async with serve(self.handler, "127.0.0.1", self.port):
            self.ready.set()
            await asyncio.Future()

    async def handler(self, ws):
        await ws.send(json.dumps({"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1,
                                                 "authentication": {"challenge": "c1", "salt": "s1"}}}))
        ident = json.loads(await ws.recv())
        if ident["d"].get("authentication") != obs.auth_string(PASSWORD, "s1", "c1"):
            await ws.close(4009, "auth failed")
            return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        self.clients.add(ws)
        try:
            async for raw in ws:
                m = json.loads(raw)["d"]
                rt, data = m["requestType"], m.get("requestData") or {}
                ok, resp = True, {}
                if rt == "GetInputList":
                    resp = {"inputs": [{"inputName": n} for n in list(self.inputs) + self.non_audio]}
                elif rt == "GetInputVolume":
                    ok = data["inputName"] in self.inputs
                    resp = {"inputVolumeMul": self.inputs.get(data["inputName"])}
                elif rt == "SetInputVolume":
                    self.inputs[data["inputName"]] = data["inputVolumeMul"]
                    self.sets.append((data["inputName"], data["inputVolumeMul"]))
                await ws.send(json.dumps({"op": 7, "d": {"requestType": rt, "requestId": m["requestId"],
                                                         "requestStatus": {"result": ok, "code": 100},
                                                         "responseData": resp}}))
        finally:
            self.clients.discard(ws)

    def external_change(self, name, mul):
        """OBS 側で音量を変えた (イベントを送る)"""
        self.inputs[name] = mul

        async def go():
            for ws in list(self.clients):
                await ws.send(json.dumps({"op": 5, "d": {"eventType": "InputVolumeChanged",
                                                         "eventData": {"inputName": name, "inputVolumeMul": mul}}}))
        asyncio.run_coroutine_threadsafe(go(), self.loop).result(3)


def wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class ObsAndLedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nano = FakeNano()
        cls.obs = FakeObs()
        cls.port = free_port()
        fd, cls.path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"""slider_mapping:
  0: obs:マイク
  1: tab.1
  2: master
com_port: socket://127.0.0.1:{cls.nano.port}
websocket_port: {cls.port}
obs:
  enabled: true
  port: {cls.obs.port}
  password: wrong
""")
        cls.config = d.Config(cls.path)
        cls.config.load()
        cls.app = d.DeejTab(cls.config, d.DummyAudio, FakeCatalog(), system=False)
        threading.Thread(target=lambda: asyncio.run(cls.app.run()), daemon=True).start()
        wait_until(lambda: d.already_running(cls.port))

    @classmethod
    def tearDownClass(cls):
        cls.app.stop.set()

    def run_async(self, coro):
        return asyncio.run(asyncio.wait_for(coro, 30))

    async def ui(self):
        from websockets.asyncio.client import connect
        return Client(await connect(f"ws://127.0.0.1:{self.port}/ui", origin=f"http://127.0.0.1:{self.port}"))

    async def ext(self):
        from websockets.asyncio.client import connect
        return Client(await connect(f"ws://127.0.0.1:{self.port}/"))

    def test_1_obs_wrong_password_then_connect(self):
        async def go():
            ui = await self.ui()
            await ui.wait(lambda m: m["type"] == "status" and m["obs"]["enabled"] and not m["obs"]["connected"]
                          and "パスワード" in m["obs"]["error"], timeout=8)
            n = len(ui.msgs)
            await ui.send({"type": "set_obs", "password": PASSWORD})
            # 保存の通知と接続の通知はどちらが先に来てもよい
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["obs"]["has_password"], after=n)
            self.assertNotIn("password", cfg["obs"])          # パスワードは画面に返さない
            await ui.wait(lambda m: m["type"] == "status" and m["obs"]["connected"], timeout=8, after=n)
            lists = await ui.wait(lambda m: m["type"] == "lists" and m["obs_inputs"], timeout=8, after=n)
            self.assertEqual(lists["obs_inputs"], ["デスクトップ音声", "マイク"])   # 音声のないソースは出さない
            await ui.ws.close()
        self.run_async(go())

    def test_2_slider_sets_obs_volume(self):
        self.nano.values = [512, 0, 0, 0, 0, 0]
        self.assertTrue(wait_until(lambda: self.obs.sets and self.obs.sets[-1] == ("マイク", 0.25)),
                        self.obs.sets[-3:])
        self.nano.values = [1023, 0, 0, 0, 0, 0]
        self.assertTrue(wait_until(lambda: self.obs.sets[-1] == ("マイク", 1.0)))

    def test_3_obs_restored_on_pause(self):
        self.app.set_enabled(False)
        # 最初に変える前は 1.0 だった
        self.assertTrue(wait_until(lambda: self.obs.inputs["マイク"] == 1.0))
        self.nano.values = [300, 0, 0, 0, 0, 0]
        time.sleep(0.3)
        self.assertEqual(self.obs.inputs["マイク"], 1.0)     # 一時停止中は変えない
        self.app.set_enabled(True)
        self.assertTrue(wait_until(lambda: abs(self.obs.inputs["マイク"] - 0.29 ** 2) < 1e-9))

    def test_4_led_status_lines(self):
        # 起動直後: tab.1 は拡張機能で割り当てていない → D、master は 0 → O
        self.nano.values = [1023, 1023, 0, 0, 0, 0]
        self.assertTrue(wait_until(lambda: self.nano.last("L") == ("L", False, list("NDODD"))),
                        self.nano.received[-3:])

        async def go():
            ext = await self.ext()
            await ext.wait(lambda m: m["type"] == "state")
            await ext.send({"type": "assigned", "slots": [1]})
            self.assertTrue(await asyncio.to_thread(
                wait_until, lambda: self.nano.last("L") == ("L", False, list("NNODD"))))
            # 一時停止 → g=1
            self.app.set_enabled(False)
            self.assertTrue(await asyncio.to_thread(wait_until, lambda: self.nano.last("L")[1] is True))
            self.app.set_enabled(True)
            await ext.ws.close()
        self.run_async(go())

    def test_5_led_meter_mode_uses_extension_levels(self):
        async def go():
            ext = await self.ext()
            ui = await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            await ui.send({"type": "set_option", "key": "led_mode", "value": "meter"})
            await ext.wait(lambda m: m == {"type": "meter", "on": True})
            await ext.send({"type": "assigned", "slots": [1]})
            for _ in range(10):
                await ext.send({"type": "meter", "levels": {"1": 1.0}})
                await asyncio.sleep(0.05)
            self.assertTrue(await asyncio.to_thread(
                wait_until, lambda: (self.nano.last("V") or ("V", [0, 0]))[1][1] == 255))
            await ui.send({"type": "set_option", "key": "led_mode", "value": "status"})
            await ext.wait(lambda m: m == {"type": "meter", "on": False})
            for c in (ext, ui):
                await c.ws.close()
        self.run_async(go())

    def test_6_profiles_via_ui_and_pickup(self):
        async def go():
            ui = await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            self.nano.values = [1023, 0, 1023, 0, 0, 0]
            await asyncio.to_thread(wait_until, lambda: self.obs.inputs["マイク"] == 1.0)
            await ui.send({"type": "profile_add", "name": "配信", "copy": True})
            await ui.wait(lambda m: m["type"] == "config" and len(m["profiles"]) == 2)
            await ui.send({"type": "profile_update", "name": "配信", "auto_apps": ["obs64.exe"]})
            await ui.wait(lambda m: m["type"] == "config"
                          and any(p["auto_apps"] == ["obs64.exe"] for p in m["profiles"]))
            # OBS 側でマイクを 0.25 (= スライダーの 50%) にしてから切り替える → スライダーが 50% を通るまで効かない
            self.obs.external_change("マイク", 0.25)
            await asyncio.to_thread(wait_until, lambda: self.app.obs.inputs.get("マイク") == 0.25)
            n = len(self.obs.sets)
            await ui.send({"type": "profile_switch", "name": "配信"})
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["active_profile"] == "配信")
            self.assertEqual(cfg["mapping"]["0"], ["obs:マイク"])
            lv = await ui.wait(lambda m: m["type"] == "levels" and "0" in m["pickup"])
            self.assertAlmostEqual(lv["pickup"]["0"], 0.5, delta=0.01)
            await asyncio.sleep(0.3)
            self.assertEqual(self.obs.sets[n:], [])            # まだ効いていない (音量が飛ばない)
            self.assertEqual(self.nano.last("L")[2][0], "P")   # LED は点滅
            self.nano.values = [520, 0, 1023, 0, 0, 0]          # 50% の近く → 効き始める
            await asyncio.to_thread(wait_until, lambda: len(self.obs.sets) > n)
            self.assertEqual(self.obs.sets[-1][0], "マイク")
            await ui.wait(lambda m: m["type"] == "levels" and m["pickup"] == {})
            # 使用中は消せない
            m0 = len(ui.msgs)
            await ui.send({"type": "profile_delete", "name": "配信"})
            await ui.wait(lambda m: m["type"] == "error", after=m0)
            await ui.send({"type": "profile_switch", "name": "標準"})
            await ui.wait(lambda m: m["type"] == "config" and m["active_profile"] == "標準")
            await ui.send({"type": "profile_delete", "name": "配信"})
            await ui.wait(lambda m: m["type"] == "config" and len(m["profiles"]) == 1)
            await ui.ws.close()
        self.run_async(go())

    def test_7_hotkey_setting_validation(self):
        async def go():
            ui = await self.ui()
            await ui.wait(lambda m: m["type"] == "config")
            await ui.send({"type": "set_hotkey", "name": "pause", "value": "Alt+Ctrl+P"})
            cfg = await ui.wait(lambda m: m["type"] == "config" and m["hotkeys"]["pause"])
            self.assertEqual(cfg["hotkeys"]["pause"], "ctrl+alt+p")
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(yaml.safe_load(f)["hotkeys"], {"pause": "ctrl+alt+p"})
            n = len(ui.msgs)
            await ui.send({"type": "set_hotkey", "name": "pause", "value": "p"})
            err = await ui.wait(lambda m: m["type"] == "error", after=n)
            self.assertIn("組み合わせ", err["message"])
            await ui.ws.close()
        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
