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
2. 压缩：
   - **`--target`（精确逼近目标体积）**：直接**按最终输出格式测量体积**来搜索，
     使结果 `<= 目标` 且尽量贴近（实测 99%+）。lossy 格式（jpg/webp）优先保
     分辨率二分质量，再用**连续缩放**填满量化余量；无损格式（png/bmp/tiff）质量
     无意义，纯靠连续缩放精确逼近。
   - 否则用固定 `--quality`（先转 JPEG 压缩再转回原格式），可叠加 `--max-dimension`。
3. 写出目标格式（`--to`，默认原格式）。多文件并行处理。

> 体积按真实格式测量，所以 `--target` 对 **png / webp** 也精确——这修复了旧版
> “只按中间 JPEG 估体积、转 png/webp 后严重偏离目标”的问题。

## 关于无损格式（png/bmp）的目标体积

PNG 等无损格式只能靠**降分辨率**压小。复杂图在默认下限 `--min-scale 0.2` 内可能
压不到很小的目标，此时会标注「未达标」并输出该下限处的最小结果。想精确命中很小的
目标，调低 `--min-scale`（如 `0.05`），或干脆用 `--to jpg` / `--to webp`。`--skip-larger`
（默认开）仍会拦下比原图更大的结果。

## 主要参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--target` | 目标体积，按最终格式精确逼近（结果 `<=` 目标），如 `80K` / `500K` | 关 |
| `--quality` | 无 target 时的固定 JPEG 质量 | 30 |
| `--qmin` / `--qmax` | target 搜索的质量区间（lossy 格式） | 5 / 90 |
| `--max-dimension` | 限制长边像素（只缩小） | 关 |
| `--min-scale` | target 搜索时允许的最小缩放比例 | 0.2 |
| `--to` | 输出格式：`original`(默认) / `jpg` / `png` / `webp` / `bmp` / `tiff` | original |
| `--bg` | 拍平 alpha 的背景色 | white |
| `--out-dir` / `--suffix` | 输出目录 / 文件名后缀 | 同目录 / `.min` |
| `--inplace` | 原地覆盖（锁定原格式，忽略 `--to`） | 关 |
| `--allow-larger` | 允许写出比原图更大的结果 | 关 |
| `--recursive` | 递归处理目录 | 关 |
| `--jobs` | 并行线程数 | CPU 数 |
| `--quiet` | 静默 | 关 |
