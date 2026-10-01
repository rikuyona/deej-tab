"""スライダーの LED を PC から光らせる取り決め (ファームウェア deej-6ch-led.ino と同じ)

PC → Nano (行の先頭が @。スライダーの値の行とは別):
  @L|g|m0|m1|m2|m3|m4    状態。g = 0 普通 / 1 一時停止。m = スライダー 1〜5 の光り方
                          N 普通 / O 消灯 / D 暗く (割り当て先が動いていない) / B 明滅 (100% 超え) / P 点滅 (ピックアップ待ち)
  @V|l0|l1|l2|l3|l4      音の大きさ 0〜255 (LED を音に合わせて光らせる時だけ。15 回/秒くらい)
  @P|DIM=20|BRIGHT=255…  光り方の数値 (PARAMS の名前=整数。FADE_UP / FADE_DOWN は 100 倍した整数)。
                          1 行 60 文字までに分けて送る。Nano は EEPROM に保存し、PC を外してもその光り方で動く
  @B                      起動アニメをもう一度流す (アプリで起動アニメを選んだ時に見せる)
  @C|i|lo|hi              スライダー i (0〜5) の端の位置 (キャリブレーション)。生の値 lo を 0、hi を 1023 とみなす。
                          Nano は EEPROM に保存し、LED の判断 (0 で消灯・位置で明るさ) に使う。送る値は生のまま

Nano は 3 秒 PC から何も来なければ、PC なしの時の光り方 (操作したら明るく、0 で消灯) に戻る。

光り方の数値は PARAMS にまとめてある (仮スライダーの「LED 実験」で動かして試せる)。
led_brightness() と LedSim はファームウェアの計算と同じ。直す時はファームウェアも一緒に直す。
"""

import math

LED_COUNT = 5
SLIDER_COUNT = 6           # スライダー 5 本 + ノブ (端の位置はノブも合わせる)
MODES = "NODBP"

# 光り方の数値の既定値 (ファームウェアの既定値と同じ名前・同じ値。アプリで変えると @P で Nano に送る)
PARAMS = {
    "DIM": 20,             # 普段の明るさ (0〜255)
    "BRIGHT": 255,         # 操作した直後
    "SLEEP": 4,            # 5 分操作がない時
    "INACTIVE": 6,         # 割り当て先が動いていない時
    "INACTIVE_TOUCH": 60,  # 割り当て先が動いていない時に操作した直後
    "TOUCH_MS": 2000,      # 操作してから明るいままの時間
    "SLEEP_MS": 5 * 60 * 1000,
    "BREATH_MS": 1600,     # 100% 超えの明滅の周期
    "BREATH_LOW": 60,      # 100% 超えの明滅の暗い側
    "PAUSE_MS": 3000,      # 一時停止中のゆっくりした明滅の周期
    "BLINK_MS": 250,       # ピックアップ待ちの点滅 (点灯・消灯それぞれ)
    "BLINK_LEVEL": 160,    # ピックアップ待ちの点滅の明るさ
    "FADE_UP": 0.5,        # 明るくなる速さ (10ms ごとに差のこの割合だけ近づく)
    "FADE_DOWN": 0.15,     # 暗くなる速さ
    "SWEEP_MS": 120,       # 起動アニメの 1 こまの時間
    "IDLE_STYLE": 0,       # 普段の光り方 (IDLE_STYLES の番号)
    "IDLE_MS": 4000,       # 普段の光り方の周期 (心拍はこの半分、きらめきは 1/4 ごとに光るか決める)
    "IDLE_DEPTH": 180,     # 普段の光り方の強さ (DIM に上乗せする一番明るい所。0〜255)
    "TWINKLE_CHANCE": 20,  # きらめき: 1 こまで各 LED が光る確率 (%)
    "BOOT_STYLE": 0,       # 起動アニメ (BOOT_STYLES の番号)
    "TOUCH_STYLE": 0,      # 操作した時の光り方 (TOUCH_STYLES の番号)
    "RIPPLE_SPREAD": 60,   # 波紋: 1 つ離れるごとに残る明るさ (%)。端まで全部に広がる
}
IDLE_STYLES = ["点灯したまま", "呼吸", "流れる波", "きらめき", "鼓動", "往復する光", "スライダーの位置に合わせる"]
BOOT_STYLES = ["左から右へ流れる", "中央から広がる", "全体がふわっと点く", "左右に往復する"]
TOUCH_STYLES = ["明るく光る", "波紋（端まで広がる）", "スライダーの位置に合わせる"]

