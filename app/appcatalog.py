"""割り当て先の候補になるアプリの一覧とアイコン (設定画面の「割り当てを追加」用)

- 音を出しているアプリ   : 音声セッションを持っているプロセス
- 起動中のアプリ         : タスクバーに出るウィンドウを持っているプロセス
- 最近音を出したアプリ   : 一度でも音声セッションに出たアプリ (apps.json に覚える)
- インストール済みのアプリ: スタートメニューのショートカットの行き先

どれも exe 名 (小文字) で区別する。deej の割り当てが exe 名だから。
一覧を取るのは別スレッドから (asyncio.to_thread) 呼ばれる前提。
"""

import ctypes
import glob
import io
import json
import os
import re
import sys
import threading
import time

INSTALLED_TTL = 600.0    # 秒。スタートメニューの読み直し間隔 (読むのに少し時間がかかる)
ICON_SIZE = 32

# 候補に出さないもの (Windows の部品・このアプリ自身など)
HIDDEN = {
    "applicationframehost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "searchapp.exe", "textinputhost.exe", "lockapp.exe", "systemsettings.exe",
    "explorer.exe", "msedgewebview2.exe", "deej-tab.exe", "python.exe", "pythonw.exe",
    "taskmgr.exe", "cmd.exe", "conhost.exe", "windowsterminal.exe", "openconsole.exe",
    "powershell.exe", "pwsh.exe", "rundll32.exe", "dllhost.exe", "svchost.exe",
    "audiodg.exe", "widgets.exe", "gamebar.exe", "nvcontainer.exe", "ctfmon.exe",
    "chrome_proxy.exe", "msoev.exe", "setlang.exe", "appvlp.exe", "ocpubmgr.exe",
}
# インストール済み一覧から外すショートカットの行き先 (アンインストーラー・説明書など)
JUNK_RE = re.compile(r"unins|uninstall|setup|install|update|updater|helper|crash|report|readme|"
                     r"license|help|manual|config|repair|launcher_?uninst", re.I)

STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "deej-tab")
HISTORY_FILE = os.path.join(STATE_DIR, "apps.json")
HISTORY_MAX = 200


# ---------------------------------------------------------------- exe の情報

_desc_cache = {}


def file_description(path):
    """exe のバージョン情報の「ファイルの説明」(例: Discord, Spotify)。なければ None"""
    if path in _desc_cache:
        return _desc_cache[path]
    desc = None
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if ver.GetFileVersionInfoW(path, 0, size, buf):
                ptr, n = ctypes.c_void_p(), ctypes.c_uint()
                langs = []
                if ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(n)) and n.value:
                    arr = (ctypes.c_ushort * (n.value // 2)).from_address(ptr.value)
                    langs = [f"{arr[i]:04x}{arr[i + 1]:04x}" for i in range(0, len(arr) - 1, 2)]
                for lang in langs + ["040904b0", "041104b0", "040904e4"]:
                    if ver.VerQueryValueW(buf, rf"\StringFileInfo\{lang}\FileDescription",
                                          ctypes.byref(ptr), ctypes.byref(n)) and n.value > 1:
                        s = ctypes.wstring_at(ptr.value, n.value - 1).strip()
                        if s:
                            desc = s
                            break
    except Exception:
        pass
    _desc_cache[path] = desc
    return desc


def pretty_name(exe):
    base = exe[:-4] if exe.endswith(".exe") else exe
    return base[:1].upper() + base[1:]


_icon_lock = threading.Lock()


def icon_png(path, size=ICON_SIZE, index=0):
    """exe (やアイコンファイル) のアイコンを PNG にする。取れなければ None。
    複数のスレッドで同時に取り出すとたまに失敗するので、1 つずつ順に行う"""
    with _icon_lock:
        return _icon_png(path, size, index)


def _icon_png(path, size, index):
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hicon = wintypes.HICON()
    icon_id = wintypes.UINT()
    user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                                            wintypes.UINT, wintypes.UINT]
    if user32.PrivateExtractIconsW(path, index, size, size, ctypes.byref(hicon), ctypes.byref(icon_id), 1, 0) != 1 \
            or not hicon:
        return None

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    info = ICONINFO()
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    try:
        if not user32.GetIconInfo(hicon, ctypes.byref(info)) or not info.hbmColor:
            return None
        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size,
                               biPlanes=1, biBitCount=32, biCompression=0)
        pixels = ctypes.create_string_buffer(size * size * 4)
        hdc = user32.GetDC(None)
        try:
            ok = gdi32.GetDIBits(hdc, info.hbmColor, 0, size, pixels, ctypes.byref(bih), 0)
            mask = None
            if ok and not any(pixels.raw[3::4]):
                # 古い形式のアイコン (透明度なし): マスクから透明度を作る
                mbih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size,
                                        biPlanes=1, biBitCount=32, biCompression=0)
                mask = ctypes.create_string_buffer(size * size * 4)
                gdi32.GetDIBits(hdc, info.hbmMask, 0, size, mask, ctypes.byref(mbih), 0)
        finally:
            user32.ReleaseDC(None, hdc)
        if not ok:
            return None
        from PIL import Image
        img = Image.frombuffer("RGBA", (size, size), pixels.raw, "raw", "BGRA", 0, 1)
        if mask is not None:
            alpha = bytes(0 if mask.raw[i] else 255 for i in range(0, len(mask.raw), 4))
            img.putalpha(Image.frombytes("L", (size, size), alpha))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


