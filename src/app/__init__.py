# YouTube Downloader - 模組化應用程式
# 將原本的單一大檔案拆分為關注點分離的模組：
#   utils.py     - 通用工具（日誌、格式簡化）
#   config.py    - 設定檔管理（JSON 讀寫）
#   history.py   - 下載歷史記錄（SQLite）
#   downloader.py - 下載引擎（yt-dlp 封裝、並行批次下載）
#   gui.py       - 使用者介面（tkinter）
__version__ = "2.0.0"
