"""1.2 で足した機能の単体テスト: プロファイル・ピックアップ・元に戻す・LED・ホットキー・ゲーム追従・OBS の計算"""

import asyncio
import json
import os
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402
import leds  # noqa: E402
import obs  # noqa: E402
import winsys  # noqa: E402

BASE = """slider_mapping:
  0: tab.1
  1: discord.exe
  2: master
  3: obs:マイク
com_port: socket://127.0.0.1:9
"""


def make_config(text=BASE):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    c = d.Config(path)
    c.load()
    return c


def raw(c):
    with open(c.path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class ProfileTest(unittest.TestCase):
    def test_single_profile_keeps_deej_format(self):
        c = make_config()
        c.save(enabled=False)
        self.assertNotIn("profiles", raw(c))
        self.assertEqual(c.active_profile, "標準")
        self.assertEqual(list(c.profiles), ["標準"])

    def test_add_switch_and_back(self):
        c = make_config()
        c.add_profile("ゲーム", copy=False)
        self.assertEqual(c.active_profile, "標準")
        self.assertEqual(set(raw(c)["profiles"]), {"標準", "ゲーム"})
        self.assertTrue(c.switch_profile("ゲーム"))
        self.assertEqual(c.mapping, {})
        c.save(mapping={0: ["deej.game"], 5: ["master"]})
        self.assertTrue(c.switch_profile("標準"))
        self.assertEqual(c.mapping[1], ["discord.exe"])
        self.assertEqual(c.mapping[3], ["obs:マイク"])
        self.assertTrue(c.switch_profile("ゲーム"))
        self.assertEqual(c.mapping, {0: ["deej.game"], 5: ["master"]})
        self.assertFalse(c.switch_profile("ゲーム"))   # もうそのプロファイル

    def test_copy_keeps_slider_options(self):
        c = make_config(BASE + "slider_options:\n  0: {max_volume: 200}\n")
        c.add_profile("コピー")
        c.switch_profile("コピー")
        self.assertEqual(c.max_volume(0), 2.0)
        self.assertEqual(c.mapping[1], ["discord.exe"])

    def test_rename_delete_and_errors(self):
        c = make_config()
        c.add_profile("A")
        c.rename_profile("標準", "普段")
        self.assertEqual(c.active_profile, "普段")
        with self.assertRaises(ValueError):
            c.delete_profile("普段")          # 使用中
        with self.assertRaises(ValueError):
            c.add_profile("A")                # 同じ名前
        with self.assertRaises(ValueError):
            c.add_profile(" 空白 ")
        with self.assertRaises(ValueError):
            c.add_profile("x" * 25)
        c.delete_profile("A")
        self.assertEqual(list(c.profiles), ["普段"])
        # 名前を変えたので、1 つでも profiles は残す (標準ではない名前を覚えておくため)
        self.assertEqual(raw(c)["active_profile"], "普段")

    def test_auto_apps_are_unique(self):
        c = make_config()
        c.add_profile("ゲーム")
        c.update_profile("ゲーム", auto_apps=["Valorant.exe", "valorant.exe"])
        self.assertEqual(c.profiles["ゲーム"]["auto_apps"], ["valorant.exe"])
        c.update_profile("標準", auto_apps=["valorant.exe"])
        self.assertEqual(c.profiles["ゲーム"]["auto_apps"], [])
        self.assertEqual(c.auto_profile_for("valorant.exe"), "標準")
        with self.assertRaises(ValueError):
            c.update_profile("標準", auto_apps=["notepad"])

    def test_obs_targets_are_not_process_names(self):
        c = make_config()
        self.assertEqual(c.mapped_process_names(), {"discord.exe"})
        self.assertTrue(d.valid_target("obs:マイク"))
        self.assertFalse(d.valid_target("obs:"))
        self.assertFalse(d.valid_target("obs:a|b"))


class FakeAudio(d.DummyAudio):
    pass


class AppTest(unittest.TestCase):
    def setUp(self):
        self.c = make_config()
        self.app = d.DeejTab(self.c, FakeAudio, system=False)
        self.sent = []
        self.app.broadcast = self.sent.append
        self.app.broadcast_ui = lambda m: None
        self.audio = FakeAudio()

    def line(self, *vals):
        self.app.handle_line("|".join(str(v) for v in vals), self.audio)

    def pos(self, p):
        return min(1023, round(p * 1023) + 1)   # 切り捨てで 1 % ずれないように

    # ---------- ピックアップ ----------

    def test_pickup_waits_until_crossing(self):
        self.c.add_profile("B")
        self.line(self.pos(0.2), self.pos(0.2), self.pos(0.2))
        self.audio.current.update({"discord.exe": 0.7, "master": 0.5})
        self.app.tab_values[1] = 0.9
        self.app.switch_profile("B")        # 中身は同じ (コピー)
        self.audio.calls.clear()
        self.sent.clear()
        self.line(self.pos(0.2), self.pos(0.2), self.pos(0.2))
        self.assertEqual(set(self.app.pickup), {0, 1, 2})
        self.assertEqual(self.audio.calls, [])
        self.assertFalse([m for m in self.sent if m["type"] == "volume"])
        # ディスコードのスライダーだけ 0.7 を越えて上げる
        self.line(self.pos(0.2), self.pos(0.5), self.pos(0.2))
        self.assertEqual(self.audio.calls, [])
        self.line(self.pos(0.2), self.pos(0.75), self.pos(0.2))
        self.assertIn(("discord.exe", 0.75), self.audio.calls)
        self.assertEqual(set(self.app.pickup), {0, 2})
        # 全体音量はちょうど 0.5 の近くに来たら効く
        self.line(self.pos(0.2), self.pos(0.75), self.pos(0.49))
        self.assertIn(("master", 0.49), self.audio.calls)
        # 設定画面にはピックアップの目印の位置を出す
        self.assertEqual(self.app.ui_levels()["pickup"], {"0": 0.9})

    def test_pickup_off_applies_immediately(self):
        self.c.save(pickup=False)
        self.c.add_profile("B")
        self.line(300, 300, 300)
        self.app.switch_profile("B")
        self.audio.calls.clear()
        self.line(300, 300, 300)
        self.assertEqual(self.app.pickup, {})
        self.assertIn(("master", 0.29), self.audio.calls)

    def test_unknown_volume_does_not_wait(self):
        self.c.add_profile("B")
        self.audio.running = set()          # discord は起動していない → 今の音量が分からない
        self.line(300, 300, 300)
        self.app.switch_profile("B")
        self.line(300, 300, 300)
        self.assertNotIn(1, self.app.pickup)

    # ---------- ゆっくりついてくる ----------

    def with_clock(self):
        """d.time を差し替えて、時間を手で進められるようにする"""
        import types
        real = d.time
        clock = types.SimpleNamespace(t=1000.0, sleep=real.sleep)
        clock.monotonic = lambda: clock.t
        d.time = clock
        self.addCleanup(setattr, d, "time", real)
        return clock

    def test_glide_follows_slowly(self):
        clock = self.with_clock()
        self.c.save(glide=1.0)
        self.line(0, 0, 0)                           # 最初はすぐかける
        self.assertEqual(self.audio.current["master"], 0.0)
        self.line(0, 0, 1023)                        # 全体音量を 100% へ
        self.assertEqual(self.audio.current["master"], 0.0)   # まだ変わっていない
        seen = []
        for _ in range(40):                          # 0.05 秒ずつ 2 秒ぶん
            clock.t += 0.05
            self.app.glide_tick(self.audio)
            seen.append(self.audio.current["master"])
        self.assertEqual(seen, sorted(seen))         # 上がる一方
        at1s = seen[19]                              # 1 秒後は 95% くらい
        self.assertGreater(at1s, 0.9)
        self.assertLess(seen[3], 0.6)                # 0.2 秒後はまだ半分くらい
        self.assertEqual(seen[-1], 1.0)              # 最後は目標ちょうど
        self.assertEqual(self.app.gliding, {})

    def test_glide_per_slider_override(self):
        clock = self.with_clock()
        self.c.save(glide=1.0, sliders={2: {"glide": 0}})
        self.line(0, 0, 0)
        self.line(0, 1023, 1023)
        self.assertEqual(self.audio.current["master"], 1.0)        # スライダー 3 だけすぐ
        self.assertNotEqual(self.audio.current["discord.exe"], 1.0)
        clock.t += 3
        self.app.glide_tick(self.audio)
        self.assertEqual(self.audio.current["discord.exe"], 1.0)
        self.assertEqual(self.c.glide(1), 1.0)
        self.assertEqual(self.c.glide(2), 0)

    def test_glide_stops_on_pause(self):
        clock = self.with_clock()
        self.c.save(glide=3.0, restore_on_pause=False)
        self.line(0, 0, 0)
        self.line(0, 0, 1023)
        clock.t += 0.3
        self.app.glide_tick(self.audio)
        mid = self.audio.current["master"]
        self.app.set_enabled(False)
        clock.t += 5
        self.app.glide_tick(self.audio)
        self.assertEqual(self.audio.current["master"], mid)        # 一時停止中は動かない

    def test_glide_bad_values(self):
        self.assertEqual(d.parse_glide("abc"), 0.0)
        self.assertEqual(d.parse_glide(99), 10.0)
        self.assertEqual(d.parse_glide(-1), 0.0)

    # ---------- 元に戻す ----------

    def test_restore_on_pause(self):
        self.audio.current.update({"master": 0.8, "discord.exe": 0.6})
        self.line(0, 300, 300)
        self.assertEqual(self.audio.current["master"], 0.29)
        self.app.set_enabled(False)
        self.assertTrue(self.app.restore_request.is_set())
        self.app._restore(self.audio)
        self.assertEqual(self.audio.current["master"], 0.8)
        self.assertEqual(self.audio.current["discord.exe"], 0.6)
        # 再開後にまた変えたら、その時の音量を新しく覚える
        self.app.set_enabled(True)
        self.line(0, 500, 500)
        self.assertEqual(self.audio.original["master"], 0.8)

    def test_no_restore_when_option_off(self):
        self.c.save(restore_on_pause=False)
        self.line(0, 300, 300)
        self.app.set_enabled(False)
        self.assertFalse(self.app.restore_request.is_set())

    # ---------- ゲーム ----------

    def test_game_target_follows_tracker(self):
        self.c.save(mapping={4: ["deej.game"]})
        self.app.game_name = "valorant.exe"
        self.line(0, 0, 0, 0, 700)
        self.assertIn(("valorant.exe", 0.68), self.audio.calls)
        self.app.game_name = "discord.exe"   # 別のスライダーに割り当て済み → 触らない
        self.c.save(mapping={1: ["discord.exe"], 4: ["deej.game"]})
        self.audio.calls.clear()
        self.line(0, 0, 0, 0, 200)
        self.assertEqual([c for c in self.audio.calls if "discord" in c[0]], [])

    def test_game_tracker(self):
        g = winsys.GameTracker()
        alive = {100}
        orig = winsys.pid_alive
        winsys.pid_alive = lambda pid: pid in alive
        try:
            self.assertIsNone(g.update("chrome.exe", 5, False))
            self.assertEqual(g.update("game.exe", 100, True), "game.exe")
            self.assertEqual(g.update("discord.exe", 7, False), "game.exe")   # 前に来ても覚えている
            self.assertEqual(g.update("explorer.exe", 1, True), "game.exe")   # デスクトップは数えない
            alive.clear()
            self.assertIsNone(g.update("discord.exe", 7, False))              # 終了したら忘れる
        finally:
            winsys.pid_alive = orig

    # ---------- プロファイルの自動切り替え ----------

    def test_auto_profile_switch_and_return(self):
        self.c.add_profile("ゲーム")
        self.c.update_profile("ゲーム", auto_apps=["game.exe"])
        alive = {42}
        tick = lambda fg, pid: self.app.auto_profile_tick(fg, pid, alive=lambda p: p in alive)  # noqa: E731
        tick("chrome.exe", 1)
        self.assertEqual(self.c.active_profile, "標準")
        tick("game.exe", 42)
        self.assertEqual(self.c.active_profile, "ゲーム")
        tick("discord.exe", 3)                   # ゲームが後ろに回っても、動いている間はそのまま
        self.assertEqual(self.c.active_profile, "ゲーム")
        alive.clear()
        tick("discord.exe", 3)                   # 終了したら元に戻る
        self.assertEqual(self.c.active_profile, "標準")

    def test_manual_switch_suppresses_auto(self):
        self.c.add_profile("ゲーム")
        self.c.update_profile("ゲーム", auto_apps=["game.exe"])
        alive = lambda p: True  # noqa: E731
        self.app.auto_profile_tick("game.exe", 42, alive=alive)
        self.assertEqual(self.c.active_profile, "ゲーム")
        self.app.switch_profile("標準")           # ゲーム中に手で戻した
        self.app.auto_profile_tick("game.exe", 42, alive=alive)
        self.assertEqual(self.c.active_profile, "標準")   # すぐ戻されない
        self.app.auto_profile_tick("chrome.exe", 1, alive=alive)
        self.app.auto_profile_tick("game.exe", 42, alive=alive)
        self.assertEqual(self.c.active_profile, "ゲーム")  # 一度離れてまた前に来たら切り替わる

    # ---------- LED ----------

    def test_led_modes(self):
        self.c.save(mapping={0: ["tab.1"], 1: ["discord.exe"], 2: ["master"], 3: ["tab.2"]},
                    sliders={3: {"max_volume": 200}})
        self.audio.running = set()                  # discord は起動していない
        self.app.tab_assigned = {2}
        self.line(1023, 1023, 0, 1023, 0)
        self.assertEqual(self.app.led_modes(self.audio), ["D", "D", "O", "B", "D"])
        self.app.tab_assigned = {1, 2}
        self.audio.running = {"discord.exe"}
        self.app.pickup = {2: 0.5}
        self.assertEqual(self.app.led_modes(self.audio), ["N", "N", "P", "B", "D"])

    def test_meter_levels_db_scale(self):
        self.c.save(mapping={0: ["master"], 1: ["discord.exe"], 2: ["tab.1"]})
        self.audio.peaks = {"master": 1.0, "discord.exe": 10 ** (-24 / 20)}
        self.app.tab_meter = ({1: 0.0}, d.time.monotonic())
        self.assertEqual(self.app.meter_levels(self.audio), [255, 127, 0, 0, 0])

    def test_update_leds_writes_lines(self):
        class Port:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b
        port = Port()
        self.app._led_state = self.app.new_led_state()
        self.line(1023, 1023, 1023)
        self.app.update_leds(port, self.audio)
        lines = port.data.decode().splitlines()
        self.assertTrue(lines[0].startswith("@P|"))             # 光り方の数値が先
        self.assertEqual([x for x in lines if x.startswith("@L")][0][:4], "@L|0")
        n = len(port.data)
        self.app.update_leds(port, self.audio)           # 変わっていなければすぐには送らない
        self.assertEqual(len(port.data), n)
        self.c.save(led_mode="meter")
        self.app._led_state["next"] = 0
        self.app.update_leds(port, self.audio)
        self.assertIn(b"@V|", port.data)
        self.assertIn({"type": "meter", "on": True}, self.sent)
        self.c.save(led_mode="off")
        port.data = b""
        self.app._led_state["next"] = 0
        self.app.update_leds(port, self.audio)
        self.assertEqual(port.data, b"")


    def test_led_params(self):
        class Port:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b
        port = Port()
        self.app._led_state = self.app.new_led_state()
        self.app.update_leds(port, self.audio)
        self.assertIn(b"IDLE_DEPTH=180", port.data)
        # 画面で変える: 動かしている途中は保存しない、離したら保存 (既定から変えたものだけ)
        self.app.set_led_params({"IDLE_STYLE": 5, "DIM": 999}, save=False)
        self.assertEqual(self.c.led_params, {})
        self.assertEqual(self.app.led_sim.p["IDLE_STYLE"], 5)
        self.assertEqual(self.app.led_params["DIM"], 255)            # 範囲に収める
        port.data = b""
        self.app._led_state["next"] = 0
        self.app.update_leds(port, self.audio)
        self.assertIn(b"IDLE_STYLE=5", port.data)                  # 変わったらすぐ送る
        self.app.set_led_params({"DIM": 40})
        self.assertEqual(self.c.led_params, {"IDLE_STYLE": 5, "DIM": 40})
        with open(self.c.path, encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f)["led_params"], {"IDLE_STYLE": 5, "DIM": 40})
        # 起動アニメを選ぶと Nano にも流させる
        port.data = b""
        self.app._led_state["next"] = 0
        self.app.set_led_params({"BOOT_STYLE": 2})
        self.app.update_leds(port, self.audio)
        self.assertIn(leds.BOOT_LINE.encode(), port.data)
        # config.yaml を直接書き換えても追いつく
        self.c.save(led_params={"TOUCH_STYLE": 1})
        self.app.after_config_change()
        self.assertEqual(self.app.led_params["TOUCH_STYLE"], 1)
        self.assertEqual(self.app.led_params["DIM"], leds.DIM)
        # 既定に戻すと config.yaml から消える
        asyncio.run(self.app.ui_command(None, {"type": "reset_led_params"}))
        self.assertEqual(self.c.led_params, {})
        with open(self.c.path, encoding="utf-8") as f:
            self.assertNotIn("led_params", yaml.safe_load(f))
        # 設定画面には分身の明るさが出る
        self.assertEqual(len(self.app.ui_levels()["led_levels"]), leds.LED_COUNT)


    def test_led_presets(self):
        """明るさなどは光り方ごとに覚える (切り替えると前に使っていた数値に戻る)"""
        app = self.app
        app.set_led_params({"IDLE_STYLE": 5})
        app.set_led_params({"DIM": 80, "IDLE_DEPTH": 100})
        app.set_led_params({"IDLE_STYLE": 1})                      # 初めての光り方は今の数値のまま
        self.assertEqual((app.led_params["DIM"], app.led_params["IDLE_DEPTH"]), (80, 100))
        app.set_led_params({"DIM": 30})
        app.set_led_params({"IDLE_STYLE": 5})                      # スキャナーに戻すと 80 に
        self.assertEqual(app.led_params["DIM"], 80)
        self.assertEqual(app.led_sim.p["DIM"], 80)                 # 分身 (Nano) にも
        app.set_led_params({"BRIGHT": 150})
        app.set_led_params({"IDLE_STYLE": 1})                      # 別のグループの数値はそのまま
        self.assertEqual((app.led_params["DIM"], app.led_params["BRIGHT"]), (30, 150))
        # 保存して読み直しても覚えている
        self.c.load()
        self.assertEqual(self.c.led_presets["IDLE_STYLE"]["5"]["DIM"], 80)
        app2 = d.DeejTab(self.c, lambda: self.audio, system=False)
        app2.set_led_params({"IDLE_STYLE": 5})
        self.assertEqual(app2.led_params["DIM"], 80)
        # 既定に戻すと覚えたものも消える
        asyncio.run(app.ui_command(None, {"type": "reset_led_params"}))
        self.assertEqual((self.c.led_params, self.c.led_presets), ({}, {}))
        self.assertEqual(leds.normalize_presets({"IDLE_STYLE": {"5": {"DIM": 999, "BRIGHT": 3}, "99": {"DIM": 1}},
                                                 "NOPE": {}}), {"IDLE_STYLE": {"5": {"DIM": 255}}})


