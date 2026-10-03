# 開発ガイド

deej-tab をソースから動かす・作る・配布するための資料です。使い方は [README](../../README.md) を見てください。

## 資料

| 資料 | 内容 |
|---|---|
| [hardware.md](hardware.md) | 部品・配線・ファームウェア（通信の形式、LED、ID、端の位置の保存） |
| [app.md](app.md) | PC アプリの構成と機能の設計（音量の計算、一時停止、プロファイル、LED、OBS など） |
| [extension.md](extension.md) | Chrome 拡張機能（タブの音量の変え方、割り当て、通信） |
| [mac.md](mac.md) | Mac 版（補助プログラム、Process Tap、Windows 版との違い） |
| [case.md](case.md) | 3D プリントのケースとツマミ（`case/case.py`） |
| [pcb.md](pcb.md) | 基板版（スライダーを 1 枚の基板に載せる。ブランチ `pcb-board`） |
| [wording.md](wording.md) | 画面に出す文言のルール |
| [status.md](status.md) | 版の履歴・未確認のこと |

## 全体の仕組み

```
Nano ──USB シリアル──▶ deej-tab ──OS の音量 API──▶ アプリの音量
 (値 0〜1023 × 6)        │  ▲   (Mac: 補助プログラム → Core Audio の Process Tap)
 ◀── LED の指示 ─────────┘  │
                            ├─ 設定画面   http://127.0.0.1:8765/  (WebSocket /ui)
                            ├─ 拡張機能   ws://127.0.0.1:8765/    → タブの音量
                            └─ OBS        obs-websocket v5        → 音声ソースの音量
```

通信は PC の中（127.0.0.1）だけで、外部には何も送りません。

## フォルダ

| フォルダ | 中身 |
|---|---|
| `app/` | PC アプリ。`deej_tab.py` が本体、`ui.html` が設定画面、`leds.py` が LED の計算、`winsys.py` / `macsys.py` が OS ごとの部品、`appcatalog.py` がアプリ一覧、`obs.py` が OBS 連携 |
| `app/mac/` | Mac の補助プログラム（Swift） |
| `app/tests/` | テスト（unittest） |
| `extension/` | Chrome 拡張機能（Manifest V3） |
| `firmware/deej-6ch-led/` | Arduino Nano 用ファームウェア |
| `case/` | ケース・ツマミの 3D データを作るスクリプト |
| `pcb/` | 基板版の KiCad データと、それを作るスクリプト（[pcb/README.md](../../pcb/README.md)） |
| `tools/` | 仮スライダー・拡張機能のアイコン作り・配布用 zip 作り |
| `docs/` | README の画像と、この開発資料 |

## Windows で動かす

Python 3 が必要です（3.14 で確認）。

```bat
cd app
start.bat
```

初回は `.venv` を作って必要なパッケージを入れ、トレイに常駐します。ログを画面で見たいときは `debug.bat`（`--no-tray`）。ログは `app/deej-tab.log` にも出ます。

| 作業 | コマンド（`app` フォルダで） |
|---|---|
| テスト | `.venv\Scripts\python -m unittest discover -s tests -v` |
| exe を作る | `.venv\Scripts\python build.py` |
| 配布用 zip を作る | `.venv\Scripts\python ..\tools\release.py --build`（`release/` にできる） |

- `.venv` は別の場所からコピーすると壊れます（pip が入っていない）。壊れたら `py -3 -m venv --clear .venv` で作り直します
- `build.py` は PyInstaller の onefile で `app/deej-tab.exe` を作ります。起動中の exe は置き換えられないので、トレイから終了してから実行します

### コントローラーなしで試す

`tools/fake_sliders.bat` で 6 本の仮スライダーが開き、`socket://127.0.0.1:9000` に値を送ります。`config.yaml` の `com_port` をこの値にすると、deej-tab が仮スライダーにつながります。

- 全部 0 / 50 / 100%、揺れを混ぜる（揺れの抑えを試す）、送るのを止める（つなぎ直しを試す）、`--port` で別のポート
- クリックしても最前面のアプリが変わらない（「今使っているアプリ」を試せる）
- deej-tab から届いた LED の指示を `leds.LedSim` で再現して表示します

## Mac で動かす

Xcode のコマンドラインツール（`xcode-select --install`）と Python 3 が必要です（Homebrew の Python 3.12 で確認）。

```sh
cd app
python3 -m venv .venv-mac
.venv-mac/bin/pip install -r requirements-mac.txt
.venv-mac/bin/python build_mac.py --helper-only   # 補助プログラム (mac/deej-tab-helper.swift) を作る
.venv-mac/bin/python deej_tab.py --no-tray        # ログを見ながら動かす
.venv-mac/bin/python build_mac.py                 # deej-tab.app と release/deej-tab-x.y.z-mac.zip を作る
```

ターミナルから動かすと、システムオーディオの録音の許可はターミナルに対して求められます。エディタや Claude Code などターミナル以外から動かすと、許可のダイアログが出ないまま音量を変えたアプリの音が消えることがあります。その時は `build_mac.py` で作った `deej-tab.app` で試します。詳しくは [mac.md](mac.md)。

## ファームウェアを書き込む

Arduino IDE（ボード「Arduino Nano」）か arduino-cli で書き込みます。互換品は旧ブートローダーが多いので、失敗したらプロセッサを切り替えます。

```bat
cd firmware
arduino-cli upload -p COMx --fqbn arduino:avr:nano:cpu=atmega328old deej-6ch-led
```

deej-tab が起動中だと COM ポートが開けないので、先に終了します。

## ケースを作る

`case` フォルダで `pip install numpy trimesh manifold3d` を入れた venv を作り、`python case.py` を実行します。出力は `case/out/`（STL・部品を入れた状態を回して見られる `viewer.html`・断面図）。寸法はファイル先頭の設定で変えられます。詳しくは [case.md](case.md)。

## ブランチとリリース

- `main` が公開している版です。大きな変更はブランチで作り、PR でマージします
- 版の番号は、アプリが `app/deej_tab.py` の `VERSION`、拡張機能が `extension/manifest.json`、ファームウェアが `.ino` の `FW_VERSION` です。ファームウェアの zip はアプリの版の名前で作ります
- リリースの手順
  1. `VERSION` を上げ、テストを通す
  2. `tools/release.py --build` で Windows 版・拡張機能・ファームウェアの zip を `release/` に作る
  3. Mac で `build_mac.py` を実行して Mac 版の zip を作る
  4. main にコミットしてタグ `vX.Y.Z` を push し、GitHub の Releases に zip を載せる（ノートは日本語と英語の両方）
- ケース・ツマミの STL は Releases ではなく [MakerWorld](https://makerworld.com/models/3382344) で配っています
