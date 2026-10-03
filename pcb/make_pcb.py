"""deej-tab のスライダー基板 (KiCad) を作る

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" make_pcb.py

KiCad 付属の Python (pcbnew) で動かす。部品の配置と信号の割り当てはこのスクリプトで決め、線は自動配線 (Freerouting) に引かせる
(Java と %LOCALAPPDATA%\deej-tab\tools\freerouting-2.4.1.jar が要る)。GND は自動配線から外し、表裏のベタでつなぐ。出力:
  deej-tab-sliders.kicad_pcb / .kicad_pro   KiCad で開ける基板 (配線・ベタ GND 込み)
  out/                                     JLCPCB に出すガーバー一式の zip・BOM・部品の位置 (CPL)・DRC の結果・図

基板に載るもの: スライダー PTL60 x5 (今と同じ位置)、ボリューム JH16K6B103L20KC-H13 (秋月 117390。今の SH16K4 の基板用。
本体はナットでパネルに留め、基板の切り欠きを通り抜ける。端子は軸と平行に後ろへ伸びているので、その途中を基板の長穴でハンダ付け)、
LED の抵抗 150Ω x5・ノイズ対策 100nF x6・5V の 10µF (どれもスルーホール。本体は裏、ハンダは表で天板の裏を彫って逃がす)、
Nano へつなぐ JST ZH 横向き (裏面): 電源 3 ピン (JP)・LED 6 ピン (JL)・信号 6 ピン (JA)。
秋月のコネクター付コード (ZH 相当、片側ハンダメッキ) をそのまま挿す。部品はすべて手でハンダ付けする (JLCPCB は基板だけ)。

座標: ケース (case.py) と同じ X = 左→右。V = 基板の上を手前→奥へ測った距離 (パネルの傾き 3.3° の分だけケースの Y より長い)。
スライダーの中心 (Y = 51) で V = Y になるようにしてある。KiCad の画面では奥が上 (KiCad の Y は下向きなので Y = Y0 - V)。
"""

import csv
import glob
import json
import math
import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
NAME = "deej-tab-sliders"
KICAD = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "KiCad", "10.0")
FP_LIB = os.path.join(KICAD, "share", "kicad", "footprints")

# ================================================================ 設定 (case.py と合わせる)

SLOPE = math.atan2(23.5 - 16.2, 126.0)       # パネルの傾き (case.py の H_BACK, H_FRONT, D)
SLIDER_X = [19.0, 41.0, 63.0, 85.0, 107.0]   # スライダーの中心 X (A0〜A4)
SLIDER_Y = 51.0                              # スライダーの中心 Y (ケース)
KNOB_X, KNOB_Y = 19.0, 106.0                 # ボリュームの軸の中心 (ケース)
PIN_END = 35.0                               # スライダーの端子の列 (本体の中心から。手前が 4 本の列、奥が 2 本の列)
# 手前の列 2,1,L,E と奥の列 3,B の横位置 (上から見た位置。摺動子 2 が左端なのは実物で確認済み 2026-10-03)。
# MIRROR = True は左右逆の部品用 (その時は配線の高さの並びも見直す)
PINS_FRONT = {"2": -3.75, "1": -1.25, "L": 1.25, "E": 3.75}
PINS_BACK = {"3": -1.25, "B": 3.75}
MIRROR = False

