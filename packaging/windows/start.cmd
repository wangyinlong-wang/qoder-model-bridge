@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
if defined PROXY_PYTHON goto custom_python
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" goto supported_arch
if /i "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto supported_arch
echo This automatic Windows package requires Windows 10/11 x64.
echo ARM64 and 32-bit Windows require a separately validated runtime.
goto failed

:supported_arch
set "QODER_PYTHON_ROOT=%~dp0.runtime\python-3.13.16-win64"
if not exist "%QODER_PYTHON_ROOT%\python.exe" goto prepare_python
"%QODER_PYTHON_ROOT%\python.exe" -I -X utf8 -c "import sys,struct,tkinter,venv,ensurepip; assert sys.version_info[:3] == (3,13,16) and struct.calcsize('P') == 8" >nul 2>nul
if not errorlevel 1 goto python_ready

:prepare_python
echo Preparing the bundled Python 3.13.16 runtime offline...
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo Windows PowerShell 5.1 or newer is required for the first setup.
  goto failed
)
powershell.exe -NoLogo -NoProfile -Command ^
  "$ErrorActionPreference='Stop'; $runtime=$env:QODER_PYTHON_ROOT; $parent=Split-Path -Parent $runtime;" ^
  "$bundled=Join-Path (Split-Path -Parent $parent) 'installers\python-3.13.16-amd64.zip';" ^
  "if ($PSVersionTable.PSVersion -lt [Version]'5.1') { throw 'Windows PowerShell 5.1 or newer is required.' };" ^
  "New-Item -ItemType Directory -Force -Path $parent | Out-Null;" ^
  "if ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'The runtime parent must not be a junction or symbolic link.' };" ^
  "$scratch=Join-Path $parent ('.python-setup-'+[Guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $scratch | Out-Null;" ^
  "$archive=Join-Path $scratch 'python.zip'; $stage=Join-Path $scratch 'runtime';" ^
  "try {" ^
  "if (-not (Test-Path -LiteralPath $bundled -PathType Leaf)) { throw 'The bundled Python ZIP is missing. Extract the complete offline package; no download will be attempted.' };" ^
  "$archive=$bundled; Write-Host 'Using the Python archive included in this offline package.';" ^
  "$expected='bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159'; $actual=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash;" ^
  "if ($actual -ne $expected) { throw ('Python archive checksum mismatch.'+[Environment]::NewLine+'File: '+$archive+[Environment]::NewLine+'Bytes: '+(Get-Item -LiteralPath $archive).Length+[Environment]::NewLine+'Expected SHA256: '+$expected+[Environment]::NewLine+'Actual SHA256: '+$actual+[Environment]::NewLine+'No runtime files were installed. Extract a fresh copy of the complete package.') };" ^
  "Write-Host 'Python archive SHA-256 verified. Extracting...';" ^
  "Expand-Archive -LiteralPath $archive -DestinationPath $stage;" ^
  "& (Join-Path $stage 'python.exe') -I -X utf8 -c 'import sys,struct,tkinter,venv,ensurepip; assert sys.version_info[:3] == (3,13,16) and struct.calcsize(chr(80)) == 8';" ^
  "if ($LASTEXITCODE -ne 0) { throw 'The Python runtime could not start with Tcl/Tk.' };" ^
  "if (Test-Path -LiteralPath $runtime) { if ((Get-Item -LiteralPath $runtime).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Existing runtime is a link; it was preserved.' }; $backup=$runtime+'.backup-'+[Guid]::NewGuid().ToString('N'); [IO.Directory]::Move($runtime,$backup); Write-Host ('Previous runtime preserved at '+$backup) };" ^
  "[IO.Directory]::Move($stage,$runtime);" ^
  "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 } finally { if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force } }"
if errorlevel 1 goto failed

:python_ready
set "PROXY_PYTHON=%QODER_PYTHON_ROOT%\python.exe"
if not defined PROXY_GUI_PYTHON set "PROXY_GUI_PYTHON=%PROXY_PYTHON%"

:custom_python
"%PROXY_PYTHON%" -I -X utf8 "%~dp0bootstrap.py" %*
set "QODER_EXIT_CODE=%ERRORLEVEL%"
if not "%QODER_EXIT_CODE%"=="0" if not defined QODER_INSTALLER_MODE pause
exit /b %QODER_EXIT_CODE%

:failed
echo Setup did not finish. Check the error above, then run start.cmd again.
if not defined QODER_INSTALLER_MODE pause
exit /b 1
