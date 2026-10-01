"""deej-tab: deej 互換のPCアプリ + Chromeタブ音量用 WebSocket 配信

- Arduino からのシリアル行 "v0|v1|...|vN" (0〜1023) を読み取る
- config.yaml の slider_mapping に従って Windows / Mac の音量を変える (deej 互換)
- 割り当て先が "tab.N" のスライダーは、WebSocket で Chrome 拡張へ送る
- 設定画面 (ui.html) を http://127.0.0.1:8765/ で配信し、/ui の WebSocket で割り当てを変更する
- タスクトレイ (Mac はメニューバー) に常駐する (pystray)

Windows 固有の部品は winsys.py・appcatalog.py、Mac 固有の部品は macsys.py (と補助プログラム mac/)
"""

import asyncio
import copy
import ctypes
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus

import serial
import websockets
import yaml
from websockets.datastructures import Headers
from websockets.http11 import Response

import leds

log = logging.getLogger("deej-tab")

VERSION = "1.3.0"

LINE_RE = re.compile(r"^\d{1,4}(\|\d{1,4})*$")
# コントローラーの ID の行 (ファームウェア deej-6ch-led 1.3 以降): @ID|名前|版|8 桁の 16 進
ID_RE = re.compile(r"^@ID\|([\w.-]{1,24})\|([\w.]{1,8})\|([0-9A-F]{8})$")
NOISE_THRESHOLDS = {"none": 0.005, "low": 0.015, "default": 0.025, "high": 0.035}   # none は 1% ごと (位置は 1% 刻みなので 0.01 だと誤差で止まる)
SPECIAL_TARGETS = {"master", "mic", "system", "deej.current", "deej.unmapped", "deej.game"}
TAB_RE = re.compile(r"^tab\.([1-9]\d*)$")
# アプリの名前: Windows は exe 名、Mac は .app の名前 (どちらも小文字)
EXE_RE = re.compile(r"^[\w .\-()+&']+\.(exe|app)$")
IS_MAC = sys.platform == "darwin"
CHROME_APPS = ("chrome.exe", "google chrome.app")   # タブの音量を拡張機能で変えるブラウザ
OBS_RE = re.compile(r"^obs:([^\x00-\x1f|]{1,100})$")
PROFILE_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,24}$")
DEFAULT_PROFILE = "標準"
LED_MODES = ("off", "status", "meter")

PICKUP_TOL = 0.03            # ピックアップ: 今の音量との差がこれ以内になったら効き始める
WATCH_INTERVAL = 0.5         # 秒。最前面のアプリ (ゲームの追従・プロファイルの自動切り替え) の確認間隔
LED_INTERVAL = 0.1           # 秒。LED の状態を計算する間隔 (変わった時か LED_HEARTBEAT ごとに送る)
LED_HEARTBEAT = 1.0          # 秒。Nano は 3 秒 PC から何も来ないと PC なしの光り方に戻る
METER_INTERVAL = 1 / 15      # 秒。LED を音に合わせる時に音の大きさを送る間隔
LED_PARAMS_RESEND = 5.0      # 秒。光り方の数値を送り直す間隔 (Nano は接続した時にリセットされ、起動中の行を取りこぼすので)
METER_FLOOR_DB = -48.0       # これより小さい音は LED を光らせない
TAB_METER_STALE = 0.5        # 秒。拡張機能からの音の大きさがこれより古ければ 0 とみなす

# スライダーごとの音量の上限 (%) と「100% にする位置」(スライダーの何 % の所か)
MAX_VOLUME_RANGE = (10, 400)
UNITY_RANGE = (5, 95)
GLIDE_RANGE = (0.0, 10.0)    # 秒。音量がスライダーについてくるまでの時間 (0 = すぐ)
GLIDE_INTERVAL = 0.03        # 秒。ゆっくりついてくる時に音量を変える間隔
GLIDE_DONE = 0.005           # 目標との差がこれ以下になったら目標に合わせて終わる
UNITY_SNAP = 0.02            # 100% の位置の前後これだけは 100% ちょうどに吸い付かせる
CAL_MARGIN = 8               # 端の位置を合わせる時、測った端からこれだけ内側を 0% / 100% にする (端で揺れても確実に届くように)

SESSION_REFRESH_MIN = 5.0    # 秒。これより短い間隔ではセッション一覧を取り直さない
SESSION_REFRESH_MAX = 45.0   # 秒。これより古い一覧はスライダー操作時に取り直す
CONFIG_POLL = 2.0            # 秒。config.yaml の更新確認間隔
SERIAL_STALE = 5.0           # 秒。これだけ何も届かなければ繋ぎ直す (スリープ復帰後の固まり対策)
ID_ASK_AFTER = 2.5           # 秒。つないでからこれだけ ID が来なければ @I で聞く (Nano は開くとリセットされ、起動時に送る)
PROBE_TIME = 3.5             # 秒。コントローラーを探す時に 1 つのポートを読む時間
TRAY_CHECK = 5.0             # 秒。トレイアイコンが消えていないかの確認間隔
KEEPALIVE = 20.0             # 秒。拡張機能の Service Worker を維持するための ping 間隔
UI_LEVEL_INTERVAL = 0.05     # 秒。設定画面へスライダー値を送る間隔
UI_REFRESH = 5.0             # 秒。設定画面へアプリ一覧・COMポート一覧を送り直す間隔
CHANNELS = 6                 # 設定画面に出すスライダー数 (A0〜A5)

# exe 化 (PyInstaller) した時は、config.yaml とログは exe の隣、ui.html は exe の中
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
if IS_MAC and FROZEN:
    # .app の中には書き込まない (署名が壊れる・更新で消える) ので、config.yaml とログはユーザーのフォルダ
    APP_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), "deej-tab")
    os.makedirs(APP_DIR, exist_ok=True)
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "deej-tab"
CONFIG_ARG = None   # 起動時に config.yaml の場所を指定されたとき (設定画面のプロセスにも渡す)
# 設定画面用の Chrome プロファイルと、前回合わせたウィンドウの大きさ
UI_DATA_DIR = (os.path.join(os.path.expanduser("~/Library/Application Support"), "deej-tab") if IS_MAC
               else os.path.join(os.environ.get("LOCALAPPDATA") or APP_DIR, "deej-tab"))
UI_PROFILE_DIR = os.path.join(UI_DATA_DIR, "ui-profile")
WINDOW_FILE = os.path.join(UI_DATA_DIR, "window.json")
SETTINGS_LOCK = os.path.join(UI_DATA_DIR, "settings.lock")   # Mac: 設定画面を 1 枚だけにする
DEFAULT_WINDOW = (1045, 1010)   # 論理ピクセル (150% なら実際は 1.5 倍)。window.json がない時だけ使う

LANGUAGES = ("auto", "ja", "en")
# タスクトレイなど Python 側で出す文言の英語 (設定画面の英語は ui.html の I18N_EN)
TEXT_EN = {
    "設定を開く": "Open settings",
    "一時停止 (スライダーで音量を変えない)": "Pause (sliders don't change the volume)",
    "プロファイル": "Profile",
    "Windows の起動時に自動で起動": "Start with Windows",
    "ログイン時に自動で起動": "Start at login",
    "終了": "Quit",
    "deej-tab - 一時停止中 (スライダーで音量は変わりません)": "deej-tab - Paused (sliders don't change the volume)",
    "deej-tab - {port} に接続中": "deej-tab - Connected to {port}",
    "deej-tab - デバイス未接続 ({port})": "deej-tab - Controller not connected ({port})",
}


def system_language():
    """Windows / Mac の表示言語が日本語なら ja、それ以外は en"""
    if IS_MAC:
        import macsys
        lang = macsys.system_language()
        if lang:
            return lang
    if sys.platform == "win32":
        try:
            return "ja" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x11 else "en"
        except Exception:
            pass
    import locale
    return "ja" if (locale.getlocale()[0] or "").lower().startswith(("ja", "japanese")) else "en"


def resolve_language(language):
    """設定の language (auto / ja / en) から、実際に使う言語 (ja / en)"""
    return system_language() if language == "auto" else language


def sysmod():
    """ホットキー・最前面のアプリの部品 (Windows: winsys / Mac: macsys)"""
    if IS_MAC:
        import macsys
        return macsys
    import winsys
    return winsys


def tr(text, lang, **kw):
    """Python 側の文言を lang の言語にする。kw は {name} の差し込み"""
    return (TEXT_EN.get(text, text) if lang == "en" else text).format(**kw)


DEFAULT_CONFIG = """slider_mapping:
  0: tab.1
  1: tab.2
  2: tab.3
  3: discord.exe
  4: deej.current
  5: master
enabled: true
invert_sliders: false
com_port: COM3
baud_rate: 9600
noise_reduction: default
websocket_port: 8765
"""

CONFIG_HEADER = """\
# deej-tab の設定
# 設定画面 (タスクトレイのアイコン) から変更すると、このファイルが書き換わります。
# 手で書き換えても、保存すると自動で読み直します。
#
# slider_mapping の割り当て先:
#   master / mic / system / deej.current / deej.unmapped / xxx.exe / tab.1, tab.2 …
# enabled: false にすると、スライダーを動かしても音量を変えません (一時停止)
# invert_sliders: 全スライダーの向きの反転。slider_options の invert があるスライダーはそちらが優先
#   (足し合わせないので、両方オンでも元に戻ることはありません)
# slider_options: スライダーごとの設定 (書いていない項目は既定値)
#   invert: true / false          向きの反転
#   max_volume: 150               いちばん上まで上げた時の音量 (%)。100 を超える分は Chrome タブ・OBS だけに効く
#   unity_position: 50            スライダーのどこで 100% になるか (%)。書かなければ比例 (150% なら 67%)
#   glide: 1.0                    音量がスライダーについてくるまでの時間 (秒)。書かなければ下の glide
# device_id: つないだコントローラーの ID (ファームウェアが送る)。com_port が見つからない時、
#   USB のポートを順に開いて同じ ID のコントローラーを探し、com_port を書き換える (別の USB の口に差した時)
# slider_calibration: スライダーの端の位置 (設定画面の「端の位置を合わせる」で測る)。
#   0: [12, 1008]  生の値 12 以下を 0%、1008 以上を 100% にする。書いていないスライダーは 0〜1023。
#   プロファイルを切り替えても変わらない。Nano にも送り、LED も同じ範囲で動く
# glide: 0.3  音量がスライダーについてくるまでの時間 (秒、全スライダー)。0 ならすぐ (目標の 95% まで近づく時間)
# restore_on_pause: 一時停止・終了した時に、Windows と OBS の音量を deej-tab が変える前に戻す
# pickup: プロファイルを切り替えた時、スライダーが今の音量の位置を通るまで効かせない (音量が飛ばない)
# hotkeys.pause: 一時停止のショートカット (例: ctrl+alt+p)
# language: 画面の言語 auto (Windows の表示言語に合わせる) / ja / en
# led_mode: off / status (状態を表示) / meter (音に合わせて光る)。ファームウェア deej-6ch-led が必要
# led_params: LED の光り方の数値 (設定画面の「LED の光り方」で変える。既定から変えたものだけ書く)
# led_presets: 光り方ごとに覚えた明るさなど (切り替えると、その光り方で前に使っていた数値に戻す)
# obs: OBS 連携 (obs-websocket)。割り当て先は obs:ソース名
# profiles: 割り当て一式 (slider_mapping・slider_options) を名前を付けて保存したもの。
#   active_profile が今のプロファイル (上の slider_mapping がその中身)。
#   auto_apps: そのアプリが前に来たら自動で切り替える / hotkey: 切り替えのショートカット

"""


def slider_position(v, max_volume=1.0, unity=None):
    """slider_volume の逆: 音量 v になるスライダーの位置 (ピックアップの目印用)"""
    if max_volume <= 1.0:
        return clamp(v / max_volume, 0.0, 1.0)
    u = unity if unity is not None else 1.0 / max_volume
    if v <= 1.0:
        return clamp(v * u, 0.0, 1.0)
    return clamp(u + (v - 1.0) / (max_volume - 1.0) * (1.0 - u), 0.0, 1.0)


def mul_to_volume(mul):
    """OBS・タブのゲイン → スライダーの音量 (volume_to_mul の逆)"""
    return mul ** 0.5 if mul <= 1 else mul


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def led_now():
    """LED の分身で使う時刻 (ms)"""
    return int(time.monotonic() * 1000)


def slider_volume(p, max_volume=1.0, unity=None):
    """スライダーの位置 p (0〜1) → 音量 (0〜max_volume。1.0 = 100%)。

    max_volume が 1 以下なら比例。1 を超える時は unity (位置) で 100% になるように2本の直線でつなぐ:
    下半分は 0〜100%、上半分は 100%〜max_volume。unity が None なら比例と同じ位置 (1 / max_volume)。
    100% の前後 UNITY_SNAP は 100% ちょうどにする (ちょうど 100% に合わせやすいように)"""
    if max_volume <= 1.0:
        return round(p * max_volume, 2)
    u = unity if unity is not None else 1.0 / max_volume
    if abs(p - u) <= UNITY_SNAP:
        return 1.0
    if p < u:
        return round(p / u, 2)
    return round(1.0 + (p - u) / (1.0 - u) * (max_volume - 1.0), 2)


