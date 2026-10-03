"""Mac の部品 (winsys.py の Mac 版)

- 補助プログラム (mac/deej-tab-helper.swift) とのやりとり
- 音量 (MacAudio): 全体・マイクの音量と、アプリごとの音量 (補助プログラムの Process Tap)
- 設定画面に出すアプリ一覧とアイコン (MacCatalog)
- グローバルなショートカット・最前面のアプリ (全画面かどうか)
- ログイン時の自動起動 (~/Library/LaunchAgents)

アプリは .app の名前 (小文字。例: discord.app) で区別する。Windows の exe 名と同じ役割。
"""

import base64
import json
import logging
import os
import plistlib
import subprocess
import sys
import threading
import time

log = logging.getLogger("deej-tab")

HELPER_NAME = "deej-tab-helper"
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.expanduser("~/Library/Application Support"), "deej-tab")

REQUEST_TIMEOUT = 2.0     # 秒。補助プログラムの返事を待つ時間
RESTART_WAIT = 3.0        # 秒。補助プログラムが落ちたら、これだけ空けてから起動し直す
PROCESS_CACHE = 1.0       # 秒。音を扱っているアプリの一覧を使い回す時間
PEAK_CACHE = 0.04         # 秒。音の大きさを使い回す時間 (LED 1 回分の計算で何度も聞かないように)
METER_KEEP = 2.0          # 秒。これだけ聞かれなかったアプリは、音の大きさを測るのをやめる


class HelperError(Exception):
    pass


def helper_path():
    if FROZEN:
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), HELPER_NAME)
    return os.path.join(HERE, "mac", "build", HELPER_NAME)


