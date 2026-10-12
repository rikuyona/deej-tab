"""スライダーのつられ (クロストーク) を測る。

スライダー 1 だけを動かしている間の、ほかのスライダーの値の揺れを
「LED を普段どおり光らせた時」と「LED を全部消した時」で比べる。
LED を消すと揺れが消えるなら、原因は LED の電流 (GND の浮き)。

使い方 (deej-tab を終了してから。COM ポートは 1 つのプロセスしか開けない):
  app\\.venv\\Scripts\\python tools\\crosstalk_test.py COM6
"""
import sys
import time
import statistics

import serial

PHASE_SEC = 15
NAMES = ["1", "2", "3", "4", "5", "ノブ"]


def run_phase(ser, label, led_line, out):
    input(f"\n[{label}] Enter を押したら {PHASE_SEC} 秒間、スライダー 1 だけを端から端まで往復させてください（ほかは触らない）")
    ser.reset_input_buffer()   # Enter を待っている間に溜まった行は使わない
    rows = []
    start = time.time()
    last_led = 0
    buf = b""
    while time.time() - start < PHASE_SEC:
        now = time.time()
        if now - last_led > 0.3:
            ser.write(led_line)
            last_led = now
        buf += ser.read(ser.in_waiting or 1)
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            parts = line.decode(errors="ignore").strip().split("|")
            if len(parts) == 6 and all(p.isdigit() for p in parts):
                vals = [int(p) for p in parts]
                rows.append(vals)
                out.write(f"{label},{now - start:.3f}," + ",".join(parts) + "\n")
        left = PHASE_SEC - int(time.time() - start)
        print(f"\r  残り {left:2d} 秒  スライダー 1 = {rows[-1][0] if rows else '-':>4}", end="", flush=True)
    print()
    return rows


def summary(label, rows):
    print(f"\n[{label}] {len(rows)} 行")
    s1 = [r[0] for r in rows]
    print(f"  スライダー 1（動かした）: {min(s1)}〜{max(s1)}")
    for i in range(1, 6):
        v = [r[i] for r in rows]
        changes = sum(1 for a, b in zip(v, v[1:]) if a != b)
        print(f"  スライダー {NAMES[i]:>3}: 幅 {max(v) - min(v):3d}（{min(v)}〜{max(v)}）"
              f"  標準偏差 {statistics.pstdev(v):5.2f}  値が変わった回数 {changes}")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    ser = serial.Serial(port, 9600, timeout=0.05)
    print(f"{port} を開きました。Nano が起動し直すので 3 秒待ちます…")
    time.sleep(3)
    ser.reset_input_buffer()
    path = "crosstalk.csv"
    with open(path, "w", encoding="utf-8") as out:
        out.write("phase,t,s1,s2,s3,s4,s5,knob\n")
        a = run_phase(ser, "A LED 普段どおり", b"@L|0|N|N|N|N|N\n", out)
        b = run_phase(ser, "B LED 全部消灯", b"@L|0|O|O|O|O|O\n", out)
    ser.close()
    summary("A LED 普段どおり", a)
    summary("B LED 全部消灯", b)
    print(f"\n記録: {path}")


if __name__ == "__main__":
    main()