# 数値の範囲 (アプリ・仮スライダーの画面と、Nano が受け取る時の範囲。ファームウェアの paramTable と同じ)
PARAM_LIMITS = {
    "DIM": (0, 255), "BRIGHT": (20, 255), "SLEEP": (0, 255), "INACTIVE": (0, 255), "INACTIVE_TOUCH": (0, 255),
    "TOUCH_MS": (200, 6000), "SLEEP_MS": (10000, 3600000), "BREATH_MS": (300, 5000), "BREATH_LOW": (0, 255),
    "PAUSE_MS": (500, 8000), "BLINK_MS": (60, 1000), "BLINK_LEVEL": (10, 255),
    "FADE_UP": (0.02, 1.0), "FADE_DOWN": (0.01, 1.0), "SWEEP_MS": (30, 500),
    "IDLE_STYLE": (0, len(IDLE_STYLES) - 1), "IDLE_MS": (800, 10000), "IDLE_DEPTH": (0, 255),
    "TWINKLE_CHANCE": (0, 100), "BOOT_STYLE": (0, len(BOOT_STYLES) - 1), "TOUCH_STYLE": (0, len(TOUCH_STYLES) - 1),
    "RIPPLE_SPREAD": (10, 95),
}
FLOAT_PARAMS = ("FADE_UP", "FADE_DOWN")   # Nano には 100 倍した整数で送る
# 光り方ごとに覚える数値 (光り方を切り替えると、その光り方で前に使っていた数値に戻す)
STYLE_GROUPS = {
    "IDLE_STYLE": ("DIM", "IDLE_DEPTH", "IDLE_MS", "TWINKLE_CHANCE"),
    "BOOT_STYLE": ("SWEEP_MS",),
    "TOUCH_STYLE": ("BRIGHT", "TOUCH_MS", "RIPPLE_SPREAD"),
}
PARAM_LINE_MAX = 60                        # Nano の受信バッファ (64) に収まる長さ
# 端の位置 (キャリブレーション) の範囲 (ファームウェアの handleCalibration と同じ)
CAL_DEFAULT = (0, 1023)
CAL_LO_MAX = 400           # 下端はここまで
CAL_HI_MIN = 623           # 上端はここから
CAL_MIN_SPAN = 300         # 下端と上端はこれ以上離れていないといけない
BOOT_LINE = "@B\n"

# 昔からの名前 (テスト・ほかのコードから使う)
DIM, BRIGHT, SLEEP, INACTIVE = PARAMS["DIM"], PARAMS["BRIGHT"], PARAMS["SLEEP"], PARAMS["INACTIVE"]
TOUCH_MS, SLEEP_MS = PARAMS["TOUCH_MS"], PARAMS["SLEEP_MS"]
PC_TIMEOUT_MS = 3000
METER_TIMEOUT_MS = 300
BREATH_MS, PAUSE_MS, BLINK_MS = PARAMS["BREATH_MS"], PARAMS["PAUSE_MS"], PARAMS["BLINK_MS"]
TOUCH_THRESHOLD = 8


def status_line(paused, modes):
    return "@L|%d|%s\n" % (1 if paused else 0, "|".join(modes))


def meter_line(levels):
    return "@V|%s\n" % "|".join(str(max(0, min(255, int(v)))) for v in levels)


def normalize_params(raw):
    """知っている名前だけを範囲に収めて返す (設定ファイル・画面から来た数値用)"""
    out = {}
    for k, v in (raw or {}).items():
        if k not in PARAM_LIMITS:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = PARAM_LIMITS[k]
        v = max(lo, min(hi, v))
        out[k] = round(v, 2) if k in FLOAT_PARAMS else int(round(v))
    return out


