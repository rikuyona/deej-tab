"""Mac 版の部品 (macsys) の単体テスト。補助プログラムは偽物 (fake_mac_helper.py) を使うので Windows でも動く"""

import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import deej_tab as d  # noqa: E402
import macsys  # noqa: E402

FAKE = [sys.executable, os.path.join(HERE, "fake_mac_helper.py")]


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.h = macsys.Helper(FAKE)

    def tearDown(self):
        self.h.stop()

    def test_request(self):
        self.assertEqual(self.h.request("hello")["version"], 1)
        with self.assertRaises(macsys.HelperError):
            self.h.request("nosuchcommand")

    def test_missing_helper(self):
        h = macsys.Helper(["/no/such/deej-tab-helper"])
        with self.assertRaises(macsys.HelperError):
            h.request("hello")

    def test_restart_resends(self):
        """補助プログラムが落ちたら、起動し直して音量を送り直す"""
        audio = macsys.MacAudio(self.h)
        audio.set_processes({"discord.app"}, 0.5)
        try:
            self.h.request("crash", timeout=1)
        except macsys.HelperError:
            pass
        self.assertTrue(wait_for(lambda: not self.h._alive()))
        self.h.failed_at = 0   # 待たずに起動し直させる
        self.h.request("hello")
        self.assertTrue(wait_for(lambda: self.h.request("state")["state"]["gains"].get("discord.app") == 0.25))


class MacAudioTest(unittest.TestCase):
    def setUp(self):
        self.h = macsys.Helper(FAKE)
        self.audio = macsys.MacAudio(self.h)

    def tearDown(self):
        self.h.stop()

    def state(self):
        return self.h.request("state")["state"]

    def test_app_volume(self):
        self.audio.set_processes({"discord.app"}, 0.5)
        self.assertEqual(self.state()["gains"], {"discord.app": 0.25})   # 2 乗 (タブと同じ)
        self.assertEqual(self.audio.get_process("discord.app"), 0.5)
        self.assertEqual(self.audio.get_process("spotify.app"), 1.0)
        self.assertIsNone(self.audio.get_process("notrunning.app"))
        self.assertTrue(self.audio.has_session("spotify.app"))

    def test_unmapped(self):
        """「そのほか」を変えると、そのほかのアプリの個別の音量は消える"""
        self.audio.set_processes({"discord.app"}, 0.5)
        self.audio.set_processes({"spotify.app"}, 0.2)
        self.audio.set_processes(set(), 0.1, mapped_names=frozenset({"discord.app"}))
        st = self.state()
        self.assertEqual(st["gains"], {"discord.app": 0.25})
        self.assertEqual(st["others"], 0.01)
        self.assertEqual(self.audio.get_process("spotify.app"), 0.1)
        self.assertEqual(self.audio.get_process("discord.app"), 0.5)

    def test_master_restore(self):
        self.audio.set_master(0.2)
        self.audio.set_mic(0.3)
        self.audio.set_processes({"discord.app"}, 0.5)
        self.assertEqual(self.audio.get_master(), 0.2)
        self.audio.restore()
        st = self.state()
        self.assertEqual((st["output"], st["input"], st["gains"]), (0.5, 0.8, {}))
        self.assertEqual(self.audio.get_process("discord.app"), 1.0)

    def test_peaks(self):
        self.assertEqual(self.audio.peak({"discord.app"}), 0.5)
        self.assertEqual(self.audio.peak_master(), 0.75)
        self.assertEqual(self.audio.peak(mapped_names=frozenset({"discord.app"})), 0.25)
        self.assertEqual(self.audio.peak_mic(), 0.0)
        self.assertEqual(self.state()["meter"],
                         {"apps": ["discord.app"], "others_exclude": ["discord.app"], "master": True})

    def test_foreground(self):
        self.assertEqual(self.audio.foreground_process_name(), "google chrome.app")


