#!/usr/bin/env python3
"""
main.py — 把 PDF 全部光栅化为图像，并尽力压缩到指定目标体积。

适用场景：原 PDF 含大量矢量/字体/扫描图，常规压缩收效甚微时，整页转图像
再做 JPEG 压缩往往能稳定命中体积上限（代价：丢失文字层，不可再选中/搜索）。

整体策略：
  1. 用 PyMuPDF 把每页**只渲染一次**为高分辨率主图（master），后续所有
     分辨率档位都由该主图做 Lanczos 缩小得到，避免重复光栅化（核心提速点）。
  2. 在 (dpi, JPEG 质量) 二维空间里搜索“目标体积下的最佳画质”：
       - 外层：dpi 从高到低，定位“最低质量也能达标”的最高分辨率档位；
       - 内层：对 JPEG 质量做二分搜索，逼近目标体积。
     体积用 img2pdf 真实封装后**实测**，不再用经验公式估算。
  3. JPEG 编码在多页时用线程池并行（Pillow 编码会释放 GIL）。
  4. 用 img2pdf 无损封装 JPEG（不重编码，体积可精确控制）。

输入适应：
  * 多页、各页尺寸/方向不同都支持；
  * --page-size 可把所有页**统一**到同一物理尺寸（A4 / mode / max / 自定义），
    --fit 控制留白/裁切/拉伸，自动旋转使方向匹配，输出页面盒严格一致；
  * 默认 auto：保持每页原始几何（向后兼容旧行为，但 DPI 元数据更准确）。

用法:
  python main.py whitepaper.pdf --target 2M
  python main.py whitepaper.pdf --target 500K --page-size A4 --fit contain
  python main.py in.pdf --target 1M --grayscale --min-quality 50
  python main.py in.pdf --target 800K --pages 1-3,5 --max-dpi 220

依赖: pip install pymupdf img2pdf pillow
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF
import img2pdf
from PIL import Image

# ── 单位与解析 ────────────────────────────────────────────────────────────────

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)B?\s*$", re.IGNORECASE)
_MULT = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}

# 常见纸张尺寸（单位 pt = 1/72 inch），纵向（portrait）
NAMED_PT = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "B5": (498.90, 708.66),
    "LETTER": (612.0, 792.0),
    "LEGAL": (612.0, 1008.0),
}


def parse_size(s: str) -> int:
    """'2M' / '500K' / '1.5MB' / '123456' -> bytes"""
    m = _SIZE_RE.match(s)
    if not m:
        raise argparse.ArgumentTypeError(f"无法解析体积: {s!r}（示例 2M / 500K / 1.5MB）")
    return int(float(m.group(1)) * _MULT[m.group(2).upper()])


def human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def parse_pages(spec: str | None, n: int) -> list[int]:
    """'1-3,5' -> [0,1,2,4]（1 基，含端点）；空 -> 全部页。越界与重复自动剔除。"""
    if not spec:
        return list(range(n))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
            out.extend(range(a - 1, b))
        else:
            out.append(int(part) - 1)
    seen, res = set(), []
    for i in out:
        if 0 <= i < n and i not in seen:
            seen.add(i)
            res.append(i)
    return res


def parse_pagesize(spec: str):
    """返回 (kind, value)：
    kind ∈ {auto, mode, max, fixed}；fixed 时 value=(w_pt,h_pt)，其余 value=None。
    支持 A4/Letter… 命名，及 '210x297mm' / '595x842' / '8.5x11in'。"""
    s = spec.strip()
    low = s.lower()
    if low in ("auto", "native"):
        return "auto", None
    if low in ("mode", "common"):
        return "mode", None
    if low in ("max", "bbox", "bound"):
        return "max", None
    if s.upper() in NAMED_PT:
        return "fixed", NAMED_PT[s.upper()]
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*[x*]\s*([0-9]*\.?[0-9]+)\s*(mm|pt|in)?\s*$", s, re.I)
    if m:
        w, h, unit = float(m.group(1)), float(m.group(2)), (m.group(3) or "pt").lower()
        f = {"mm": 72 / 25.4, "in": 72.0, "pt": 1.0}[unit]
        return "fixed", (w * f, h * f)
    raise argparse.ArgumentTypeError(f"无法解析页面尺寸: {spec!r}")


def parse_color(s: str) -> tuple[int, int, int]:
    """'white' / 'black' / '#RRGGBB' / 'R,G,B' -> (r,g,b)。"""
    s = s.strip().lower()
    named = {"white": (255, 255, 255), "black": (0, 0, 0),
             "gray": (128, 128, 128), "grey": (128, 128, 128)}
    if s in named:
        return named[s]
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    if "," in s:
        parts = [int(x) for x in s.split(",")]
        if len(parts) == 3:
            return tuple(parts)  # type: ignore[return-value]
    raise argparse.ArgumentTypeError(f"无法解析颜色: {s!r}")


# ── 渲染与归一化 ──────────────────────────────────────────────────────────────

def resolve_canvas(kind: str, value, page_sizes: list[tuple[float, float]]):
    """根据策略确定统一画布的物理尺寸 (w_pt, h_pt)；auto 返回 None（不归一化）。"""
    if kind == "auto":
        return None
    if kind == "fixed":
        return value
    if kind == "mode":  # 取出现最多的尺寸（四舍五入到 0.1pt 聚类）
        c = Counter((round(w, 1), round(h, 1)) for w, h in page_sizes)
        return c.most_common(1)[0][0]
    if kind == "max":  # 取能容纳所有页的外接尺寸
        return max(w for w, _ in page_sizes), max(h for _, h in page_sizes)
    return None


def render_master(page: fitz.Page, dpi: int, grayscale: bool) -> Image.Image:
    """把一页渲染成 PIL 主图（最高工作分辨率，全程只渲染一次）。"""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    cs = fitz.csGRAY if grayscale else fitz.csRGB
    pix = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
    mode = "L" if grayscale else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def fit_to_canvas(img: Image.Image, canvas_px: tuple[int, int], fit: str,
                  pad: tuple[int, int, int], auto_orient: bool) -> Image.Image:
    """把图像放入固定像素画布。fit ∈ {contain, cover, stretch}。"""
    cw, ch = canvas_px
    if auto_orient:  # 方向不一致则旋转 90°，最小化留白
        iw, ih = img.size
        if iw != ih and cw != ch and (iw > ih) != (cw > ch):
            img = img.rotate(90, expand=True)

    if fit == "stretch":
        return img.resize((cw, ch), Image.LANCZOS)

    iw, ih = img.size
    scale = (max if fit == "cover" else min)(cw / iw, ch / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)

    fill = pad[0] if img.mode == "L" else pad
    canvas = Image.new(img.mode, (cw, ch), fill)
    canvas.paste(resized, ((cw - nw) // 2, (ch - nh) // 2))  # cover 时多余部分被裁掉
    return canvas


def build_masters(doc, page_idx, max_dpi, grayscale, canvas_pt, fit, pad,
                  auto_orient, log) -> list[Image.Image]:
    """渲染所有目标页为最高分辨率主图，并按需归一化到统一画布。"""
    masters = []
    canvas_px = None
    if canvas_pt:
        cw, ch = canvas_pt
        canvas_px = (round(cw / 72 * max_dpi), round(ch / 72 * max_dpi))
    for n, i in enumerate(page_idx, 1):
        img = render_master(doc[i], max_dpi, grayscale)
        if canvas_px:
            img = fit_to_canvas(img, canvas_px, fit, pad, auto_orient)
        masters.append(img)
        log(f"  渲染 {n}/{len(page_idx)} (page {i + 1}) -> {img.size[0]}x{img.size[1]}px")
    return masters


# ── 编码与封装 ────────────────────────────────────────────────────────────────

def encode(img: Image.Image, q: int, dpi: int, optimize: bool) -> bytes:
    """编码为 baseline JPEG，写入 dpi 元数据以确定 PDF 页面物理尺寸。"""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q, optimize=optimize,
             progressive=False, dpi=(dpi, dpi))
    return buf.getvalue()


def encode_all(pool, imgs, q, dpi, optimize=False) -> list[bytes]:
    return list(pool.map(lambda im: encode(im, q, dpi, optimize), imgs))


def build_pdf(jpegs: list[bytes]) -> bytes:
    """img2pdf 无损封装 JPEG（不重编码）。dpi 元数据已带，页面盒由此确定。"""
    return img2pdf.convert(jpegs)


# ── 搜索：在 (dpi, 质量) 空间里找“达标的最佳画质” ─────────────────────────────

def search(masters, max_dpi, ladder, target, qmin, qmax, min_quality, pool, log):
    """返回 (pdf_bytes, size, dpi, q, feasible)。"""

    def imgs_at(dpi):
        scale = dpi / max_dpi
        if scale >= 0.999:
            return masters
        return [m.resize((max(1, round(m.width * scale)),
                          max(1, round(m.height * scale))), Image.LANCZOS)
                for m in masters]

    best_infeasible = None  # (size, dpi, q, imgs) 全档位都超标时的兜底（取最小）
    last_feasible = None    # (dpi, q, imgs) 已达标项（供 min-quality 继续降档）

    for dpi in ladder:
        imgs = imgs_at(dpi)

        # 1) 该档最低质量是否达标？不达标说明此分辨率太大，降档
        jl = encode_all(pool, imgs, qmin, dpi)
        sl = len(build_pdf(jl))
        if sl > target:
            log(f"[dpi {dpi}] 最低质量 q={qmin} 仍 {human(sl)} > 目标，降档")
            if best_infeasible is None or sl < best_infeasible[0]:
                best_infeasible = (sl, dpi, qmin, imgs)
            continue

        # 2) 该档最高质量是否达标？达标直接收工（最高分辨率 + 最高画质）
        jh = encode_all(pool, imgs, qmax, dpi)
        sh = len(build_pdf(jh))
        if sh <= target:
            q = qmax
            log(f"[dpi {dpi}] 最高质量 q={qmax} 即达标 {human(sh)}")
        else:
            # 3) 二分搜索：找 <= target 的最高质量
            lo, hi, q = qmin, qmax, qmin
            while lo <= hi:
                mid = (lo + hi) // 2
                s = len(build_pdf(encode_all(pool, imgs, mid, dpi)))
                log(f"[dpi {dpi}] 试 q={mid}: {human(s)}")
                if s <= target:
                    q = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

        last_feasible = (dpi, q, imgs)
        # min-quality：若该档质量过低且还能降 dpi，则牺牲分辨率换更高画质
        if min_quality and q < min_quality and dpi != ladder[-1]:
            log(f"[dpi {dpi}] 命中质量 q={q} < 期望 {min_quality}，继续降档以提画质")
            continue

        return finalize(pool, imgs, q, dpi) + (True,)

    if last_feasible:  # min-quality 未满足，用最低档（画质预算最大）的达标结果
        dpi, q, imgs = last_feasible
        return finalize(pool, imgs, q, dpi) + (True,)

    # 全部超标：输出能做到的最小文件
    _, dpi, q, imgs = best_infeasible
    return finalize(pool, imgs, q, dpi) + (False,)


def finalize(pool, imgs, q, dpi):
    """以 optimize=True 重新编码选定方案（体积更小，结果 <= 搜索时实测值）。"""
    jpegs = encode_all(pool, imgs, q, dpi, optimize=True)
    pdf = build_pdf(jpegs)
    return pdf, len(pdf), dpi, q


# ── 主流程 ────────────────────────────────────────────────────────────────────

def build_ladder(max_dpi: int, min_dpi: int, step: float) -> list[int]:
    """dpi 阶梯：max -> min 按等比 step 下降，去重且严格递减。"""
    ladder, d = [], float(max_dpi)
    while d > min_dpi:
        v = int(round(d))
        if not ladder or v < ladder[-1]:
            ladder.append(v)
        d *= step
    if not ladder or ladder[-1] != min_dpi:
        ladder.append(min_dpi)
    return ladder


def run(args) -> int:
    if not os.path.isfile(args.input):
        print(f"错误：找不到输入文件 {args.input!r}", file=sys.stderr)
        return 2

    try:
        doc = fitz.open(args.input)
    except Exception as e:  # noqa: BLE001
        print(f"错误：无法打开 PDF：{e}", file=sys.stderr)
        return 2

    if doc.is_encrypted and not doc.authenticate(args.password or ""):
        print("错误：PDF 已加密，请用 --password 提供口令", file=sys.stderr)
        return 2

    if doc.page_count == 0:
        print("错误：PDF 没有页面", file=sys.stderr)
        return 2

    page_idx = parse_pages(args.pages, doc.page_count)
    if not page_idx:
        print(f"错误：--pages {args.pages!r} 未选中任何页", file=sys.stderr)
        return 2

    log = print if not args.quiet else (lambda *a, **k: None)
    target = args.target
    out = args.output or (os.path.splitext(args.input)[0] + ".squeezed.pdf")
    if os.path.abspath(out) == os.path.abspath(args.input):
        print("错误：输出会覆盖输入文件，请用 -o 指定其它路径", file=sys.stderr)
        return 2

    # 解析归一化画布
    kind, value = args.page_size
    page_sizes = [(doc[i].rect.width, doc[i].rect.height) for i in page_idx]
    canvas_pt = resolve_canvas(kind, value, page_sizes)
    if canvas_pt:
        log(f"统一页面尺寸 -> {canvas_pt[0]:.0f}x{canvas_pt[1]:.0f}pt "
            f"(fit={args.fit}, auto-orient={'on' if not args.no_auto_orient else 'off'})")

    ladder = build_ladder(args.max_dpi, args.min_dpi, args.dpi_step)
    log(f"目标 {human(target)}，{len(page_idx)} 页，dpi 阶梯 {ladder}")

    log("渲染主图（最高分辨率，仅一次）：")
    masters = build_masters(doc, page_idx, args.max_dpi, args.grayscale,
                            canvas_pt, args.fit, args.pad_color,
                            not args.no_auto_orient, log)

    jobs = max(1, min(args.jobs or (os.cpu_count() or 1), len(masters)))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pdf, size, dpi, q, feasible = search(
            masters, args.max_dpi, ladder, target,
            args.qmin, args.qmax, args.min_quality, pool, log)

    with open(out, "wb") as f:
        f.write(pdf)

    if feasible:
        log(f"\n✓ 输出: {out}  {human(size)}  (目标 {human(target)}, dpi={dpi}, 质量={q})")
        return 0
    print(f"\n[警告] 无法压到 {human(target)}，已输出最小可得结果 {human(size)} "
          f"(dpi={dpi}, q={q})。可尝试 --grayscale 或更低 --min-dpi。", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="光栅化 PDF 并压缩到目标体积（支持多页、统一页面尺寸）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input", help="输入 PDF 路径")
    ap.add_argument("-o", "--output", default=None, help="输出路径（默认 *.squeezed.pdf）")
    ap.add_argument("--target", required=True, type=parse_size,
                    help="目标体积，如 2M / 500K / 1.5MB")
    ap.add_argument("--max-dpi", type=int, default=200, help="最高渲染分辨率")
    ap.add_argument("--min-dpi", type=int, default=72, help="最低渲染分辨率")
    ap.add_argument("--dpi-step", type=float, default=0.75, help="dpi 阶梯等比系数")
    ap.add_argument("--qmin", type=int, default=15, help="JPEG 最低质量")
    ap.add_argument("--qmax", type=int, default=90, help="JPEG 最高质量")
    ap.add_argument("--min-quality", type=int, default=0,
                    help="期望最低 JPEG 质量；为达到则不惜降低 dpi（0=关闭）")
    ap.add_argument("--grayscale", action="store_true", help="转灰度，体积更小")
    ap.add_argument("--page-size", type=parse_pagesize, default=("auto", None),
                    metavar="SPEC",
                    help="统一页面尺寸: auto(默认,保持原样) / mode / max / "
                         "A4|Letter|A3|A5|Legal / 210x297mm / 595x842 / 8.5x11in")
    ap.add_argument("--fit", choices=("contain", "cover", "stretch"), default="contain",
                    help="归一化时的适配方式（留白/裁切/拉伸）")
    ap.add_argument("--pad-color", type=parse_color, default=(255, 255, 255),
                    metavar="COLOR", help="contain 留白颜色: white/black/#RRGGBB/R,G,B")
    ap.add_argument("--no-auto-orient", action="store_true",
                    help="归一化时不自动旋转页面以匹配方向")
    ap.add_argument("--pages", default=None, help="页面范围，如 1-3,5（1 基，含端点）")
    ap.add_argument("--password", default=None, help="加密 PDF 的口令")
    ap.add_argument("--jobs", type=int, default=None, help="并行编码线程数（默认 CPU 数）")
    ap.add_argument("--quiet", action="store_true", help="静默（只输出错误/警告）")
    return ap


def main():
    args = build_parser().parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