# ボリューム JH16K6 (データシート 16K6-BXXX L20KC H13): 本体 Φ17・取付面から後ろまで 9.3 (スライダーの 7.0 より深い)。
# 端子 3 本は軸の中心から 13 の所で、取付面の 3.4 後ろから 13.4 後ろまで軸と平行に伸びる (幅約 2.9・厚さ約 0.4 の板。先の 4.0 だけ細い)。
# 基板 (取付面の 7.0〜8.6 後ろ) はこの板の幅の広い所を通るので、穴は長穴にする。回り止めは手前 (-Y) で今のケースと同じ向き → 端子は軸の右
POT_PIN_R = 13.0                             # 端子の列の軸からの距離 (右)
POT_PIN_PITCH = 5.0
POT_SLOT = (3.3, 0.9)                        # 長穴 (端子の板の幅 2.9 / 厚さ 0.4 にすき間)。基板の上では V 方向に長い
POT_PAD = (3.8, 1.9)                         # 奥の端子のパッドが基板の奥の縁から 0.5 になる長さ
POT_CUT_R = 9.0                              # 本体 (Φ17) が通る切り欠き
POT_CUT_BLOCK = (9.2, 7.8)                   # 端子の付け根のかたまり (軸から右へ, 前後の半幅)。本体の後ろの方は軸から 8.7 まで
BOARD_T = 1.6

TRACK = 0.3          # 信号線の幅
CLEAR = 0.2          # すき間
VIA = (0.6, 0.3)     # ビア 外径, 穴
LED_R = "150"                                # LED の抵抗。赤 LED (Vf 1.8V、データシートの条件 20mA) に約 19mA (330Ω の約 2.2 倍の明るさ)
RES_V = (66.0, 76.16)                        # LED の抵抗 (1/4W、足の間隔 10.16) の手前・奥の足の V。スライダーの右のすき間 (X + 7.5)、スライダー 5 だけ左
CAP_V = 50.0                                 # 摺動子の 100nF (足の間隔 2.54) の V。抵抗と同じすき間
GAP_DX = 7.5                                 # すき間に置く部品の X (スライダーの中心から)

# コネクター (JST ZH 1.5mm、横向き。秋月 S3B-ZR / S6B-ZR)。電源・信号・LED で分ける (2026-10-03、ユーザーの選択)。
# 秋月のコネクター付コード (ZH 相当、片側ハンダメッキ) は 6 ピンと 3 ピンだけなので 3+6+6。
# 信号は Nano の A 側 (A0〜A5 が連続)、LED は D 側の並びと同じ順にしてある
# JL と JA は Nano のすぐ左に縦に並べ、口を Nano へ向ける (コードはまっすぐ Nano の端子へ。並びも Nano の端子と同じ前後の順)。
# JP はボリュームの近く、口は手前 (コードは基板の下を手前へ出して Nano の手前の 5V・GND へ)
# (名前, 型番, 1 本目の端子の X, V, 向き, 並びの順の信号 (None は空き))
#   向き "front": 端子は X に並び、口は手前。信号は左から。"right": 端子は V に並び、口は右 (Nano)。信号は手前から
#   1 番はデータシート (口から見て左、基板が下) を裏に付けた向きに直すと、"front" は右端、"right" は奥の端
CONNS = [("JP", "S3B-ZR", 38.0, 102.0, "front", ["GND", "+5V", "GND"]),
         ("JL", "S6B-ZR", 70.0, 91.5, "right", [None, "D3", "D5", "D6", "D9", "D10"]),
         ("JA", "S6B-ZR", 70.0, 103.0, "right", ["A5", "A4", "A3", "A2", "A1", "A0"])]
ZH_PITCH = 1.5
ZH_PAD = (1.03, 1.73, 0.73)                    # パッド 横, 縦, 穴 (KiCad の ZH の部品形状と同じ。ピンは Φ0.5)
ZH_BODY = (1.5, 4.6, 1.4, 3.7)                # ZH 横向きの本体: 端の端子からの張り出し, 口の側, 口と反対の側, 基板の下面からの高さ (JST のデータシート)
TOOLS = os.path.join(os.environ["LOCALAPPDATA"], "deej-tab", "tools")
FREEROUTING = os.path.join(TOOLS, "freerouting-2.4.1.jar")       # Java 25 以上が要る
JAVA = (glob.glob(os.path.join(TOOLS, "jdk-25*", "bin", "java.exe")) or ["java"])[0]   # zip 版の Java (Eclipse Temurin JRE 25)
LED_PINS = ["D3", "D5", "D6", "D9", "D10"]   # スライダー 1〜5 の LED (ファームウェアと同じ)

