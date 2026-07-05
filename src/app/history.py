"""
下載歷史記錄模組 — 使用 SQLite 持久化儲存每次下載的完整記錄。
提供查詢、統計、清除等功能。
"""

import sqlite3
import os
from datetime import datetime

HISTORY_DB = "yd_history.db"


class DownloadHistory:
    """管理下載歷史的 SQLite 資料庫。"""

    def __init__(self, db_path: str = HISTORY_DB):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        """建立資料庫連線（每個執行緒獨立連線，確保執行緒安全）。"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        """初始化資料表結構。"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS download_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    url         TEXT    NOT NULL,
                    title       TEXT,
                    format      TEXT,
                    resolution  TEXT,
                    file_path   TEXT,
                    file_size   INTEGER DEFAULT 0,
                    status      TEXT    DEFAULT 'success',
                    error_msg   TEXT,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_date
                ON download_history(downloaded_at DESC)
            """)

    def add_record(self, url: str, title: str = "", format_type: str = "",
                   resolution: str = "", file_path: str = "", file_size: int = 0,
                   status: str = "success", error_msg: str = ""):
        """新增一筆下載記錄。"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO download_history (url, title, format, resolution, file_path, file_size, status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (url, title, format_type, resolution, file_path, file_size, status, error_msg))

    def get_all(self, limit: int = 100, offset: int = 0) -> list:
        """取得最近的下載記錄。"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM download_history
                ORDER BY downloaded_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        """取得下載統計資訊。"""
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM download_history").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM download_history WHERE status='success'"
            ).fetchone()[0]
            failed = total - success
            total_size = conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) FROM download_history"
            ).fetchone()[0]
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "total_size_bytes": total_size,
        }

    def clear(self):
        """清除所有歷史記錄。"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM download_history")

    def delete_older_than(self, days: int):
        """刪除指定天數之前的記錄。"""
        with self._get_conn() as conn:
            conn.execute("""
                DELETE FROM download_history
                WHERE downloaded_at < datetime('now', '-' || ? || ' days')
            """, (days,))
