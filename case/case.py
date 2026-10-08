"""deej-tab ケース (3Dプリント用) を作る

    .venv\\Scripts\\python case.py

出力 (out/)。Bambu Lab A1 mini + AMS で多色刷りする前提:
  top_plate.stl                    天板 (一枚もの、厚さ TOP_T の詰まった板)。表を上にした印刷向き。1 色。
                                   目盛り・番号は彫り (深さ MARK_DEPTH)。刷ったあとに塗料を流し込んで拭き取る。
                                   サポートは外側の帯の下 (色の境目の溝の所) だけ「ビルドプレートのみ」。
                                   スライダー本体は裏のくぼみに押し込むと爪で留まる。底ケースとはスナップフィット (四隅の M3x8 でも締められる)
  bottom_case.stl                  底ケース (底 + 分割溝より下の側面 + 四隅の柱 + Nano の台 + スナップの溝)。底を下にした印刷向き
  nano_clip.stl                    Nano の押さえ (手前の面を下に立てて刷る)
  cap_shell.stl + cap_core.stl     スライダーのツマミ (黒い外殻 + 乳白の芯)。同じく同時に読み込んで1つの部品にし、色を割り当てる
  fit_test.stl                     ツマミの爪のかかりのはめ合いテスト (1 色)
  knob_body.stl + knob_line.stl    回転ボリュームのノブ (黒い本体 + 乳白の指示線)。上面を下にした印刷向き。同時に読み込んで1つの部品にする
  knob_fit_test.stl                ノブの軸穴のはめ合いテスト (山の内径 5.5 / 5.6 / 5.7。1 色)
  spacer.stl                       (--pcb だけ) 配線版の底ケースに基板版の天板を載せる時の、四隅の柱のスペーサー 4 個 (1 色)
  viewer.html                      部品を入れた状態を回して見られる確認用ページ (ブラウザで開く)

座標: X = 左→右, Y = 手前→奥, Z = 上。単位 mm。
寸法は下の「設定」を書き換えて作り直せる。
"""

import math
import os
import sys

import numpy as np
import trimesh
import manifold3d
from manifold3d import CrossSection, Manifold

# ================================================================ 設定

W = 126.0            # 横幅
D = 126.0            # 奥行き
H_FRONT = 16.2       # 手前の高さ (底板込み)
H_BACK = 23.5        # 奥の高さ (底板込み)
WALL = 2.4           # 側面の厚さ
TOP = 2.0            # パネルの厚さ
PLATE = 2.0          # 底板の厚さ
CORNER_R = 8.0       # 外形の角の丸み (上から見た角)
FIT = 0.3            # はめ合いのすき間

# 見た目 (Lofree Flow 寄せ: 柔らかい縁・上の帯と本体のツートン・底板の影)
EDGE = 3.0           # 上面の縁の丸み。パネル面を下に印刷するので 45° の面取りから丸みにつなぐ (サポート不要)
BAND = 5.0           # 天板の側面の高さ (パネル上面から分割溝の中心まで)。ここで天板と底ケースに分ける
LIP = (1.2, 1.5)     # 天板の位置合わせの縁 厚さ, 分割面から下 (底ケースの内側) への出っ張り
BOSS_DEPTH = 8.0     # 天板のボスの下端 (パネル上面から。天板の印刷の高さがほぼこれになる)。その下は底ケースから柱を立てる
BOSS_GAP = 0.2       # 天板のボスと底ケースの柱のすき間 (締めた力が分割面にかかるように)
SCREW_TIP = 0.6      # ネジの下穴をパネル上面の手前で止める残りの厚さ (パネル裏から)
GROOVE = (0.8, 0.5)  # 分割溝 高さ, 深さ
PLATE_CHAMFER = 1.2  # 底板の下の縁の面取り (ケースが浮いて見える)
SLOT_CHAMFER = 0.5   # スライダーの溝の縁の面取り
MARK_DEPTH = 0.4     # 目盛り・文字の彫りの深さ (0.08 積層で 5 層。0.6 は深すぎた)。1 色で刷り、あとから塗料を流し込んで表を拭き取る (2026-10-01)
                     # (多色で刷ると、彫りを埋める形でも表に載せる形でもきれいに出なかった)
TICK = (0.9, 1.8, 3.2, 6.8)                  # 目盛り 線幅 (0.4 ノズルで 2 本ちょうど), 短い線, 長い線 (0/50/100%), 溝の中心からの距離 (ツマミの横)
KNOB_TICK = (9.6, 10.6, 11.6, 300.0)         # ノブの目盛り 内側の半径, 短い線の外側, 長い線の外側, 回転角 (データシート 300°)
KNOB_ROT = 0.0                               # ノブの目盛り全体を回す角度 (時計回り)。軸は 18 山のローレットでツマミは 20° 刻みにしか付かないので、実物で最小位置の指示線に合わせる
LABELS = ["1", "2", "3", "4", "5"]           # スライダーの手前の番号 (空文字で無し)
LABEL_H = 3.8                                # 番号の高さ
TEXT_GROW = 0.2                              # 文字の輪郭を外へ太らせる量 (片側)。画の太さを目盛りと同じ約 0.9 にする (0.4 ノズルで 2 本ちょうど)
LOGO = ""                                    # 右奥のロゴ (空文字で無し)
LOGO_H = 4.6
FONT = r"C:\Windows\Fonts\bahnschrift.ttf"   # Bahnschrift (DIN 系)

# 手持ちのフィラメント (Bambu PLA Matte) と、部品ごとの割り当て。ビューアの色と、印刷時の割り当ての表示に使う
FILAMENT = {
    "ivory": ("マットアイボリーホワイト (11100)", [249, 248, 243]),
    "charcoal": ("マットチャコール (11101)", [58, 59, 61]),
    "ash": ("マットアッシュグレー (11102)", [155, 158, 160]),
    "orange": ("マットマンダリンオレンジ (11300)", [249, 153, 99]),
}
PART_FILAMENT = {
    "top_plate": "ivory",       # 天板
    "marks": "charcoal",        # 目盛り・番号
    "bottom_case": "ash",       # 底ケース
    "cap_shell": "charcoal",    # ツマミの外殻
    "cap_core": "ivory",        # ツマミの芯 (白いライン)
    "nano_clip": "ash",         # Nano の押さえ (後付け。底ケースと同じ色)
    "knob_body": "charcoal",    # ノブ (回転ボリューム用)
    "knob_line": "ivory",       # ノブの指示線
}

# スライダー: Bourns PTL60 (データシートの寸法)
SLIDER_X = [19.0, 41.0, 63.0, 85.0, 107.0]   # 各スライダーの中心 X (A0〜A4、22mm 間隔)
SLIDER_Y = 51.0                              # スライダー中心の Y (上から見た位置)
SLIDER_BODY = (9.0, 75.0, 7.0)               # 本体 幅, 長さ, 取付面から下の高さ
SLIDER_PINS = 3.5                            # 本体の下に出る端子の長さ (端子は両端にだけある)
PIN_END = 35.0                               # 端子の列の位置 (本体中心から。列どうしの間隔 70)
PINS_4 = [-3.75, -1.25, 1.25, 3.75]          # 片側の端子 2,1,L,E の横位置 (2.5 間隔)
PINS_2 = [-1.25, 3.75]                       # 反対側の端子 3,B の横位置 (データシートの穴の図: 3 は 1 と、B は E と同じ線上)
SLIDER_FLIP = [False] * 5                    # True で 180°回す (3,B の端子を手前に)
WIRE_MARGIN = 2.5                            # 端子の下に空けておく配線の余裕
SLIDER_SCREW_PITCH = 71.0                    # 取付ネジ M2 の間隔
SLOT = (3.0, 67.0)                           # パネルの溝 幅, 長さ (レバー 2.0 x 6.0、ストローク 60)
SLOT_OFFSET = 0.0                            # 溝の横ずれ (レバーが本体の中心からずれている場合。実物で確認)
LEVER = (2.0, 6.0, 15.0)                     # レバー 厚さ, 幅, 取付面からの高さ (厚さ・幅は実測。上から下まで同じ厚さ)
LEVER_NOTCH = (5.0, 1.0, 0.5)                # レバーの溝 上からの位置, 幅 (上下), 深さ。幅 6 の両端 (厚さ 2 の縁) にある横溝 (実測)。LED は上の 5x5 の中
CAP = (11.5, 28.0, 12.5, 8.5)                # スライダーのツマミ (自作) 幅, いちばん長い所の長さ(スライド方向), 山の高さ, レバーへの差し込み深さ。底はパネルの上 4.5mm
CAP_CEIL = 1.6                               # 谷の天井のレバーの上の厚さ (芯 0.4 + 外殻 1.2)
# 形はユーザー指定のモデル (Cults3D「Perilla deslizante de potenciometro」) の画像から:
#   下の帯は底へすぼむ面取り / その上の両端は外へふくらむ弧で山へ / 上面は山から山への大きな凹んだ弧 / 上面に V 溝 / 両側面は平ら
CAP_BAND = (2.2, 1.9, 1.0)                   # 下の帯 高さ, 両端のすぼみ, 両側面のすぼみ
CAP_HORN = (9.2, 0.8, 0.3)                   # 山 中心からの距離 (外側の角), 上の平らな幅, 内側の角の面取り
CAP_NOTCH = ([3.0, 5.5, 7.8], 0.9, 0.5)      # 上面の V 溝 中心からの位置 (片側), 幅, 深さ。スライド方向と直角
CAP_LINE_RAISE = 0.35                        # 白いライン (芯の板) を上面・側面から盛り上げる高さ
CAP_EDGE = 0.3                               # 両側面と上面・両端の弧の境目の面取り (下の帯は除く)
CAP_LINE_EDGE = 0.6                          # 白いラインの角の面取り
# ツマミは元の CS-52 と同じ2色構成: 乳白の芯 (レバーの LED の光を通す) に、黒い外殻を前後から2つかぶせる。芯の板が指示線として上面と両側面に出て光る
CAP_WALL = 1.2                               # 外殻の厚さ (0.4 ノズル 3 周)
CAP_FIN = 1.2                                # 指示線 (芯の板) の幅
CAP_GAP = 0.0                                # 外殻の内側と芯のすき間 (多色刷りなので 0。別々に刷って組むなら 0.1)
CAP_FIT = 0.2                                # ツマミの穴のすき間
# 穴の両端に、レバーの溝へはまる爪 (穴の壁と一体で、少しだけ出す丸い出っ張り)。押し込むと入り、引くと抜ける
CAP_SNAP = (0.25, 0.05)                      # 爪がレバーの縁より内側へ入る量 (かかり), 溝の上下とのすき間 (丸の半径 = 溝の幅/2 - これ)
FIT_TEST = ([0.05, 0.10, 0.15], 1.2, 4.0)    # はめ合いテストピース 爪のかかりの候補, 穴の上に残す厚さ, 並べる間隔
TRAVEL = 60.0
M2_HOLE = 2.4
M2_HEAD = 4.4                                # M2 皿ネジの頭

# ノブ: SH16K4B103L20KCCI (Φ17, 取付ネジ M7x0.75, 回り止め)
KNOB_X, KNOB_Y = 19.0, 106.0
POT_BODY = (17.0, 9.3)                       # 本体 直径, 取付面から下の奥行き
POT_HOLE = 7.4                               # M7 ブッシング用の穴
POT_TAB = (3.4, 1.8, 1.2, 7.8)               # 回り止めの逃げ 幅, 奥行き, 深さ, 中心からの距離 (手前側)
KNOB = (15.0, 15.7, 14.5)                    # ABS-28 直径, 全高, ローレット部の高さ (YUNG の図面)
KNOB_RECESS = (12.8, 3.85)                   # ABS-28 底のくぼみ 直径, 深さ (ナットにかぶさる)
KNOB_BORE = 14.2                             # ABS-28 軸穴の深さ (底から。天井 1.5mm として)
# 印刷用のノブ (2026-10-02)。外形・底のくぼみ・軸穴の深さは ABS-28 と同じにして、置き換えても位置が変わらないようにする。
# 軸は 6mm・18 山のローレット。穴は丸 (外径) で、奥の部分にだけ内向きの山を立て、押し込むと山がローレットに食い込んで回らない
KNOB_GRIP = (6.2, 5.6, 8.0, 18)              # 軸穴 外径, 山の先の内径, 山のある長さ (穴の奥から), 山の数
KNOB_FIT_TEST = [5.5, 5.6, 5.7]              # はめ合いテスト 山の先の内径の候補 (きつい順)
KNOB_KNURL = (30, 0.45)                      # 側面の縦溝 本数, 半径 (深さ)。0.4 ノズルで出る太さ
KNOB_LINE = (1.2, 2.4, 0.6)                  # 上面の指示線 (乳白の別パーツを埋める) 幅, 中心側の端の半径, 深さ (0.2 積層で 3 層)
SHAFT = 20.0                                 # ボリュームの軸の長さ (取付面から)
NUT = (10.0, 2.0, 12.0, 0.4)                 # M7 ナット 二面幅, 厚さ / 座金 外径, 厚さ