# 部品 (秋月などで買うスルーホール部品)
PARTS = {LED_R: ("カーボン抵抗 1/4W " + LED_R + "Ω", "Resistor_THT", "R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"),
         "100nF": ("積層セラミックコンデンサー 0.1µF 50V RD15F104Z1HL2L (秋月 104065、10 個入り)", "Capacitor_THT", "C_Disc_D3.8mm_W2.6mm_P2.50mm"),
         "10uF": ("積層セラミックコンデンサー 10µF 25V THD30E-1E106Z (秋月 100464、足の間隔 5mm)", "Capacitor_THT", "C_Disc_D5.0mm_W2.5mm_P5.00mm")}


def v_of(y):
    """ケースの Y → 基板の上の V"""
    return SLIDER_Y + (y - SLIDER_Y) / math.cos(SLOPE)


def board_outline():
    """基板の外形 (X, V)。Nano の真上 (X 78〜 の奥) は切り欠いて、Nano にハンダ付けする線の逃げにする。
    左奥はボリュームの本体が通るように切り欠く (円と、端子の付け根のかたまりの分の四角。左と奥の縁まで抜ける)"""
    kx, kv = KNOB_X, v_of(KNOB_Y)
    r = POT_CUT_R
    bx, bh = POT_CUT_BLOCK
    left = 13.0
    a0 = math.atan2(-bh, math.sqrt(r * r - bh * bh))                 # 円と四角の下の辺の交点
    a1 = math.atan2(-math.sqrt(r * r - (kx - left) ** 2), left - kx)   # 円と左の縁の交点
    arc = [(kx + r * math.cos(a), kv + r * math.sin(a)) for a in [a0 + (a1 - a0) * i / 24 for i in range(25)]]
    return [(left, 13.5), (113.0, 13.5), (113.0, 90.5), (78.0, 90.5), (78.0, 113.5), (kx + bx, 113.5), (kx + bx, kv - bh)] + arc


OUTLINE = board_outline()


X0, Y0 = 50.0, 180.0     # KiCad の図面の上での原点のずらし


def P(x, v):
    return pcbnew.VECTOR2I_MM(X0 + x, Y0 - v)


def mm(a):
    return pcbnew.FromMM(a)


# ================================================================ 基板の道具

board = pcbnew.NewBoard(os.path.join(HERE, NAME + ".kicad_pcb"))
nets = {}


def net(name):
    if name not in nets:
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        nets[name] = n
    return nets[name]


def via(x, v, name):
    o = pcbnew.PCB_VIA(board)
    o.SetPosition(P(x, v))
    o.SetViaType(pcbnew.VIATYPE_THROUGH)
    o.SetWidth(mm(VIA[0]))
    o.SetDrill(mm(VIA[1]))
    o.SetNet(net(name))
    board.Add(o)


def load(lib, name):
    return pcbnew.FootprintLoad(os.path.join(FP_LIB, lib + ".pretty"), name)


def place(fp, ref, value, x, v, rot=0.0, bottom=False):
    fp.SetReference(ref)
    fp.SetValue(value)
    board.Add(fp)
    fp.SetPosition(P(x, v))
    fp.SetOrientationDegrees(rot)
    if bottom:
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    return fp


def pad_xy(fp, num):
    """端子の位置 (X, V)"""
    for p in fp.Pads():
        if p.GetNumber() == num:
            q = p.GetPosition()
            return round(pcbnew.ToMM(q.x) - X0, 4), round(Y0 - pcbnew.ToMM(q.y), 4)
    raise KeyError(num)


def set_pad_net(fp, num, name):
    for p in fp.Pads():
        if p.GetNumber() == num:
            p.SetNet(net(name))


def silk(text, x, v, layer=pcbnew.B_SilkS, size=1.0, rot=0.0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(P(x, v))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I_MM(size, size))
    t.SetTextThickness(mm(size * 0.15))
    t.SetTextAngleDegrees(rot)
    if layer in (pcbnew.B_SilkS, pcbnew.B_Cu):
        t.SetMirrored(True)
    board.Add(t)