class Helper:
    """補助プログラムを起動して、命令を送って返事を待つ。どのスレッドから呼んでもよい"""

    def __init__(self, cmd=None):
        self.cmd = list(cmd) if cmd else [helper_path()]   # テストでは偽の補助プログラムを渡す
        self.lock = threading.Lock()
        self.proc = None
        self.next_id = 1
        self.waiting = {}          # id → {"ev": Event, "res": 返事}
        self.events = {}           # イベント名 → 呼ぶ関数 (例: "hotkey")
        self.on_restart = []       # 起動し直した時に呼ぶ関数 (補助プログラムが覚えていた設定を送り直す)
        self.started = 0           # 起動した回数
        self.failed_at = 0.0

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _start(self):
        if not os.path.exists(self.cmd[-1]):
            raise HelperError(f"補助プログラムがありません: {self.cmd[-1]} (build_mac.py で作ってください)")
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="helper-out").start()
        threading.Thread(target=self._read_err, args=(self.proc,), daemon=True, name="helper-err").start()
        self.started += 1
        if self.started > 1:
            log.info("補助プログラムを起動し直しました")
            for cb in list(self.on_restart):
                threading.Thread(target=self._call, args=(cb,), daemon=True).start()

    @staticmethod
    def _call(cb):
        try:
            cb()
        except Exception as e:
            log.warning("補助プログラムに設定を送り直せません: %s", e)

    def request(self, cmd, timeout=REQUEST_TIMEOUT, **args):
        with self.lock:
            if not self._alive():
                if self.proc is not None and time.monotonic() - self.failed_at < RESTART_WAIT:
                    raise HelperError("補助プログラムが動いていません")
                try:
                    self._start()
                except HelperError:
                    raise
                except Exception as e:
                    self.failed_at = time.monotonic()
                    raise HelperError(f"補助プログラムを起動できません: {e}")
            rid = self.next_id
            self.next_id += 1
            slot = {"ev": threading.Event(), "res": None}
            self.waiting[rid] = slot
            try:
                self.proc.stdin.write((json.dumps(dict(args, id=rid, cmd=cmd)) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except OSError as e:
                self.waiting.pop(rid, None)
                self._kill()
                raise HelperError(f"補助プログラムに送れません: {e}")
        if not slot["ev"].wait(timeout):
            self.waiting.pop(rid, None)
            raise HelperError(f"補助プログラムの返事がありません ({cmd})")
        res = slot["res"] or {"error": "補助プログラムが終了しました"}
        if "error" in res:
            raise HelperError(res["error"])
        return res

    def _kill(self):
        self.failed_at = time.monotonic()
        try:
            self.proc.kill()
        except Exception:
            pass

    def _read(self, proc):
        for raw in proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if "event" in msg:
                cb = self.events.get(msg["event"])
                if cb:
                    try:
                        cb(msg)
                    except Exception as e:
                        log.warning("補助プログラムからの知らせの処理に失敗: %s", e)
                continue
            slot = self.waiting.pop(msg.get("id"), None)
            if slot:
                slot["res"] = msg
                slot["ev"].set()
        # 終了した: 待っているものは全部失敗にする
        self.failed_at = time.monotonic()
        proc.stdout.close()
        try:
            proc.wait(1)
        except subprocess.TimeoutExpired:
            pass
        log.warning("補助プログラムが終了しました (%s)", proc.returncode)
        for rid in list(self.waiting):
            slot = self.waiting.pop(rid, None)
            if slot:
                slot["ev"].set()

    @staticmethod
    def _read_err(proc):
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                log.warning("%s", line)
        proc.stderr.close()

    def stop(self):
        with self.lock:
            if self._alive():
                try:
                    self.proc.stdin.close()
                    self.proc.wait(2)
                except Exception:
                    self._kill()
            elif self.proc is not None and not self.proc.stdin.closed:
                self.proc.stdin.close()


_helper = None
_helper_lock = threading.Lock()


def helper():
    global _helper
    with _helper_lock:
        if _helper is None:
            _helper = Helper()
        return _helper


# ---------------------------------------------------------------- 音量

def gain(v):
    """スライダーの音量 → 音にかける倍率。Chrome のタブと同じく 2 乗 (下のほうでも細かく変えられるように)"""
    return round(max(0.0, min(1.0, v)) ** 2, 4)


class MacAudio:
    """WindowsAudio の Mac 版。アプリごとの音量は補助プログラムが覚えていて、新しく出てきた
    プロセス (Chrome の Helper など) にも自動でかける。補助プログラムが起動し直したら送り直す"""

    def __init__(self, helper_=None):
        self.h = helper_ or helper()
        self.volumes = {}          # {アプリ名: 音量} (個別に変えたもの)
        self.others = None         # 「そのほかのアプリ」(音量, 入れないアプリの一覧)
        self.original = {}         # deej-tab が最初に変える前の音量 {("master",) / ("mic",): 値}
        self._procs = (-1e9, {})
        self._meter = {"apps": {}, "others": None, "master": 0.0}   # 最後に聞かれた時刻
        self._meter_sent = None
        self._peaks = (-1e9, None)
        self.h.on_restart.append(self._resend)

    def _resend(self):
        if self.volumes:
            self.h.request("set_gains", gains={n: gain(v) for n, v in self.volumes.items()})
        if self.others:
            self.h.request("set_others", gain=gain(self.others[0]), exclude=self.others[1])
        self._meter_sent = None

    def _remember(self, key, getter):
        if key not in self.original:
            try:
                self.original[key] = float(getter())
            except Exception:
                pass

    def set_master(self, v):
        self._remember(("master",), self.get_master)
        self.h.request("set_volume", scope="output", value=v)

    def set_mic(self, v):
        self._remember(("mic",), self.get_mic)
        self.h.request("set_volume", scope="input", value=v)

    def get_master(self):
        return float(self.h.request("get_volume", scope="output")["value"])

    def get_mic(self):
        return float(self.h.request("get_volume", scope="input")["value"])

    def processes(self):
        """音を扱っているアプリ {名前: {"title", "path", "output"}} (少しの間使い回す)"""
        at, procs = self._procs
        if time.monotonic() - at > PROCESS_CACHE:
            procs = {p["name"]: p for p in self.h.request("processes")["processes"]}
            self._procs = (time.monotonic(), procs)
        return procs

    def set_processes(self, names, v, mapped_names=None):
        if mapped_names is not None:
            exclude = sorted(mapped_names)
            self.others = (v, exclude)
            # 「そのほか」を変えたら、そのほかのアプリの個別の音量は消える (Windows と同じく後から変えたほうが効く)
            self.volumes = {n: x for n, x in self.volumes.items() if n in mapped_names}
            self.h.request("set_others", gain=gain(v), exclude=exclude)
            return
        names = [n for n in names if n]
        if not names:
            return
        for n in names:
            self.volumes[n] = v
        self.h.request("set_gains", gains={n: gain(v) for n in names})

    def get_process(self, name):
        if not self.has_session(name):
            return None
        if name in self.volumes:
            return self.volumes[name]
        if self.others and name not in self.others[1]:
            return self.others[0]
        return 1.0

    def has_session(self, name):
        return name in self.processes()

    def restore(self):
        """アプリの音量は横取りをやめれば元 (100%) に戻る。全体・マイクは覚えておいた音量に戻す
        (全体は今のまま。一時停止・終了した瞬間に急に大音量にならないように)"""
        self.volumes, self.others = {}, None
        original, self.original = self.original, {}
        try:
            self.h.request("reset")
        except Exception as e:
            log.warning("アプリの音量を元に戻せません: %s", e)
        for key, v in original.items():
            try:
                if key == ("master",):
                    continue
                self.h.request("set_volume", scope="input", value=v)
            except Exception as e:
                log.warning("音量を元に戻せません (%s): %s", key, e)
        log.info("音量を元に戻しました")

    # ---------- 音の大きさ (LED を音に合わせる時) ----------

    def _peaks_for(self):
        """聞かれたものだけ測るように補助プログラムに頼んで、測った値を返す"""
        now = time.monotonic()
        m = self._meter
        apps = sorted(n for n, t in m["apps"].items() if now - t < METER_KEEP)
        others = m["others"][1] if m["others"] and now - m["others"][0] < METER_KEEP else None
        want = (tuple(apps), tuple(others) if others is not None else None, now - m["master"] < METER_KEEP)
        if want != self._meter_sent:
            self.h.request("meter", apps=apps, others_exclude=others, master=want[2])
            self._meter_sent = want
            m["apps"] = {n: t for n, t in m["apps"].items() if now - t < METER_KEEP}
        at, peaks = self._peaks
        if peaks is None or now - at > PEAK_CACHE:
            peaks = self.h.request("peaks")
            self._peaks = (now, peaks)
        return peaks

    def peak_master(self):
        self._meter["master"] = time.monotonic()
        try:
            return float(self._peaks_for().get("master", 0.0))
        except Exception:
            return 0.0

    def peak_mic(self):
        return 0.0   # マイクの音の大きさを測るにはマイクの許可がいるので、Mac では測らない

    def peak(self, names=None, mapped_names=None):
        now = time.monotonic()
        try:
            if mapped_names is not None:
                self._meter["others"] = (now, sorted(mapped_names))
                return float(self._peaks_for().get("others", 0.0))
            names = [n for n in (names or []) if n]
            for n in names:
                self._meter["apps"][n] = now
            apps = self._peaks_for().get("apps", {})
            return max([float(apps.get(n, 0.0)) for n in names] + [0.0])
        except Exception:
            return 0.0

    def foreground_process_name(self):
        try:
            return self.h.request("front").get("name")
        except Exception:
            return None


# ---------------------------------------------------------------- アプリ一覧

APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", os.path.expanduser("~/Applications"))
INSTALLED_TTL = 600.0
ICON_SIZE = 32
HISTORY_FILE = os.path.join(DATA_DIR, "apps.json")
HISTORY_MAX = 200
HIDDEN = {"finder.app", "deej-tab.app", "terminal.app", "iterm.app", "python.app", "systemuiserver.app",
          "controlcenter.app", "dock.app", "loginwindow.app", "system settings.app", "activity monitor.app"}


def installed_apps():
    """インストール済みのアプリ {名前: パス}"""
    result = {}
    for d in APP_DIRS:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for e in entries:
            if e.lower().endswith(".app") and not e.startswith("."):
                result.setdefault(e.lower(), os.path.join(d, e))
    return result


class MacCatalog:
    """appcatalog.AppCatalog の Mac 版 (設定画面の「割り当てを追加」の候補)"""

    def __init__(self, helper_=None, history_file=HISTORY_FILE):
        self.h = helper_ or helper()
        self.history_file = history_file
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.paths = {}        # 名前 → .app の場所
        self.titles = {}       # .app の場所 → 表示名 (日本語の名前など)
        self.icons = {}
        self.installed = {}
        self.installed_at = 0.0
        self.history = self._load_history()

    def _load_history(self):
        try:
            with open(self.history_file, encoding="utf-8") as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(v, dict)}
        except Exception:
            return {}

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            items = sorted(self.history.items(), key=lambda kv: kv[1].get("seen", 0), reverse=True)[:HISTORY_MAX]
            tmp = self.history_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(items), f, ensure_ascii=False)
            os.replace(tmp, self.history_file)
        except OSError:
            pass

    def _installed(self):
        with self.scan_lock:
            if time.monotonic() - self.installed_at > INSTALLED_TTL or not self.installed_at:
                self.installed = installed_apps()
                self.installed_at = time.monotonic()
                self._load_titles(list(self.installed.values()))
            return self.installed

    def _load_titles(self, paths):
        paths = [p for p in paths if p and p not in self.titles]
        if not paths:
            return
        try:
            names = self.h.request("names", paths=paths, timeout=10)["names"]
        except Exception:
            names = {}
        for p in paths:
            n = names.get(p) or os.path.basename(p)
            self.titles[p] = n[:-4] if n.lower().endswith(".app") else n

    def list(self):
        """[{exe, name, audio, window, recent, installed}] (exe はアプリ名。Windows と同じ形)"""
        procs = self.h.request("processes")["processes"]
        audio = {p["name"]: p for p in procs if p["name"].endswith(".app")}
        windows = {a["name"]: a for a in self.h.request("apps")["apps"]}
        installed = self._installed()
        now = time.time()
        with self.lock:
            changed = False
            for name, p in audio.items():
                h = self.history.get(name)
                if h is None or (p.get("path") and h.get("path") != p.get("path")):
                    self.history[name] = {"path": p.get("path"), "seen": now}
                    changed = True
                elif now - h.get("seen", 0) > 3600:
                    h["seen"] = now
                    changed = True
            if changed:
                self._save_history()
            names = (set(audio) | set(windows) | set(self.history) | set(installed)) - HIDDEN
            paths = {}
            for n in names:
                p = ((audio.get(n) or {}).get("path") or (windows.get(n) or {}).get("path")
                     or installed.get(n) or self.history.get(n, {}).get("path"))
                if p:
                    paths[n] = p
                    self.paths[n] = p
        self._load_titles(list(paths.values()))
        items = []
        for n in names:
            title = (windows.get(n) or {}).get("title") or self.titles.get(paths.get(n)) \
                or (audio.get(n) or {}).get("title") or n[:-4]
            # Mac は音を出さない常駐アプリ (Google Drive など) も Core Audio の利用者に出るので、
            # 今出力しているものだけを「再生中」にする
            playing = bool((audio.get(n) or {}).get("output"))
            items.append({"exe": n, "name": title, "audio": playing, "window": n in windows,
                          "recent": n in self.history, "installed": n in installed})
        items.sort(key=lambda d: d["name"].lower())
        return items

    def remember(self, path):
        """選んだ .app を覚える。アプリ名を返す"""
        path = path.rstrip("/")
        if not path.lower().endswith(".app") or not os.path.isdir(path):
            raise ValueError(f"アプリ (.app) ではありません: {path}")
        name = os.path.basename(path).lower()
        with self.lock:
            self.history[name] = {"path": path, "seen": time.time()}
            self.paths[name] = path
            self.icons.pop(name, None)
            self._save_history()
        return name

    def icon(self, name):
        name = name.lower()
        with self.lock:
            if name in self.icons:
                return self.icons[name]
            path = self.paths.get(name)
        if not path:
            return None
        try:
            png = self.h.request("icon", path=path, size=ICON_SIZE * 2, timeout=5).get("png")
            png = base64.b64decode(png) if png else None
        except Exception:
            png = None
        with self.lock:
            self.icons[name] = png
        return png


