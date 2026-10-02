#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.py - miHoYo 缓存提取工具的图形界面（PySide6 版本）。

功能：
    - 输入/输出路径选择（浏览按钮 + 拖拽文件夹）
    - 分类进度条与实时日志
    - 分类统计表格
    - 打开输出目录
    - 主题切换（Fusion / 系统原生 / 暗色 / 亮色）
    - 多语言（中文 / English）

依赖：
    pip install PySide6

运行：
    python scripts/gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

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
        "verify_copy": "校验复制完整性（SHA256）",
        "verify_content": "校验内容有效性",
        "gen_report": "生成校验报告",
        "use_cache_meta": "使用缓存元数据（URL 命名 + 智能分类）",
        "merge_video": "合并视频分片",
        "invalid": "无效",
        "report_saved": "校验报告已保存: {path}",
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
        "stopped": "已停止",
        "themes": ["Fusion", "系统原生", "亮色", "暗色"],
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
        "verify_copy": "Verify copy integrity (SHA256)",
        "verify_content": "Verify content validity",
        "gen_report": "Generate verification report",
        "use_cache_meta": "Use cache metadata (URL naming + smart classify)",
        "merge_video": "Merge video segments",
        "invalid": "Invalid",
        "report_saved": "Report saved: {path}",
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
        "stopped": "Stopped",
        "themes": ["Fusion", "System", "Light", "Dark"],
        "lang_zh": "中文",
        "lang_en": "English",
    },
}

# 主题键：I18N 显示名 -> 应用函数
THEME_KEYS = ["fusion", "native", "light", "dark"]


# ---------------------------------------------------------------------------
# 拖拽输入框
# ---------------------------------------------------------------------------