# ================================================================ 部品

def slider_footprint():
    """Bourns PTL60 (端子 6 本、穴 Φ1.1 +0.2/-0 → 1.2)。原点はスライダーの中心。本体 9 x 75 の線をシルクに"""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("deej", "Bourns_PTL60"))
    sx = -1 if MIRROR else 1
    for row, dy in ((PINS_FRONT, -PIN_END), (PINS_BACK, PIN_END)):
        for num, dx in row.items():
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
            p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            p.SetSize(pcbnew.VECTOR2I_MM(1.9, 1.9))
            p.SetDrillSize(pcbnew.VECTOR2I_MM(1.2, 1.2))
            p.SetLayerSet(p.PTHMask())
            p.SetPosition(pcbnew.VECTOR2I_MM(sx * dx, -dy))
            fp.Add(p)
    for layer in (pcbnew.F_Fab, pcbnew.F_CrtYd):      # 本体の手前の端は基板の端と同じ位置なので、シルクは描かない
        r = pcbnew.PCB_SHAPE(fp)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        g = 0.25 if layer == pcbnew.F_CrtYd else 0.0
        r.SetStart(pcbnew.VECTOR2I_MM(-4.5 - g, -37.5 - g))
        r.SetEnd(pcbnew.VECTOR2I_MM(4.5 + g, 37.5 + g))
        r.SetLayer(layer)
        r.SetWidth(mm(0.12 if layer == pcbnew.F_SilkS else 0.05))
        fp.Add(r)
    return fp


sliders = []
for k, x in enumerate(SLIDER_X):
    fp = place(slider_footprint(), f"RV{k + 1}", "PTL60-15R0-103B2", x, SLIDER_Y)
    for num in ("1", "L", "E"):
        set_pad_net(fp, num, "GND")
    set_pad_net(fp, "3", "+5V")
    set_pad_net(fp, "2", f"A{k}")
    set_pad_net(fp, "B", f"LED{k + 1}")
    sliders.append(fp)

# ボリューム: 端子 3 本の長穴 (軸の右 POT_PIN_R、前後に POT_PIN_PITCH)。原点は軸の中心。
# データシートの正面図 (軸の側から見た図) で回り止めが左・端子が下・端子は左から 1 2 3。回り止めを手前に向けると端子は右で、1 が手前
knob_v = v_of(KNOB_Y)


def pot_footprint():
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("deej", "Supertech_JH16K6_H13_slots"))
    for i, num in enumerate(("1", "2", "3")):
        p = pcbnew.PAD(fp)
        p.SetNumber(num)
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        p.SetShape(pcbnew.PAD_SHAPE_OVAL)
        p.SetSize(pcbnew.VECTOR2I_MM(POT_PAD[1], POT_PAD[0]))
        p.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
        p.SetDrillSize(pcbnew.VECTOR2I_MM(POT_SLOT[1], POT_SLOT[0]))
        p.SetLayerSet(p.PTHMask())
        p.SetPosition(pcbnew.VECTOR2I_MM(POT_PIN_R, (1 - i) * POT_PIN_PITCH))     # KiCad の Y は下向き: 1 が手前
        fp.Add(p)
    for layer in (pcbnew.F_Fab, pcbnew.F_CrtYd):
        c = pcbnew.PCB_SHAPE(fp)
        c.SetShape(pcbnew.SHAPE_T_CIRCLE)
        c.SetCenter(pcbnew.VECTOR2I_MM(0, 0))
        c.SetEnd(pcbnew.VECTOR2I_MM(8.5 if layer == pcbnew.F_Fab else 8.75, 0))
        c.SetLayer(layer)
        c.SetWidth(mm(0.05))
        fp.Add(c)
    return fp


pot = place(pot_footprint(), "RV6", "JH16K6B103L20KC-H13", KNOB_X, knob_v)
set_pad_net(pot, "1", "GND")
set_pad_net(pot, "2", "A5")
set_pad_net(pot, "3", "+5V")

