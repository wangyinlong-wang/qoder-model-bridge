; -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "FileFunc.nsh"

!ifndef PAYLOAD_DIR
  !error "Define PAYLOAD_DIR as the prepared offline payload directory."
!endif
!ifndef OUTPUT_FILE
  !error "Define OUTPUT_FILE as the installer executable path."
!endif

!define APP_NAME "Qoder 内网模型接入"
!define APP_ID "qoder-internal-proxy-offline-1"
!define APP_KEY "Software\QoderInternalProxy"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\QoderInternalProxy"
!define PYTHON_DIR ".runtime\python-3.13.16-win64"

Name "${APP_NAME}（离线安装版）"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\QoderInternalProxy"
RequestExecutionLevel user
SetCompressor zlib
CRCCheck on
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "1.1.2.0"
VIAddVersionKey "ProductName" "Qoder Internal Proxy Offline Installer"
VIAddVersionKey "FileDescription" "Offline installer for the local internal-model helper"
VIAddVersionKey "FileVersion" "1.1.2"
VIAddVersionKey "ProductVersion" "1.1.2"
VIAddVersionKey "LegalCopyright" "Bundled third-party notices are preserved."

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "安装本地模型接入工具、Python 和全部组件。安装过程无需联网。$\r$\n$\r$\n适用于 Windows 10/11 x64，默认安装到当前用户目录。使用模型时仍需访问内网网关。$\r$\n$\r$\n请先停止转发并关闭已打开的工具窗口。"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE ValidateInstallDirectory
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "打开 Qoder 内网模型接入"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApplication
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "本安装包需要 Windows 10/11 x64。"
    Abort
  ${EndIf}
  ReadEnvStr $0 PROCESSOR_ARCHITECTURE
  ReadEnvStr $1 PROCESSOR_ARCHITEW6432
  ${If} $0 != "AMD64"
  ${AndIf} $1 != "AMD64"
    MessageBox MB_OK|MB_ICONSTOP "本安装包只支持 Windows x64；ARM64 和 32 位系统尚未适配。"
    Abort
  ${EndIf}
  FindWindow $0 "" "${APP_NAME}"
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "请先在工具窗口停止转发并关闭窗口，再重新运行安装器。"
    Abort
  ${EndIf}
  ReadRegStr $0 HKCU "${APP_KEY}" "InstallDir"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
FunctionEnd

Function ValidateInstallDirectory
  System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR") i.r0'
  ${If} $0 == -1
    Return
  ${EndIf}
  IntOp $1 $0 & 0x400
  ${If} $1 != 0
    MessageBox MB_OK|MB_ICONSTOP "请选择普通文件夹，不能安装到符号链接或目录联接。"
    Abort
  ${EndIf}
  ReadINIStr $0 "$INSTDIR\qoder-install.ini" "Installation" "Id"
  ${If} $0 == "${APP_ID}"
    IfFileExists "$INSTDIR\.runtime\qoder-proxy-rollback.json" pending_restore 0
    IfFileExists "$INSTDIR\.runtime\qoder-ide-global-proxy-rollback.json" pending_restore 0
    IfFileExists "$INSTDIR\.runtime\qoder-ide-cn-proxy-rollback.json" pending_restore verified
    pending_restore:
    MessageBox MB_OK|MB_ICONSTOP "此目录仍有活动代理记录。请先在原工具正常停止并退出以释放本工具记录；IDEA 仍需完成代理恢复，再重新安装。"
    Abort
  ${EndIf}
  FindFirst $0 $1 "$INSTDIR\*"
  inspect_directory:
    ${If} $1 == ""
      FindClose $0
      Return
    ${EndIf}
    ${If} $1 != "."
    ${AndIf} $1 != ".."
      FindClose $0
      MessageBox MB_OK|MB_ICONSTOP "请选择空文件夹，或本工具先前的安装目录，避免覆盖其他文件。"
      Abort
    ${EndIf}
    FindNext $0 $1
    Goto inspect_directory
  verified:
FunctionEnd