# Arduino Nano (Type-C) 。USB-C を奥の面に出す
NANO_X = 90.0                                # 中心 X (スライダー4・5の奥端の下。端子とのすき間が最大になる位置)
NANO = (18.5, 43.8, 1.6)                     # 台の内寸に使う基板 幅, 長さ, 厚さ (実物は 18 x 43.5 前後)
NANO_BOARD = (17.8, 43.4)                    # 表示・干渉チェック・押さえ用の基板の大きさ (実測 2026-09-30)
NANO_THICK = 1.1                             # 基板の実際の厚さ (実測。ケースの台と USB-C の穴は NANO[2] = 1.6 で作って印刷済み)
NANO_USB_MARGIN = 0.1                        # USB-C を穴の下の縁から浮かせる量 (穴は上下 0.15 ずつ大きい)
USB_RECESS = 1.7                             # USB-C の口の面を奥の外壁の面から引っ込める量
NANO_USB_OUT = 1.0                           # USB-C の口が基板の端からはみ出す量 (実物で確認)
USB_C = (8.94, 3.26, 7.35)                   # USB-C レセプタクル 幅, 高さ, 奥行き (基板の上に載る形)
NANO_STANDOFF = 2.0                          # 底板からの高さ (裏面の CH340C などを逃がす)
NANO_WIRE = 2.5                              # 端子のパッドにハンダ付けする配線の高さ (基板上面から)
USB_HOLE = (0.15, 0.4)                       # 奥の面の穴 USB-C の口の形 (長円) からのすき間 (片側), 外側の縁の面取り
USB_Z_ABOVE_BOARD = USB_C[1] / 2            # 基板上面から USB-C 中心までの高さ
NANO_STOP_T = 1.75                           # 手前の止め (押さえを挿す板) の厚さ。1.6 だと細かった (ユーザー、2026-10-01)。基板側の面は動かさず手前に厚くする
NANO_WALL_FROM = 92.0                        # Nano の台の壁をこの Y より奥だけに付ける
# Nano の押さえ (後付け。印刷済みの底ケースの手前の止めにかぶせて接着し、基板の手前の端を上から押さえる)
# 台が奥側にしかなく、USB を抜き挿しすると基板が奥を支点に傾いて手前が浮いたため (2026-09-30)
NANO_CLIP = (0.2, 1.2, 0.1, 1.0, 0.05)       # 止めとのすき間 (横・前後), 止めの前と左右の壁の厚さ, 止めの上のすき間, 天井の厚さ, 基板の上面とのすき間
NANO_CLIP_SIDE = (0.1, 1.2, 4.6)             # 左右の爪: 基板の側面とのすき間 (片側), 厚さ / 基板にかかる長さ (手前の端から)。
                                             # 上は ICSP と 1 本目の端子 VIN・TX1 (どちらも使わない) を押さえる。裏は手前の端から 5mm 部品がない (実物で確認)。
                                             # スライダー4の配線の余裕の手前 (0.2) で止める

# 組み立てネジ: M3 (天板の四隅のボスへ、底ケースの下から。頭は座ぐりに沈める)
BOSS_R = 3.6
BOSS_PILOT = 2.5
BOSS_POS = [(7.5, 7.5), (W - 7.5, 7.5), (7.5, D - 7.5), (W - 7.5, D - 7.5)]
M3_HOLE = 3.4
SCREW = (8.0, 6.4)   # 組み立てネジ M3 (なべ / キャップ) の首下長さ, 座ぐりの直径。座ぐりの深さを四隅で変えて全部同じ長さにする
COL_WALL = 1.2       # 底ケースの柱の座ぐりまわりの厚さ

FEET = (10.5, 1.0)   # 底板のゴム足の凹み 直径, 深さ

# ================================================================ 基板版 (python case.py --pcb → out/pcb/)
# スライダー・ボリュームを基板 (pcb/make_pcb.py) に載せ、Nano とはケーブル (JST ZH 電源 3 ピン・LED 6 ピン・信号 6 ピン) でつなぐ版 (2026-10-03)。
# Nano・USB-C の位置は配線版と同じ。スライダーは爪をやめて M2 の皿ネジで留める (基板があるので本体の下に爪を掛けられない)
# ボリュームは今の SH16K4 と同じシリーズの基板用 JH16K6B103L20KC-H13 (秋月 117390)。今と同じくナットでパネルに留めるので、
# パネルの穴・回り止め・ノブ・目盛りは配線版と同じ。本体 (取付面から 9.3) は基板の切り欠きを通り抜け、軸と平行に後ろへ伸びた端子を基板の長穴でハンダ付けする
PCB = "--pcb" in sys.argv
PCB_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pcb", "outline.json")   # pcb/make_pcb.py が書く外形とコネクターの位置
PCB_T = 1.6
PCB_PLATE_GAP = 0.5                          # 天板の裏と基板の上面のすき間 (スライダー本体の高さ 7.0±0.5 の短い個体でも基板を押さない)
PCB_SOLDER = 2.4                             # 基板の裏に出る端子とハンダ (基板の下面から)
JH16K6_LEAD = (13.0, 5.0, 3.4, 13.4, 2.9, 0.4)   # 端子: 軸からの距離 (右), 間隔, 取付面から後ろへ 始まり・終わり, 板の幅, 厚さ (データシートの図から)
JH16K6_BLOCK = (11.4, 7.6, 2.2, 3.4)         # 端子の付け根の板 (取付面のすぐ後ろ): 軸から右へ, 前後の半幅, 取付面から後ろへ 始まり・終わり
PCB_TOP_RELIEF = 3.0                         # コネクター・抵抗・コンデンサーの足とハンダが表に出る所の逃げ (天板の裏を彫る深さ)
PCB_THT = {"150": ("res", 6.5, 2.5), "100nF": ("cap", 3.8, 2.5, 3.8), "10uF": ("cap", 6.5, 2.5, 7.0)}   # 0.1µF は秋月 104065 (RD15F104Z1HL2L)、10µF は秋月 100464 (THD30E-1E106Z) の寸法
# 裏に付けるスルーホール部品の本体: 抵抗 (寝かせる。長さ, 直径) / コンデンサー (立てる。幅, 厚さ, 基板の下面からの高さ)

SEG = 64

# ================================================================ 形状の道具

SLOPE = math.atan2(H_BACK - H_FRONT, D)   # パネルの傾き


def top_z(y):
    """上面の高さ"""
    return H_FRONT + (H_BACK - H_FRONT) * y / D


def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def cyl(x, y, z0, z1, r, r2=None):
    return Manifold.cylinder(z1 - z0, r, r if r2 is None else r2, SEG).translate([x, y, z0])


def rounded_rect(w, d, r):
    return CrossSection.square([w - 2 * r, d - 2 * r]).translate([r, r]).offset(r, circular_segments=SEG)