class Config:
    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.mapping = {}          # {slider_idx: [target, ...]} (小文字)
        self.enabled = True
        self.invert = False        # 全体の反転 (スライダーごとの指定がないスライダーに使う)
        self.sliders = {}          # {slider_idx: {"invert": bool, "max_volume": %, "unity_position": %}} (書いてある項目だけ)
        self.com_port = "COM4"
        self.baud_rate = 9600
        self.noise = "default"
        self.ws_port = 8765
        self.restore_on_pause = True
        self.pickup = True
        self.glide_default = 0.0
        self.calibration = {}      # {slider_idx: (下端, 上端)} 生の値 (合わせたスライダーだけ)
        self.device_id = ""        # 最後につないだコントローラーの ID (8 桁の 16 進。分からなければ "")
        self.hotkeys = {"pause": ""}
        self.led_mode = "status"
        self.language = "auto"     # 画面の言語 (auto / ja / en)
        self.led_params = {}       # 光り方の数値のうち、既定から変えたもの (leds.PARAMS の名前)
        self.led_presets = {}      # 光り方ごとに覚えた数値 {"IDLE_STYLE": {"5": {"DIM": 20, …}}}
        self.obs = {"enabled": False, "host": "127.0.0.1", "port": 4455, "password": ""}
        self.active_profile = DEFAULT_PROFILE
        self.profiles = {}         # {名前: {"auto_apps": [...], "hotkey": "..."}} (割り当ての中身はファイルにだけ持つ)
        self._tab_slots = []
        self._mapped_names = frozenset()

    def load(self):
        raw = self._read()
        mapping = {}
        for k, v in (raw.get("slider_mapping") or {}).items():
            targets = v if isinstance(v, list) else [v]
            mapping[int(k)] = [str(t).strip().lower() for t in targets if t is not None]
        self.mapping = mapping
        self.enabled = bool(raw.get("enabled", True))
        self.invert = bool(raw.get("invert_sliders", False))
        self.sliders = parse_slider_options(raw.get("slider_options"))
        self.com_port = str(raw.get("com_port", "COM4"))
        self.baud_rate = int(raw.get("baud_rate", 9600))
        noise = str(raw.get("noise_reduction", "default")).lower()
        self.noise = noise if noise in NOISE_THRESHOLDS else "default"
        self.ws_port = int(raw.get("websocket_port", 8765))
        self.restore_on_pause = bool(raw.get("restore_on_pause", True))
        self.pickup = bool(raw.get("pickup", True))
        lang = str(raw.get("language", "auto")).lower()
        self.language = lang if lang in LANGUAGES else "auto"
        self.glide_default = parse_glide(raw.get("glide", 0.0))
        dev = str(raw.get("device_id") or "").upper()
        self.device_id = dev if re.fullmatch(r"[0-9A-F]{8}", dev) else ""
        cal = raw.get("slider_calibration")
        self.calibration = leds.normalize_calibration(cal if isinstance(cal, dict) else {})
        hk = raw.get("hotkeys") if isinstance(raw.get("hotkeys"), dict) else {}
        self.hotkeys = {"pause": str(hk.get("pause") or "")}
        led = str(raw.get("led_mode", "status")).lower()
        self.led_mode = led if led in LED_MODES else "status"
        lp = raw.get("led_params")
        self.led_params = leds.normalize_params(lp if isinstance(lp, dict) else {})
        pr = raw.get("led_presets")
        self.led_presets = leds.normalize_presets(pr if isinstance(pr, dict) else {})
        obs = raw.get("obs") if isinstance(raw.get("obs"), dict) else {}
        self.obs = {"enabled": bool(obs.get("enabled", False)), "host": str(obs.get("host") or "127.0.0.1"),
                    "port": int(obs.get("port") or 4455), "password": str(obs.get("password") or "")}
        self.active_profile = str(raw.get("active_profile") or DEFAULT_PROFILE)
        profiles = {}
        for name, p in (raw.get("profiles") or {}).items():
            if not isinstance(p, dict):
                continue
            profiles[str(name)] = {
                "auto_apps": [str(a).lower() for a in (p.get("auto_apps") or []) if EXE_RE.match(str(a).lower())],
                "hotkey": str(p.get("hotkey") or ""),
            }
        profiles.setdefault(self.active_profile, {"auto_apps": [], "hotkey": ""})
        self.profiles = profiles
        self.mtime = os.path.getmtime(self.path)
        # スライダーを動かすたびに使うので、読み込んだ時に作っておく
        slots, names = set(), set()
        for targets in mapping.values():
            for t in targets:
                m = TAB_RE.match(t)
                if m:
                    slots.add(int(m.group(1)))
                elif t not in SPECIAL_TARGETS and not OBS_RE.match(t):
                    names.add(t)
        self._tab_slots = sorted(slots)
        self._mapped_names = frozenset(names)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write(self, raw):
        """書き込んで読み直す。今のプロファイルの中身 (slider_mapping・slider_options) は profiles にも写す。
        プロファイルを 1 つしか使っていなければ profiles は書かない (deej と同じ形のまま)"""
        profiles, active = self._profiles_raw(raw)
        extra = any(p.get("auto_apps") or p.get("hotkey") for p in profiles.values())
        if len(profiles) > 1 or extra or active != DEFAULT_PROFILE:
            raw["profiles"] = profiles
            raw["active_profile"] = active
        else:
            raw.pop("profiles", None)
            raw.pop("active_profile", None)
        body = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(CONFIG_HEADER + body)
        os.replace(tmp, self.path)
        self.load()

    def save(self, mapping=None, sliders=None, **options):
        """config.yaml を書き換えて読み直す。知らないキーはそのまま残す (コメントは消える)"""
        raw = self._read()
        if mapping is not None:
            raw["slider_mapping"] = {
                int(k): (v[0] if len(v) == 1 else list(v))
                for k, v in sorted(mapping.items()) if v
            }
        raw.update(options)
        if "slider_calibration" in options:
            cal = leds.normalize_calibration(options["slider_calibration"])
            raw["slider_calibration"] = {i: list(v) for i, v in sorted(cal.items())}
        for key in ("led_params", "led_presets", "slider_calibration"):
            if key in options and not options[key]:
                raw.pop(key, None)   # 全部既定・何も覚えていなければ書かない
        if sliders is not None:
            opts = {int(k): dict(v) for k, v in sorted(sliders.items()) if v}
            if opts:
                raw["slider_options"] = opts
            else:
                raw.pop("slider_options", None)
        self._write(raw)

    # ---------- プロファイル ----------

    @staticmethod
    def _profiles_raw(raw):
        """ファイルの profiles に、今のプロファイルの中身 (上の slider_mapping など) を写したもの"""
        profiles = {str(k): dict(v) for k, v in (raw.get("profiles") or {}).items() if isinstance(v, dict)}
        active = str(raw.get("active_profile") or DEFAULT_PROFILE)
        cur = profiles.setdefault(active, {})
        cur["slider_mapping"] = raw.get("slider_mapping") or {}
        if raw.get("slider_options"):
            cur["slider_options"] = raw["slider_options"]
        else:
            cur.pop("slider_options", None)
        return profiles, active

    def switch_profile(self, name):
        """切り替えたら True (もうそのプロファイルなら False)"""
        raw = self._read()
        profiles, active = self._profiles_raw(raw)
        if name not in profiles:
            raise ValueError(f"プロファイルがありません: {name}")
        if name == active:
            return False
        p = profiles[name]
        raw["slider_mapping"] = p.get("slider_mapping") or {}
        if p.get("slider_options"):
            raw["slider_options"] = p["slider_options"]
        else:
            raw.pop("slider_options", None)
        raw["profiles"] = profiles
        raw["active_profile"] = name
        self._write(raw)
        return True

    @staticmethod
    def _check_name(name):
        if not isinstance(name, str) or not PROFILE_NAME_RE.match(name) or name != name.strip():
            raise ValueError("プロファイルの名前は 1〜24 文字で付けてください (前後の空白なし)")

    def add_profile(self, name, copy=True):
        self._check_name(name)
        raw = self._read()
        profiles, active = self._profiles_raw(raw)
        if name in profiles:
            raise ValueError(f"同じ名前のプロファイルがあります: {name}")
        new = {"slider_mapping": {}}
        if copy:
            src = profiles[active]
            new["slider_mapping"] = dict(src.get("slider_mapping") or {})
            if src.get("slider_options"):
                new["slider_options"] = dict(src["slider_options"])
        profiles[name] = new
        raw["profiles"] = profiles
        raw["active_profile"] = active
        self._write(raw)

    def rename_profile(self, name, new):
        self._check_name(new)
        raw = self._read()
        profiles, active = self._profiles_raw(raw)
        if name not in profiles:
            raise ValueError(f"プロファイルがありません: {name}")
        if new in profiles and new != name:
            raise ValueError(f"同じ名前のプロファイルがあります: {new}")
        raw["profiles"] = {(new if k == name else k): v for k, v in profiles.items()}
        raw["active_profile"] = new if active == name else active
        self._write(raw)

    def delete_profile(self, name):
        raw = self._read()
        profiles, active = self._profiles_raw(raw)
        if name not in profiles:
            raise ValueError(f"プロファイルがありません: {name}")
        if name == active:
            raise ValueError("使用中のプロファイルは消せません。先に別のプロファイルに切り替えてください")
        del profiles[name]
        raw["profiles"] = profiles
        raw["active_profile"] = active
        self._write(raw)

    def update_profile(self, name, auto_apps=None, hotkey=None):
        raw = self._read()
        profiles, active = self._profiles_raw(raw)
        if name not in profiles:
            raise ValueError(f"プロファイルがありません: {name}")
        p = profiles[name]
        if auto_apps is not None:
            apps = []
            for a in auto_apps:
                a = str(a).strip().lower()
                if not EXE_RE.match(a):
                    raise ValueError(f"アプリの exe 名（例: valorant.exe）を入れてください: {a}" if not IS_MAC
                                     else f"アプリの名前（例: discord.app）を入れてください: {a}")
                if a not in apps:
                    apps.append(a)
            # 同じアプリは 1 つのプロファイルにだけ (どれに切り替えるか迷わないように)
            for k, other in profiles.items():
                if k != name and other.get("auto_apps"):
                    other["auto_apps"] = [x for x in other["auto_apps"] if x not in apps]
                    if not other["auto_apps"]:
                        other.pop("auto_apps")
            if apps:
                p["auto_apps"] = apps
            else:
                p.pop("auto_apps", None)
        if hotkey is not None:
            if hotkey:
                p["hotkey"] = hotkey
            else:
                p.pop("hotkey", None)
        raw["profiles"] = profiles
        raw["active_profile"] = active
        self._write(raw)

    def auto_profile_for(self, exe):
        for name, p in self.profiles.items():
            if exe in p["auto_apps"]:
                return name
        return None

    def changed_on_disk(self):
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

    def tab_slots(self):
        return self._tab_slots

    def mapped_process_names(self):
        return self._mapped_names

    # ---------- スライダーごとの設定 ----------

    def glide(self, idx):
        """音量がついてくるまでの時間 (秒)。スライダーごとの指定があればそれ"""
        return self.sliders.get(idx, {}).get("glide", self.glide_default)

    def inverted(self, idx):
        """実際に反転するか。スライダーごとの指定があればそれだけを使う (全体の反転と重ねない)"""
        return self.sliders.get(idx, {}).get("invert", self.invert)

    def max_volume(self, idx):
        return self.sliders.get(idx, {}).get("max_volume", 100) / 100.0

    def unity(self, idx):
        u = self.sliders.get(idx, {}).get("unity_position")
        return None if u is None else u / 100.0

    def calibrated(self, idx, n):
        """生の値 n (0〜1023) → スライダーの位置 (0〜1)。端の位置を合わせていればその範囲で"""
        lo, hi = self.calibration.get(idx, leds.CAL_DEFAULT)
        return clamp((n - lo) / (hi - lo), 0.0, 1.0)

    def volume(self, idx, p):
        return slider_volume(p, self.max_volume(idx), self.unity(idx))

    def position(self, idx, v):
        return slider_position(v, self.max_volume(idx), self.unity(idx))


def parse_slider_options(raw):
    """config.yaml の slider_options を読む。おかしな値は捨てる / 範囲に収める"""
    result = {}
    if not isinstance(raw, dict):
        return result
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        o = {}
        if isinstance(v.get("invert"), bool):
            o["invert"] = v["invert"]
        if isinstance(v.get("max_volume"), (int, float)) and not isinstance(v.get("max_volume"), bool):
            o["max_volume"] = int(clamp(round(v["max_volume"]), *MAX_VOLUME_RANGE))
        if isinstance(v.get("unity_position"), (int, float)) and not isinstance(v.get("unity_position"), bool):
            o["unity_position"] = int(clamp(round(v["unity_position"]), *UNITY_RANGE))
        if isinstance(v.get("glide"), (int, float)) and not isinstance(v.get("glide"), bool):
            o["glide"] = parse_glide(v["glide"])
        if o:
            result[idx] = o
    return result


def parse_glide(v):
    try:
        return round(clamp(float(v), *GLIDE_RANGE), 2)
    except (TypeError, ValueError):
        return 0.0


def valid_target(t):
    return t in SPECIAL_TARGETS or bool(TAB_RE.match(t)) or bool(EXE_RE.match(t)) or bool(OBS_RE.match(t))


# ---------------------------------------------------------------- 音量操作 (Windows)

