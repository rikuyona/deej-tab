# Mac 版 deej-tab 引き継ぎ（Mac の Claude Code 向け）

Windows 上で Mac 版のコードを書いたが、**Mac では一度もビルド・実行していない**。
この Mac でビルドと実機確認をして、動くところまで直すのが仕事。

## 前提・お願い

- ユーザーへの返答・説明はすべて日本語。コード内のコメントも既存に合わせて日本語（です・ます調ではなく、短い説明調）
- **Windows 版の動作は変えないこと**。OS の分岐は `IS_MAC` / `sys.platform` で行う
- 対応 OS は macOS 14.2 以降（Process Tap がこの版から）
- 変更は未コミット（2026-10-02 時点）。コミット・プッシュはユーザーに確認してから

## プロジェクトの概要

deej（Arduino の物理スライダーで PC の音量を操作）互換の PC アプリ + Chrome 拡張（タブごとの音量）。
Nano から USB シリアルで `v0|v1|...|v5`（0〜1023）が届き、`config.yaml` の割り当てに従って音量を変える。
設定画面は `ui.html` を `http://127.0.0.1:8765/` で配信し、`/ui` の WebSocket で操作する。詳しくは README.md。

## Mac 版の設計

| 役割 | Windows | Mac |
|---|---|---|
| 音量 | `WindowsAudio`（pycaw） | `macsys.MacAudio` → 補助プログラム |
| アプリ一覧・アイコン | `appcatalog.AppCatalog` | `macsys.MacCatalog` |
| ショートカット・最前面のアプリ | `winsys.py` | `macsys.py` → 補助プログラム |
| 自動起動 | レジストリ Run | `~/Library/LaunchAgents/io.github.rikuyona.deej-tab.plist` |
| 設定画面 | pywebview（WebView2） | pywebview（WKWebView）。別プロセス `--settings` |
| 常駐 | タスクトレイ（pystray） | メニューバー（pystray darwin） |
| 設定・ログ（.app の時） | exe の隣 | `~/Library/Application Support/deej-tab/` |

- アプリ名は `.app` の名前の小文字（例 `discord.app`、`google chrome.app`）。Windows の `discord.exe` と同じ役割。`EXE_RE` は `.exe` / `.app` の両方を通す
- `deej_tab.sysmod()` が winsys / macsys を返す
- アプリの音量の倍率は音量の 2 乗（`macsys.gain`。Chrome タブと同じ感じ方にするため）
- `ui.html` は配信時に `<html lang="ja" data-platform="mac">` を埋め込む。JS の `MAC_TEXT` / `MAC_PARTS` で Windows 向けの文言を Mac 向けに差し替える（英訳の後に置き換える）。`APP_EXT` で `.exe` / `.app` を切り替える

### 補助プログラム `app/mac/deej-tab-helper.swift`

本体（Python）が子プロセスとして起動。標準入出力で 1 行 1 JSON。

```
本体 → {"id": 1, "cmd": "set_gains", "gains": {"discord.app": 0.25}}
補助 → {"id": 1, "ok": true, ...}   失敗時 {"id": 1, "error": "..."}
補助 → {"event": "hotkey", "name": "pause"}
```

命令：`hello` / `get_volume`・`set_volume`（scope: output/input）/ `processes` / `set_gains` / `set_others`（deej.unmapped）/ `reset` / `meter` / `peaks` / `front` / `apps` / `names` / `icon` / `hotkeys`。
標準入力が閉じたら後片付けして終了する。