class LedProtocolTest(unittest.TestCase):
    def test_params_lines(self):
        p = dict(leds.PARAMS, DIM=33, FADE_UP=0.25)
        lines = leds.params_lines(p)
        self.assertTrue(all(len(x) <= leds.PARAM_LINE_MAX for x in lines))
        got = {}
        for x in lines:
            got.update(leds.parse_line(x)[1])
        self.assertEqual(got, p)
        self.assertEqual(leds.parse_line("@P|DIM=999|NOPE=3|FADE_UP=1|IDLE_MS=x"), ("P", {"DIM": 255, "FADE_UP": 0.02}))
        self.assertEqual(leds.parse_line("@B"), ("B",))
        sim = leds.LedSim(0, [500] * 6)
        sim.receive("@P|IDLE_STYLE=3|TWINKLE_CHANCE=90", 0)
        self.assertEqual((sim.p["IDLE_STYLE"], sim.p["TWINKLE_CHANCE"]), (3, 90))
        self.assertEqual(len(sim.advance(5000)), leds.LED_COUNT)
        self.assertEqual(sim.t, 5000)

    def test_firmware_matches(self):
        """ファームウェアの既定値・受け取る範囲が leds.py と同じか"""
        import re
        path = os.path.join(os.path.dirname(__file__), "..", "..", "firmware", "deej-6ch-led", "deej-6ch-led.ino")
        src = open(path, encoding="utf-8").read()
        defaults = {k: int(v) for k, v in re.findall(r"^long (\w+) = (\d+);", src, re.M)}
        want = {k: round(v * 100) if k in leds.FLOAT_PARAMS else v for k, v in leds.PARAMS.items()}
        self.assertEqual(defaults, want)
        table = {k: (int(lo), int(hi)) for k, lo, hi in re.findall(r'\{"(\w+)", &\w+, (\d+), (\d+)\}', src)}
        limits = {k: (round(lo * 100), round(hi * 100)) if k in leds.FLOAT_PARAMS else (lo, hi)
                  for k, (lo, hi) in leds.PARAM_LIMITS.items()}
        self.assertEqual(table, limits)

    def test_roundtrip(self):
        line = leds.status_line(False, list("NODBP"))
        self.assertEqual(line, "@L|0|N|O|D|B|P\n")
        self.assertEqual(leds.parse_line(line), ("L", False, list("NODBP")))
        self.assertEqual(leds.parse_line(leds.meter_line([0, 300, -5, 12, 255])), ("V", [0, 255, 0, 12, 255]))
        for bad in ("@L|0|N|N", "@L|0|N|N|N|N|X", "@V|1|2|3|4", "@V|a|1|2|3|4", "1|2|3"):
            self.assertIsNone(leds.parse_line(bad), bad)

    def test_brightness(self):
        b = leds.led_brightness
        # PC なし: 0 で消灯、動かした直後は明るい、5 分で暗く
        self.assertEqual(b(10000, 0, 0, 9000), 0)
        self.assertEqual(b(10000, 500, 9000, 9000), leds.BRIGHT)
        self.assertEqual(b(10000, 500, 0, 9000), leds.DIM)
        self.assertEqual(b(10 ** 6, 500, 0, 0), leds.SLEEP)
        pc = lambda mode, paused=False: (paused, mode, 9990)  # noqa: E731
        self.assertEqual(b(10000, 500, 0, 9000, pc("O")), 0)
        self.assertEqual(b(10000, 500, 0, 9000, pc("D")), leds.INACTIVE)
        self.assertIn(b(10000, 500, 0, 9000, pc("P")), (0, 160))
        self.assertLessEqual(b(10000, 500, 0, 9000, pc("N", True)), leds.DIM)
        self.assertGreaterEqual(b(10000, 500, 0, 9000, pc("B")), 60)
        self.assertEqual(b(10000, 500, 0, 9000, pc("N"), (255, 9990)), leds.BRIGHT)
        # PC が 3 秒来なければ PC なしの光り方
        self.assertEqual(b(20000, 0, 0, 9000, pc("B")), 0)