class WindowsAudio:
    """Windows の音量 (pycaw)。COM を使うので、作ったスレッド (シリアルのスレッド) からだけ呼ぶ"""

    def __init__(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        self.AudioUtilities = AudioUtilities
        self.sessions = []
        self.last_refresh = 0.0
        self.original = {}      # deej-tab が最初に変える前の音量 {("master",) / ("mic",) / ("app", 名前): 値}
        self._meters = {}       # セッション・デバイスごとの音の大きさを読むインターフェース

    def _endpoint(self, device):
        # pycaw のバージョン差を吸収
        if hasattr(device, "EndpointVolume"):
            return device.EndpointVolume
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(iface, POINTER(IAudioEndpointVolume))

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_refresh < SESSION_REFRESH_MIN:
            return
        self.sessions = self.AudioUtilities.GetAllSessions()
        self.last_refresh = now
        self._meters = {k: v for k, v in self._meters.items() if not isinstance(k, int)}

    def _session_name(self, s):
        try:
            if s.Process is None:
                return "system" if getattr(s, "ProcessId", 0) == 0 else None
            return s.Process.name().lower()
        except Exception:
            return None

    def _remember(self, key, getter):
        if key not in self.original:
            try:
                self.original[key] = float(getter())
            except Exception:
                pass

    def set_master(self, v):
        ep = self._endpoint(self.AudioUtilities.GetSpeakers())
        self._remember(("master",), ep.GetMasterVolumeLevelScalar)
        ep.SetMasterVolumeLevelScalar(v, None)

    def set_mic(self, v):
        ep = self._endpoint(self.AudioUtilities.GetMicrophone())
        self._remember(("mic",), ep.GetMasterVolumeLevelScalar)
        ep.SetMasterVolumeLevelScalar(v, None)

    def get_master(self):
        return float(self._endpoint(self.AudioUtilities.GetSpeakers()).GetMasterVolumeLevelScalar())

    def get_mic(self):
        return float(self._endpoint(self.AudioUtilities.GetMicrophone()).GetMasterVolumeLevelScalar())

    def _matching(self, names=None, mapped_names=None):
        """names に入っている (mapped_names を渡したら、そこに入っていない) セッションと名前"""
        if time.monotonic() - self.last_refresh > SESSION_REFRESH_MAX:
            self.refresh(force=True)
        else:
            self.refresh()
        for s in self.sessions:
            name = self._session_name(s)
            if name is None:
                continue
            if mapped_names is not None:
                if name == "system" or name in mapped_names:
                    continue
            elif name not in names:
                continue
            yield s, name

    def set_processes(self, names, v, mapped_names=None):
        """names: 対象プロセス名の集合。'system' を含めるとシステム音。
        mapped_names が与えられた場合は「そこに含まれない全セッション」(deej.unmapped)"""
        for attempt in range(2):
            try:
                for s, name in self._matching(names, mapped_names):
                    vol = s.SimpleAudioVolume
                    self._remember(("app", name), vol.GetMasterVolume)
                    vol.SetMasterVolume(v, None)
                return
            except Exception:
                # セッションが消えていた等。一覧を取り直して1回だけ再試行
                self.refresh(force=True)

    def get_process(self, name):
        """そのアプリの今の音量 (セッションがなければ None)"""
        for s, _ in self._matching({name}):
            try:
                return float(s.SimpleAudioVolume.GetMasterVolume())
            except Exception:
                return None
        return None

    def has_session(self, name):
        return any(True for _ in self._matching({name}))

    def restore(self):
        """覚えておいた「deej-tab が変える前」の音量に戻して、覚えたものは消す"""
        original, self.original = self.original, {}
        for key, v in original.items():
            try:
                if key == ("master",):
                    self._endpoint(self.AudioUtilities.GetSpeakers()).SetMasterVolumeLevelScalar(v, None)
                elif key == ("mic",):
                    self._endpoint(self.AudioUtilities.GetMicrophone()).SetMasterVolumeLevelScalar(v, None)
                else:
                    for s, _ in self._matching({key[1]}):
                        s.SimpleAudioVolume.SetMasterVolume(v, None)
            except Exception as e:
                log.warning("音量を元に戻せません (%s): %s", key, e)
        if original:
            log.info("音量を元に戻しました (%d か所)", len(original))

    # ---------- 音の大きさ (LED を音に合わせる時) ----------

    def _device_meter(self, key, device):
        if key not in self._meters:
            from comtypes import CLSCTX_ALL
            from ctypes import POINTER, cast
            from pycaw.pycaw import IAudioMeterInformation
            dev = getattr(device, "_dev", device)
            iface = dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            self._meters[key] = cast(iface, POINTER(IAudioMeterInformation))
        return self._meters[key]

    def peak_master(self):
        try:
            return float(self._device_meter("master", self.AudioUtilities.GetSpeakers()).GetPeakValue())
        except Exception:
            return 0.0

    def peak_mic(self):
        try:
            return float(self._device_meter("mic", self.AudioUtilities.GetMicrophone()).GetPeakValue())
        except Exception:
            return 0.0

    def peak(self, names=None, mapped_names=None):
        from pycaw.pycaw import IAudioMeterInformation
        best = 0.0
        try:
            for s, _ in self._matching(names, mapped_names):
                key = id(s)
                if key not in self._meters:
                    self._meters[key] = s._ctl.QueryInterface(IAudioMeterInformation)
                best = max(best, float(self._meters[key].GetPeakValue()))
        except Exception:
            pass
        return best

    def foreground_process_name(self):
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import psutil
        try:
            return psutil.Process(pid.value).name().lower()
        except Exception:
            return None


def list_com_ports():
    from serial.tools import list_ports
    return [{"device": p.device, "description": p.description} for p in list_ports.comports()]


def usb_serial_ports():
    """USB のシリアルポートだけ (Bluetooth のポートは開くと相手につなぎに行って待たされるので除く)"""
    from serial.tools import list_ports
    return sorted(p.device for p in list_ports.comports() if "VID:PID=" in (p.hwid or ""))


def probe_port(device, baud, timeout=PROBE_TIME):
    """ポートを開いて少し読む。ID が来れば {"id", "fw", "name"}、スライダーの値だけなら {"id": None}、
    どちらもなければ (ほかの機器・開けない) None"""
    try:
        port = serial.Serial(device, baudrate=baud, timeout=0.1, write_timeout=0.5)
    except Exception:
        return None
    values, asked = False, False
    start = time.monotonic()
    try:
        while time.monotonic() - start < timeout:
            line = port.readline().decode("ascii", "ignore").strip()
            m = ID_RE.match(line)
            if m:
                return {"id": m.group(3), "fw": m.group(2), "name": m.group(1)}
            if LINE_RE.match(line) and line.count("|") == CHANNELS - 1:
                values = True
            if not asked and time.monotonic() - start > ID_ASK_AFTER:
                asked = True
                try:
                    port.write(b"@I\n")
                except Exception:
                    pass
    except Exception:
        return None
    finally:
        port.close()
    return {"id": None} if values else None


def find_controller(device_id, baud, ports):
    """ports の中からコントローラーを探す。device_id が分かっていれば同じ ID のものだけ、
    分からなければ (ID を送らない古いファームウェア) スライダーの値を送ってくる最初のもの。(ポート, 情報) か None"""
    for dev in ports:
        info = probe_port(dev, baud)
        if info is None:
            continue
        if info["id"] == device_id if device_id else True:
            return dev, info
    return None


class DummyAudio:
    """Windows 以外・テスト用。実際には何もせず記録だけする。音量は current に持つ"""

    def __init__(self):
        self.calls = []
        self.current = {}       # {"master" / "mic" / アプリ名: 音量}。なければ 1.0
        self.running = None     # 起動中のアプリ名の集合 (None なら全部起動中とみなす)
        self.peaks = {}         # {"master" / アプリ名: 音の大きさ}
        self.original = {}
        self.foreground = None

    def _set(self, key, v):
        self.original.setdefault(key, self.current.get(key, 1.0))
        self.current[key] = v

    def set_master(self, v):
        self.calls.append(("master", v))
        self._set("master", v)

    def set_mic(self, v):
        self.calls.append(("mic", v))
        self._set("mic", v)

    def set_processes(self, names, v, mapped_names=None):
        key = "unmapped" if mapped_names is not None else ",".join(sorted(names))
        self.calls.append((key, v))
        for n in (names if mapped_names is None else []):
            self._set(n, v)

    def get_master(self):
        return self.current.get("master", 1.0)

    def get_mic(self):
        return self.current.get("mic", 1.0)

    def get_process(self, name):
        if self.running is not None and name not in self.running:
            return None
        return self.current.get(name, 1.0)

    def has_session(self, name):
        return self.running is None or name in self.running

    def restore(self):
        original, self.original = self.original, {}
        for k, v in original.items():
            self.current[k] = v
        self.calls.append(("restore", dict(original)))

    def peak_master(self):
        return self.peaks.get("master", 0.0)

    def peak_mic(self):
        return self.peaks.get("mic", 0.0)

    def peak(self, names=None, mapped_names=None):
        return max([self.peaks.get(n, 0.0) for n in (names or [])] + [0.0])

    def foreground_process_name(self):
        return self.foreground


# ---------------------------------------------------------------- 本体

class DeejTab:
    def __init__(self, config, audio_factory, catalog=None, system=True):
        """system: OS の仕組み (ホットキー・最前面のアプリの確認) を使うか。テストでは使わない"""
        self.config = config
        self.audio_factory = audio_factory
        self.catalog = catalog       # 設定画面に出すアプリ一覧 (appcatalog.AppCatalog)。None なら出さない
        self.system = system and sys.platform in ("win32", "darwin")
        self.loop = None
        self.clients = set()         # 拡張機能
        self.ui_clients = set()      # 設定画面
        self.tab_values = {}         # {slot: 音量} (1.0 = 100%。上限を上げたスライダーは 1 を超える)
        self.tab_assigned = set()    # 拡張機能でタブを割り当て済みのスロット (LED を暗くするかの判断)
        self.tab_meter = ({}, 0.0)   # 拡張機能からの音の大きさ ({slot: 0〜1}, 受け取った時刻)
        self.values = []             # スライダーごとの最後に適用した位置 (0.0〜1.0, 未受信は -1)
        self.levels = []             # 設定画面に出す今の位置 (反転後・ノイズ除去前)
        self.volumes = []            # 設定画面に出す今の音量 (levels を音量にしたもの)
        self.shown_levels = []       # 設定画面に実際に出す位置 (levels に揺れの抑えをかけたもの。触っていない時にちらつかないように)
        self.reapply = False         # True なら次の受信で全スライダーを適用し直す
        self.raw_values = []         # Nano から届いた生の値 (0〜1023)
        self.calib = None            # 端の位置を測っている間 {"min": [...], "max": [...]} (その間は音量を変えない)
        self.applied = {}            # スライダーごとに実際にかけている音量 (ゆっくりついてくる途中の値)
        self.gliding = {}            # ゆっくりついてくる途中のスライダー {idx: 目標の音量}
        self._glide_t = 0.0
        self.pickup = {}             # ピックアップ待ちのスライダー {idx: 今の音量}
        self.pickup_side = {}        # {idx: スライダーが今の音量より上 (1) か下 (-1) か}
        self.pickup_request = False  # True なら次の受信でピックアップの基準を取る (プロファイルを切り替えた時)
        self.restore_request = threading.Event()   # 音量を元に戻す (シリアルのスレッドで行う)
        self.leds = ""               # 最後に送った LED の状態 (設定画面にも出す)
        self.led_params = dict(leds.PARAMS, **config.led_params)   # 今の光り方の数値 (Nano に送るもの)
        self.led_presets = copy.deepcopy(config.led_presets)      # 光り方ごとに覚えた数値
        self.led_boot = False        # True なら次に Nano へ「起動アニメをもう一度」を送る
        self.led_lock = threading.Lock()
        # 実機の LED の分身 (Nano に送ったものを同じように受け取り、設定画面に光り方を出す)
        self.led_sim = leds.LedSim(led_now(), [512] * CHANNELS, dict(self.led_params))
        self.led_sim.cal = [config.calibration.get(i, leds.CAL_DEFAULT) for i in range(leds.SLIDER_COUNT)]
        self.game_name = None        # deej.game: 最後に全画面だったアプリ
        self.auto = {"profile": None, "exe": None, "pid": 0, "base": None, "suppressed": None}
        self.serial_state = {"connected": False, "port": config.com_port, "error": ""}
        self.device = None           # 今つないでいるコントローラーの {"id", "fw", "name"} (ID を送らなければ None)
        self._searched_ports = None  # 最後にコントローラーを探した時の USB ポートの一覧 (変わった時だけ探し直す)
        self._sent_enabled = config.enabled
        self._sent_lang = resolve_language(config.language)   # 拡張機能に送った言語 (つないだ時にも送る)
        self._sent_meter = None
        self._last_lists = None
        self.config_lock = threading.Lock()
        self.stop = threading.Event()
        self.audio_thread = None
        self.hotkeys = None
        from obs import ObsClient
        self.obs = ObsClient(lambda: self.config.obs, on_change=self._obs_changed)

    # ---------- シリアル ----------

    def serial_thread(self):
        audio = self.audio_factory()
        port = None
        opened_with = None
        next_config_check = 0.0
        last_data = 0.0
        stale = False                # 無通信で繋ぎ直している最中 (ログを繰り返さない)
        last_open = 0.0
        id_asked = False
        self._led_state = self.new_led_state()
        while not self.stop.is_set():
            now = time.monotonic()
            if self.restore_request.is_set():
                self.restore_request.clear()
                self._restore(audio)
            if now >= next_config_check:
                next_config_check = now + CONFIG_POLL
                if self.config.changed_on_disk():
                    with self.config_lock:
                        self.reload_config()
            wanted = (self.config.com_port, self.config.baud_rate)
            if port is not None and opened_with != wanted:
                port.close()
                port = None
            if port is None:
                try:
                    port = serial.serial_for_url(wanted[0], baudrate=wanted[1], timeout=0.1, write_timeout=0.5)
                    opened_with = wanted
                    last_data = last_open = time.monotonic()
                    self.values = []
                    self._led_state.update(sent=None, broken=False, params_sent=None, cal_sent=None)
                    self.device = None
                    self._searched_ports = None   # また消えたら探し直せるように
                    id_asked = False
                    with self.led_lock:
                        self.led_sim.boot(led_now())   # Nano は接続するとリセットされ、起動アニメから始まる
                    if not stale:
                        self.serial_state = {"connected": True, "port": wanted[0], "error": ""}
                        log.info("シリアル接続: %s", wanted[0])
                except Exception as e:
                    err = str(e)
                    if err != self.serial_state["error"]:
                        log.warning("シリアルに接続できません (%s): %s。2秒ごとに再試行", wanted[0], e)
                    self.serial_state = {"connected": False, "port": wanted[0], "error": err}
                    self.levels = []
                    if self.search_controller(wanted):
                        continue
                    self.stop.wait(2)
                    continue
            try:
                raw = port.readline()
            except Exception as e:
                log.warning("シリアル切断: %s", e)
                self.serial_state = {"connected": False, "port": wanted[0], "error": str(e)}
                self.levels = []
                try:
                    port.close()
                except Exception:
                    pass
                port = None
                continue
            if raw:
                last_data = time.monotonic()
                if stale:
                    stale = False
                    self.serial_state = {"connected": True, "port": wanted[0], "error": ""}
                    log.info("シリアルの受信が戻りました: %s", wanted[0])
                line = raw.decode("ascii", "ignore").strip()
                if line.startswith("@ID|"):
                    self.on_device_id(line)
                else:
                    self.handle_line(line, audio)
            elif time.monotonic() - last_data > SERIAL_STALE:
                # スリープ復帰後などに、開いたまま何も届かなくなることがあるので開き直す
                if not stale:
                    stale = True
                    log.warning("シリアルから %d 秒受信がないので繋ぎ直します: %s", SERIAL_STALE, wanted[0])
                    self.serial_state = {"connected": False, "port": wanted[0], "error": "受信なし"}
                    self.levels = []
                try:
                    port.close()
                except Exception:
                    pass
                port = None
                continue
            if self.device is None and not id_asked and time.monotonic() - last_open > ID_ASK_AFTER:
                # 起動時の ID を取りこぼした (開いてもリセットされない時など) ので聞く。古いファームウェアは無視する
                id_asked = True
                try:
                    port.write(b"@I\n")
                except Exception:
                    pass
            self.glide_tick(audio)
            if not self._led_state["broken"]:
                try:
                    self.update_leds(port, audio)
                except Exception as e:
                    # 相手が受け取らない (古い仮スライダーなど) と書き込みが詰まるので、この接続では送らない
                    self._led_state["broken"] = True
                    log.warning("LED の指示を送れないので、この接続では送りません: %s", e)
        if self.restore_request.is_set():
            self._restore(audio)
        if port is not None:
            port.close()

    def on_device_id(self, line):
        """コントローラーから ID が届いた。初めての ID なら覚える (別の USB の口に差した時に探すため)"""
        m = ID_RE.match(line)
        if not m:
            return
        info = {"id": m.group(3), "fw": m.group(2), "name": m.group(1)}
        if info != self.device:
            log.info("コントローラー: %s %s (ID %s)", info["name"], info["fw"], info["id"])
        self.device = info
        if self.config.device_id != info["id"]:
            if self.config.device_id:
                log.info("別のコントローラーにつながりました (前の ID %s)", self.config.device_id)
            with self.config_lock:
                self.config.save(device_id=info["id"])
            self.after_config_change()

    def search_controller(self, wanted):
        """com_port が消えた時、ほかの USB のポートからコントローラーを探して com_port を書き換える。
        USB のポートの顔ぶれが変わった時だけ探す (ほかの Arduino などを何度も開いてリセットしないように)。見つけたら True"""
        name, baud = wanted
        if "://" in name:
            return False   # 仮スライダー
        try:
            ports = usb_serial_ports()
        except Exception:
            return False
        if name in ports or ports == self._searched_ports:
            return False   # ポートはある (ほかのアプリが使っている) か、前に探した時と同じ
        self._searched_ports = ports
        if not ports:
            return False
        log.info("%s が見つからないので、コントローラーを探します: %s", name, ", ".join(ports))
        found = find_controller(self.config.device_id, baud, ports)
        if not found:
            log.info("コントローラーは見つかりませんでした")
            return False
        dev, info = found
        with self.config_lock:
            self.config.save(com_port=dev)
        log.info("コントローラーが %s に移ったので、つなぎ直します", dev)
        self.broadcast_ui({"type": "notice", "message": f"コントローラーが {dev} に移ったので、つなぎ直しました"})
        self.after_config_change()
        return True

    def _restore(self, audio):
        try:
            audio.restore()
        except Exception as e:
            log.warning("音量を元に戻せません: %s", e)

    def handle_line(self, line, audio):
        if not LINE_RE.match(line):
            return
        parts = [int(x) for x in line.split("|")]
        if parts[0] > 1023:
            return
        with self.led_lock:
            self.led_sim.set_raw(parts[:leds.LED_COUNT], led_now())
        parts = [min(n, 1023) for n in parts]
        self.raw_values = parts
        cal = self.calib
        if cal is not None:
            if len(cal["min"]) != len(parts):
                cal["min"], cal["max"] = list(parts), list(parts)
            cal["min"] = [min(a, n) for a, n in zip(cal["min"], parts)]
            cal["max"] = [max(a, n) for a, n in zip(cal["max"], parts)]
        if self.reapply:
            self.reapply = False
            self.values = []
        if len(self.values) != len(parts):
            self.values = [-1.0] * len(parts)
        c = self.config
        threshold = NOISE_THRESHOLDS[c.noise]
        levels = []
        for idx, n in enumerate(parts):
            p = int(c.calibrated(idx, n) * 100) / 100.0
            if c.inverted(idx):
                p = round(1.0 - p, 2)
            levels.append(p)
        self.levels = levels
        self.volumes = [c.volume(idx, p) for idx, p in enumerate(levels)]
        shown = self.shown_levels
        if len(shown) != len(levels):
            shown = list(levels)
        self.shown_levels = [q if not significantly_different(q, p, threshold) else p for q, p in zip(shown, levels)]
        if self.pickup_request:
            self.pickup_request = False
            self.start_pickup(audio)
        if not c.enabled or cal is not None:
            return   # 一時停止中・端の位置を測っている間は表示だけ (再開した時に全部適用し直す)
        for idx, p in enumerate(levels):
            if not significantly_different(self.values[idx], p, threshold):
                continue
            if idx in self.pickup and not self.picked_up(idx, self.volumes[idx]):
                continue
            self.values[idx] = p
            self.set_target(idx, self.volumes[idx], audio)

    # ---------- 音量がゆっくりついてくる ----------

    def set_target(self, idx, v, audio):
        """スライダーの音量を目標にする。glide が 0 か、まだ一度もかけていなければすぐかける"""
        cur = self.applied.get(idx)
        if self.config.glide(idx) <= 0 or cur is None:
            self.gliding.pop(idx, None)
            self.applied[idx] = v
            self.apply(idx, v, audio)
            return
        if not self.gliding:
            self._glide_t = time.monotonic()
        self.gliding[idx] = v

    def glide_tick(self, audio):
        """目標に向かって少しずつ音量を変える (最初は速く、近づくほどゆっくり)"""
        if not self.gliding or not self.config.enabled:
            return
        now = time.monotonic()
        dt = now - self._glide_t
        if dt < GLIDE_INTERVAL:
            return
        self._glide_t = now
        for idx, target in list(self.gliding.items()):
            tau = max(self.config.glide(idx) / 3.0, 0.01)   # 3τ で 95%
            cur = self.applied.get(idx, target)
            v = cur + (target - cur) * (1.0 - math.exp(-dt / tau))
            if abs(target - v) <= GLIDE_DONE:
                v = target
                del self.gliding[idx]
            v = round(v, 3)
            self.applied[idx] = v
            self.apply(idx, v, audio)

    # ---------- ピックアップ ----------

    def current_volume(self, target, audio):
        """割り当て先の今の音量 (分からなければ None)。ピックアップの基準に使う"""
        m = TAB_RE.match(target)
        try:
            if m:
                return self.tab_values.get(int(m.group(1)))
            if target == "master":
                return audio.get_master()
            if target == "mic":
                return audio.get_mic()
            om = OBS_RE.match(target)
            if om:
                real = self.obs.find(om.group(1))
                return mul_to_volume(self.obs.inputs[real]) if real else None
            if target in SPECIAL_TARGETS:
                return None
            return audio.get_process(target)
        except Exception:
            return None

    def start_pickup(self, audio):
        """プロファイルを切り替えた直後: 各スライダーの割り当て先の今の音量を覚え、
        スライダーがそこを通るまでは音量を変えない (切り替えた瞬間に音量が飛ばないように)"""
        self.pickup, self.pickup_side = {}, {}
        # 割り当て先が変わったので、ゆっくりついてくる時の出発点は今の音量 (分からなければすぐかける)
        self.applied, self.gliding = {}, {}
        for idx, targets in self.config.mapping.items():
            for t in targets:
                ref = self.current_volume(t, audio)
                if ref is not None:
                    self.applied[idx] = ref
                    if self.config.pickup:
                        self.pickup[idx] = ref
                    break
        # すでに今の位置で合っているものは待たない
        for idx in list(self.pickup):
            if idx < len(self.volumes):
                self.picked_up(idx, self.volumes[idx])

    def picked_up(self, idx, v):
        """ピックアップ待ちが終わったら True (待ちも消す)"""
        ref = self.pickup[idx]
        side = (v > ref) - (v < ref)
        prev = self.pickup_side.get(idx)
        if abs(v - ref) <= PICKUP_TOL or (prev is not None and side != prev):
            del self.pickup[idx]
            self.pickup_side.pop(idx, None)
            log.info("スライダー %d が今の音量 (%d%%) に合いました", idx + 1, round(ref * 100))
            return True
        self.pickup_side[idx] = side
        return False

    # ---------- 音量を変える ----------

    def apply(self, idx, v, audio):
        """v: 音量 (1.0 = 100%)。OS の音量は 100% までなので、超える分は Chrome タブ・OBS にだけ効く"""
        targets = self.config.mapping.get(idx, [])
        wv = min(v, 1.0)
        names = set()
        for t in targets:
            m = TAB_RE.match(t)
            try:
                if m:
                    self.send_tab(int(m.group(1)), v)
                elif t == "master":
                    audio.set_master(wv)
                elif t == "mic":
                    audio.set_mic(wv)
                elif t == "deej.current":
                    self.apply_app(audio.foreground_process_name(), v, names)
                elif t == "deej.game":
                    self.apply_app(self.game_name, v, names)
                elif t == "deej.unmapped":
                    audio.set_processes(set(), wv, mapped_names=self.config.mapped_process_names())
                elif OBS_RE.match(t):
                    self.obs.set_volume(OBS_RE.match(t).group(1), v)
                else:
                    names.add(t)
            except Exception as e:
                log.warning("音量変更に失敗 (%s): %s", t, e)
        if names:
            try:
                audio.set_processes(names, wv)
            except Exception as e:
                log.warning("音量変更に失敗 (%s): %s", names, e)

    def apply_app(self, name, v, names):
        """deej.current / deej.game: そのアプリの音量を変える。Chrome なら表示中のタブだけ"""
        if name in CHROME_APPS and self.config.tab_slots():
            # Chrome 全体ではなく、表示中のタブだけを拡張機能に変えさせる
            self.broadcast({"type": "current", "value": v})
        elif name and not self.is_reserved_process(name):
            names.add(name)

    def is_reserved_process(self, name):
        """deej.current で触らないアプリ: 他のスライダーに割り当て済みのアプリと、
        tab.N を使っている時の Chrome (Chrome 全体を変えるとタブ用スライダーの上限まで変わるため)"""
        name = name.lower()
        if name in self.config.mapped_process_names():
            return True
        return name in CHROME_APPS and bool(self.config.tab_slots())

    # ---------- LED ----------

    def target_available(self, t, audio):
        m = TAB_RE.match(t)
        if m:
            return int(m.group(1)) in self.tab_assigned
        om = OBS_RE.match(t)
        if om:
            return self.obs.connected and self.obs.find(om.group(1)) is not None
        if t == "deej.game":
            return self.game_name is not None
        if t in SPECIAL_TARGETS:
            return True
        return audio.has_session(t)

    def led_modes(self, audio):
        modes = []
        for idx in range(leds.LED_COUNT):
            targets = self.config.mapping.get(idx, [])
            v = self.volumes[idx] if idx < len(self.volumes) else None
            if idx in self.pickup:
                modes.append("P")
            elif not targets or not any(self.target_available(t, audio) for t in targets):
                modes.append("D")
            elif v is not None and v <= 0:
                modes.append("O")
            elif v is not None and v > 1.0:
                modes.append("B")
            else:
                modes.append("N")
        return modes

    def target_peak(self, t, audio):
        m = TAB_RE.match(t)
        if m:
            levels, at = self.tab_meter
            return levels.get(int(m.group(1)), 0.0) if time.monotonic() - at < TAB_METER_STALE else 0.0
        if t == "master":
            return audio.peak_master()
        if t == "mic":
            return audio.peak_mic()
        if t == "deej.unmapped":
            return audio.peak(mapped_names=self.config.mapped_process_names())
        if t == "deej.current":
            name = audio.foreground_process_name()
            return audio.peak({name}) if name else 0.0
        if t == "deej.game":
            return audio.peak({self.game_name}) if self.game_name else 0.0
        if OBS_RE.match(t):
            return 0.0
        return audio.peak({t})

    def meter_levels(self, audio):
        levels = []
        for idx in range(leds.LED_COUNT):
            peak = max([self.target_peak(t, audio) for t in self.config.mapping.get(idx, [])] + [0.0])
            if peak <= 0:
                levels.append(0)
                continue
            db = 20 * math.log10(max(peak, 1e-6))
            levels.append(int(clamp((db - METER_FLOOR_DB) / -METER_FLOOR_DB, 0.0, 1.0) * 255))
        return levels

    def update_leds(self, port, audio):
        """LED の指示を Nano に送る (状態が変わった時と、1 秒ごと)。音に合わせる時は音の大きさも送る"""
        c = self.config
        st = self._led_state
        now = time.monotonic()
        meter_on = c.led_mode == "meter" and c.enabled
        if meter_on != self._sent_meter:
            self._sent_meter = meter_on
            self.broadcast({"type": "meter", "on": meter_on})
        # 光り方の数値は LED を PC から光らせない (off) 時も送る (Nano 単独の光り方も変わる)
        if now >= st["next"]:
            lines = leds.params_lines(self.led_params)
            if lines != st["params_sent"] or now - st["params_at"] >= LED_PARAMS_RESEND:
                for line in lines:
                    self._led_write(port, line)
                st["params_sent"], st["params_at"] = lines, now
            # 端の位置も同じように送る (Nano は EEPROM に保存し、LED の判断に使う)
            cal_lines = leds.calibration_lines(c.calibration)
            if cal_lines != st["cal_sent"] or now - st["cal_at"] >= LED_PARAMS_RESEND:
                for line in cal_lines:
                    self._led_write(port, line)
                st["cal_sent"], st["cal_at"] = cal_lines, now
            if self.led_boot:
                self.led_boot = False
                self._led_write(port, leds.BOOT_LINE)
        if c.led_mode == "off":
            self.leds = ""
            st["next"] = max(st["next"], now + LED_INTERVAL)
            return
        if now >= st["next"]:
            st["next"] = now + LED_INTERVAL
            modes = self.led_modes(audio)
            line = leds.status_line(not c.enabled, modes)
            self.leds = ("Z" * leds.LED_COUNT) if not c.enabled else "".join(modes)
            if line != st["sent"] or now - st["at"] >= LED_HEARTBEAT:
                self._led_write(port, line)
                st["sent"], st["at"] = line, now
        if meter_on and now >= st["meter_next"]:
            st["meter_next"] = now + METER_INTERVAL
            self._led_write(port, leds.meter_line(self.meter_levels(audio)))

    @staticmethod
    def new_led_state():
        """Nano に送った LED の行の記録 (接続ごと)"""
        return {"sent": None, "at": 0.0, "next": 0.0, "meter_next": 0.0, "broken": False,
                "params_sent": None, "params_at": 0.0, "cal_sent": None, "cal_at": 0.0}

    def _led_write(self, port, line):
        """Nano に LED の行を送り、分身にも同じ行を渡す"""
        port.write(line.encode("ascii"))
        with self.led_lock:
            self.led_sim.receive(line, led_now())

    def set_led_params(self, params, save=True, remember=True):
        """光り方の数値を変える (設定画面から)。save=False は動かしている途中 (Nano と分身にだけ反映)。
        光り方を切り替えた時は、その光り方で前に使っていた明るさなどに戻す。
        remember=False はそれをせずにそのまま置き換える (config.yaml を直接書き換えた時・既定に戻す時)"""
        new = leds.normalize_params(params)
        if remember:
            new = leds.apply_change(self.led_params, self.led_presets, new)
        else:
            new = {k: v for k, v in new.items() if self.led_params.get(k) != v}
            self.led_params.update(new)
        with self.led_lock:
            self.led_sim.p.update(new)
            if "BOOT_STYLE" in new:
                self.led_sim.boot(led_now())   # 選んだらすぐ見せる
        if "BOOT_STYLE" in new:
            self.led_boot = True
        if save:
            diff = {k: v for k, v in self.led_params.items() if v != leds.PARAMS[k]}
            with self.config_lock:
                self.config.save(led_params=diff, led_presets=copy.deepcopy(self.led_presets))
            log.info("LED の光り方を変更: %s", diff or "既定")
            self.after_config_change()

    # ---------- 設定の変更 ----------

    def reload_config(self):
        try:
            self.config.load()
            log.info("config.yaml を再読み込みしました")
        except Exception as e:
            log.error("config.yaml の読み込みに失敗: %s", e)
            return
        self.values = []  # 次の受信で全スライダーを適用し直す
        self.applied, self.gliding = {}, {}
        self.after_config_change()

    def after_config_change(self):
        slots = self.config.tab_slots()
        self.tab_values = {s: v for s, v in self.tab_values.items() if s in slots}
        self.broadcast({"type": "slots", "slots": slots})
        if self.config.enabled != self._sent_enabled:
            self._sent_enabled = self.config.enabled
            self.broadcast({"type": "enabled", "value": self.config.enabled})
        lang = resolve_language(self.config.language)
        if lang != self._sent_lang:   # 拡張機能の文言も同じ言語にする
            self._sent_lang = lang
            self.broadcast({"type": "lang", "value": lang})
        if self.config.led_presets != self.led_presets:
            self.led_presets = copy.deepcopy(self.config.led_presets)
        want = dict(leds.PARAMS, **self.config.led_params)
        if want != self.led_params:   # config.yaml を直接書き換えた時など
            self.set_led_params(want, save=False, remember=False)
        self.update_hotkeys()
        self.broadcast_ui(self.ui_config())

    def set_enabled(self, enabled):
        """スライダー操作の有効/一時停止 (設定画面・トレイ・ホットキーから)。
        一時停止中は Chrome のタブが元の音量 (100%) に戻る。restore_on_pause なら Windows・OBS の音量も戻す"""
        with self.config_lock:
            if self.config.enabled == enabled:
                return
            self.config.save(enabled=enabled)
        log.info("スライダー操作: %s", "有効" if enabled else "一時停止")
        if not enabled and self.config.restore_on_pause:
            self.restore_request.set()
            self.obs.restore()
            self.applied = {}   # 元に戻したので、再開したらすぐかける
        self.gliding = {}
        self.pickup, self.pickup_side = {}, {}
        self.reapply = True
        self.after_config_change()

    def end_calibration(self):
        """端の位置を測るのを終える。次の受信で全スライダーを今の位置で適用し直す"""
        self.calib = None
        self.reapply = True

    def save_calibration(self):
        """測った範囲から端の位置を決めて保存し、測るのを終える。
        十分に動かしたスライダーだけ変える (動かしていないスライダーは前のまま)。
        端まで届いていて合わせる必要がないスライダーは full (前に合わせていれば既定に戻すので saved)。
        返り値: {"saved": [番号...], "full": [番号...], "skipped": [番号...]}"""
        cal = self.calib
        if cal is None:
            raise ValueError("端の位置を測っていません")
        result = dict(self.config.calibration)
        saved, full, skipped = [], [], []
        for i, (mn, mx) in enumerate(zip(cal["min"], cal["max"])):
            if mx - mn < leds.CAL_MIN_SPAN:
                skipped.append(i)
                continue
            lo = 0 if mn <= CAL_MARGIN else mn + CAL_MARGIN
            hi = 1023 if mx >= 1023 - CAL_MARGIN else mx - CAL_MARGIN
            if not leds.valid_calibration(lo, hi):
                skipped.append(i)
                continue
            if (lo, hi) == leds.CAL_DEFAULT and i not in result:
                full.append(i)
                continue
            result[i] = (lo, hi)
            saved.append(i)
        if saved:
            with self.config_lock:
                self.config.save(slider_calibration=result)
            log.info("スライダーの端の位置を保存: %s",
                     ", ".join(f"{i + 1}: {result[i][0]}〜{result[i][1]}" for i in saved))
        self.end_calibration()
        if saved:
            self.after_config_change()
        if full:
            log.info("端まで届いているので合わせなかったスライダー: %s", ", ".join(str(i + 1) for i in full))
        return {"saved": saved, "full": full, "skipped": skipped}

    def save_sliders(self, sliders, **options):
        with self.config_lock:
            self.config.save(sliders=sliders, **options)
        self.reapply = True
        self.after_config_change()

    def switch_profile(self, name, auto=False):
        """プロファイルを切り替える。auto: 最前面のアプリで自動で切り替えた時"""
        with self.config_lock:
            if not self.config.switch_profile(name):
                return
        if not auto:
            # 手で切り替えたら自動の切り替えは解除。今前にある自動のアプリでは、いったん切り替えない
            fg = self.auto.get("fg")
            self.auto.update(profile=None, exe=None, pid=0, base=None,
                             suppressed=fg if fg and self.config.auto_profile_for(fg) else None)
        log.info("プロファイルを切り替え: %s%s", name, " (自動)" if auto else "")
        self.values = []
        self.pickup_request = True
        self.after_config_change()

    def shutdown(self):
        """終了: restore_on_pause なら音量を元に戻し、タブも元の音量に戻させる"""
        if self.config.restore_on_pause:
            self.restore_request.set()
            self.obs.restore()
            self.broadcast({"type": "enabled", "value": False})
        self.stop.set()
        if self.hotkeys:
            self.hotkeys.stop()
        if self.audio_thread:
            self.audio_thread.join(3)
        time.sleep(0.3)   # 拡張機能・OBS へ送り終わるのを待つ

    # ---------- ホットキー・最前面のアプリ ----------

    def hotkey_bindings(self):
        b = {"pause": self.config.hotkeys.get("pause", "")}
        for name, p in self.config.profiles.items():
            if p.get("hotkey"):
                b["profile:" + name] = p["hotkey"]
        return b

    def update_hotkeys(self):
        if self.hotkeys is not None:
            b = self.hotkey_bindings()
            if b != getattr(self, "_bindings", None):
                self._bindings = b
                self.hotkeys.set_bindings(b)

    def on_hotkey(self, name):
        if name == "pause":
            self.set_enabled(not self.config.enabled)
        elif name.startswith("profile:"):
            self.switch_profile(name[len("profile:"):])

    def watch_thread(self):
        """最前面のアプリを見て、deej.game を覚え、プロファイルを自動で切り替える"""
        winsys = sysmod()
        winsys.use_physical_pixels()
        tracker = winsys.GameTracker()
        while not self.stop.wait(WATCH_INTERVAL):
            try:
                name, pid, full = winsys.foreground()
                self.game_name = tracker.update(name, pid, full)
                self.auto_profile_tick(name, pid)
            except Exception as e:
                log.warning("最前面のアプリの確認に失敗: %s", e)

    def auto_profile_tick(self, fg, pid, alive=None):
        """プロファイルの自動切り替え。
        auto_apps のアプリが前に来たらそのプロファイルに切り替え、そのアプリが終了したら元のプロファイルに戻す"""
        alive = alive or sysmod().pid_alive
        a = self.auto
        a["fg"] = fg
        if a["suppressed"] and fg != a["suppressed"]:
            a["suppressed"] = None
        target = self.config.auto_profile_for(fg) if fg else None
        if target and fg != a["suppressed"] and target != self.config.active_profile:
            base = a["base"] if a["profile"] else self.config.active_profile
            self.switch_profile(target, auto=True)
            a.update(profile=target, exe=fg, pid=pid, base=base)
        elif target and a["profile"] == target:
            a.update(exe=fg, pid=pid)
        elif a["profile"] and not alive(a["pid"]):
            base = a["base"]
            a.update(profile=None, exe=None, pid=0, base=None)
            if base in self.config.profiles and base != self.config.active_profile:
                self.switch_profile(base, auto=True)

    # ---------- OBS ----------

    def _obs_changed(self):
        # ソース一覧が変わったら、設定画面の一覧を送り直す
        if self.loop is not None:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.send_lists()))

    # ---------- WebSocket ----------

    def send_tab(self, slot, v):
        self.tab_values[slot] = v
        self.broadcast({"type": "volume", "slot": slot, "value": v})

    def broadcast(self, msg):
        if self.loop is None:
            return
        data = json.dumps(msg)
        self.loop.call_soon_threadsafe(self._broadcast, data)

    def _broadcast(self, data):
        for ws in list(self.clients):
            asyncio.ensure_future(self._safe_send(ws, data))

    async def _safe_send(self, ws, data):
        try:
            await ws.send(data)
        except Exception:
            self.clients.discard(ws)

    # ---------- 設定画面 ----------

    def broadcast_ui(self, msg):
        if self.loop is None:
            return
        data = json.dumps(msg)
        self.loop.call_soon_threadsafe(self._broadcast_ui, data)

    def _broadcast_ui(self, data):
        for ws in list(self.ui_clients):
            asyncio.ensure_future(self._safe_send_ui(ws, data))

    async def _safe_send_ui(self, ws, data):
        try:
            await ws.send(data)
        except Exception:
            self.ui_clients.discard(ws)

    def ui_config(self):
        c = self.config
        sliders = {}
        for i in range(CHANNELS):
            o = c.sliders.get(i, {})
            sliders[str(i)] = {
                "invert": c.inverted(i),
                "invert_own": "invert" in o,              # 全体の反転ではなく、このスライダーで指定している
                "max_volume": o.get("max_volume", 100),
                "unity_position": o.get("unity_position"),  # None = 比例 (自動)
                "glide": o.get("glide"),                    # None = 全体の設定
            }
        return {
            "type": "config",
            "enabled": c.enabled,
            "mapping": {str(k): v for k, v in c.mapping.items()},
            "sliders": sliders,
            "options": {"com_port": c.com_port, "invert_sliders": c.invert,
                        "noise_reduction": c.noise, "restore_on_pause": c.restore_on_pause,
                        "pickup": c.pickup, "led_mode": c.led_mode, "glide": c.glide_default,
                        "language": c.language},
            "lang": resolve_language(c.language),   # 実際に使う言語 (auto を解いたもの)
            "led_params": dict(self.led_params),
            "led_meta": {"defaults": leds.PARAMS, "limits": leds.PARAM_LIMITS, "idle_styles": leds.IDLE_STYLES,
                         "boot_styles": leds.BOOT_STYLES, "touch_styles": leds.TOUCH_STYLES},
            "calibration": {str(i): list(v) for i, v in c.calibration.items()},
            "cal_meta": {"margin": CAL_MARGIN, "min_span": leds.CAL_MIN_SPAN,
                         "lo_max": leds.CAL_LO_MAX, "hi_min": leds.CAL_HI_MIN},
            "hotkeys": dict(c.hotkeys),
            "obs": {k: v for k, v in c.obs.items() if k != "password"} | {"has_password": bool(c.obs["password"])},
            "profiles": [{"name": n, **p} for n, p in c.profiles.items()],
            "active_profile": c.active_profile,
            "autostart": autostart_enabled(),
            "channels": CHANNELS,
            "system": self.system,
            "platform": "mac" if IS_MAC else "win",
        }

    def ui_status(self):
        return {
            "type": "status", "serial": self.serial_state, "device": self.device, "extension": len(self.clients),
            "obs": self.obs.status(), "game": self.game_name, "auto_profile": self.auto["profile"],
            "hotkey_errors": dict(self.hotkeys.errors) if self.hotkeys else {},
        }

    def ui_levels(self):
        pickup = {str(i): round(self.config.position(i, ref), 3) for i, ref in self.pickup.items()}
        with self.led_lock:
            led_levels = list(self.led_sim.advance(led_now()))
        # 表示は揺れの抑えをかけた位置で出す (音量・ピックアップの判定は生の levels のまま)
        shown = list(self.shown_levels) if len(self.shown_levels) == len(self.levels) else list(self.levels)
        volumes = [self.config.volume(i, p) for i, p in enumerate(shown)]
        out = {"type": "levels", "values": shown, "volumes": volumes,
               "pickup": pickup, "leds": self.leds, "led_levels": led_levels,
               "applied": [self.applied.get(i, v) for i, v in enumerate(volumes)]}
        cal = self.calib
        if cal is not None:
            out["calib"] = {"raw": list(self.raw_values), "min": list(cal["min"]), "max": list(cal["max"])}
        return out

    async def ui_lists(self):
        async def apps():
            if self.catalog is None:
                return []
            try:
                return await asyncio.to_thread(self.catalog.list)
            except Exception as e:
                log.warning("アプリ一覧の取得に失敗: %s", e)
                return []
        apps, ports = await asyncio.gather(apps(), asyncio.to_thread(list_com_ports))
        return {"type": "lists", "apps": apps, "ports": ports, "obs_inputs": sorted(self.obs.inputs)}

    async def send_lists(self, ws=None):
        """アプリ・COM ポートの一覧を送る。ws を省くと、前回と変わっていた時だけ全画面に送る"""
        data = json.dumps(await self.ui_lists())
        if ws is not None:
            await self._safe_send_ui(ws, data)
        elif data != self._last_lists:
            self._broadcast_ui(data)
        self._last_lists = data

    async def ui_handler(self, ws):
        # 設定画面は同じオリジンからだけ受け付ける (他のサイトから設定を書き換えられないように)
        origin = ws.request.headers.get("Origin", "")
        port = self.config.ws_port
        if origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            await ws.close(1008, "forbidden origin")
            return
        self.ui_clients.add(ws)
        try:
            await ws.send(json.dumps(self.ui_config()))
            await ws.send(json.dumps(self.ui_status()))
            # 値は変わった時だけ送るので、今の値をここで送る (スライダーが止まっていてもメーターが出るように)
            await ws.send(json.dumps(self.ui_levels()))
            # 一覧は初回に少し時間がかかる (スタートメニューを読む) ので、待たずに操作を受け付ける
            asyncio.ensure_future(self.send_lists(ws))
            async for raw in ws:
                try:
                    await self.ui_command(ws, json.loads(raw))
                except Exception as e:
                    log.warning("設定画面からの操作に失敗: %s", e)
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
        finally:
            self.ui_clients.discard(ws)
            # 端の位置を測っている途中で画面を閉じたら、測るのをやめる (音量が変わらないままにならないように)
            if self.calib is not None and self.calib.get("ws") is ws:
                self.end_calibration()
                log.info("設定画面が閉じたので、スライダーの端の位置を測るのをやめました")

    async def ui_command(self, ws, msg):
        t = msg.get("type")
        if t == "set_targets":
            idx = int(msg["index"])
            if not 0 <= idx < CHANNELS:
                raise ValueError(f"スライダーの番号が範囲外です: {idx}")
            targets = []
            for x in msg["targets"]:
                x = str(x).strip().lower()
                if not x or x in targets:
                    continue
                if not valid_target(x):
                    raise ValueError(f"割り当て先として使えない名前です: {x}")
                targets.append(x)
            with self.config_lock:
                mapping = {k: list(v) for k, v in self.config.mapping.items()}
                # 同じ割り当て先は1つのスライダーにだけ置く (他のスライダーからは外す)
                for k in mapping:
                    if k != idx:
                        mapping[k] = [x for x in mapping[k] if x not in targets]
                mapping[idx] = targets
                self.config.save(mapping=mapping)
            log.info("割り当てを変更: A%d → %s", idx, ", ".join(targets) or "(なし)")
            self.pickup.pop(idx, None)
            self.applied.pop(idx, None)
            self.gliding.pop(idx, None)
            self.reapply = True
            self.after_config_change()
        elif t == "set_slider":
            self.set_slider(int(msg["index"]), msg)
        elif t == "set_enabled":
            self.set_enabled(bool(msg["value"]))
        elif t == "calib_start":
            self.calib = {"min": list(self.raw_values), "max": list(self.raw_values), "ws": ws}
            self.gliding = {}
            log.info("スライダーの端の位置を測り始めました (その間は音量を変えません)")
        elif t == "calib_cancel":
            self.end_calibration()
            log.info("スライダーの端の位置を測るのをやめました")
        elif t == "calib_save":
            await self._safe_send_ui(ws, json.dumps({"type": "calib_result", **self.save_calibration()}))
        elif t == "calib_reset":
            idx = msg.get("index")
            cal = dict(self.config.calibration)
            if idx is None:
                cal = {}
            else:
                cal.pop(int(idx), None)
            with self.config_lock:
                self.config.save(slider_calibration=cal)
            log.info("スライダーの端の位置を既定に戻しました: %s", "全部" if idx is None else int(idx) + 1)
            self.reapply = True
            self.after_config_change()
        elif t == "remember_app":
            if self.catalog is None:
                raise ValueError("アプリ一覧が使えません")
            exe = await asyncio.to_thread(self.catalog.remember, str(msg["path"]))
            log.info("アプリを登録: %s", exe)
            await self.send_lists()
        elif t == "set_option" and msg.get("key") == "invert_sliders":
            # 一括反転: 全体の反転を切り替えて、スライダーごとの反転の指定は消す (二重に反転させない)
            value = bool(msg["value"])
            sliders = {k: {kk: vv for kk, vv in v.items() if kk != "invert"}
                       for k, v in self.config.sliders.items()}
            self.save_sliders(sliders, invert_sliders=value)
            log.info("設定を変更: 全スライダーの反転 = %s", value)
        elif t == "set_option":
            key, value = msg["key"], msg["value"]
            if key == "noise_reduction":
                if value not in NOISE_THRESHOLDS:
                    raise ValueError(f"noise_reduction: {value}")
            elif key == "com_port":
                value = str(value).strip()
                if not value:
                    raise ValueError("接続先が空です")
            elif key in ("restore_on_pause", "pickup"):
                value = bool(value)
            elif key == "glide":
                value = parse_glide(value)
            elif key == "led_mode":
                if value not in LED_MODES:
                    raise ValueError(f"led_mode: {value}")
            elif key == "language":
                if value not in LANGUAGES:
                    raise ValueError(f"language: {value}")
            else:
                raise ValueError(f"変更できない設定です: {key}")
            with self.config_lock:
                self.config.save(**{key: value})
            log.info("設定を変更: %s = %s", key, value)
            if key in ("com_port", "noise_reduction"):
                self.reapply = True
            self.after_config_change()
        elif t == "set_led_params":
            if not isinstance(msg.get("params"), dict):
                raise ValueError("params がありません")
            self.set_led_params(msg["params"], save=bool(msg.get("save", True)))
        elif t == "reset_led_params":
            self.led_presets = {}
            self.set_led_params(leds.PARAMS, remember=False)
        elif t == "led_boot":
            with self.led_lock:
                self.led_sim.boot(led_now())
            self.led_boot = True
        elif t == "set_hotkey":
            spec = sysmod().normalize_hotkey(str(msg.get("value") or ""))
            name = msg.get("name")
            if name == "pause":
                with self.config_lock:
                    self.config.save(hotkeys={**self.config.hotkeys, "pause": spec})
            elif isinstance(name, str) and name.startswith("profile:"):
                with self.config_lock:
                    self.config.update_profile(name[len("profile:"):], hotkey=spec)
            else:
                raise ValueError(f"ショートカットの名前が違います: {name}")
            self.check_hotkey_conflicts()
            log.info("ショートカット: %s = %s", name, spec or "(なし)")
            self.after_config_change()
        elif t == "set_obs":
            obs = dict(self.config.obs)
            if "enabled" in msg:
                obs["enabled"] = bool(msg["enabled"])
            if "host" in msg:
                obs["host"] = str(msg["host"]).strip() or "127.0.0.1"
            if "port" in msg:
                port = int(msg["port"])
                if not 1 <= port <= 65535:
                    raise ValueError("ポート番号は 1〜65535 です")
                obs["port"] = port
            if "password" in msg:
                obs["password"] = str(msg["password"])
            with self.config_lock:
                self.config.save(obs=obs)
            log.info("OBS 連携: %s (%s:%s)", "オン" if obs["enabled"] else "オフ", obs["host"], obs["port"])
            self.obs.reconnect()
            self.after_config_change()
        elif t == "profile_switch":
            self.switch_profile(str(msg["name"]))
        elif t in ("profile_add", "profile_rename", "profile_delete", "profile_update"):
            with self.config_lock:
                if t == "profile_add":
                    self.config.add_profile(str(msg["name"]), copy=bool(msg.get("copy", True)))
                elif t == "profile_rename":
                    self.config.rename_profile(str(msg["name"]), str(msg["new"]))
                elif t == "profile_delete":
                    self.config.delete_profile(str(msg["name"]))
                else:
                    self.config.update_profile(str(msg["name"]), auto_apps=msg.get("auto_apps"))
            log.info("プロファイル: %s %s", t, msg.get("name"))
            self.after_config_change()
        elif t == "set_autostart":
            set_autostart(bool(msg["value"]))
            self.broadcast_ui(self.ui_config())
        elif t == "refresh_lists":
            await self.send_lists(ws)
        elif t == "fit_window":
            if sys.platform == "win32":   # Chrome で開いた設定画面 (Mac は自前のウィンドウだけ)
                await asyncio.to_thread(fit_settings_window, msg)
        else:
            raise ValueError(f"不明な操作です: {t}")

    def check_hotkey_conflicts(self):
        seen = {}
        for name, spec in self.hotkey_bindings().items():
            if spec in seen:
                raise ValueError(f"同じショートカット ({spec}) が 2 か所に設定されています")
            seen[spec] = name

    def set_slider(self, idx, msg):
        """スライダーごとの設定 (反転・上限・100% の位置)。msg に入っている項目だけ変える"""
        if not 0 <= idx < CHANNELS:
            raise ValueError(f"スライダーの番号が範囲外です: {idx}")
        c = self.config
        o = dict(c.sliders.get(idx, {}))
        if "invert" in msg:
            inv = bool(msg["invert"])
            # 全体の反転と同じなら、個別の指定は持たない (全体を切り替えた時に一緒に変わるように)
            if inv == c.invert:
                o.pop("invert", None)
            else:
                o["invert"] = inv
        if "max_volume" in msg:
            mv = int(clamp(round(float(msg["max_volume"])), *MAX_VOLUME_RANGE))
            if mv == 100:
                o.pop("max_volume", None)
            else:
                o["max_volume"] = mv
        if "glide" in msg:
            if msg["glide"] is None:
                o.pop("glide", None)       # 全体の設定に合わせる
            else:
                o["glide"] = parse_glide(msg["glide"])
        if "unity_position" in msg:
            u = msg["unity_position"]
            if u is None:
                o.pop("unity_position", None)
            else:
                o["unity_position"] = int(clamp(round(float(u)), *UNITY_RANGE))
        sliders = {k: dict(v) for k, v in c.sliders.items()}
        sliders[idx] = o
        self.save_sliders(sliders)
        log.info("スライダー %d の設定: %s", idx + 1, o or "既定")

    async def ui_pump(self):
        """設定画面にスライダー値と接続状態を送り続ける"""
        last_levels = None
        last_status = None
        last_lists = time.monotonic()
        while True:
            await asyncio.sleep(UI_LEVEL_INTERVAL)
            if not self.ui_clients:
                continue
            msg = self.ui_levels()
            if msg != last_levels:
                last_levels = msg
                self._broadcast_ui(json.dumps(msg))
            status = self.ui_status()
            if status != last_status:
                last_status = status
                self._broadcast_ui(json.dumps(status))
            if time.monotonic() - last_lists > UI_REFRESH:
                last_lists = time.monotonic()
                try:
                    await self.send_lists()
                except Exception as e:
                    log.warning("一覧の取得に失敗: %s", e)

    # ---------- WebSocket (拡張機能) ----------

    async def ws_handler(self, ws):
        if ws.request.path == "/ui":
            await self.ui_handler(ws)
            return
        self.clients.add(ws)
        log.info("拡張機能が接続しました")
        try:
            await ws.send(json.dumps({"type": "slots", "slots": self.config.tab_slots()}))
            await ws.send(json.dumps({"type": "state", "enabled": self.config.enabled,
                                      "values": {str(k): v for k, v in self.tab_values.items()}}))
            await ws.send(json.dumps({"type": "lang", "value": resolve_language(self.config.language)}))
            if self._sent_meter:
                await ws.send(json.dumps({"type": "meter", "on": True}))
            async for raw in ws:
                try:
                    self.ext_message(json.loads(raw))
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.clients.discard(ws)
            log.info("拡張機能が切断しました")

    def ext_message(self, msg):
        """拡張機能から: 割り当て済みのスロット、タブの音の大きさ"""
        if msg.get("type") == "assigned":
            self.tab_assigned = {int(s) for s in msg.get("slots", [])}
        elif msg.get("type") == "meter":
            self.tab_meter = ({int(k): float(v) for k, v in (msg.get("levels") or {}).items()}, time.monotonic())

    async def process_request(self, connection, request):
        # 拡張機能の起動確認用。WebSocket でない GET /health には 200 を返す
        # (未起動時に WebSocket で接続失敗するとエラーが記録されるため、先にここで確認させる)
        if request.path == "/health":
            response = connection.respond(HTTPStatus.OK, "ok\n")
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        # 割り当て先の候補に出すアプリのアイコン (/appicon/discord.exe)。
        # 何が入っているかを他のサイトから覗かれないように、設定画面 (同じオリジン) からだけ返す
        if request.path.startswith("/appicon/"):
            exe = urllib.parse.unquote(request.path[len("/appicon/"):]).lower()
            site = request.headers.get("Sec-Fetch-Site", "same-origin")
            body = None
            if site in ("same-origin", "none") and EXE_RE.match(exe) and self.catalog is not None:
                body = await asyncio.to_thread(self.catalog.icon, exe)
            if not body:
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
            return Response(HTTPStatus.OK, "OK", Headers({
                "Content-Type": "image/png",
                "Content-Length": str(len(body)),
                "Cache-Control": "max-age=3600",
            }), body)
        # 設定画面のアイコン (Chrome のアプリウィンドウのタイトルバー・タスクバーに出る)
        if request.path in ("/favicon.ico", "/icon.png"):
            body = icon_bytes(request.path)
            return Response(HTTPStatus.OK, "OK", Headers({
                "Content-Type": "image/x-icon" if request.path.endswith(".ico") else "image/png",
                "Content-Length": str(len(body)),
                "Cache-Control": "max-age=86400",
            }), body)
        # 設定画面。WebSocket でない GET / には ui.html を返す
        if request.path in ("/", "/index.html") and "upgrade" not in request.headers.get("Connection", "").lower():
            with open(os.path.join(RES_DIR, "ui.html"), encoding="utf-8") as f:
                # 画面の文言を OS に合わせるため、どちらで動いているかを埋め込む
                page = f.read().replace('<html lang="ja">', f'<html lang="ja" data-platform="{"mac" if IS_MAC else "win"}">', 1)
                response = connection.respond(HTTPStatus.OK, page)
            del response.headers["Content-Type"]
            response.headers["Content-Type"] = "text/html; charset=utf-8"
            response.headers["Cache-Control"] = "no-store"
            return response
        return None

    async def keepalive(self):
        while True:
            await asyncio.sleep(KEEPALIVE)
            self._broadcast(json.dumps({"type": "ping"}))

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.audio_thread = threading.Thread(target=self.serial_thread, daemon=True, name="serial")
        self.audio_thread.start()
        if self.system:
            self.hotkeys = sysmod().HotkeyThread(self.on_hotkey)
            self.hotkeys.start()
            self.update_hotkeys()
            threading.Thread(target=self.watch_thread, daemon=True, name="watch").start()
        async with websockets.serve(self.ws_handler, "127.0.0.1", self.config.ws_port,
                                    process_request=self.process_request):
            log.info("WebSocket 待ち受け: ws://127.0.0.1:%d", self.config.ws_port)
            if not self.config.enabled:
                log.info("スライダー操作は一時停止中です")
            asyncio.ensure_future(self.ui_pump())
            asyncio.ensure_future(self.obs.run())
            if self.catalog is not None:
                # スタートメニューの読み込みを先に済ませておく (設定画面の一覧がすぐ出るように)
                asyncio.ensure_future(asyncio.to_thread(self.catalog.list))
            await self.keepalive()


