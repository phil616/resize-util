# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 单文件打包配置（适用于 PyInstaller 6.x）。
#
# 用法（在目标平台上执行，产物即该平台的可执行文件）：
#   Windows:  pyinstaller resize-gui.spec   ->  dist\ResizeTools.exe
#   Linux:    pyinstaller resize-gui.spec   ->  dist/ResizeTools
#
# 单文件 EXE 内已内置 Python 解释器、tkinter、PyMuPDF、img2pdf、Pillow，
# 以及两个子工具的 main.py，目标机无需任何 Python 环境或网络。
import glob
import os
import sys
from PyInstaller.utils.hooks import collect_all

# 收集 3 个库的全部子模块/二进制/数据（PyMuPDF 的 .pyd、Pillow 编解码器等）
datas, binaries, hiddenimports = [], [], []
for pkg in ("fitz", "img2pdf", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 两个子工具的源码：GUI 运行时按路径加载，需作为数据一并打入
datas += [
    (os.path.join("..", "pdf-resize", "main.py"), "pdf-resize"),
    (os.path.join("..", "image-resize", "main.py"), "image-resize"),
]

# —— 兼容自带 Tcl/Tk 9.0 的独立 Python（uv standalone 在 Win/Linux 都是 9.0）——
# PyInstaller 自带的 tkinter hook 目前主要覆盖 Tcl/Tk 8.6；遇到 9.0 会漏掉它的
# 共享库与脚本目录，导致打出来的程序一开界面就报 libtcl9.0 缺失。这里探测并
# 手动打入；运行时由 main.py 的 _import_tk() 设置 TCL_LIBRARY/TK_LIBRARY 指向它们。
# 用官方 python.org 的 Python（Tcl/Tk 8.6）打包时这些通配不命中，交给标准 hook。
_base = sys.base_prefix
_lib = os.path.join(_base, "lib")
for _pat in ("libtcl9*.so*", "libtk9*.so*", "libtcl9tk9*.so*"):
    for _f in glob.glob(os.path.join(_lib, _pat)):
        binaries.append((_f, "."))
for _root in (_base, _lib, os.path.join(_base, "DLLs"), os.path.join(_base, "bin")):
    for _f in glob.glob(os.path.join(_root, "*tcl9*.dll")) + glob.glob(os.path.join(_root, "*tk9*.dll")):
        binaries.append((_f, "."))
for _name in ("tcl9.0", "tk9.0", "tcl9"):
    for _root in (_lib, os.path.join(_base, "tcl")):
        _d = os.path.join(_root, _name)
        if os.path.isdir(_d):
            datas.append((_d, _name))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ResizeTools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX 可能损坏 PyMuPDF 的二进制，关闭更稳
    runtime_tmpdir=None,
    console=False,        # GUI 程序，不弹黑色控制台窗口
    disable_windowed_traceback=True,   # 未捕捉异常时直接退出，不弹模态对话框（CI 不会挂死）
    icon=None,            # 如需图标：把 .ico 路径填这里
)
