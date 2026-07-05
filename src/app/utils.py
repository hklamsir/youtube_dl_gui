"""
通用工具模組 — 包含 yt-dlp 日誌攔截器與編碼簡化函數。
"""


class YtdlpLogger:
    """攔截 yt-dlp 的日誌訊息並傳送到 GUI 的訊息佇列。"""

    def __init__(self, queue):
        self.queue = queue

    def debug(self, msg):
        if msg.startswith('[debug] '):
            pass
        else:
            self.info(msg)

    def info(self, msg):
        self.queue.put({"type": "log", "text": f"[yt-dlp] {msg}"})

    def warning(self, msg):
        self.queue.put({"type": "log", "text": f"[yt-dlp 警告] {msg}"})

    def error(self, msg):
        self.queue.put({"type": "log", "text": f"[yt-dlp 錯誤] {msg}"})


def simplify_codec(codec: str) -> str:
    """將 yt-dlp 回傳的完整編碼名稱簡化為常用簡稱。"""
    if not codec or codec == 'none':
        return 'none'
    codec = codec.lower()
    if codec.startswith('vp09'):
        return 'vp9'
    if codec.startswith('av01'):
        return 'av1'
    if codec.startswith('avc1'):
        return 'h264'
    return codec
