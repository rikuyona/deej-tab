# deej-tab — Claude Code 向けの約束ごと

物理スライダー（Arduino Nano）で、アプリ・Chrome のタブ・OBS の音量を操作する自作ミキサー。
PC アプリ（Python）・Chrome 拡張機能・ファームウェア・3D プリントのケース・基板をこのリポジトリにまとめている。

設計と経緯は [docs/dev/](../docs/dev/README.md) にある。作業の前に、触る部分の資料を読むこと。

## 進め方

- ユーザーへの返答・説明はすべて日本語。コードのコメントも日本語で、です・ます調ではなく短い説明調（既存に合わせる）
- コミット・push・リリースはユーザーに確かめてから。ブランチの運用は [docs/dev/README.md](../docs/dev/README.md#ブランチとリリース)
- 決めたこと・わかったことは、該当する `docs/dev/*.md` に書き足す（日付入りの作業ログではなく、今の設計として書く。理由は残す）
- 画面に出す文言は [docs/dev/wording.md](../docs/dev/wording.md) のルールに従う。設定画面に文言を足したら `ui.html` の `I18N_EN` に英語も足す

## してはいけないこと

- **確認のために deej-tab を別に動かす時は、音量操作を `DummyAudio` にする**。`WindowsAudio` のままだと本物の音量が変わる
- Mac 対応の変更で Windows 版の動作を変えない（分岐は `IS_MAC` / `sys.platform`）
- ファームウェアの EEPROM は並び順で保存している。数値を足す時は `paramTable` の最後に足す
- COM ポートは 1 つのプロセスしか開けない。ファームウェアを書き込む前に deej-tab を終了する

## よく使うコマンド（Windows、`app` フォルダで）

| 作業 | コマンド |
|---|---|
| テスト | `.venv\Scripts\python -m unittest discover -s tests` |
| exe を作る | `.venv\Scripts\python build.py`（起動中の exe は置き換えられない） |
| 配布用 zip | `.venv\Scripts\python ..\tools\release.py --build` |
| ファームウェアの書き込み | `arduino-cli upload -p COMx --fqbn arduino:avr:nano:cpu=atmega328old deej-6ch-led`（`firmware` フォルダで） |

## 開発機のくせ（Windows）

- Node は入っていない（JS の構文チェックはできない）
- WMI が固まることがあり、固まると pip・`Get-CimInstance`・`tasklist` まで止まる。`python -c "import platform; platform._wmi_query('OS','Version')"` がすぐ返るかで確かめる（PC の再起動で直る）
- COM3 / COM4 は Bluetooth の仮想ポートで、コントローラーではない
- 道具の置き場所は `%LOCALAPPDATA%\deej-tab\`（`arduino-cli\`、基板用の `tools\`）