class MacCatalogTest(unittest.TestCase):
    def setUp(self):
        self.h = macsys.Helper(FAKE)
        self.tmp = tempfile.mkdtemp()
        self.c = macsys.MacCatalog(self.h, history_file=os.path.join(self.tmp, "apps.json"))

    def tearDown(self):
        self.h.stop()

    def test_list(self):
        items = {i["exe"]: i for i in self.c.list()}
        self.assertTrue(items["discord.app"]["audio"])
        self.assertTrue(items["safari.app"]["window"])
        self.assertEqual(items["safari.app"]["name"], "Safari")
        self.assertTrue(items["spotify.app"]["recent"])   # 音を扱ったアプリは覚える
        self.assertEqual(self.c.icon("discord.app")[:4], b"\x89PNG")
        self.assertIsNone(self.c.icon("unknown.app"))

    def test_remember(self):
        app = os.path.join(self.tmp, "My Game.app")
        os.mkdir(app)
        self.assertEqual(self.c.remember(app + "/"), "my game.app")
        with self.assertRaises(ValueError):
            self.c.remember(os.path.join(self.tmp, "nothing.app"))


class MacHotkeyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(macsys.parse_hotkey("ctrl+alt+p"), (0x1000 | 0x0800, 0x23))
        self.assertEqual(macsys.parse_hotkey("win+shift+F5"), (0x0100 | 0x0200, 0x60))
        self.assertEqual(macsys.parse_hotkey("f13"), (0, 0x69))
        self.assertEqual(macsys.parse_hotkey("ctrl+numpad3"), (0x1000, 0x55))
        self.assertIsNone(macsys.parse_hotkey(""))
        for bad in ("p", "ctrl+", "hyper+p", "ctrl+nosuchkey", "f5", "pause"):
            with self.assertRaises(ValueError, msg=bad):
                macsys.parse_hotkey(bad)

    def test_normalize(self):
        self.assertEqual(macsys.normalize_hotkey("Win+Alt+Ctrl+P"), "ctrl+alt+win+p")

    def test_register(self):
        h = macsys.Helper(FAKE)
        got = []
        try:
            t = macsys.HotkeyThread(got.append, h)
            t.set_bindings({"pause": "ctrl+alt+p", "profile:x": "", "profile:bad": "nosuch+p"})
            self.assertTrue(wait_for(lambda: "pause" in got))
            self.assertTrue(wait_for(lambda: "profile:bad" in t.errors))
            self.assertEqual(h.request("state")["state"]["hotkeys"], {"pause": {"mods": 0x1800, "key": 0x23}})
        finally:
            h.stop()


class MacMiscTest(unittest.TestCase):
    def test_autostart(self):
        orig = macsys.AGENT_FILE
        macsys.AGENT_FILE = os.path.join(tempfile.mkdtemp(), "LaunchAgents", "x.plist")
        try:
            self.assertIsNone(macsys.autostart_registered())
            macsys.set_autostart(True, ["/Applications/deej-tab.app/Contents/MacOS/deej-tab", "--tray"])
            self.assertEqual(macsys.autostart_registered(),
                             '"/Applications/deej-tab.app/Contents/MacOS/deej-tab" "--tray"')
            macsys.set_autostart(False, [])
            self.assertIsNone(macsys.autostart_registered())
        finally:
            macsys.AGENT_FILE = orig

    def test_game_tracker(self):
        g = macsys.GameTracker()
        self.assertIsNone(g.update("finder.app", os.getpid(), True))
        self.assertEqual(g.update("game.app", os.getpid(), True), "game.app")
        self.assertEqual(g.update("safari.app", os.getpid(), False), "game.app")

    def test_targets(self):
        """.app の名前も割り当て先・自動切り替えのアプリに使える"""
        self.assertTrue(d.valid_target("discord.app"))
        self.assertTrue(d.valid_target("google chrome.app"))
        self.assertFalse(d.valid_target("../x.app"))

    def test_chrome_current_tab(self):
        """Mac の Chrome でも、今使っているアプリ (deej.current) は表示中のタブだけを変える"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("slider_mapping:\n  0: tab.1\n  1: deej.current\ncom_port: socket://127.0.0.1:9\n")
        c = d.Config(path)
        c.load()
        audio = d.DummyAudio()
        audio.foreground = "google chrome.app"
        app = d.DeejTab(c, lambda: audio, system=False)
        sent = []
        app.broadcast = sent.append
        app.broadcast_ui = lambda m: None
        app.handle_line("0|512|0|0|0|0", audio)
        self.assertIn({"type": "current", "value": 0.5}, sent)
        self.assertNotIn("google chrome.app", [k for k, _ in audio.calls])


if __name__ == "__main__":
    unittest.main()