def significantly_different(old, new, threshold):
    if old < 0:
        return True
    if abs(old - new) >= threshold:
        return True
    # 0 と 1 には吸い付くようにする (deej と同じ)
    if (abs(new - 1.0) < 1e-6 and old != 1.0) or (abs(new) < 1e-6 and old != 0.0):
        return True
    return False


# ---------------------------------------------------------------- 自動起動・設定画面・トレイ

def autostart_args():
    """Mac の LaunchAgent に書く起動のコマンド"""
    if FROZEN:
        return [sys.executable, "--tray"]
    return [sys.executable, os.path.abspath(__file__), "--tray"]


def autostart_command():
    if IS_MAC:
        return " ".join(f'"{a}"' for a in autostart_args())
    if FROZEN:
        return f'"{sys.executable}" --tray'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{os.path.abspath(__file__)}" --tray'


def autostart_registered():
    """Run (Mac は LaunchAgent) に登録されているコマンド (未登録なら None)"""
    if IS_MAC:
        import macsys
        return macsys.autostart_registered()
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0]
    except OSError:
        return None


def autostart_enabled():
    return autostart_registered() is not None


def set_autostart(enabled):
    """ログイン時の自動起動 (Windows は HKCU の Run、Mac は ~/Library/LaunchAgents に登録する)"""
    if IS_MAC:
        import macsys
        macsys.set_autostart(enabled, autostart_args())
        log.info("自動起動: %s", "オン" if enabled else "オフ")
        return
    if sys.platform != "win32":
        raise RuntimeError("Windows 以外では使えません")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    log.info("自動起動: %s", "オン" if enabled else "オフ")


