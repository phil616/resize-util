# resize-tools

以"压小体积为目标"的压缩工具集，各子项目独立（独立 `pyproject.toml` / 虚拟环境）。

| 子项目 | 作用 |
|---|---|
| [`pdf-resize/`](pdf-resize/) | 把 PDF 整页光栅化为图像并压缩到指定体积上限；支持多页、各页尺寸/方向不同时统一页面尺寸。 |
| [`image-resize/`](image-resize/) | 通用图像有损压缩：任意格式 → JPEG 压缩 → 转回原格式，忽略 alpha / 动画，以压小为目标。 |
| [`gui/`](gui/) | tkinter 图形界面，把上面两个工具集成到一个窗口，方便非命令行用户操作。 |

每个子项目的用法见各自目录下的 README。

```bash
# 命令行
cd pdf-resize   && uv sync && uv run python main.py whitepaper.pdf --target 2M
cd image-resize && uv sync && uv run python main.py photo.png --to jpg

# 图形界面（一个窗口操作两个工具）
cd gui && uv sync && uv run python main.py
```

打包成单文件 Windows EXE（最终用户免 Python、离线可用）见 [`gui/BUILD.md`](gui/BUILD.md)。

## 仓库结构

```
.
├── pdf-resize/      PDF 压缩 CLI
├── image-resize/    图像压缩 CLI
├── gui/             tkinter 图形界面 + 打包脚本（build.bat / resize-gui.spec）
├── .github/workflows/build-windows.yml   云端构建 Windows .exe
├── .gitattributes   文本换行规范化 + 二进制走 Git LFS
├── .gitignore
└── LICENSE          MIT
```

> 二进制资源（`*.pdf`、图片等）通过 **Git LFS** 跟踪，clone 时需安装
> [git-lfs](https://git-lfs.com/)；否则拿到的是指针文件。

## License

[MIT](LICENSE) © 2026 phil616
