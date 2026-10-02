#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.py - miHoYo 缓存提取工具的图形界面。

功能：
    - 输入/输出路径选择（浏览按钮 + 拖拽）
    - 分类进度条与实时日志
    - 分类统计表格
    - 打开输出目录
    - 主题切换 / 多语言（中文 / English）

依赖：
    pip install tqdm tkinterdnd2

运行：
    python scripts/gui.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import StringVar, BooleanVar, filedialog, messagebox
from tkinter import ttk
import tkinter as tk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

# 让 scripts/ 目录下的模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_cache import (  # noqa: E402
    extract,
    default_cache_dir,
    project_root,
    resolve_output_dir,
)


# ---------------------------------------------------------------------------
# 国际化
# ---------------------------------------------------------------------------

I18N = {
    "zh_CN": {
        "title": "miHoYo 缓存提取工具",
        "input": "缓存目录 (Cache_Data)",
        "output": "输出目录",
        "browse": "浏览…",
        "start": "开始提取",
        "stop": "停止",
        "force": "覆盖已存在文件",
        "quiet": "静默模式（不逐条输出）",
        "progress": "进度",
        "log": "日志",
        "stats": "分类统计",
        "type": "类型",
        "count": "数量",
        "total": "合计",
        "open_output": "打开输出目录",
        "theme": "主题",
        "lang": "语言",
        "drag_hint_input": "可将 Cache_Data 文件夹拖拽到此处",
        "drag_hint_output": "可将目标文件夹拖拽到此处",
        "ready": "就绪",
        "running": "提取中…",
        "done": "完成，共处理 {total} 个文件",
        "no_input": "请先选择缓存目录",
        "no_output": "请先选择输出目录",
        "input_not_dir": "输入路径不是有效目录",
        "cleared": "已清空统计",
        "lang_zh": "中文",
        "lang_en": "English",
    },
    "en": {
        "title": "miHoYo Cache Extractor",
        "input": "Cache Directory (Cache_Data)",
        "output": "Output Directory",
        "browse": "Browse…",
        "start": "Start",
        "stop": "Stop",
        "force": "Overwrite existing files",
        "quiet": "Quiet mode (no per-file log)",
        "progress": "Progress",
        "log": "Log",
        "stats": "Statistics",
        "type": "Type",
        "count": "Count",
        "total": "Total",
        "open_output": "Open Output Dir",
        "theme": "Theme",
        "lang": "Language",
        "drag_hint_input": "Drop Cache_Data folder here",
        "drag_hint_output": "Drop target folder here",
        "ready": "Ready",
        "running": "Extracting…",
        "done": "Done, {total} files processed",
        "no_input": "Please select a cache directory",
        "no_output": "Please select an output directory",
        "input_not_dir": "Input path is not a valid directory",
        "cleared": "Stats cleared",
        "lang_zh": "中文",
        "lang_en": "English",
    },
}

