"""
YouTube Downloader - 主程式入口
使用模組化架構，提供圖形化介面的 YouTube 影片下載工具。

依賴安裝:
    pip install -r requirements.txt
    另外需要手動安裝 FFmpeg 並在設定中指定路徑。
"""

import tkinter as tk
import sys

from app.gui import YouTubeDownloaderGUI
from app.setup_checker import SetupChecker
from app.setup_wizard import SetupWizard


def _run_setup_wizard(root: tk.Tk, checker: SetupChecker, ffmpeg_path: str = "") -> bool:
    """
    執行依賴檢測與引導精靈。
    回傳 True 表示可以繼續啟動主程式。
    """
    result = checker.check_all(ffmpeg_path)

    if result.all_ready:
        return True

    # 有缺失 → 啟動引導精靈
    wizard = SetupWizard(result, ffmpeg_path)
    if not wizard.run():
        return False  # 使用者關閉視窗

    # 精靈結束後再次檢查
    result2 = checker.check_all(ffmpeg_path)
    if not result2.critical_ready:
        # 仍有必要依賴未安裝，顯示警告但繼續
        print("警告：部分必要依賴尚未安裝，部分功能可能無法使用。")
    return True


def main():
    # ── 0. 最低 Python 版本檢查（無 tkinter 時也能執行）───
    if sys.version_info < (3, 11):
        print("=" * 60)
        print(" 錯誤：需要 Python 3.11 或以上版本。")
        print(f" 目前版本：{sys.version}")
        print(" 請從 https://www.python.org/downloads/ 下載最新版本。")
        print("=" * 60)
        sys.exit(1)

    # ── 1. 建立 tk root ──
    root = tk.Tk()
    root.withdraw()  # 先隱藏，等精靈結束後再顯示

    # ── 2. 依賴檢測與引導 ──
    try:
        # 讀取現有設定檔中的 ffmpeg_path（若可匯入）
        from app.config import load_settings
        settings = load_settings()
        ffmpeg_path = settings.get("ffmpeg_path", "")
    except Exception:
        ffmpeg_path = ""

    checker = SetupChecker()
    can_continue = _run_setup_wizard(root, checker, ffmpeg_path)

    if not can_continue:
        root.destroy()
        sys.exit(0)

    # ── 3. 顯示主視窗並啟動主程式 ──
    root.deiconify()
    app = YouTubeDownloaderGUI(root)
    app.videos_tree.bind("<Button-1>", app._toggle_video_selection)
    root.mainloop()


if __name__ == "__main__":
    main()
