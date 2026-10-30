"""
gui —— ncmdump GUI 主界面（tkinter，深色主题）

支持：单文件 / 多文件 / 整个文件夹批量，可选 FLAC / MP3 输出。
"""

import os
import queue
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from converter import convert_one
from ncmcore import NCMFile, NCMError
from transcoder import FLAC_LEVELS, MP3_BITRATES, find_ffmpeg

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:  # pragma: no cover
    DND_FILES = None
    TkinterDnD = None
    _HAS_DND = False


# --------------------------------------------------------------------------
# 主题
# --------------------------------------------------------------------------

BG = "#1c1c1c"
PANEL = "#242426"
FIELD = "#2d2d30"
BORDER = "#3a3a3d"
FG = "#e8e8e8"
MUTED = "#9b9b9b"
ACCENT = "#c62f2f"
OK = "#4ec9b0"
WARN = "#d7a94b"
ERR = "#f48771"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0


class App:
    def __init__(self, root):
        self.root = root
        self.items = {}          # tree id -> dict(path, status, ...)
        self._msgq = queue.Queue()
        self._stop = threading.Event()
        self._running = False
        self._ffmpeg = find_ffmpeg()

        root.title("ncmdump 图形版 —— 网易云音乐 .ncm 转换工具")
        root.geometry("980x700")
        root.minsize(860, 620)
        root.configure(bg=BG)

        self._setup_style()
        self._build_ui()

        if not self._ffmpeg:
            self._log("警告：未找到 ffmpeg.exe，跨格式转换不可用（同格式提取不受影响）。", WARN)

        self._poll()

    # -- 样式 ---------------------------------------------------------------

    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass

        st.configure(".", background=BG, foreground=FG,
                     fieldbackground=FIELD, font=(FONT, 9))
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL, relief="flat")
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Panel.TLabel", background=PANEL, foreground=FG)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        st.configure("TButton", background=FIELD, foreground=FG,
                     bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD,
                     padding=(10, 5))
        st.map("TButton",
               background=[("active", "#3a3a3d"), ("disabled", "#2a2a2c")],
               foreground=[("disabled", "#6a6a6a")])
        st.configure("Go.TButton", background=ACCENT, foreground="#ffffff",
                     bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                     padding=(16, 7), font=(FONT, 10, "bold"))
        st.map("Go.TButton",
               background=[("active", "#d8403a"), ("disabled", "#5a3a3a")],
               foreground=[("disabled", "#a08a8a")])
        st.configure("TCheckbutton", background=PANEL, foreground=FG,
                     indicatorcolor=FIELD, indicatorbackground=FIELD)
        st.map("TCheckbutton",
               indicatorcolor=[("selected", ACCENT)],
               background=[("active", PANEL)])
        st.configure("TRadiobutton", background=PANEL, foreground=FG,
                     indicatorcolor=FIELD)
        st.map("TRadiobutton",
               indicatorcolor=[("selected", ACCENT)],
               background=[("active", PANEL)])
        st.configure("TCombobox", background=FIELD, foreground=FG,
                     fieldbackground=FIELD, arrowcolor=FG,
                     selectbackground=FIELD, selectforeground=FG,
                     bordercolor=BORDER, padding=(5, 3))
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)],
               selectbackground=[("readonly", FIELD)])
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                     bordercolor=BORDER, padding=(5, 4))
        st.configure("TSpinbox", fieldbackground=FIELD, foreground=FG,
                     bordercolor=BORDER, arrowcolor=FG, padding=(4, 3))
        st.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                     bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("Treeview", background="#1a1a1c", foreground=FG,
                     fieldbackground="#1a1a1c", bordercolor=BORDER,
                     rowheight=26, font=(FONT, 9))
        st.configure("Treeview.Heading", background="#2d2d30",
                     foreground=FG, relief="flat", font=(FONT, 9, "bold"))
        st.map("Treeview",
               background=[("selected", "#343a45")],
               foreground=[("selected", FG)])
        st.configure("Vertical.TScrollbar", background=FIELD,
                     troughcolor=BG, bordercolor=BORDER, arrowcolor=FG)
        st.configure("TLabelframe", background=BG, bordercolor=BORDER)
        st.configure("TLabelframe.Label", background=BG, foreground=FG,
                     font=(FONT, 9, "bold"))

    # -- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        root = self.root

        top = ttk.Frame(root, padding=(14, 12, 14, 6))
        top.pack(fill="x")

        ttk.Label(top, text="ncmdump 图形版", font=(FONT, 15, "bold")).pack(side="left")
        ttk.Label(top, text="  网易云 .ncm → FLAC / MP3",
                  foreground=MUTED).pack(side="left", padx=(2, 0))
        self.lbl_ffmpeg = ttk.Label(top, foreground=MUTED, font=(FONT, 8))
        self.lbl_ffmpeg.pack(side="right")
        self._refresh_ffmpeg_label()

        # 文件列表
        listbox_frame = ttk.Frame(root, padding=(14, 4, 14, 4))
        listbox_frame.pack(fill="both", expand=True)

        cols = ("name", "fmt", "kbps", "dur", "status")
        tv = ttk.Treeview(listbox_frame, columns=cols, show="headings",
                          selectmode="extended")
        tv.heading("name", text="文件名")
        tv.heading("fmt", text="源格式")
        tv.heading("kbps", text="码率")
        tv.heading("dur", text="时长")
        tv.heading("status", text="状态")
        tv.column("name", width=430, minwidth=200, stretch=True)
        tv.column("fmt", width=70, anchor="center", stretch=False)
        tv.column("kbps", width=80, anchor="center", stretch=False)
        tv.column("dur", width=70, anchor="center", stretch=False)
        tv.column("status", width=210, stretch=False)
        tv.tag_configure("ok", foreground=OK)
        tv.tag_configure("err", foreground=ERR)
        tv.tag_configure("run", foreground=WARN)
        tv.tag_configure("skip", foreground=MUTED)

        vs = ttk.Scrollbar(listbox_frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        listbox_frame.rowconfigure(0, weight=1)
        listbox_frame.columnconfigure(0, weight=1)
        self.tv = tv

        if _HAS_DND:
            tv.drop_target_register(DND_FILES)
            tv.dnd_bind("<<Drop>>", self._on_drop)

        # 工具栏
        bar = ttk.Frame(root, padding=(14, 2, 14, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="添加文件", command=self._add_files).pack(side="left")
        ttk.Button(bar, text="添加文件夹", command=self._add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="移除选中", command=self._remove_selected).pack(side="left")
        ttk.Button(bar, text="清空", command=self._clear).pack(side="left", padx=6)
        ttk.Label(bar, text="（也可以直接把文件拖进列表）",
                  foreground=MUTED, font=(FONT, 8)).pack(side="left", padx=8)
        self.lbl_count = ttk.Label(bar, foreground=MUTED)
        self.lbl_count.pack(side="right")

        # 设置区
        cfg = ttk.Labelframe(root, text=" 转换设置 ", padding=(12, 8))
        cfg.pack(fill="x", padx=14, pady=(0, 8))

        self.target = tk.StringVar(value="flac")
        row1 = ttk.Frame(cfg, style="Panel.TFrame")
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="输出格式", style="Panel.TLabel").pack(side="left")
        for text, val in (("FLAC（无损）", "flac"), ("MP3", "mp3"),
                          ("与源文件相同", "auto")):
            ttk.Radiobutton(row1, text=text, value=val, variable=self.target,
                            command=self._sync_options).pack(side="left", padx=(12, 0))

        row2 = ttk.Frame(cfg, style="Panel.TFrame")
        row2.pack(fill="x", pady=(0, 6))

        ttk.Label(row2, text="MP3 码率", style="Panel.TLabel").pack(side="left")
        self.mp3_br = ttk.Combobox(row2, values=MP3_BITRATES, width=7, state="readonly")
        self.mp3_br.set("320k")
        self.mp3_br.pack(side="left", padx=(6, 16))

        ttk.Label(row2, text="FLAC 压缩级别", style="Panel.TLabel").pack(side="left")
        self.flac_lv = ttk.Combobox(row2, values=FLAC_LEVELS, width=4, state="readonly")
        self.flac_lv.set("5")
        self.flac_lv.pack(side="left", padx=(6, 16))

        ttk.Label(row2, text="并行数", style="Panel.TLabel").pack(side="left")
        self.workers = tk.IntVar(value=2)
        ttk.Spinbox(row2, from_=1, to=8, width=4, textvariable=self.workers,
                    state="readonly").pack(side="left", padx=(6, 0))

        row3 = ttk.Frame(cfg, style="Panel.TFrame")
        row3.pack(fill="x", pady=(0, 6))
        ttk.Label(row3, text="输出目录", style="Panel.TLabel").pack(side="left")
        self.outdir = tk.StringVar(value="")
        ent = ttk.Entry(row3, textvariable=self.outdir)
        ent.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row3, text="浏览…", command=self._pick_outdir, width=8).pack(side="left")
        ttk.Button(row3, text="清空", command=lambda: self.outdir.set(""),
                   width=6).pack(side="left", padx=(6, 0))

        row4 = ttk.Frame(cfg, style="Panel.TFrame")
        row4.pack(fill="x")
        self.naming = tk.StringVar(value="meta")
        ttk.Label(row4, text="文件命名", style="Panel.TLabel").pack(side="left")
        ttk.Radiobutton(row4, text="标题 - 艺术家", value="meta",
                        variable=self.naming).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(row4, text="沿用原文件名", value="original",
                        variable=self.naming).pack(side="left", padx=(12, 0))

        self.embed_lrc = tk.BooleanVar(value=True)
        ttk.Checkbutton(row4, text="嵌入同名 .lrc 歌词",
                        variable=self.embed_lrc).pack(side="left", padx=(20, 0))
        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(row4, text="覆盖已存在文件",
                        variable=self.overwrite).pack(side="left", padx=(14, 0))

        ttk.Label(cfg, text="提示：输出格式与源文件相同时直接提取、不重编码；"
                            "MP3 源文件转 FLAC 只会让文件变大，不会提升音质。",
                  foreground=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(4, 0))

        # 底部
        bottom = ttk.Frame(root, padding=(14, 0, 14, 12))
        bottom.pack(fill="x")
        self.prog = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.prog.pack(fill="x")
        self.lbl_status = ttk.Label(bottom, text="就绪", foreground=MUTED)
        self.lbl_status.pack(anchor="w", pady=(5, 0))

        acts = ttk.Frame(bottom)
        acts.pack(fill="x", pady=(8, 0))
        self.btn_go = ttk.Button(acts, text="开始转换", style="Go.TButton",
                                 command=self._start)
        self.btn_go.pack(side="left")
        self.btn_stop = ttk.Button(acts, text="停止", command=self._stop_run,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(acts, text="打开输出目录",
                   command=self._open_outdir).pack(side="left")
        self.btn_clear_log = ttk.Button(acts, text="清空日志",
                                        command=self._clear_log)
        self.btn_clear_log.pack(side="right")

        logf = ttk.Frame(root, padding=(14, 0, 14, 12))
        logf.pack(fill="both", expand=False)
        self.log = tk.Text(logf, height=6, bg="#141416", fg=FG,
                           insertbackground=FG, relief="flat",
                           font=(MONO, 9), wrap="word",
                           highlightthickness=1, highlightbackground=BORDER)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.configure(state="disabled")

        self._sync_options()

    def _refresh_ffmpeg_label(self):
        if self._ffmpeg:
            self.lbl_ffmpeg.configure(text="ffmpeg：已就绪", foreground=OK)
        else:
            self.lbl_ffmpeg.configure(text="ffmpeg：未找到", foreground=ERR)

    def _sync_options(self):
        t = self.target.get()
        self.mp3_br.configure(state="readonly" if t == "mp3" else "disabled")
        self.flac_lv.configure(state="readonly" if t == "flac" else "disabled")

    # -- 日志 ---------------------------------------------------------------

    def _log(self, text, color=None):
        self.log.configure(state="normal")
        tag = None
        if color:
            tag = "c_%s" % color.lstrip("#")
            self.log.tag_configure(tag, foreground=color)
            self.log.insert("end", text + "\n", tag)
        else:
            self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- 文件列表 -----------------------------------------------------------

    def _add_paths(self, paths):
        new = []
        existing = {v["path"].lower() for v in self.items.values()}
        for p in paths:
            if not os.path.isfile(p):
                continue
            if not p.lower().endswith(".ncm"):
                continue
            if p.lower() in existing:
                continue
            existing.add(p.lower())
            new.append(p)

        if not new:
            return

        for p in new:
            iid = self.tv.insert("", "end",
                                 values=(os.path.basename(p), "…", "", "", "待处理"))
            self.items[iid] = {"path": p, "status": "pending"}

        self._update_count()
        threading.Thread(target=self._probe, args=(new,), daemon=True).start()

    def _probe(self, paths):
        """后台读取每个 ncm 的元信息，避免大批量时卡界面。"""
        for p in paths:
            try:
                n = NCMFile(p)
                info = (os.path.basename(p),
                        n.source_format.upper(),
                        ("%d kbps" % n.bitrate_kbps) if n.bitrate_kbps else "-",
                        n.duration_text, "待处理")
                n.close()
            except NCMError as exc:
                info = (os.path.basename(p), "?", "", "", str(exc))
            except Exception as exc:
                info = (os.path.basename(p), "?", "", "", "损坏：%s" % type(exc).__name__)
            self._msgq.put(("probe", p, info))

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 .ncm 文件",
            filetypes=[("网易云音乐文件", "*.ncm"), ("所有文件", "*.*")])
        if paths:
            self._add_paths(list(paths))

    def _add_folder(self):
        d = filedialog.askdirectory(title="选择包含 .ncm 的文件夹（含子目录）")
        if not d:
            return
        found = []
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(".ncm"):
                    found.append(os.path.join(dirpath, fn))
        self._add_paths(found)
        if not found:
            self._log("该文件夹下没有找到 .ncm 文件：%s" % d, WARN)

    def _remove_selected(self):
        for iid in self.tv.selection():
            self.items.pop(iid, None)
            self.tv.delete(iid)
        self._update_count()

    def _clear(self):
        self.items.clear()
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._update_count()

    def _update_count(self):
        n = len(self.items)
        self.lbl_count.configure(text="共 %d 个文件" % n if n else "")

    def _on_drop(self, event):
        raw = event.data.strip()
        paths, buf, in_brace = [], "", False
        for ch in raw:
            if ch == "{":
                in_brace, buf = True, ""
            elif ch == "}":
                in_brace = False
                if buf:
                    paths.append(buf)
                buf = ""
            elif ch == " " and not in_brace:
                if buf:
                    paths.append(buf)
                buf = ""
            else:
                buf += ch
        if buf:
            paths.append(buf)

        targets = []
        for p in paths:
            if os.path.isdir(p):
                for dp, _d, fs in os.walk(p):
                    targets += [os.path.join(dp, f) for f in fs
                                if f.lower().endswith(".ncm")]
            else:
                targets.append(p)
        self._add_paths(targets)

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.outdir.set(d)

    def _open_outdir(self):
        d = self.outdir.get().strip()
        if not d:
            done = [v["dst"] for v in self.items.values() if v.get("dst")]
            if done:
                d = os.path.dirname(done[0])
            else:
                messagebox.showinfo("提示", "还没有输出文件。")
                return
        if os.path.isdir(d):
            os.startfile(d)

    # -- 转换 ---------------------------------------------------------------

    def _start(self):
        if self._running:
            return
        pending = [iid for iid, v in self.items.items() if v["status"] != "done"]
        if not pending:
            messagebox.showinfo("提示", "请先添加 .ncm 文件。")
            return

        self._stop.clear()
        self._running = True
        self.btn_go.configure(state="disabled", text="转换中…")
        self.btn_stop.configure(state="normal")
        self.prog["value"] = 0
        self.lbl_status.configure(text="准备中…", foreground=FG)

        cfg = dict(
            out_dir=self.outdir.get().strip(),
            target=self.target.get(),
            mp3_bitrate=self.mp3_br.get() or "320k",
            flac_level=self.flac_lv.get() or "5",
            naming=self.naming.get(),
            embed_lyrics=self.embed_lrc.get(),
            overwrite=self.overwrite.get(),
            workers=int(self.workers.get()),
        )
        self._log("开始转换：%d 个文件 → %s" % (len(pending), cfg["target"].upper()))

        threading.Thread(target=self._run, args=(pending, cfg), daemon=True).start()

    def _stop_run(self):
        if self._running:
            self._stop.set()
            self.lbl_status.configure(text="正在停止…", foreground=WARN)

    def _run(self, pending, cfg):
        from transcoder import Transcoder
        tc = Transcoder(self._ffmpeg)
        total = len(pending)
        done = [0]

        def work(iid):
            if self._stop.is_set():
                return
            item = self.items.get(iid)
            if not item:
                return
            src = item["path"]
            self._msgq.put(("start", src, iid))
            out_dir = cfg["out_dir"] or os.path.dirname(src)
            try:
                res = convert_one(
                    src, out_dir, cfg["target"],
                    mp3_bitrate=cfg["mp3_bitrate"],
                    flac_level=cfg["flac_level"],
                    naming=cfg["naming"],
                    embed_lyrics=cfg["embed_lyrics"],
                    overwrite=cfg["overwrite"],
                    transcoder=tc,
                )
            except Exception as exc:
                res = None
                self._msgq.put(("fail", src, (iid, str(exc))))
                return
            item["dst"] = res.dst
            done[0] += 1
            self._msgq.put(("done", src, (iid, res, done[0], total)))

        workers = max(1, min(8, int(cfg.get("workers", 2) or 2)))
        self._msgq.put(("workers", "", workers))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, pending))
        finally:
            self._msgq.put(("finish", "", (done[0], total, self._stop.is_set())))

    # -- 队列轮询 -----------------------------------------------------------

    def _poll(self):
        try:
            while True:
                msg = self._msgq.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]

        if kind == "probe":
            _p, info = msg[1], msg[2]
            for iid, v in self.items.items():
                if v["path"].lower() == _p.lower() and v["status"] == "pending":
                    self.tv.item(iid, values=info)
                    if info[1] == "?":
                        self.tv.item(iid, tags=("err",))
                    break

        elif kind == "start":
            iid = msg[2]
            if self.tv.exists(iid):
                vals = list(self.tv.item(iid, "values"))
                vals[4] = "转换中…"
                self.tv.item(iid, values=vals, tags=("run",))
                self.tv.see(iid)

        elif kind == "done":
            iid, res, n, total = msg[2]
            if self.tv.exists(iid):
                vals = list(self.tv.item(iid, "values"))
                if res.ok:
                    self.items[iid]["status"] = "done"
                    if res.skipped:
                        vals[4] = "已存在，跳过"
                        self.tv.item(iid, values=vals, tags=("skip",))
                        self._log("跳过（已存在）：%s" % os.path.basename(res.dst), MUTED)
                    else:
                        vals[4] = "完成 · %s" % res.message
                        self.tv.item(iid, values=vals, tags=("ok",))
                        self._log("[%d/%d] %s → %s（%s）"
                                  % (n, total, os.path.basename(res.src),
                                     os.path.basename(res.dst), res.message), OK)
                else:
                    vals[4] = "失败：%s" % res.message
                    self.tv.item(iid, values=vals, tags=("err",))
                    self._log("[失败] %s：%s"
                              % (os.path.basename(res.src), res.message), ERR)
            pct = n * 100.0 / max(1, total)
            self.prog["value"] = pct
            self.lbl_status.configure(text="进度 %d / %d（%.0f%%）" % (n, total, pct))

        elif kind == "fail":
            iid, err = msg[2]
            if self.tv.exists(iid):
                vals = list(self.tv.item(iid, "values"))
                vals[4] = "失败：%s" % err
                self.tv.item(iid, values=vals, tags=("err",))
            self._log("[失败] %s" % err, ERR)

        elif kind == "finish":
            n, total, stopped = msg[2]
            self._running = False
            self.btn_go.configure(state="normal", text="开始转换")
            self.btn_stop.configure(state="disabled")
            if stopped:
                self.lbl_status.configure(text="已停止（完成 %d / %d）" % (n, total))
                self._log("已手动停止。", WARN)
            else:
                self.prog["value"] = 100
                self.lbl_status.configure(
                    text="全部完成：%d 个文件" % n, foreground=OK)
                self._log("全部完成，共 %d 个文件。" % n, OK)


def main():
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
