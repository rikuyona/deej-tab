; deej-tab の Windows 用インストーラー (Inno Setup 6)
;
; tools\release.py --build が作る (直接作る時は ISCC.exe /DAppVersion=1.3.3 installer\deej-tab.iss)。
; 管理者の許可なしで %LOCALAPPDATA%\Programs\deej-tab に入れる。config.yaml とログは exe の隣にでき、
; 上書き・アンインストールでは消さない (入れ直しても設定が残る)。
; ブラウザの拡張機能は {app}\extension に置く (ブラウザの拡張機能のページから読み込んでもらう。更新もこのフォルダ)

#ifndef AppVersion
  #error AppVersion を /DAppVersion=x.y.z で渡す
#endif

[Setup]
AppId={{2629F786-FC1B-4CC1-AC8F-9C03553B033F}
AppName=deej-tab
AppVersion={#AppVersion}
AppVerName=deej-tab {#AppVersion}
AppPublisher=rikuyona
AppPublisherURL=https://github.com/rikuyona/deej-tab
AppSupportURL=https://github.com/rikuyona/deej-tab
DefaultDirName={localappdata}\Programs\deej-tab
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=deej-tab-{#AppVersion}-setup
SetupIconFile=..\app\build\deej-tab.ico
UninstallDisplayIcon={app}\deej-tab.exe
UninstallDisplayName=deej-tab
VersionInfoVersion={#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 動いている deej-tab は [Code] で --quit を送って止める (音量を元に戻す処理を飛ばさないため)
CloseApplications=no

[Languages]
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ja.AutoStart=Windows の起動時に自動で起動する
en.AutoStart=Start with Windows
ja.DesktopIcon=デスクトップにショートカットを作る
en.DesktopIcon=Create a desktop shortcut
ja.OpenExtension=ブラウザの拡張機能のフォルダを開く
en.OpenExtension=Open the browser extension folder
ja.Finished=deej-tab をインストールしました。タスクトレイ（画面右下）のアイコンから設定を開けます。コントローラーは USB につなぐと自動で見つけます。%n%nタブごとに音量を変える時は、ブラウザ（Chrome・Edge・Brave など）に拡張機能を入れます。ブラウザで chrome://extensions（Edge は edge://extensions）を開き、「デベロッパー モード」をオンにして、「パッケージ化されていない拡張機能を読み込む」で次のフォルダを選んでください。%n%n%1
en.Finished=deej-tab is installed. Open the settings from its icon in the system tray (bottom right). The controller is found automatically when you plug it into USB.%n%nTo control the volume of each tab, add the extension to your browser (Chrome, Edge, Brave, etc.): open chrome://extensions (edge://extensions in Edge), turn on "Developer mode", click "Load unpacked" and choose this folder:%n%n%1

[Tasks]
Name: "autostart"; Description: "{cm:AutoStart}"
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[InstallDelete]
; 拡張機能と exe の部品 (_internal) は、前の版で消したファイルが残らないよう入れ直す
Type: filesandordirs; Name: "{app}\extension"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\app\deej-tab.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\deej-tab"; Filename: "{app}\deej-tab.exe"
Name: "{autodesktop}\deej-tab"; Filename: "{app}\deej-tab.exe"; Tasks: desktopicon

[Registry]
; アプリのトレイの「Windows の起動時に自動で起動」と同じ値 (autostart_command)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "deej-tab"; \
  ValueData: """{app}\deej-tab.exe"" --tray"; Tasks: autostart

[Run]
; アプリの「更新する」から /SILENT /deejrestart=1 で起動された時は、入れ終わったら起動し直す
; (下の「起動する」のチェックは /SILENT では動かない)。設定画面を開いて、更新の結果を知らせる
Filename: "{app}\deej-tab.exe"; Flags: nowait; Check: RestartAfterUpdate
Filename: "{app}\deej-tab.exe"; Description: "{cm:LaunchProgram,deej-tab}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\extension"; Description: "{cm:OpenExtension}"; Flags: shellexec nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

// 動いている deej-tab を止める。入っている exe に --quit を送り (トレイの「終了」と同じ)、
// それで止まらない時 (zip 版など --quit を知らない古い版) は強制終了する
procedure StopDeejTab();
var
  Code: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\deej-tab.exe');
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM deej-tab.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function RestartAfterUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:deejrestart|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopDeejTab();
  Result := '';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('Finished'), [ExpandConstant('{app}\extension')]);
    WizardForm.FinishedLabel.Height := ScaleY(190);
    // 説明が長いので、その下の「起動する」などのチェックを説明の下へずらす
    WizardForm.RunList.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopDeejTab();
    // トレイから自動起動をオンにした時の値も消す (消した exe を起動しようとしないように)
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, 'deej-tab');
  end;
end;
