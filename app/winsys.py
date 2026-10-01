"""Windows の部品: グローバルホットキーと、最前面のウィンドウ (全画面かどうか) の確認"""

import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger("deej-tab")

MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODS = {"ctrl": MOD_CTRL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
NAMED_KEYS = {
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E,
    "pause": 0x13, "scrolllock": 0x91, "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE, "bracketleft": 0xDB, "bracketright": 0xDD,
    "backslash": 0xDC, "backquote": 0xC0,
}
# 修飾キーなしでも登録してよいキー (普段の入力に使わないもの)
SOLO_OK = {"pause", "scrolllock"} | {f"f{n}" for n in range(13, 25)}

WM_HOTKEY, WM_APP, WM_QUIT = 0x0312, 0x8000, 0x0012


def key_code(key):
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    if key.startswith("numpad") and key[6:].isdigit() and len(key) == 7:
        return 0x60 + int(key[6])
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    return NAMED_KEYS.get(key)


def parse_hotkey(spec):
    """"ctrl+alt+p" → (修飾キー, 仮想キーコード)。空なら None。書き方がおかしければ ValueError"""
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
    vk = key_code(key)
    if vk is None:
        raise ValueError(f"キーが分かりません: {key}")
    if not mods and key not in SOLO_OK:
        raise ValueError("Ctrl・Alt・Shift・Win のどれかと組み合わせてください")
    return mods, vk


def normalize_hotkey(spec):
    """保存用にそろえた書き方 (ctrl+alt+shift+win+キー の順)"""
    parsed = parse_hotkey(spec)
    if parsed is None:
        return ""
    mods, _ = parsed
    key = (spec or "").strip().lower().replace(" ", "").split("+")[-1]
    return "+".join([m for m in ("ctrl", "alt", "shift", "win") if mods & MODS[m]] + [key])


class HotkeyThread(threading.Thread):
    """RegisterHotKey は登録したスレッドにメッセージが届くので、専用のスレッドで待つ。
    set_bindings({名前: "ctrl+alt+p"}) で登録し直す。押されたら on_hotkey(名前) を呼ぶ"""

    def __init__(self, on_hotkey):
        super().__init__(daemon=True, name="hotkeys")
        self.on_hotkey = on_hotkey
        self.lock = threading.Lock()
        self.wanted = {}
        self.errors = {}          # {名前: 登録できなかった理由}
        self.tid = None
        self.ready = threading.Event()

    def set_bindings(self, bindings):
        with self.lock:
            self.wanted = {k: v for k, v in bindings.items() if v}
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, WM_APP, 0, 0)

    def stop(self):
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)

    def run(self):
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # このスレッドのメッセージキューを作る
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = {}   # id → 名前
        self._register(registered)
        self.ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                name = registered.get(msg.wParam)
                if name:
                    try:
                        self.on_hotkey(name)
                    except Exception as e:
                        log.warning("ホットキーの処理に失敗 (%s): %s", name, e)
            elif msg.message == WM_APP:
                self._register(registered)
        for hid in registered:
            user32.UnregisterHotKey(None, hid)

    def _register(self, registered):
        user32 = ctypes.windll.user32
        for hid in list(registered):
            user32.UnregisterHotKey(None, hid)
        registered.clear()
        with self.lock:
            wanted = dict(self.wanted)
        errors = {}
        for n, (name, spec) in enumerate(sorted(wanted.items()), start=1):
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as e:
                errors[name] = str(e)
                continue
            if user32.RegisterHotKey(None, n, mods | MOD_NOREPEAT, vk):
                registered[n] = name
            else:
                errors[name] = "ほかのアプリが使っているため登録できません"
                log.warning("ホットキーを登録できません (%s: %s)", name, spec)
        self.errors = errors


# ---------------------------------------------------------------- 最前面のウィンドウ

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
NOT_GAMES = {"explorer.exe", "deej-tab.exe", "python.exe", "pythonw.exe", "applicationframehost.exe",
             "lockapp.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe"}


def foreground():
    """最前面のウィンドウの (exe 名, pid, 全画面か)。取れなければ (None, 0, False)。
    全画面 = ウィンドウがモニター全体を覆っている (ボーダーレスも含む)"""
    import psutil
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, 0, False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name().lower()
    except Exception:
        return None, 0, False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in DESKTOP_CLASSES:
        return name, pid.value, False
    # 最大化しただけの普通のウィンドウは数えない (タイトルバーがある、または最大化の状態)
    GWL_STYLE, WS_CAPTION = -16, 0x00C00000
    if user32.IsZoomed(hwnd) or (user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION) == WS_CAPTION:
        return name, pid.value, False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
    m = mi.rcMonitor
    full = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    return name, pid.value, full


def use_physical_pixels():
    """このスレッドのウィンドウ・モニターの座標を実際のピクセルで扱う (拡大表示でずれないように)"""
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def pid_alive(pid):
    import psutil
    try:
        return pid > 0 and psutil.pid_exists(pid)
    except Exception:
        return False


class GameTracker:
    """「最後に全画面だったアプリ」(deej.game)。全画面のアプリが前に来たら覚え、
    その後は別のウィンドウが前に来ても、そのアプリが終了するまで覚えておく"""

    def __init__(self):
        self.name = None
        self.pid = 0

    def update(self, name, pid, full):
        if full and name and name not in NOT_GAMES:
            self.name, self.pid = name, pid
        elif self.name and not pid_alive(self.pid):
            self.name, self.pid = None, 0
        return self.name