# コネクター: 裏面 (JST ZH 横向き)。端子の並び順に信号を割り当てる
conn_pin = {}                              # 信号 → (X, V)
conn_num = {}                              # 信号 → (コネクター, 端子番号)
conn_info = []                             # case.py へ渡す


def zh_footprint(n, way):
    """JST ZH 横向き (スルーホール)。KiCad に無いので作る。裏に置くので裏の層に描く。原点は 1 本目 (左端 / 手前の端) の端子。
    way "front": 端子は X に並び口は手前 (KiCad の下)。"right": 端子は V に並び (KiCad では上へ) 口は右。
    1 番は並びの最後 (右端 / 奥の端)"""
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("deej", f"JST_ZH_S{n}B-ZR_Horizontal"))
    for i in range(n):
        p = pcbnew.PAD(fp)
        p.SetNumber(str(n - i))
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        p.SetShape(pcbnew.PAD_SHAPE_ROUNDRECT if i == n - 1 else pcbnew.PAD_SHAPE_OVAL)
        if way == "front":
            p.SetSize(pcbnew.VECTOR2I_MM(ZH_PAD[0], ZH_PAD[1]))
            p.SetPosition(pcbnew.VECTOR2I_MM(i * ZH_PITCH, 0))
        else:
            p.SetSize(pcbnew.VECTOR2I_MM(ZH_PAD[1], ZH_PAD[0]))
            p.SetPosition(pcbnew.VECTOR2I_MM(0, -i * ZH_PITCH))
        p.SetDrillSize(pcbnew.VECTOR2I_MM(ZH_PAD[2], ZH_PAD[2]))
        p.SetLayerSet(p.PTHMask())
        fp.Add(p)
    end, mouth, back, _ = ZH_BODY
    span = (n - 1) * ZH_PITCH
    if way == "front":      # (KiCad の X0, Y0, X1, Y1)
        box = (-end, -back, span + end, mouth)
    else:
        box = (-back, -span - end, mouth, end)
    for layer, g in ((pcbnew.B_Fab, 0.0), (pcbnew.B_CrtYd, 0.25), (pcbnew.B_SilkS, -0.2)):     # シルクは本体の少し内側
        r = pcbnew.PCB_SHAPE(fp)
        r.SetShape(pcbnew.SHAPE_T_RECT)
        r.SetStart(pcbnew.VECTOR2I_MM(box[0] - g, box[1] - g))
        r.SetEnd(pcbnew.VECTOR2I_MM(box[2] + g, box[3] + g))
        r.SetLayer(layer)
        r.SetWidth(mm(0.12 if layer == pcbnew.B_SilkS else 0.05))
        fp.Add(r)
    return fp


for cref, cval, cx0, cv0, way, sigs in CONNS:
    conn = zh_footprint(len(sigs), way)
    conn.SetReference(cref)
    conn.SetValue(cval)
    board.Add(conn)
    conn.SetPosition(P(cx0, cv0))
    conn.Reference().SetVisible(False)     # 番号は下の silk で書く
    conn.Value().SetVisible(False)
    for i, sig in enumerate(sigs):
        num = str(len(sigs) - i)
        if sig:
            set_pad_net(conn, num, sig)
            conn_pin[sig] = pad_xy(conn, num)
            conn_num[sig] = (cref, num)
    conn_info.append({"x": cx0, "v": cv0, "n": len(sigs), "pitch": ZH_PITCH, "way": way})

# ================================================================ 配線

def pin(fp, num):
    return pad_xy(fp, num)


refs = {"R": 0, "C": 0}


def ref(kind):
    refs[kind] += 1
    return f"{kind}{refs[kind]}"


bom = []      # (ref, value)
reliefs = []  # 表に出るハンダの逃げ (天板の裏を彫る所) (X0, V0, X1, V1)
tht_parts = []


