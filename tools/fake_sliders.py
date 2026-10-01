"""仮スライダー: Nano の代わりに deej-tab へスライダー値を送るテスト用ツール

config.yaml の com_port を socket://127.0.0.1:9000 にすると、deej-tab がここにつながる。
送信形式はファームウェアと同じ "v0|v1|...|v5\\n" (0〜1023)。

テスト用の機能:
- 0% / 50% / 100% に全部そろえる
- ノイズを混ぜる (実機のガリや揺れの再現。ノイズ除去の強さを試せる)
- 送信を止める (Nano が固まった状態の再現。deej-tab が 5 秒で繋ぎ直すかを試せる)
- 別のポートで待ち受ける: fake_sliders.py --port 9100
- LED: ファームウェアと同じ計算 (app/leds.py の LedSim) で光らせて見せる
- LED 実験: 光り方を手で選んだり、明るさ・速さの数値を動かしたりして、その場で見比べる。
  気に入った数値は「書き出す」で led_params.json に保存し、ファームウェアに貼る const の行を出す
"""

import argparse
import json
import math
import os
import queue
import random
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import leds  # noqa: E402  (ファームウェアと同じ LED の計算)

HOST = "127.0.0.1"
CHANNELS = 6
INTERVAL = 0.02  # 秒
NAMES = ["1", "2", "3", "4", "5", "ノブ"]
PARAMS_FILE = os.path.join(HERE, "led_params.json")

MODE_NAMES = {"N": "普通", "O": "消灯", "D": "暗い (割り当て先なし)", "B": "明滅 (100% 超え)", "P": "点滅 (ピックアップ待ち)"}

# 動かせる数値: (名前, 説明, 最小, 最大, 刻み)
PARAM_SLIDERS = [
    ("DIM", "操作していないときの明るさ", 0, 255, 1),
    ("BRIGHT", "操作した直後の明るさ", 20, 255, 1),
    ("SLEEP", "5 分放置した時の明るさ", 0, 40, 1),
    ("INACTIVE", "割り当て先なしの明るさ", 0, 60, 1),
    ("TOUCH_MS", "操作後に明るい時間 (ms)", 200, 6000, 100),
    ("FADE_UP", "明るくなる速さ", 0.02, 1.0, 0.01),
    ("FADE_DOWN", "暗くなる速さ", 0.01, 1.0, 0.01),
    ("BREATH_MS", "100% 超えの明滅の周期 (ms)", 300, 5000, 50),
    ("BREATH_LOW", "100% 超えの明滅の暗い側", 0, 200, 1),
    ("BLINK_MS", "ピックアップ待ちの点滅 (ms)", 60, 1000, 10),
    ("BLINK_LEVEL", "ピックアップ待ちの明るさ", 10, 255, 1),
    ("PAUSE_MS", "一時停止中の明滅の周期 (ms)", 500, 8000, 100),
    ("SWEEP_MS", "起動アニメの 1 こま (ms)", 30, 500, 10),
    ("IDLE_MS", "アニメの速さ (1 回の長さ ms)", 800, 10000, 100),
    ("IDLE_DEPTH", "アニメの明るさ (足す分)", 0, 255, 1),
    ("TWINKLE_CHANCE", "きらめく割合 (%)", 0, 100, 1),
    ("RIPPLE_SPREAD", "波紋の広がり (%)", 10, 95, 1),
]

# 起動時の位置は全部 50% (100% だと大きすぎる)
values = [512] * CHANNELS
lock = threading.Lock()
status = "起動中"
noise = 0          # 送る値に混ぜる揺れ (±)
paused = False     # True の間は何も送らない
from_pc = queue.Queue()   # deej-tab から届いた LED の行


def now_ms():
    return int(time.monotonic() * 1000)


def server(port):
    global status
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows の SO_REUSEADDR は同じポートの二重待ち受けを許してしまい、
    # 古い仮スライダーが接続を横取りしたまま気付けなくなるので排他にする
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        srv.bind((HOST, port))
    except OSError:
        status = f"ポート {port} は使用中です (仮スライダーがもう起動していませんか？)"
        return
    srv.listen(1)
    while True:
        status = f"deej-tab の接続待ち ({HOST}:{port})"
        conn, _ = srv.accept()
        conn.settimeout(INTERVAL)
        status = "deej-tab に接続中"
        buf = b""
        try:
            while True:
                if not paused:
                    with lock:
                        vals = [clamp(v + random.randint(-noise, noise)) if noise else v for v in values]
                    conn.sendall(("|".join(map(str, vals)) + "\n").encode())
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    *lines, buf = (buf + data).split(b"\n")
                    for line in lines:
                        from_pc.put(line.decode("ascii", "ignore"))
                except socket.timeout:
                    pass
        except OSError:
            pass
        conn.close()