def find_browser():
    """設定画面をアプリ風ウィンドウで開くためのブラウザ (Chrome → Edge)"""
    if sys.platform != "win32":
        return None
    import winreg
    for exe in ("chrome.exe", "msedge.exe"):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
            except OSError:
                pass
    return None


# 設定画面を出すプロセス: 自前のウィンドウ (exe / Python) か、Chrome・Edge のアプリウィンドウ
BROWSER_PROCESSES = ("chrome.exe", "msedge.exe")
SETTINGS_PROCESSES = ("deej-tab.exe", "python.exe", "pythonw.exe") + BROWSER_PROCESSES


def find_settings_window(processes=SETTINGS_PROCESSES):
    """開いている設定画面 (タイトルが "deej-tab" のウィンドウ)。なければ None"""
    if sys.platform != "win32":
        return None
    import psutil
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, buf, 64)
        if buf.value != "deej-tab":
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            if psutil.Process(pid.value).name().lower() in processes:
                found.append(hwnd)
        except Exception:
            pass
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return None
    # 複数あれば手前のもの (開いたばかりの設定画面は手前にある)
    fg = user32.GetForegroundWindow()
    return fg if fg in found else found[0]


def close_settings_window():
    """開いている設定画面を閉じる (本体を終了する時。つながらない画面だけが残らないように)"""
    if IS_MAC:
        import macsys
        import signal
        pid = macsys.settings_pid(SETTINGS_LOCK)
        if pid:
            os.kill(pid, signal.SIGTERM)
        return
    if sys.platform != "win32":
        return
    WM_CLOSE = 0x0010
    for _ in range(3):   # 自前のウィンドウと Chrome のアプリウィンドウの両方が開いていることもある
        hwnd = find_settings_window()
        if not hwnd:
            return
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.3)


