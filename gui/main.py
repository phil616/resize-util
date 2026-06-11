#!/usr/bin/env python3
"""
gui/main.py — 把 pdf-resize 与 image-resize 两个压缩工具集成到一个 tkinter 界面。

实现方式：运行时按路径加载两个子项目的 main.py 作为模块，直接复用它们的
build_parser()（做参数校验/类型转换）与 run()（核心逻辑），不重复实现压缩。
任务在后台线程执行，子工具的 print 进度通过队列实时回显到日志区。

运行: uv run python main.py   （需要图形环境 / DISPLAY）
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import queue
import sys
import threading

# tkinter 延迟到真正打开界面时再导入：无界面自检 / CI smoke test 不依赖图形库，
# 打包后的 EXE 即便缺图形环境也能跑 RESIZE_SELFTEST。
tk = ttk = filedialog = messagebox = None  # type: ignore[assignment]


def _import_tk():
    """真正打开 GUI 时才导入 tkinter，并填入模块全局名。"""
    global tk, ttk, filedialog, messagebox
    # 打包态若内置了 Tcl/Tk 9.0 的脚本目录（见 spec），指给 Tcl 以便找到 init.tcl
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        for var, sub in (("TCL_LIBRARY", "tcl9.0"), ("TK_LIBRARY", "tk9.0")):
            p = os.path.join(base, sub)
            if os.path.isdir(p) and not os.environ.get(var):
                os.environ[var] = p
    import tkinter as _tk
    from tkinter import filedialog as _fd, messagebox as _mb, ttk as _ttk
    tk, ttk, filedialog, messagebox = _tk, _ttk, _fd, _mb


def _base_dir() -> str:
    """两个子工具源码所在的基准目录。
    - 开发态：仓库根（gui/ 的上一级）。
    - PyInstaller 单文件态：运行时解压目录 sys._MEIPASS（spec 里把
      pdf-resize/main.py、image-resize/main.py 作为数据打了进去）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _base_dir()


