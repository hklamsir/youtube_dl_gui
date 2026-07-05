"""
引導精靈模組 — 以逐步對話框引導使用者安裝缺失的依賴。
使用原生 tkinter/ttk，與主程式風格一致。
"""

import tkinter as tk
from tkinter import ttk
import webbrowser
import subprocess
import sys
from .setup_checker import SetupChecker, SetupResult, DepStatus


# ─── 常數 ────────────────────────────────────────────────

_WINDOW_SIZE = "580x440"


class SetupWizard:
    """依賴安裝引導精靈。"""

    def __init__(self, setup_result: SetupResult, ffmpeg_config_path: str = ""):
        """
        setup_result: SetupChecker.check_all() 的回傳結果。
        ffmpeg_config_path: 設定檔中已記錄的 FFmpeg 路徑（若有）。
        """
        self._result = setup_result
        self._ffmpeg_config_path = ffmpeg_config_path
        self._checker = SetupChecker()

        # 決定需要引導的步驟清單
        self._steps: list[DepStatus] = []
        if not setup_result.python.meet_requirement:
            self._steps.append(setup_result.python)
        if not setup_result.node.meet_requirement:
            self._steps.append(setup_result.node)
        if not setup_result.ffmpeg.meet_requirement:
            self._steps.append(setup_result.ffmpeg)

        self._current_step_index = -1  # -1 = 摘要頁
        self._completed: dict[str, bool] = {}
        self._user_cancelled = False

        # 建立視窗
        self._win = tk.Toplevel()
        self._win.title("環境設定精靈")
        self._win.geometry(_WINDOW_SIZE)
        self._win.resizable(False, False)

        self._main = ttk.Frame(self._win, padding="20")
        self._main.pack(fill="both", expand=True)
        self._main.columnconfigure(0, weight=1)

        # 內容區域
        self._content_frame = ttk.Frame(self._main)
        self._content_frame.grid(row=0, column=0, sticky="nsew")
        self._main.rowconfigure(0, weight=1)

        # 底部按鈕
        self._btn_frame = ttk.Frame(self._main)
        self._btn_frame.grid(row=1, column=0, sticky="ew", pady=(15, 0))

    # ─── 公開 API ──────────────────────────────────────────

    def run(self) -> bool:
        """
        啟動引導精靈（模態對話框）。
        回傳 True 表示使用者完成設定，False 表示取消。
        """
        self._win.transient(self._win.master)
        self._win.grab_set()
        self._center_window()
        self._show_summary()
        self._win.wait_window()
        return not self._user_cancelled

    # ─── 頁面切換 ──────────────────────────────────────────

    def _show_summary(self):
        """步驟 -1：檢測摘要頁。"""
        self._current_step_index = -1
        self._clear_content()

        r = self._result

        title = ttk.Label(
            self._content_frame,
            text="🔧 環境設定精靈",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(pady=(0, 20))

        desc = ttk.Label(
            self._content_frame,
            text="檢測到以下依賴狀態，請依照指示完成設定：",
            wraplength=500,
        )
        desc.pack(pady=(0, 15))

        # 依賴狀態列表
        table = ttk.Frame(self._content_frame)
        table.pack(fill="x", padx=20)

        for dep in [r.python, r.node, r.ffmpeg]:
            row_frame = ttk.Frame(table)
            row_frame.pack(fill="x", pady=4)

            if dep.meet_requirement:
                icon = "✅"
                status_text = f"{dep.name} {dep.version}  （已就緒）"
            elif dep.installed:
                icon = "⚠️"
                status_text = f"{dep.name} {dep.version}  （版本不符，需要 ≥ {dep.min_version}）"
            else:
                icon = "❌"
                status_text = f"{dep.name}  尚未安裝"

            ttk.Label(row_frame, text=icon, font=("Segoe UI", 12)).pack(side=tk.LEFT)
            ttk.Label(row_frame, text=status_text).pack(side=tk.LEFT, padx=(8, 0))

        # FFmpeg 特殊提示
        if not r.ffmpeg.meet_requirement:
            ff_note = ttk.Label(
                self._content_frame,
                text="⚠ FFmpeg 可稍後在程式「設定」中手動指定路徑。",
                foreground="#e67e22",
                wraplength=500,
            )
            ff_note.pack(pady=(15, 0))

        # 按鈕
        self._btn_frame.destroy()
        self._btn_frame = ttk.Frame(self._main)
        self._btn_frame.grid(row=1, column=0, sticky="ew", pady=(15, 0))

        if r.critical_ready:
            # Python + Node.js 已就緒，FFmpeg 可跳過
            ttk.Button(
                self._btn_frame, text="啟動主程式",
                command=self._finish,
            ).pack(side=tk.RIGHT, padx=5)
        if self._steps:
            ttk.Button(
                self._btn_frame, text="開始設定",
                command=self._next_step,
            ).pack(side=tk.RIGHT, padx=5)
        ttk.Button(
            self._btn_frame, text="跳過設定",
            command=self._skip_all,
        ).pack(side=tk.RIGHT, padx=5)

    def _show_step(self):
        """顯示當前步驟的引導頁。"""
        self._clear_content()
        dep = self._steps[self._current_step_index]
        step_num = self._current_step_index + 1
        total = len(self._steps)

        # ── 頂部進度 ──
        header = ttk.Frame(self._content_frame)
        header.pack(fill="x", pady=(0, 10))

        ttk.Label(
            header, text=f"步驟 {step_num}/{total}：{dep.name}",
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT)

        # ── 狀態圖示 ──
        status_frame = ttk.Frame(self._content_frame)
        status_frame.pack(pady=10)

        if dep.installed and not dep.meet_requirement:
            icon = "⚠️"
            status_text = f"已安裝 {dep.name} {dep.version}，但版本不符"
            status_sub = f"需求最低版本：{dep.min_version}"
        elif not dep.installed:
            icon = "❌"
            status_text = f"尚未安裝 {dep.name}"
            status_sub = f"需求最低版本：{dep.min_version}"
        else:
            icon = "✅"
            status_text = f"{dep.name} 已就緒"
            status_sub = ""

        ttk.Label(status_frame, text=icon, font=("Segoe UI", 28)).pack()
        ttk.Label(status_frame, text=status_text, font=("Segoe UI", 11)).pack()

        if status_sub:
            ttk.Label(status_frame, text=status_sub, foreground="#888").pack()

        # ── 安裝指引 ──
        guide_frame = ttk.LabelFrame(self._content_frame, text="安裝指引", padding="10")
        guide_frame.pack(fill="both", expand=True, padx=10, pady=10)

        guide_text = tk.Text(guide_frame, height=7, width=55, wrap="word",
                             font=("Consolas", 10), relief="flat",
                             borderwidth=0, background="#f8f8f8")
        guide_text.pack(fill="both", expand=True)

        if dep.install_hint:
            guide_text.insert("1.0", dep.install_hint)
        if dep.path_hint:
            guide_text.insert("end", f"\n\n{ dep.path_hint}")

        guide_text.config(state="disabled")

        # ── 按鈕 ──
        self._btn_frame.destroy()
        self._btn_frame = ttk.Frame(self._main)
        self._btn_frame.grid(row=1, column=0, sticky="ew", pady=(15, 0))

        # FFmpeg 特殊：允許「稍後手動設定」
        if dep.name == "FFmpeg":
            ttk.Button(
                self._btn_frame, text="稍後手動設定路徑",
                command=self._skip_ffmpeg,
            ).pack(side=tk.LEFT, padx=5)

        # 重新檢測
        ttk.Button(
            self._btn_frame, text="已安裝完成，重新檢測",
            command=self._recheck,
        ).pack(side=tk.RIGHT, padx=5)

        # 下載連結
        if dep.download_url:
            ttk.Button(
                self._btn_frame, text="開啟下載頁面",
                command=lambda: webbrowser.open(dep.download_url),
            ).pack(side=tk.RIGHT, padx=5)

        # 導航
        if self._current_step_index > 0:
            ttk.Button(
                self._btn_frame, text="← 上一步",
                command=self._prev_step,
            ).pack(side=tk.LEFT, padx=5)

        if self._current_step_index < len(self._steps) - 1:
            ttk.Button(
                self._btn_frame, text="跳過此步驟 →",
                command=self._next_step,
            ).pack(side=tk.RIGHT, padx=5)

    def _show_completion(self):
        """步驟完成頁。"""
        self._current_step_index = len(self._steps)
        self._clear_content()

        ttk.Label(
            self._content_frame,
            text="✅ 環境設定完成！",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(0, 20))

        ttk.Label(
            self._content_frame,
            text="所有必要依賴已就緒，即將啟動 YouTube 下載器。",
            wraplength=450,
        ).pack(pady=(0, 10))

        # 列出最終狀態
        for dep in [self._result.python, self._result.node, self._result.ffmpeg]:
            icon = "✅" if dep.meet_requirement else "⚠️"
            ttk.Label(
                self._content_frame,
                text=f"{icon} {dep.name} {dep.version if dep.version else '（跳過）'}",
            ).pack(anchor="w", padx=60, pady=2)

        self._btn_frame.destroy()
        self._btn_frame = ttk.Frame(self._main)
        self._btn_frame.grid(row=1, column=0, sticky="ew", pady=(15, 0))
        ttk.Button(
            self._btn_frame, text="啟動主程式",
            command=self._finish,
        ).pack(side=tk.RIGHT, padx=5)

    # ─── 動作 ─────────────────────────────────────────────

    def _recheck(self):
        """重新檢測目前步驟的依賴，若通過則自動前進。"""
        dep = self._steps[self._current_step_index]
        checker = SetupChecker()
        new_result = checker.check_all(self._ffmpeg_config_path)

        updated = None
        if dep.name == "Python":
            updated = new_result.python
        elif dep.name == "Node.js":
            updated = new_result.node
        elif dep.name == "FFmpeg":
            updated = new_result.ffmpeg

        if updated and updated.meet_requirement:
            self._result = new_result
            self._steps[self._current_step_index] = updated
            self._steps.pop(self._current_step_index)
            # 若還有下一步 → 顯示下一步；否則 → 完成頁
            if self._current_step_index < len(self._steps):
                self._show_step()
            else:
                self._show_completion()
        else:
            # 仍未通過，刷新當前步驟顯示
            if updated:
                self._steps[self._current_step_index] = updated
                self._result = new_result
                # 如果是最後的步驟且只有 FFmpeg 未過
                if dep.name == "FFmpeg" and len(self._steps) == 1:
                    self._show_summary()
                    return
            self._show_step()

    def _next_step(self):
        """進入下一個步驟。"""
        self._current_step_index += 1
        if self._current_step_index >= len(self._steps):
            self._show_completion()
        else:
            self._show_step()

    def _prev_step(self):
        """回到上一個步驟。"""
        if self._current_step_index > 0:
            self._current_step_index -= 1
            self._show_step()
        else:
            self._show_summary()

    def _skip_ffmpeg(self):
        """FFmpeg 特殊：標記為跳過（使用者將手動設定路徑）。"""
        # 從步驟清單中移除 FFmpeg
        self._steps.pop(self._current_step_index)
        self._result.ffmpeg.meet_requirement = True  # 強制標記為已處理
        self._result.ffmpeg.version = "（手動設定）"
        if self._current_step_index < len(self._steps):
            self._show_step()
        else:
            self._show_completion()

    def _skip_all(self):
        """跳過所有設定，直接關閉精靈。"""
        self._user_cancelled = False  # 不是取消，是跳過
        self._win.destroy()

    def _finish(self):
        """完成設定，關閉精靈。"""
        self._user_cancelled = False
        self._win.destroy()

    # ─── UI 輔助 ──────────────────────────────────────────

    def _clear_content(self):
        """清除內容區域的所有元件。"""
        for child in self._content_frame.winfo_children():
            child.destroy()

    def _center_window(self):
        """將精靈視窗置中於主視窗。"""
        self._win.update_idletasks()
        w = self._win.winfo_width()
        h = self._win.winfo_height()
        parent = self._win.master
        if parent:
            x = parent.winfo_x() + (parent.winfo_width() - w) // 2
            y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        else:
            x = (self._win.winfo_screenwidth() - w) // 2
            y = (self._win.winfo_screenheight() - h) // 2
        self._win.geometry(f"+{x}+{y}")
