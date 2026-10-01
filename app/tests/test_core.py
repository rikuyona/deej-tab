"""deej_tab の単体テスト:  .venv\\Scripts\\python -m unittest discover -s tests -v"""

import os
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deej_tab as d  # noqa: E402


def make_config(text):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    c = d.Config(path)
    c.load()
    return c


BASE = """slider_mapping:
  0: tab.1
  1: [discord.exe, Spotify.exe]
  2: master
  3: deej.current
  4: deej.unmapped
  5: mic
com_port: socket://127.0.0.1:9
"""


class SliderVolumeTest(unittest.TestCase):
    def test_linear_up_to_100(self):
        self.assertEqual(d.slider_volume(0.0), 0.0)
        self.assertEqual(d.slider_volume(0.5), 0.5)
        self.assertEqual(d.slider_volume(1.0), 1.0)

    def test_max_below_100_scales(self):
        self.assertEqual(d.slider_volume(1.0, 0.5), 0.5)
        self.assertEqual(d.slider_volume(0.5, 0.5), 0.25)

    def test_boost_proportional_by_default(self):
        # 200% で比例なら、真ん中で 100%、いちばん上で 200%
        self.assertEqual(d.slider_volume(0.5, 2.0), 1.0)
        self.assertEqual(d.slider_volume(1.0, 2.0), 2.0)
        self.assertEqual(d.slider_volume(0.25, 2.0), 0.5)
        self.assertEqual(d.slider_volume(0.0, 2.0), 0.0)

    def test_boost_with_unity_position(self):
        # 150% で、スライダーの 80% の所を 100% に
        self.assertEqual(d.slider_volume(0.4, 1.5, 0.8), 0.5)
        self.assertEqual(d.slider_volume(0.8, 1.5, 0.8), 1.0)
        self.assertEqual(d.slider_volume(0.9, 1.5, 0.8), 1.25)
        self.assertEqual(d.slider_volume(1.0, 1.5, 0.8), 1.5)

    def test_snaps_to_unity(self):
        self.assertEqual(d.slider_volume(0.51, 2.0), 1.0)
        self.assertEqual(d.slider_volume(0.49, 2.0), 1.0)
        self.assertNotEqual(d.slider_volume(0.55, 2.0), 1.0)

    def test_monotonic(self):
        for mv, u in ((1.0, None), (2.0, None), (4.0, 0.3), (1.25, 0.9), (0.5, None)):
            vols = [d.slider_volume(p / 100, mv, u) for p in range(101)]
            self.assertEqual(vols, sorted(vols), (mv, u))
            self.assertEqual(vols[0], 0.0)
            self.assertAlmostEqual(vols[-1], mv)


