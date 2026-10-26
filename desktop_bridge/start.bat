@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto check_venv
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto create_with_py
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto create_with_python
echo 未找到 Python 3.11 或更新版本。
echo 请安装 Windows 版 Python，并保留 Tcl/Tk 组件，然后重新运行本文件。
echo 下载地址：https://www.python.org/downloads/windows/
goto failed

:create_with_py
py -3 -m venv .venv
if errorlevel 1 goto failed
goto check_venv

:create_with_python
python -m venv .venv
if errorlevel 1 goto failed

:check_venv
".venv\Scripts\python.exe" -c "import sys, tkinter; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 (
  echo 本目录的 Python 环境不可用，或缺少 Tcl/Tk 图形界面组件。
  echo 请安装完整的 Python 3.11 或更新版本，再删除本目录下的 .venv 文件夹重试。
  goto failed
)
".venv\Scripts\python.exe" -c "import websockets; from websockets.sync.client import connect; assert 12 <= int(websockets.__version__.split('.')[0]) < 13" >nul 2>&1
if not errorlevel 1 goto launch
echo 首次运行，正在安装连接工具依赖…
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

:launch
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo 启动未完成。请查看上方提示后重试。
pause
exit /b 1