def _load(name: str, subdir: str):
    """按文件路径加载子项目的 main.py，避免两个同名 main 模块冲突。"""
    path = os.path.join(ROOT, subdir, "main.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # @dataclass 等需要模块已在 sys.modules 中
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


pdf_mod = _load("pdf_resize_main", "pdf-resize")
img_mod = _load("image_resize_main", "image-resize")


class _QueueWriter:
    """把 print 输出塞进队列，供主线程回显到日志区。"""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str) -> int:
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.running = False
        self.q: queue.Queue = queue.Queue()
        self.img_inputs: list[str] = []
        self.run_buttons: list[ttk.Button] = []

        root.title("Resize 工具箱 — PDF / 图像 压缩")
        root.geometry("760x680")
        root.minsize(680, 560)

        nb = ttk.Notebook(root)
        nb.pack(fill="x", padx=10, pady=(10, 6))
        self._build_pdf_tab(nb)
        self._build_image_tab(nb)

        # 日志区
        logf = ttk.LabelFrame(root, text="运行日志")
        logf.pack(fill="both", expand=True, padx=10, pady=6)
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled",
                           font=("monospace", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # 状态栏 + 进度条
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.status = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right")

    # ── 控件辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row(parent, r):
        f = ttk.Frame(parent)
        f.grid(row=r, column=0, sticky="ew", pady=3)
        parent.columnconfigure(0, weight=1)
        return f

    @staticmethod
    def _labeled(parent, text, width=10):
        ttk.Label(parent, text=text, width=width, anchor="e").pack(side="left", padx=(0, 6))

    # ── PDF 选项卡 ────────────────────────────────────────────────────────────

    def _build_pdf_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="PDF 压缩")

        self.pdf_input = tk.StringVar()
        self.pdf_output = tk.StringVar()
        self.pdf_target = tk.StringVar(value="2M")
        self.pdf_pagesize = tk.StringVar(value="auto")
        self.pdf_fit = tk.StringVar(value="contain")
        self.pdf_maxdpi = tk.StringVar(value="200")
        self.pdf_mindpi = tk.StringVar(value="72")
        self.pdf_minq = tk.StringVar(value="0")
        self.pdf_gray = tk.BooleanVar(value=False)
        self.pdf_pages = tk.StringVar()
        self.pdf_pwd = tk.StringVar()

        r = self._row(tab, 0)
        self._labeled(r, "输入 PDF")
        ttk.Entry(r, textvariable=self.pdf_input).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="浏览…", command=self._pick_pdf_in).pack(side="left", padx=4)

        r = self._row(tab, 1)
        self._labeled(r, "输出(可选)")
        ttk.Entry(r, textvariable=self.pdf_output).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="浏览…", command=self._pick_pdf_out).pack(side="left", padx=4)

        r = self._row(tab, 2)
        self._labeled(r, "目标体积")
        ttk.Entry(r, textvariable=self.pdf_target, width=12).pack(side="left")
        ttk.Label(r, text="如 2M / 500K").pack(side="left", padx=8)

        r = self._row(tab, 3)
        self._labeled(r, "页面尺寸")
        ttk.Combobox(r, textvariable=self.pdf_pagesize, width=14,
                     values=["auto", "mode", "max", "A4", "Letter", "A3", "A5", "Legal",
                             "210x297mm", "595x842"]).pack(side="left")
        ttk.Label(r, text="适配").pack(side="left", padx=(16, 4))
        ttk.Combobox(r, textvariable=self.pdf_fit, width=10, state="readonly",
                     values=["contain", "cover", "stretch"]).pack(side="left")

        r = self._row(tab, 4)
        self._labeled(r, "最大 DPI")
        ttk.Entry(r, textvariable=self.pdf_maxdpi, width=7).pack(side="left")
        ttk.Label(r, text="最小 DPI").pack(side="left", padx=(16, 4))
        ttk.Entry(r, textvariable=self.pdf_mindpi, width=7).pack(side="left")
        ttk.Label(r, text="最低质量").pack(side="left", padx=(16, 4))
        ttk.Entry(r, textvariable=self.pdf_minq, width=7).pack(side="left")
        ttk.Checkbutton(r, text="灰度", variable=self.pdf_gray).pack(side="left", padx=16)

        r = self._row(tab, 5)
        self._labeled(r, "页面范围")
        ttk.Entry(r, textvariable=self.pdf_pages, width=14).pack(side="left")
        ttk.Label(r, text="如 1-3,5").pack(side="left", padx=(8, 16))
        ttk.Label(r, text="口令").pack(side="left", padx=(0, 4))
        ttk.Entry(r, textvariable=self.pdf_pwd, width=14, show="•").pack(side="left")

        r = self._row(tab, 6)
        b = ttk.Button(r, text="开始压缩 PDF", command=self._start_pdf)
        b.pack(side="right")
        self.run_buttons.append(b)

    def _pick_pdf_in(self):
        p = filedialog.askopenfilename(title="选择 PDF",
                                       filetypes=[("PDF", "*.pdf"), ("全部", "*.*")])
        if p:
            self.pdf_input.set(p)

    def _pick_pdf_out(self):
        p = filedialog.asksaveasfilename(title="输出 PDF", defaultextension=".pdf",
                                         filetypes=[("PDF", "*.pdf")])
        if p:
            self.pdf_output.set(p)

    def _start_pdf(self):
        inp = self.pdf_input.get().strip()
        if not inp:
            messagebox.showwarning("缺少输入", "请选择输入 PDF")
            return
        if not self.pdf_target.get().strip():
            messagebox.showwarning("缺少目标", "请填写目标体积，如 2M")
            return
        argv = [inp, "--target", self.pdf_target.get().strip(),
                "--page-size", self.pdf_pagesize.get().strip() or "auto",
                "--fit", self.pdf_fit.get(),
                "--max-dpi", self.pdf_maxdpi.get().strip() or "200",
                "--min-dpi", self.pdf_mindpi.get().strip() or "72"]
        if self.pdf_output.get().strip():
            argv += ["-o", self.pdf_output.get().strip()]
        if (self.pdf_minq.get().strip() or "0") != "0":
            argv += ["--min-quality", self.pdf_minq.get().strip()]
        if self.pdf_gray.get():
            argv.append("--grayscale")
        if self.pdf_pages.get().strip():
            argv += ["--pages", self.pdf_pages.get().strip()]
        if self.pdf_pwd.get():
            argv += ["--password", self.pdf_pwd.get()]
        self._run_job(pdf_mod, argv, "压缩 PDF")

    # ── 图像选项卡 ────────────────────────────────────────────────────────────

    def _build_image_tab(self, nb):
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="图像压缩")

        self.img_mode = tk.StringVar(value="quality")  # quality | target
        self.img_quality = tk.StringVar(value="30")
        self.img_target = tk.StringVar(value="200K")
        self.img_to = tk.StringVar(value="original")
        self.img_maxdim = tk.StringVar()
        self.img_outdir = tk.StringVar()
        self.img_suffix = tk.StringVar(value=".min")
        self.img_recursive = tk.BooleanVar(value=False)
        self.img_inplace = tk.BooleanVar(value=False)
        self.img_allow_larger = tk.BooleanVar(value=False)

        # 输入列表
        r = self._row(tab, 0)
        self._labeled(r, "输入图像", 10)
        box = ttk.Frame(r)
        box.pack(side="left", fill="x", expand=True)
        self.img_list = tk.Listbox(box, height=5)
        lsb = ttk.Scrollbar(box, command=self.img_list.yview)
        self.img_list.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.img_list.pack(side="left", fill="both", expand=True)
        btns = ttk.Frame(r)
        btns.pack(side="left", padx=4, fill="y")
        for t, c in (("添加文件", self._img_add_files), ("添加目录", self._img_add_dir),
                     ("移除", self._img_remove), ("清空", self._img_clear)):
            ttk.Button(btns, text=t, width=9, command=c).pack(pady=1)

        # 压缩模式
        r = self._row(tab, 1)
        self._labeled(r, "模式")
        ttk.Radiobutton(r, text="固定质量", variable=self.img_mode, value="quality").pack(side="left")
        ttk.Entry(r, textvariable=self.img_quality, width=6).pack(side="left", padx=(2, 16))
        ttk.Radiobutton(r, text="目标体积", variable=self.img_mode, value="target").pack(side="left")
        ttk.Entry(r, textvariable=self.img_target, width=8).pack(side="left", padx=2)
        ttk.Label(r, text="(质量0-95 / 体积如 80K)").pack(side="left", padx=8)

        r = self._row(tab, 2)
        self._labeled(r, "输出格式")
        ttk.Combobox(r, textvariable=self.img_to, width=10, state="readonly",
                     values=["original", "jpg", "png", "webp", "bmp", "tiff"]).pack(side="left")
        ttk.Label(r, text="长边上限").pack(side="left", padx=(16, 4))
        ttk.Entry(r, textvariable=self.img_maxdim, width=8).pack(side="left")
        ttk.Label(r, text="px(可空)").pack(side="left", padx=4)

        r = self._row(tab, 3)
        self._labeled(r, "输出目录")
        ttk.Entry(r, textvariable=self.img_outdir).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="浏览…", command=self._img_pick_outdir).pack(side="left", padx=4)
        ttk.Label(r, text="后缀").pack(side="left", padx=(8, 2))
        ttk.Entry(r, textvariable=self.img_suffix, width=8).pack(side="left")

        r = self._row(tab, 4)
        self._labeled(r, "")
        ttk.Checkbutton(r, text="递归目录", variable=self.img_recursive).pack(side="left")
        ttk.Checkbutton(r, text="原地覆盖", variable=self.img_inplace).pack(side="left", padx=16)
        ttk.Checkbutton(r, text="允许变大", variable=self.img_allow_larger).pack(side="left")

        r = self._row(tab, 5)
        b = ttk.Button(r, text="开始压缩 图像", command=self._start_image)
        b.pack(side="right")
        self.run_buttons.append(b)

    def _img_add_files(self):
        ps = filedialog.askopenfilenames(
            title="选择图像",
            filetypes=[("图像", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff"),
                       ("全部", "*.*")])
        for p in ps:
            if p not in self.img_inputs:
                self.img_inputs.append(p)
        self._img_refresh()

    def _img_add_dir(self):
        d = filedialog.askdirectory(title="选择目录")
        if d and d not in self.img_inputs:
            self.img_inputs.append(d)
            self._img_refresh()

    def _img_remove(self):
        for i in reversed(self.img_list.curselection()):
            del self.img_inputs[i]
        self._img_refresh()

    def _img_clear(self):
        self.img_inputs.clear()
        self._img_refresh()

    def _img_refresh(self):
        self.img_list.delete(0, "end")
        for p in self.img_inputs:
            tag = "📁 " if os.path.isdir(p) else ""
            self.img_list.insert("end", tag + p)

    def _img_pick_outdir(self):
        d = filedialog.askdirectory(title="输出目录")
        if d:
            self.img_outdir.set(d)

    def _start_image(self):
        if not self.img_inputs:
            messagebox.showwarning("缺少输入", "请先添加图像文件或目录")
            return
        argv = list(self.img_inputs)
        if self.img_mode.get() == "target":
            if not self.img_target.get().strip():
                messagebox.showwarning("缺少目标", "请填写目标体积，如 80K")
                return
            argv += ["--target", self.img_target.get().strip()]
        else:
            argv += ["--quality", self.img_quality.get().strip() or "30"]
        if self.img_to.get() != "original":
            argv += ["--to", self.img_to.get()]
        if self.img_maxdim.get().strip():
            argv += ["--max-dimension", self.img_maxdim.get().strip()]
        if self.img_outdir.get().strip():
            argv += ["--out-dir", self.img_outdir.get().strip()]
        if self.img_suffix.get().strip() and self.img_suffix.get() != ".min":
            argv += ["--suffix", self.img_suffix.get()]
        if self.img_recursive.get():
            argv.append("--recursive")
        if self.img_inplace.get():
            argv.append("--inplace")
        if self.img_allow_larger.get():
            argv.append("--allow-larger")
        self._run_job(img_mod, argv, "压缩图像")

    # ── 后台执行与日志 ────────────────────────────────────────────────────────

    def _run_job(self, mod, argv, desc):
        if self.running:
            messagebox.showinfo("请稍候", "已有任务在运行")
            return
        self.running = True
        for b in self.run_buttons:
            b.configure(state="disabled")
        self.progress.start(12)
        self.status.set(f"正在{desc} …")
        self._append(f"\n===== {desc} =====\n$ {' '.join(argv)}\n")
        self.q = queue.Queue()

        def worker():
            code = None
            w = _QueueWriter(self.q)
            try:
                with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                    args = mod.build_parser().parse_args(argv)
                    code = mod.run(args)
            except SystemExit as e:  # argparse 校验失败
                self.q.put(f"\n[参数错误] 退出码 {e.code}\n")
            except BaseException as e:  # noqa: BLE001
                self.q.put(f"\n[错误] {type(e).__name__}: {e}\n")
            self.q.put(("__DONE__", code))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(60, self._poll)

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__DONE__":
                    self._job_done(item[1])
                    return
                self._append(item)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _job_done(self, code):
        self.running = False
        self.progress.stop()
        for b in self.run_buttons:
            b.configure(state="normal")
        msg = {0: "完成 ✓", 1: "完成（有警告 / 未完全达标）", 2: "失败",
               None: "结束"}.get(code, "结束")
        self.status.set(msg)
        self._append(f"----- {msg} (code={code}) -----\n")

    def _append(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")


def _selftest_emit(msg: str) -> None:
    """把自检结果同时打到 stdout 和 RESIZE_SELFTEST_LOG 指向的文件。
    打包成 windowed EXE 后没有 stdout，文件是 CI 能看到结果的唯一途径。"""
    try:
        print(msg)
    except Exception:  # noqa: BLE001
        pass
    logf = os.environ.get("RESIZE_SELFTEST_LOG")
    if logf:
        try:
            with open(logf, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:  # noqa: BLE001
            pass


def _selftest() -> int:
    """无界面冒烟自检：跑通三大重依赖（Pillow / img2pdf / PyMuPDF）与子工具的
    动态加载。验证打包后的 EXE（设 RESIZE_SELFTEST=1 触发），也用作 CI smoke test。

    关键：全程 try/except，绝不让异常向外抛 —— 否则 windowed EXE 会弹出一个
    模态的 traceback 对话框并永久阻塞（在 CI 里无人点击，任务会一直挂着）。"""
    import tempfile
    import traceback
    try:
        import fitz
        from PIL import Image

        d = tempfile.mkdtemp()
        ip = os.path.join(d, "t.png")
        Image.new("RGB", (80, 80), (20, 180, 90)).save(ip)
        ic = img_mod.run(img_mod.build_parser().parse_args([ip, "--to", "jpg"]))

        pp, op = os.path.join(d, "t.pdf"), os.path.join(d, "t.out.pdf")
        doc = fitz.open()
        doc.new_page(width=300, height=400).insert_text((50, 50), "hello")
        doc.save(pp)
        doc.close()
        pc = pdf_mod.run(pdf_mod.build_parser().parse_args(
            [pp, "--target", "100K", "-o", op, "--quiet"]))

        ok = ic == 0 and pc in (0, 1) and os.path.exists(op)
        _selftest_emit(
            f"SELFTEST image_code={ic} pdf_code={pc} "
            f"pdf_out={os.path.getsize(op) if os.path.exists(op) else 'MISSING'} "
            f"-> {'OK' if ok else 'FAIL'}")
        return 0 if ok else 1
    except BaseException:  # noqa: BLE001  绝不外抛，避免 windowed 模态弹窗
        _selftest_emit("SELFTEST CRASH:\n" + traceback.format_exc())
        return 3


def main():
    # 打包成无控制台（windowed）EXE 后，sys.stdout/stderr 可能为 None，
    # 子工具或自检里的 print 会直接崩溃；兜底重定向到 devnull。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if os.environ.get("RESIZE_SELFTEST"):
        sys.exit(_selftest())
    _import_tk()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