class ConfigTest(unittest.TestCase):
    def test_defaults_and_derived(self):
        c = make_config(BASE)
        self.assertTrue(c.enabled)
        self.assertEqual(c.tab_slots(), [1])
        self.assertEqual(c.mapped_process_names(), {"discord.exe", "spotify.exe"})
        for i in range(6):
            self.assertFalse(c.inverted(i))
            self.assertEqual(c.max_volume(i), 1.0)
            self.assertIsNone(c.unity(i))

    def test_per_slider_invert_overrides_global_no_double_invert(self):
        c = make_config(BASE + """invert_sliders: true
slider_options:
  1: {invert: false}
  2: {invert: true}
""")
        self.assertTrue(c.inverted(0))    # 全体の反転
        self.assertFalse(c.inverted(1))   # 個別にオフ
        self.assertTrue(c.inverted(2))    # 全体・個別の両方オン → 反転 1 回 (元に戻らない)

    def test_bad_slider_options_are_ignored_or_clamped(self):
        c = make_config(BASE + """slider_options:
  0: {max_volume: 9999, unity_position: -5, invert: "yes"}
  x: {invert: true}
  2: nonsense
""")
        self.assertEqual(c.sliders, {0: {"max_volume": 400, "unity_position": 5}})

    def test_save_roundtrip_keeps_unknown_keys(self):
        c = make_config(BASE + "custom_key: 42\n")
        c.save(sliders={0: {"max_volume": 150}, 3: {}}, enabled=False)
        with open(c.path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.assertEqual(raw["custom_key"], 42)
        self.assertEqual(raw["slider_options"], {0: {"max_volume": 150}})
        self.assertFalse(raw["enabled"])
        self.assertFalse(c.enabled)
        self.assertEqual(c.max_volume(0), 1.5)
        # 空にしたら slider_options ごと消える
        c.save(sliders={})
        with open(c.path, encoding="utf-8") as f:
            self.assertNotIn("slider_options", yaml.safe_load(f))

    def test_valid_target(self):
        for t in ("master", "tab.1", "tab.12", "discord.exe", "my game (x86).exe", "deej.current"):
            self.assertTrue(d.valid_target(t), t)
        for t in ("tab.0", "tab.x", "discord", "../evil.exe", "a/b.exe", "", "master2"):
            self.assertFalse(d.valid_target(t), t)


class FakeAudio(d.DummyAudio):
    def __init__(self, foreground=None):
        super().__init__()
        self.foreground = foreground

    def foreground_process_name(self):
        return self.foreground


class HandleLineTest(unittest.TestCase):
    def setUp(self):
        self.c = make_config(BASE)
        self.app = d.DeejTab(self.c, FakeAudio, system=False)
        self.sent = []
        self.app.broadcast = self.sent.append
        self.app.broadcast_ui = lambda m: None
        self.audio = FakeAudio()

    def line(self, *vals):
        self.app.handle_line("|".join(str(v) for v in vals), self.audio)

    def test_applies_and_clamps_windows_targets(self):
        self.c.sliders = {0: {"max_volume": 200}, 1: {"max_volume": 200}}
        self.line(1023, 1023, 1023, 0, 0, 0)
        # タブには 200% がそのまま、Windows のアプリには 100% まで
        self.assertIn({"type": "volume", "slot": 1, "value": 2.0}, self.sent)
        self.assertIn(("discord.exe,spotify.exe", 1.0), self.audio.calls)
        self.assertIn(("master", 1.0), self.audio.calls)
        self.assertEqual(self.app.volumes[:3], [2.0, 2.0, 1.0])

    def test_invert_per_slider(self):
        self.c.sliders = {2: {"invert": True}}
        self.line(1023, 1023, 1023, 1023, 1023, 1023)
        self.assertEqual(self.app.levels, [1.0, 1.0, 0.0, 1.0, 1.0, 1.0])
        self.assertIn(("master", 0.0), self.audio.calls)

    def test_noise_threshold(self):
        self.line(512, 0, 0, 0, 0, 0)
        n = len(self.sent)
        self.line(520, 0, 0, 0, 0, 0)   # 0.8% しか動いていない → 送らない
        self.assertEqual(len(self.sent), n)
        self.line(560, 0, 0, 0, 0, 0)
        self.assertEqual(len(self.sent), n + 1)

    def test_noise_none_follows_every_percent(self):
        self.c.noise = "none"
        self.line(287, 0, 0, 0, 0, 0)   # 28%
        n = len(self.sent)
        self.line(297, 0, 0, 0, 0, 0)   # 29% (0.29 - 0.28 は浮動小数で 0.01 より少し小さい)
        self.assertEqual(len(self.sent), n + 1)

    def test_shown_levels_ignore_noise(self):
        self.line(512, 0, 0, 0, 0, 0)
        first = self.app.ui_levels()["values"][0]
        self.line(520, 0, 0, 0, 0, 0)   # 触っていない時の揺れ → 表示は変えない
        self.assertEqual(self.app.ui_levels()["values"][0], first)
        self.assertEqual(self.app.levels[0], 0.5)   # 判定用の生の位置は今の値
        self.line(560, 0, 0, 0, 0, 0)
        self.assertEqual(self.app.ui_levels()["values"][0], 0.54)

    def test_disabled_shows_levels_but_does_not_apply(self):
        self.c.enabled = False
        self.line(1023, 1023, 1023, 1023, 1023, 1023)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.audio.calls, [])
        self.assertEqual(self.app.levels, [1.0] * 6)

    def test_bad_lines_are_ignored(self):
        for bad in ("", "abc", "1|2|", "5000|1", "1||2", "-1|3"):
            self.app.handle_line(bad, self.audio)
        self.assertEqual(self.app.levels, [])

    def test_current_chrome_goes_to_extension(self):
        self.audio.foreground = "chrome.exe"
        self.line(0, 0, 0, 700, 0, 0)
        self.assertTrue(any(m.get("type") == "current" for m in self.sent))

    def test_current_skips_reserved_app(self):
        self.audio.foreground = "discord.exe"
        self.line(0, 0, 0, 700, 0, 0)
        self.assertNotIn(("discord.exe", 0.68), self.audio.calls)


class SignificantlyDifferentTest(unittest.TestCase):
    def test_edges_snap(self):
        self.assertTrue(d.significantly_different(-1, 0.5, 0.025))
        self.assertTrue(d.significantly_different(0.99, 1.0, 0.025))
        self.assertTrue(d.significantly_different(0.01, 0.0, 0.025))
        self.assertFalse(d.significantly_different(0.5, 0.51, 0.025))


if __name__ == "__main__":
    unittest.main()