def side_profile_extrude(pts, x0, x1):
    """(y, z) の多角形を X 方向に押し出す"""
    cs = CrossSection([pts])
    m = Manifold.extrude(cs, x1 - x0)
    # (u, v, w) -> (x = w, y = u, z = v)
    return m.transform(np.array([[0, 0, 1, x0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))


def on_panel(m, x, y):
    """パネル座標 (上面が z=0、下向きが -z) で作った形を、上から見た (x, y) の位置のパネル上に置く"""
    return m.rotate([math.degrees(SLOPE), 0, 0]).translate([x, y, top_z(y)])


def slot(w, l):
    r = w / 2
    a = Manifold.cylinder(1, r, r, SEG).translate([0, -(l / 2 - r), 0])
    b = Manifold.cylinder(1, r, r, SEG).translate([0, (l / 2 - r), 0])
    return Manifold.batch_hull([a, b])


# ================================================================ 上ケース

def edge_profile():
    """上面の縁の断面 (内側への入り, 上面からの下がり)。側面から 45° の面取り、そこから上面へ丸み"""
    r = EDGE
    pts = [(0.0, 2 * r * (1 - math.sqrt(0.5)))]
    for k in range(9):
        a = math.radians(45 + 45 * k / 8)
        pts.append((r - r * math.cos(a), r - r * math.sin(a)))
    return pts


def outer_solid(z0, lift=0.0):
    """外形: 上から見て角丸、上面が傾いた立体 (z0 より上)。上面の縁は面取り＋丸み。全体が凸なので断面を重ねた凸包で作る
    lift: 上面を上げる量 (高さ方向)"""
    slabs = []
    for inset, drop in edge_profile():
        plan = Manifold.extrude(rounded_rect(W - 2 * inset, D - 2 * inset, CORNER_R - inset), H_BACK + 5).translate([inset, inset, z0])
        wedge = side_profile_extrude([(-1, z0), (D + 1, z0), (D + 1, top_z(D + 1) - drop + lift), (-1, top_z(-1) - drop + lift)], -1, W + 1)
        slabs.append(plan ^ wedge)
    return Manifold.batch_hull(slabs)


def band_zone(z_lo, z_hi):
    """パネル上面に平行な層 (上面から下へ z_lo〜z_hi、パネルに直角に測る)"""
    c = math.cos(SLOPE)
    lo, hi = z_hi / c, z_lo / c
    return side_profile_extrude([(-1, top_z(-1) - lo), (D + 1, top_z(D + 1) - lo),
                                 (D + 1, top_z(D + 1) - hi), (-1, top_z(-1) - hi)], -1, W + 1)


def text_section(s, height, anchor="center"):
    """文字の輪郭 (高さ height に合わせ、anchor: center / right で原点に置く)"""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    tp = TextPath((0, 0), s, size=10, prop=FontProperties(fname=FONT))
    polys = [p[:-1] for p in tp.to_polygons(closed_only=True) if len(p) > 3]
    cs = CrossSection(polys, fillrule=manifold3d.FillRule.NonZero)   # フォントの塗り方。EvenOdd だと重なった輪郭 (「4」の横棒と縦棒) に穴が空いた
    x0, y0, x1, y1 = cs.bounds()
    k = height / (y1 - y0)
    cs = cs.translate([-(x0 + x1) / 2 if anchor == "center" else -x1, -(y0 + y1) / 2]).scale([k, k])
    # 画を太らせる (細いと 0.4 ノズルで汚くなる)。外側だけ太らせ、中の穴 (0・6・8・9 などの) は元の大きさのまま残す
    filled = CrossSection.batch_boolean([CrossSection([p], fillrule=manifold3d.FillRule.NonZero) for p in cs.to_polygons()], manifold3d.OpType.Add)
    holes = filled - cs
    return filled.offset(TEXT_GROW, circular_segments=8) - holes


def marks_2d():
    """パネルの目盛り・番号・ロゴ。(上から見た位置 x, y, パネル座標の輪郭) のリスト"""
    out = []
    tw, short, long_, tx = TICK
    n = 11
    for k, x in enumerate(SLIDER_X):
        ticks = []
        for i in range(n):
            y = -TRAVEL / 2 + TRAVEL * i / (n - 1)
            ln = long_ if i in (0, n // 2, n - 1) else short
            ticks.append(CrossSection.square([ln, tw]).translate([SLOT_OFFSET + tx, y - tw / 2]))
        cs = CrossSection.batch_boolean(ticks, manifold3d.OpType.Add)
        if k < len(LABELS) and LABELS[k]:
            y = -SLIDER_SCREW_PITCH / 2 - M2_HEAD / 2 - 1.8 - LABEL_H / 2
            cs = cs + text_section(LABELS[k], LABEL_H).translate([SLOT_OFFSET, y])
        out.append((x, SLIDER_Y, cs))
    # ノブ: 奥を中心に回転角ぶん、手前は空ける
    r0, r_short, r_long, sweep = KNOB_TICK
    ticks = []
    for i in range(n):
        a = -sweep / 2 + sweep * i / (n - 1) + KNOB_ROT
        r1 = r_long if i in (0, n // 2, n - 1) else r_short
        ticks.append(CrossSection.square([tw, r1 - r0]).translate([-tw / 2, r0]).rotate(-a))
    out.append((KNOB_X, KNOB_Y, CrossSection.batch_boolean(ticks, manifold3d.OpType.Add)))
    if LOGO:
        out.append((W - EDGE - 9.0, KNOB_Y, text_section(LOGO, LOGO_H, anchor="right")))
    return out


def marks_solid(depth, extra=0.0):
    """目盛りを厚さ depth (+上に extra) の立体にしてパネルに置く"""
    ms = [on_panel(Manifold.extrude(cs, depth + extra).translate([0, 0, -depth]), x, y) for x, y, cs in marks_2d()]
    return Manifold.batch_boolean(ms, manifold3d.OpType.Add)


def slot_chamfer(w, l, c):
    """溝の上の縁の面取り (パネル座標、上面 z=0 から下へ c)"""
    e = l / 2 - w / 2
    return Manifold.batch_hull([Manifold.cylinder(c + 0.5, w / 2, w / 2 + c + 0.5, SEG).translate([0, sy * e, -c]) for sy in (-1, 1)])


def inner_solid():
    t = TOP / math.cos(SLOPE)
    plan = Manifold.extrude(rounded_rect(W - 2 * WALL, D - 2 * WALL, max(CORNER_R - WALL, 0.5)), H_BACK + 5)
    plan = plan.translate([WALL, WALL, PLATE - 1])
    wedge = side_profile_extrude([(-1, PLATE - 1), (D + 1, PLATE - 1),
                                  (D + 1, top_z(D + 1) - t), (-1, top_z(-1) - t)], -1, W + 1)
    return plan ^ wedge


def panel_cuts():
    """パネルに開ける穴: スライダーの溝 (縁の面取りも)・M2 の皿ネジ穴・ボリュームの軸の穴と回り止めの逃げ"""
    cuts = []
    for x in SLIDER_X:
        cuts.append(on_panel(slot(*SLOT).scale([1, 1, TOP + 2]).translate([SLOT_OFFSET, 0, -TOP - 1]), x, SLIDER_Y))
        cuts.append(on_panel(slot_chamfer(*SLOT, SLOT_CHAMFER).translate([SLOT_OFFSET, 0, 0]), x, SLIDER_Y))
        for dy in (-SLIDER_SCREW_PITCH / 2, SLIDER_SCREW_PITCH / 2):
            hole = Manifold.cylinder(TOP + 2, M2_HOLE / 2, M2_HOLE / 2, SEG).translate([0, dy, -TOP - 1])
            sink = Manifold.cylinder(1.0, M2_HOLE / 2, M2_HEAD / 2, SEG).translate([0, dy, -1.0 + 0.001])
            cuts.append(on_panel(hole + sink + Manifold.cylinder(1, M2_HEAD / 2, M2_HEAD / 2, SEG).translate([0, dy, 0]), x, SLIDER_Y))
    cuts.append(on_panel(Manifold.cylinder(TOP + 2, POT_HOLE / 2, POT_HOLE / 2, SEG).translate([0, 0, -TOP - 1]), KNOB_X, KNOB_Y))
    if not PCB:   # 基板版のボリュームは基板で留めるので回り止めは無い
        tw, td, tdepth, tr = POT_TAB
        cuts.append(on_panel(Manifold.cube([tw, td, tdepth + 0.01]).translate([-tw / 2, -tr - td / 2, -TOP - 0.01]), KNOB_X, KNOB_Y))
    return Manifold.batch_boolean(cuts, manifold3d.OpType.Add)


def usb_hole():
    """USB-C の穴 (奥の面)"""
    uz = PLATE + NANO_STANDOFF + NANO[2] + USB_Z_ABOVE_BOARD
    hf, hc = USB_HOLE
    uw, uh = USB_C[0] + 2 * hf, USB_C[1] + 2 * hf
    usb = stadium_y(NANO_X, D - WALL - 1, D + 1, uz, uw, uh)
    return usb + Manifold.batch_hull([stadium_y(NANO_X, D - hc, D - hc + 0.01, uz, uw, uh),        # 外側の縁の面取り (プラグを入れやすく)
                                      stadium_y(NANO_X, D, D + 1, uz, uw + 2 * hc, uh + 2 * hc)])


def groove_ring():
    """分割溝 (上の帯と本体の境目。印刷の色替えをここに隠す)"""
    gh, gd = GROOVE
    return band_zone(BAND - gh / 2, BAND + gh / 2) - Manifold.extrude(
        rounded_rect(W - 2 * gd, D - 2 * gd, CORNER_R - gd), H_BACK + 5).translate([gd, gd, 0])


def shell():
    """一体の上ケース (底ケースの側面を作るのに使う)"""
    s = outer_solid(PLATE) - inner_solid()
    for x, y in BOSS_POS:   # 四隅のボス
        s = s + (cyl(x, y, PLATE, H_BACK, BOSS_R) ^ outer_solid(PLATE))
        s = s - cyl(x, y, PLATE - 1, PLATE + 12, BOSS_PILOT / 2)
    return s - panel_cuts() - usb_hole() - groove_ring()


# ================================================================ 天板と底ケースに分ける
# 天板は一枚もので、表を上にして刷る (2026-10-01)。パネルの下を空洞にすると裏一面にサポートが要るので、
# パネル上面から TOP_T までを中身の詰まった板にし、裏を平らにする (プレートに接する)。
#   - 色の境目の溝 (BAND) より上は外形いっぱい、それより下は底ケースの壁の内側に入る「差し込み」
#   - スライダー本体・ボリュームは裏のくぼみ (貫通) に入れる。スライダーは爪でパチッと留まる (M2 の皿ネジ穴も残す)
#   - 天板と底ケースはスナップフィット: 差し込みの側面の出っ張りが、底ケースの壁の内側の溝に掛かる。四隅の M3 でも締められる
#   - 目盛りは彫る (深さ MARK_DEPTH)。刷ったあとに塗料を流し込む
#   - サポートは外側の帯の下 (色の境目の溝の所) だけ
SLIDER_CLAW = (1.2, 6.0, 0.6, 0.2, 0.8, 22.0, 3.5, 1.1)
# スライダーの爪: 厚さ, 幅, 本体の下に掛かる量, 本体の下とのすき間, まわりの切り込みの幅, 位置 (本体の中心から前後), 付け根の深さ (パネル上面から), 返しの高さ
# 本体の高さはデータシート 7.0±0.5。M2 でネジ留めもするので、中間より少し長め (7.2) の本体に掛かる位置にする
TOP_T_WIRED = TOP + SLIDER_BODY[2] + SLIDER_CLAW[3] + SLIDER_CLAW[7]   # 配線版の天板の厚さ (基板版のスペーサーの厚さに使う)
TOP_T = TOP_T_WIRED                          # 天板の厚さ (パネル上面から裏まで)
if PCB:
    TOP_T = TOP + SLIDER_BODY[2] - PCB_PLATE_GAP                  # 基板版: 裏が基板の上面から PCB_PLATE_GAP 上
POCKET_FIT = 0.3                             # くぼみと部品のすき間 (片側)
SNAP = (7.5, 0.7, 0.4, 0.6, 0.3, 40.0)
# 天板と底ケースのスナップ: 深さ (パネル上面から), 差し込みの出っ張りの高さ, 出っ張りの先の平らな所, 溝の深さ (壁の内側から), 溝の下側のすき間, 長さ (各辺の中央)
# 差し込みの側面は壁の内側から FIT 離れているので、壁が押し広げられる量は 出っ張り - FIT。
# はまった時に出っ張りの上の斜面が溝の上の斜面にぴったり触れる寸法にする (ガタが出ない。ユーザーのコツ)。下側と奥はすき間
SCREW_ENGAGE = 4.9                           # 四隅の M3 が天板の裏に入る長さ


def plan_prism(inset):
    """外形を inset だけ内側へずらした角丸長方形の柱 (上下に長い)"""
    return Manifold.extrude(rounded_rect(W - 2 * inset, D - 2 * inset, max(CORNER_R - inset, 0.3)), H_BACK + 20).translate([inset, inset, -5])


def taper(i0, d0, i1, d1):
    """パネル上面からの深さ d0 で inset i0、d1 で i1 になる錐台"""
    return Manifold.batch_hull([plan_prism(i0) ^ band_zone(d0 - 0.001, d0), plan_prism(i1) ^ band_zone(d1, d1 + 0.001)])


def snap_bars(face, prof, length, depth):
    """各辺の中央に置く棒 (スナップの出っ張り・溝)。prof は断面 [(内向きの距離, 深さ)] (側面 face が 0、外向きが負)"""
    cs = CrossSection([[(u, -d) for u, d in prof]])
    bar = Manifold.extrude(cs, length).transform(np.array([[0, 0, 1, -length / 2], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))   # (u 辺に沿う, v 内向き, w 上)
    k = depth * math.sin(SLOPE)   # 手前と奥はパネルの傾きで深さ depth の所が前後にずれるので戻す (左右は辺に沿ったずれなので要らない)
    return Manifold.batch_boolean([on_panel(bar, W / 2, face - k), on_panel(bar.rotate([0, 0, 180]), W / 2, D - face - k),
                                   on_panel(bar.rotate([0, 0, -90]), face, D / 2), on_panel(bar.rotate([0, 0, 90]), W - face, D / 2)],
                                  manifold3d.OpType.Add)


def snap_profiles():
    """スナップの断面 (出っ張り, 溝)。どちらも側面から内へ 0.3 入れる (面がぴったり重ならないように)"""
    sp, sh, sf, gd, gc, span = SNAP
    a, b = sp - sf / 2, sp + sf / 2          # 出っ張りの先の平らな所
    bump = [(0.3, a - sh - 0.3), (0.0, a - sh), (-sh, a), (-sh, b), (0.0, b + sh), (0.3, b + sh + 0.3)]
    # 溝: 上の斜面は出っ張りの上の斜面と同じ線 (壁の内側は差し込みの側面より FIT 外)。底は出っ張りの先より gc 下まで、下は 45°
    top_at = lambda u: a - sh + FIT - u      # 出っ張りの上の斜面の深さ (壁の内側から u の所)
    bot = b + gc
    groove = [(0.3, top_at(0.3)), (-gd, top_at(-gd)), (-gd, bot), (0.0, bot + gd), (0.3, bot + gd + 0.3)]
    return bump, groove


def pockets():
    """天板の裏のくぼみ (スライダー本体・ボリューム)。パネル上面から TOP より下を貫通"""
    f, e = POCKET_FIT, 0.0
    bw, bl, _ = SLIDER_BODY
    ps = [on_panel(Manifold.cube([bw + 2 * f, bl + 2 * f, TOP_T + 1]).translate([SLOT_OFFSET - bw / 2 - f, -bl / 2 - f, -TOP_T - 1]), x, SLIDER_Y)
          ^ band_zone(TOP - e, TOP_T + 1) for x in SLIDER_X]
    if PCB:
        # ボリューム (JH16K6): 本体と、右の端子の付け根の板・軸と平行に後ろへ伸びる端子の逃げ (どちらも貫通)
        r = POT_BODY[0] / 2 + f + 0.2
        lr, lp, l0, l1, lw, lt = JH16K6_LEAD
        br, bh, b0, b1 = JH16K6_BLOCK
        hw = max(lp + lw / 2, bh) + f
        side = Manifold.cube([lr + lt / 2 + 0.5 + f, 2 * hw, TOP_T + 1]).translate([0, -hw, -TOP_T - 1])
        ps.append(on_panel(Manifold.cylinder(TOP_T + 1, r, r, SEG).translate([0, 0, -TOP_T - 1]) + side, KNOB_X, KNOB_Y) ^ band_zone(TOP - e, TOP_T + 1))
        # コネクター・抵抗・コンデンサーの足とハンダの逃げ (make_pcb.py が足のまわり 1.5 の四角で書く)
        info = pcb_info()
        for x0, v0, x1, v1 in info["reliefs"]:
            m = Manifold.cube([x1 - x0, v1 - v0, PCB_TOP_RELIEF + 1]).translate([x0, v0 - SLIDER_Y, -TOP_T - 1])
            ps.append(on_panel(m, 0, SLIDER_Y) ^ band_zone(TOP_T - PCB_TOP_RELIEF, TOP_T + 1))
        return Manifold.batch_boolean(ps, manifold3d.OpType.Add)
    r = POT_BODY[0] / 2 + f + 0.2          # 回り止めの逃げ (中心から 7.8 + 0.9) も入る大きさ
    pr, pd = POT_BODY[0] / 2, POT_BODY[1]
    lugs = Manifold.cube([pr + 5 + f, 15 + 2 * f, TOP_T + 1]).translate([0, -7.5 - f, -TOP_T - 1])         ^ Manifold.cube([40, 40, pd - 1 + 2 + f]).translate([-20, -20, -TOP - pd + 1 - 1])   # 端子 (右向き、本体の下の方から出る) の逃げ
    ps.append(on_panel(Manifold.cylinder(TOP_T + 1, r, r, SEG).translate([0, 0, -TOP_T - 1]) + lugs, KNOB_X, KNOB_Y) ^ band_zone(TOP - e, TOP_T + 1))
    return Manifold.batch_boolean(ps, manifold3d.OpType.Add)


def slider_claws():
    """スライダーの爪。(削る切り込み, 足す返し) を返す。爪はくぼみの長い壁を U 字に切り離した板で、付け根はパネル側、先は裏の口。
    先の返しが本体の下に掛かる (本体を裏から押し込むと、返しの斜面で爪が外へ逃げる)"""
    t, w, catch, gap, slit, yc, root, hb = SLIDER_CLAW
    bw, bl, bh = SLIDER_BODY
    a = bw / 2 + POCKET_FIT                  # くぼみの壁 (本体の中心から)
    zc = -(TOP + bh + gap)                   # 返しの上面 (本体の下の面の gap 下)
    cuts, barbs = [], []
    for sx in (-1, 1):
        for sy in (-1, 1):
            y = sy * yc
            cut = Manifold.cube([t + slit, w + 2 * slit, TOP_T + 1 - root]).translate([a, y - w / 2 - slit, -TOP_T - 1])                 - Manifold.cube([t, w, TOP_T - root]).translate([a, y - w / 2, -TOP_T])
            # 返し: 上面は平ら (本体の下を受ける)、下は口に向かって細くなる斜面
            # 爪の板へ 0.4 食い込ませてつなぐ。斜面は裏 (-TOP_T) で a に来る線をそのまま裏より下までのばし、最後に裏の面でまとめて切る
            # (点や面が切る面にぴったり乗ると STL が乱れた)
            # 裏の面ではくぼみの壁より 0.1 内側を通す (壁・爪の面・裏の面が 1 本の線で出会わないように)。幅も爪より両側 0.1 狭く
            k = (catch - 0.1) / (TOP_T + zc - 0.2)  # 斜面の傾き (深さ 1 あたりの横)
            ext = 0.5
            prof = CrossSection([[(a + 0.4, zc), (a - catch, zc), (a - catch, zc - 0.2), (a - 0.1 + k * ext, -TOP_T - ext), (a + 0.4, -TOP_T - ext)]])
            barb = Manifold.extrude(prof, w - 0.2).rotate([90, 0, 0]).translate([0, y + w / 2 - 0.1, 0])
            if sx < 0:
                cut, barb = cut.mirror([1, 0, 0]), barb.mirror([1, 0, 0])
            cuts.append(cut)
            barbs.append(barb)
    place = lambda ms: Manifold.batch_boolean([on_panel(m.translate([SLOT_OFFSET, 0, 0]), x, SLIDER_Y) for x in SLIDER_X for m in ms], manifold3d.OpType.Add)
    return place(cuts), place(barbs)


def split(s, b):
    """一体の上ケース s と底板 b を、天板 と 底ケース に分ける"""
    above = band_zone(-5, BAND)
    plug_in = WALL + FIT                      # 差し込みの側面 (外形から)
    # 天板: 溝より上の外形 + 差し込み (パネルの下から裏まで) を 1 つの塊にして、穴・くぼみ・爪を削る。
    # 部品を 0.01 重ねてつなぐ・彫りを埋め戻す などをすると、面がぴったり重なった所で STL が乱れた (2026-10-01)
    top = (outer_solid(PLATE) ^ band_zone(-5, BAND)) + (plan_prism(plug_in) ^ band_zone(TOP, TOP_T + 1))
    bump, groove = snap_profiles()
    top = top + snap_bars(plug_in, bump, SNAP[5], SNAP[0])        # スナップの出っ張り
    if PCB:   # 基板版は本体の下に基板があるので爪は無し (M2 で留める)
        top = top - panel_cuts() - groove_ring() - marks_solid(MARK_DEPTH, 1.0) - pockets()   # 目盛りの彫り
    else:
        cuts, barbs = slider_claws()
        top = top - panel_cuts() - groove_ring() - marks_solid(MARK_DEPTH, 1.0) - pockets() - cuts + barbs   # 目盛りの彫り
    tip = TOP_T - SCREW_ENGAGE
    pilots = Manifold.batch_boolean([cyl(x, y, 0, H_BACK + 5, BOSS_PILOT / 2) for x, y in BOSS_POS], manifold3d.OpType.Add)
    top = (top - (pilots ^ band_zone(tip - 0.5, TOP_T + 1))) ^ band_zone(-5, TOP_T)   # 裏の面でまとめて切る
    # 底ケース: 溝より下の側面 + 底板 + 四隅の柱 (天板の裏の BOSS_GAP 下まで)、壁の内側にスナップの溝
    bosses = Manifold.batch_boolean([cyl(x, y, PLATE - 1, H_BACK + 5, BOSS_R + 0.01) for x, y in BOSS_POS], manifold3d.OpType.Add)
    cols = Manifold.batch_boolean([cyl(x, y, PLATE - 0.5, H_BACK + 5, SCREW[1] / 2 + COL_WALL) for x, y in BOSS_POS], manifold3d.OpType.Add)
    cols = cols ^ band_zone(TOP_T + BOSS_GAP, H_BACK + 10) ^ outer_solid(0)
    holes = [cyl(x, y, -1, H_BACK + 5, M3_HOLE / 2) for x, y in BOSS_POS]
    holes += [cyl(x, y, -1, screw_seat_z(y), SCREW[1] / 2) for x, y in BOSS_POS]     # 座ぐり (頭を沈める)
    groove = snap_bars(WALL, groove, SNAP[5] + 2, SNAP[0])   # 出っ張りより少し長い溝
    bottom_case = (s - above - bosses) + b + cols - Manifold.batch_boolean(holes, manifold3d.OpType.Add) - groove
    return top, bottom_case


def solid_only(m):
    """面がぴったり重なった所に残る体積ゼロの膜を捨てる"""
    return Manifold.batch_boolean([p for p in m.decompose() if p.volume() > 0.1], manifold3d.OpType.Add)


def for_print_up(m, ref=None):
    """表を上にする (傾きを戻す)。ref を渡すと ref と同じ位置に動かす (目盛りの色付け用)"""
    def turn(x):
        return x.translate([0, 0, -H_FRONT]).rotate([-math.degrees(SLOPE), 0, 0])
    b = turn(m if ref is None else ref).bounding_box()
    return turn(m).translate([-b[0], -b[1], -b[2]])


def screw_seat_z(y):
    """座ぐりの底 (ネジ頭の座面) の高さ: ネジの先が天板の裏から SCREW_ENGAGE 入る位置"""
    return top_z(y) - (TOP_T - SCREW_ENGAGE) / math.cos(SLOPE) - SCREW[0]


def screw_report():
    """四隅の座ぐりの深さと、天板へのかかり"""
    c = math.cos(SLOPE)
    out = []
    for x, y in BOSS_POS:
        seat = screw_seat_z(y)
        col_top = top_z(y) - (TOP_T + BOSS_GAP) / c
        out.append((x, y, seat, SCREW_ENGAGE, col_top - seat))
    return out


# ================================================================ 底板

def bottom():
    c = PLATE_CHAMFER
    p = Manifold.batch_hull([
        Manifold.extrude(rounded_rect(W - 2 * c, D - 2 * c, CORNER_R - c), PLATE).translate([c, c, 0]),
        Manifold.extrude(rounded_rect(W, D, CORNER_R), PLATE - c).translate([0, 0, c]),
    ])

    # ゴム足の凹み
    fr, fd = FEET
    for x, y in [(18, 18), (W - 18, 18), (18, D - 18), (W - 18, D - 18)]:
        p = p - cyl(x, y, -0.01, fd, fr / 2)

    # Nano の受け: 基板の両脇を支える台と壁、手前の止め
    nw, nl, _ = NANO
    y1 = nano_board_end()
    y0 = y1 - nl
    zt = PLATE + NANO_STANDOFF + NANO[2]
    yw = max(y0, NANO_WALL_FROM)  # 台の壁はスライダーの奥端より奥だけ (配線と当たらないように)
    for sx in (-1, 1):
        edge = NANO_X + sx * (nw / 2 + FIT)
        wall = box(min(edge, edge + sx * 1.6), yw, PLATE, max(edge, edge + sx * 1.6), D - WALL + 0.5, zt + 1.2)
        ledge = box(min(edge, edge - sx * 1.5), yw, PLATE, max(edge, edge - sx * 1.5), y1, PLATE + NANO_STANDOFF)
        p = p + wall + ledge
        # 奥の止め: 基板の奥端を受ける (ケーブルを抜くときに基板が奥へ動かないように)。USB-C の両脇
        if D - WALL - y1 > 0.05:
            xa, xb = NANO_X + sx * (USB_C[0] / 2 + 1.0), NANO_X + sx * (nw / 2 + FIT)
            p = p + box(min(xa, xb), y1, PLATE, max(xa, xb), D - WALL + 0.5, zt + 1.0)
    p = p + box(NANO_X - 4, y0 - NANO_STOP_T, PLATE, NANO_X + 4, y0, zt + 1.2)
    return p


def nano_seat_z():
    """基板の下面の実際の高さ。基板が設計より薄い (1.1) ので、台 (PLATE + NANO_STANDOFF) に載せると USB-C が穴の下の縁に当たる。
    USB-C が穴の下の縁から NANO_USB_MARGIN 浮く高さにする (台より約 0.45 上。支えと押さえでこの高さに保つ)"""
    hole_bottom = PLATE + NANO_STANDOFF + NANO[2] + USB_Z_ABOVE_BOARD - USB_C[1] / 2 - USB_HOLE[0]
    return hole_bottom + NANO_USB_MARGIN - NANO_THICK


def nano_clip(board_w=NANO_BOARD[0]):
    """Nano の押さえ (組み込んだ位置)。床に置く 1 つの箱から、手前の止め (8 x NANO_STOP_T) が入るくぼみと、基板の手前の端が入るすき間を削った形。
    すき間の上が基板を押さえ、下が裏を床から受け、左右が爪になって角を挟む。
    基板を差し込んでから一緒にケースへ下ろす。印刷は手前の面を下に立てる (nano_clip_print)"""
    c, t, stop_gap, roof, lift = NANO_CLIP
    sc, st, sl = NANO_CLIP_SIDE
    y1 = nano_board_end()
    y0 = y1 - NANO[1]                                  # 止めの奥の面
    stop_top = PLATE + NANO_STANDOFF + NANO[2] + 1.2   # 印刷済みの止めの上面
    board_front = y1 - NANO_BOARD[1]
    zb = nano_seat_z()                                 # 基板の下面
    top = stop_top + stop_gap + roof
    xo = board_w / 2 + sc + st                         # 外側の半幅 (爪の外側)
    front = y0 - NANO_STOP_T - c - t
    body = box(NANO_X - xo, front, PLATE, NANO_X + xo, board_front + sl, top)
    pocket = box(NANO_X - 4 - c, y0 - NANO_STOP_T - c, PLATE - 1, NANO_X + 4 + c, y0 + c, stop_top + stop_gap)      # 止めが入るくぼみ (下は開く)
    slot = box(NANO_X - board_w / 2 - sc, y0 + c, zb, NANO_X + board_w / 2 + sc, y1, zb + NANO_THICK + lift)   # 基板が入るすき間 (奥は開く)
    return body - pocket - slot


def nano_clip_print(board_w=NANO_BOARD[0]):
    """手前の面を下にして立てる向き (どの断面も下の断面に載るのでサポート不要)"""
    m = nano_clip(board_w).rotate([90, 0, 0])
    b = m.bounding_box()
    return m.translate([-b[0], -b[1], -b[2]])


def pcb_spacers():
    """基板版の天板を配線版の底ケースに載せる時の、四隅の柱のスペーサー 4 個 (2026-10-08)。
    基板版の天板は配線版より薄く、配線版の柱の上に TOP_T_WIRED - TOP_T のすき間ができる。柱の上面と天板の裏はどちらもパネルと平行なので平らな輪でよい。
    外径は柱と同じ、穴は柱と同じ M3 の通し穴。柱とのすき間 BOSS_GAP はそのまま残る"""
    t = TOP_T_WIRED - TOP_T
    r = SCREW[1] / 2 + COL_WALL
    ring = Manifold.cylinder(t, r, r, SEG) - Manifold.cylinder(t + 2, M3_HOLE / 2, M3_HOLE / 2, SEG).translate([0, 0, -1])
    step = 2 * r + 3
    return Manifold.batch_boolean([ring.translate([r + step * (i % 2), r + step * (i // 2), 0]) for i in range(4)], manifold3d.OpType.Add)


# ================================================================ ツマミ

def arc_pts(c, r, a0, a1, n=16):
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * i / n), c[1] + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def cap_side_pts():
    """横から見た形の右半分 (y, z)。上の中心 → 谷の弧 → 山 → ふくらんだ端の弧 → 下の帯 → 底 の順"""
    cw, cl, ch, cdepth = CAP
    hv, hp, hl = cdepth + CAP_CEIL, ch, cl / 2
    bh, by, _ = CAP_BAND
    yh, hw, hc = CAP_HORN
    yi = yh - hw                                   # 山の内側の角
    # 上面: (0, hv) と (yi, hp - hc) を通り、中心が y=0 上にある円弧
    zi = hp - hc
    rt = (yi ** 2 + (zi - hv) ** 2) / (2 * (zi - hv))
    top = arc_pts((0.0, hv + rt), rt, -math.pi / 2, -math.pi / 2 + math.asin(yi / rt), 24)
    # 両端: 帯の上の角 (hl, bh) で縦に接し、山の外側の角 (yh, hp) を通る円弧 (中心は z=bh 上)
    re = ((hl - yh) ** 2 + (hp - bh) ** 2) / (2 * (hl - yh))
    ce = (hl - re, bh)
    end = arc_pts(ce, re, math.atan2(hp - bh, yh - ce[0]), 0.0, 20)
    return top + [(yi + hc, hp)] + end + [(hl - by, 0.0)]


def cap_profile(grow=0.0, z0=0.0):
    """ツマミをスライド方向の横から見た輪郭 (CrossSection、座標は (y, z))。grow で外へ太らせる (負で内側へ)。
    z0 < 0 なら底を開けて z0 まで下げる"""
    cw, cl, ch, cdepth = CAP
    right = cap_side_pts()
    pts = right + [(-y, z) for y, z in reversed(right[1:])]     # 上の中心から右回り
    cs = CrossSection([pts[::-1]])                             # 反時計回りにして渡す
    if z0 < 0:
        hb = right[-1][0]
        cs = cs + CrossSection.square([2 * hb, 5]).translate([-hb, -5])
    if grow:
        cs = cs.offset(grow, join_type=manifold3d.JoinType.Miter)
    return cs ^ CrossSection.square([cl + 10, ch + 10 - min(z0, 0)]).translate([-cl / 2 - 5, min(z0, 0)])


def cap_top_z(y):
    """上面 (谷の弧) の高さ。溝を彫る位置の計算用"""
    top = [p for p in cap_side_pts() if p[0] <= CAP_HORN[0] - CAP_HORN[1]][:25]
    y = abs(y)
    for (y0, z0), (y1, z1) in zip(top, top[1:]):
        if y <= y1:
            return z0 + (z1 - z0) * (y - y0) / (y1 - y0)
    return top[-1][1]


def edge_offset(poly, dist):
    """多角形 (反時計回り) の各辺を内側へ dist(i) だけ平行にずらし、隣り合う辺の交点を新しい頂点にする (頂点数は変えない)"""
    n = len(poly)
    P = np.asarray(poly, dtype=float)
    out = []
    for i in range(n):
        a, b, c = P[i - 1], P[i], P[(i + 1) % n]
        lines = []
        for (p, q), d in (((a, b), dist(i - 1)), ((b, c), dist(i))):
            t = (q - p) / np.linalg.norm(q - p)
            nrm = np.array([-t[1], t[0]])                  # 反時計回りなので左が内側
            lines.append((p + nrm * d, t, nrm, d))
        (p1, t1, n1, d1), (p2, t2, n2, d2) = lines
        det = t1[0] * t2[1] - t1[1] * t2[0]
        if abs(det) < 1e-6:
            out.append(b + n1 * max(d1, d2))
        else:
            k = ((p2 - p1)[0] * t2[1] - (p2 - p1)[1] * t2[0]) / det
            out.append(p1 + t1 * k)
    return out


def loft_x(P, Q, x0, x1):
    """(y, z) の多角形 P を x=x0、同じ頂点数の Q を x=x1 に置いてつないだ立体"""
    n = len(P)
    v = np.array([(x0, y, z) for y, z in P] + [(x1, y, z) for y, z in Q], dtype=np.float32)
    tris = []
    capP = manifold3d.triangulate([np.asarray(P, dtype=float)])
    capQ = manifold3d.triangulate([np.asarray(Q, dtype=float)])
    tris += [(t[0], t[2], t[1]) for t in capP]                     # x0 側 (外向き -x)
    tris += [(t[0] + n, t[1] + n, t[2] + n) for t in capQ]         # x1 側 (外向き +x)
    for i in range(n):
        j = (i + 1) % n
        tris += [(i, j, j + n), (i, j + n, i + n)]
    m = Manifold(manifold3d.Mesh(v, np.array(tris, dtype=np.uint32)))
    if m.is_empty():
        tris = [(a, c, b) for a, b, c in tris]
        m = Manifold(manifold3d.Mesh(v, np.array(tris, dtype=np.uint32)))
    return m


def cap_solid(grow=0.0, z0=0.0, edge=None):
    """ツマミの外形 (grow で太らせる / 負で内側。z0 < 0 で底を開けて下へ伸ばす)。底面中心が原点、スライド方向が Y"""
    cw, cl, ch, cdepth = CAP
    bh, _, bx = CAP_BAND
    hy = cl / 2 + 2

    def slab(hx, z):
        return Manifold.cube([2 * hx, 2 * hy, 0.01]).translate([-hx, -hy, z])
    if z0 < 0:   # 内側: 帯の所は縦に下ろす
        levels = [(cw / 2 - bx + grow, z0 - 0.01), (cw / 2 - bx + grow, 0.0), (cw / 2 + grow, bh)]
    else:
        levels = [(cw / 2 - bx + grow, -0.01), (cw / 2 + grow, bh)]
    block = Manifold.batch_hull([slab(hx, z) for hx, z in levels + [(cw / 2 + grow, ch + 2)]])
    to_x = np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
    prof = cap_profile(grow, z0)
    e = CAP_EDGE if edge is None else edge
    if z0 < 0 or e <= 0:
        side = Manifold.extrude(prof, cw + 4).transform(to_x).translate([-cw / 2 - 2, 0, 0])
        return block ^ side
    # 両側面の縁の面取り: 輪郭を内側へ CAP_EDGE ずらした形 (下の帯と底はずらさない) へ 45° でつなぐ
    P = [tuple(q) for q in max(prof.to_polygons(), key=len)]
    zb = bh + grow + 1e-6
    Q = edge_offset(P, lambda i: 0.0 if max(P[i][1], P[(i + 1) % len(P)][1]) <= zb else e)
    hx = cw / 2 + grow
    side = Manifold.extrude(prof, 2 * (hx - e) + 0.02).transform(to_x).translate([-(hx - e) - 0.01, 0, 0])   # 面取り部と少し重ねて1つにする
    side = side + loft_x(P, Q, hx - e, hx) + loft_x(Q, P, -hx, -(hx - e))
    return block ^ side


def lever_hole(body, f, snap=None):
    """body にツマミの差し込み穴をあけ、爪を付けて返す。f は四方のすき間、snap は爪のかかり。
    穴の両端 (Y) の壁に、壁と一体の爪を付ける"""
    cdepth = CAP[3]
    lt, lw, _ = LEVER
    npos, nw, nd = LEVER_NOTCH
    grip, play = CAP_SNAP if snap is None else (snap, CAP_SNAP[1])
    x0 = SLOT_OFFSET
    hx, hy = lt / 2 + f, lw / 2 + f                      # 穴の半分の大きさ
    hole = Manifold.cube([2 * hx, 2 * hy, cdepth + 0.01]).translate([x0 - hx, -hy, -0.01])
    # 爪: 横から見て丸い出っ張り (X 向きの円柱の一部)。半径は溝の幅から上下 play を引いた半分で、先がレバーの縁より grip 内側へ出る
    r = nw / 2 - play
    zc = cdepth - npos - nw / 2                           # 溝の中央の高さ
    yt = lw / 2 - grip                                    # 爪の先
    assert yt > lw / 2 - nd and r > grip, "爪がレバーの溝に収まらない"
    claw = (Manifold.cylinder(lt, r, r, 48).rotate([0, 90, 0])
            .translate([x0 - lt / 2, yt + r, zc]))        # 幅はレバーの厚さ (穴の両脇に 0.2 空く)
    claws = claw + claw.mirror([0, 1, 0])
    return (body - hole) + (claws ^ body)


def fit_test():
    """はめ合いのテストピース: ツマミの下の部分 (穴の天井 FIT_TEST[1] を残して上を平らに切る) を、爪のかかりを変えて並べる。
    1色の一体物。上面にかかり (1/100 mm) を刻む"""
    fits, ceil, gap = FIT_TEST
    cw, cl, ch, cdepth = CAP
    zt = cdepth + ceil
    body = cap_solid() ^ Manifold.cube([cw + 4, cl + 4, zt]).translate([-cw / 2 - 2, -cl / 2 - 2, 0])
    out = []
    for i, f in enumerate(fits):
        label = text_section(f"{round(f * 100):02d}", 3.4).rotate(90)
        b = label.bounds()
        label = label.translate([-(b[0] + b[2]) / 2, 7.0 - (b[1] + b[3]) / 2])        # 平らな上面の端寄り
        mark = Manifold.extrude(label, 0.6).translate([0, 0, zt - 0.4])
        out.append((lever_hole(body, CAP_FIT, f) - mark).translate([(i - (len(fits) - 1) / 2) * (cw + gap), 0, 0]))
    return Manifold.batch_boolean(out, manifold3d.OpType.Add)


def cap_shape():
    """スライダーのツマミ。底面中心が原点、スライド方向が Y。(黒い外殻 前後2つ, 乳白の芯) を返す"""
    cw, cl, ch, cdepth = CAP
    solid = cap_solid()
    slab = Manifold.cube([cw + 2, CAP_FIN, ch + 2]).translate([-cw / 2 - 1, -CAP_FIN / 2, -1])
    floor = Manifold.cube([cw + 4, cl + 4, ch + 4]).translate([-cw / 2 - 2, -cl / 2 - 2, 0])
    inner = cap_solid(-CAP_WALL, -1.0)
    fin = cap_solid(CAP_LINE_RAISE, edge=CAP_LINE_EDGE) ^ slab ^ floor                  # 白いライン: 上面と側面から少し盛り上がる
    core = (inner ^ floor) + fin
    core = lever_hole(core, CAP_FIT)
    # 外殻: 上面の V 溝 (面に直角に彫る)。溝は両側面まで抜ける
    ys, gw, gd = CAP_NOTCH
    grooves = []
    for y in ys:
        for sy in (-1, 1):
            yy = sy * y
            z = cap_top_z(yy)
            dz = (cap_top_z(yy + 0.05) - cap_top_z(yy - 0.05)) / 0.1      # 面の傾き
            nrm = np.array([-dz, 1.0]) / math.hypot(dz, 1.0)            # 面の法線 (上向き)
            tan = np.array([nrm[1], -nrm[0]])
            c = np.array([yy, z])
            tri = [c + tan * (gw / 2) + nrm * 0.3, c - nrm * gd, c - tan * (gw / 2) + nrm * 0.3]
            v = CrossSection([[tuple(q) for q in tri]])
            if v.area() < 0 or v.is_empty():
                v = CrossSection([[tuple(q) for q in tri[::-1]]])
            grooves.append(Manifold.extrude(v, cw + 4).transform(np.array([[0, 0, 1, -cw / 2 - 2], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)))
    shell = solid - cap_solid(-(CAP_WALL - CAP_GAP), -1.0) - slab
    shell = shell - Manifold.batch_boolean(grooves, manifold3d.OpType.Add)
    halves = sorted(shell.decompose(), key=lambda m: -m.volume())[:2]   # 前後の2つ
    return halves[0] + halves[1], core


def knob_bore(grip=None):
    """ノブの軸穴 (引く形)。底面が z=0。入口は丸 (外径) で面取り、奥の KNOB_GRIP[2] だけ内向きの山がある"""
    od, gd, glen, n = KNOB_GRIP
    gd = gd if grip is None else grip
    plain = Manifold.cylinder(KNOB_BORE + 0.01, od / 2, od / 2, SEG).translate([0, 0, -0.01])
    entry = Manifold.cylinder(0.6, od / 2 + 0.5, od / 2, SEG).translate([0, 0, -0.01])
    # 山: 外径と内径を交互に結んだ星形の穴。山 (内径) の所が軸の溝に入る
    star = []
    for k in range(2 * n):
        r = gd / 2 if k % 2 == 0 else od / 2
        a = math.pi * k / n
        star.append((r * math.cos(a), r * math.sin(a)))
    grip_zone = Manifold.extrude(CrossSection([star]), glen).translate([0, 0, KNOB_BORE - glen])
    # 山の付け根に 45° の斜面 (上を下にして刷ったとき、山が宙に浮かないように。軸も入りやすい)
    lead = Manifold.cylinder((od - gd) / 2, od / 2, gd / 2, SEG).translate([0, 0, KNOB_BORE - glen - (od - gd) / 2])
    hole = plain - (Manifold.cylinder(KNOB_BORE + 1, od / 2 + 1, od / 2 + 1, SEG).translate([0, 0, KNOB_BORE - glen]) - grip_zone)
    hole = hole - (Manifold.cylinder((od - gd) / 2, od / 2 + 1, od / 2 + 1, SEG).translate([0, 0, KNOB_BORE - glen - (od - gd) / 2]) - lead)
    return hole + entry


def knob_shape(grip=None):
    """回転ボリュームのノブ (印刷用。外形は ABS-28 と同じ寸法)。底面中心が原点。(本体, 指示線) を返す。
    側面に縦溝、上面の縁は面取り、上面に乳白の指示線を埋める (手前 -Y 向き)。底のくぼみはナットにかぶさる"""
    d, h, hk = KNOB
    rd, rdepth = KNOB_RECESS
    r = d / 2
    prof = [(0, 0), (r - 0.4, 0), (r, 0.4), (r, hk - 1.5), (r - 0.9, h - 0.3), (r - 1.2, h), (0, h)]
    body = Manifold.revolve(CrossSection([prof]), SEG)
    n, gr = KNOB_KNURL
    grooves = [Manifold.cylinder(hk - 1.5 - 1.0, gr, gr, 12).translate([r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), 1.0])
               for k in range(n)]
    body = body - Manifold.batch_boolean(grooves, manifold3d.OpType.Add)
    # 底のくぼみ (ナットにかぶさる)。軸のまわりは筒で残す
    if rdepth > 0:
        body = body - (Manifold.cylinder(rdepth, rd / 2, rd / 2, SEG) - Manifold.cylinder(rdepth + 1, 4.2, 4.2, SEG)).translate([0, 0, -0.01])
    body = body - knob_bore(grip)
    lw, l0, ld = KNOB_LINE
    top_r = r - 1.2
    line = Manifold.cube([lw, top_r - 0.5 - l0, ld]).translate([-lw / 2, -(top_r - 0.5), h - ld])
    groove = Manifold.cube([lw, top_r - 0.5 - l0, ld + 0.5]).translate([-lw / 2, -(top_r - 0.5), h - ld])   # 上面と同じ高さで切ると閉じた空洞になるので上へ抜く
    return body - groove, line


def knob_print():
    """ノブを印刷の向きに (上面を下に。指示線が最初の数層になるので色替えが少ない)。(本体, 指示線)"""
    body, line = knob_shape()
    h = KNOB[1]
    turn = lambda m: m.rotate([180, 0, 0]).translate([0, 0, h])
    return turn(body), turn(line)


def knob_fit_test():
    """ノブの軸穴のはめ合いテスト: 軸穴の山の部分だけを、山の内径を変えて 3 つ並べる (1色)。
    上面に内径 (1/10 mm、例 56 = 5.6) を刻む。ボリュームの軸に押し込んで、回らずに手で抜ける程度を選ぶ"""
    od, gd, glen, n = KNOB_GRIP
    hb = glen + 1.0                      # 山の長さ + 入口
    out = []
    for i, g in enumerate(KNOB_FIT_TEST):
        block = Manifold.cube([12.0, 18.0, hb]).translate([-6.0, -6.0, 0])
        hole = knob_bore(g).translate([0, 0, -(KNOB_BORE - hb)])          # 穴の奥を上面に合わせ、上まで抜く
        hole = hole + Manifold.cylinder(2, od / 2, od / 2, SEG).translate([0, 0, hb - 0.5])
        label = text_section(f"{round(g * 10)}", 3.4)
        b = label.bounds()
        label = label.translate([-(b[0] + b[2]) / 2, 8.5 - (b[1] + b[3]) / 2])
        mark = Manifold.extrude(label, 0.6).translate([0, 0, hb - 0.4])
        out.append((block - hole - mark).translate([i * 15.0, 0, 0]))
    return Manifold.batch_boolean(out, manifold3d.OpType.Add)


def knob_turn():
    """表示するノブの向き: 指示線 (手前向きで作ってある) を最小の目盛りに合わせる回転 (Z 軸まわり、度)"""
    a0 = -KNOB_TICK[3] / 2 + KNOB_ROT          # 最小の目盛りの向き (奥から時計回り)
    return 180.0 - a0


def knob_lift():
    """ノブの底のパネルからの高さ: 軸を切らずに奥まで差した位置"""
    return SHAFT - TOP - KNOB_BORE


# ================================================================ 確認用の部品

def slider_parts(x, flip):
    """PTL60 1本分: 本体、両端の端子、端子の下の配線の余裕 (パネル座標で作って置く)"""
    bw, bl, bh = SLIDER_BODY
    z_body = -TOP - bh
    body = Manifold.cube([bw, bl, bh]).translate([-bw / 2, -bl / 2, z_body])
    pins = []
    wires = []
    for end, offsets in ((-PIN_END, PINS_4), (PIN_END, PINS_2)):
        for ox in offsets:
            pins.append(Manifold.cube([0.6, 1.0, SLIDER_PINS]).translate([ox - 0.3, end - 0.5, z_body - SLIDER_PINS]))
        x0, x1 = min(offsets) - 1.2, max(offsets) + 1.2
        if PCB:   # 基板版: 基板の裏に出る端子とハンダ
            wires.append(Manifold.cube([x1 - x0, 2.0, PCB_SOLDER]).translate([x0, end - 1.0, z_body - PCB_T - PCB_SOLDER]))
        else:
            wires.append(Manifold.cube([x1 - x0, 4.0, WIRE_MARGIN]).translate([x0, end - 2.0, z_body - SLIDER_PINS - WIRE_MARGIN]))
    pins = Manifold.batch_boolean(pins, manifold3d.OpType.Add)
    wires = Manifold.batch_boolean(wires, manifold3d.OpType.Add)

    def place(m):
        if flip:
            m = m.rotate([0, 0, 180])
        return on_panel(m, x, SLIDER_Y)
    return place(body), place(pins), place(wires)


def nano_board_end():
    """基板の奥端 (USB 側) の Y。USB-C の口が外壁の面から USB_RECESS 引っ込む位置から決める"""
    return D - USB_RECESS - NANO_USB_OUT


def stadium_y(x, y0, y1, zc, w, h):
    """Y 方向に伸びる長円の柱 (USB-C の口の形)"""
    r = h / 2
    return Manifold.batch_hull([Manifold.cylinder(y1 - y0, r, r, SEG).rotate([-90, 0, 0]).translate([x + sx * (w / 2 - r), y0, zc]) for sx in (-1, 1)])


def nano_parts():
    """Arduino Nano (Type-C / CH340C)。(干渉チェック用の外形, 配線の余裕, 表示用の部品 [(色の名前, 形)]) を返す
    基板: 17.8 x 43.4 x 1.1 (実測)、端子 2 x 15 (2.54 間隔、列の間隔 15.24)、ICSP 2 x 3 (USB と反対の端)
    上面: USB-C (上付け)、ATmega328P (TQFP-32、45° 回転)、リセットボタン、発振子、LED。裏面: CH340C (SOP-16)、AMS1117 (SOT-223)"""
    bw, bl = NANO_BOARD
    nt = NANO_THICK
    y1 = nano_board_end()          # 基板の奥端 (USB 側)
    y0 = y1 - bl
    zb = nano_seat_z()             # 基板の下面 (支えと押さえで台より少し上に保つ)
    zt = zb + nt
    x = NANO_X
    uw, uh, ul = USB_C
    board = box(x - bw / 2, y0, zb, x + bw / 2, y1, zt)
    # 端子の穴とパッド (表裏)
    pts = [(x + sx * 7.62, y0 + (bl - 14 * 2.54) / 2 + 2.54 * i) for sx in (-1, 1) for i in range(15)]
    pts += [(x + 2.54 * i, y0 + 1.9 + 2.54 * j) for i in (-1, 0, 1) for j in (0, 1)]      # ICSP
    holes = Manifold.batch_boolean([Manifold.cylinder(nt + 1, 0.5, 0.5, 12).translate([px, py, zb - 0.5]) for px, py in pts], manifold3d.OpType.Add)
    rings = Manifold.batch_boolean([Manifold.cylinder(nt + 0.1, 0.85, 0.85, 12).translate([px, py, zb - 0.05]) for px, py in pts], manifold3d.OpType.Add)
    pads = rings - holes
    board = board - rings
    # USB-C: 金属の殻 (長円)、口の中の空き、舌
    uc = zt + uh / 2
    ym = y1 + NANO_USB_OUT                                           # 口の面
    usb_out = stadium_y(x, ym - ul, ym, uc, uw, uh)
    shell = usb_out - stadium_y(x, ym - ul + 1.2, ym + 0.1, uc, uw - 0.6, uh - 0.6)
    tongue = box(x - 3.35, ym - ul + 1.2, uc - 0.35, x + 3.35, ym - 0.8, uc + 0.35)
    # ATmega328P (TQFP-32): 7 x 7 の本体と、まわりの足
    mcu = Manifold.cube([7.0, 7.0, 1.0]).translate([-3.5, -3.5, 0])
    leads = Manifold.cube([9.0, 9.0, 0.15]).translate([-4.5, -4.5, 0]) - Manifold.cube([7.0, 7.0, 1]).translate([-3.5, -3.5, -0.5])
    leads = leads - Manifold.cube([2.0, 2.0, 1]).translate([3.5, 3.5, -0.5]) - Manifold.cube([2.0, 2.0, 1]).translate([-5.5, 3.5, -0.5]) \
        - Manifold.cube([2.0, 2.0, 1]).translate([3.5, -5.5, -0.5]) - Manifold.cube([2.0, 2.0, 1]).translate([-5.5, -5.5, -0.5])
    gaps = []
    for k in range(9):                                               # 足の間のすき間 (0.8 間隔 8 本)
        c = -3.2 + 0.8 * k
        gaps.append(Manifold.cube([0.35, 10, 1]).translate([c - 0.175, -5, -0.5]))
        gaps.append(Manifold.cube([10, 0.35, 1]).translate([-5, c - 0.175, -0.5]))
    leads = leads - Manifold.batch_boolean(gaps, manifold3d.OpType.Add)
    cy = y0 + 15.5
    mcu = mcu.rotate([0, 0, 45]).translate([x, cy, zt])
    leads = leads.rotate([0, 0, 45]).translate([x, cy, zt])
    by = y0 + 26.0
    button = box(x - 1.75, by - 3.0, zt, x + 1.75, by + 3.0, zt + 1.5)          # リセットボタン (3 x 6)
    btn_top = box(x - 0.8, by - 1.5, zt + 1.5, x + 0.8, by + 1.5, zt + 2.3)
    xtal = box(x + 3.2, y0 + 23.0, zt, x + 4.6, y0 + 26.2, zt + 0.9)           # 16 MHz 発振子
    leds = Manifold.batch_boolean([box(x + dx - 0.4, y1 - 12.5, zt, x + dx + 0.4, y1 - 11.3, zt + 0.5) for dx in (-4.2, -2.4, 2.4, 4.2)],
                                  manifold3d.OpType.Add)                        # TX / RX / PWR / L
    # 裏面: CH340C (SOP-16) と AMS1117 (SOT-223)
    ch340 = box(x - 2.0, y1 - 20.0, zb - 1.6, x + 2.0, y1 - 10.0, zb)
    ch_leads = box(x - 3.0, y1 - 19.6, zb - 0.9, x + 3.0, y1 - 10.4, zb - 0.7)
    reg = box(x - 3.3, y0 + 5.2, zb - 1.6, x + 3.3, y0 + 8.7, zb)   # 裏は手前の端から 5mm 何もない (実物)
    reg_tab = box(x - 1.5, y0 + 8.7, zb - 0.2, x + 1.5, y0 + 10.5, zb)
    black = Manifold.batch_boolean([mcu, button, xtal, ch340, reg], manifold3d.OpType.Add)
    metal = Manifold.batch_boolean([shell, tongue, leads, ch_leads, reg_tab], manifold3d.OpType.Add)
    body = Manifold.batch_boolean([box(x - bw / 2, y0, zb, x + bw / 2, y1, zt), black, btn_top, usb_out, leds], manifold3d.OpType.Add)   # パッドの厚みは除く
    # 両側の端子列 (15 ピン) に上から配線する。USB と反対の端から 3 本目までは使わない (A 側 VIN・GND・RST、D 側 TX1・RX0・RST。
    # 線をつけるのは 4 本目の 5V / GND から) ので、4 本目の中心の 1.3 手前から。2 本目からにすると、スライダー4 の奥の端子 (3 は 1 と同じ線上) の
    # 配線の余裕と重なった (2026-10-01。実物はこの配線で組めている)
    rows = [box(x + sx * 7.62 - 1.3, y0 + (bl - 14 * 2.54) / 2 + 3 * 2.54 - 1.3, zt, x + sx * 7.62 + 1.3, y0 + (bl + 38.1) / 2 + 1.3, zt + NANO_WIRE)
            for sx in (-1, 1)]
    detail = [("nano", board), ("nano_pad", pads), ("nano_ic", black), ("nano_metal", metal), ("nano_btn", btn_top), ("nano_led", leds)]
    return body, rows[0] + rows[1], detail


def parts():
    out = []
    lt, lw, lh = LEVER
    for k, x in enumerate(SLIDER_X):
        body, pins, wires = slider_parts(x, SLIDER_FLIP[k])
        out.append(("slider", body))
        out.append(("pins", pins))
        out.append(("wires", wires))
        cw, cl, ch, cdepth = CAP
        sweep = Manifold.cube([cw, cl + TRAVEL, ch]).translate([SLOT_OFFSET - cw / 2, -(cl + TRAVEL) / 2, -TOP + lh - cdepth])
        out.append(("cap_sweep", on_panel(sweep, x, SLIDER_Y)))
        lever = Manifold.cube([lt, lw + TRAVEL, lh]).translate([SLOT_OFFSET - lt / 2, -(lw + TRAVEL) / 2, -TOP])
        out.append(("lever", on_panel(lever, x, SLIDER_Y)))
    if PCB:
        out += pcb_parts()
    else:
        pr, pd = POT_BODY[0] / 2, POT_BODY[1]
        pot = Manifold.cylinder(pd, pr, pr, SEG).translate([0, 0, -TOP - pd])
        pot = pot + Manifold.cube([6, 15, 2]).translate([pr - 1, -7.5, -TOP - pd + 1])     # 端子 (右向き)
        af, nt, wd, wt = NUT
        nut = Manifold.cylinder(wt, wd / 2, wd / 2, SEG) + Manifold.cylinder(nt, af / math.sqrt(3), af / math.sqrt(3), 6).translate([0, 0, wt])
        nut = nut - Manifold.cylinder(nt + wt + 1, 3.5, 3.5, SEG).translate([0, 0, -0.5])
        shaft = Manifold.cylinder(SHAFT - TOP, 3.0, 3.0, SEG)
        out.append(("pot", on_panel(pot, KNOB_X, KNOB_Y)))
        out.append(("pot", on_panel(nut + shaft, KNOB_X, KNOB_Y)))
    kb, kl = knob_shape()
    out.append(("knob", on_panel(kb.rotate([0, 0, knob_turn()]).translate([0, 0, knob_lift()]), KNOB_X, KNOB_Y)))
    nano, nano_wires, _ = nano_parts()
    out.append(("nano", nano))
    out.append(("nano_wires", nano_wires))
    out.append(("nano_clip", nano_clip()))
    return out


def pcb_info():
    """pcb/make_pcb.py が書いた基板の外形 (X, V) とコネクター J1 の 1 番の端子の位置"""
    import json
    with open(PCB_JSON, encoding="utf-8") as f:
        return json.load(f)


def pcb_parts():
    """基板版の部品: 基板、ボリューム (JH16K6)、裏の部品 (コネクター・抵抗・コンデンサー)、裏に出る端子とハンダ、表に出るコネクターの端子。
    基板の上の座標 (X, V) は pcb/make_pcb.py と同じ。基板の上面はパネル上面から TOP + スライダー本体の高さ"""
    info = pcb_info()
    zt = -(TOP + SLIDER_BODY[2])                 # 基板の上面 (パネル座標)
    zb = zt - PCB_T

    def put(m):                                  # 基板の上の座標 (X, V - SLIDER_Y) で作った形をケースへ
        return on_panel(m, 0, SLIDER_Y)

    def bx(x0, v0, x1, v1, z0, z1):
        return Manifold.cube([x1 - x0, v1 - v0, z1 - z0]).translate([x0, v0 - SLIDER_Y, z0])

    board = put(Manifold.extrude(CrossSection([[(x, v - SLIDER_Y) for x, v in info["outline"]]]), PCB_T).translate([0, 0, zb]))
    kv = SLIDER_Y + (KNOB_Y - SLIDER_Y) / math.cos(SLOPE)     # ボリュームの軸の V
    # ボリューム: 配線版と同じ本体・ナット・軸 (パネルに留める) と、端子の付け根の板
    pr, pd = POT_BODY[0] / 2, POT_BODY[1]
    af, nt, wd, wt = NUT
    nut = Manifold.cylinder(wt, wd / 2, wd / 2, SEG) + Manifold.cylinder(nt, af / math.sqrt(3), af / math.sqrt(3), 6).translate([0, 0, wt])
    nut = nut - Manifold.cylinder(nt + wt + 1, 3.5, 3.5, SEG).translate([0, 0, -0.5])
    br, bh, b0, b1 = JH16K6_BLOCK
    pot = Manifold.cylinder(pd, pr, pr, SEG).translate([0, 0, -TOP - pd]) + Manifold.cylinder(SHAFT - TOP, 3.0, 3.0, SEG) + nut
    pot = pot + Manifold.cube([br, 2 * bh, b1 - b0]).translate([0, -bh, -TOP - b1])
    # 端子 (軸と平行に後ろへ伸びる板。基板を貫通する)
    lr, lp, l0, l1, lw, lt = JH16K6_LEAD
    leads = [Manifold.cube([lt, lw, l1 - l0]).translate([lr - lt / 2, dv - lw / 2, -TOP - l1]) for dv in (-lp, 0, lp)]
    under = []                                   # 裏の部品 (抵抗・コンデンサーの本体とコネクター)
    pins = []
    for ref, val, ax, av, bxx, bv in info["parts"]:
        kind, *dim = PCB_THT[val]
        mx, mv = (ax + bxx) / 2, (av + bv) / 2
        along_v = abs(bv - av) > abs(bxx - ax)
        if kind == "res":                        # 寝かせた抵抗 (足の向きに沿った円柱)
            ln, dia = dim
            cylv = Manifold.cylinder(ln, dia / 2, dia / 2, 24).rotate([-90, 0, 0] if along_v else [0, 90, 0])
            cylv = cylv.translate([mx, mv - SLIDER_Y - ln / 2, zb - dia / 2] if along_v else [mx - ln / 2, mv - SLIDER_Y, zb - dia / 2])
            under.append(cylv)
        else:                                    # 立てたコンデンサー (幅は足の並びの向き)
            w, t, h = dim
            hx, hv = (t / 2, w / 2) if along_v else (w / 2, t / 2)
            under.append(bx(mx - hx, mv - hv, mx + hx, mv + hv, zb - h, zb))
        for px, pv in ((ax, av), (bxx, bv)):     # 表に出る足とハンダ
            pins.append(bx(px - 1.0, pv - 1.0, px + 1.0, pv + 1.0, zt, zt + 1.5))
    end, mouth, back, ch = info["conn_body"]     # JST ZH 横向き: 端の端子からの張り出し, 口の側, 反対の側, 高さ
    for ci in info["conns"]:
        c0, cv, span = ci["x"], ci["v"], ci["pitch"] * (ci["n"] - 1)
        if ci["way"] == "front":                 # 端子は X に並び、口は手前。コードは基板の下を手前へ
            under.append(bx(c0 - end, cv - mouth, c0 + span + end, cv + back, zb - ch, zb))
            under.append(bx(c0 - end + 0.5, cv - mouth - 8.0, c0 + span + end - 0.5, cv - mouth, zb - ch + 0.5, zb - 0.5))
            pins.append(bx(c0 - 1.0, cv - 1.0, c0 + span + 1.0, cv + 1.0, zt, zt + 2.0))
        else:                                    # 端子は V に並び、口は右 (Nano)。差したハウジングは口の右 4mm
            under.append(bx(c0 - back, cv - end, c0 + mouth, cv + span + end, zb - ch, zb))
            under.append(bx(c0 + mouth, cv - end + 0.5, c0 + mouth + 4.0, cv + span + end - 0.5, zb - ch + 0.5, zb - 0.5))
            pins.append(bx(c0 - 1.0, cv - 1.0, c0 + 1.0, cv + span + 1.0, zt, zt + 2.0))
    return [("pcb", board), ("pot", on_panel(pot, KNOB_X, KNOB_Y)), ("pcb_parts", put(Manifold.batch_boolean(under, manifold3d.OpType.Add))),
            ("pcb_pins", put(Manifold.batch_boolean(pins, manifold3d.OpType.Add)) + on_panel(Manifold.batch_boolean(leads, manifold3d.OpType.Add), KNOB_X, KNOB_Y))]


def gap_report(part_list):
    """スライダーの下まわり (端子・配線) と Nano のいちばん近いすき間"""
    nano = [m for n, m in part_list if n in ("nano", "nano_wires")]
    nano = nano[0] + nano[1]
    under = [m for n, m in part_list if n in ("slider", "pins", "wires")]
    for k in range(len(SLIDER_X)):
        body, pins, wires = under[3 * k: 3 * k + 3]
        g_body = body.min_gap(nano, 20)
        g_pins = (pins + wires).min_gap(nano, 20)
        if min(g_body, g_pins) < 20:
            print(f"  スライダー{k + 1}: 本体と Nano {g_body:.1f} mm / 端子+配線の余裕と Nano {g_pins:.1f} mm")


def check(shell_m, bottom_m, part_list):
    """部品がケースに食い込んでいないか確認する (レバーとノブは上に出るので除く)"""
    ok = True
    for name, m in part_list:
        if name in ("lever", "cap_sweep"):
            # レバーはパネルの溝の中だけを通ること
            v = (m ^ shell_m).volume()
        elif name == "knob":
            v = (m ^ shell_m).volume()
        else:
            v = (m ^ shell_m).volume() + (m ^ bottom_m).volume()
        if v > 0.01:
            ok = False
            print(f"  食い込み: {name} {v:.2f} mm3")
    # スライダー同士・Nano とのすき間
    names = [n for n, _ in part_list]
    for i, (n1, a) in enumerate(part_list):
        for n2, b in part_list[i + 1:]:
            pair = {n1, n2}
            if pair <= {"cap_sweep", "knob"} and pair != {"knob"}:
                pass  # ツマミどうし・ツマミとノブは比べる
            elif pair & {"lever", "knob", "cap_sweep"}:
                continue
            elif pair <= {"slider", "pins", "wires"} and len(pair) > 1:
                continue  # 同じスライダーの本体と端子は重なってよい
            elif "pcb" in pair and pair & {"pins", "wires", "pcb_pins"}:
                continue  # 端子は基板を貫通する (ボリュームの本体は基板の切り欠きを通るので、基板とは比べる)
            v = (a ^ b).volume()
            if v > 0.01:
                ok = False
                print(f"  部品どうしが重なる: {n1} と {n2} {v:.2f} mm3")
    print("干渉チェック:", "OK" if ok else "NG")
    return ok


# ================================================================ 出力

def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False)


def for_print_shell(m, ref=None):
    """パネル面を下にする (傾きを戻してひっくり返す)。ref を渡すと ref と同じ位置に動かす (目盛りの色付け用)"""
    def turn(x):
        return x.translate([0, 0, -H_FRONT]).rotate([-math.degrees(SLOPE), 0, 0]).rotate([180, 0, 0])
    b = turn(m if ref is None else ref).bounding_box()
    return turn(m).translate([-b[0], -b[1], -b[2]])


CAP_SHAPE = None


def display_parts():
    global CAP_SHAPE
    if CAP_SHAPE is None:
        CAP_SHAPE = cap_shape()
    """表示用の部品 (レバーはストロークの途中の1か所に置く)"""
    out = [(n, m) for n, m in parts() if n not in ("lever", "wires", "cap_sweep", "nano_wires")]
    lt, lw, lh = LEVER
    for k, x in enumerate(SLIDER_X):
        pos = [-20, 10, 25, -5, 0][k % 5]
        lever = Manifold.cube([lt, lw, lh]).translate([SLOT_OFFSET - lt / 2, pos - lw / 2, -TOP])
        cw, cl, ch, cdepth = CAP
        cb, cline = CAP_SHAPE          # 外殻, 芯
        z = -TOP + lh - cdepth
        out.append(("lever", on_panel(lever, x, SLIDER_Y)))
        out.append(("cap", on_panel(cb.translate([0, pos, z]), x, SLIDER_Y)))
        out.append(("capcore", on_panel(cline.translate([0, pos, z]), x, SLIDER_Y)))
    kb, kl = knob_shape()
    out.append(("line", on_panel(kl.rotate([0, 0, knob_turn()]).translate([0, 0, knob_lift()]), KNOB_X, KNOB_Y)))
    out = [(n, m) for n, m in out if n != "nano"] + nano_parts()[2]     # Nano は部品ごとに色を分ける
    return out


def export_viewer(shell_m, bottom_m, part_list, out):
    """色付きの GLB と、それを表示する viewer.html (1ファイルで開ける) を出す"""
    import base64
    fc = {k: FILAMENT[v][1] for k, v in PART_FILAMENT.items()}
    colors = {"shelltop": fc["top_plate"], "marks": fc["marks"], "bottom": fc["bottom_case"], "slider": [190, 190, 184], "pins": [200, 170, 90], "lever": [28, 28, 30],
              "cap": fc["cap_shell"], "capcore": fc["cap_core"], "line": fc["knob_line"], "pot": [180, 180, 180], "knob": fc["knob_body"],
              "nano": [22, 78, 160], "nano_clip": fc["nano_clip"], "nano_pad": [214, 178, 90], "nano_ic": [26, 26, 28], "nano_metal": [205, 207, 212],
              "nano_btn": [235, 235, 230], "nano_led": [250, 250, 245], "pcb": [36, 112, 66], "pcb_parts": [45, 45, 48], "pcb_pins": [200, 170, 90]}
    scene = trimesh.Scene()
    items = [("shelltop", shell_m), ("marks", marks_solid(MARK_DEPTH)), ("bottom", bottom_m)] + part_list
    for k, (name, m) in enumerate(items):
        t = to_trimesh(m)
        mat = trimesh.visual.material.PBRMaterial(baseColorFactor=colors[name] + [255], roughnessFactor=0.75, metallicFactor=0.05)
        t.visual = trimesh.visual.TextureVisuals(material=mat)
        scene.add_geometry(t, node_name=f"{name}_{k}", geom_name=f"{name}_{k}")
    glb = scene.export(file_type="glb")
    with open(os.path.join(out, "preview.glb"), "wb") as f:
        f.write(glb)
    b64 = base64.b64encode(glb).decode()
    import json
    html = VIEWER_HTML.replace("__COLORS__", json.dumps(colors)).replace("__GLB__", b64).replace("__W__", str(W)).replace("__D__", str(D))
    with open(os.path.join(out, "viewer.html"), "w", encoding="utf-8") as f:
        f.write(html)


VIEWER_HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>deej-tab ケース</title>
<style>
  html,body{margin:0;height:100%;background:#eef0f3;font:14px "Segoe UI","Yu Gothic UI",sans-serif;color:#222}
  #ui{position:fixed;top:12px;left:12px;background:#fff;border-radius:10px;padding:10px 14px;box-shadow:0 2px 12px rgba(0,0,0,.12)}
  #ui label{display:block;margin:4px 0;cursor:pointer}
  #ui b{display:block;margin-bottom:6px}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>
</head><body>
<div id="ui"><b>deej-tab ケース</b>
  <label><input type="checkbox" id="shell" checked> 天板</label>
  <label><input type="checkbox" id="ghost"> 天板を半透明</label>
  <label><input type="checkbox" id="bottom" checked> 底ケース</label>
  <label><input type="checkbox" id="parts" checked> 部品</label>
  <label><input type="checkbox" id="capghost"> ツマミを半透明</label>
  <small>ドラッグで回転 / ホイールで拡大</small>
</div>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.NeutralToneMapping;   // 色味を変えずに明るい所だけ抑える
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xeef0f3);
const cam = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 1, 2000);
cam.up.set(0, 0, 1);
cam.position.set(__W__ * 0.5 + 60, -150, 190);
const ctl = new OrbitControls(cam, renderer.domElement);
ctl.target.set(__W__ / 2, __D__ / 2, 10);
ctl.update();
scene.add(new THREE.HemisphereLight(0xffffff, 0x9a9a9a, 1.5));   // 室内の白い光 (色かぶりなし)
const sun = new THREE.DirectionalLight(0xffffff, 2.0); sun.position.set(80, -120, 200); scene.add(sun);
const fill = new THREE.DirectionalLight(0xffffff, 0.4); fill.position.set(-120, 150, 120); scene.add(fill);
const COLORS = __COLORS__;   // フィラメントの色 (sRGB)。GLB の色はリニア扱いで白っぽくなるので、こちらを使う
const bin = Uint8Array.from(atob('__GLB__'), c => c.charCodeAt(0));
const groups = { shell: [], bottom: [], parts: [], caps: [] };
new GLTFLoader().parse(bin.buffer, '', (g) => {
  g.scene.traverse((o) => {
    if (!o.isMesh) return;
    o.geometry = o.geometry.toNonIndexed();   // 角をはっきり見せるため面ごとに法線を付ける
    o.geometry.computeVertexNormals();
    const n = o.name.replace(/_[0-9]+$/, '');   // 末尾の番号を外した名前
    const c = COLORS[n] ? new THREE.Color().setRGB(COLORS[n][0] / 255, COLORS[n][1] / 255, COLORS[n][2] / 255, THREE.SRGBColorSpace) : o.material.color.clone();
    o.material = new THREE.MeshStandardMaterial({ color: c, roughness: .9, metalness: 0, side: THREE.DoubleSide });   // マット PLA
    (['shell', 'shelltop', 'marks'].includes(n) ? groups.shell : n === 'bottom' ? groups.bottom : groups.parts).push(o);
    if (['cap', 'capcore', 'knob', 'line'].includes(n)) groups.caps.push(o);
  });
  scene.add(g.scene);
  if (new URLSearchParams(location.search).has('ghost')) document.getElementById('ghost').click();
  setGhost(groups.caps, document.getElementById('capghost').checked, .35);
  window.ready = true;
});
const bind = (id, fn) => document.getElementById(id).addEventListener('change', (e) => fn(e.target.checked));
bind('shell', (v) => groups.shell.forEach((o) => o.visible = v));
bind('bottom', (v) => groups.bottom.forEach((o) => o.visible = v));
bind('parts', (v) => groups.parts.forEach((o) => o.visible = v));
function setGhost(list, v, a) { list.forEach((o) => { o.material.transparent = v; o.material.opacity = v ? a : 1; o.material.depthWrite = !v; }); }
bind('ghost', (v) => setGhost(groups.shell, v, .22));
bind('capghost', (v) => setGhost(groups.caps, v, .35));
const q = new URLSearchParams(location.search);
if (q.has('target')) { const [x, y, z] = q.get('target').split(',').map(Number); ctl.target.set(x, y, z); ctl.update(); }
if (q.has('view')) { const [x, y, z] = q.get('view').split(',').map(Number); cam.position.set(x, y, z); ctl.update(); }
addEventListener('resize', () => { cam.aspect = innerWidth / innerHeight; cam.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
(function loop() { renderer.render(scene, cam); requestAnimationFrame(loop); })();
</script></body></html>
"""


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "out", "pcb") if PCB else os.path.join(here, "out")
    os.makedirs(out, exist_ok=True)
    print("基板版 (スライダー・ボリュームを基板に載せる)" if PCB else "配線版")

    top, base = split(shell(), bottom())
    p = parts()
    check(top, base, p)
    v = (top ^ base).volume()
    print("天板と底ケースの重なり:", "なし" if v < 0.01 else f"{v:.2f} mm3")
    gap_report(p)

    for old in ("shell.stl", "bottom.stl", "cap.stl"):   # 以前の出力
        if os.path.exists(os.path.join(out, old)):
            os.remove(os.path.join(out, old))
    for old in ("marks.stl", "top_marks.stl", "top_frame.stl", "top_frame_flush.stl", "top_frame_recessed.stl", "top_panel.stl", "top_panel_marks.stl"):
        if os.path.exists(os.path.join(out, old)):   # 以前の出力 (パネル面を下に刷る薄い天板の目盛り / 枠とパネルに分ける版)
            os.remove(os.path.join(out, old))
    # 表を上に。傾いたまま作って傾きを戻すと、底の面に一直線に並んだ点 (面積 0 の三角) が残り STL が水密でなくなったので、
    # 底を水平な面で 0.001 切り直す (2026-10-01)
    top_print = for_print_up(top).trim_by_plane([0, 0, 1], 0.001).translate([0, 0, -0.001])
    to_trimesh(top_print).export(os.path.join(out, "top_plate.stl"))
    to_trimesh(base).export(os.path.join(out, "bottom_case.stl"))
    cs, cc = cap_shape()   # 底面を下にして印刷。2つは同じ位置なので同時に読み込んで1つの部品にし、色を割り当てる
    to_trimesh(cs).export(os.path.join(out, "cap_shell.stl"))
    to_trimesh(cc).export(os.path.join(out, "cap_core.stl"))
    to_trimesh(fit_test()).export(os.path.join(out, "fit_test.stl"))   # はめ合いのテスト (1色)
    kb, kl = knob_print()   # 上面を下に。2つは同じ位置なので同時に読み込んで1つの部品にし、色を割り当てる
    to_trimesh(kb).export(os.path.join(out, "knob_body.stl"))
    to_trimesh(kl).export(os.path.join(out, "knob_line.stl"))
    to_trimesh(knob_fit_test()).export(os.path.join(out, "knob_fit_test.stl"))   # ノブの軸穴のはめ合いテスト (1色)
    for bw in (18.0, 18.2, 18.4):   # 基板の幅を測る前に出していた 3 種類
        if os.path.exists(os.path.join(out, f"nano_clip_{bw:.1f}.stl")):
            os.remove(os.path.join(out, f"nano_clip_{bw:.1f}.stl"))
    to_trimesh(nano_clip_print()).export(os.path.join(out, "nano_clip.stl"))   # Nano の押さえ (天井を下に。1色)
    if os.path.exists(os.path.join(out, "nano_support.stl")):   # 以前の別部品の支え (押さえの板にした)
        os.remove(os.path.join(out, "nano_support.stl"))
    if PCB:
        to_trimesh(pcb_spacers()).export(os.path.join(out, "spacer.stl"))   # 配線版の底ケースを使う時の柱のスペーサー (1色)
    export_viewer(top, base, display_parts(), out)

    ps = for_print_up(top).bounding_box()
    print(f"top_plate.stl   印刷サイズ {ps[3]:.1f} x {ps[4]:.1f} x {ps[5]:.1f} mm")
    bb = base.bounding_box()
    print(f"bottom_case.stl 印刷サイズ {bb[3] - bb[0]:.1f} x {bb[4] - bb[1]:.1f} x {bb[5] - bb[2]:.1f} mm")
    print(f"パネルの傾き {math.degrees(SLOPE):.1f}°")
    names = {"top_plate": "天板", "marks": "目盛り", "bottom_case": "底ケース", "cap_shell": "ツマミ外殻", "cap_core": "ツマミ芯", "nano_clip": "Nano の押さえ", "knob_body": "ノブ", "knob_line": "ノブの指示線"}
    print("フィラメント: " + " / ".join(f"{names[k]}={FILAMENT[v][0]}" for k, v in PART_FILAMENT.items()))
    print(f"天板の厚さ {TOP_T:.1f} mm。四隅のネジ (スナップで足りなければ): M3×{SCREW[0]:g} (なべ / キャップ)、座ぐり Φ{SCREW[1]:g}")
    for x, y, seat, e, rest in screw_report():
        print(f"  ({x:.1f}, {y:.1f}) {'手前' if y < D / 2 else '奥'}: 座ぐりの深さ {seat:.1f}mm / 天板へのかかり {e:.1f}mm / 座面の上の柱 {rest:.1f}mm")
    if PCB:
        r = SCREW[1] / 2 + COL_WALL
        print(f"spacer.stl: 配線版の底ケースを使う時に四隅の柱に載せる輪 4 個 (厚さ {TOP_T_WIRED - TOP_T:.1f}・外径 {2 * r:.1f}・穴 {M3_HOLE:g})。"
              f"四隅を M3 で締めるなら M3×{SCREW[0] + 2:g} (天板へのかかり {SCREW_ENGAGE - (TOP_T_WIRED - TOP_T) + 2:.1f}mm)")
        print(f"スライダーは M2 の皿ネジ 10 本で天板に留める (パネル {TOP:g}mm + スライダーへ約 2mm → M2×4)。ノブの底はパネルから {knob_lift():.1f}mm、軸穴の深さ {KNOB_BORE:.1f}mm")


if __name__ == "__main__":
    main()