# ---------------------------------------------------------------- 一覧の元

def windowed_processes():
    """タスクバーに出るウィンドウを持っているプロセス {exe名: 実行ファイルのパス}"""
    import psutil
    from ctypes import wintypes
    user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    GW_OWNER, GWL_EXSTYLE = 4, -20
    WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000
    DWMWA_CLOAKED = 14
    pids = set()

    def real_pid(hwnd, pid):
        # UWP アプリのウィンドウは ApplicationFrameHost が持っている。中の子ウィンドウが本体
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def child(ch, _):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(ch, ctypes.byref(p))
            if p.value != pid:
                found.append(p.value)
                return False
            return True
        user32.EnumChildWindows(hwnd, child, 0)
        return found[0] if found else pid

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if user32.GetWindow(hwnd, GW_OWNER) and not ex & WS_EX_APPWINDOW:
            return True
        if ex & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pids.add((hwnd, pid.value))
        return True

    user32.EnumWindows(each, 0)
    result = {}
    for hwnd, pid in pids:
        try:
            p = psutil.Process(pid)
            name = p.name().lower()
            if name == "applicationframehost.exe":
                p = psutil.Process(real_pid(hwnd, pid))
                name = p.name().lower()
            try:
                path = p.exe()
            except Exception:
                path = None
            result.setdefault(name, path)
        except Exception:
            pass
    return result


def audio_processes():
    """音声セッションを持っているプロセス {exe名: 実行ファイルのパス}"""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    result = {}
    for s in AudioUtilities.GetAllSessions():
        try:
            if s.Process is None:
                continue
            name = s.Process.name().lower()
            try:
                path = s.Process.exe()
            except Exception:
                path = None
            result.setdefault(name, path)
        except Exception:
            pass
    return result


def start_menu_dirs():
    dirs = []
    for env in ("APPDATA", "ProgramData"):
        base = os.environ.get(env)
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    return dirs


def squirrel_target(target, args):
    """Discord などの Squirrel 形式: Update.exe --processStart Discord.exe → (Discord.exe のパス, exe名)"""
    m = re.search(r"--processStart\s+\"?([^\"\s]+\.exe)", args or "", re.I)
    if not m:
        return None
    exe = m.group(1)
    root = os.path.dirname(target)
    found = sorted(glob.glob(os.path.join(root, "app-*", exe)))
    return (found[-1] if found else None), exe.lower()


def installed_apps():
    """スタートメニューのショートカットから {exe名: (表示名, exeのパス, (アイコンのパス, 番号))}"""
    import comtypes
    import comtypes.client
    from comtypes.persist import IPersistFile
    from comtypes.shelllink import IShellLinkW, ShellLink
    comtypes.CoInitialize()
    windir = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    result = {}
    for base in start_menu_dirs():
        for lnk in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
            title = os.path.splitext(os.path.basename(lnk))[0]
            if JUNK_RE.search(title):
                continue
            try:
                link = comtypes.client.CreateObject(ShellLink, interface=IShellLinkW)
                link.QueryInterface(IPersistFile).Load(lnk, 0)
                target = link.GetPath(0)
                args = link.GetArguments()
                icon = link.GetIconLocation()
            except Exception:
                continue
            if not target or not target.lower().endswith(".exe"):
                continue
            if os.path.normcase(target).startswith(windir):
                continue
            exe = os.path.basename(target).lower()
            path = target
            if exe == "update.exe":
                sq = squirrel_target(target, args)
                if not sq:
                    continue
                path, exe = sq
            elif JUNK_RE.search(exe[:-4]):
                continue
            if exe in HIDDEN:
                continue
            icon_loc = (icon[0], icon[1]) if icon and icon[0] else None
            # 同じ exe に複数のショートカットがあれば、名前が短いほう (本体のことが多い)
            if exe not in result or len(title) < len(result[exe][0]):
                result[exe] = (title, path if path and os.path.exists(path) else None, icon_loc)
    return result