def normalize_presets(raw):
    """光り方ごとに覚えた数値 {"IDLE_STYLE": {"5": {"DIM": 20, …}}} を、知っているものだけ範囲に収めて返す"""
    out = {}
    for style_key, keys in STYLE_GROUPS.items():
        styles = (raw or {}).get(style_key)
        if not isinstance(styles, dict):
            continue
        hi = PARAM_LIMITS[style_key][1]
        for n, vals in styles.items():
            if str(n).isdigit() and int(n) <= hi and isinstance(vals, dict):
                v = {k: x for k, x in normalize_params(vals).items() if k in keys}
                if v:
                    out.setdefault(style_key, {})[str(int(n))] = v
    return out


def apply_change(params, presets, change):
    """光り方の数値を変える。光り方を切り替える時は、今の光り方の数値を presets に覚えてから、
    切り替え先で前に使っていた数値に戻す (初めての光り方なら今の数値のまま)。
    params・presets を書き換え、実際に変わった数値を返す"""
    out = dict(change)
    for style_key, keys in STYLE_GROUPS.items():
        if style_key not in change or change[style_key] == params[style_key]:
            continue
        presets.setdefault(style_key, {})[str(params[style_key])] = {k: params[k] for k in keys}
        saved = presets[style_key].get(str(change[style_key]), {})
        for k in keys:
            if k not in change and k in saved:
                out[k] = saved[k]
    out = {k: v for k, v in out.items() if params.get(k) != v}
    params.update(out)
    return out


def valid_calibration(lo, hi):
    return 0 <= lo <= CAL_LO_MAX and CAL_HI_MIN <= hi <= 1023 and hi - lo >= CAL_MIN_SPAN