class LedSimTest(unittest.TestCase):
    def run_sim(self, sim, start, ms):
        out = None
        for t in range(start, start + ms, leds.LedSim.STEP_MS):
            out = sim.step(t)
        return out

    def test_boot_sweep_then_dim(self):
        sim = leds.LedSim(0, [500] * 6)
        self.assertEqual(sim.step(0), [255, 20, 20, 20, 20])
        self.assertEqual(sim.step(130), [20, 255, 20, 20, 20])
        self.assertEqual(self.run_sim(sim, 600, 1000), [20] * 5)

    def test_touch_fades_up_and_down(self):
        sim = leds.LedSim(0, [500] * 6)
        self.run_sim(sim, 600, 500)
        sim.set_raw([700, 500, 500, 500, 500, 500], 1100)
        up = self.run_sim(sim, 1100, 100)
        self.assertEqual(up[0], 255)                  # すぐ明るく
        self.assertEqual(up[1:], [20] * 4)
        mid = self.run_sim(sim, 1200, 2000)           # 2 秒たつと暗くなり始める
        self.assertLess(mid[0], 255)
        self.assertEqual(self.run_sim(sim, 3200, 2000)[0], 20)

    def test_idle_styles(self):
        p = dict(leds.PARAMS, IDLE_STYLE=1)
        sim = leds.LedSim(0, [500] * 6, p)
        seen = {tuple(self.run_sim(sim, t, 10)) for t in range(600, 5000, 250)}
        self.assertTrue(all(len(set(s)) == 1 for s in seen))          # 呼吸: 全部そろって
        self.assertGreater(len({s[0] for s in seen}), 3)               # 明るさが変わる
        p = dict(leds.PARAMS, IDLE_STYLE=2)
        sim = leds.LedSim(0, [500] * 6, p)
        out = self.run_sim(sim, 600, 1500)
        self.assertGreater(len(set(out)), 1)                           # 波: 1 本ずつずれる

    def test_pc_modes_through_sim(self):
        sim = leds.LedSim(0, [500] * 6)
        sim.receive(leds.status_line(False, list("NOPDB")), 600)
        out = self.run_sim(sim, 600, 300)
        self.assertEqual(out[1], 0)
        self.assertIn(out[2], (0, 160))
        self.assertEqual(out[3], leds.INACTIVE)

    def test_boot_styles(self):
        p = dict(leds.PARAMS)
        expect_len = {0: 5, 1: 3, 2: 6, 3: 9}   # こまの数
        for style, n in expect_len.items():
            p["BOOT_STYLE"] = style
            self.assertIsNotNone(leds.boot_level(0, n * p["SWEEP_MS"] - 1, p), style)
            self.assertIsNone(leds.boot_level(0, n * p["SWEEP_MS"], p), style)
        p["BOOT_STYLE"] = 1                      # 中央から: 最初は真ん中だけ
        self.assertEqual([leds.boot_level(i, 0, p) for i in range(5)], [20, 20, 255, 20, 20])
        p["BOOT_STYLE"] = 2                      # ふわっと: 0 から上がって普段の明るさで終わる
        self.assertEqual(leds.boot_level(0, 0, p), 0)
        self.assertLessEqual(abs(leds.boot_level(0, 6 * p["SWEEP_MS"] - 1, p) - 20), 1)   # 整数の丸めで ±1

    def test_idle_styles_new(self):
        base = 20
        for style in range(len(leds.IDLE_STYLES)):
            p = dict(leds.PARAMS, IDLE_STYLE=style)
            for t in range(0, 8000, 37):
                for i in range(5):
                    v = leds.idle_level(t, base, i, p, 600)
                    self.assertTrue(0 <= v <= 255, (style, t, i, v))
        p = dict(leds.PARAMS, IDLE_STYLE=5)      # スキャナー: 明るい点は 1 本 (隣は尾で、それより暗い)
        for t in (0, 1000, 2000, 3000):
            row = [leds.idle_level(t, base, i, p) for i in range(5)]
            self.assertLessEqual(sum(1 for v in row if v > base + p["IDLE_DEPTH"] // 2), 1, row)
            self.assertLessEqual(sum(1 for v in row if v > base), 3, row)
        p = dict(leds.PARAMS, IDLE_STYLE=6)      # 位置で明るさ: 上げているほど明るい
        self.assertLess(leds.idle_level(0, base, 0, p, 100), leds.idle_level(0, base, 0, p, 900))
        p = dict(leds.PARAMS, IDLE_STYLE=4)      # 心拍: 周期の頭で光る
        self.assertGreater(leds.idle_level(60, base, 0, p), base)
        self.assertEqual(leds.idle_level(600, base, 0, p), base)
        # 5 分放置の時はどのスタイルでも一定
        self.assertEqual(leds.idle_level(60, leds.SLEEP, 0, p), leds.SLEEP)

    def test_touch_styles(self):
        p = dict(leds.PARAMS, TOUCH_STYLE=1)     # 波紋
        touched = [-99999] * 5
        touched[2] = 1000
        self.assertEqual(leds.ripple_levels(1100, touched, p), [91, 153, 0, 153, 91])   # 60% ずつ
        touched[2], touched[0] = -99999, 1000                                        # 端を動かすと反対の端まで
        self.assertEqual(leds.ripple_levels(1100, touched, p), [0, 153, 91, 54, 32])
        touched[2], touched[0] = 1000, -99999
        # 割り当て先が動いていない (D) LED にも波紋を出す。D を操作した時は INACTIVE_TOUCH から広げる
        pc = (False, "D", 1000)
        self.assertEqual(leds.led_brightness(1100, 500, -99999, 1000, pc, None, p, 1, 153), 153)
        self.assertEqual(leds.led_brightness(1100, 500, -99999, 1000, pc, None, p, 1, 0), p["INACTIVE"])
        modes = ["N", "N", "D", "N", "N"]
        self.assertEqual(leds.ripple_levels(1100, touched, p, modes)[1], p["INACTIVE_TOUCH"] * 60 // 100)
        sim = leds.LedSim(0, [500] * 6, p)
        self.run_sim(sim, 600, 500)
        sim.set_raw([500, 500, 700, 500, 500, 500], 1100)
        out = self.run_sim(sim, 1100, 200)
        self.assertEqual(out[2], 255)
        self.assertGreater(out[1], 100)
        self.assertGreater(out[0], 40)
        p = dict(leds.PARAMS, TOUCH_STYLE=2)     # 位置を表示
        self.assertEqual(leds.led_brightness(1000, 1023, 900, 0, p=p), 255)
        self.assertEqual(leds.led_brightness(1000, 511, 900, 0, p=p), 127)


class CalibrationTest(unittest.TestCase):
    """スライダーの端の位置を合わせる (アプリ・LED の分身・Nano への行)"""

    def setUp(self):
        self.c = make_config()
        self.app = d.DeejTab(self.c, FakeAudio, system=False)
        self.app.broadcast = lambda m: None
        self.app.broadcast_ui = lambda m: None
        self.audio = FakeAudio()

    def line(self, *vals):
        self.app.handle_line("|".join(str(v) for v in vals), self.audio)

    def cmd(self, msg):
        class Ws:
            def __init__(self):
                self.sent = []

            async def send(self, data):
                self.sent.append(data)
        ws = Ws()
        asyncio.run(self.app.ui_command(ws, msg))
        return ws

    def test_calibrated_position(self):
        self.c.save(slider_calibration={2: [100, 900]})
        self.assertEqual(self.c.calibration, {2: (100, 900)})
        self.line(0, 0, 100)
        self.assertEqual(self.app.levels[2], 0.0)
        self.line(0, 0, 500)
        self.assertEqual(self.app.levels[2], 0.5)
        self.line(0, 0, 950)
        self.assertEqual(self.app.levels[2], 1.0)
        self.assertEqual(self.audio.current["master"], 1.0)
        self.line(0, 0, 1023)                           # 合わせていないスライダーは 0〜1023 のまま
        self.assertEqual(self.app.levels[0], 0.0)

    def test_invalid_calibration_ignored(self):
        self.c.save(slider_calibration={0: [500, 600], 1: [0, 1023], 2: [10, 1000], 9: [0, 900]})
        self.assertEqual(self.c.calibration, {2: (10, 1000)})
        self.assertEqual(raw(self.c)["slider_calibration"], {2: [10, 1000]})
        self.c.save(slider_calibration={})
        self.assertNotIn("slider_calibration", raw(self.c))

    def test_calibration_survives_profile_switch(self):
        self.c.save(slider_calibration={1: [20, 990]})
        self.c.add_profile("ゲーム", copy=False)
        self.c.switch_profile("ゲーム")
        self.assertEqual(self.c.calibration, {1: (20, 990)})

    def test_measure_and_save(self):
        self.line(500, 500, 500, 500)
        self.cmd({"type": "calib_start"})
        self.audio.calls.clear()
        for v in (500, 30, 3, 700, 1012, 600):         # スライダー 1: 3〜1012、2: 下だけ、3: 動かさない、4: 上まで届かない
            self.line(v, min(v, 500), 500, max(80, min(v, 600)))
        self.assertEqual(self.audio.calls, [])          # 測っている間は音量を変えない
        lv = self.app.ui_levels()["calib"]
        self.assertEqual((lv["min"][0], lv["max"][0]), (3, 1012))
        ws = self.cmd({"type": "calib_save"})
        res = json.loads(ws.sent[0])
        self.assertEqual(res["saved"], [0])
        self.assertEqual(res["skipped"], [1, 2, 3])
        self.assertEqual(self.c.calibration, {0: (0, 1012 - d.CAL_MARGIN)})
        self.assertIsNone(self.app.calib)
        self.line(600, 500, 500, 500)                   # 終わったら今の位置で適用し直す
        self.assertNotEqual(self.audio.calls, [])

    def test_measure_keeps_previous_for_unmoved(self):
        self.c.save(slider_calibration={1: [20, 990]})
        self.line(500, 500)
        self.cmd({"type": "calib_start"})
        self.line(100, 500)
        self.line(900, 500)
        self.cmd({"type": "calib_save"})
        self.assertEqual(self.c.calibration, {0: (100 + d.CAL_MARGIN, 900 - d.CAL_MARGIN), 1: (20, 990)})

    def test_full_range_not_saved(self):
        self.line(500)
        self.cmd({"type": "calib_start"})
        self.line(0)
        self.line(1023)
        res = json.loads(self.cmd({"type": "calib_save"}).sent[0])
        self.assertEqual((res["saved"], res["full"]), ([], [0]))
        self.assertEqual(self.c.calibration, {})
        # 前に合わせていたスライダーが端まで届いたら、既定に戻す (保存した扱い)
        self.c.save(slider_calibration={0: [20, 990]})
        self.cmd({"type": "calib_start"})
        self.line(0)
        self.line(1023)
        res = json.loads(self.cmd({"type": "calib_save"}).sent[0])
        self.assertEqual((res["saved"], res["full"]), ([0], []))
        self.assertEqual(self.c.calibration, {})

    def test_cancel_and_reset(self):
        self.c.save(slider_calibration={0: [20, 990], 1: [30, 980]})
        self.cmd({"type": "calib_start"})
        self.assertIsNotNone(self.app.calib)
        self.cmd({"type": "calib_cancel"})
        self.assertIsNone(self.app.calib)
        self.cmd({"type": "calib_reset", "index": 0})
        self.assertEqual(self.c.calibration, {1: (30, 980)})
        self.cmd({"type": "calib_reset", "index": None})
        self.assertEqual(self.c.calibration, {})

    def test_sends_calibration_lines(self):
        class Port:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b
        port = Port()
        self.c.save(slider_calibration={3: [12, 1000]})
        self.app._led_state = self.app.new_led_state()
        self.line(500, 500, 500, 500)
        self.app.update_leds(port, self.audio)
        lines = port.data.decode().splitlines()
        self.assertIn("@C|3|12|1000", lines)
        self.assertIn("@C|0|0|1023", lines)
        self.assertEqual(self.app.led_sim.cal[3], (12, 1000))   # 分身も同じ範囲で光る


class ControllerIdTest(unittest.TestCase):
    """コントローラーの ID と、別の USB の口に差した時に探し直す"""

    def setUp(self):
        self.c = make_config(BASE.replace("socket://127.0.0.1:9", "COM5"))
        self.app = d.DeejTab(self.c, FakeAudio, system=False)
        self.app.broadcast = lambda m: None
        self.ui = []
        self.app.broadcast_ui = self.ui.append
        self.saved = (d.usb_serial_ports, d.probe_port)
        self.addCleanup(self.restore)

    def restore(self):
        d.usb_serial_ports, d.probe_port = self.saved

    def fake_ports(self, ports, answers):
        """ports: USB のポート一覧、answers: {ポート: probe_port の返り値}"""
        self.probed = []
        d.usb_serial_ports = lambda: list(ports)

        def probe(dev, baud, timeout=None):
            self.probed.append(dev)
            return answers.get(dev)
        d.probe_port = probe

    def test_id_line(self):
        self.assertTrue(d.ID_RE.match("@ID|deej-6ch-led|1.3|0A1B2C3D"))
        self.assertFalse(d.ID_RE.match("@ID|deej-6ch-led|1.3|0a1b2c3d"))
        self.app.on_device_id("@ID|deej-6ch-led|1.3|0A1B2C3D")
        self.assertEqual(self.app.device, {"id": "0A1B2C3D", "fw": "1.3", "name": "deej-6ch-led"})
        self.assertEqual(self.c.device_id, "0A1B2C3D")
        self.assertEqual(raw(self.c)["device_id"], "0A1B2C3D")
        self.assertEqual(self.app.ui_status()["device"]["id"], "0A1B2C3D")

    def test_finds_same_id_on_other_port(self):
        self.c.save(device_id="0A1B2C3D")
        other = {"id": "FFFF0000", "fw": "1.3", "name": "deej-6ch-led"}
        mine = {"id": "0A1B2C3D", "fw": "1.3", "name": "deej-6ch-led"}
        self.fake_ports(["COM6", "COM7", "COM8"], {"COM6": other, "COM7": {"id": None}, "COM8": mine})
        self.assertTrue(self.app.search_controller(("COM5", 9600)))
        self.assertEqual(self.c.com_port, "COM8")
        self.assertEqual(self.probed, ["COM6", "COM7", "COM8"])
        self.assertEqual(self.ui[0]["type"], "notice")

    def test_without_id_takes_first_controller(self):
        self.fake_ports(["COM6", "COM7"], {"COM7": {"id": None}})
        self.assertTrue(self.app.search_controller(("COM5", 9600)))
        self.assertEqual(self.c.com_port, "COM7")

    def test_searches_only_when_ports_change(self):
        self.c.save(device_id="0A1B2C3D")
        self.fake_ports(["COM6"], {})
        self.assertFalse(self.app.search_controller(("COM5", 9600)))
        self.assertFalse(self.app.search_controller(("COM5", 9600)))   # 同じ顔ぶれならもう開かない
        self.assertEqual(self.probed, ["COM6"])
        self.fake_ports(["COM6", "COM9"], {"COM9": {"id": "0A1B2C3D", "fw": "1.3", "name": "x"}})
        self.assertTrue(self.app.search_controller(("COM5", 9600)))
        self.assertEqual(self.c.com_port, "COM9")

    def test_no_search_when_port_present_or_fake(self):
        self.fake_ports(["COM5", "COM6"], {"COM6": {"id": None}})
        self.assertFalse(self.app.search_controller(("COM5", 9600)))   # ある (ほかのアプリが使っている)
        self.assertFalse(self.app.search_controller(("socket://127.0.0.1:9000", 9600)))
        self.assertEqual(self.probed, [])

    def test_firmware_sends_id(self):
        ino = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "firmware", "deej-6ch-led", "deej-6ch-led.ino"), encoding="utf-8").read()
        self.assertIn('"@ID|%s|%s|%08lX"', ino)
        self.assertTrue(d.ID_RE.match("@ID|deej-6ch-led|1.3|%08X" % 0x1D01))


class LedCalibrationTest(unittest.TestCase):
    def test_parse_and_calibrated(self):
        self.assertEqual(leds.parse_line("@C|2|12|1008"), ("C", 2, 12, 1008))
        self.assertIsNone(leds.parse_line("@C|2|500|600"))   # 狭すぎる
        self.assertIsNone(leds.parse_line("@C|6|0|1023"))    # 番号が範囲外
        self.assertEqual(leds.calibrated(12, 12, 1008), 0)
        self.assertEqual(leds.calibrated(1008, 12, 1008), 1023)
        self.assertEqual(leds.calibrated(510, 12, 1008), 511)

    def test_sim_uses_calibrated_value(self):
        # PC なしの時: 生の値は 8 以上でも、合わせた下端以下なら 0 とみなして消える
        sim = leds.LedSim(0, [40] * 6)
        sim.advance(5000)
        self.assertGreater(sim.levels[0], 0)
        sim.receive("@C|0|50|1000", 5000)
        for _ in range(200):
            sim.step(5000 + 10 * _)
        self.assertEqual(sim.step(7010)[0], 0)
        self.assertGreater(sim.step(7020)[1], 0)


class HotkeyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(winsys.parse_hotkey("ctrl+alt+p"), (winsys.MOD_CTRL | winsys.MOD_ALT, ord("P")))
        self.assertEqual(winsys.parse_hotkey("Shift + F5"), (winsys.MOD_SHIFT, 0x74))
        self.assertEqual(winsys.parse_hotkey("f13"), (0, 0x7C))
        self.assertIsNone(winsys.parse_hotkey(""))
        for bad in ("p", "ctrl+", "hyper+p", "ctrl+nosuchkey", "f5"):
            with self.assertRaises(ValueError, msg=bad):
                winsys.parse_hotkey(bad)

    def test_normalize(self):
        self.assertEqual(winsys.normalize_hotkey("Alt+Ctrl+P"), "ctrl+alt+p")
        self.assertEqual(winsys.normalize_hotkey(""), "")


class ObsMathTest(unittest.TestCase):
    def test_volume_curve(self):
        self.assertEqual(obs.volume_to_mul(0.5), 0.25)
        self.assertEqual(obs.volume_to_mul(2.0), 2.0)
        self.assertEqual(obs.volume_to_mul(99), obs.MAX_MUL)
        for v in (0.0, 0.3, 1.0, 1.7):
            self.assertAlmostEqual(d.mul_to_volume(obs.volume_to_mul(v)), v)

    def test_auth_string(self):
        # obs-websocket の説明にある手順どおりに作れているか (SHA256 → base64 を 2 回)
        import base64
        import hashlib
        secret = base64.b64encode(hashlib.sha256(b"pw" + b"salt").digest())
        expect = base64.b64encode(hashlib.sha256(secret + b"chal").digest()).decode()
        self.assertEqual(obs.auth_string("pw", "salt", "chal"), expect)

    def test_slider_position_inverse(self):
        for mv, u in ((1.0, None), (2.0, None), (1.5, 0.8), (0.5, None)):
            for p in (0.0, 0.1, 0.33, 0.6, 0.95, 1.0):
                v = d.slider_volume(p, mv, u)
                if abs(v - 1.0) < 1e-9 and mv > 1:
                    continue   # 100% の吸い付きの範囲は位置が一つに決まらない
                self.assertAlmostEqual(d.slider_position(v, mv, u), p, delta=0.011)


if __name__ == "__main__":
    unittest.main()
