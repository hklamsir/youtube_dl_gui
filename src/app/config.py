"""
設定檔管理模組 — 使用 JSON 檔案持久化使用者偏好設定。
"""

import json
import os


SETTINGS_FILE = "yd_settings.json"

DEFAULT_SETTINGS = {
    'ffmpeg_path': '',
    'retries': 2,
    'delay': 5,
    'default_download_path': os.getcwd(),
    'parallel_downloads': 2,   # 並行下載數量（1 = 序列，2~4 = 並行）
}


def load_settings() -> dict:
    """從 JSON 檔案載入設定，若檔案不存在或損壞則回傳預設值。"""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        merged.update(settings)
        print(f"設定已從 {SETTINGS_FILE} 載入。")
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        print("未找到設定檔或格式錯誤，使用預設值。")
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> bool:
    """將設定寫入 JSON 檔案。回傳是否成功。"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"無法儲存設定: {e}")
        return False
