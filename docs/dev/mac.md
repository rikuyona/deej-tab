# Mac 版

macOS 14.2 以降・Apple シリコンの Mac で動きます（アプリごとの音量に使う Core Audio の Process Tap が 14.2 から）。本体は Windows と同じ `deej_tab.py` で、OS ごとの部分だけを差し替えています。

- OS の分岐は `IS_MAC` / `sys.platform`。**Mac の変更で Windows 版の動作を変えない**
- `deej_tab.sysmod()` が `winsys` / `macsys` を返します

## Windows 版との対応

| 役割 | Windows | Mac |
|---|---|---|
| 音量 | `WindowsAudio`（pycaw） | `macsys.MacAudio` → 補助プログラム |
| アプリ一覧・アイコン | `appcatalog.AppCatalog` | `macsys.MacCatalog` |
| ショートカット・最前面のアプリ | `winsys.py` | `macsys.py` → 補助プログラム |
| 自動起動 | レジストリの Run | `~/Library/LaunchAgents/io.github.rikuyona.deej-tab.plist` |
| 設定画面 | pywebview（WebView2） | pywebview（WKWebView）。別プロセス `--settings` |
| 常駐 | タスクトレイ（pystray） | メニューバー（pystray darwin） |
| 設定・ログ（配布版） | exe の隣 | `~/Library/Application Support/deej-tab/` |

- アプリ名は `.app` の名前の小文字です（例 `discord.app`、`google chrome.app`）。Windows の `discord.exe` と同じ役割で、`EXE_RE` は両方を通します
- アプリの音量の倍率は音量の 2 乗です（`macsys.gain`。Chrome のタブと同じ感じ方にするため）
- `ui.html` は配信時に `<html lang="ja" data-platform="mac">` を埋め込みます。JS の `MAC_TEXT` / `MAC_PARTS` で Windows 向けの文言を Mac 向けに差し替え（英訳の後に置き換える）、`APP_EXT` で `.exe` / `.app` を切り替えます
- 初期設定は `discord.app` / `/dev/cu.usbserial`（なければ USB のポートを探す）
- ショートカットの表示は `⌃⌥⇧P` のように記号で出します

## 補助プログラム（`app/mac/deej-tab-helper.swift`）

本体（Python）が子プロセスとして起動し、標準入出力で 1 行 1 JSON をやりとりします。標準入力が閉じたら後片付けして終わります。

```
本体 → {"id": 1, "cmd": "set_gains", "gains": {"discord.app": 0.25}}
補助 → {"id": 1, "ok": true, ...}        失敗時 {"id": 1, "error": "..."}
補助 → {"event": "hotkey", "name": "pause"}
```

命令：`hello` / `get_volume`・`set_volume`（scope: output・input）/ `processes` / `set_gains` / `set_others`（deej.unmapped）/ `reset` / `meter` / `peaks` / `front` / `apps` / `app_of`（pid のアプリ）/ `names` / `icon` / `hotkeys`

### アプリごとの音量

- `CATapDescription(stereoMixdownOfProcesses:)` ＋ `muteBehavior = .mutedWhenTapped` の Tap と、出力デバイスを入れた非公開の集約デバイスを作ります。IOProc で Tap の入力（**最後の 2ch** を Tap とみなす）に倍率をかけ、出力の ch0/1 に書きます
- 倍率が 1 のアプリは Tap を作りません（何もしない）
- `Engine.reconcile()` が、プロセスの一覧と既定の出力デバイスの変化（リスナー＋2 秒ごと）に合わせて Tap を作り直します。倍率が 1 でないアプリも「そのほか」も測るものもなく、Tap もなければ、プロセスの一覧は取りません（`needsTaps`）
- 子プロセス（Chrome Helper など）は `responsibility_get_pid_responsible_for_pid`（dlsym）と、実行ファイルのパスの一番外側の `.app` で親アプリにまとめます。Chrome は更新後に `…/code_sign_clone/…/Google Chrome.app.bundle` から動くので、`NSRunningApplication` の bundleURL を先に使います
- 自分と同じアプリ（deej-tab.app）のプロセスは対象外です
- 音を出していない常駐アプリは「再生中」に出しません（今出力しているものだけ）

### 軽くするための決まり