def clamp(v):
    return max(0, min(1023, v))


def no_activate(root):
    """クリックしても最前面のアプリにならないようにする (deej.current を試せるように)。
    操作中に隠れないよう、常に手前に表示する"""
    root.attributes("-topmost", True)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id())
        GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE)
    except Exception:
        pass


def load_params():
    p = dict(leds.PARAMS)
    try:
        with open(PARAMS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        p.update({k: v for k, v in saved.items() if k in p})
    except Exception:
        pass
    return p


def led_color(level):
    """LED の明るさを画面の色に (実物の見え方に近づける)。赤い LED"""
    x = leds.perceived(level)
    off = (0x2a, 0x12, 0x12)
    on = (0xff, 0x4d, 0x4d)
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * x) for a, b in zip(off, on))


def main():
    global noise, paused
    ap = argparse.ArgumentParser(description="仮スライダー")
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()

    root = tk.Tk()
    root.title(f"仮スライダー ({args.port})")
    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, anchor="w").grid(row=0, column=0, columnspan=CHANNELS, sticky="we", padx=8, pady=(8, 0))

    params = load_params()
    sim = leds.LedSim(now_ms(), list(values), params)

    # ---------- スライダー
    scales = []
    for i in range(CHANNELS):
        pct = tk.StringVar()

        def on_change(v, i=i, pct=pct):
            n = int(float(v))
            with lock:
                values[i] = n
            pct.set(f"{n / 1023 * 100:.0f}%")
        s = tk.Scale(root, from_=1023, to=0, length=240, showvalue=False, command=on_change)
        s.set(values[i])
        on_change(values[i])
        s.grid(row=1, column=i, padx=6, pady=(8, 0))
        scales.append(s)
        tk.Label(root, textvariable=pct, width=5).grid(row=2, column=i)
        tk.Label(root, text=f"A{i} ({NAMES[i]})", fg="#666").grid(row=3, column=i)

    # ---------- LED (実物の見え方に近い色で)
    led_canvas = []
    for i in range(leds.LED_COUNT):
        c = tk.Canvas(root, width=34, height=34, highlightthickness=0, bg="#16181d")
        c.grid(row=4, column=i, pady=(4, 2))
        glow = c.create_oval(1, 1, 33, 33, fill="#16181d", outline="")
        dot = c.create_oval(8, 8, 26, 26, fill="#2a1212", outline="#444")
        led_canvas.append((c, glow, dot))
    led_text = [tk.StringVar() for _ in range(leds.LED_COUNT)]
    for i in range(leds.LED_COUNT):
        tk.Label(root, textvariable=led_text[i], fg="#888", font=("Consolas", 8)).grid(row=5, column=i)

    # ---------- 送り方のテスト
    tools = tk.Frame(root)
    tools.grid(row=6, column=0, columnspan=CHANNELS, sticky="we", padx=8, pady=(6, 4))
    for label, v in (("全部 0%", 0), ("全部 50%", 512), ("全部 100%", 1023)):
        tk.Button(tools, text=label, command=lambda v=v: [s.set(v) for s in scales]).pack(side="left", padx=(0, 4))
    noise_var = tk.IntVar(value=0)

    def on_noise(*_):
        global noise
        try:
            noise = max(0, min(100, noise_var.get()))
        except tk.TclError:
            pass   # 入力途中 (空など)
    noise_var.trace_add("write", on_noise)
    tk.Label(tools, text="  ノイズ ±").pack(side="left")
    tk.Spinbox(tools, from_=0, to=40, width=3, textvariable=noise_var).pack(side="left")
    paused_var = tk.BooleanVar(value=False)

    def on_pause():
        global paused
        paused = paused_var.get()
    tk.Checkbutton(tools, text="送信を止める", variable=paused_var, command=on_pause).pack(side="left", padx=(8, 0))

    # ---------- LED 実験
    lab = ttk.LabelFrame(root, text="LED 実験 (ファームウェアと同じ計算。数値を動かすとその場で変わります)")
    lab.grid(row=7, column=0, columnspan=CHANNELS, sticky="we", padx=8, pady=(4, 8))

    # 既定は「手で選ぶ (全部 普通)」: 割り当てていなくても光り方を試せるように
    source = tk.StringVar(value="manual")
    row = ttk.Frame(lab)
    row.pack(fill="x", padx=6, pady=(4, 2))
    # PC (deej-tab) から LED にどんな指示が来ているか。普段の光り方のアニメが出るのは「普通」の LED だけ
    ttk.Label(row, text="LED への指示:").pack(side="left")
    ttk.Radiobutton(row, text="本物の deej-tab に合わせる", value="pc", variable=source).pack(side="left", padx=4)
    ttk.Radiobutton(row, text="状態を手で選ぶ ↓", value="manual", variable=source).pack(side="left", padx=4)
    ttk.Radiobutton(row, text="PC 未接続の時", value="none", variable=source).pack(side="left", padx=4)
    ttk.Label(lab, foreground="#888", justify="left", text=(
        "本物の deej-tab に合わせる: deej-tab が送る状態と、設定画面の「LED の光り方」の数値をそのまま使う (割り当てのない LED は暗い)\n"
        "状態を手で選ぶ: 各 LED の状態を下で決めて、光り方の数値をこの画面で試す\n"
        "PC 未接続の時: deej-tab が起動していない時の Nano の動き (全部 普通、一番下にすると消灯)")).pack(anchor="w", padx=8)

    manual = ttk.Frame(lab)
    manual.pack(fill="x", padx=6, pady=2)
    mode_vars = []
    for i in range(leds.LED_COUNT):
        v = tk.StringVar(value=MODE_NAMES["N"])
        ttk.Label(manual, text=f"LED {i + 1}").grid(row=0, column=i, sticky="w")
        ttk.Combobox(manual, textvariable=v, values=list(MODE_NAMES.values()), state="readonly", width=17).grid(
            row=1, column=i, padx=(0, 4))
        mode_vars.append(v)
    opts = ttk.Frame(lab)
    opts.pack(fill="x", padx=6, pady=2)
    manual_pause = tk.BooleanVar(value=False)
    manual_meter = tk.BooleanVar(value=False)
    ttk.Checkbutton(opts, text="一時停止中", variable=manual_pause).pack(side="left")
    ttk.Checkbutton(opts, text="音に合わせる (ダミーの音を流す)", variable=manual_meter).pack(side="left", padx=10)

    # アニメーションの種類: 普段 / 起動時 / 操作した時
    styles = ttk.Frame(lab)
    styles.pack(fill="x", padx=6, pady=(4, 2))
    style_vars = {}
    presets = {}   # 光り方ごとに覚えた数値 (この画面を閉じるまで)
    param_vars = {}
    for key, title, names in (("IDLE_STYLE", "操作していないとき", leds.IDLE_STYLES),
                              ("BOOT_STYLE", "起動したとき", leds.BOOT_STYLES),
                              ("TOUCH_STYLE", "操作したとき", leds.TOUCH_STYLES)):
        ttk.Label(styles, text=title + ":").pack(side="left", padx=(0, 4))
        var = tk.StringVar(value=names[int(params[key])])

        def on_style(*_, key=key, var=var, names=names):
            # 光り方ごとに明るさなどを覚える (アプリの設定画面と同じ)
            changed = leds.apply_change(params, presets, {key: names.index(var.get())})
            for k, v in changed.items():
                if k in param_vars:
                    param_vars[k].set(v)
            if key == "BOOT_STYLE":
                sim.boot(now_ms())   # 選んだらすぐ見せる
        var.trace_add("write", on_style)
        ttk.Combobox(styles, textvariable=var, values=names, state="readonly", width=18).pack(side="left", padx=(0, 14))
        style_vars[key] = (var, names)

    grid = ttk.Frame(lab)
    grid.pack(fill="x", padx=6, pady=4)
    for n, (key, desc, lo, hi, step) in enumerate(PARAM_SLIDERS):
        r, c = divmod(n, 3)
        cell = ttk.Frame(grid)
        cell.grid(row=r, column=c, sticky="we", padx=4, pady=1)
        var = tk.DoubleVar(value=params[key])
        shown = tk.StringVar()

        def on_param(*_, key=key, var=var, shown=shown, step=step):
            v = var.get()
            v = round(v / step) * step
            params[key] = v if isinstance(step, float) else int(v)
            shown.set(f"{params[key]:.2f}" if isinstance(step, float) else str(params[key]))
        var.trace_add("write", on_param)
        on_param()
        ttk.Label(cell, text=desc, width=28).pack(side="left")
        ttk.Scale(cell, from_=lo, to=hi, variable=var, length=150).pack(side="left")
        ttk.Label(cell, textvariable=shown, width=6).pack(side="left")
        param_vars[key] = var

    btns = ttk.Frame(lab)
    btns.pack(fill="x", padx=6, pady=(2, 6))

    def replay_boot():
        sim.boot(now_ms())

    def fake_sleep():
        sim.idle_since = now_ms() - params["SLEEP_MS"] - 1

    def reset_params():
        presets.clear()
        for k, var in param_vars.items():
            var.set(leds.PARAMS[k])
        for k, (var, names) in style_vars.items():
            var.set(names[leds.PARAMS[k]])

    def export():
        with open(PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
        text = leds.firmware_constants(params)
        root.clipboard_clear()
        root.clipboard_append(text)
        w = tk.Toplevel(root)
        w.title("書き出した数値")
        tk.Label(w, text=f"保存しました: {PARAMS_FILE}\nファームウェアに貼る行 (クリップボードにもコピーしました):",
                 justify="left").pack(anchor="w", padx=8, pady=(8, 2))
        t = tk.Text(w, width=48, height=len(text.splitlines()) + 1, font=("Consolas", 9))
        t.insert("1.0", text)
        t.pack(padx=8, pady=(0, 8))

    ttk.Button(btns, text="起動アニメを再生", command=replay_boot).pack(side="left")
    ttk.Button(btns, text="5 分放置を再現", command=fake_sleep).pack(side="left", padx=4)
    ttk.Button(btns, text="既定に戻す", command=reset_params).pack(side="left", padx=4)
    ttk.Button(btns, text="書き出す", command=export).pack(side="left", padx=4)
    ttk.Label(btns, text="※画面の色は、LED が目にどう見えるかに近づけた目安です", foreground="#888").pack(side="left", padx=10)

    no_activate(root)
    threading.Thread(target=server, args=(args.port,), daemon=True).start()

    # ---------- 光り方の計算 (10ms ごと、ファームウェアの loop と同じ) と描画
    name_to_mode = {v: k for k, v in MODE_NAMES.items()}
    state = {"t": now_ms(), "manual_at": 0, "meter_at": 0, "phase": [random.random() * 6 for _ in range(5)]}

    def tick():
        t = now_ms()
        src = source.get()
        # deej-tab から届いた行 (手で選んでいる間は読み捨てる)
        got_params = False
        while not from_pc.empty():
            line = from_pc.get_nowait()
            if src == "pc":
                sim.receive(line, t)
                got_params = got_params or line.startswith("@P|")
        if got_params:
            # 設定画面で変えた数値をこの画面のスライダーにも出す (変わったものだけ)
            for k, var in param_vars.items():
                if abs(var.get() - params[k]) > 1e-9:
                    var.set(params[k])
            for k, (var, names) in style_vars.items():
                if var.get() != names[int(params[k])]:
                    var.set(names[int(params[k])])
        if src == "manual":
            if t - state["manual_at"] >= 200:
                state["manual_at"] = t
                modes = [name_to_mode[v.get()] for v in mode_vars]
                sim.receive(leds.status_line(manual_pause.get(), modes), t)
            if manual_meter.get() and t - state["meter_at"] >= 66:
                state["meter_at"] = t
                lv = []
                for i, ph in enumerate(state["phase"]):
                    beat = max(0.0, math.sin(t / 180.0 + ph)) ** 3
                    lv.append(int(255 * min(1.0, 0.2 + 0.8 * beat * random.uniform(0.6, 1.0))))
                sim.receive(leds.meter_line(lv), t)
        elif src == "none":
            sim.pc, sim.meter = None, None
        with lock:
            raws = list(values)
        sim.set_raw(raws, t)
        levels = None
        while state["t"] + leds.LedSim.STEP_MS <= t:
            state["t"] += leds.LedSim.STEP_MS
            levels = sim.step(state["t"])
        if levels is not None:
            for i, lv in enumerate(levels):
                c, glow, dot = led_canvas[i]
                c.itemconfig(dot, fill=led_color(lv))
                # 明るい時だけ周りをうっすら光らせる
                g = leds.perceived(lv) * 0.45
                c.itemconfig(glow, fill="#%02x%02x%02x" % (round(0x16 + (0xff - 0x16) * g * 0.5),
                                                           round(0x18 + (0x4d - 0x18) * g * 0.3),
                                                           round(0x1d + (0x4d - 0x1d) * g * 0.3)))
                mode = sim.pc[1][i] if sim.pc and t - sim.pc[2] <= leds.PC_TIMEOUT_MS else "-"
                led_text[i].set(f"{mode} {lv:3d}")
        root.after(15, tick)

    def poll():
        pc_live = sim.pc is not None and now_ms() - sim.pc[2] <= leds.PC_TIMEOUT_MS
        src = {"pc": "deej-tab の指示" if pc_live else "PC の指示なし", "manual": "手で選んだ指示", "none": "PC なし"}[source.get()]
        status_var.set(status + ("  [送信停止中]" if paused else "") + f"  LED: {src}")
        root.after(200, poll)

    poll()
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
