# pdf-resize

把 PDF 整页光栅化为图像并压缩到**指定体积上限**的单文件工具。适用于矢量/字体/扫描内容繁多、常规压缩收效甚微的场景。代价是丢失文字层（不可再选中/搜索）。

## 安装

```bash
uv sync          # 或 pip install pymupdf img2pdf pillow
```

## 用法

```bash
# 压到 2MB 以内，保持每页原始尺寸
uv run python main.py whitepaper.pdf --target 2M

# 把所有页统一成 A4，留白适配，灰度
uv run python main.py whitepaper.pdf --target 500K --page-size A4 --grayscale

# 期望画质不低于 q=50，必要时降低分辨率来达成
uv run python main.py whitepaper.pdf --target 1M --min-quality 50

# 只处理 1-3 和第 5 页
uv run python main.py whitepaper.pdf --target 800K --pages 1-3,5
```

默认输出 `<输入名>.squeezed.pdf`，可用 `-o` 指定。压不到目标时退出码为 1，并输出能做到的最小结果。

## 工作原理

1. **每页只渲染一次**为最高分辨率主图，各 DPI 档位由主图做 Lanczos 缩小得到，避免重复光栅化。
2. 在 **(dpi, JPEG 质量)** 二维空间里搜索“目标体积下的最佳画质”：DPI 从高到低定位可行档位，质量做二分搜索。体积用 img2pdf 真实封装后**实测**。
3. 多页时 JPEG 编码用线程池并行。
4. img2pdf 无损封装 JPEG（不重编码），DPI 元数据决定输出页面物理尺寸。

## 主要参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--target` | 目标体积，如 `2M` / `500K` / `1.5MB`（必填） | — |
| `--page-size` | 统一页面尺寸：`auto`(保持原样) / `mode` / `max` / `A4`·`Letter`·`A3`·`A5`·`Legal` / `210x297mm` / `595x842` / `8.5x11in` | `auto` |
| `--fit` | 归一化适配：`contain`(留白) / `cover`(裁切) / `stretch`(拉伸) | `contain` |
| `--pad-color` | contain 留白色：`white`/`black`/`#RRGGBB`/`R,G,B` | `white` |
| `--no-auto-orient` | 归一化时不自动旋转页面匹配方向 | 关 |
| `--max-dpi` / `--min-dpi` | 渲染分辨率上下限 | 200 / 72 |
| `--qmin` / `--qmax` | JPEG 质量搜索区间 | 15 / 90 |
| `--min-quality` | 期望最低质量，为达成不惜降 dpi（0=关闭） | 0 |
| `--grayscale` | 转灰度，体积更省 ~30-60% | 关 |
| `--pages` | 页面范围，如 `1-3,5`（1 基，含端点） | 全部 |
| `--password` | 加密 PDF 口令 | — |
| `--jobs` | 并行编码线程数 | CPU 数 |
| `--quiet` | 静默 | 关 |