Core Audio への問い合わせは 1 つずつ coreaudiod とのやりとりになり、補助プログラムと coreaudiod の両方の CPU を使います。1.4.0 では、常駐中の補助プログラムの CPU の大半がプロセスの一覧（`audioProcesses`）でした。

- プロセスのオブジェクト → pid は変わらないので覚えておきます（`objectPids`。一覧からなくなったら忘れる）
- 「今出力しているか」（`kAudioProcessPropertyIsRunningOutput`）はプロセスごとの問い合わせになり重いので、要る時だけ聞きます。`processes` 命令の `output: false` で聞かず、Tap の作り直しも聞きません。本体の `MacAudio.processes()`（あるかどうかの判定。LED の状態表示で 1 秒ごと）は `output: false`、設定画面の一覧（`MacCatalog`）だけが聞きます。聞くのをやめると coreaudiod の CPU が 2〜3% 下がりました
- 最前面のアプリ（`front`、本体が 0.5 秒ごとに聞く）は、全画面かを調べるのにウィンドウの一覧を取るので、最前面のアプリが同じ間は 2 秒ごとにだけ調べ直します（`FULL_RECHECK`）。`full: false` なら調べません（`deej.current` の名前だけ要る時）
- 測った値（1.4.0 → 1.5.0、偽の命令を送って補助プログラムだけを測った値）：何もしていない時 0.17% → 0%、`processes` を 1 秒ごと 0.36% → 0.07%、`front` を 0.5 秒ごと 0.47% → 0.23%（1 コアに対して）

### 固まった時

補助プログラムが落ちれば起動し直しますが（`RESTART_WAIT`）、落ちずに返事をしなくなると、命令のたびに 2 秒待たされ続けてスライダーが効かなくなります。`HANG_LIMIT`（3）回続けて返事がなければ止めて、次の命令で起動し直し、覚えている音量を送り直します。遅れてでも何か届けば数え直します（固まってはいない）。

### 録音の許可を待つ間

Tap の `AudioDeviceStart` は、録音の許可の返事を待つ間止まります。ほかの命令まで止まらないよう、`set_gains` / `set_others` / `reset` / `meter` はすぐ返事をして、Tap の作り直しは後で行います。`peaks` / `processes` は作り直しを待ちません（`tapsLock`）。作り直しに 0.5 秒以上かかったらログに出します。時間はスリープしている間を数えない時計（`systemUptime`）で測ります。`Date` で測っていた時は、作り直しの途中で眠った時間まで入り、「976 秒かかりました」という誤った記録が残っていました（スリープの記録と一致）。

### そのほか

