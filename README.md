# deej-tab

物理スライダーで、Windows のアプリごとの音量と **Chrome のタブごとの音量** を操作する自作ミキサーです。
[deej](https://github.com/omriharel/deej) を元に、PC アプリ・Chrome 拡張機能・LED 付きファームウェア・3D プリント用ケースまでまとめてあります。

<!-- 実物の写真をここに: ![deej-tab](docs/photo.jpg) -->

## できること

- スライダー 5 本＋ノブ 1 つに、全体音量・マイク・アプリ（Discord・Spotify など）・**Chrome のタブ**・今使っているアプリ・ゲーム・OBS の音声ソースを割り当てる
- Chrome で開いた配信や動画を、タブごとに別のスライダーで調整（Alt+Shift+A でタブを割り当て）
- 設定画面で割り当て・スライダーごとの最大音量（100% 超えも可。タブ・OBS のみ）・音量の変わり方を変更
- プロファイル（割り当て一式）の切り替え。アプリが前に来たら自動で切り替えも可
- 一時停止（ショートカットも可）。止めると音量を元に戻す
- スライダーの LED で状態や音の大きさを表示。光り方は設定画面で選べる
- config.yaml は deej と互換

## 必要なもの

### PC

- Windows 10 / 11
- Google Chrome 116 以降（タブの音量を操作する場合）

### 部品

| 部品 | 例 | 数 |
|---|---|---|
| マイコン | Arduino Nano（互換品可。CH340 / USB-C） | 1 |
| スライダー | Bourns PTL60-15R0-103B2（60mm・10kΩ B カーブ・赤 LED 付き） | 5 |
| 回転ボリューム | 10kΩ B カーブ・6mm 軸（SH16K4B103L20KCCI など） | 1 |
| ノブ | 3D プリント（MakerWorld のデータ）または市販の 6mm 軸用（ABS-28 など） | 1 |
| LED 用抵抗 | 330Ω | 5 |

LED なしのスライダーでも動きます（LED の機能が使えないだけです）。

### 配線

| 部品 | 端子 | つなぐ先 |
|---|---|---|
| スライダー 1〜5 | 1 | GND |
| | 3 | 5V |
| | 2（ワイパー） | A0〜A4 |
| | B（LED＋） | 330Ω → D3 / D5 / D6 / D9 / D10 |
| | E（LED−） | GND |
| | L | つながない |
| 回転ボリューム | 両端 | 5V / GND |
| | 中央 | A5 |

D0 / D1 は USB シリアルに使うので空けておきます。

## セットアップ

[Releases](../../releases) から最新版の zip をダウンロードします。

### 1. ファームウェアを書き込む

1. `deej-tab-firmware-x.y.z.zip` を展開する
2. [Arduino IDE](https://www.arduino.cc/en/software) で `firmware/deej-6ch-led/deej-6ch-led.ino` を開く
3. ボード「Arduino Nano」、ポートを選んで書き込む
   - 互換品で失敗する場合は、プロセッサを「ATmega328P (Old Bootloader)」にする
   - ポートが出ない場合は CH340 のドライバを入れる
   - deej-tab が起動中だと書き込めません（トレイから終了してから）

### 2. PC アプリを入れる

1. `deej-tab-x.y.z-windows.zip` を展開し、好きな場所に置く（設定ファイルとログは exe の隣に作られます）
2. `deej-tab.exe` を起動する
   - 署名していないため「Windows によって PC が保護されました」と出ることがあります。「詳細情報」→「実行」で起動できます
3. タスクトレイのアイコンをクリックすると設定画面が開きます。「設定」→「コントローラー」でマイコンの COM ポートを選びます

### 3. Chrome 拡張機能を入れる

1. `deej-tab-extension-x.y.z.zip` を展開する（展開したフォルダは消さずに残しておく）
2. Chrome で `chrome://extensions` を開き、右上の「デベロッパー モード」をオンにする
3. 「パッケージ化されていない拡張機能を読み込む」で、展開した `deej-tab-extension` フォルダを選ぶ

更新するときは、新しい版でフォルダの中身を置き換えて、`chrome://extensions` の「更新」ボタン（↻）を押します。

### 4. ケースを印刷する（任意）

ケース・ツマミの 3D データは MakerWorld で配布しています。印刷設定と組み立て方もそちらにあります。

**MakerWorld：**（準備中）

寸法を変えたいときは `case/case.py` の先頭の設定を書き換えて作り直せます（下の「ソースから動かす」）。

## 使い方

- **タブの割り当て**：音量を変えたいタブで Alt+Shift+A を押すと、空いている番号（タブ 1〜6）に割り当てます。押すたびに次の番号へ移り、最後は解除。拡張機能のアイコンをクリック（Alt+Shift+D）すると番号を選べます
- 設定画面の割り当てで「Chrome タブ 1」などをスライダーに置くと、そのタブの音量をそのスライダーで操作します
- タブの音量は「Chrome のアプリ音量 × 全体音量」に対する割合です

## ソースから動かす

Python 3 が必要です（3.14 で動作確認）。

```bat
cd app
start.bat
```

初回は `.venv` を作って必要なパッケージを入れ、トレイに常駐します。ログを画面で見たいときは `debug.bat`。

| 作業 | コマンド（`app` フォルダで） |
|---|---|
| テスト | `.venv\Scripts\python -m unittest discover -s tests -v` |
| exe を作る | `.venv\Scripts\python build.py` |
| 配布用 zip を作る | `.venv\Scripts\python ..\tools\release.py --build`（`release/` にできる） |

- ケース：`case` フォルダで `pip install numpy trimesh manifold3d` を入れた venv を作り、`python case.py`。出力は `case/out/`
- 仮スライダー：`tools/fake_sliders.bat` でコントローラーなしに試せます（config.yaml の `com_port` を `socket://127.0.0.1:9000` に）

## 仕組み

```
Nano ──USB シリアル──▶ deej-tab.exe ──Windows の音量 API──▶ アプリの音量
 (スライダーの値)          │  ▲
                          │  └─ 設定画面 (http://127.0.0.1:8765/)
                          └─WebSocket──▶ Chrome 拡張機能 ──▶ タブの音量
```

拡張機能はタブの音声をキャプチャしてゲインをかけます。通信は PC の中（127.0.0.1）だけで、外部には何も送りません。

## ライセンス

MIT License（[LICENSE](LICENSE)）。ファームウェアと config.yaml の形式は [deej](https://github.com/omriharel/deej)（MIT License, © Omri Harel）を元にしています。