# ---------------------------------------------------------------- ショートカット

# Carbon の修飾キー。設定ファイルの書き方は Windows と同じ (win = Command)
MODS = {"ctrl": 0x1000, "alt": 0x0800, "shift": 0x0200, "win": 0x0100}
MOD_ORDER = ("ctrl", "alt", "shift", "win")
# Mac の仮想キーコード (US 配列の位置)
LETTERS = {"a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08,
           "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F,
           "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
           "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C,
           "9": 0x19, "0": 0x1D}
NAMED_KEYS = {
    "space": 0x31, "pageup": 0x74, "pagedown": 0x79, "end": 0x77, "home": 0x73, "left": 0x7B, "up": 0x7E,
    "right": 0x7C, "down": 0x7D, "insert": 0x72, "delete": 0x75, "minus": 0x1B, "equal": 0x18, "comma": 0x2B,
    "period": 0x2F, "slash": 0x2C, "semicolon": 0x29, "quote": 0x27, "bracketleft": 0x21, "bracketright": 0x1E,
    "backslash": 0x2A, "backquote": 0x32,
}
FKEYS = {1: 0x7A, 2: 0x78, 3: 0x63, 4: 0x76, 5: 0x60, 6: 0x61, 7: 0x62, 8: 0x64, 9: 0x65, 10: 0x6D, 11: 0x67,
         12: 0x6F, 13: 0x69, 14: 0x6B, 15: 0x71, 16: 0x6A, 17: 0x40, 18: 0x4F, 19: 0x50, 20: 0x5A}