class DropLineEdit(QLineEdit):
    """支持拖拽文件夹路径的输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._placeholder = ""

    def setPlaceholder(self, text: str) -> None:
        self._placeholder = text
        self.setPlaceholderText(text)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.setText(path)
                self.setStyleSheet("")


# ---------------------------------------------------------------------------
# 后台提取线程
# ---------------------------------------------------------------------------

class ExtractThread(QThread):
    progress = Signal(int, int, str, str)   # current, total, type_name, filename
    finished_ok = Signal(dict)              # counts
    stopped = Signal(dict)                  # counts
    failed = Signal(str)                    # error message

    def __init__(self, input_dir: Path, output_dir: Path, force: bool, quiet: bool,
                 do_verify: bool = False, do_verify_content: bool = False,
                 report_path: Path | None = None,
                 use_cache_meta: bool = True, merge_video: bool = True):
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.force = force
        self.quiet = quiet
        self.do_verify = do_verify
        self.do_verify_content = do_verify_content
        self.report_path = report_path
        self.use_cache_meta = use_cache_meta
        self.merge_video = merge_video
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            counts = extract(
                self.input_dir,
                self.output_dir,
                force=self.force,
                progress=self._on_progress,
                do_verify=self.do_verify,
                do_verify_content=self.do_verify_content,
                report_path=self.report_path,
                use_cache_meta=self.use_cache_meta,
                merge_video=self.merge_video,
            )
            if self._stop:
                self.stopped.emit(counts)
            else:
                self.finished_ok.emit(counts)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_progress(self, current: int, total: int, type_name: str, filename: str) -> None:
        if not self._stop:
            self.progress.emit(current, total, type_name, filename)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.lang = "zh_CN"
        self.tr = I18N[self.lang]
        self.worker: ExtractThread | None = None
        self._stats: dict[str, int] = {}
        self._invalid_count = 0

        self._build_ui()
        self._apply_theme("fusion")
        self._refresh_texts()

    # ------------------------------------------------------------- build UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶部：语言 + 主题
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("语言"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["中文", "English"])
        self.lang_combo.currentIndexChanged.connect(self._on_lang_change)
        top_bar.addWidget(self.lang_combo)
        top_bar.addSpacing(20)
        top_bar.addWidget(QLabel("主题"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(self.tr["themes"])
        self.theme_combo.currentIndexChanged.connect(self._on_theme_change)
        top_bar.addWidget(self.theme_combo)
        top_bar.addStretch(1)
        root.addLayout(top_bar)

        # 路径区
        path_group = QGroupBox()
        grid = QGridLayout(path_group)

        self.input_edit = DropLineEdit()
        self.output_edit = DropLineEdit()
        self.input_label = QLabel()
        self.output_label = QLabel()
        self.input_browse = QPushButton()
        self.output_browse = QPushButton()
        self.input_browse.clicked.connect(lambda: self._browse("input"))
        self.output_browse.clicked.connect(lambda: self._browse("output"))

        grid.addWidget(self.input_label, 0, 0)
        grid.addWidget(self.input_edit, 0, 1)
        grid.addWidget(self.input_browse, 0, 2)
        grid.addWidget(self.output_label, 1, 0)
        grid.addWidget(self.output_edit, 1, 1)
        grid.addWidget(self.output_browse, 1, 2)
        grid.setColumnStretch(1, 1)
        root.addWidget(path_group)

        # 选项
        opt = QHBoxLayout()
        self.force_cb = QCheckBox()
        self.quiet_cb = QCheckBox()
        self.verify_copy_cb = QCheckBox()
        self.verify_copy_cb.setChecked(True)
        self.verify_content_cb = QCheckBox()
        self.verify_content_cb.setChecked(True)
        self.gen_report_cb = QCheckBox()
        self.gen_report_cb.setChecked(True)
        self.use_cache_meta_cb = QCheckBox()
        self.use_cache_meta_cb.setChecked(True)
        self.merge_video_cb = QCheckBox()
        self.merge_video_cb.setChecked(True)
        opt.addWidget(self.force_cb)
        opt.addWidget(self.quiet_cb)
        opt.addWidget(self.verify_copy_cb)
        opt.addWidget(self.verify_content_cb)
        opt.addWidget(self.gen_report_cb)
        opt.addWidget(self.use_cache_meta_cb)
        opt.addWidget(self.merge_video_cb)
        opt.addStretch(1)
        root.addLayout(opt)

        # 进度
        prog_group = QGroupBox()
        pvl = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_label = QLabel()
        pvl.addWidget(self.progress_bar)
        pvl.addWidget(self.progress_label)
        root.addWidget(prog_group)

        # 按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton()
        self.start_btn.clicked.connect(self._on_start)
        self.open_btn = QPushButton()
        self.open_btn.clicked.connect(self._open_output)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # 主体：日志 + 统计
        splitter = QSplitter(Qt.Horizontal)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        splitter.addWidget(self._wrap_group(self.log_text))

        self.stats_table = QTableWidget(0, 2)
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stats_table.setSelectionMode(QTableWidget.NoSelection)
        splitter.addWidget(self._wrap_group(self.stats_table))
        splitter.setSizes([460, 220])
        root.addWidget(splitter, 1)

        self.resize(780, 620)
        self.setMinimumSize(700, 540)

        # 预填默认路径（与 CLI 优先级一致：项目根 Cache_Data -> 系统默认缓存路径）
        self.output_edit.setText(str(resolve_output_dir(None)))
        default_in = self._default_input_dir()
        if default_in:
            self.input_edit.setText(str(default_in))

    def _default_input_dir(self) -> Path | None:
        """按与 CLI 一致的优先级返回默认输入目录。"""
        # 1. 项目根 Cache_Data
        local = project_root() / "Cache_Data"
        if local.is_dir():
            return local
        # 2. 系统默认缓存路径
        sys_default = default_cache_dir()
        if sys_default:
            return sys_default
        return None

    def _wrap_group(self, widget: QWidget) -> QGroupBox:
        box = QGroupBox()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(widget)
        return box

    # --------------------------------------------------------- text refresh
    def _refresh_texts(self) -> None:
        tr = self.tr
        self.setWindowTitle(tr["title"])
        self.input_label.setText(tr["input"])
        self.output_label.setText(tr["output"])
        self.input_browse.setText(tr["browse"])
        self.output_browse.setText(tr["browse"])
        self.force_cb.setText(tr["force"])
        self.quiet_cb.setText(tr["quiet"])
        self.verify_copy_cb.setText(tr["verify_copy"])
        self.verify_content_cb.setText(tr["verify_content"])
        self.gen_report_cb.setText(tr["gen_report"])
        self.use_cache_meta_cb.setText(tr["use_cache_meta"])
        self.merge_video_cb.setText(tr["merge_video"])
        self.start_btn.setText(tr["start"])
        self.open_btn.setText(tr["open_output"])
        self.progress_label.setText(tr["ready"])
        self.input_edit.setPlaceholder(tr["drag_hint_input"])
        self.output_edit.setPlaceholder(tr["drag_hint_output"])

        # 表格表头
        self.stats_table.setHorizontalHeaderLabels([tr["type"], tr["count"]])

        # 重组语言下拉显示
        idx = self.lang_combo.currentIndex()
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItems([tr["lang_zh"], tr["lang_en"]])
        self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.blockSignals(False)

        # 重组主题下拉
        theme_idx = self.theme_combo.currentIndex()
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItems(tr["themes"])
        self.theme_combo.setCurrentIndex(theme_idx)
        self.theme_combo.blockSignals(False)

    # ------------------------------------------------------------- handlers
    def _browse(self, kind: str) -> None:
        current = self.input_edit.text() if kind == "input" else self.output_edit.text()
        start = current if Path(current).is_dir() else str(Path.home())
        d = QFileDialog.getExistingDirectory(self, self.tr["browse"], start)
        if d:
            if kind == "input":
                self.input_edit.setText(d)
            else:
                self.output_edit.setText(d)

    def _on_lang_change(self, idx: int) -> None:
        self.lang = "zh_CN" if idx == 0 else "en"
        self.tr = I18N[self.lang]
        self._refresh_texts()

    def _on_theme_change(self, idx: int) -> None:
        if 0 <= idx < len(THEME_KEYS):
            self._apply_theme(THEME_KEYS[idx])

    # ------------------------------------------------------------- themes
    def _apply_theme(self, key: str) -> None:
        app = QApplication.instance()
        if key == "fusion":
            app.setStyle("Fusion")
            app.setPalette(app.style().standardPalette())
        elif key == "native":
            app.setStyle("")  # 系统原生
            app.setPalette(app.style().standardPalette())
        elif key == "light":
            app.setStyle("Fusion")
            pal = app.style().standardPalette()
            app.setPalette(pal)
        elif key == "dark":
            app.setStyle("Fusion")
            from PySide6.QtGui import QPalette, QColor
            pal = QPalette()
            pal.setColor(QPalette.Window, QColor(53, 53, 53))
            pal.setColor(QPalette.WindowText, Qt.white)
            pal.setColor(QPalette.Base, QColor(25, 25, 25))
            pal.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
            pal.setColor(QPalette.ToolTipBase, Qt.white)
            pal.setColor(QPalette.ToolTipText, Qt.white)
            pal.setColor(QPalette.Text, Qt.white)
            pal.setColor(QPalette.Button, QColor(53, 53, 53))
            pal.setColor(QPalette.ButtonText, Qt.white)
            pal.setColor(QPalette.BrightText, Qt.red)
            pal.setColor(QPalette.Link, QColor(42, 130, 218))
            pal.setColor(QPalette.Highlight, QColor(42, 130, 218))
            pal.setColor(QPalette.HighlightedText, Qt.black)
            app.setPalette(pal)

    # ------------------------------------------------------------- log/stats
    def _log(self, msg: str) -> None:
        self.log_text.appendPlainText(msg)

    def _update_stats(self, counts: dict[str, int]) -> None:
        self.stats_table.setRowCount(0)
        total = 0
        for name in sorted(counts):
            row = self.stats_table.rowCount()
            self.stats_table.insertRow(row)
            self.stats_table.setItem(row, 0, QTableWidgetItem(name))
            self.stats_table.setItem(row, 1, QTableWidgetItem(str(counts[name])))
            total += counts[name]
        if self._invalid_count:
            row = self.stats_table.rowCount()
            self.stats_table.insertRow(row)
            item = QTableWidgetItem(self.tr["invalid"])
            self.stats_table.setItem(row, 0, item)
            self.stats_table.setItem(row, 1, QTableWidgetItem(str(self._invalid_count)))
        row = self.stats_table.rowCount()
        self.stats_table.insertRow(row)
        self.stats_table.setItem(row, 0, QTableWidgetItem(self.tr["total"]))
        self.stats_table.setItem(row, 1, QTableWidgetItem(str(total)))

    # ------------------------------------------------------------- run
    def _on_start(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.start_btn.setEnabled(False)
            return

        tr = self.tr
        input_path = self.input_edit.text().strip()
        output_path = self.output_edit.text().strip()

        if not input_path or input_path in (tr["drag_hint_input"], I18N["en"]["drag_hint_input"]):
            QMessageBox.warning(self, tr["title"], tr["no_input"])
            return
        if not Path(input_path).is_dir():
            QMessageBox.critical(self, tr["title"], tr["input_not_dir"])
            return
        if not output_path or output_path in (tr["drag_hint_output"], I18N["en"]["drag_hint_output"]):
            QMessageBox.warning(self, tr["title"], tr["no_output"])
            return

        self._stats = {}
        self._invalid_count = 0
        self._update_stats({})
        self.log_text.clear()
        self.start_btn.setText(tr["stop"])
        self.progress_label.setText(tr["running"])
        self.progress_bar.setValue(0)

        report_path = (Path(output_path) / "report.json").resolve() \
            if self.gen_report_cb.isChecked() else None

        self.worker = ExtractThread(
            Path(input_path), Path(output_path),
            force=self.force_cb.isChecked(),
            quiet=self.quiet_cb.isChecked(),
            do_verify=self.verify_copy_cb.isChecked(),
            do_verify_content=self.verify_content_cb.isChecked(),
            report_path=report_path,
            use_cache_meta=self.use_cache_meta_cb.isChecked(),
            merge_video=self.merge_video_cb.isChecked(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_progress(self, current: int, total: int, type_name: str, filename: str) -> None:
        pct = int(current / total * 100) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_label.setText(f"{current}/{total}  {type_name}")
        if type_name == "invalid":
            self._invalid_count += 1
        else:
            self._stats[type_name] = self._stats.get(type_name, 0) + 1
        if not self.quiet_cb.isChecked():
            self._log(f"  {type_name:>12}  {filename}")
        self._update_stats(self._stats)

    def _on_done(self, counts: dict[str, int]) -> None:
        self._update_stats(counts)
        total = sum(counts.values())
        self.progress_bar.setValue(100)
        msg = self.tr["done"].format(total=total)
        if self._invalid_count:
            msg += f"  ({self._invalid_count} {self.tr['invalid']})"
        self.progress_label.setText(msg)
        self._log(msg)
        if self.gen_report_cb.isChecked():
            self._log(self.tr["report_saved"].format(path=self.worker.report_path))

    def _on_stopped(self, counts: dict[str, int]) -> None:
        self._update_stats(counts)
        self.progress_label.setText(self.tr["stopped"])
        self._log("--- " + self.tr["stopped"] + " ---")

    def _on_failed(self, msg: str) -> None:
        self._log(f"[ERROR] {msg}")

    def _on_worker_finished(self) -> None:
        self.start_btn.setText(self.tr["start"])
        self.start_btn.setEnabled(True)
        self.worker = None

    def _open_output(self) -> None:
        output = self.output_edit.text().strip()
        tr = self.tr
        if not output or output in (tr["drag_hint_output"], I18N["en"]["drag_hint_output"]):
            output = str(resolve_output_dir(None))
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