THEMES = ["clam", "alt", "default", "classic", "vista", "xpnative"]


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.lang = "zh_CN"
        self.tr = I18N[self.lang]

        self.input_var = StringVar()
        self.output_var = StringVar(value=str(resolve_output_dir(None)))
        self.force_var = BooleanVar(value=False)
        self.quiet_var = BooleanVar(value=False)
        self.theme_var = StringVar(value="clam")

        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self._stats: dict[str, int] = {}

        self._build_ui()
        self._apply_theme()
        self._poll_queue()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        tr = self.tr
        self.root.title(tr["title"])
        self.root.geometry("760x640")
        self.root.minsize(680, 560)

        style = ttk.Style()
        self._available_themes = [t for t in THEMES if t in style.theme_names()]
        if "clam" not in self._available_themes and self._available_themes:
            self.theme_var.set(self._available_themes[0])

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        # 语言 + 主题
        opts = ttk.Frame(top)
        opts.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(opts, text=tr["lang"]).pack(side=tk.LEFT)
        self.lang_combo = ttk.Combobox(
            opts, values=[tr["lang_zh"], tr["lang_en"]], width=10, state="readonly"
        )
        self.lang_combo.set(tr["lang_zh"])
        self.lang_combo.pack(side=tk.LEFT, padx=(4, 16))
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)

        ttk.Label(opts, text=tr["theme"]).pack(side=tk.LEFT)
        self.theme_combo = ttk.Combobox(
            opts, values=self._available_themes, width=10, state="readonly",
            textvariable=self.theme_var,
        )
        self.theme_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_theme())

        # 输入路径
        self._build_path_row(top, "input", self.input_var, tr["drag_hint_input"])
        # 输出路径
        self._build_path_row(top, "output", self.output_var, tr["drag_hint_output"])

        # 选项
        opt_frame = ttk.Frame(top)
        opt_frame.pack(fill=tk.X, pady=(4, 0))
        self.force_cb = ttk.Checkbutton(opt_frame, text=tr["force"], variable=self.force_var)
        self.force_cb.pack(side=tk.LEFT)
        self.quiet_cb = ttk.Checkbutton(opt_frame, text=tr["quiet"], variable=self.quiet_var)
        self.quiet_cb.pack(side=tk.LEFT, padx=(12, 0))

        # 进度
        prog_frame = ttk.LabelFrame(self.root, text=tr["progress"], padding=8)
        prog_frame.pack(fill=tk.X, padx=8, pady=(4, 0))
        self.progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress.pack(fill=tk.X)
        self.progress_label = ttk.Label(prog_frame, text=tr["ready"])
        self.progress_label.pack(anchor=tk.W, pady=(4, 0))

        # 按钮区
        btn_frame = ttk.Frame(self.root, padding=(8, 4))
        btn_frame.pack(fill=tk.X)
        self.start_btn = ttk.Button(btn_frame, text=tr["start"], command=self._on_start)
        self.start_btn.pack(side=tk.LEFT)
        self.open_btn = ttk.Button(btn_frame, text=tr["open_output"], command=self._open_output)
        self.open_btn.pack(side=tk.LEFT, padx=(8, 0))

        # 主体：左日志，右统计
        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # 日志
        log_frame = ttk.LabelFrame(body, text=tr["log"], padding=4)
        body.add(log_frame, weight=3)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 统计表格
        stats_frame = ttk.LabelFrame(body, text=tr["stats"], padding=4)
        body.add(stats_frame, weight=1)
        self.tree = ttk.Treeview(stats_frame, columns=("count",), show="tree headings", height=10)
        self.tree.heading("#0", text=tr["type"])
        self.tree.heading("count", text=tr["count"])
        self.tree.column("#0", width=110)
        self.tree.column("count", width=60, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True)

    def _build_path_row(self, parent, kind: str, var: StringVar, hint: str) -> None:
        tr = self.tr
        label_text = tr["input"] if kind == "input" else tr["output"]
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label_text, width=18).pack(side=tk.LEFT)
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        if kind == "input":
            self.input_entry = entry
        else:
            self.output_entry = entry

        browse = ttk.Button(row, text=tr["browse"], width=10,
                            command=lambda: self._browse(kind))
        browse.pack(side=tk.LEFT)

        # 拖拽支持
        if _HAS_DND:
            entry.drop_target_register(DND_FILES)
            entry.dnd_bind("<<Drop>>", lambda e, k=kind: self._on_drop(e, k))
            entry.configure(foreground="gray")
            var.set(hint)
            entry.bind("<FocusIn>", lambda e, k=kind: self._clear_hint(k))
        else:
            if kind == "input":
                # 无 DND 时填入默认路径提示
                d = default_cache_dir()
                if d:
                    var.set(str(d))

    # ------------------------------------------------------------- helpers
    def _clear_hint(self, kind: str) -> None:
        var = self.input_var if kind == "input" else self.output_var
        tr = self.tr
        hint = tr["drag_hint_input"] if kind == "input" else tr["drag_hint_output"]
        if var.get() == hint:
            var.set("")

    def _browse(self, kind: str) -> None:
        var = self.input_var if kind == "input" else self.output_var
        d = filedialog.askdirectory()
        if d:
            var.set(d)
            entry = self.input_entry if kind == "input" else self.output_entry
            entry.configure(foreground="black")

    def _on_drop(self, event, kind: str) -> None:
        paths = self.root.tk.splitlist(event.data)
        if paths:
            var = self.input_var if kind == "input" else self.output_var
            var.set(paths[0])
            entry = self.input_entry if kind == "input" else self.output_entry
            entry.configure(foreground="black")

    def _apply_theme(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use(self.theme_var.get())
        except tk.TclError:
            pass

    def _on_lang_change(self, _event=None) -> None:
        tr = self.tr
        self.lang = "en" if self.lang_combo.get() == tr["lang_en"] else "zh_CN"
        self.tr = I18N[self.lang]
        self._refresh_texts()

    def _refresh_texts(self) -> None:
        tr = self.tr
        self.root.title(tr["title"])
        # 重设 entry 占位提示（仅当为空或为旧提示时）
        for kind, hint_key in (("input", "drag_hint_input"), ("output", "drag_hint_output")):
            var = self.input_var if kind == "input" else self.output_var
            entry = self.input_entry if kind == "input" else self.output_entry
            old = I18N["zh_CN" if self.lang == "en" else "en"][hint_key]
            if var.get() in (old, ""):
                var.set(tr[hint_key])
                entry.configure(foreground="gray")

        self.force_cb.configure(text=tr["force"])
        self.quiet_cb.configure(text=tr["quiet"])
        self.start_btn.configure(text=tr["start"])
        self.open_btn.configure(text=tr["open_output"])
        # 重新设置下拉显示名
        cur = self.lang_combo.get()
        self.lang_combo.configure(values=[tr["lang_zh"], tr["lang_en"]])
        self.lang_combo.set(tr["lang_zh"] if self.lang == "zh_CN" else tr["lang_en"])

    # ----------------------------------------------------------- log/stats
    def _log(self, msg: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _update_stats(self, counts: dict[str, int]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        total = 0
        for name in sorted(counts):
            self.tree.insert("", tk.END, text=name, values=(counts[name],))
            total += counts[name]
        self.tree.insert("", tk.END, text=self.tr["total"], values=(total,))

    # --------------------------------------------------------------- run
    def _on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            # 正在运行 → 停止
            self.stop_event.set()
            self.start_btn.configure(state=tk.DISABLED)
            return

        tr = self.tr
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        # 清除提示占位
        if input_path in (tr["drag_hint_input"], I18N["en"]["drag_hint_input"]):
            input_path = ""
        if output_path in (tr["drag_hint_output"], I18N["en"]["drag_hint_output"]):
            output_path = str(resolve_output_dir(None))

        if not input_path:
            messagebox.showwarning(tr["title"], tr["no_input"])
            return
        if not Path(input_path).is_dir():
            messagebox.showerror(tr["title"], tr["input_not_dir"])
            return
        if not output_path:
            messagebox.showwarning(tr["title"], tr["no_output"])
            return

        # 清空旧状态
        self.stop_event.clear()
        self._stats = {}
        self._update_stats({})
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self.start_btn.configure(text=tr["stop"])
        self.progress_label.configure(text=tr["running"])
        self.progress["value"] = 0
        self.progress["maximum"] = 100

        self.worker = threading.Thread(
            target=self._run_extract,
            args=(Path(input_path), Path(output_path), self.force_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _run_extract(self, input_dir: Path, output_dir: Path, force: bool) -> None:
        quiet = self.quiet_var.get()

        def _progress(current: int, total: int, type_name: str, filename: str) -> None:
            if self.stop_event.is_set():
                return
            self.msg_queue.put(("progress", current, total, type_name, filename))

        try:
            counts = extract(input_dir, output_dir, force=force, progress=_progress)
            if not self.stop_event.is_set():
                self.msg_queue.put(("done", counts))
            else:
                self.msg_queue.put(("stopped", counts))
        except Exception as exc:
            self.msg_queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, current, total, type_name, filename = msg
                    pct = (current / total * 100) if total else 0
                    self.progress["value"] = pct
                    self.progress_label.configure(
                        text=f"{current}/{total}  {type_name}"
                    )
                    self._stats[type_name] = self._stats.get(type_name, 0) + 1
                    if not self.quiet_var.get():
                        self._log(f"  {type_name:>12}  {filename}")
                    self._update_stats(self._stats)
                elif kind == "done":
                    counts = msg[1]
                    self._update_stats(counts)
                    total = sum(counts.values())
                    self.progress["value"] = 100
                    self.progress_label.configure(text=self.tr["done"].format(total=total))
                    self._log(self.tr["done"].format(total=total))
                    self.start_btn.configure(text=self.tr["start"], state=tk.NORMAL)
                    self.worker = None
                elif kind == "stopped":
                    counts = msg[1]
                    self._update_stats(counts)
                    self.progress_label.configure(text=self.tr["ready"])
                    self._log("--- stopped ---")
                    self.start_btn.configure(text=self.tr["start"], state=tk.NORMAL)
                    self.worker = None
                elif kind == "error":
                    self._log(f"[ERROR] {msg[1]}")
                    self.start_btn.configure(text=self.tr["start"], state=tk.NORMAL)
                    self.worker = None
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _open_output(self) -> None:
        output = self.output_var.get().strip()
        if not output or output in (self.tr["drag_hint_output"], I18N["en"]["drag_hint_output"]):
            output = str(resolve_output_dir(None))
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