NUMPAD = {0: 0x52, 1: 0x53, 2: 0x54, 3: 0x55, 4: 0x56, 5: 0x57, 6: 0x58, 7: 0x59, 8: 0x5B, 9: 0x5C}
SOLO_OK = {f"f{n}" for n in range(13, 21)}


def key_code(key):
    if key in LETTERS:
        return LETTERS[key]
    if key.startswith("numpad") and key[6:].isdigit() and len(key) == 7:
        return NUMPAD[int(key[6])]
    if key.startswith("f") and key[1:].isdigit() and int(key[1:]) in FKEYS:
        return FKEYS[int(key[1:])]
    return NAMED_KEYS.get(key)


def parse_hotkey(spec):
    """"ctrl+alt+p" → (Carbon の修飾キー, 仮想キーコード)。空なら None。書き方がおかしければ ValueError"""
    spec = (spec or "").strip().lower().replace(" ", "")
    if not spec:
        return None
    parts = spec.split("+")
    key = parts[-1]
    mods = 0
    for m in parts[:-1]:
        if m not in MODS:
            raise ValueError(f"修飾キーが分かりません: {m}")
        mods |= MODS[m]
    code = key_code(key)
    if code is None:
        raise ValueError(f"キーが分かりません: {key}")
    if not mods and key not in SOLO_OK:
        raise ValueError("Control・Option・Shift・Command のどれかと組み合わせてください")
    return mods, code