# ---------------------------------------------------------------- まとめ

class AppCatalog:
    def __init__(self, history_file=HISTORY_FILE):
        self.history_file = history_file
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.paths = {}          # exe名 → アイコンを取るファイル (パス, 番号)
        self.icons = {}          # exe名 → PNG (取れなかったら None)
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
        # 起動時の先読みと設定画面からの要求が重なっても、スタートメニューは 1 回だけ読む
        with self.scan_lock:
            if time.monotonic() - self.installed_at > INSTALLED_TTL or not self.installed_at:
                try:
                    self.installed = installed_apps()
                except Exception:
                    self.installed = {}
                self.installed_at = time.monotonic()
            return self.installed

    def list(self):
        """設定画面に出す一覧: [{exe, name, audio, window, recent, installed}]。名前順"""
        audio = audio_processes()
        windows = windowed_processes()
        installed = self._installed()
        now = time.time()
        with self.lock:
            changed = False
            for exe, path in audio.items():
                h = self.history.get(exe)
                if h is None or (path and h.get("path") != path):
                    self.history[exe] = {"path": path, "seen": now}
                    changed = True
                elif now - h.get("seen", 0) > 3600:
                    h["seen"] = now
                    changed = True
            if changed:
                self._save_history()
            names = set(audio) | set(windows) | set(self.history) | set(installed)
            names -= HIDDEN
            names.discard("system")
            items = []
            for exe in names:
                inst = installed.get(exe)
                path = audio.get(exe) or windows.get(exe) or (inst and inst[1]) or self.history.get(exe, {}).get("path")
                if path:
                    self.paths.setdefault(exe, (path, 0))
                elif inst and inst[2]:
                    self.paths.setdefault(exe, inst[2])
                name = (inst and inst[0]) or (path and file_description(path)) or pretty_name(exe)
                items.append({
                    "exe": exe, "name": name,
                    "audio": exe in audio, "window": exe in windows,
                    "recent": exe in self.history, "installed": inst is not None,
                })
        items.sort(key=lambda d: d["name"].lower())
        return items

    def remember(self, path):
        """ファイルを選んで割り当てた exe を覚える (アイコンと「最近」に出すため)。exe 名を返す"""
        if not path.lower().endswith(".exe") or not os.path.isfile(path):
            raise ValueError(f"exe ファイルではありません: {path}")
        exe = os.path.basename(path).lower()
        with self.lock:
            self.history[exe] = {"path": path, "seen": time.time()}
            self.paths[exe] = (path, 0)
            self.icons.pop(exe, None)
            self._save_history()
        return exe

    def icon(self, exe):
        """exe のアイコン (PNG)。一覧に出たことのない exe は None"""
        exe = exe.lower()
        with self.lock:
            if exe in self.icons:
                return self.icons[exe]
            loc = self.paths.get(exe)
        if not loc:
            return None   # まだ一覧に出ていない (覚えないので、あとで出てきたら取れる)
        try:
            png = icon_png(os.path.expandvars(loc[0]), index=loc[1])
        except Exception:
            png = None
        with self.lock:
            self.icons[exe] = png
        return png


if __name__ == "__main__":
    # 動作確認: python appcatalog.py
    sys.stdout.reconfigure(encoding="utf-8")
    t = time.monotonic()
    c = AppCatalog(history_file=os.path.join(os.environ.get("TEMP", "."), "deej-apps-test.json"))
    items = c.list()
    print(f"{len(items)} 件 ({time.monotonic() - t:.2f} 秒)")
    for d in items:
        flags = "".join(k[0].upper() if d[k] else "." for k in ("audio", "window", "recent", "installed"))
        print(f"  {flags}  {d['exe']:<32} {d['name']}")
    t = time.monotonic()
    ok = sum(1 for d in items if c.icon(d["exe"]))
    print(f"アイコン {ok}/{len(items)} ({time.monotonic() - t:.2f} 秒)")
    t = time.monotonic()
    c.list()
    print(f"2回目 {time.monotonic() - t:.2f} 秒")