- 音の大きさ（LED のメーター）：音量用の Tap があればそこから、なければ `.unmuted` の測るだけの Tap。全体は全体の Tap
- ショートカット：Carbon の `RegisterEventHotKey`（メインスレッド。`NSApplication.run()` で回す）
- 全画面の判定：最前面のアプリのいちばん手前のウィンドウ（`CGWindowListCopyWindowInfo`、layer 0）が画面の大きさ以上か
- API の書き方は [Mikser](https://github.com/erdmncdr/Mikser)（`AppTap.swift`）と [AudioCap](https://github.com/insidegui/AudioCap) を参考にしました

## アプリにする（`app/build_mac.py`）

- 補助プログラムを arm64 と x86_64 で作って lipo でまとめ、`deej-tab.app` と `release/deej-tab-<版>-mac.dmg` を作ります（`--helper-only` で補助プログラムだけ）
- 配布は dmg です（1.3.x は zip）。開くと `deej-tab.app` と「Applications」へのショートカットが並び、ドラッグで入れられます。zip だと展開した場所（ダウンロード）から動かしがちで、ログイン時の自動起動もその場所を指してしまうためです
  - `hdiutil create`（UDZO・HFS+）だけで作ります。ウィンドウの背景やアイコンの位置は付けていません（Finder を AppleScript で動かす必要があり、作る環境によって失敗しやすいため）
  - `.app` は `ditto` で写します（署名を壊さないため）
- Apple の公証（notarization）は受けていません（Apple Developer Program が要る）。macOS 15 からは右クリックの「開く」で警告を飛ばせなくなったので、README ではシステム設定の「このまま開く」を案内しています
- Pillow のうち使わない部品は入れません（Windows の `build.EXCLUDE` と同じ。.app は 37MB → 29MB、dmg は 18.7MB → 15.4MB）。外しても描けることは `test_robust.py` で確かめます。新しい版の確認に使う certifi の証明書は PyInstaller が `Resources/certifi` に入れます
- Info.plist に `LSUIElement`（メニューバーだけに出る）・`NSAudioCaptureUsageDescription`・`LSMinimumSystemVersion` を入れます
- 自己署名の条件を `identifier "io.github.rikuyona.deej-tab"` にしています。自己署名は中身のハッシュで同じアプリかを見るため、そのままだと作り直すたびに録音の許可が外れました

## 気をつけること

- **メニューバーは別スレッドから触ると落ちます**（`NSStatusItem setMenu:` で SIGTRAP）。`macsys.on_main`（`AppHelper.callAfter`）でメインスレッドから更新します
- ターミナル以外（エディタ・Claude Code など）の子プロセスとして動かすと、許可のダイアログが出ないまま Tap の音が 0 で届き、アプリの音が消えます。試す時は `.app` で
- シリアルポートは Windows と違って 1 つのプロセスが独占しません。deej-tab を 2 つ動かすと（確認用など）、コントローラーを探す時にもう一方が使っているポートも開き、Nano がリセットされます
- 補助プログラムは自分と同じアプリの音を除くので、Claude Code から起動した `afplay` などは一覧に出ません（仕様どおり）

## 確認済み（実機）

テスト・Swift のコンパイル（警告のみ）・lipo、全体の音量、アプリごとの音量（Chrome の音が 100% / 0% で鳴る・消える）、`deej.current`・`deej.unmapped`（unmapped を下げても coreaudiod の CPU は +3% 程度）、設定画面とアプリ選択・アイコン、本物の Nano（`/dev/cu.usbserial-110` を自動で見つけた）・LED、Chrome 拡張機能（そのまま動く。Option+Shift+1 で割り当て）、メニューバー・一時停止・ログイン時の自動起動・ショートカット。

1.4.0：`build_mac.py`（ファームウェアの HEX と `.ino` が `.app` に入る）、設定画面からのファームウェアの書き込み（`/dev/cu.usbserial-20220`、57600bps・約 10 秒。書いたあと同じ ID でつなぎ直す）、「コントローラーが見つかりません」の表示（ドライバの行は出さない）と同梱のファームウェアの版、メニューバーありで `--quit`（1 秒で止まる）、拡張機能をつないできたブラウザの判定（Chrome で `google chrome.app`）。

1.5.0：テスト（Mac で全部）、`build_mac.py`、certifi の証明書で GitHub の一覧を読めること（証明書の置き場所を消した Python で）。シリアルのまとめ読みは Mac でも効きます（pyserial の `readline` は Mac でも 1 バイトずつ読む）。偽の Nano（pty から毎秒 40 行、DummyAudio、トレイなし）で本体全体が 2.27% → 1.40%、補助プログラムが 0.40% → 0.23%（最前面の確認だけ）。本物の音量を変える確認（Tap あり）と .app の起動は、ユーザーの環境で確かめる。

集約デバイスの入力は Tap の 2ch だけでした（確認した Mac の出力デバイスの場合）。

## 未確認

- 集約デバイスの入力で、出力デバイス自身の入力（USB オーディオインターフェースなど）が Tap より前に並ぶか（今は「最後の 2ch が Tap」としている）
- `mutedWhenTapped` の Tap で音が二重に出たり途切れたりしないか。倍率を 1 に戻して Tap を消す時のプツッという音
- Chrome の Helper が増えた時（新しいタブ）に、Tap の作り直しで音が途切れる時間
- Bluetooth ヘッドホンへの切り替えなど、既定の出力デバイスが変わった時に追従するか
- pystray（darwin）のアイコンの大きさ、メニューの「既定の項目」の扱い
- pywebview の `window.width` / `resize` が枠込みかどうか（`SettingsApi._fit_mac`）

## 対応していないこと（仕様）

- 「システム音」への割り当て（Mac に通知音だけの音量がないため。画面からも隠している）
- LED のメーターのマイクの音の大きさ（マイクの許可が要るため 0 を返す）
- 2 回目に deej-tab.app を開いた時に設定画面を出すこと（メニューバーから開く）
- アプリの音量の 100% 超え（Chrome のタブ・OBS は Windows と同じく超えられる）
- 一時停止・終了で、アプリの音量は横取りをやめるので 100% に戻ります（覚えた値には戻さない）
