"""
依賴檢測模組 — 檢查 Python / Node.js / FFmpeg 的安裝狀態與版本。
純邏輯模組，無 UI 依賴，可獨立單元測試。
"""

import subprocess
import sys
import os
import shutil
from dataclasses import dataclass, field


@dataclass
class DepStatus:
    """單一依賴的檢測結果。"""
    name: str                       # "Python" / "Node.js" / "FFmpeg"
    installed: bool = False         # 是否已安裝（或可於 PATH 中找到）
    version: str = ""               # 實際版本號
    min_version: str = ""           # 最低需求版本
    meet_requirement: bool = False  # 版本是否符合需求
    download_url: str = ""          # 官方下載連結
    install_hint: str = ""          # 安裝提示（已依平台自適應）
    path_hint: str = ""             # PATH 設定提示（已依平台自適應）


@dataclass
class SetupResult:
    """整體依賴檢測結果。"""
    python: DepStatus = field(default_factory=lambda: DepStatus("Python"))
    node: DepStatus = field(default_factory=lambda: DepStatus("Node.js"))
    ffmpeg: DepStatus = field(default_factory=lambda: DepStatus("FFmpeg"))

    @property
    def all_ready(self) -> bool:
        """三項依賴是否全部就緒。FFmpeg 可跳過（允許手動設定路徑）。"""
        return (self.python.meet_requirement
                and self.node.meet_requirement
                and self.ffmpeg.meet_requirement)

    @property
    def critical_ready(self) -> bool:
        """Python 與 Node.js 是否就緒（FFmpeg 允許後補）。"""
        return self.python.meet_requirement and self.node.meet_requirement

    @property
    def missing_items(self) -> list:
        """回傳尚未就緒的依賴名稱列表。"""
        missing = []
        if not self.python.meet_requirement:
            missing.append("Python")
        if not self.node.meet_requirement:
            missing.append("Node.js")
        if not self.ffmpeg.meet_requirement:
            missing.append("FFmpeg")
        return missing