def normalize_hotkey(spec):
    parsed = parse_hotkey(spec)
    if parsed is None:
        return ""
    mods, _ = parsed
    key = (spec or "").strip().lower().replace(" ", "").split("+")[-1]
    return "+".join([m for m in MOD_ORDER if mods & MODS[m]] + [key])


class HotkeyThread:
    """winsys.HotkeyThread と同じ使い方。登録と待ち受けは補助プログラムがする"""

    def __init__(self, on_hotkey, helper_=None):
        self.on_hotkey = on_hotkey
        self.h = helper_ or helper()
        self.wanted = {}
        self.errors = {}
        self.h.events["hotkey"] = lambda msg: self.on_hotkey(msg.get("name"))
        self.h.on_restart.append(self._register)

    def start(self):
        pass

    def stop(self):
        pass

    def set_bindings(self, bindings):
        self.wanted = {k: v for k, v in bindings.items() if v}
        threading.Thread(target=self._register, daemon=True, name="hotkeys").start()

    def _register(self):
        errors, send = {}, {}
        for name, spec in sorted(self.wanted.items()):
            try:
                mods, code = parse_hotkey(spec)
            except ValueError as e:
                errors[name] = str(e)
                continue
            send[name] = {"mods": mods, "key": code}
        try:
            errors.update(self.h.request("hotkeys", bindings=send)["errors"])
        except Exception as e:
            log.warning("ショートカットを登録できません: %s", e)
            errors.update({n: "登録できません" for n in send})
        for name in errors:
            log.warning("ショートカットを登録できません (%s: %s)", name, self.wanted.get(name))
        self.errors = errors


