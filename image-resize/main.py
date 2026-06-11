#!/usr/bin/env python3
"""
image-resize / main.py — 通用图像有损压缩。

原则：以压小体积为第一目标，画质无所谓。任何格式都先拍平为 RGB（丢弃
PNG/调色板透明，多帧 GIF/TIFF 只取首帧），用 JPEG 做有损压缩，再转回原
格式（或 --to 指定的格式）保存。

管线:
  1. Pillow 打开任意格式 -> 修正 EXIF 方向 -> 拍平 alpha 到背景色 -> RGB。
  2. JPEG 有损压缩（progressive + optimize + 4:2:0 子采样，体积最小）：
       - 给定 --target：在 (缩放, JPEG 质量) 二维空间搜索命中体积上限的方案，
         先在高分辨率下二分质量；最低质量仍超标则按比例缩小再试。
       - 否则用固定 --quality，可叠加 --max-dimension 限制长边。
  3. 把压缩结果转回目标格式保存。
  4. --skip-larger（默认开）：结果不比原图小则跳过，绝不把文件改大。
     （PNG 等无损格式经 JPEG 往复后常会变大，此时改用 --to jpg 才能真正压缩。）

用法:
  python main.py photo.png                       # -> photo.min.png（默认 q=30）
  python main.py *.jpg --quality 20              # 批量，固定低质量
  python main.py img/ --recursive --to jpg       # 目录递归，统一输出 jpg
  python main.py a.png --target 80K              # 压到 80KB 以内（缩放+质量搜索）
  python main.py a.jpg --max-dimension 1600 --inplace   # 限长边并原地覆盖

依赖: pip install pillow
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image, ImageOps

# 能读取的输入扩展名（输出格式见 EXT_FMT / --to）
INPUT_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif",
              ".bmp", ".tif", ".tiff", ".ppm", ".pgm", ".tga", ".ico"}

# 扩展名 -> 输出时使用的保存格式；gif 丢动画后退化为 png
EXT_FMT = {".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg", ".png": "png",
           ".webp": "webp", ".gif": "png", ".bmp": "bmp",
           ".tif": "tiff", ".tiff": "tiff"}
FMT_EXT = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "bmp": ".bmp", "tiff": ".tiff"}

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)B?\s*$", re.IGNORECASE)
_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


# ── 单位与解析 ────────────────────────────────────────────────────────────────

def parse_size(s: str) -> int:
    m = _SIZE_RE.match(s)
    if not m:
        raise argparse.ArgumentTypeError(f"无法解析体积: {s!r}（示例 2M / 500K / 80K）")
    return int(float(m.group(1)) * _MULT[m.group(2).upper()])


def human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def parse_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lower()
    named = {"white": (255, 255, 255), "black": (0, 0, 0),
             "gray": (128, 128, 128), "grey": (128, 128, 128)}
    if s in named:
        return named[s]
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    if "," in s:
        p = [int(x) for x in s.split(",")]
        if len(p) == 3:
            return tuple(p)  # type: ignore[return-value]
    raise argparse.ArgumentTypeError(f"无法解析颜色: {s!r}")


def resolve_format(path: str, to: str | None) -> str:
    """决定输出保存格式（小写 Pillow 格式名）。"""
    if to and to.lower() not in ("original", "keep"):
        f = to.lower()
        return "jpeg" if f in ("jpg", "jpe") else f
    return EXT_FMT.get(os.path.splitext(path)[1].lower(), "jpeg")


# ── 加载与编码 ────────────────────────────────────────────────────────────────

def load_flat(path: str, bg: tuple[int, int, int]) -> Image.Image:
    """打开任意图像，修正方向，拍平到 RGB（忽略 alpha、只取首帧）。"""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)  # 手机照片方向修正
    except Exception:  # noqa: BLE001
        pass
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, bg)
        base.paste(rgba, mask=rgba.split()[-1])  # 用 alpha 作蒙版拍到背景上
        return base
    return img.convert("RGB")


def cap_dimension(img: Image.Image, max_dim: int) -> Image.Image:
    """限制长边不超过 max_dim（只缩小不放大）。"""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    s = max_dim / max(w, h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def scaled(img: Image.Image, scale: float) -> Image.Image:
    if scale >= 0.999:
        return img
    w, h = img.size
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def encode_jpeg(img: Image.Image, q: int) -> bytes:
    """以最省体积的设置编码 JPEG。"""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q, optimize=True,
             progressive=True, subsampling="4:2:0")
    return buf.getvalue()


LOSSY_FMTS = {"jpeg", "webp"}  # 质量是有效体积杠杆的格式；其余（png/bmp/tiff）靠缩放


def encode_to(im: Image.Image, q: int, fmt: str) -> bytes:
    """把图像直接编码为【最终输出格式】的字节，体积可精确测量/控制。
    lossy 格式用 q 作质量；无损格式（png/bmp/tiff）忽略 q，靠分辨率控体积。"""
    buf = io.BytesIO()
    if fmt == "jpeg":
        im.save(buf, format="JPEG", quality=q, optimize=True,
                progressive=True, subsampling="4:2:0")
    elif fmt == "webp":
        im.save(buf, format="WEBP", quality=q, method=6)
    elif fmt == "png":
        im.save(buf, format="PNG", optimize=True)
    elif fmt == "tiff":
        im.save(buf, format="TIFF", compression="tiff_deflate")
    else:  # bmp 等
        im.save(buf, format=fmt.upper())
    return buf.getvalue()


def _scale_fill(make, target, q, smin, smax, iters=16, tol=0.004):
    """在 [smin, smax] 连续二分缩放比例，找使 make(s,q) <= target 的最大 s。
    体积随分辨率单调，故能精确逼近 target。返回 (data, scale) 或 None（最小也超标）。"""
    d_hi = make(smax, q)
    if len(d_hi) <= target:          # 满分辨率即达标
        return d_hi, smax
    d_lo = make(smin, q)
    if len(d_lo) > target:           # 最小分辨率也超标 → 不可行
        return None
    best, lo, hi = (d_lo, smin), smin, smax
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        d = make(mid, q)
        n = len(d)
        if n <= target:
            best, lo = (d, mid), mid
            if target - n <= tol * target:   # 已足够接近，提前收敛
                break
        else:
            hi = mid
    return best


def _lossy_to_target(img, target, qmin, qmax, min_scale, fmt):
    """lossy 格式（jpeg/webp）下逼近 target。返回 (data, scale, q, size, feasible)。
    优先保分辨率二分质量，再用连续缩放填满整数质量留下的余量。"""
    def make(scale, q):
        return encode_to(scaled(img, scale), q, fmt)

    top = make(1.0, qmax)
    if len(top) <= target:                       # 满分辨率最高质量已达标（不放大）
        return top, 1.0, qmax, len(top), True

    full_lo = make(1.0, qmin)
    if len(full_lo) <= target:                   # 质量杠杆即可达标，保满分辨率
        loq, hiq, bq, bdata = qmin, qmax, qmin, full_lo
        while loq <= hiq:
            mid = (loq + hiq) // 2
            d = make(1.0, mid)
            if len(d) <= target:
                bq, bdata = mid, d
                loq = mid + 1
            else:
                hiq = mid - 1
        if bq < qmax:                            # 用 q=bq+1 + 连续缩放填满余量
            r = _scale_fill(make, target, bq + 1, min_scale, 1.0)
            if r and len(r[0]) > len(bdata):
                return r[0], r[1], bq + 1, len(r[0]), True
        return bdata, 1.0, bq, len(bdata), True

    r = _scale_fill(make, target, qmin, min_scale, 1.0)   # 最低质量仍超标→缩放
    if r:
        return r[0], r[1], qmin, len(r[0]), True
    data = make(min_scale, qmin)                 # 连最小分辨率都超标
    return data, min_scale, qmin, len(data), False


def compress_to_target(img, target, qmin, qmax, fmt, min_scale):
    """逼近 target，体积按【最终格式】实测。无损格式压不到目标时**自动转 JPEG**
    （只关心大小，不在乎质量/透明）。返回 (data, scale, q, size, feasible, out_fmt)。"""
    if fmt in LOSSY_FMTS:
        d, s, q, sz, ok = _lossy_to_target(img, target, qmin, qmax, min_scale, fmt)
        return d, s, q, sz, ok, fmt

    # 无损格式（png/bmp/tiff）：只能靠降分辨率，先试无损
    def make(scale, q):
        return encode_to(scaled(img, scale), q, fmt)

    r = _scale_fill(make, target, 0, min_scale, 1.0)
    if r:                                        # 无损即可达标，保留原格式
        return r[0], r[1], 0, len(r[0]), True, fmt
    # 无损压不到目标 → 转 JPEG 给你压到大小
    d, s, q, sz, ok = _lossy_to_target(img, target, qmin, qmax, min_scale, "jpeg")
    return d, s, q, sz, ok, "jpeg"


def render_output(jpeg: bytes, fmt: str) -> tuple[bytes, str]:
    """把 JPEG 压缩结果转回目标格式的字节。"""
    if fmt == "jpeg":
        return jpeg, ".jpg"
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    buf = io.BytesIO()
    if fmt == "png":
        im.save(buf, "PNG", optimize=True)
    elif fmt == "webp":
        im.save(buf, "WEBP", quality=80, method=6)
    else:
        im.save(buf, fmt.upper())
    return buf.getvalue(), FMT_EXT.get(fmt, "." + fmt)


# ── 单文件处理 ────────────────────────────────────────────────────────────────

@dataclass
class Result:
    path: str
    out_path: str | None
    orig: int
    new: int
    detail: str
    status: str  # ok | skipped | over | error


def out_path_for(path: str, ext: str, opts) -> str:
    d, base = os.path.split(path)
    stem = os.path.splitext(base)[0]
    if opts.inplace:                       # 原地：保留名字，扩展名随实际格式
        return os.path.join(d, stem + ext)
    return os.path.join(opts.out_dir or d, stem + opts.suffix + ext)


def process(path: str, opts) -> Result:
    try:
        orig = os.path.getsize(path)
        img = load_flat(path, opts.bg)
        if opts.max_dimension:
            img = cap_dimension(img, opts.max_dimension)

        req_fmt = EXT_FMT.get(os.path.splitext(path)[1].lower(), "jpeg") \
            if opts.inplace else resolve_format(path, opts.to)

        if opts.target:
            # 按最终格式精确逼近；无损压不到目标会自动转 jpg（见 compress_to_target）
            data, scale, q, _, feasible, fmt = compress_to_target(
                img, opts.target, opts.qmin, opts.qmax, req_fmt, opts.min_scale)
            tag = (f"scale={scale:.3g}" + (f" q={q}" if fmt in LOSSY_FMTS else "")
                   + ("" if feasible else " 未达标"))
        else:
            # 固定质量：转 JPEG 压缩再转回原格式
            fmt = req_fmt
            jpeg = encode_jpeg(img, opts.quality)
            data, _ = render_output(jpeg, fmt)
            # 只关心大小：无损格式压不过原图就退回 jpg
            if not opts.allow_larger and fmt not in LOSSY_FMTS and len(data) >= orig:
                data, fmt = jpeg, "jpeg"
            feasible = True
            tag = f"q={opts.quality}"

        ext = FMT_EXT.get(fmt, "." + fmt)
        if fmt != req_fmt:                       # 发生了 png/bmp→jpg 回退
            tag += f"  {req_fmt}->jpg"
        out = out_path_for(path, ext, opts)

        if not opts.allow_larger and len(data) >= orig:
            return Result(path, None, orig, len(data),
                          f"结果 {human(len(data))} ≥ 原图，跳过", "skipped")

        if opts.out_dir:
            os.makedirs(opts.out_dir, exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        if opts.inplace and os.path.abspath(out) != os.path.abspath(path) \
                and os.path.exists(path):
            os.remove(path)                      # 原地且换了扩展名：删掉旧文件
        ratio = 100 * (1 - len(data) / orig) if orig else 0
        status = "ok" if feasible else "over"
        return Result(path, out, orig, len(data),
                      f"{human(orig)} -> {human(len(data))} (-{ratio:.0f}%, {tag})", status)
    except Exception as e:  # noqa: BLE001
        return Result(path, None, 0, 0, f"错误: {e}", "error")


# ── 输入收集与主流程 ─────────────────────────────────────────────────────────

def collect_inputs(inputs: list[str], recursive: bool) -> list[str]:
    files: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            if recursive:
                for root, _, names in os.walk(item):
                    files += [os.path.join(root, n) for n in names
                              if os.path.splitext(n)[1].lower() in INPUT_EXTS]
            else:
                files += [os.path.join(item, n) for n in sorted(os.listdir(item))
                          if os.path.splitext(n)[1].lower() in INPUT_EXTS
                          and os.path.isfile(os.path.join(item, n))]
        elif os.path.isfile(item):
            files.append(item)
        else:
            print(f"警告：跳过不存在的路径 {item!r}", file=sys.stderr)
    # 去重保序
    seen, res = set(), []
    for f in files:
        a = os.path.abspath(f)
        if a not in seen:
            seen.add(a)
            res.append(f)
    return res


def run(args) -> int:
    files = collect_inputs(args.input, args.recursive)
    if not files:
        print("错误：没有可处理的图像", file=sys.stderr)
        return 2

    jobs = max(1, min(args.jobs or (os.cpu_count() or 1), len(files)))
    log = print if not args.quiet else (lambda *a, **k: None)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(lambda p: process(p, args), files))

    tot_in = tot_out = 0
    errs = 0
    for r in results:
        if r.status == "error":
            errs += 1
            print(f"  ✗ {r.path}: {r.detail}", file=sys.stderr)
            continue
        mark = {"ok": "✓", "over": "!", "skipped": "·"}[r.status]
        log(f"  {mark} {os.path.basename(r.path)}: {r.detail}"
            + (f" -> {r.out_path}" if r.out_path else ""))
        if r.status in ("ok", "over"):
            tot_in += r.orig
            tot_out += r.new

    n_ok = sum(r.status in ("ok", "over") for r in results)
    if tot_in:
        log(f"\n共处理 {n_ok}/{len(files)} 个文件："
            f"{human(tot_in)} -> {human(tot_out)} (-{100 * (1 - tot_out / tot_in):.0f}%)")
    return 1 if errs else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="通用图像有损压缩（任意格式 -> JPEG -> 转回原格式，以压小为目标）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input", nargs="+", help="图像文件 / 目录 / 通配符")
    ap.add_argument("--target", type=parse_size, default=None,
                    help="目标体积上限，如 80K / 500K（按最终格式精确逼近，<= 目标）")
    ap.add_argument("--quality", type=int, default=30,
                    help="无 --target 时的固定 JPEG 质量")
    ap.add_argument("--qmin", type=int, default=5, help="--target 搜索的最低质量（lossy 格式）")
    ap.add_argument("--qmax", type=int, default=90, help="--target 搜索的最高质量（lossy 格式）")
    ap.add_argument("--max-dimension", type=int, default=None, help="限制长边像素（只缩小）")
    ap.add_argument("--min-scale", type=float, default=0.2,
                    help="--target 搜索时允许的最小缩放比例")
    ap.add_argument("--to", default=None, metavar="FMT",
                    help="输出格式: original(默认,转回原格式) / jpg / png / webp / bmp / tiff")
    ap.add_argument("--bg", type=parse_color, default=(255, 255, 255),
                    metavar="COLOR", help="拍平 alpha 的背景色: white/black/#RRGGBB/R,G,B")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认与源文件同目录）")
    ap.add_argument("--suffix", default=".min", help="输出文件名后缀（非 --inplace 时）")
    ap.add_argument("--inplace", action="store_true",
                    help="原地替换源文件（忽略 --to；无损压不到目标会转 jpg 并删除原文件）")
    ap.add_argument("--allow-larger", action="store_true",
                    help="允许写出比原图更大的结果（默认跳过）")
    ap.add_argument("--recursive", action="store_true", help="递归处理目录")
    ap.add_argument("--jobs", type=int, default=None, help="并行线程数（默认 CPU 数）")
    ap.add_argument("--quiet", action="store_true", help="静默（只输出错误）")
    return ap


def main():
    args = build_parser().parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