Section "离线安装" SEC_MAIN
  Call ValidateInstallDirectory
  SetOutPath "$INSTDIR"
  WriteINIStr "$INSTDIR\qoder-install.ini" "Installation" "Id" "${APP_ID}"
  WriteINIStr "$INSTDIR\qoder-install.ini" "Installation" "Version" "1.1.2"
  !include "install-files.nsh"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  DetailPrint "正在从随包文件准备 Python 和组件，请稍候……"
  ; SetEnvironmentVariable affects this installer process and its children only.
  System::Call 'kernel32::SetEnvironmentVariableW(w "QODER_INSTALLER_MODE", w "1") i.r0'
  System::Call 'kernel32::SetEnvironmentVariableW(w "PROXY_PYTHON", p 0) i.r0'
  System::Call 'kernel32::SetEnvironmentVariableW(w "PROXY_GUI_PYTHON", p 0) i.r0'
  nsExec::ExecToLog '"$SYSDIR\cmd.exe" /d /v:off /c start.cmd --setup-only'
  Pop $0
  System::Call 'kernel32::SetEnvironmentVariableW(w "QODER_INSTALLER_MODE", p 0)'
  ${If} $0 != "0"
    DetailPrint "离线环境准备失败，退出状态：$0"
    MessageBox MB_OK|MB_ICONSTOP "离线环境准备失败，尚未创建启动快捷方式。请查看安装详情。$\r$\n$\r$\n可重新运行此安装包，或运行安装目录中的 Uninstall.exe 移除文件。"
    SetErrorLevel 1
    Abort
  ${EndIf}
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${PYTHON_DIR}\pythonw.exe" '-X utf8 "$INSTDIR\launcher.py"'
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${PYTHON_DIR}\pythonw.exe" '-X utf8 "$INSTDIR\launcher.py"'
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\环境检查.lnk" "$SYSDIR\cmd.exe" '/d /v:off /k start.cmd --setup-only'
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\安装使用说明.lnk" "$SYSDIR\notepad.exe" '"$INSTDIR\INSTALLER-README.txt"'
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}（离线安装版）"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "1.1.2"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${PYTHON_DIR}\pythonw.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Function LaunchApplication
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\${PYTHON_DIR}\pythonw.exe" -X utf8 "$INSTDIR\launcher.py"'
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  ReadINIStr $0 "$INSTDIR\qoder-install.ini" "Installation" "Id"
  ${If} $0 != "${APP_ID}"
    MessageBox MB_OK|MB_ICONSTOP "找不到本工具的安装标识，已停止卸载。"
    Abort
  ${EndIf}
  System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR") i.r0'
  IntOp $1 $0 & 0x400
  ${If} $1 != 0
    MessageBox MB_OK|MB_ICONSTOP "安装目录已变成链接，已停止卸载。"
    Abort
  ${EndIf}
  FindWindow $0 "" "${APP_NAME}"
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "请先在工具窗口停止转发并关闭窗口，再重新卸载。"
    Abort
  ${EndIf}
  IfFileExists "$INSTDIR\.runtime\qoder-proxy-rollback.json" pending_uninstall_restore 0
  IfFileExists "$INSTDIR\.runtime\qoder-ide-global-proxy-rollback.json" pending_uninstall_restore 0
  IfFileExists "$INSTDIR\.runtime\qoder-ide-cn-proxy-rollback.json" pending_uninstall_restore restored
  pending_uninstall_restore:
  MessageBox MB_OK|MB_ICONSTOP "此工具仍有活动代理记录。请先正常停止并退出以释放本工具记录；IDEA 仍需完成代理恢复，再重新卸载。"
  Abort
  restored:
FunctionEnd

; This recursion deliberately preserves every reparse point, including nested junctions.
Function un.RemoveGeneratedTree
  Exch $0
  Push $1
  Push $2
  Push $3
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r3'
  ${If} $3 == -1
    Goto cleanup_done
  ${EndIf}
  IntOp $1 $3 & 0x400
  ${If} $1 != 0
    DetailPrint "保留链接：$0"
    Goto cleanup_done
  ${EndIf}
  IntOp $1 $3 & 0x10
  ${If} $1 == 0
    Delete "$0"
    Goto cleanup_done
  ${EndIf}
  FindFirst $1 $2 "$0\*"
  cleanup_loop:
    ${If} $2 == ""
      FindClose $1
      RMDir "$0"
      Goto cleanup_done
    ${EndIf}
    ${If} $2 != "."
    ${AndIf} $2 != ".."
      Push "$0\$2"
      Call un.RemoveGeneratedTree
    ${EndIf}
    FindNext $1 $2
    Goto cleanup_loop
  cleanup_done:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Delete only named package files and do not follow changed parent directories.
Function un.DeletePackageFile
  Exch $0
  Push $1
  Push $2
  StrCpy $1 $0
  parent_loop:
    System::Call 'kernel32::GetFileAttributesW(w r1) i.r2'
    IntOp $2 $2 & 0x400
    ${If} $2 != 0
      Goto file_done
    ${EndIf}
    ${GetParent} "$1" $1
    ${If} $1 != "$INSTDIR"
    ${AndIf} $1 != ""
      Goto parent_loop
    ${EndIf}
  Delete "$0"
  file_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  Push "$INSTDIR\.venv"
  Call un.RemoveGeneratedTree
  Push "$INSTDIR\__pycache__"
  Call un.RemoveGeneratedTree
  System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR\.runtime") i.r0'
  IntOp $0 $0 & 0x400
  ${If} $0 == 0
    Push "$INSTDIR\${PYTHON_DIR}"
    Call un.RemoveGeneratedTree
  ${EndIf}
  !include "uninstall-files.nsh"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\环境检查.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\安装使用说明.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\卸载.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "${APP_KEY}"
  Delete "$INSTDIR\qoder-install.ini"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DetailPrint "工具已卸载；保存的配置、证书及非本安装包文件保留在原目录。"
SectionEnd
