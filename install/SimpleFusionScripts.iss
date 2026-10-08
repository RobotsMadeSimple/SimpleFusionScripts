; Windows installer for the SimpleFusionScripts add-ins (Inno Setup 6).
;
; Built by GitHub Actions for every release (see .github/workflows/release.yml):
;   iscc /DAppVersion=1.2 install\SimpleFusionScripts.iss      -> dist\SimpleFusionScripts-Setup-1.2.exe
;
; Installs for the current user only (no administrator rights needed) into Fusion's add-ins
; folder, one folder per add-in picked on the components page. Fusion loads them on its next
; start. Running a newer installer updates them; Windows' "Installed apps" uninstalls them.
; Each user's own BuildBook data (logs, thumbnails, settings in %APPDATA%\RobotsMadeSimple) is
; left alone.

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{6D1B3E44-2C7A-4F8B-9E31-5A0C7F2D9B61}
AppName=Robots Made Simple Fusion Add-ins
AppVersion={#AppVersion}
AppPublisher=Robots Made Simple
AppPublisherURL=https://github.com/RobotsMadeSimple/SimpleFusionScripts
AppSupportURL=https://github.com/RobotsMadeSimple/SimpleFusionScripts/blob/main/INSTALL.md
DefaultDirName={userappdata}\Autodesk\Autodesk Fusion 360\API\AddIns
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SimpleFusionScripts-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Robots Made Simple Fusion Add-ins
; (the add-ins folder is Fusion's: never removed, only our add-ins' folders inside it)
UninstallFilesDir={userappdata}\RobotsMadeSimple\uninstall
LicenseFile=..\LICENSE

[Types]
Name: "all"; Description: "All add-ins"
Name: "custom"; Description: "Choose add-ins"; Flags: iscustom

[Components]
Name: "buildbook"; Description: "BuildBook - build manuals: exploded views, pictures, parts lists, PDF and videos"; Types: all custom
Name: "browserplus"; Description: "Browser+ - find and tidy joints, part tree with folders, BOM"; Types: all
Name: "holethreadcallouts"; Description: "Hole && Thread Callouts - which holes to tap, in one picture"; Types: all

[Files]
Source: "..\BuildBook\*"; DestDir: "{app}\BuildBook"; Components: buildbook; \
  Excludes: "logs\*,cache\*,thumbs\*,tests\*,tools\*,__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\BrowserPlus\*"; DestDir: "{app}\BrowserPlus"; Components: browserplus; \
  Excludes: "logs\*,cache\*,thumbs\*,tests\*,__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\HoleThreadCallouts\*"; DestDir: "{app}\HoleThreadCallouts"; Components: holethreadcallouts; \
  Excludes: "logs\*,cache\*,thumbs\*,tests\*,__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs ignoreversion

[UninstallDelete]
; (files the add-ins made while running: Python's caches, old logs)
Type: filesandordirs; Name: "{app}\BuildBook\__pycache__"
Type: filesandordirs; Name: "{app}\BuildBook\commands\__pycache__"
Type: filesandordirs; Name: "{app}\BuildBook\lib\__pycache__"
Type: filesandordirs; Name: "{app}\BrowserPlus\__pycache__"
Type: filesandordirs; Name: "{app}\BrowserPlus\lib\__pycache__"
Type: filesandordirs; Name: "{app}\HoleThreadCallouts\__pycache__"
Type: filesandordirs; Name: "{app}\HoleThreadCallouts\lib\__pycache__"

[Messages]
FinishedLabel=The add-ins are installed.%n%nRestart Fusion (close it completely and open it again). They start on their own: BuildBook's button is in the Utilities tab, under Add-Ins.

[Code]
{ (FILE_ATTRIBUTE_REPARSE_POINT comes with Inno Setup) }

function IsLink(const Path: String): Boolean;
var
  Rec: TFindRec;
begin
  Result := False;
  if FindFirst(Path, Rec) then
  begin
    Result := (Rec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0;
    FindClose(Rec);
  end;
end;

procedure UnlinkOrBackup(const Name: String);
{ An add-in installed with the Git installer is a link to the user's copy of the repository:
  remove only the link (never write into their repository). A plain folder stays where it is
  and is updated in place. }
var
  Path: String;
  Code: Integer;
begin
  Path := ExpandConstant('{app}\') + Name;
  if DirExists(Path) and IsLink(Path) then
    Exec(ExpandConstant('{cmd}'), '/c rmdir "' + Path + '"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if WizardIsComponentSelected('buildbook') then UnlinkOrBackup('BuildBook');
  if WizardIsComponentSelected('browserplus') then UnlinkOrBackup('BrowserPlus');
  if WizardIsComponentSelected('holethreadcallouts') then UnlinkOrBackup('HoleThreadCallouts');
end;

function InitializeSetup(): Boolean;
var
  Code: Integer;
begin
  Result := True;
  { Fusion running: it keeps using the old add-ins until it's restarted (installing is fine). }
  if Exec(ExpandConstant('{cmd}'), '/c tasklist /FI "IMAGENAME eq Fusion360.exe" | find /I "Fusion360.exe" >nul',
          '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    Result := MsgBox('Fusion is open. You can install now, but the add-ins only update after Fusion is ' +
                     'closed and opened again.' + #13#10#13#10 + 'Continue?', mbConfirmation, MB_YESNO) = IDYES;
end;
