# 打包成单文件 Windows EXE

把整套程序（Python 解释器 + tkinter + PyMuPDF/img2pdf/Pillow + 两个子工具）打进
**一个 `ResizeTools.exe`**。最终用户**无需安装 Python、无需联网**，双击即用。

## ⚠️ 重要前提：不能在 Linux/macOS 上产出 Windows .exe

PyInstaller（以及 Nuitka 等）**只能为运行它的操作系统生成可执行文件，不支持跨平台编译**。
所以 `.exe` 必须在 **Windows 上**构建，二选一：

| 方式 | 需要 Windows 机器？ | 步骤 |
|---|---|---|
| **A. 在 Windows 上本地打包** | 需要 | 双击运行 `gui\build.bat` |
| **B. 用 GitHub Actions 云端打包** | 不需要 | 见下方「云端构建」，在 Actions 里下载 .exe |

## A. 在 Windows 上本地打包

1. 安装 [Python 3.12+](https://www.python.org/downloads/)（勾选 *Add python.exe to PATH*）。
2. 把整个仓库拷到 Windows，进入 `gui\` 目录，双击或在命令行运行：
   ```bat
   build.bat
   ```
3. 产物在 `gui\dist\ResizeTools.exe`。把这**一个文件**发给用户即可。

> 手动等价命令：
> ```bat
> python -m venv .build-venv && .build-venv\Scripts\activate
> pip install pyinstaller pymupdf img2pdf pillow
> pyinstaller resize-gui.spec --noconfirm --clean
> ```

## B. 云端构建（无 Windows 机器）

仓库已含 `.github/workflows/build-windows.yml`：

1. 推到 GitHub。
2. 打开仓库 **Actions → build-windows-exe → Run workflow**（或推一个 `v*` tag 自动触发）。
3. 跑完后在该次运行页面底部 **Artifacts** 里下载 `ResizeTools-windows`，解压即得 `ResizeTools.exe`。

CI 用 python.org 官方 Python（Tcl/Tk 8.6），打包后会自动跑一次无界面自检（`RESIZE_SELFTEST`），退出码 0 才算通过。

## 验证产物

无界面冒烟自检（会实际跑通 Pillow / img2pdf / PyMuPDF 与两个子工具）：

```bat
set RESIZE_SELFTEST=1 && dist\ResizeTools.exe   :: 打印 SELFTEST ... OK，退出码 0
```

直接双击 `ResizeTools.exe` 则打开图形界面。

## 说明与注意

- **体积**：单文件约 50–60MB（内置了整个 Python 与 PyMuPDF 等二进制），属正常。
- **首次启动**：单文件会先自解压到临时目录，首次启动略慢，之后正常。
- **Tcl/Tk 版本**：用官方 Python（8.6）时 PyInstaller 标准 hook 直接搞定；若用
  uv 的独立 Python（自带 Tcl/Tk **9.0**），`resize-gui.spec` 会自动探测并打入
  9.0 的库与脚本目录，`main.py` 运行时设置 `TCL_LIBRARY/TK_LIBRARY` —— 两种都已适配。
- **杀毒误报**：PyInstaller 单文件偶尔被某些杀软误报，可改用目录模式
  （把 spec 末尾的 `EXE(...)` 拆成 `EXE(...exclude_binaries=True...)` + `COLLECT(...)`）或对 exe 签名。
- **图标**：把 `.ico` 路径填进 `resize-gui.spec` 里 `EXE(..., icon=...)`。
