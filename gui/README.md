# resize-gui

把 [`pdf-resize`](../pdf-resize/) 与 [`image-resize`](../image-resize/) 两个压缩工具集成到一个 **tkinter** 图形界面，方便不熟悉命令行的用户操作。

界面分两个选项卡（PDF 压缩 / 图像压缩），底部是实时运行日志与进度条。

## 安装与运行

```bash
uv sync
uv run python main.py        # 需要图形环境（DISPLAY / 桌面）
```

> tkinter 随 Python 自带，无需单独安装；`uv sync` 装的是两个子工具用到的
> `pymupdf` / `img2pdf` / `pillow`，供 GUI 直接复用它们的核心逻辑。

## 实现方式

GUI **不重复实现压缩算法**：运行时按路径把两个子项目的 `main.py` 当作模块加载，
用界面字段拼出命令行参数，交给各自的 `build_parser()` 校验、再调用 `run()` 执行。
任务跑在后台线程里，子工具的进度打印通过队列实时回显到日志区——所以两个 CLI
工具更新后，GUI 自动跟随，无需改动。

## 选项卡

- **PDF 压缩**：选输入 PDF、目标体积，可选统一页面尺寸（A4/mode/max/自定义）、
  适配方式、最大/最小 DPI、最低质量、灰度、页面范围、口令。
- **图像压缩**：添加多个文件或目录，选「固定质量 / 目标体积」模式、输出格式
  （original/jpg/png/…）、长边上限、输出目录、后缀，以及递归 / 原地覆盖 / 允许变大。

## 打包成单文件 EXE（给没有 Python 的用户）

把整套程序打成**一个 `ResizeTools.exe`**（内置 Python 与全部依赖，离线可用）。
详见 **[BUILD.md](BUILD.md)**：在 Windows 上跑 `build.bat`，或用仓库自带的
GitHub Actions 工作流在云端构建并下载。

> 注意：PyInstaller 不能跨平台编译，`.exe` 必须在 Windows 上构建。

## 无界面自检 / CI smoke test

```bash
RESIZE_SELFTEST=1 uv run python main.py   # 跑通三大依赖与两个子工具，退出码 0 即正常
```