# ---------------------------------------------------------------- 最前面のアプリ

NOT_GAMES = {"finder.app", "deej-tab.app", "python.app", "terminal.app", "iterm.app",
             "loginwindow", "dock.app", "controlcenter.app", "systemuiserver.app"}


def foreground():
    """最前面のアプリの (名前, pid, 全画面か)。取れなければ (None, 0, False)"""
    try:
        r = helper().request("front")
    except Exception:
        return None, 0, False
    return r.get("name"), int(r.get("pid") or 0), bool(r.get("full"))


def use_physical_pixels():
    pass


def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class GameTracker:
    """winsys.GameTracker と同じ (最後に全画面だったアプリ)"""

    def __init__(self):
        self.name = None
        self.pid = 0

    def update(self, name, pid, full):
        if full and name and name not in NOT_GAMES:
            self.name, self.pid = name, pid
        elif self.name and not pid_alive(self.pid):
            self.name, self.pid = None, 0
        return self.name


# ---------------------------------------------------------------- 自動起動 (LaunchAgent)

AGENT_LABEL = "io.github.rikuyona.deej-tab"
AGENT_FILE = os.path.join(os.path.expanduser("~/Library/LaunchAgents"), AGENT_LABEL + ".plist")


def autostart_registered():
    """登録されている起動のコマンド (引数を空白でつないだもの)。未登録なら None"""
    try:
        with open(AGENT_FILE, "rb") as f:
            d = plistlib.load(f)
        return " ".join(f'"{a}"' for a in d.get("ProgramArguments", []))
    except Exception:
        return None


def set_autostart(enabled, args):
    if not enabled:
        try:
            os.remove(AGENT_FILE)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(AGENT_FILE), exist_ok=True)
    with open(AGENT_FILE, "wb") as f:
        plistlib.dump({"Label": AGENT_LABEL, "ProgramArguments": list(args), "RunAtLoad": True,
                       "ProcessType": "Interactive"}, f)


# ---------------------------------------------------------------- 設定画面のウィンドウ

def settings_lock(path):
    """設定画面を 1 枚だけにする。取れたら True (プロセスが終わるまで持つ)。取れなければ False"""
    import fcntl
    global _lock_file
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    _lock_file = f
    return True


_lock_file = None


def settings_pid(path):
    """開いている設定画面のプロセス (なければ 0)"""
    import fcntl
    try:
        with open(path) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return 0   # 鍵が取れた = 誰も持っていない
            except OSError:
                return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def activate(pid=None):
    """そのプロセス (省略したら自分) のウィンドウを手前に出す"""
    try:
        from AppKit import NSApplication, NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        if pid is None:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return True
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return bool(app and app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception as e:
        log.warning("ウィンドウを手前に出せません: %s", e)
        return False


def set_dock_icon(show):
    """Dock にアイコンを出すか (常駐している本体は出さない、設定画面は出す)"""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(0 if show else 1)   # Regular / Accessory
    except Exception as e:
        log.warning("Dock の表示を変えられません: %s", e)


def on_main(fn):
    """fn をメインスレッドで動かす (待たない)。メニューバーのアイコン・メニューはメインスレッドでしか触れない
    (ほかのスレッドから触ると macOS がアプリを落とす)"""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn)


def system_language():
    try:
        from Foundation import NSLocale
        langs = NSLocale.preferredLanguages()
        return "ja" if langs and str(langs[0]).startswith("ja") else "en"
    except Exception:
        return None