def focus_settings_window():
    """開いている設定画面を手前に出す。見つかれば True"""
    if IS_MAC:
        import macsys
        pid = macsys.settings_pid(SETTINGS_LOCK)
        return bool(pid) and macsys.activate(pid)
    hwnd = find_settings_window()
    if not hwnd:
        return False
    bring_to_front(hwnd)
    return True


def bring_to_front(hwnd):
    """ウィンドウを最前面に出して操作できる状態にする。

    Windows は操作中でないプロセスの SetForegroundWindow を拒否する (タスクバーが点滅するだけ)。
    exe (onefile) は起動用の親プロセスの子として動くので、exe を押した直後でもこれに当たる。
    前面ウィンドウの入力スレッドに一時的につなぐのと、Alt キーを送るのとで許可を得る"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE, SW_SHOW = 9, 5
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    if user32.GetForegroundWindow() == hwnd:
        return
    me = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    if user32.GetForegroundWindow() != hwnd:
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)


def fit_settings_window(msg, hwnd=None):
    """設定画面のウィンドウを、中身がはみ出さないぎりぎりの大きさにして画面の中央に置く。
    msg は設定画面から: width/height = 必要な中身の大きさ、inner_* = 今の中身の大きさ (CSS px)、dpr = 倍率。
    hwnd を渡さなければ、Chrome で開いた設定画面を探す (ページの resizeTo は Chrome が最大化などで無視する)。
    その時は Chrome / Edge のウィンドウだけを見る (自前のウィンドウは自分で合わせるので、
    別の所から開いたページの要求で、そちらを動かしてしまわないように)"""
    hwnd = hwnd or find_settings_window(BROWSER_PROCESSES)
    if not hwnd:
        return
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    # 高 DPI のウィンドウを実際のピクセルで扱う (Per-Monitor v2)
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    if user32.IsIconic(hwnd):
        return
    dpr = float(msg["dpr"])
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # 枠・タイトルバーの分 = ウィンドウ全体 − 今の中身 (ページが測った時の状態のまま計算する)
    frame_w = (rect.right - rect.left) - round(float(msg["inner_width"]) * dpr)
    frame_h = (rect.bottom - rect.top) - round(float(msg["inner_height"]) * dpr)
    SW_RESTORE = 9
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    w = round(float(msg["width"]) * dpr) + frame_w
    h = round(float(msg["height"]) * dpr) + frame_h

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    MONITOR_DEFAULTTONEAREST = 2
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(mi))
    work = mi.rcWork
    w = min(w, work.right - work.left)
    h = min(h, work.bottom - work.top)
    x = work.left + (work.right - work.left - w) // 2
    y = work.top + (work.bottom - work.top - h) // 2
    # 次からは最初からこの大きさで開く (論理ピクセルで覚える)
    scale = user32.GetDpiForWindow(hwnd) / 96
    save_window_size(round(w / scale), round(h / scale))
    # すでにこの大きさなら動かさない (開いた時から合っていれば、何も起きない)
    cur_w, cur_h = rect.right - rect.left, rect.bottom - rect.top
    if abs(cur_w - w) <= 2 and abs(cur_h - h) <= 2:
        return
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    log.info("設定画面の大きさ: %dx%d", w, h)


def saved_window_size():
    try:
        with open(WINDOW_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return int(d["width"]), int(d["height"])
    except Exception:
        return DEFAULT_WINDOW


def save_window_size(w, h):
    if (w, h) == saved_window_size():
        return
    try:
        os.makedirs(UI_DATA_DIR, exist_ok=True)
        with open(WINDOW_FILE, "w", encoding="utf-8") as f:
            json.dump({"width": w, "height": h}, f)
    except OSError as e:
        log.warning("ウィンドウの大きさを保存できません: %s", e)


def centered_position(w, h):
    """メインの画面の作業領域の中央に置く時の左上 (論理ピクセル)"""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    old = None
    try:
        # DPI 非対応として聞くと、論理ピクセルで返ってくる
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))
    except Exception:
        pass
    work = wintypes.RECT()
    SPI_GETWORKAREA = 0x0030
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
    if old:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    return (work.left + max(0, (work.right - work.left - w) // 2),
            work.top + max(0, (work.bottom - work.top - h) // 2))


_settings_proc = None   # このプロセスから起動した設定画面 (開きかけを二重に起動しないため)
_settings_mutex = None  # 設定画面のプロセスが 1 つだけになるように持っておく


def open_settings(port):
    global _settings_proc
    if focus_settings_window():
        return
    # 自前のウィンドウ (WebView2) で開く。別のプロセスにするのは、画面を閉じても本体は残すため
    if webview_available():
        # 設定画面はウィンドウが出るまで数秒かかる。その間にもう一度押されても起動し直さない
        # (開いたら自分で手前に出る)。別のプロセスから起動された分は run_settings_window が弾く
        if _settings_proc is not None and _settings_proc.poll() is None:
            return
        if FROZEN:
            cmd = [sys.executable, "--settings"]
        elif IS_MAC:
            cmd = [sys.executable, os.path.abspath(__file__), "--settings"]
        else:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            cmd = [pythonw if os.path.exists(pythonw) else sys.executable, os.path.abspath(__file__), "--settings"]
        if CONFIG_ARG:
            cmd.append(CONFIG_ARG)
        # exe (onefile) から自分を起動すると、そのままでは親の展開フォルダを使い回す。
        # 親が先に終わる (2つ目の起動から開いた) とフォルダが消えて落ちるので、自分で展開させる
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        _settings_proc = subprocess.Popen(cmd, cwd=APP_DIR, env=env)
        return
    # pywebview がなければ Chrome / Edge のアプリウィンドウで開く
    url = f"http://127.0.0.1:{port}/"
    browser = find_browser()
    if browser:
        # 設定画面専用のプロファイルで開く。普段の Chrome が起動中だと、そちらのウィンドウの大きさ
        # (最大化など) で開いてから縮むため。別のプロファイルなら大きさと位置の指定がそのまま効く
        w, h = saved_window_size()
        x, y = centered_position(w, h)
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={UI_PROFILE_DIR}",
                          "--no-first-run", "--no-default-browser-check",
                          f"--window-size={w},{h}", f"--window-position={x},{y}"])
    else:
        import webbrowser
        webbrowser.open(url)


def webview_available():
    import importlib.util
    return sys.platform in ("win32", "darwin") and importlib.util.find_spec("webview") is not None


class SettingsApi:
    """設定画面 (自前のウィンドウ) の JavaScript から window.pywebview.api.xxx() で呼ばれる"""

    def __init__(self):
        self._hwnd = None
        self._window = None
        self._pending_fit = None
        self._lock = threading.Lock()

    def fit(self, msg):
        if IS_MAC:
            self._fit_mac(msg)
            return
        # ページの方が先に準備できて、ウィンドウがまだ出ていないことがある。その時は出てから合わせる
        with self._lock:
            hwnd = self._hwnd
            if not hwnd:
                self._pending_fit = msg
        if hwnd:
            fit_settings_window(msg, hwnd)

    def _shown(self, hwnd):
        with self._lock:
            self._hwnd = hwnd
            msg, self._pending_fit = self._pending_fit, None
        if msg:
            fit_settings_window(msg, hwnd)

    def _fit_mac(self, msg):
        """Mac: ウィンドウの大きさを中身に合わせる (pywebview の大きさは枠込みの論理ピクセル)"""
        import webview
        win = self._window
        if win is None:
            return
        frame_w = win.width - float(msg["inner_width"])
        frame_h = win.height - float(msg["inner_height"])
        w = round(float(msg["width"]) + frame_w)
        h = round(float(msg["height"]) + frame_h)
        try:
            screen = webview.screens[0]
            w, h = min(w, screen.width), min(h, screen.height - 40)   # 40: メニューバーと Dock の分
        except Exception:
            pass
        save_window_size(w, h)
        if abs(win.width - w) <= 2 and abs(win.height - h) <= 2:
            return
        win.resize(w, h)
        log.info("設定画面の大きさ: %dx%d", w, h)

    def pick_exe(self):
        """割り当てるアプリを exe ファイル (Mac は .app) から選ぶ。選んだファイルのパス (やめたら None)"""
        import webview
        if IS_MAC:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, directory="/Applications",
                                                    file_types=("アプリ (*.app)",))
            return paths[0] if paths else None
        start = os.environ.get("ProgramFiles", "")
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=start,
                                                file_types=("アプリ (*.exe)",))
        return paths[0] if paths else None


def style_settings_window(hwnd):
    """タイトルバーを画面と同じ暗い色にして、アイコンを付ける"""
    from ctypes import wintypes
    dwm = ctypes.windll.dwmapi
    DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 20, 34, 35, 36

    def attr(key, value):
        v = wintypes.DWORD(value)
        dwm.DwmSetWindowAttribute(hwnd, key, ctypes.byref(v), ctypes.sizeof(v))

    attr(DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    attr(DWMWA_CAPTION_COLOR, 0x0014100E)   # #0e1014 (COLORREF は 0x00BBGGRR)。Windows 11 のみ
    attr(DWMWA_BORDER_COLOR, 0x00362B26)    # #262b36
    attr(DWMWA_TEXT_COLOR, 0x00EFEAE8)      # #e8eaef
    try:
        os.makedirs(UI_DATA_DIR, exist_ok=True)
        ico = os.path.join(UI_DATA_DIR, "icon.ico")
        save_icon(ico)
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x0080
        for kind, size in ((0, 16), (1, 32)):   # ICON_SMALL / ICON_BIG
            scale = user32.GetDpiForWindow(hwnd) / 96
            px = round(size * scale)
            h = user32.LoadImageW(None, ico, IMAGE_ICON, px, px, LR_LOADFROMFILE)
            if h:
                user32.SendMessageW(hwnd, WM_SETICON, kind, ctypes.c_void_p(h))
    except Exception as e:
        log.warning("設定画面のアイコンを付けられません: %s", e)


def run_settings_window(port):
    """設定画面を自前のウィンドウ (pywebview = Edge WebView2 / Mac は WKWebView) で開き、閉じるまで待つ"""
    global _settings_mutex
    if IS_MAC:
        run_settings_window_mac(port)
        return
    # 設定画面は 1 枚だけ。もう開いている (開きかけを含む) なら、それを手前に出して終わる
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ERROR_ALREADY_EXISTS = 183
    _settings_mutex = kernel32.CreateMutexW(None, False, "Local\\deej-tab.settings")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not focus_settings_window():
            time.sleep(0.3)
        return
    import webview
    try:
        # タスクバーで Python などとまとめられないように、アプリとしての名前を付ける
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("deej-tab.settings")
    except Exception:
        pass
    w, h = saved_window_size()
    x, y = centered_position(w, h)
    api = SettingsApi()
    window = webview.create_window(
        "deej-tab", f"http://127.0.0.1:{port}/", js_api=api,
        width=w, height=h, x=x, y=y, min_size=(640, 480), background_color="#0e1014",
    )
    api._window = window

    def on_shown():
        hwnd = int(window.native.Handle.ToInt64())
        style_settings_window(hwnd)
        bring_to_front(hwnd)   # 別プロセスから開くので、そのままだと他のウィンドウの後ろに出ることがある
        api._shown(hwnd)

    window.events.shown += on_shown
    webview.start()


def run_settings_window_mac(port):
    import macsys
    # 設定画面は 1 枚だけ。もう開いているなら、それを手前に出して終わる
    if not macsys.settings_lock(SETTINGS_LOCK):
        macsys.activate(macsys.settings_pid(SETTINGS_LOCK))
        return
    import webview
    w, h = saved_window_size()
    api = SettingsApi()
    window = webview.create_window(
        "deej-tab", f"http://127.0.0.1:{port}/", js_api=api,
        width=w, height=h, min_size=(640, 480), background_color="#0e1014",
    )
    api._window = window

    def on_shown():
        # 本体と同じ .app (Dock に出さない設定) から開くので、開いている間は Dock に出して手前に出す
        macsys.set_dock_icon(True)
        macsys.activate()

    window.events.shown += on_shown
    webview.start()


def already_running(port):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
ICON_RED = (255, 77, 77)       # 実機のスライダー LED の赤 (ui.html の --accent)
ICON_GRAY = (128, 134, 148)    # デバイス未接続


def app_icon(size=256, lit=True):
    """アプリのアイコン: スライダー3本。つまみより下は LED が光っている。
    64 マスの座標で描き、8 倍で描いてから縮める。小さいサイズは線を太く・飾りを省く。
    lit=False はデバイス未接続 (つまみを灰色にして光らせない)"""
    from PIL import Image, ImageDraw, ImageFilter
    small = size <= 24
    detail = size >= 48
    ss = 8
    S = size * ss
    k = S / 64
    knob = ICON_RED if lit else ICON_GRAY

    def box(x0, y0, x1, y1):
        return [round(x0 * k), round(y0 * k), round(x1 * k), round(y1 * k)]

    def layer(draw, blur=0):
        """半透明の図形は別の層に描いて重ねる (ImageDraw は直接描くと透明度が混ざらないため)"""
        nonlocal img
        over = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        draw(ImageDraw.Draw(over))
        if blur:
            over = over.filter(ImageFilter.GaussianBlur(blur * k))
            over.putalpha(Image.composite(over.getchannel("A"), Image.new("L", (S, S), 0), mask))
        img = Image.alpha_composite(img, over)

    # 背景: 角丸の四角に上から下へのグラデーション
    pad, radius = (0, 13) if small else (2, 14)
    top, bottom = (42, 46, 57), (15, 17, 22)
    grad = Image.new("RGBA", (1, 256))
    for y in range(256):
        t = y / 255
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    grad = grad.resize((S, S), Image.BILINEAR)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box(pad, pad, 64 - pad, 64 - pad), radius=round(radius * k), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    if detail:
        # 縁をうっすら明るくして立体感を出す
        layer(lambda d: d.rounded_rectangle(box(pad, pad, 64 - pad, 64 - pad), radius=round(radius * k),
                                            outline=(255, 255, 255, 26), width=round(0.6 * k)))

    if small:
        xs, tw, kw, kh, y0, y1 = (14, 32, 50), 6, 17, 11, 9, 55
    else:
        xs, tw, kw, kh, y0, y1 = (19, 32, 45), 4, 14, 8, 13, 51
    knobs_y = (38, 22, 31)

    # 溝と、つまみより下の光っている部分
    def tracks(d):
        for x, ky in zip(xs, knobs_y):
            d.rounded_rectangle(box(x - tw / 2, y0, x + tw / 2, y1), radius=round(tw / 2 * k), fill=(7, 8, 11, 255))
    layer(tracks)
    if lit:
        def lit_tracks(d):
            for x, ky in zip(xs, knobs_y):
                d.rounded_rectangle(box(x - tw / 2, ky, x + tw / 2, y1), radius=round(tw / 2 * k),
                                    fill=ICON_RED + (150 if small else 120,))
        layer(lit_tracks)

    # つまみの光 (ぼかした赤を下に敷く)
    if lit and detail:
        def glow(d):
            for x, ky in zip(xs, knobs_y):
                d.rounded_rectangle(box(x - kw / 2, ky - kh / 2, x + kw / 2, ky + kh / 2),
                                    radius=round(3 * k), fill=ICON_RED + (150,))
        layer(glow, blur=3)

    # つまみ: 本体、上半分のつや、白い指示線 (自作ツマミと同じ)
    def knobs(d):
        for x, ky in zip(xs, knobs_y):
            d.rounded_rectangle(box(x - kw / 2, ky - kh / 2, x + kw / 2, ky + kh / 2),
                                radius=round((2.5 if small else 3) * k), fill=knob + (255,))
    layer(knobs)
    if detail:
        def shine(d):
            for x, ky in zip(xs, knobs_y):
                d.rounded_rectangle(box(x - kw / 2 + 0.8, ky - kh / 2 + 0.6, x + kw / 2 - 0.8, ky - 0.6),
                                    radius=round(2.2 * k), fill=(255, 255, 255, 34))
        layer(shine)
        def mark(d):
            for x, ky in zip(xs, knobs_y):
                d.rectangle(box(x - kw / 2 + 2.5, ky - 0.45, x + kw / 2 - 2.5, ky + 0.45), fill=(255, 255, 255, 230))
        layer(mark)

    return img.resize((size, size), Image.LANCZOS)


def save_icon(path):
    """全サイズ入りの .ico を書く (exe・設定画面のファビコン用)"""
    imgs = [app_icon(s) for s in ICON_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=imgs[:-1])


_icon_cache = {}


def icon_bytes(path):
    """設定画面に返すアイコン (/favicon.ico は全サイズ入り、/icon.png は 256px)"""
    if path not in _icon_cache:
        import io
        buf = io.BytesIO()
        if path.endswith(".ico"):
            imgs = [app_icon(s) for s in (16, 24, 32, 48, 64)]
            imgs[-1].save(buf, format="ICO", sizes=[im.size for im in imgs], append_images=imgs[:-1])
        else:
            app_icon(256).save(buf, format="PNG")
        _icon_cache[path] = buf.getvalue()
    return _icon_cache[path]


def tray_image(lit=True):
    return app_icon(64, lit)


def tray_title(state, enabled=True, lang="ja"):
    if not enabled:
        return tr("deej-tab - 一時停止中 (スライダーで音量は変わりません)", lang)
    if state["connected"]:
        return tr("deej-tab - {port} に接続中", lang, port=state["port"])
    return tr("deej-tab - デバイス未接続 ({port})", lang, port=state["port"])


def restore_tray_icon(icon):
    """トレイアイコンが通知領域から消えていたら登録し直す。登録し直したら True。

    pystray は Explorer の再起動には対応しているが、スリープ復帰時に登録し直しに
    失敗するとアイコンが消えたままになる。NIM_MODIFY は消えたアイコンに対して失敗するので、
    それで生きているかを確かめる。"""
    if sys.platform != "win32":
        return False
    from pystray._util import win32 as w
    data = w.NOTIFYICONDATAW(cbSize=ctypes.sizeof(w.NOTIFYICONDATAW), hWnd=icon._hwnd,
                             hID=id(icon), uFlags=w.NIF_TIP, szTip=icon.title)
    if w.Shell_NotifyIcon(w.NIM_MODIFY, data):
        return False
    icon._show()
    return True


def run_tray(app, port):
    import pystray

    def on_open(icon, item):
        open_settings(port)

    def on_autostart(icon, item):
        try:
            set_autostart(not autostart_enabled())
            app.broadcast_ui(app.ui_config())
        except Exception as e:
            log.error("自動起動の切り替えに失敗: %s", e)

    def on_quit(icon, item):
        log.info("終了します")
        try:
            close_settings_window()
        except Exception as e:
            log.warning("設定画面を閉じられません: %s", e)
        app.shutdown()
        icon.stop()

    def on_pause(icon, item):
        try:
            app.set_enabled(not app.config.enabled)
        except Exception as e:
            log.error("一時停止の切り替えに失敗: %s", e)

    def profile_items():
        def switch(name):
            def run(icon, item):
                try:
                    app.switch_profile(name)
                except Exception as e:
                    log.error("プロファイルの切り替えに失敗: %s", e)
            return run
        for name in app.config.profiles:
            yield pystray.MenuItem(name, switch(name), radio=True,
                                   checked=lambda item, n=name: app.config.active_profile == n)

    def lang():
        return resolve_language(app.config.language)

    def text(s):
        """メニューを開くたびに今の言語で出す"""
        return lambda item: tr(s, lang())

    icons = {True: tray_image(True), False: tray_image(False)}
    state = dict(app.serial_state)
    icon = pystray.Icon(
        "deej-tab", icons[state["connected"] and app.config.enabled], tray_title(state, app.config.enabled, lang()),
        menu=pystray.Menu(
            pystray.MenuItem(text("設定を開く"), on_open, default=True),
            pystray.MenuItem(text("一時停止 (スライダーで音量を変えない)"), on_pause,
                             checked=lambda item: not app.config.enabled),
            pystray.MenuItem(text("プロファイル"), pystray.Menu(profile_items),
                             visible=lambda item: len(app.config.profiles) > 1),
            pystray.MenuItem(text("ログイン時に自動で起動" if IS_MAC else "Windows の起動時に自動で起動"), on_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(text("終了"), on_quit),
        ),
    )

    def watch(icon):
        # デバイスの接続状態をアイコン (つまみの色) とツールチップに出す
        icon.visible = True
        shown = None
        next_tray_check = time.monotonic() + TRAY_CHECK
        while not app.stop.is_set():
            state = dict(app.serial_state)
            enabled = app.config.enabled
            key = (state["connected"], state["port"], enabled, app.config.active_profile,
                   tuple(app.config.profiles), app.config.language)
            if key != shown:
                shown = key
                # 一時停止中は未接続と同じ灰色のつまみ (光らせない)
                icon.icon = icons[state["connected"] and enabled]
                icon.title = tray_title(state, enabled, lang()) + (
                    f" [{app.config.active_profile}]" if len(app.config.profiles) > 1 else "")
                icon.update_menu()
            if time.monotonic() >= next_tray_check:
                next_tray_check = time.monotonic() + TRAY_CHECK
                try:
                    if restore_tray_icon(icon):
                        log.info("トレイアイコンが消えていたので表示し直しました")
                except Exception as e:
                    log.warning("トレイアイコンを確認できません: %s", e)
            app.stop.wait(1.0)

    if IS_MAC:
        import macsys
        macsys.set_dock_icon(False)   # メニューバーにだけ出す (python で動かした時。.app は Info.plist で指定)
    icon.run(setup=watch)


def setup_logging():
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # websockets は接続のたびに INFO を出す (拡張機能の /health 確認で溜まる) ので警告以上だけにする
    logging.getLogger("websockets").setLevel(logging.WARNING)
    fh = RotatingFileHandler(os.path.join(APP_DIR, "deej-tab.log"), maxBytes=1_000_000,
                             backupCount=1, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw では None
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def main():
    setup_logging()
    global CONFIG_ARG
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    CONFIG_ARG = args[0] if args else None
    tray_only = "--tray" in sys.argv       # 自動起動時は設定画面を開かない
    no_tray = "--no-tray" in sys.argv      # 黒い画面で動かす (動作確認用)
    path = args[0] if args else os.path.join(APP_DIR, "config.yaml")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(CONFIG_HEADER + DEFAULT_CONFIG)
        log.info("config.yaml がないので作りました: %s", path)
    config = Config(path)
    config.load()

    # 設定画面だけのプロセス (本体のトレイから起動される)
    if "--settings" in sys.argv:
        run_settings_window(config.ws_port)
        return

    # 2つ目の起動は設定画面を開くだけにする
    if already_running(config.ws_port):
        if not tray_only:
            open_settings(config.ws_port)
        return

    # 自動起動が Python 版のまま登録されていたら exe に付け替える
    if FROZEN and not args and autostart_enabled() and autostart_registered() != autostart_command():
        try:
            set_autostart(True)
        except Exception as e:
            log.warning("自動起動の登録を exe に切り替えられません: %s", e)

    catalog = None
    if sys.platform == "win32":
        audio_factory = WindowsAudio
        from appcatalog import AppCatalog
        catalog = AppCatalog()
    elif IS_MAC:
        import macsys
        audio_factory = macsys.MacAudio
        catalog = macsys.MacCatalog()
    else:
        audio_factory = DummyAudio
        log.warning("Windows・Mac 以外のため音量操作は行いません (タブ音量のみ)")
    app = DeejTab(config, audio_factory, catalog)

    try:
        import pystray  # noqa: F401
    except ImportError:
        no_tray = True
    if no_tray:
        try:
            asyncio.run(app.run())
        except KeyboardInterrupt:
            app.shutdown()
        return

    threading.Thread(target=lambda: asyncio.run(app.run()), daemon=True).start()
    if not tray_only:
        threading.Timer(1.0, open_settings, args=(config.ws_port,)).start()
    run_tray(app, config.ws_port)


if __name__ == "__main__":
    main()
