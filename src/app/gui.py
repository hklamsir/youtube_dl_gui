"""
使用者介面模組 — 基於 tkinter/ttk 的圖形化操作介面。
負責所有 UI 元件建立、事件綁定、進度顯示與使用者互動。
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from PIL import Image, ImageTk
from io import BytesIO
import os
import queue
import threading
import requests
from datetime import datetime

from .config import load_settings, save_settings, DEFAULT_SETTINGS
from .downloader import DownloadManager
from .history import DownloadHistory


class YouTubeDownloaderGUI:
    """GUI 版本的 YouTube 下載器，使用 tkinter 和 ttk。"""

    LOG_FILE = "yd_log.txt"
    MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("YouTube 下載器 - Designed by Lamsir")
        self.root.geometry("900x900")
        self.root.resizable(True, True)

        # ─── 訊息佇列（執行緒間通訊）───
        self.queue = queue.Queue()
        self._log_lock = threading.Lock()

        # ─── 設定 ───
        self.settings = load_settings()
        self.FFMPEG_PATH = self.settings.get('ffmpeg_path', '')
        self.DOWNLOAD_RETRIES = self.settings.get('retries', 2)
        self.RETRY_DELAY = self.settings.get('delay', 5)
        self.PARALLEL_DOWNLOADS = self.settings.get('parallel_downloads', 2)
        self.DEFAULT_DOWNLOAD_PATH = self.settings.get('default_download_path', os.getcwd())

        # ─── 下載管理與歷史 ───
        self.download_manager = DownloadManager(
            ffmpeg_path=self.FFMPEG_PATH,
            retries=self.DOWNLOAD_RETRIES,
            retry_delay=self.RETRY_DELAY,
            parallel_downloads=self.PARALLEL_DOWNLOADS,
            msg_queue=self.queue,
        )
        self.history = DownloadHistory()

        # ─── tk 變數 ───
        self.url_var = tk.StringVar()
        self.video_title_var = tk.StringVar()
        self.download_path_var = tk.StringVar(value=self.DEFAULT_DOWNLOAD_PATH)
        self.download_type_var = tk.StringVar(value="video")
        self.subtitle_var = tk.StringVar(value="none")
        self.total_progress_var = tk.DoubleVar()
        self.file_progress_var = tk.DoubleVar()

        self.ffmpeg_path_var = tk.StringVar(value=self.FFMPEG_PATH)
        self.retries_var = tk.IntVar(value=self.DOWNLOAD_RETRIES)
        self.delay_var = tk.IntVar(value=self.RETRY_DELAY)
        self.parallel_var = tk.IntVar(value=self.PARALLEL_DOWNLOADS)
        self.default_download_path_var = tk.StringVar(value=self.DEFAULT_DOWNLOAD_PATH)

        # ─── 資料儲存 ───
        self.available_formats = []
        self.available_subtitles = {}
        self.channel_videos = []
        self.thumbnail_photo = None
        self.interactive_widgets = []
        self._after_id = None

        # ─── 視窗關閉處理 ───
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # ─── 建立 UI ───
        self._create_widgets()

        # ─── 將主視窗置中於螢幕 ───
        self._center_root_window()

        # ─── 啟動初始化 ───
        threading.Thread(target=self.download_manager.update_yt_dlp, daemon=True).start()
        self._check_ffmpeg()
        self._check_queue()
        self.url_var.trace_add("write", self._validate_url_length)

    # ═══════════════════════════════════════════════════════
    #  設定管理
    # ═══════════════════════════════════════════════════════

    def _save_settings(self):
        """將目前設定寫入 JSON 檔案。"""
        settings = {
            'ffmpeg_path': self.FFMPEG_PATH,
            'retries': self.DOWNLOAD_RETRIES,
            'delay': self.RETRY_DELAY,
            'parallel_downloads': self.PARALLEL_DOWNLOADS,
            'default_download_path': self.DEFAULT_DOWNLOAD_PATH,
        }
        if save_settings(settings):
            self._log(f"設定已儲存。")
        else:
            self.queue.put({"type": "error", "text": "無法儲存設定。"})

    def _apply_settings(self, settings_window: tk.Toplevel):
        """套用設定視窗中的變更。"""
        new_ffmpeg_path = self.ffmpeg_path_var.get()
        new_default_path = self.default_download_path_var.get()

        if new_ffmpeg_path and not (
            os.path.isfile(new_ffmpeg_path) and os.access(new_ffmpeg_path, os.X_OK)
        ):
            self._show_error(
                "路徑錯誤",
                f"指定的 FFmpeg 路徑無效或檔案無法執行:\n{new_ffmpeg_path}",
            )
            return

        if not os.path.isdir(new_default_path):
            self._show_error(
                "路徑錯誤",
                f"指定的預設下載路徑無效:\n{new_default_path}",
            )
            return

        self.FFMPEG_PATH = new_ffmpeg_path
        self.DOWNLOAD_RETRIES = self.retries_var.get()
        self.RETRY_DELAY = self.delay_var.get()
        self.PARALLEL_DOWNLOADS = self.parallel_var.get()
        self.DEFAULT_DOWNLOAD_PATH = new_default_path

        self.download_path_var.set(self.DEFAULT_DOWNLOAD_PATH)

        # 更新下載管理器
        self.download_manager.ffmpeg_path = self.FFMPEG_PATH
        self.download_manager.retries = self.DOWNLOAD_RETRIES
        self.download_manager.retry_delay = self.RETRY_DELAY
        self.download_manager.parallel_downloads = self.PARALLEL_DOWNLOADS

        self._save_settings()
        self._log("設定已更新。")
        self._check_ffmpeg()
        settings_window.destroy()

    def _browse_ffmpeg_path(self, parent: tk.Toplevel):
        path = filedialog.askopenfilename(
            parent=parent,
            title="選取 ffmpeg.exe",
            initialdir=(
                os.path.dirname(self.ffmpeg_path_var.get())
                if self.ffmpeg_path_var.get() else "/"
            ),
            filetypes=[("Executable files", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.ffmpeg_path_var.set(path)

    def _browse_default_path(self, parent: tk.Toplevel):
        path = filedialog.askdirectory(
            parent=parent,
            title="選取預設下載路徑",
            initialdir=self.default_download_path_var.get(),
        )
        if path:
            self.default_download_path_var.set(path)

    def _open_settings_window(self):
        """開啟設定對話框。"""
        self.ffmpeg_path_var.set(self.FFMPEG_PATH)
        self.retries_var.set(self.DOWNLOAD_RETRIES)
        self.delay_var.set(self.RETRY_DELAY)
        self.parallel_var.set(self.PARALLEL_DOWNLOADS)
        self.default_download_path_var.set(self.DEFAULT_DOWNLOAD_PATH)

        win = tk.Toplevel(self.root)
        win.title("設定")
        win.geometry("600x260")
        win.transient(self.root)
        win.grab_set()

        main = ttk.Frame(win, padding="10")
        main.grid(row=0, column=0, sticky="nsew")
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        row = 0

        # FFmpeg 路徑
        ttk.Label(main, text="FFmpeg 路徑:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ff_frame = ttk.Frame(main)
        ff_frame.grid(row=row, column=1, sticky="ew")
        ff_frame.columnconfigure(0, weight=1)
        ttk.Entry(ff_frame, textvariable=self.ffmpeg_path_var, width=60).grid(
            row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(ff_frame, text="瀏覽",
                   command=lambda: self._browse_ffmpeg_path(win)).grid(row=0, column=1)
        row += 1

        # 預設下載路徑
        ttk.Label(main, text="預設下載路徑:").grid(row=row, column=0, sticky=tk.W, pady=5)
        dp_frame = ttk.Frame(main)
        dp_frame.grid(row=row, column=1, sticky="ew")
        dp_frame.columnconfigure(0, weight=1)
        ttk.Entry(dp_frame, textvariable=self.default_download_path_var, width=60).grid(
            row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(dp_frame, text="瀏覽",
                   command=lambda: self._browse_default_path(win)).grid(row=0, column=1)
        row += 1

        # 重試次數
        ttk.Label(main, text="下載失敗重試次數:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(main, from_=0, to=3, textvariable=self.retries_var,
                    width=8, wrap=True, state="readonly").grid(row=row, column=1, sticky=tk.W)
        row += 1

        # 重試延遲
        ttk.Label(main, text="重試等待秒數:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(main, from_=1, to=10, textvariable=self.delay_var,
                    width=8, wrap=True, state="readonly").grid(row=row, column=1, sticky=tk.W)
        row += 1

        # 並行下載數量
        ttk.Label(main, text="同時下載數量:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(main, from_=1, to=4, textvariable=self.parallel_var,
                    width=8, wrap=True, state="readonly").grid(row=row, column=1, sticky=tk.W)
        row += 1

        # 按鈕
        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(20, 0))
        ttk.Button(btn_frame, text="確定",
                   command=lambda: self._apply_settings(win)).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消",
                   command=win.destroy).pack(side=tk.LEFT, padx=10)

        self._center_window(win)

    # ═══════════════════════════════════════════════════════
    #  UI 建立
    # ═══════════════════════════════════════════════════════

    def _check_ffmpeg(self):
        if not (os.path.isfile(self.FFMPEG_PATH) and os.access(self.FFMPEG_PATH, os.X_OK)):
            self._log("警告: FFmpeg 路徑無效或未設定。請在'設定'中修改。")
            return False
        self._log(f"FFmpeg 路徑已設定為: {self.FFMPEG_PATH}")
        return True

    def _validate_url_length(self, *args):
        max_len = 2048
        current = self.url_var.get()
        if len(current) > max_len:
            self.url_var.set(current[:max_len])
            self._log(f"警告：貼上的網址過長，已自動截斷至 {max_len} 個字元。")

    def _create_widgets(self):
        main = ttk.Frame(self.root, padding="10")
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        row = 0

        # ── 頂部：網址 + 設定按鈕 ──
        header = ttk.Frame(main)
        header.grid(row=row, column=0, columnspan=3, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="YouTube 網址:").grid(row=0, column=0, sticky=tk.W, pady=5)
        url_entry = ttk.Entry(header, textvariable=self.url_var, width=50)
        url_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=(5, 0))

        ttk.Button(header, text="分析網址",
                   command=self._analyze_url).grid(row=0, column=2, sticky=tk.W, padx=(5, 0))
        ttk.Button(header, text="設定",
                   command=self._open_settings_window).grid(row=0, column=3, sticky=tk.E, padx=(10, 0))
        row += 1

        url_entry.bind('<FocusIn>', lambda e: (e.widget.select_range(0, 'end'), e.widget.icursor('end')))
        self._create_url_context_menu(url_entry)

        # ── 標題 ──
        ttk.Label(main, text="標題:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Label(main, textvariable=self.video_title_var, wraplength=1600,
                  anchor="w", justify=tk.LEFT).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=5, padx=(5, 0))
        row += 1

        # ── 縮圖 ──
        self.thumbnail_label = ttk.Label(main)
        self.thumbnail_label.grid(row=row, column=1, columnspan=2, pady=5)
        row += 1

        # ── 下載路徑 ──
        ttk.Label(main, text="下載路徑:").grid(row=row, column=0, sticky=tk.W, pady=5)
        path_frame = ttk.Frame(main)
        path_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        path_frame.columnconfigure(0, weight=1)
        path_entry = ttk.Entry(path_frame, textvariable=self.download_path_var)
        path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        browse_btn = ttk.Button(path_frame, text="瀏覽", command=self._browse_path)
        browse_btn.grid(row=0, column=1)
        row += 1

        # ── 字幕 + 類型 ──
        ttk.Label(main, text="字幕:").grid(row=row, column=0, sticky=tk.W, pady=5)
        opt_frame = ttk.Frame(main)
        opt_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)

        self.subtitle_combo = ttk.Combobox(
            opt_frame, textvariable=self.subtitle_var, values=["none"],
            state="readonly", width=30)
        self.subtitle_combo.pack(side=tk.LEFT, padx=(0, 50))

        type_frame = ttk.Frame(opt_frame)
        type_frame.pack(side=tk.LEFT)
        video_radio = ttk.Radiobutton(type_frame, text="影片 (MP4)",
                                      variable=self.download_type_var, value="video")
        video_radio.pack(side=tk.LEFT)
        audio_radio = ttk.Radiobutton(type_frame, text="音訊 (MP3)",
                                      variable=self.download_type_var, value="audio")
        audio_radio.pack(side=tk.LEFT, padx=(20, 0))
        row += 1

        # ── 分頁筆記本 ──
        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=10)
        main.rowconfigure(row, weight=1)
        row += 1

        # 分頁 0：影片格式
        self.formats_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.formats_frame, text="影片格式")
        self._build_formats_tab()

        # 分頁 1：頻道影片
        self.videos_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.videos_frame, text="頻道影片")
        self._build_videos_tab()

        # 分頁 2：下載歷史（新增）
        self.history_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.history_frame, text="下載歷史")
        self._build_history_tab()

        # 切換到歷史分頁時自動重新整理
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ── 進度條 ──
        ttk.Label(main, text="總進度:").grid(row=row, column=0, sticky=tk.W, pady=(10, 0))
        self.total_progress_bar = ttk.Progressbar(
            main, variable=self.total_progress_var, maximum=100)
        self.total_progress_bar.grid(row=row, column=1, columnspan=2, sticky="ew", pady=(10, 5))
        row += 1

        ttk.Label(main, text="檔案進度:").grid(row=row, column=0, sticky=tk.W)
        self.file_progress_bar = ttk.Progressbar(
            main, variable=self.file_progress_var, maximum=100)
        self.file_progress_bar.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        row += 1

        # ── 狀態 ──
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(main, textvariable=self.status_var).grid(
            row=row, column=0, columnspan=3, sticky=tk.W, pady=5)
        row += 1

        # ── 日誌 ──
        log_frame = ttk.LabelFrame(main, text="日誌", padding="5")
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main.rowconfigure(row, weight=1)
        row += 1

        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, width=70)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        # ── 下載按鈕 ──
        style = ttk.Style()
        style.configure("Large.TButton", font=('TkDefaultFont', 12, 'bold'),
                        padding=(20, 10))
        self.download_btn = ttk.Button(
            main, text="下載", command=self._start_download,
            state="disabled", style="Large.TButton")
        self.download_btn.grid(row=row, column=0, columnspan=3, pady=10)

        # 收集互動元件
        self.interactive_widgets = [
            url_entry, path_entry, browse_btn, self.subtitle_combo,
            video_radio, audio_radio, self.formats_tree, self.videos_tree,
        ]

    def _build_formats_tab(self):
        """建立「影片格式」分頁內容。"""
        self.formats_tree = ttk.Treeview(
            self.formats_frame,
            columns=("Resolution", "Video Codec", "TBR", "Has Audio", "Filesize"),
            show="headings", height=8,
        )
        for col, text in [
            ("Resolution", "解析度"), ("Video Codec", "影像編碼"),
            ("TBR", "位元率 (kbps)"), ("Has Audio", "包含音訊"),
            ("Filesize", "檔案大小"),
        ]:
            self.formats_tree.heading(col, text=text, anchor=tk.W)
        self.formats_tree.column("Resolution", width=100)
        self.formats_tree.column("Video Codec", width=100)
        self.formats_tree.column("TBR", width=100)
        self.formats_tree.column("Has Audio", width=80)
        self.formats_tree.column("Filesize", width=120)

        scrollbar = ttk.Scrollbar(self.formats_frame, orient="vertical",
                                  command=self.formats_tree.yview)
        self.formats_tree.configure(yscrollcommand=scrollbar.set)
        self.formats_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.formats_frame.columnconfigure(0, weight=1)
        self.formats_frame.rowconfigure(0, weight=1)

    def _build_videos_tab(self):
        """建立「頻道影片」分頁內容。"""
        self.videos_tree = ttk.Treeview(
            self.videos_frame, columns=("Title",), show="tree headings", height=10)
        self.videos_tree.heading("#0", text="選取", anchor=tk.CENTER)
        self.videos_tree.heading("Title", text="影片標題", anchor=tk.W)
        self.videos_tree.column("#0", width=40, anchor=tk.CENTER)
        self.videos_tree.column("Title", width=800)

        scrollbar = ttk.Scrollbar(self.videos_frame, orient="vertical",
                                  command=self.videos_tree.yview)
        self.videos_tree.configure(yscrollcommand=scrollbar.set)
        self.videos_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.videos_frame.columnconfigure(0, weight=1)
        self.videos_frame.rowconfigure(0, weight=1)

        self.videos_tree.bind('<<TreeviewSelect>>', self._on_video_select)

        btn_frame = ttk.Frame(self.videos_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=5)
        ttk.Button(btn_frame, text="全選",
                   command=self._select_all_videos).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="取消全選",
                   command=self._deselect_all_videos).grid(row=0, column=1, padx=5)

    def _build_history_tab(self):
        """建立「下載歷史」分頁內容，並讓列表寬度填滿可用空間。"""
        self.history_frame.columnconfigure(0, weight=1)
        self.history_frame.rowconfigure(0, weight=1)

        hist_tree_frame = ttk.Frame(self.history_frame)
        hist_tree_frame.grid(row=0, column=0, sticky="nsew")
        hist_tree_frame.columnconfigure(0, weight=1)
        hist_tree_frame.rowconfigure(0, weight=1)

        self.history_tree = ttk.Treeview(
            hist_tree_frame,
            columns=("Title", "Format", "Resolution", "Status", "Date"),
            show="headings", height=10,
        )
        self.history_tree.heading("Title", text="標題", anchor=tk.W)
        self.history_tree.heading("Format", text="格式", anchor=tk.CENTER)
        self.history_tree.heading("Resolution", text="解析度", anchor=tk.CENTER)
        self.history_tree.heading("Status", text="狀態", anchor=tk.CENTER)
        self.history_tree.heading("Date", text="日期", anchor=tk.CENTER)

        # Title 欄位隨視窗寬度自動伸展，其餘欄位固定寬度
        self.history_tree.column("Title", width=320, minwidth=200, stretch=True)
        self.history_tree.column("Format", width=60, stretch=False, anchor=tk.CENTER)
        self.history_tree.column("Resolution", width=80, stretch=False, anchor=tk.CENTER)
        self.history_tree.column("Status", width=60, stretch=False, anchor=tk.CENTER)
        self.history_tree.column("Date", width=140, stretch=False, anchor=tk.CENTER)

        h_scroll = ttk.Scrollbar(hist_tree_frame, orient="vertical",
                                 command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=h_scroll.set)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        h_scroll.grid(row=0, column=1, sticky="ns")

        # 統計 + 按鈕行
        ctrl_frame = ttk.Frame(self.history_frame)
        ctrl_frame.grid(row=1, column=0, sticky="ew", pady=5, padx=5)

        self.history_stats_var = tk.StringVar(value="統計：尚無記錄")
        ttk.Label(ctrl_frame, textvariable=self.history_stats_var).pack(side=tk.LEFT)

        ttk.Button(ctrl_frame, text="重新整理",
                   command=self._refresh_history).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(ctrl_frame, text="清除歷史",
                   command=self._clear_history).pack(side=tk.RIGHT)

        # 載入現有記錄
        self._refresh_history()

    def _on_tab_changed(self, event):
        """當切換到歷史分頁時自動重新整理。"""
        if self.notebook.index("current") == 2:
            self._refresh_history()

    # ═══════════════════════════════════════════════════════
    #  歷史記錄
    # ═══════════════════════════════════════════════════════

    def _refresh_history(self):
        """重新載入並顯示下載歷史。"""
        self.history_tree.delete(*self.history_tree.get_children())
        records = self.history.get_all(limit=200)
        for rec in records:
            self.history_tree.insert("", "end", values=(
                rec.get("title", "")[:80],
                rec.get("format", ""),
                rec.get("resolution", ""),
                "成功" if rec.get("status") == "success" else "失敗",
                rec.get("downloaded_at", "")[:19],
            ))
        stats = self.history.get_stats()
        size_mb = stats["total_size_bytes"] / (1024 * 1024)
        self.history_stats_var.set(
            f"統計：共 {stats['total']} 筆 | "
            f"成功 {stats['success']} / 失敗 {stats['failed']} | "
            f"總大小 {size_mb:.1f} MB"
        )

    def _clear_history(self):
        """清除所有歷史記錄（需使用者確認）。"""
        if self._ask_yesno("確認", "確定要清除所有下載歷史記錄嗎？"):
            self.history.clear()
            self._refresh_history()
            self._log("下載歷史記錄已清除。")

    def _add_history_record(self, url: str, title: str, fmt: str = "",
                            resolution: str = "", file_path: str = "",
                            status: str = "success", error_msg: str = ""):
        """新增一筆下載歷史記錄（執行緒安全：僅寫 DB，UI 更新透過佇列）。"""
        file_size = 0
        if file_path and os.path.isfile(file_path):
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                pass

        self.history.add_record(
            url=url, title=title, format_type=fmt, resolution=resolution,
            file_path=file_path, file_size=file_size,
            status=status, error_msg=error_msg,
        )

        # 透過佇列通知主執行緒更新顯示（避免從背景執行緒直接操作 tkinter）
        self.queue.put({"type": "refresh_history"})

    # ═══════════════════════════════════════════════════════
    #  右鍵選單
    # ═══════════════════════════════════════════════════════

    def _create_url_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="剪下", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="複製", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="貼上", command=lambda: widget.event_generate("<<Paste>>"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ═══════════════════════════════════════════════════════
    #  UI 狀態控制
    # ═══════════════════════════════════════════════════════

    def _set_ui_state(self, state: str):
        for widget in self.interactive_widgets:
            wtype = widget.winfo_class()
            if wtype in ('TCombobox', 'TEntry', 'TButton', 'TRadiobutton'):
                widget.config(state=state)
            elif wtype == 'Treeview':
                if state == 'disabled':
                    widget.unbind("<<TreeviewSelect>>")
                    widget.unbind("<Button-1>")
                else:
                    widget.bind("<<TreeviewSelect>>", self._on_video_select)
                    widget.bind("<Button-1>", self._toggle_video_selection)
        if state == 'normal':
            if self.available_formats or self.channel_videos:
                self.download_btn.config(state='normal')
        else:
            self.download_btn.config(state='disabled')

    def _browse_path(self):
        path = filedialog.askdirectory(initialdir=self.download_path_var.get())
        if path:
            self.download_path_var.set(path)

    def _log(self, message: str):
        """寫入 GUI 日誌區塊，並持久化到 LOG_FILE。"""
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
        self._write_log_file(message)

    def _write_log_file(self, message: str):
        """將訊息附加到持久日誌檔（執行緒安全）。"""
        try:
            timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            line = f"{timestamp} {message}\n"
            with self._log_lock:
                self._rotate_if_needed()
                with open(self.LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass  # 寫入日誌失敗不應影響主程式

    def _rotate_if_needed(self):
        """若日誌檔超過上限，裁減為原有的一半大小。"""
        try:
            if not os.path.isfile(self.LOG_FILE):
                return
            size = os.path.getsize(self.LOG_FILE)
            if size <= self.MAX_LOG_SIZE:
                return
            # 讀取全部行，保留後半
            with open(self.LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            keep = len(lines) // 2
            with open(self.LOG_FILE, "w", encoding="utf-8") as f:
                f.write("…（已自動裁減舊日誌）…\n")
                f.writelines(lines[keep:])
        except Exception:
            pass

    def _update_status(self, message: str):
        self.status_var.set(message)
        self.root.update_idletasks()

    def _center_root_window(self):
        """將主視窗置中於螢幕。"""
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 2
        self.root.geometry(f"+{x}+{y}")

    def _center_window(self, win: tk.Toplevel):
        self.root.update_idletasks()
        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")

    def _show_info(self, title: str, message: str):
        """顯示置中於主視窗的資訊對話框。"""
        self._show_message(title, message, icon="info")

    def _show_error(self, title: str, message: str):
        """顯示置中於主視窗的錯誤對話框。"""
        self._show_message(title, message, icon="error")

    def _show_message(self, title: str, message: str, icon: str = "info"):
        """顯示置中於主視窗的訊息對話框。"""
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        if icon == "error":
            emoji = "❌"
            label_fg = "#c0392b"
        else:
            emoji = "✅"
            label_fg = "#27ae60"

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=emoji, font=("Segoe UI", 24)).pack(pady=(0, 5))
        msg_label = ttk.Label(frame, text=message, wraplength=400, justify=tk.CENTER,
                              foreground=label_fg)
        msg_label.pack(pady=(0, 15))

        ttk.Button(frame, text="確定", command=dialog.destroy).pack(pady=(0, 5))

        self._center_window(dialog)
        dialog.deiconify()
        dialog.grab_set()
        self.root.wait_window(dialog)

    def _ask_yesno(self, title: str, message: str) -> bool:
        """顯示置中於主視窗的 Yes/No 對話框。"""
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        result = tk.BooleanVar(value=False)

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="❓", font=("Segoe UI", 24)).pack(pady=(0, 5))
        ttk.Label(frame, text=message, wraplength=400, justify=tk.CENTER).pack(pady=(0, 15))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(0, 5))

        ttk.Button(btn_frame, text="是", command=lambda: (result.set(True), dialog.destroy())).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="否", command=lambda: (result.set(False), dialog.destroy())).pack(
            side=tk.LEFT, padx=5)

        self._center_window(dialog)
        dialog.deiconify()
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result.get()

    def _on_closing(self):
        if self._after_id:
            self.root.after_cancel(self._after_id)
        self.root.destroy()

    # ═══════════════════════════════════════════════════════
    #  訊息佇列監聽
    # ═══════════════════════════════════════════════════════

    def _check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                mtype = msg.get("type")
                if mtype == "log":
                    self._log(msg["text"])
                elif mtype == "status":
                    self._update_status(msg["text"])
                elif mtype == "video_title":
                    self.video_title_var.set(msg["text"])
                elif mtype == "total_progress":
                    self.total_progress_var.set(msg["value"])
                elif mtype == "file_progress":
                    self.file_progress_var.set(msg["value"])
                elif mtype == "set_ui_state":
                    self._set_ui_state(msg["state"])
                elif mtype == "formats":
                    self._populate_formats(msg["data"])
                elif mtype == "subtitles":
                    self._populate_subtitles(msg["data"])
                elif mtype == "videos":
                    self._populate_videos(msg["data"])
                elif mtype == "switch_tab":
                    self.notebook.select(msg["index"])
                elif mtype == "thumbnail_url":
                    threading.Thread(target=self._display_thumbnail_worker,
                                     args=(msg["url"],), daemon=True).start()
                elif mtype == "update_thumbnail":
                    self._update_thumbnail(msg["image"])
                elif mtype == "error":
                    self._show_error("錯誤", msg["text"])
                elif mtype == "success":
                    self._show_info("成功", msg["text"])
                elif mtype == "clear_and_disable_subtitles":
                    self.subtitle_combo['values'] = []
                    self.subtitle_combo.set("")
                    self.subtitle_combo.config(state='disabled')
                elif mtype == "update_single_video_subtitles":
                    subtitles = msg["data"]
                    self.available_subtitles = subtitles
                    display_values = list(subtitles.keys())
                    self.subtitle_combo['values'] = display_values
                    self.subtitle_combo.set(display_values[0] if display_values else "無可用字幕")
                    self.subtitle_combo.config(state='readonly')
                elif mtype == "refresh_history":
                    self._refresh_history()
        except queue.Empty:
            pass
        self._after_id = self.root.after(100, self._check_queue)

    # ═══════════════════════════════════════════════════════
    #  縮圖顯示
    # ═══════════════════════════════════════════════════════

    def _display_thumbnail_worker(self, url: str):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
            img.thumbnail((320, 180))
            photo = ImageTk.PhotoImage(img)
            self.queue.put({"type": "update_thumbnail", "image": photo})
        except Exception as e:
            self._log(f"無法載入縮圖: {e}")

    def _update_thumbnail(self, photo):
        self.thumbnail_photo = photo
        self.thumbnail_label.config(image=self.thumbnail_photo)

    # ═══════════════════════════════════════════════════════
    #  網址分析
    # ═══════════════════════════════════════════════════════

    def _analyze_url(self):
        url = self.url_var.get().strip()
        if not url:
            self._show_error("錯誤", "請輸入 YouTube 網址")
            return
        if not self.FFMPEG_PATH or not os.path.isfile(self.FFMPEG_PATH):
            self._show_error(
                "FFmpeg 錯誤",
                f"FFmpeg 路徑無效。\n請在'設定'中修改路徑。",
            )
            return

        # 清除舊資料
        self.video_title_var.set("")
        self.thumbnail_label.config(image='')
        self.thumbnail_photo = None
        self.formats_tree.delete(*self.formats_tree.get_children())
        self.videos_tree.delete(*self.videos_tree.get_children())
        self.subtitle_combo['values'] = ["none"]
        self.subtitle_combo.set("none")
        self.available_formats.clear()
        self.available_subtitles.clear()
        self.channel_videos.clear()
        self.total_progress_var.set(0)
        self.file_progress_var.set(0)
        self.log_text.delete('1.0', tk.END)

        self._set_ui_state('disabled')
        self._update_status("正在分析網址...")

        threading.Thread(target=self._analyze_url_worker, args=(url,), daemon=True).start()

    def _analyze_url_worker(self, url: str):
        try:
            result = self.download_manager.analyze_url(url)

            if result["type"] == "playlist":
                self.queue.put({"type": "status", "text": "正在分析頻道/播放列表..."})
                self.queue.put({"type": "switch_tab", "index": 1})
                self.queue.put({"type": "video_title", "text": result["title"]})
                if result.get("thumbnail_url"):
                    self.queue.put({"type": "thumbnail_url", "url": result["thumbnail_url"]})
                self.queue.put({"type": "videos", "data": result["videos"]})
                self.queue.put({"type": "status", "text": f"找到 {result['video_count']} 個影片"})
                self.queue.put({"type": "clear_and_disable_subtitles"})

            else:  # single
                self.queue.put({"type": "status", "text": "正在分析影片格式..."})
                self.queue.put({"type": "switch_tab", "index": 0})
                self.queue.put({"type": "video_title", "text": result["title"]})
                if result.get("thumbnail_url"):
                    self.queue.put({"type": "thumbnail_url", "url": result["thumbnail_url"]})
                self.queue.put({"type": "formats", "data": result["formats"]})
                self.queue.put({"type": "subtitles", "data": result["subtitles"]})
                self.queue.put({"type": "status", "text": f"找到 {len(result['formats'])} 種格式"})

        except RuntimeError as e:
            self.queue.put({"type": "error", "text": str(e)})
            self.queue.put({"type": "status", "text": "分析失敗"})
        except Exception as e:
            self.queue.put({"type": "error", "text": f"發生未預期錯誤: {e}"})
            self.queue.put({"type": "status", "text": "分析失敗"})
        finally:
            self.queue.put({"type": "set_ui_state", "state": "normal"})

    def _fetch_video_details_worker(self, url: str):
        """取得頻道列表中個別影片的詳細資訊。"""
        try:
            details = self.download_manager.fetch_video_details(url)
            if details.get("thumbnail_url"):
                self.queue.put({"type": "thumbnail_url", "url": details["thumbnail_url"]})
            self.queue.put({"type": "update_single_video_subtitles",
                            "data": details["subtitles"]})
            self.queue.put({"type": "status", "text": "影片詳細資訊載入完成"})
        except Exception as e:
            self._log(f"無法獲取影片資訊: {e}")
            self.queue.put({"type": "status", "text": "影片資訊載入失敗"})
            self.queue.put({"type": "update_single_video_subtitles", "data": {'無': 'none'}})

    # ═══════════════════════════════════════════════════════
    #  資料填充
    # ═══════════════════════════════════════════════════════

    def _populate_formats(self, formats: list):
        self.formats_tree.delete(*self.formats_tree.get_children())
        self.available_formats = formats
        for resolution, vcodec, tbr, has_audio, _, filesize, _ in formats:
            self.formats_tree.insert("", "end", values=(
                resolution, vcodec, f"{tbr:.0f}" if tbr else "N/A",
                "是" if has_audio else "否", filesize,
            ))
        if formats:
            self.formats_tree.selection_set(self.formats_tree.get_children()[0])

    def _populate_subtitles(self, subtitles: dict):
        self.available_subtitles = subtitles
        display_values = list(subtitles.keys())
        self.subtitle_combo['values'] = display_values
        self.subtitle_combo.set(display_values[0] if display_values else "無可用字幕")
        self.subtitle_combo.config(state='readonly')

    def _populate_videos(self, videos: list):
        self.videos_tree.delete(*self.videos_tree.get_children())
        self.channel_videos = videos
        for title, _ in videos:
            display_title = title[:80] + "..." if len(title) > 80 else title
            self.videos_tree.insert("", "end", text="☐", values=(display_title,))

    # ═══════════════════════════════════════════════════════
    #  頻道影片選擇
    # ═══════════════════════════════════════════════════════

    def _select_all_videos(self):
        for item in self.videos_tree.get_children():
            self.videos_tree.item(item, text="☑")

    def _deselect_all_videos(self):
        for item in self.videos_tree.get_children():
            self.videos_tree.item(item, text="☐")

    def _get_selected_videos(self) -> list:
        selected = []
        for item in self.videos_tree.get_children():
            if self.videos_tree.item(item, "text") == "☑":
                index = self.videos_tree.index(item)
                selected.append(self.channel_videos[index])
        return selected

    def _toggle_video_selection(self, event):
        item = self.videos_tree.identify_row(event.y)
        if not item:
            return
        if self.videos_tree.identify_region(event.x, event.y) == 'tree':
            current = self.videos_tree.item(item, "text")
            self.videos_tree.item(item, text="☑" if current == "☐" else "☐")

    def _on_video_select(self, event):
        selected = self.videos_tree.selection()
        if not selected:
            return
        index = self.videos_tree.index(selected[0])
        if index >= len(self.channel_videos):
            return
        _, video_url = self.channel_videos[index]
        if video_url:
            self.queue.put({"type": "status", "text": "正在讀取影片詳細資訊..."})
            self.subtitle_combo.set("讀取中...")
            self.subtitle_combo.config(state='disabled')
            threading.Thread(target=self._fetch_video_details_worker,
                             args=(video_url,), daemon=True).start()

    # ═══════════════════════════════════════════════════════
    #  下載邏輯
    # ═══════════════════════════════════════════════════════

    def _start_download(self):
        is_playlist = bool(self.channel_videos)
        download_path = self.download_path_var.get()

        if not os.path.exists(download_path):
            self._show_error("錯誤", "下載路徑不存在")
            return
        if is_playlist:
            if not self._get_selected_videos():
                self._show_error("錯誤", "請至少選擇一個要下載的影片")
                return
        elif (self.download_type_var.get() == "video"
              and not self.formats_tree.selection()):
            self._show_error("錯誤", "請選擇一個影片格式")
            return

        self._set_ui_state('disabled')
        self.total_progress_var.set(0)
        self.file_progress_var.set(0)
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        try:
            download_path = self.download_path_var.get()
            subtitle_key = self.subtitle_var.get()
            subtitle_lang = (
                self.available_subtitles.get(subtitle_key)
                if subtitle_key not in ["none", "無"] else None
            )
            is_playlist = bool(self.channel_videos)

            if is_playlist:
                self._download_playlist(download_path, subtitle_lang)
            else:
                self._download_single(download_path, subtitle_lang)

            self.queue.put({"type": "status", "text": "下載已完成"})
        except Exception as e:
            self.queue.put({"type": "error", "text": f"下載失敗: {e}"})
            self.queue.put({"type": "status", "text": "下載失敗"})
        finally:
            self.queue.put({"type": "set_ui_state", "state": "normal"})

    def _download_playlist(self, download_path: str, subtitle_lang):
        """使用並行下載處理播放清單。"""
        selected_videos = self._get_selected_videos()
        total = len(selected_videos)
        self._log(f"準備下載 {total} 個選定的影片（同時進行 {self.PARALLEL_DOWNLOADS} 個）...")

        for i, (title, video_url) in enumerate(selected_videos):
            self._put_initial_progress(i, total, title)

        self.download_manager.parallel_downloads = self.PARALLEL_DOWNLOADS
        result = self.download_manager.download_playlist_parallel(
            selected_videos, download_path, subtitle_lang,
        )

        # 寫入歷史記錄
        for r in result["results"]:
            self._add_history_record(
                url=r["url"], title=r["title"], fmt="MP4",
                resolution="1080p", file_path=r["file_path"],
                status=r["status"], error_msg=r.get("error", ""),
            )

        self.queue.put({"type": "success",
                        "text": f"下載完成！\n成功: {result['success']}, 失敗: {result['failed']}"})

    def _download_single(self, download_path: str, subtitle_lang):
        """處理單一影片下載。"""
        url = self.url_var.get().strip()
        title = self.video_title_var.get()
        self.queue.put({"type": "file_progress", "value": 0})
        self.queue.put({"type": "total_progress", "value": 0})

        file_path = ""
        resolution = ""
        fmt_type = "MP4"

        if self.download_type_var.get() == "video":
            selection = self.formats_tree.selection()[0]
            index = self.formats_tree.index(selection)
            format_data = self.available_formats[index]
            resolution, _, _, has_audio, height, _, format_id = format_data

            self._log(f"--- 開始下載 {resolution} 的影片... ---")
            self._update_status("正在下載影片...")

            file_path = self.download_manager.download_video(
                url, format_id, has_audio, download_path, subtitle_lang, height,
            )
            fmt_type = "MP4"
        else:
            self._log("--- 開始下載音訊為 MP3... ---")
            self._update_status("正在下載音訊...")
            self.download_manager.download_audio(url, download_path, subtitle_lang)
            fmt_type = "MP3"

        self._log(f"--- ✔ 下載成功完成: {title} ---")
        self.queue.put({"type": "total_progress", "value": 100})
        self.queue.put({"type": "success", "text": "下載成功完成"})

        # 寫入歷史記錄
        self._add_history_record(
            url=url, title=title, fmt=fmt_type,
            resolution=resolution, file_path=file_path, status="success",
        )

    def _put_initial_progress(self, index: int, total: int, title: str):
        """初始化進度顯示。"""
        self.queue.put({"type": "file_progress", "value": 0})
        self.queue.put({"type": "log", "text": f"--- 開始下載 ({index+1}/{total}): {title[:50]}... ---"})
        self.queue.put({"type": "status", "text": f"正在下載 {index+1}/{total}: {title[:30]}..."})