# コネクターの端子も表でハンダ付けするので逃げを作る
for ci in conn_info:
    span = ci["pitch"] * (ci["n"] - 1)
    x1, v1 = (ci["x"] + span, ci["v"]) if ci["way"] == "front" else (ci["x"], ci["v"] + span)
    reliefs.append((ci["x"] - 1.5, ci["v"] - 1.5, x1 + 1.5, v1 + 1.5))


def tht(value, x, v, rot=0.0):
    """スルーホールの抵抗・コンデンサー (本体は裏)"""
    _, lib, name = PARTS[value]
    fp = place(load(lib, name), ref("R" if value == LED_R else "C"), value, x, v, rot=rot, bottom=True)
    # 部品の原点は 1 番の足なので、足 2 本の真ん中が (x, v) に来るように動かす
    ps = [pin(fp, p.GetNumber()) for p in fp.Pads()]
    q = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(q.x + mm(x - (ps[0][0] + ps[1][0]) / 2), q.y - mm(v - (ps[0][1] + ps[1][1]) / 2)))
    bom.append((fp.GetReference(), value))
    tht_parts.append((fp.GetReference(), value, fp))
    ps = [pin(fp, p.GetNumber()) for p in fp.Pads()]
    reliefs.append((min(p[0] for p in ps) - 1.5, min(p[1] for p in ps) - 1.5, max(p[0] for p in ps) + 1.5, max(p[1] for p in ps) + 1.5))
    return fp


def set_nets(fp, key, nets_by_order):
    """足を key の小さい順に並べて、順に nets_by_order の信号をつなぐ。並べた足の位置を返す"""
    ps = sorted(fp.Pads(), key=lambda p: key(pad_xy(fp, p.GetNumber())))
    for p, n in zip(ps, nets_by_order):
        p.SetNet(net(n))
    return [pad_xy(fp, p.GetNumber()) for p in ps]


for k, fp in enumerate(sliders):
    x = SLIDER_X[k]
    a, d = f"A{k}", LED_PINS[k]
    gx = x + GAP_DX if k < 4 else x - GAP_DX      # すき間に置く部品の X (スライダー 5 は右に余地がないので左)
    # LED の抵抗: 奥の足が端子 B (LED のアノード)、手前の足が D ピン
    r = tht(LED_R, gx, (RES_V[0] + RES_V[1]) / 2, rot=90)
    set_nets(r, lambda t: t[1], [d, f"LED{k + 1}"])
    # 摺動子の 100nF: 摺動子に近い足が摺動子、遠い足が GND
    c = tht("100nF", gx, CAP_V)
    set_nets(c, lambda t: abs(t[0] - x), [a, "GND"])

# ボリュームの 100nF (摺動子 ↔ GND): ボリュームと JA の間の空いた所
cpot = tht("100nF", 52.0, 108.0)
set_nets(cpot, lambda t: t[0], ["A5", "GND"])
# 5V の 10µF: JP の奥 (JP の口は手前向きなので奥が空く)
c5 = conn_pin["+5V"]
cbig = tht("10uF", c5[0] + 2.5, 108.0)
set_nets(cbig, lambda t: t[0], ["+5V", "GND"])

# ================================================================ 外形・ベタ GND・文字

for (x0, v0), (x1, v1) in zip(OUTLINE, OUTLINE[1:] + OUTLINE[:1]):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(P(x0, v0))
    s.SetEnd(P(x1, v1))
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(mm(0.1))
    board.Add(s)

