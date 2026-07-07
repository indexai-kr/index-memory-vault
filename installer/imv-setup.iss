#define AppVersion "0.2.1"
[Setup]
AppName=INDEX Memory Vault
AppVersion={#AppVersion}
DefaultDirName={localappdata}\INDEX\imv
OutputDir=..\dist
OutputBaseFilename=imv-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "..\dist\imv-server.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\imv.exe"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{%USERPROFILE}\vault"

[Run]
Filename: "{app}\imv.exe"; Parameters: "install-windows --server ""{app}\imv-server.exe"" --vault ""{%USERPROFILE}\vault"" --report ""{app}\install-report.txt"""; Description: "Claude config merge and diagnostics"; Flags: runhidden waituntilterminated

[Code]
var
  ReportPage: TOutputMsgMemoWizardPage;

procedure InitializeWizard;
begin
  ReportPage := CreateOutputMsgMemoPage(wpFinished,
    '설치 및 자가진단 완료',
    'INDEX Memory Vault가 설치되었습니다.',
    '아래 진단 결과를 확인한 뒤 Claude Desktop을 완전히 종료하고 재실행하십시오.', '');
end;

procedure CurPageChanged(CurPageID: Integer);
var
  ReportText: AnsiString;
begin
  if CurPageID = ReportPage.ID then
  begin
    if LoadStringFromFile(ExpandConstant('{app}\install-report.txt'), ReportText) then
      ReportPage.RichEditViewer.Lines.Text := String(ReportText)
    else
      ReportPage.RichEditViewer.Lines.Text := '진단 보고서를 읽지 못했습니다.';
  end;
end;