def normalize_calibration(raw):
    """端の位置 {番号: [下端, 上端]} を読む。おかしなもの・既定 (0〜1023) と同じものは捨てる"""
    out = {}
    for k, v in (raw or {}).items():
        try:
            idx, lo, hi = int(k), int(v[0]), int(v[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if 0 <= idx < SLIDER_COUNT and valid_calibration(lo, hi) and (lo, hi) != CAL_DEFAULT:
            out[idx] = (lo, hi)
    return out


def calibrated(raw, lo, hi):
    """生の値 → 端の位置を合わせた値 (0〜1023)。ファームウェアの calibrated() と同じ整数の計算"""
    if raw <= lo:
        return 0
    if raw >= hi:
        return 1023
    return (raw - lo) * 1023 // (hi - lo)


def calibration_lines(cal):
    """端の位置を Nano に送る行 (@C|番号|下端|上端)。合わせていないスライダーは既定 (0〜1023) を送る"""
    return ["@C|%d|%d|%d\n" % ((i,) + tuple(cal.get(i, CAL_DEFAULT))) for i in range(SLIDER_COUNT)]


def params_lines(p):
    """光り方の数値を Nano に送る行 (@P|名前=値|…)。1 行 PARAM_LINE_MAX 文字まで"""
    lines, cur = [], "@P"
    for k in PARAMS:
        v = p.get(k, PARAMS[k])
        item = "|%s=%d" % (k, round(v * 100) if k in FLOAT_PARAMS else int(v))
        if len(cur) + len(item) + 1 > PARAM_LINE_MAX:
            lines.append(cur + "\n")
            cur = "@P"
        cur += item
    lines.append(cur + "\n")
    return lines


def parse_line(line):
    """@L / @V / @P / @B / @C の行を読む (ファームウェアと仮スライダー用)。
    ("L", paused, modes) / ("V", levels) / ("P", {名前: 値}) / ("B",) / ("C", 番号, 下端, 上端) / None"""
    line = line.strip()
    if line == "@B":
        return ("B",)
    if line.startswith("@P|"):
        raw = {}
        for item in line[3:].split("|"):
            k, _, v = item.partition("=")
            if k in PARAM_LIMITS and v.lstrip("-").isdigit():
                raw[k] = int(v) / 100 if k in FLOAT_PARAMS else int(v)
        return "P", normalize_params(raw)
    parts = line.split("|")
    if parts[0] == "@C" and len(parts) == 4 and all(p.isdigit() for p in parts[1:]):
        idx, lo, hi = (int(p) for p in parts[1:])
        if idx < SLIDER_COUNT and valid_calibration(lo, hi):
            return "C", idx, lo, hi
        return None
    if parts[0] == "@L" and len(parts) == 2 + LED_COUNT and all(m in MODES for m in parts[2:]):
        return "L", parts[1] == "1", parts[2:]
    if parts[0] == "@V" and len(parts) == 1 + LED_COUNT and all(p.isdigit() for p in parts[1:]):
        return "V", [min(255, int(p)) for p in parts[1:]]
    return None


def triangle(t, period, lo, hi):
    """lo → hi → lo を period ミリ秒で繰り返す三角波 (ファームウェアと同じ整数の計算)"""
    period = max(2, int(period))
    x = int(t) % period
    half = period // 2
    up = x if x < half else period - x
    return int(lo + (hi - lo) * up // half)


def clamp8(v):
    return max(0, min(255, int(v)))


def twinkle_hash(slot, i):
    """きらめきの乱数 (ファームウェアの unsigned long と同じく 32 ビットで回る)"""
    h = (slot * 1103515245 + i * 2654435761 + 12345) & 0xFFFFFFFF
    return (h >> 16) & 0x7FFF


def heartbeat(now, period, amp):
    """心拍: ドクン (強)、ドクン (弱) の 2 回"""
    x = int(now) % max(400, int(period))
    if x < 120:
        return amp * (x if x < 60 else 120 - x) // 60
    if 200 <= x < 320:
        y = x - 200
        return amp * 6 // 10 * (y if y < 60 else 120 - y) // 60
    return 0


def idle_level(now, base, i, p, raw=0):
    """普段の明るさ。IDLE_STYLE で光り方を変える (5 分放置の時は一定)"""
    style = int(p["IDLE_STYLE"])
    d = int(p["IDLE_DEPTH"])
    period = int(p["IDLE_MS"])
    if style == 0 or base <= p["SLEEP"]:
        return base
    if style == 1:   # ゆっくり呼吸 (全部そろって)
        return clamp8(triangle(now, period, base, base + d))
    if style == 2:   # 流れる波 (1 本ずつずれる)
        return clamp8(triangle(now + i * period // (LED_COUNT * 2), period, base, base + d))
    if style == 3:   # きらめき: 1/4 周期ごとに、ときどき 1 本がふわっと光る
        slot_ms = max(50, period // 4)
        if twinkle_hash(int(now) // slot_ms, i) % 100 < p["TWINKLE_CHANCE"]:
            return clamp8(base + triangle(now, slot_ms, 0, d))
        return base
    if style == 4:   # 心拍
        return clamp8(base + heartbeat(now, period // 2, d))
    if style == 5:   # スキャナー: 明るい点が左右に往復する (両隣にも尾を引く)
        pos = triangle(now, period, 0, (LED_COUNT - 1) * 100)
        dist = abs(i * 100 - pos)
        return clamp8(base + d * (150 - dist) // 150) if dist < 150 else base
    if style == 6:   # 位置で明るさ (上げているほど明るい)
        return clamp8(raw * (base + d) // 1023)
    return base


def boot_level(i, t, p):
    """起動アニメの明るさ。終わっていたら None。t = 起動してからの時間 (ms)"""
    step = max(10, int(p["SWEEP_MS"]))
    style = int(p["BOOT_STYLE"])
    bright, dim = int(p["BRIGHT"]), int(p["DIM"])
    k = int(t) // step
    if style == 1:   # 中央から広がる
        if k >= 3:
            return None
        return bright if abs(i - 2) == k else dim
    if style == 2:   # 全体がふわっと点く (前半で明るくなり、後半で普段の明るさへ)
        total = step * 6
        half = total // 2
        if t >= total:
            return None
        if t < half:
            return bright * int(t) // half
        return bright - (bright - dim) * (int(t) - half) // (total - half)
    if style == 3:   # 往復する
        order = [0, 1, 2, 3, 4, 3, 2, 1, 0]
        if k >= len(order):
            return None
        return bright if order[k] == i else dim
    if k >= LED_COUNT:   # 左から右へ流れる
        return None
    return bright if k == i else dim


def touch_level(now, raw, p):
    """操作した直後の明るさ。位置を表示なら、動かしている間は位置に合わせる"""
    if int(p["TOUCH_STYLE"]) == 2:
        return max(int(p["DIM"]), raw * int(p["BRIGHT"]) // 1023)
    return int(p["BRIGHT"])


def ripple_levels(now, touched, p, modes=None):
    """波紋: 操作した LED から端まで全部に広げる。1 つ離れるごとに RIPPLE_SPREAD % ずつ弱くなる。
    TOUCH_STYLE が波紋の時だけ。modes (PC からの指示) で割り当て先が動いていない (D) LED を操作した時は、
    その LED と同じ INACTIVE_TOUCH から広げる (真ん中より隣が明るくならないように)"""
    out = [0] * LED_COUNT
    if int(p["TOUCH_STYLE"]) != 1:
        return out
    spread = int(p["RIPPLE_SPREAD"])
    for j in range(LED_COUNT):
        if now - touched[j] < p["TOUCH_MS"]:
            peak = int(p["INACTIVE_TOUCH"]) if modes and modes[j] == "D" else int(p["BRIGHT"])
            for i in range(LED_COUNT):
                v = peak
                for _ in range(abs(i - j)):
                    v = v * spread // 100
                if i != j:
                    out[i] = max(out[i], v)
    return out


def led_brightness(now, raw, touched, idle_since, pc=None, meter=None, p=None, i=0, ripple=0):
    """LED 1 本の明るさ (0〜255)。

    now: 今の時刻 (ms)、raw: スライダーの値 (0〜1023)、touched: 最後に動かした時刻、
    idle_since: 全スライダーの最後の操作時刻 (5 分で減光)、
    pc: PC からの指示 (paused, mode, 受け取った時刻) または None、meter: (音の大きさ, 受け取った時刻) または None、
    p: 光り方の数値 (省くと PARAMS)、i: 何本目の LED か (流れる波などで使う)、
    ripple: 隣の LED を操作した時の明るさ (波紋)"""
    p = p or PARAMS
    sleeping = now - idle_since > p["SLEEP_MS"]
    base = p["SLEEP"] if sleeping else idle_level(now, p["DIM"], i, p, raw)
    lit = now - touched < p["TOUCH_MS"]
    if pc is None or now - pc[2] > PC_TIMEOUT_MS:
        # PC なし: 0 で消灯、動かした直後は明るく
        if raw < 8:
            return 0
        return touch_level(now, raw, p) if lit else max(base, ripple)
    paused, mode = pc[0], pc[1]
    if paused:
        return triangle(now, p["PAUSE_MS"], 0, p["DIM"])
    if mode == "O":
        return 0
    if mode == "P":
        return p["BLINK_LEVEL"] if (now // p["BLINK_MS"]) % 2 == 0 else 0
    if mode == "D":
        # 割り当て先が動いていなくても、隣を操作した時の波紋は出す
        return max(p["INACTIVE_TOUCH"] if lit else p["INACTIVE"], ripple)
    if lit:
        return touch_level(now, raw, p)
    base = max(base, ripple)
    if mode == "B":
        return triangle(now, p["BREATH_MS"], p["BREATH_LOW"], p["BRIGHT"])
    if meter is not None and now - meter[1] <= METER_TIMEOUT_MS:
        return base + meter[0] * (p["BRIGHT"] - base) // 255
    return base


class LedSim:
    """ファームウェアの LED 全体の動き (起動時の流れ・なめらかな変化) をそのまま再現する。
    step() を 10ms ごとに呼ぶ (ファームウェアの loop と同じ間隔)"""

    STEP_MS = 10

    def __init__(self, now, raws, p=None):
        # 渡された数値はコピーせずにそのまま見る (仮スライダーで動かした数値がすぐ効くように)
        self.p = p if p is not None else dict(PARAMS)
        self.raw = list(raws)
        self.last_touch_value = list(raws)
        self.touched = [now - self.p["TOUCH_MS"]] * LED_COUNT
        self.idle_since = now
        self.shown = [float(self.p["DIM"])] * LED_COUNT
        self.pc = None          # (paused, [modes], 受け取った時刻)
        self.meter = None       # ([levels], 受け取った時刻)
        self.sweep_start = now  # 起動アニメの始まり
        self.t = now            # advance() で進めた時刻
        self.levels = [int(v) for v in self.shown]
        self.cal = [CAL_DEFAULT] * SLIDER_COUNT   # 端の位置 (@C で受け取る)

    def boot(self, now):
        self.sweep_start = now

    def receive(self, line, now):
        r = parse_line(line)
        if r and r[0] == "L":
            self.pc = (r[1], r[2], now)
        elif r and r[0] == "V":
            self.meter = (r[1], now)
        elif r and r[0] == "P":
            self.p.update(r[1])
        elif r and r[0] == "B":
            self.boot(now)
        elif r and r[0] == "C":
            self.cal[r[1]] = (r[2], r[3])

    def advance(self, now, catch_up=1000):
        """now まで 10ms ずつ進めて、最後の明るさを返す (まとめて呼ぶ時用。遅れすぎた分は飛ばす)"""
        if now - self.t > catch_up:
            self.t = now - catch_up
        while self.t + self.STEP_MS <= now:
            self.t += self.STEP_MS
            self.levels = self.step(self.t)
        return self.levels

    def set_raw(self, raws, now):
        for i, v in enumerate(raws):
            self.raw[i] = v
            if abs(v - self.last_touch_value[i]) >= TOUCH_THRESHOLD:
                self.last_touch_value[i] = v
                if i < LED_COUNT:
                    self.touched[i] = now
                self.idle_since = now

    def step(self, now):
        """10ms 進めて、各 LED の明るさ (0〜255) を返す"""
        p = self.p
        boot = [boot_level(i, now - self.sweep_start, p) for i in range(LED_COUNT)]
        if boot[0] is not None:
            self.shown = [float(v) for v in boot]
            return [int(v) for v in self.shown]
        pc_live = self.pc is not None and now - self.pc[2] <= PC_TIMEOUT_MS
        ripple = ripple_levels(now, self.touched, p, self.pc[1] if pc_live else None)
        out = []
        for i in range(LED_COUNT):
            pc = (self.pc[0], self.pc[1][i], self.pc[2]) if self.pc else None
            meter = (self.meter[0][i], self.meter[1]) if self.meter else None
            # 明るさの判断は端の位置を合わせた値で (操作したかどうかは生の値で見る。ファームウェアと同じ)
            pos = calibrated(self.raw[i], *self.cal[i])
            target = led_brightness(now, pos, self.touched[i], self.idle_since, pc, meter, p, i,
                                    ripple[i])
            k = p["FADE_UP"] if target > self.shown[i] else p["FADE_DOWN"]
            if pc_live and (self.pc[1][i] == "P" or self.pc[0]):
                k = 1.0   # 点滅・一時停止の明滅はそのまま出す
            self.shown[i] += (target - self.shown[i]) * k
            out.append(int(self.shown[i] + 0.5))
        return out


def perceived(level):
    """LED の明るさ (PWM 0〜255) を画面に出す時の色の強さ (0〜1)。
    LED は PWM が小さい所でも目には明るく見えるので、画面の明るさに直す (ガンマ 2.2 の逆)"""
    return math.pow(max(0, min(255, level)) / 255.0, 1 / 2.2)


def firmware_constants(p):
    """ファームウェアの既定値に貼る行 (FADE_UP / FADE_DOWN は 100 倍した整数)"""
    return "\n".join(f"long {k} = {round(p[k] * 100) if k in FLOAT_PARAMS else int(p[k])};" for k in PARAMS)