for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net("GND"))
    z.SetLocalClearance(mm(0.3))
    z.SetMinThickness(mm(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(mm(0.3))
    z.SetThermalReliefSpokeWidth(mm(0.4))
    ol = z.Outline()
    ol.NewOutline()
    for x, v in OUTLINE:
        q = P(x, v)
        ol.Append(q.x, q.y)
    board.Add(z)          # 基板の縁からのすき間は設計ルール (m_CopperEdgeClearance) で空ける

# 表裏の GND をつなぐビア (スライダーの間のすき間)
for k in range(4):
    gx = (SLIDER_X[k] + SLIDER_X[k + 1]) / 2
    for v in range(22, 84, 8):
        via(gx, v, "GND")
for x, v in ((16.0, 95.0), (45.0, 111.0), (62.0, 111.0)):
    via(x, v, "GND")

# 裏の文字: コネクターの端子名・題名
for (cref, _, cx0, cv0, way, sigs) in CONNS:
    n = len(sigs)
    if way == "front":
        silk(cref, cx0 + ZH_PITCH * (n - 1) / 2, cv0 + 3.4, size=1.0)
    else:
        silk(cref, cx0 - 4.6, cv0 + ZH_PITCH * (n - 1) / 2, size=1.0, rot=90)
    for i, sig in enumerate(sigs):
        if not sig:
            continue
        px, pv = conn_pin[sig]
        if way == "front":
            silk(sig, px, pv - 2.6, size=0.8, rot=90)
        else:
            silk(sig, px - 2.7, pv, size=0.8)
silk("deej-tab slider board v1", 55.5, 50.0, size=1.2, rot=90)
silk("JP/JL/JA: JST ZH 3P+6P+6P -> Nano", 57.5, 50.0, size=0.9, rot=90)

# ================================================================ 設計ルール・保存・DRC・出力

ds = board.GetDesignSettings()
ds.SetBoardThickness(mm(BOARD_T))
ds.m_CopperEdgeClearance = mm(0.5)                     # ベタを塗る前に入れる (ベタは基板の外形いっぱいに描いてある)
ds.SetAuxOrigin(P(OUTLINE[0][0], OUTLINE[0][1]))     # 基板の左手前の角を原点に (ガーバー・ドリル・部品の位置で共通)
nc = ds.m_NetSettings.GetDefaultNetclass()
nc.SetClearance(mm(CLEAR))
nc.SetTrackWidth(mm(TRACK))
nc.SetViaDiameter(mm(VIA[0]))
nc.SetViaDrill(mm(VIA[1]))

path = os.path.join(HERE, NAME + ".kicad_pcb")
os.makedirs(OUT, exist_ok=True)

# ---- 自動配線 (Freerouting)。GND は Specctra の網から外す (ベタでつなぐ。端子は障害物として残る)
dsn = os.path.join(OUT, NAME + ".dsn")
ses = os.path.join(OUT, NAME + ".ses")
pcbnew.ExportSpecctraDSN(board, dsn)
text = open(dsn, encoding="utf-8").read()
import re
text = re.sub(r"\(net GND\s*\(pins[^)]*\)\s*\)", "", text)
open(dsn, "w", encoding="utf-8").write(text)
if os.path.exists(ses):
    os.remove(ses)
r = subprocess.run([JAVA, "-jar", FREEROUTING, "-de", dsn, "-do", ses, "-mp", "200", "-l", "en", "--gui.enabled=false"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=OUT)   # ログ (en/freerouting.log) は out に出す
if not os.path.exists(ses):
    print(r.stdout[-3000:], r.stderr[-3000:])
    sys.exit("自動配線に失敗")
print("自動配線:", [l for l in r.stdout.splitlines() if "routed" in l.lower() or "unrouted" in l.lower()][-3:])
pcbnew.ImportSpecctraSES(board, ses)

pcbnew.SaveBoard(path, board)
# ベタを塗る。作ったばかりの基板のまま塗ると、外形に円弧の切り欠きを入れたら塗りが空になったので、読み込み直してから塗る
board = pcbnew.LoadBoard(path)
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
pcbnew.SaveBoard(path, board)
# case.py --pcb が読む外形とコネクターの位置 (基板の上の座標 X, V)
with open(os.path.join(HERE, "outline.json"), "w", encoding="utf-8") as f:
    json.dump({"outline": [[round(x, 4), round(v, 4)] for x, v in OUTLINE], "conns": conn_info, "conn_body": ZH_BODY,
               "reliefs": [[round(c, 3) for c in r] for r in reliefs],
               "parts": [[r_, val, *[round(c, 3) for c in pin(fp_, "1")], *[round(c, 3) for c in pin(fp_, "2")]] for r_, val, fp_ in tht_parts]},
              f, indent=1)
print("saved", path)

os.makedirs(OUT, exist_ok=True)
cli = os.path.join(KICAD, "bin", "kicad-cli.exe")


def run(*args):
    r = subprocess.run([cli, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode not in (0, 5):
        print(r.stdout, r.stderr)
    return r


# DRC (重大な違反があれば止める)
rep = os.path.join(OUT, "drc.txt")
run("pcb", "drc", "--severity-error", "--severity-warning", "-o", rep, path)
print(open(rep, encoding="utf-8").read()[-1500:])

# ガーバー・ドリル (JLCPCB 向け)
gdir = os.path.join(OUT, "gerber")
os.makedirs(gdir, exist_ok=True)
for f in os.listdir(gdir):
    os.remove(os.path.join(gdir, f))
run("pcb", "export", "gerbers", "--no-protel-ext", "--use-drill-file-origin", "-l", "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts", "-o", gdir + os.sep, path)
run("pcb", "export", "drill", "--format", "excellon", "--drill-origin", "plot", "--excellon-separate-th", "-o", gdir + os.sep, path)
import zipfile
with zipfile.ZipFile(os.path.join(OUT, NAME + "-gerber.zip"), "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(os.listdir(gdir)):
        zf.write(os.path.join(gdir, f), f)

# 買う部品の一覧 (基板に載るもの。スライダー・ボリューム・コネクターも含む)。JLCPCB には基板だけ頼む
groups = {}
for r, val in bom:
    groups.setdefault(val, []).append(r)
if os.path.exists(os.path.join(OUT, NAME + "-cpl.csv")):       # 以前の部品実装用の出力
    os.remove(os.path.join(OUT, NAME + "-cpl.csv"))
with open(os.path.join(OUT, NAME + "-bom.csv"), "w", newline="", encoding="utf-8-sig") as g:
    w = csv.writer(g)
    w.writerow(["部品", "記号", "数", "部品形状"])
    w.writerow(["スライダー Bourns PTL60-15R0-103B2", "RV1-RV5", 5, "Bourns_PTL60"])
    w.writerow(["ボリューム JH16K6B103L20KC-H13 (秋月 117390)", "RV6", 1, "長穴 3 つ"])
    w.writerow(["コネクター JST S3B-ZR-3.4 (ZH 横向き 3 ピン、秋月 114161)", "JP", 1, "JST_ZH_S3B-ZR_Horizontal"])
    w.writerow(["コネクター JST S6B-ZR-3.4 (ZH 横向き 6 ピン、秋月 114164)", "JL,JA", 2, "JST_ZH_S6B-ZR_Horizontal"])
    w.writerow(["コネクター付コード 3P 黒白赤 (ZH 相当、秋月 118272)", "JP のケーブル", 1, ""])
    w.writerow(["コネクター付コード 6P(H) 白赤黒青黄緑 (ZH 相当、秋月 105719)", "JL・JA のケーブル", 2, ""])
    for val, rs in groups.items():
        w.writerow([PARTS[val][0], ",".join(rs), len(rs), PARTS[val][2]])

# 図 (表・裏)
for side, layers in (("top", "F.Cu,F.SilkS,Edge.Cuts"), ("bottom", "B.Cu,B.SilkS,Edge.Cuts")):
    run("pcb", "export", "svg", "--mode-single", "--exclude-drawing-sheet", "--fit-page-to-board", "-l", layers,
        *(["--mirror"] if side == "bottom" else []), "-o", os.path.join(OUT, f"{NAME}-{side}.svg"), path)

for cref, _, _, _, _, sigs in CONNS:
    print(f"{cref} の端子 (番号: 信号):", ", ".join(f"{len(sigs) - i}:{s_ or '空き'}" for i, s_ in reversed(list(enumerate(sigs)))))
