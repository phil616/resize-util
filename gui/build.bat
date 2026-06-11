@echo off
REM 在 Windows 上一键打包成单文件 dist\ResizeTools.exe（最终用户无需 Python / 网络）。
REM 需要本机已装 Python 3.12+（python.org 官方版即可，自带 Tcl/Tk 8.6，最稳）。
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.12+ 并勾选 "Add to PATH"。
  exit /b 1
)

echo [1/3] 创建打包用虚拟环境 .build-venv ...
python -m venv .build-venv || exit /b 1
call .build-venv\Scripts\activate.bat

echo [2/3] 安装依赖（pyinstaller / pymupdf / img2pdf / pillow）...
python -m pip install --upgrade pip >nul
python -m pip install pyinstaller pymupdf img2pdf pillow || exit /b 1

echo [3/3] 打包（单文件，无控制台窗口）...
pyinstaller resize-gui.spec --noconfirm --clean || exit /b 1

echo.
echo ===== 完成 =====
echo 产物: %cd%\dist\ResizeTools.exe
echo 自检: set RESIZE_SELFTEST=1 ^&^& dist\ResizeTools.exe   (退出码 0 即正常)
endlocal