- アプリごとの音量：`CATapDescription(stereoMixdownOfProcesses:)` + `muteBehavior = .mutedWhenTapped` の Tap と、出力デバイスを入れた非公開の集約デバイスを作る。IOProc で Tap の入力（**最後の 2ch** を Tap とみなしている）に倍率をかけて出力 ch0/1 に書く。倍率が 1 のアプリは Tap を作らない
- `Engine.reconcile()` がプロセス一覧・既定の出力デバイスの変化（リスナー＋2 秒ごと）に合わせて Tap を作り直す
- 子プロセス（Chrome Helper など）は `responsibility_get_pid_responsible_for_pid`（dlsym）と、実行ファイルのパスの一番外側の `.app` で親アプリにまとめる
- 自分自身と同じアプリ（deej-tab.app）のプロセスは対象外
- 音の大きさ（LED メーター）：音量用の Tap があればそこから、なければ `.unmuted` の測るだけの Tap。master は全体の Tap
- ショートカット：Carbon の `RegisterEventHotKey`（メインスレッド。`NSApplication.run()` で回す）
- 全画面判定：最前面アプリのいちばん手前のウィンドウ（`CGWindowListCopyWindowInfo`、layer 0）が画面の大きさ以上か
- API の書き方は公開実装 [Mikser](https://github.com/erdmncdr/Mikser)（`Sources/Mikser/Core/AppTap.swift`）と [AudioCap](https://github.com/insidegui/AudioCap) を参考にした

## ファイル

- `app/mac/deej-tab-helper.swift` … 補助プログラム（新規）
- `app/macsys.py` … Mac の部品（新規）
- `app/build_mac.py` … 補助プログラム（arm64＋x86_64 を lipo）と deej-tab.app を作り、`release/deej-tab-<版>-mac.zip` にする。Info.plist に `LSUIElement`・`NSAudioCaptureUsageDescription`・`LSMinimumSystemVersion` を入れて自己署名（新規）
- `app/requirements-mac.txt`（新規）
- `app/tests/test_mac.py`・`app/tests/fake_mac_helper.py` … 偽の補助プログラムを使った macsys のテスト（新規）
- `app/deej_tab.py`・`app/ui.html`・`README.md`・`.gitignore` … OS 分岐・文言・説明（変更）

## やってほしいこと（順番に）

1. 準備（`app` フォルダで）
   ```sh
   xcode-select --install   # 入っていなければ
   python3 -m venv .venv-mac
   .venv-mac/bin/pip install -r requirements-mac.txt
   ```
2. テスト：`.venv-mac/bin/python -m unittest discover -s tests -v`（Windows では 112 件成功。winsys / appcatalog を import するテストが Mac で落ちる場合は、Windows 専用のテストを skip するなど対処）
3. 補助プログラムのビルド：`.venv-mac/bin/python build_mac.py --helper-only`
   - **コンパイルエラーが出る可能性が高い**。Windows では一度もコンパイルしていない。怪しいところ：
     - `CATapDescription` の初期化子の引数の型（`[AudioObjectID]` を渡している）、`isPrivate` / `name` / `uuid` / `muteBehavior`
     - 集約デバイスの辞書のキー定数（`kAudioAggregateDevice…Key` を `[String: Any]` のキーに使っている）
     - `kAudioObjectUnknown`・Carbon 定数（`kEventClassKeyboard` など）の型変換
     - `proc_pidpath`、`AudioHardwareServiceGetPropertyData` / `SetPropertyData`
     - `onMain { ... }` の複数行クロージャの型推論
4. 補助プログラム単体で動作確認：
   ```sh
   echo '{"id":1,"cmd":"processes"}' | mac/build/deej-tab-helper
   ```
   `front`・`get_volume`（scope: output）なども試す
5. 本体：`.venv-mac/bin/python deej_tab.py --no-tray`（ログが画面に出る）。ターミナルから動かすと「システムオーディオの録音」の許可はターミナルに対して求められる
   - 確認：コントローラーの接続（`/dev/cu.usbserial-…`。`com_port` が見つからなければ USB ポートを自動で探す）、全体音量・マイク、アプリの音量（Spotify・Discord・Safari など）、deej.current / deej.unmapped / deej.game、LED、ショートカット、設定画面（大きさ合わせ・アプリ選択ダイアログ・アイコン）、メニューバー、自動起動、言語
   - Chrome 拡張（タブの音量）は Mac の Chrome でもそのまま使えるはず
6. アプリにする：`.venv-mac/bin/python build_mac.py` → `build/dist/deej-tab.app` を起動して同じ確認（メニューバーだけに出る・設定画面のときだけ Dock に出る・許可のダイアログが deej-tab の名前で出る）
7. 直したことを README の「Mac 版について」と、この文書に反映する

## 実機で確かめたい不安な点

- 集約デバイスの入力に、出力デバイス自身の入力（USB オーディオインターフェースなど）が Tap より前に並ぶか（今は「最後の 2ch が Tap」としている）
- `mutedWhenTapped` の Tap で、音が二重に出たり途切れたりしないか。倍率を 1 に戻して Tap を消す時のプツッという音
- Chrome の Helper が増えた時（新しいタブ）に Tap の作り直しで音が途切れる時間
- Bluetooth ヘッドホンへの切り替えなど、既定の出力デバイスが変わった時に追従するか
- pystray（darwin）のアイコンの大きさ、メニューの「既定の項目」の扱い
- pywebview の `window.width` / `resize` が枠込みかどうか（`SettingsApi._fit_mac`）
- 自己署名なので、ビルドし直すと録音の許可が外れることがある（その時は システム設定 → プライバシーとセキュリティ → 画面とシステムオーディオの録音 で入れ直す）

## 未対応（仕様として）

- 「システム音」への割り当て（UI からも隠している）
- LED メーターのマイクの音の大きさ（マイクの許可が必要になるため 0 を返す）
- 2 回目に deej-tab.app を開いた時に設定画面を出すこと（メニューバーから開く）
- 100% を超える音量（アプリは 100% まで。Chrome タブ・OBS は Windows と同じく超えられる）
