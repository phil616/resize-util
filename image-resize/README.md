# image-resize

通用图像有损压缩单文件工具。**任意格式 → 拍平为 JPEG 有损压缩 → 转回原格式**，以压小体积为第一目标，画质无所谓。自动忽略 PNG/调色板的 alpha 通道（拼合到背景色），多帧 GIF/TIFF 只取首帧。

## 安装

```bash
uv sync          # 或 pip install pillow
```

## 用法

```bash
# 单张：默认低质量(q=30)，输出 photo.min.png
uv run python main.py photo.png

# 批量 + 固定更低质量
uv run python main.py *.jpg --quality 20

# 目录递归，统一输出为 jpg（对 png 等无损格式这样才真正能压小）
uv run python main.py img/ --recursive --to jpg

# 压到 80KB 以内（自动缩放 + 质量二分搜索）
uv run python main.py a.png --target 80K

# 限制长边 1600px 并原地覆盖
uv run python main.py a.jpg --max-dimension 1600 --inplace
```

默认在源文件旁写出 `<名字>.min.<扩展名>`，可用 `--out-dir` / `--inplace` 改变。

## 工作原理

1. Pillow 打开任意格式 → 修正 EXIF 方向 → 把 alpha 拍平到背景色 → 转 RGB。
2. JPEG 有损压缩（`progressive` + `optimize` + 4:2:0 子采样，体积最小）。
   - 给定 `--target`：在 **(缩放, 质量)** 二维空间搜索命中体积上限的方案；先在高分辨率二分质量，最低质量仍超标则按比例缩小再试。
   - 否则用固定 `--quality`，可叠加 `--max-dimension` 限长边。
3. 把压缩结果转回目标格式（`--to`，默认原格式）保存。多文件并行处理。

## 关于"转回原格式"与体积

JPEG 这一步才是真正的有损压缩。若把结果再转回 **PNG 等无损格式**，JPEG 噪点会让 PNG 反而**变大**——`--skip-larger`（默认开）会检测到并跳过，提示你改用 `--to jpg`。所以：

- 想保留格式：`image.png → image.min.png`（仅当确实变小才写出）。
- 想稳定压小：加 `--to jpg`，输出始终是体积最小的 JPEG。

## 主要参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--target` | 目标体积上限，如 `80K` / `500K`（启用缩放+质量搜索） | 关 |
| `--quality` | 无 target 时的固定 JPEG 质量 | 30 |
| `--qmin` / `--qmax` | target 搜索的质量区间 | 5 / 90 |
| `--max-dimension` | 限制长边像素（只缩小） | 关 |
| `--min-scale` / `--scale-step` | target 搜索的缩放下限/等比系数 | 0.2 / 0.8 |
| `--to` | 输出格式：`original`(默认) / `jpg` / `png` / `webp` / `bmp` / `tiff` | original |
| `--bg` | 拍平 alpha 的背景色 | white |
| `--out-dir` / `--suffix` | 输出目录 / 文件名后缀 | 同目录 / `.min` |
| `--inplace` | 原地覆盖（锁定原格式，忽略 `--to`） | 关 |
| `--allow-larger` | 允许写出比原图更大的结果 | 关 |
| `--recursive` | 递归处理目录 | 关 |
| `--jobs` | 并行线程数 | CPU 数 |
| `--quiet` | 静默 | 关 |