class SetupChecker:
    """檢測 Python / Node.js / FFmpeg 安裝狀態。"""

    def check_all(self, ffmpeg_config_path: str = "") -> SetupResult:
        """檢測所有依賴並回傳 SetupResult。"""
        result = SetupResult()
        result.python = self.check_python()
        result.node = self.check_node()
        result.ffmpeg = self.check_ffmpeg(ffmpeg_config_path)
        return result

    def check_python(self) -> DepStatus:
        """檢查 Python 版本是否 ≥ 3.11。"""
        status = DepStatus(
            name="Python",
            min_version="3.11",
            version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            installed=True,
            download_url="https://www.python.org/downloads/",
        )
        status.meet_requirement = sys.version_info >= (3, 11)

        if status.meet_requirement:
            status.install_hint = ""
            status.path_hint = ""
        else:
            status.install_hint = self._python_upgrade_hint()
            status.path_hint = ""

        return status

    def check_node(self) -> DepStatus:
        """檢查 Node.js 是否可於 PATH 中找到且版本 ≥ 18。"""
        status = DepStatus(
            name="Node.js",
            min_version="18",
            download_url="https://nodejs.org/",
        )
        try:
            output = subprocess.check_output(
                ["node", "--version"],
                stderr=subprocess.STDOUT,
                timeout=10,
                text=True,
            ).strip()
            # 移除前綴 "v"
            version = output.lstrip("v")
            status.version = version
            status.installed = True
            # 比較版本
            status.meet_requirement = self._version_ge(version, "18")
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            status.installed = False
            status.version = ""
            status.meet_requirement = False

        status.install_hint = self._node_install_hint()
        status.path_hint = self._node_path_hint()
        return status

    def check_ffmpeg(self, config_path: str = "") -> DepStatus:
        """
        檢查 FFmpeg 是否可於 PATH 或設定路徑中找到。
        若設定檔有指定路徑則優先使用。
        """
        status = DepStatus(
            name="FFmpeg",
            min_version="任意",
            download_url="https://ffmpeg.org/download.html",
        )

        # 先試設定檔路徑
        if config_path and os.path.isfile(config_path):
            candidate = config_path
        else:
            candidate = "ffmpeg"

        # 從 PATH 或 which 尋找
        ffmpeg_path = shutil.which(candidate)
        if not ffmpeg_path:
            # 也嘗試常見名稱
            for name in ("ffmpeg.exe", "ffmpeg"):
                found = shutil.which(name)
                if found:
                    ffmpeg_path = found
                    break

        if ffmpeg_path:
            status.installed = True
            status.meet_requirement = True
            try:
                output = subprocess.check_output(
                    [ffmpeg_path, "-version"],
                    stderr=subprocess.STDOUT,
                    timeout=10,
                    text=True,
                )
                # 第一行通常為 "ffmpeg version x.x.x"
                first_line = output.splitlines()[0] if output.strip() else ""
                parts = first_line.split()
                if len(parts) >= 3:
                    status.version = parts[2]
            except Exception:
                status.version = "未知"
        else:
            status.installed = False
            status.version = ""
            status.meet_requirement = False

        status.install_hint = self._ffmpeg_install_hint()
        status.path_hint = self._ffmpeg_path_hint()
        return status

    # ─── 版本比較 ──────────────────────────────────────────

    @staticmethod
    def _version_ge(actual: str, minimum: str) -> bool:
        """簡易語意版本比較：actual >= minimum。"""
        try:
            act_parts = [int(x) for x in actual.split(".")]
            min_parts = [int(x) for x in minimum.split(".")]
            # 補齊長度
            while len(act_parts) < len(min_parts):
                act_parts.append(0)
            while len(min_parts) < len(act_parts):
                min_parts.append(0)
            return act_parts >= min_parts
        except (ValueError, AttributeError):
            return False

    # ─── 各平台安裝提示 ───────────────────────────────────

    @staticmethod
    def _python_upgrade_hint() -> str:
        if sys.platform == "win32":
            return (
                "請從官方網站下載最新 Python 3.11+ 安裝程式。\n"
                "安裝時務必勾選「Add Python to PATH」。\n"
                "安裝完成後請重新啟動此程式。"
            )
        elif sys.platform == "darwin":
            return (
                "使用 Homebrew 安裝:\n"
                "  brew install python@3.13\n"
                "或從 https://www.python.org/downloads/ 下載。"
            )
        else:
            return (
                "使用系統套件管理安裝:\n"
                "  sudo apt install python3.11    (Debian/Ubuntu)\n"
                "  sudo dnf install python3.11    (Fedora)\n"
                "或從 https://www.python.org/downloads/ 下載。"
            )

    @staticmethod
    def _node_install_hint() -> str:
        if sys.platform == "win32":
            return (
                "請從官方網站下載 Node.js LTS 版本 (.msi 安裝檔)。\n"
                "安裝程式會自動將 Node.js 加入系統 PATH。"
            )
        elif sys.platform == "darwin":
            return (
                "使用 Homebrew 安裝:\n"
                "  brew install node\n"
                "或從 https://nodejs.org/ 下載 macOS 安裝檔。"
            )
        else:
            return (
                "使用系統套件管理安裝:\n"
                "  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo bash -\n"
                "  sudo apt install nodejs          (Debian/Ubuntu)\n"
                "或從 https://nodejs.org/ 下載。"
            )

    @staticmethod
    def _ffmpeg_install_hint() -> str:
        if sys.platform == "win32":
            return (
                "步驟 1: 前往 https://ffmpeg.org/ → Windows Builds → gyan.dev\n"
                "步驟 2: 下載 ffmpeg-release-full.7z\n"
                "步驟 3: 解壓縮到 C:\\ffmpeg\n"
                "步驟 4: 將 C:\\ffmpeg\\bin 加入系統 PATH，\n"
                "         或在程式「設定」中手動指定 ffmpeg.exe 路徑。"
            )
        elif sys.platform == "darwin":
            return (
                "使用 Homebrew 安裝:\n"
                "  brew install ffmpeg"
            )
        else:
            return (
                "使用系統套件管理安裝:\n"
                "  sudo apt install ffmpeg          (Debian/Ubuntu)\n"
                "  sudo dnf install ffmpeg          (Fedora)"
            )

    @staticmethod
    def _node_path_hint() -> str:
        return (
            "若已安裝 Node.js 但仍檢測不到，\n"
            "請確認 Node.js 已加入系統 PATH 環境變數。\n"
            "重新開啟終端機或重新啟動電腦後再試。"
        )

    @staticmethod
    def _ffmpeg_path_hint() -> str:
        return (
            "若已安裝 FFmpeg 但仍檢測不到，\n"
            "請確認 ffmpeg.exe 所在目錄已加入系統 PATH，\n"
            "或在程式「設定」頁面手動指定 ffmpeg.exe 的完整路徑。"
        )
