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
| [pcb.md](pcb.md) | 基板版（スライダーを 1 枚の基板に載せる） |
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
- `build.py` は PyInstaller の onedir で `app/deej-tab.exe` と `app/_internal` を作ります（2 つで 1 組。理由は [app.md](app.md#exebuildpy)）。起動中の exe は置き換えられないので、トレイから終了してから実行します

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
.venv-mac/bin/python build_mac.py                 # deej-tab.app と release/deej-tab-x.y.z-mac.dmg を作る
```

ターミナルから動かすと、システムオーディオの録音の許可はターミナルに対して求められます。エディタや Claude Code などターミナル以外から動かすと、許可のダイアログが出ないまま音量を変えたアプリの音が消えることがあります。その時は `build_mac.py` で作った `deej-tab.app` で試します。詳しくは [mac.md](mac.md)。

## ファームウェアを書き込む

使う人は、deej-tab の設定画面（「設定」→「コントローラー」→「ファームウェア」）から書き込みます（[hardware.md](hardware.md#設定画面から書き込む)）。書き込むのは `firmware/deej-6ch-led/deej-6ch-led.hex` で、**`.ino` を直したら `tools/build_firmware.py` で作り直してコミットします**（作り直し忘れはテスト `test_bundled_hex_is_current` が見つける）。

開発中は Arduino IDE（ボード「Arduino Nano」）か arduino-cli で書き込んでもかまいません。互換品は旧ブートローダーが多いので、失敗したらプロセッサを切り替えます。

```bat
cd firmware
arduino-cli upload -p COMx --fqbn arduino:avr:nano:cpu=atmega328old deej-6ch-led
```

deej-tab が起動中だと COM ポートが開けないので、先に終了します。

## ケースを作る

`case` フォルダで `pip install numpy trimesh manifold3d` を入れた venv を作り、`python case.py` を実行します。出力は `case/out/`（STL・部品を入れた状態を回して見られる `viewer.html`・断面図）。寸法はファイル先頭の設定で変えられます。詳しくは [case.md](case.md)。

## インストーラー

Windows 版は Inno Setup 6 のインストーラー（`installer/deej-tab.iss`）でも配ります。zip を展開して置き場所を決める・自動起動を入れる・拡張機能のフォルダを探す、の 3 つで、初めての人がつまずいたためです。zip 版（インストールしない）も残しています。

- Inno Setup 6 は `winget install JRSoftware.InnoSetup`（`%LOCALAPPDATA%\Programs\Inno Setup 6` に入る）。`tools/release.py` が探して、`ISCC.exe /DAppVersion=…` で作ります
- 入れる先は `%LOCALAPPDATA%\Programs\deej-tab`（管理者の許可は要らない）。`config.yaml` とログは今までどおり exe の隣にでき、上書き・アンインストールでは消しません
- 選べること：Windows の起動時に自動で起動（初期設定でオン。トレイのメニューと同じ Run の値）・デスクトップのショートカット
- 拡張機能は `{app}\extension` に置きます（前の版のファイルが残らないよう、入れるたびに消してから置く）。ブラウザの拡張機能のページから読み込むので、置き場所が変わらないことが大事です。完了画面に読み込み方とフォルダの場所を出し、「フォルダを開く」も付けています
- 上書き・アンインストールの前に、入っている exe に `--quit` を送って止め（音量を元に戻す処理を飛ばさないため）、それでも残っていれば（zip 版など）`taskkill` で止めます
- アンインストールでは、トレイから自動起動をオンにした時の Run の値も消します（消した exe を起動しようとしないように）
- 「Windows によって PC が保護されました」は、コード署名をしないかぎり出ます（有料の証明書か、Azure の署名サービスが要る）。README で「詳細情報」→「実行」を案内しています
- 完了画面の説明は長いので、欄の高さを決めて（`FinishedLabel.Height`）、その下にチェック（起動する・フォルダを開く）をずらしています。`AdjustHeight` では伸びきらなかった
- 確かめ方：`/VERYSILENT /DIR=作業用フォルダ` で入れ、上書き・アンインストールまで通す。画面は `/LANG=ja`・`/LANG=en` で開き、ボタン（`TNewButton`）に `BM_CLICK` を送って完了画面まで進めて撮りました

## ブランチとリリース

- `main` が公開している版です。大きな変更はブランチで作り、PR でマージします
- 版の番号は、アプリが `app/deej_tab.py` の `VERSION`、拡張機能が `extension/manifest.json`、ファームウェアが `.ino` の `FW_VERSION` です。ファームウェアの zip はアプリの版の名前で作ります
- リリースの手順
  1. `VERSION` を上げ、テストを通す（ファームウェアを直したら `tools/build_firmware.py` で HEX も作り直す）
  2. `tools/release.py --build` で Windows のインストーラー（`deej-tab-X.Y.Z-setup.exe`）と、Windows 版・拡張機能・ファームウェアの zip を `release/` に作る
  3. Mac で `build_mac.py` を実行して Mac 版の dmg を作る
  4. main にコミットしてタグ `vX.Y.Z` を push し、GitHub の Releases に zip・インストーラー・dmg を載せる（ノートは日本語と英語の両方）
- 拡張機能だけを直した時は、タグを `extension-vX.Y.Z` にし、アプリ・Mac 版・ファームウェアは前の版のファイルをそのまま一緒に載せる（Latest からすべて揃うように）
- ファームウェアだけを直した時も同じで、タグを `firmware-vX.Y.Z` にします。zip は `tools/release.py --firmware` で `FW_VERSION` の名前で作ります。アプリの版の名前の zip（`deej-tab-firmware-1.3.1.zip` など）とぶつからないよう、`FW_VERSION` はそれまでに出したアプリの版より大きい番号にします
- 基板版のガーバーと部品の一覧、ケースの STL の zip（`case.py --pcb` の出力。MakerWorld は配線版だけなので）は、タグ `pcb-vX.Y.Z` のプレリリースに載せる（Latest にしない。アプリ・拡張機能の Latest からすべて揃うように）。実物で確かめたらプレリリースを外す
- ケース・ツマミの STL は Releases ではなく [MakerWorld](https://makerworld.com/models/3382344) で配っています
