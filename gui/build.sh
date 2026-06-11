#!/usr/bin/env bash
# 在 Linux / macOS 上打包成单文件 dist/ResizeTools（产物即当前系统的可执行文件，
# 不是 Windows .exe —— PyInstaller 不能跨平台编译，Windows 请用 build.bat 或 CI）。
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .build-venv
# shellcheck disable=SC1091
. .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install pyinstaller pymupdf img2pdf pillow
pyinstaller resize-gui.spec --noconfirm --clean

echo
echo "===== 完成 ====="
echo "产物: $(pwd)/dist/ResizeTools"
echo "自检: RESIZE_SELFTEST=1 ./dist/ResizeTools   (退出码 0 即正常)"
