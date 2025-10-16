import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from PIL import Image, ImageTk
from operator import itemgetter
from io import BytesIO
import yt_dlp
import os
import sys
import re
import threading
import queue
import time
import requests
import json
import subprocess

# --- 注意 ---
# 此腳本需要安裝 yt-dlp, requests 和 Pillow 函式庫
# pip install yt-dlp requests pillow

# +++ YtdlpLogger 類別 +++
# 功能：攔截 yt-dlp 的日誌訊息並傳送到 GUI 的佇列
class YtdlpLogger:
    def __init__(self, queue):
        self.queue = queue

    def debug(self, msg):
        # 忽略詳細的除錯訊息，除非需要進行深入除錯
        if msg.startswith('[debug] '):
            pass
        else:
            self.info(msg)

    def info(self, msg):
        # 將 yt-dlp 的一般訊息傳送到日誌窗口
        self.queue.put({"type": "log", "text": f"[yt-dlp] {msg}"})

    def warning(self, msg):
        self.queue.put({"type": "log", "text": f"[yt-dlp 警告] {msg}"})

    def error(self, msg):
        self.queue.put({"type": "log", "text": f"[yt-dlp 錯誤] {msg}"})

class YouTubeDownloaderGUI:
    # GUI版本的YouTube下載器，使用 tkinter และ ttk
    
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube 下載器 - Designed by Lamsir")
        self.root.geometry("900x900") 
        self.root.resizable(True, True)
        
        # 用於線程間通信的佇列
        self.queue = queue.Queue()

        # --- 設定 ---
        self.SETTINGS_FILE = "yd_settings.json"
        # 這些值將由 _load_settings 方法載入或設定預設值
        self.FFMPEG_PATH = ""
        self.DOWNLOAD_RETRIES = 2
        self.RETRY_DELAY = 5
        self.DEFAULT_DOWNLOAD_PATH = os.getcwd()

        # 變數
        self.url_var = tk.StringVar()
        self.video_title_var = tk.StringVar()
        self.download_path_var = tk.StringVar() # 將在 _load_settings 中設定初始值
        self.download_type_var = tk.StringVar(value="video")
        self.subtitle_var = tk.StringVar(value="none")
        self.total_progress_var = tk.DoubleVar()
        self.file_progress_var = tk.DoubleVar()

        # 設定專用的 tk 變數
        self.ffmpeg_path_var = tk.StringVar()
        self.retries_var = tk.IntVar()
        self.delay_var = tk.IntVar()
        self.default_download_path_var = tk.StringVar()
        
        # 資料儲存
        self.available_formats = []
        self.available_subtitles = {}
        self.channel_videos = []
        self.thumbnail_photo = None # 防止圖片被垃圾回收
        
        # 在下載過程中需要禁用的互動式小工具列表
        self.interactive_widgets = []

        # 先載入設定，再創建小工具
        self._load_settings()
        self._create_widgets()

        # --- 新增：啟動時檢查 yt-dlp 更新 ---
        self._start_yt_dlp_update()

        # 檢查 ffmpeg 並啟動佇列監聽
        if self._check_ffmpeg():
            self._log(f"FFmpeg 路徑已設定為: {self.FFMPEG_PATH}")
        self._check_queue()
        
        # 新增：為網址變數新增追蹤，以驗證長度
        self.url_var.trace_add("write", self._validate_url_length)

    def _start_yt_dlp_update(self):
        """在單獨的線程中開始檢查 yt-dlp 的更新。"""
        self.queue.put({"type": "log", "text": "--- 正在檢查 yt-dlp 更新 ---"})
        update_thread = threading.Thread(target=self._update_yt_dlp_worker, daemon=True)
        update_thread.start()

    def _update_yt_dlp_worker(self):
        """工作函數，用於執行 yt-dlp 的 pip 升級命令。"""
        try:
            # 使用 sys.executable 確保我們使用的是正確 Python 環境中的 pip
            command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            
            # 執行命令，並將 stdout 和 stderr 一起捕獲
            result = subprocess.check_output(command, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
            
            # 將輸出逐行發送到日誌，以提高可讀性
            for line in result.strip().split('\n'):
                 self.queue.put({"type": "log", "text": line})
            self.queue.put({"type": "log", "text": "--- yt-dlp 更新檢查完成 ---"})

        except FileNotFoundError:
            self.queue.put({"type": "log", "text": "錯誤：找不到 Python/pip 命令。無法自動更新 yt-dlp。"})
            self.queue.put({"type": "log", "text": "--- yt-dlp 更新檢查失敗 ---"})
        except subprocess.CalledProcessError as e:
            self.queue.put({"type": "log", "text": f"yt-dlp 更新失敗，錯誤碼 {e.returncode}:"})
            for line in e.output.strip().split('\n'):
                self.queue.put({"type": "log", "text": line})
            self.queue.put({"type": "log", "text": "--- yt-dlp 更新檢查失敗 ---"})
        except Exception as e:
            self.queue.put({"type": "log", "text": f"檢查 yt-dlp 更新時發生未知錯誤: {e}"})
            self.queue.put({"type": "log", "text": "--- yt-dlp 更新檢查失敗 ---"})

    def _validate_url_length(self, *args):
        MAX_URL_LENGTH = 2048 # 設定合理的URL最大長度
        current_url = self.url_var.get()
        if len(current_url) > MAX_URL_LENGTH:
            self.url_var.set(current_url[:MAX_URL_LENGTH])
            self._log(f"警告：貼上的網址過長，已自動截斷至 {MAX_URL_LENGTH} 個字元。")


    def _load_settings(self):
        try:
            with open(self.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            # 使用 .get() 來提供預設值，以防某些鍵遺失
            self.FFMPEG_PATH = settings.get('ffmpeg_path', '')
            self.DOWNLOAD_RETRIES = settings.get('retries', 2)
            self.RETRY_DELAY = settings.get('delay', 5)
            self.DEFAULT_DOWNLOAD_PATH = settings.get('default_download_path', os.getcwd())
            
            print(f"設定已從 {self.SETTINGS_FILE} 載入。")
        except (FileNotFoundError, json.JSONDecodeError):
            print("未找到設定檔或格式錯誤，使用預設值。")
            # 如果檔案不存在或無效，則使用預設值
            self.FFMPEG_PATH = ""
            self.DOWNLOAD_RETRIES = 2
            self.RETRY_DELAY = 5
            self.DEFAULT_DOWNLOAD_PATH = os.getcwd()

        # 更新將在UI中使用或設定視窗中使用的 tk 變數
        self.ffmpeg_path_var.set(self.FFMPEG_PATH)
        self.retries_var.set(self.DOWNLOAD_RETRIES)
        self.delay_var.set(self.RETRY_DELAY)
        self.default_download_path_var.set(self.DEFAULT_DOWNLOAD_PATH)
        self.download_path_var.set(self.DEFAULT_DOWNLOAD_PATH) # 將主介面的下載路徑設為預設值

    def _save_settings(self):
        settings = {
            'ffmpeg_path': self.FFMPEG_PATH,
            'retries': self.DOWNLOAD_RETRIES,
            'delay': self.RETRY_DELAY,
            'default_download_path': self.DEFAULT_DOWNLOAD_PATH
        }
        try:
            with open(self.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
            self._log(f"設定已儲存至 {self.SETTINGS_FILE}。")
        except Exception as e:
            self.queue.put({"type": "error", "text": f"無法儲存設定: {e}"})

    def _apply_settings(self, settings_window):
        new_ffmpeg_path = self.ffmpeg_path_var.get()
        new_default_path = self.default_download_path_var.get()

        # 驗證路徑
        if new_ffmpeg_path and not (os.path.isfile(new_ffmpeg_path) and os.access(new_ffmpeg_path, os.X_OK)):
            messagebox.showerror("路徑錯誤", f"指定的 FFmpeg 路徑無效或檔案無法執行:\n{new_ffmpeg_path}", parent=settings_window)
            return
        
        if not os.path.isdir(new_default_path):
             messagebox.showerror("路徑錯誤", f"指定的預設下載路徑無效:\n{new_default_path}", parent=settings_window)
             return

        # 更新應用程式內的設定值
        self.FFMPEG_PATH = new_ffmpeg_path
        self.DOWNLOAD_RETRIES = self.retries_var.get()
        self.RETRY_DELAY = self.delay_var.get()
        self.DEFAULT_DOWNLOAD_PATH = new_default_path

        # 更新主介面的下載路徑
        self.download_path_var.set(self.DEFAULT_DOWNLOAD_PATH)

        self._save_settings()
        self._log(f"設定已更新。")
        
        # 重新檢查路徑
        self._check_ffmpeg()

        settings_window.destroy()

    def _browse_ffmpeg_path(self, parent):
        path = filedialog.askopenfilename(
            parent=parent,
            title="選取 ffmpeg.exe",
            initialdir=os.path.dirname(self.ffmpeg_path_var.get()) if self.ffmpeg_path_var.get() else "/",
            filetypes=[("Executable files", "*.exe"), ("All files", "*.*")]
        )
        if path:
            self.ffmpeg_path_var.set(path)

    def _browse_default_path(self, parent):
        path = filedialog.askdirectory(
            parent=parent,
            title="選取預設下載路徑",
            initialdir=self.default_download_path_var.get()
        )
        if path:
            self.default_download_path_var.set(path)


    def _open_settings_window(self):
        # 在打開前確保 tk 變數反映當前的設定
        self.ffmpeg_path_var.set(self.FFMPEG_PATH)
        self.retries_var.set(self.DOWNLOAD_RETRIES)
        self.delay_var.set(self.RETRY_DELAY)
        self.default_download_path_var.set(self.DEFAULT_DOWNLOAD_PATH)

        settings_window = tk.Toplevel(self.root)
        settings_window.title("設定")
        settings_window.geometry("600x200")
        settings_window.transient(self.root) 
        settings_window.grab_set() 

        main_frame = ttk.Frame(settings_window, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        settings_window.columnconfigure(0, weight=1)
        settings_window.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)

        row_index = 0
        # FFmpeg 路徑
        ttk.Label(main_frame, text="FFmpeg 目錄:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        ffmpeg_frame = ttk.Frame(main_frame)
        ffmpeg_frame.grid(row=row_index, column=1, sticky=(tk.W, tk.E))
        ffmpeg_frame.columnconfigure(0, weight=1)
        ffmpeg_entry = ttk.Entry(ffmpeg_frame, textvariable=self.ffmpeg_path_var, width=60)
        ffmpeg_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        ffmpeg_browse_btn = ttk.Button(ffmpeg_frame, text="瀏覽", command=lambda: self._browse_ffmpeg_path(settings_window))
        ffmpeg_browse_btn.grid(row=0, column=1)
        row_index += 1

        # 預設下載路徑
        ttk.Label(main_frame, text="預設下載路徑:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        default_path_frame = ttk.Frame(main_frame)
        default_path_frame.grid(row=row_index, column=1, sticky=(tk.W, tk.E))
        default_path_frame.columnconfigure(0, weight=1)
        default_path_entry = ttk.Entry(default_path_frame, textvariable=self.default_download_path_var, width=60)
        default_path_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        default_path_browse_btn = ttk.Button(default_path_frame, text="瀏覽", command=lambda: self._browse_default_path(settings_window))
        default_path_browse_btn.grid(row=0, column=1)
        row_index += 1

        # 重試次數
        ttk.Label(main_frame, text="下載失敗重試次數:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        retries_spinbox = ttk.Spinbox(main_frame, from_=0, to=3, textvariable=self.retries_var, width=8, wrap=True, state="readonly")
        retries_spinbox.grid(row=row_index, column=1, sticky=tk.W)
        row_index += 1

        # 重試延遲
        ttk.Label(main_frame, text="重試等待秒數:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        delay_spinbox = ttk.Spinbox(main_frame, from_=1, to=10, textvariable=self.delay_var, width=8, wrap=True, state="readonly")
        delay_spinbox.grid(row=row_index, column=1, sticky=tk.W)
        row_index += 1

        # 按鈕
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row_index, column=0, columnspan=2, pady=(20, 0))

        ok_btn = ttk.Button(button_frame, text="確定", command=lambda: self._apply_settings(settings_window))
        ok_btn.pack(side=tk.LEFT, padx=10)

        cancel_btn = ttk.Button(button_frame, text="取消", command=settings_window.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=10)
        
        # --- 新增：置中視窗 ---
        self.root.update_idletasks() 
        settings_window.update_idletasks() 
        main_win_x = self.root.winfo_x()
        main_win_y = self.root.winfo_y()
        main_win_width = self.root.winfo_width()
        main_win_height = self.root.winfo_height()

        settings_win_width = settings_window.winfo_width()
        settings_win_height = settings_window.winfo_height()

        center_x = main_win_x + (main_win_width - settings_win_width) // 2
        center_y = main_win_y + (main_win_height - settings_win_height) // 2

        settings_window.geometry(f"+{center_x}+{center_y}")
        
    def _check_ffmpeg(self):
        # 檢查指定的路徑中是否存在 ffmpeg
        if not (os.path.isfile(self.FFMPEG_PATH) and os.access(self.FFMPEG_PATH, os.X_OK)):
            # 不再使用 messagebox，而是記錄日誌，避免啟動時卡住
            self._log("警告: FFmpeg 路徑無效或未設定。請在'設定'中修改。")
            return False
        return True
    
    def _create_widgets(self):
        # 創建並排列 GUI 小工具
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
        # --- UI佈局調整 ---
        row_index = 0
        
        # 修改: 將設定按鈕放在右上角
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=row_index, column=0, columnspan=3, sticky=(tk.W, tk.E))
        header_frame.columnconfigure(1, weight=1)

        ttk.Label(header_frame, text="YouTube 網址:").grid(row=0, column=0, sticky=tk.W, pady=5)
        url_entry = ttk.Entry(header_frame, textvariable=self.url_var, width=50)
        url_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5, padx=(5, 0))
        
        analyze_btn = ttk.Button(header_frame, text="分析網址", command=self._analyze_url)
        analyze_btn.grid(row=0, column=2, sticky=tk.W, padx=(5,0))
        
        settings_btn = ttk.Button(header_frame, text="設定", command=self._open_settings_window)
        settings_btn.grid(row=0, column=3, sticky=tk.E, padx=(10,0))
        row_index += 1

        # 當點擊輸入框時，全選文字
        def select_all_on_focus(event):
            event.widget.select_range(0, 'end')
            event.widget.icursor('end')
        url_entry.bind('<FocusIn>', select_all_on_focus)
        
        self._create_url_context_menu(url_entry)
        
        ttk.Label(main_frame, text="標題:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        title_label = ttk.Label(main_frame, textvariable=self.video_title_var, wraplength=1600, anchor="w", justify=tk.LEFT)
        title_label.grid(row=row_index, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5, padx=(5,0))
        row_index += 1
        
        self.thumbnail_label = ttk.Label(main_frame)
        self.thumbnail_label.grid(row=row_index, column=1, columnspan=2, pady=5)
        row_index += 1

        ttk.Label(main_frame, text="下載路徑:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        path_frame = ttk.Frame(main_frame)
        path_frame.grid(row=row_index, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        path_frame.columnconfigure(0, weight=1)
        path_entry = ttk.Entry(path_frame, textvariable=self.download_path_var)
        path_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        browse_btn = ttk.Button(path_frame, text="瀏覽", command=self._browse_path)
        browse_btn.grid(row=0, column=1)
        row_index += 1

        ttk.Label(main_frame, text="字幕:").grid(row=row_index, column=0, sticky=tk.W, pady=5)
        options_frame = ttk.Frame(main_frame)
        options_frame.grid(row=row_index, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        self.subtitle_combo = ttk.Combobox(options_frame, textvariable=self.subtitle_var, values=["none"], state="readonly", width=30)
        self.subtitle_combo.pack(side=tk.LEFT, padx=(0, 50))

        type_frame = ttk.Frame(options_frame)
        type_frame.pack(side=tk.LEFT)
        video_radio = ttk.Radiobutton(type_frame, text="影片 (MP4)", variable=self.download_type_var, value="video")
        video_radio.pack(side=tk.LEFT)
        audio_radio = ttk.Radiobutton(type_frame, text="音訊 (MP3)", variable=self.download_type_var, value="audio")
        audio_radio.pack(side=tk.LEFT, padx=(20, 0))
        row_index += 1
        
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=row_index, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        main_frame.rowconfigure(row_index, weight=1)
        row_index += 1
        
        self.formats_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.formats_frame, text="影片格式")
        
        self.formats_tree = ttk.Treeview(self.formats_frame, columns=("Resolution", "Video Codec", "TBR", "Has Audio", "Filesize"), show="headings", height=8)
        self.formats_tree.heading("Resolution", text="解析度", anchor=tk.W)
        self.formats_tree.heading("Video Codec", text="影像編碼", anchor=tk.W)
        self.formats_tree.heading("TBR", text="位元率 (kbps)", anchor=tk.W)
        self.formats_tree.heading("Has Audio", text="包含音訊", anchor=tk.W)
        self.formats_tree.heading("Filesize", text="檔案大小", anchor=tk.W)
        
        self.formats_tree.column("Resolution", width=100)
        self.formats_tree.column("Video Codec", width=100)
        self.formats_tree.column("TBR", width=100)
        self.formats_tree.column("Has Audio", width=80)
        self.formats_tree.column("Filesize", width=120)
        
        formats_scrollbar = ttk.Scrollbar(self.formats_frame, orient="vertical", command=self.formats_tree.yview)
        self.formats_tree.configure(yscrollcommand=formats_scrollbar.set)
        self.formats_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        formats_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.formats_frame.columnconfigure(0, weight=1)
        self.formats_frame.rowconfigure(0, weight=1)
        
        self.videos_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.videos_frame, text="頻道影片")
        
        self.videos_tree = ttk.Treeview(self.videos_frame, columns=("Title",), show="tree headings", height=10)
        self.videos_tree.heading("#0", text="選取", anchor=tk.CENTER)
        self.videos_tree.heading("Title", text="影片標題", anchor=tk.W)
        self.videos_tree.column("#0", width=40, anchor=tk.CENTER)
        self.videos_tree.column("Title", width=800)
        videos_scrollbar = ttk.Scrollbar(self.videos_frame, orient="vertical", command=self.videos_tree.yview)
        self.videos_tree.configure(yscrollcommand=videos_scrollbar.set)
        self.videos_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        videos_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.videos_frame.columnconfigure(0, weight=1)
        self.videos_frame.rowconfigure(0, weight=1)

        self.videos_tree.bind('<<TreeviewSelect>>', self._on_video_select)
        
        video_btn_frame = ttk.Frame(self.videos_frame)
        video_btn_frame.grid(row=1, column=0, columnspan=2, pady=5)
        select_all_btn = ttk.Button(video_btn_frame, text="全選", command=self._select_all_videos)
        select_all_btn.grid(row=0, column=0, padx=5)
        deselect_all_btn = ttk.Button(video_btn_frame, text="取消全選", command=self._deselect_all_videos)
        deselect_all_btn.grid(row=0, column=1, padx=5)
        
        ttk.Label(main_frame, text="總進度:").grid(row=row_index, column=0, sticky=tk.W, pady=(10, 0))
        self.total_progress_bar = ttk.Progressbar(main_frame, variable=self.total_progress_var, maximum=100)
        self.total_progress_bar.grid(row=row_index, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 5))
        row_index += 1

        ttk.Label(main_frame, text="檔案進度:").grid(row=row_index, column=0, sticky=tk.W)
        self.file_progress_bar = ttk.Progressbar(main_frame, variable=self.file_progress_var, maximum=100)
        self.file_progress_bar.grid(row=row_index, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        row_index += 1

        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(main_frame, textvariable=self.status_var).grid(row=row_index, column=0, columnspan=3, sticky=tk.W, pady=5)
        row_index += 1
        
        log_frame = ttk.LabelFrame(main_frame, text="日誌", padding="5")
        log_frame.grid(row=row_index, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(row_index, weight=1)
        row_index += 1
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, width=70)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # --- 變更 2：設定按鈕樣式並應用 ---
        # 為下載按鈕設定自訂樣式
        style = ttk.Style()
        style.configure("Large.TButton", font=('TkDefaultFont', 12, 'bold'), padding=(20, 10))

        self.download_btn = ttk.Button(main_frame, text="下載", command=self._start_download, state="disabled", style="Large.TButton")
        self.download_btn.grid(row=row_index, column=0, columnspan=3, pady=10)

        self.interactive_widgets = [url_entry, analyze_btn, settings_btn, path_entry, browse_btn, self.subtitle_combo, video_radio, audio_radio, self.formats_tree, self.videos_tree, select_all_btn, deselect_all_btn]

    def _create_url_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="剪下", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="複製", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="貼上", command=lambda: widget.event_generate("<<Paste>>"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _set_ui_state(self, state):
        for widget in self.interactive_widgets:
            widget_type = widget.winfo_class()
            if widget_type in ('TCombobox', 'TEntry', 'TButton', 'TRadiobutton'):
                widget.config(state=state)
            elif widget_type == 'Treeview':
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
    
    def _log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
    
    def _update_status(self, message):
        self.status_var.set(message)
        self.root.update_idletasks()
    
    def _check_queue(self):
        try:
            while True:
                message = self.queue.get_nowait()
                msg_type = message.get("type")
                if msg_type == "log": self._log(message["text"])
                elif msg_type == "status": self._update_status(message["text"])
                elif msg_type == "video_title": self.video_title_var.set(message["text"])
                elif msg_type == "total_progress": self.total_progress_var.set(message["value"])
                elif msg_type == "file_progress": self.file_progress_var.set(message["value"])
                elif msg_type == "set_ui_state": self._set_ui_state(message["state"])
                elif msg_type == "formats": self._populate_formats(message["data"])
                elif msg_type == "subtitles": self._populate_subtitles(message["data"])
                elif msg_type == "videos": self._populate_videos(message["data"])
                elif msg_type == "switch_tab": self.notebook.select(message["index"])
                elif msg_type == "thumbnail_url": threading.Thread(target=self._display_thumbnail_worker, args=(message["url"],)).start()
                elif msg_type == "update_thumbnail": self._update_thumbnail(message["image"])
                elif msg_type == "error": messagebox.showerror("錯誤", message["text"], parent=self.root)
                elif msg_type == "success": messagebox.showinfo("成功", message["text"], parent=self.root)
                elif msg_type == "clear_and_disable_subtitles":
                    self.subtitle_combo['values'] = []
                    self.subtitle_combo.set("")
                    self.subtitle_combo.config(state='disabled')
                elif msg_type == "update_single_video_subtitles":
                    subtitles = message["data"]
                    self.available_subtitles = subtitles
                    display_values = list(subtitles.keys())
                    self.subtitle_combo['values'] = display_values
                    if display_values:
                        self.subtitle_combo.set(display_values[0])
                    else:
                        self.subtitle_combo.set("無可用字幕")
                    self.subtitle_combo.config(state='readonly')

        except queue.Empty: pass
        self.root.after(100, self._check_queue)
    
    def _simplify_codec(self, codec):
        if not codec or codec == 'none': return 'none'
        codec = codec.lower()
        if codec.startswith('vp09'): return 'vp9'
        if codec.startswith('av01'): return 'av1'
        if codec.startswith('avc1'): return 'h264'
        return codec
    
    def _analyze_url(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("錯誤", "請輸入 YouTube 網址", parent=self.root)
            return
        
        if not self.FFMPEG_PATH or not os.path.isfile(self.FFMPEG_PATH):
            messagebox.showerror("FFmpeg 錯誤", f"FFmpeg 路徑無效。\n請在'設定'中修改路徑。", parent=self.root)
            return

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
        
        thread = threading.Thread(target=self._analyze_url_worker, args=(url,))
        thread.daemon = True
        thread.start()
    
    def _analyze_url_worker(self, url):
        logger = YtdlpLogger(self.queue)
        try:
            is_channel_url = bool(re.search(r'/(channel|user|c/|@)', url, re.IGNORECASE))
            is_playlist_url = 'list=' in url
            is_playlist_like = False
            url_to_fetch = url

            if is_channel_url and not is_playlist_url:
                self.queue.put({"type": "log", "text": "偵測到頻道網址，正在嘗試轉換為穩定的上傳列表..."})
                try:
                    # 執行一次輕量級請求以獲取頻道的中繼資料，主要是為了 channel_id
                    with yt_dlp.YoutubeDL({'quiet': True, 'logger': logger}) as ydl:
                        # process=False 已棄用，但此請求通常很快
                        info = ydl.extract_info(url, download=False, process=False) 
                        channel_id = info.get('channel_id') or info.get('id')

                    if not channel_id or not channel_id.startswith('UC'):
                        raise yt_dlp.utils.DownloadError("無法從網址解析有效的頻道 ID (UC...)")

                    # 將頻道 ID (UC...) 轉換為上傳列表 ID (UU...)
                    uploads_playlist_id = 'UU' + channel_id[2:]
                    url_to_fetch = f'https://www.youtube.com/playlist?list={uploads_playlist_id}'
                    self.queue.put({"type": "log", "text": f"成功轉換！正在掃描上傳列表：{url_to_fetch}"})
                    is_playlist_like = True

                except Exception as e:
                    self.queue.put({"type": "log", "text": f"警告：無法自動轉換為上傳列表 ({e})。"})
                    self.queue.put({"type": "log", "text": "將回退至直接掃描影片分頁，此方法可能不穩定。"})
                    url_to_fetch = url.rstrip('/') + '/videos'
                    is_playlist_like = True

            elif is_playlist_url:
                self.queue.put({"type": "log", "text": "偵測到播放列表網址，正在掃描..."})
                is_playlist_like = True

            if is_playlist_like:
                ydl_opts = {
                    'extract_flat': True,
                    'noplaylist': False,
                    'logger': logger
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_to_fetch, download=False)

                self.queue.put({"type": "status", "text": "正在分析頻道/播放列表..."})
                self.queue.put({"type": "switch_tab", "index": 1})

                title = info.get('title', '未知標題')
                thumbnail_url = info.get('thumbnail')
                self.queue.put({"type": "video_title", "text": title})
                if thumbnail_url:
                    self.queue.put({"type": "thumbnail_url", "url": thumbnail_url})

                videos = self._get_channel_videos(info)
                self.queue.put({"type": "videos", "data": videos})
                self.queue.put({"type": "status", "text": f"找到 {len(videos)} 個影片"})
                self.queue.put({"type": "clear_and_disable_subtitles"})
            else:
                ydl_opts = {
                    'ffmpeg_location': self.FFMPEG_PATH,
                    'noplaylist': True,
                    'logger': logger
                }
                self.queue.put({"type": "log", "text": "偵測到單一影片網址，正在獲取詳細資訊..."})
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_to_fetch, download=False)
                
                self.queue.put({"type": "status", "text": "正在分析影片格式..."})
                self.queue.put({"type": "switch_tab", "index": 0})

                title = info.get('title', '未知標題')
                thumbnail_url = info.get('thumbnail')
                self.queue.put({"type": "video_title", "text": title})
                if thumbnail_url:
                    self.queue.put({"type": "thumbnail_url", "url": thumbnail_url})

                formats = self._get_formats(info)
                subtitles = self._get_subtitles(url, info=info)
                self.queue.put({"type": "formats", "data": formats})
                self.queue.put({"type": "subtitles", "data": subtitles})
                self.queue.put({"type": "status", "text": f"找到 {len(formats)} 種格式"})
        except yt_dlp.utils.DownloadError as e:
            self.queue.put({"type": "error", "text": f"分析錯誤: {e.msg}"})
            self.queue.put({"type": "status", "text": "分析失敗"})
        except Exception as e:
            self.queue.put({"type": "error", "text": f"發生未預期錯誤: {e}"})
            self.queue.put({"type": "status", "text": "分析失敗"})
        finally:
            self.queue.put({"type": "set_ui_state", "state": "normal"})

    def _display_thumbnail_worker(self, url):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            img_data = response.content
            img = Image.open(BytesIO(img_data))
            img.thumbnail((320, 180))
            photo = ImageTk.PhotoImage(img)
            self.queue.put({"type": "update_thumbnail", "image": photo})
        except Exception as e:
            self.queue.put({"type": "log", "text": f"無法載入縮圖: {e}"})


    def _update_thumbnail(self, photo):
        self.thumbnail_photo = photo
        self.thumbnail_label.config(image=self.thumbnail_photo)

    def _get_formats(self, info):
        formats = info.get('formats', [])
        mp4_formats = [f for f in formats if f.get('ext') == 'mp4' and f.get('vcodec') != 'none']
        resolutions = []
        for fmt in mp4_formats:
            resolution = fmt.get('resolution', f"{fmt.get('width', '未知')}x{fmt.get('height', '未知')}")
            vcodec = self._simplify_codec(fmt.get('vcodec', '未知'))
            tbr = fmt.get('tbr')
            has_audio = fmt.get('acodec') is not None and fmt.get('acodec') != 'none'
            height = fmt.get('height', 0)
            format_id = fmt.get('format_id', '未知')
            filesize = fmt.get('filesize') or fmt.get('filesize_approx')
            if not filesize and tbr and info.get('duration'):
                filesize = (tbr * info.get('duration', 0) * 1000) / 8
            filesize = filesize or 0
            if resolution != 'audio only' and vcodec != '未知':
                filesize_str = f"{filesize/1024/1024:.1f} MB" if filesize > 0 else "未知"
                resolutions.append((resolution, vcodec, tbr, has_audio, height, filesize_str, format_id))
        resolutions.sort(key=itemgetter(4), reverse=True)
        return resolutions[:30]
    
    def _get_subtitles(self, url, info=None):
        if info is None:
            ydl_opts = {'listsubtitles': True, 'ffmpeg_location': self.FFMPEG_PATH, 'quiet': True, 'noplaylist': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                except Exception:
                    return {'無': 'none'}
        
        subtitles = info.get('subtitles', {})
        available_subtitles = { '無': 'none' }
        for lang, sub_info in subtitles.items():
            if lang.startswith('en') or lang.startswith('zh'):
                is_auto = any('auto' in s.get('name', '').lower() for s in sub_info)
                if not is_auto:
                   lang_name = lang
                   if lang == "en": lang_name = "英文"
                   elif lang == "zh-Hant": lang_name = "繁體中文"
                   elif lang == "zh-Hans": lang_name = "簡體中文"
                   available_subtitles[f"{lang_name} (手動)"] = lang
        return available_subtitles

    def _get_channel_videos(self, info):
        if 'entries' not in info or not info['entries']:
            return []
        entries = filter(None, info['entries'])
        return [(entry.get('title', '無標題'), entry.get('webpage_url', entry.get('url'))) for entry in entries if entry.get('webpage_url') or entry.get('url')]

    def _populate_formats(self, formats):
        self.formats_tree.delete(*self.formats_tree.get_children())
        self.available_formats = formats
        for _, (resolution, vcodec, tbr, has_audio, _, filesize, _) in enumerate(formats):
            self.formats_tree.insert("", "end", values=(resolution, vcodec, f"{tbr:.0f}" if tbr else "N/A", "是" if has_audio else "否", filesize))
        if formats:
            self.formats_tree.selection_set(self.formats_tree.get_children()[0])
    
    def _populate_subtitles(self, subtitles):
        self.available_subtitles = subtitles
        display_values = list(subtitles.keys())
        
        self.subtitle_combo['values'] = display_values
        if display_values:
            self.subtitle_combo.set(display_values[0])
        else:
            self.subtitle_combo.set("無可用字幕")
        self.subtitle_combo.config(state='readonly')
    
    def _populate_videos(self, videos):
        self.videos_tree.delete(*self.videos_tree.get_children())
        self.channel_videos = videos
        for _, (title, _) in enumerate(videos):
            self.videos_tree.insert("", "end", text="☐", values=(title[:80] + "..." if len(title) > 80 else title,))
    
    def _select_all_videos(self):
        for item in self.videos_tree.get_children():
            self.videos_tree.item(item, text="☑")
    
    def _deselect_all_videos(self):
        for item in self.videos_tree.get_children():
            self.videos_tree.item(item, text="☐")
    
    def _get_selected_videos(self):
        selected = []
        for item in self.videos_tree.get_children():
            if self.videos_tree.item(item, "text") == "☑":
                index = self.videos_tree.index(item)
                selected.append(self.channel_videos[index])
        return selected
    
    def _toggle_video_selection(self, event):
        item = self.videos_tree.identify_row(event.y)
        if not item: return
        
        if self.videos_tree.identify_region(event.x, event.y) == 'tree':
            current_text = self.videos_tree.item(item, "text")
            new_text = "☑" if current_text == "☐" else "☐"
            self.videos_tree.item(item, text=new_text)
            
    def _on_video_select(self, event):
        selected_items = self.videos_tree.selection()
        if not selected_items:
            return
        
        selected_item = selected_items[0]
        index = self.videos_tree.index(selected_item)
        
        if index >= len(self.channel_videos):
            return

        _, video_url = self.channel_videos[index]
        
        if video_url:
            self.queue.put({"type": "status", "text": "正在讀取影片詳細資訊..."})
            self.subtitle_combo.set("讀取中...")
            self.subtitle_combo.config(state='disabled')
            
            thread = threading.Thread(target=self._fetch_video_details_worker, args=(video_url,))
            thread.daemon = True
            thread.start()

    def _fetch_video_details_worker(self, url):
        try:
            ydl_opts = {'quiet': True, 'ffmpeg_location': self.FFMPEG_PATH, 'noplaylist': True, 'listsubtitles': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            thumbnail_url = info.get('thumbnail')
            if thumbnail_url:
                self.queue.put({"type": "thumbnail_url", "url": thumbnail_url})
            
            subtitles = self._get_subtitles(url, info=info)
            self.queue.put({"type": "update_single_video_subtitles", "data": subtitles})
            
            self.queue.put({"type": "status", "text": "影片詳細資訊載入完成"})
            
        except yt_dlp.utils.DownloadError as e:
            self.queue.put({"type": "log", "text": f"無法獲取影片資訊: {e.msg}"})
            self.queue.put({"type": "status", "text": "影片資訊載入失敗"})
            self.queue.put({"type": "update_single_video_subtitles", "data": {'無': 'none'}})
        except Exception as e:
            self.queue.put({"type": "log", "text": f"未預期錯誤: {e}"})
            self.queue.put({"type": "status", "text": "影片資訊載入失敗"})
            self.queue.put({"type": "update_single_video_subtitles", "data": {'無': 'none'}})

    def _start_download(self):
        is_playlist = bool(self.channel_videos)
        download_path = self.download_path_var.get()

        if not os.path.exists(download_path):
            messagebox.showerror("錯誤", "下載路徑不存在", parent=self.root)
            return
        if is_playlist:
            if not self._get_selected_videos():
                messagebox.showerror("錯誤", "請至少選擇一個要下載的影片", parent=self.root)
                return
        elif self.download_type_var.get() == "video" and not self.formats_tree.selection():
            messagebox.showerror("錯誤", "請選擇一個影片格式", parent=self.root)
            return
        
        self._set_ui_state('disabled')
        self.total_progress_var.set(0)
        self.file_progress_var.set(0)
        threading.Thread(target=self._download_worker, daemon=True).start()
    
    def _my_progress_hook(self, d):
        if d['status'] == 'downloading':
            try:
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if total_bytes > 0:
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    percent = (downloaded_bytes / total_bytes) * 100
                    self.queue.put({"type": "file_progress", "value": percent})
            except (ValueError, TypeError): pass
        elif d['status'] == 'finished':
            self.queue.put({"type": "file_progress", "value": 100})
            if d.get('postprocessor') == 'FFmpegMerger':
                self.queue.put({"type": "log", "text": "FFmpeg 合併完成。"})

    def _download_worker(self):
        try:
            download_path = self.download_path_var.get()
            subtitle_key = self.subtitle_var.get()
            subtitle_lang = self.available_subtitles.get(subtitle_key) if subtitle_key not in ["none", "無"] else None
            is_playlist = bool(self.channel_videos)
            os.chdir(download_path)
            
            if is_playlist:
                selected_videos = self._get_selected_videos()
                total_videos = len(selected_videos)
                success_count = 0
                fail_count = 0
                self.queue.put({"type": "log", "text": f"準備下載 {total_videos} 個選定的影片..."})

                playlist_format_str = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                playlist_height_for_filename = 1080
                
                for i, (title, video_url) in enumerate(selected_videos):
                    self.queue.put({"type": "file_progress", "value": 0})
                    self.queue.put({"type": "log", "text": f"--- 開始下載 ({i+1}/{total_videos}): {title[:50]}... ---"})
                    self.queue.put({"type": "status", "text": f"正在下載 {i+1}/{total_videos}: {title[:30]}..."})
                    try:
                        self._download_video(video_url, playlist_format_str, True, subtitle_lang, playlist_height_for_filename)
                        self.queue.put({"type": "log", "text": f"--- ✔ 下載成功: {title} ---"})
                        success_count += 1
                    except Exception as e:
                        self.queue.put({"type": "log", "text": f"--- ❌ 下載失敗: {title} | 錯誤: {e} ---"})
                        fail_count += 1
                        continue # Continue to the next video
                    self.queue.put({"type": "total_progress", "value": ((i + 1) / total_videos) * 100})

                self.queue.put({"type": "log", "text": "===================="})
                self.queue.put({"type": "log", "text": f"所有下載任務已完成。"})
                self.queue.put({"type": "log", "text": f"成功: {success_count} 個, 失敗: {fail_count} 個"})
                self.queue.put({"type": "log", "text": "===================="})
                self.queue.put({"type": "success", "text": f"下載完成！\n成功: {success_count}, 失敗: {fail_count}"})

            else: # 處理單一影片下載的區塊
                url = self.url_var.get().strip()
                # --- 變更 1：獲取標題以用於日誌 ---
                title = self.video_title_var.get()
                self.queue.put({"type": "file_progress", "value": 0})
                self.queue.put({"type": "total_progress", "value": 0})
                if self.download_type_var.get() == "video":
                    selection = self.formats_tree.selection()[0]
                    index = self.formats_tree.index(selection)
                    format_data = self.available_formats[index]
                    resolution, _, _, has_audio, height, _, format_id = format_data
                    self.queue.put({"type": "log", "text": f"--- 開始下載 {resolution} 的影片... ---"})
                    self.queue.put({"type": "status", "text": "正在下載影片..."})
                    self._download_video(url, format_id, has_audio, subtitle_lang, height)
                else: 
                    self.queue.put({"type": "log", "text": "--- 開始下載音訊為 MP3... ---"})
                    self.queue.put({"type": "status", "text": "正在下載音訊..."})
                    self._download_audio(url, subtitle_lang)
                
                # --- 變更 1：新增成功日誌訊息 ---
                self.queue.put({"type": "log", "text": f"--- ✔ 下載成功完成: {title} ---"})
                self.queue.put({"type": "total_progress", "value": 100})
                self.queue.put({"type": "success", "text": "下載成功完成"})
            
            self.queue.put({"type": "status", "text": "下載已完成"})

        except Exception as e:
            self.queue.put({"type": "error", "text": f"下載失敗: {e}"})
            self.queue.put({"type": "status", "text": "下載失敗"})
        finally:
            self.queue.put({"type": "set_ui_state", "state": "normal"})
    
    def _download_video(self, url, format_id, has_audio, subtitle_lang=None, height=0):
        format_str = format_id
        if not has_audio:
            format_str += "+bestaudio[ext=m4a]/bestaudio"
            self.queue.put({"type": "log", "text": "偵測到分離的影像與音訊，將使用 FFmpeg 合併..."})
        
        output_template = f"{height}p - %(title)s.%(ext)s" if height > 0 else "%(title)s.%(ext)s"

        ydl_opts = {
            'format': format_str, 
            'outtmpl': output_template,
            'merge_output_format': 'mp4', 
            'ffmpeg_location': self.FFMPEG_PATH, 
            'quiet': True, 
            'no_warnings': True, 
            'progress_hooks': [self._my_progress_hook]
        }
        if subtitle_lang:
            ydl_opts.update({
                'writesubtitles': True, 
                'subtitleslangs': [subtitle_lang], 
                'subtitlesformat': 'vtt'
            })
        
        last_exception = None
        for attempt in range(self.DOWNLOAD_RETRIES + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                if attempt > 0: self.queue.put({"type": "log", "text": "重試成功。"})
                return 
            except Exception as e:
                last_exception = e
                if attempt < self.DOWNLOAD_RETRIES:
                    log_msg = f"影片下載嘗試失敗。將在 {self.RETRY_DELAY} 秒後進行第 {attempt + 1}/{self.DOWNLOAD_RETRIES} 次重試..."
                    self.queue.put({"type": "log", "text": log_msg})
                    time.sleep(self.RETRY_DELAY)
                else:
                    self.queue.put({"type": "log", "text": "所有重試均告失敗。"})
        if last_exception: raise last_exception

    def _download_audio(self, url, subtitle_lang=None):
        self.queue.put({"type": "log", "text": "正在使用 FFmpeg 將音訊轉換為 MP3..."})
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio', 
            'outtmpl': '%(title)s.%(ext)s', 
            'postprocessors': [{
                'key': 'FFmpegExtractAudio', 
                'preferredcodec': 'mp3', 
                'preferredquality': '192'
            }], 
            'ffmpeg_location': self.FFMPEG_PATH, 
            'quiet': True, 
            'no_warnings': True, 
            'progress_hooks': [self._my_progress_hook]
        }
        if subtitle_lang:
            ydl_opts.update({
                'writesubtitles': True, 
                'subtitleslangs': [subtitle_lang], 
                'subtitlesformat': 'vtt'
            })
        
        last_exception = None
        for attempt in range(self.DOWNLOAD_RETRIES + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                self.queue.put({"type": "log", "text": "MP3 轉檔完成。"})
                if attempt > 0: self.queue.put({"type": "log", "text": "重試成功。"})
                return
            except Exception as e:
                last_exception = e
                if attempt < self.DOWNLOAD_RETRIES:
                    log_msg = f"音訊下載嘗試失敗。將在 {self.RETRY_DELAY} 秒後進行第 {attempt + 1}/{self.DOWNLOAD_RETRIES} 次重試..."
                    self.queue.put({"type": "log", "text": log_msg})
                    time.sleep(self.RETRY_DELAY)
                else:
                    self.queue.put({"type": "log", "text": "所有重試均告失敗。"})
        if last_exception: raise last_exception

def main():
    root = tk.Tk()
    app = YouTubeDownloaderGUI(root)
    app.videos_tree.bind("<Button-1>", app._toggle_video_selection)
    root.mainloop()

if __name__ == "__main__":
    main()