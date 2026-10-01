"""仮スライダー: Nano の代わりに deej-tab へスライダー値を送るテスト用ツール

config.yaml の com_port を socket://127.0.0.1:9000 にすると、deej-tab がここにつながる。
送信形式はファームウェアと同じ "v0|v1|...|v5\\n" (0〜1023)。

テスト用の機能:
- 0% / 50% / 100% に全部そろえる
- ノイズを混ぜる (実機のガリや揺れの再現。ノイズ除去の強さを試せる)
- 送信を止める (Nano が固まった状態の再現。deej-tab が 5 秒で繋ぎ直すかを試せる)
- 別のポートで待ち受ける: fake_sliders.py --port 9100
- LED: deej-tab から届いた指示を、ファームウェアと同じ計算 (app/leds.py の LedSim) で光らせて見せる
"""

import argparse
import os
import queue
import random
import socket
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import leds  # noqa: E402  (ファームウェアと同じ LED の計算)

HOST = "127.0.0.1"
CHANNELS = 6
INTERVAL = 0.02  # 秒
NAMES = ["1", "2", "3", "4", "5", "ノブ"]
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

    sim = leds.LedSim(now_ms(), list(values), dict(leds.PARAMS))

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

    no_activate(root)
    threading.Thread(target=server, args=(args.port,), daemon=True).start()

    # ---------- 光り方の計算 (10ms ごと、ファームウェアの loop と同じ) と描画
    state = {"t": now_ms()}

    def tick():
        t = now_ms()
        # deej-tab から届いた行 (状態・音の大きさ・設定画面で変えた光り方の数値)
        while not from_pc.empty():
            sim.receive(from_pc.get_nowait(), t)
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
        src = "deej-tab の指示" if pc_live else "PC の指示なし"
        status_var.set(status + ("  [送信停止中]" if paused else "") + f"  LED: {src}")
        root.after(200, poll)

    poll()
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
