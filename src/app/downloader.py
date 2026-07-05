"""
下載引擎模組 — 封裝所有 yt-dlp 相關操作。
包含：網址分析、格式解析、字幕提取、單一/批次/並行下載。
"""

import os
import sys
import re
import time
import queue
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from operator import itemgetter

import yt_dlp

from .utils import YtdlpLogger, simplify_codec


class DownloadManager:
    """YouTube 影片下載管理器，處理所有 yt-dlp 互動。"""

    def __init__(self, ffmpeg_path: str, retries: int = 2, retry_delay: int = 5,
                 parallel_downloads: int = 2, msg_queue: queue.Queue = None):
        self.ffmpeg_path = ffmpeg_path
        self.retries = retries
        self.retry_delay = retry_delay
        self.parallel_downloads = max(1, min(parallel_downloads, 4))
        self.queue = msg_queue

    @property
    def _base_ydl_opts(self) -> dict:
        """所有 yt-dlp 呼叫共用的基礎選項。"""
        return {
            'js_runtimes': {'node': {}},
            'remote_components': ['ejs:github'],
        }

    # ─── yt-dlp 更新 ───────────────────────────────────────

    def update_yt_dlp(self):
        """在背景執行緒中升級 yt-dlp。"""
        self._put_log("--- 正在檢查 yt-dlp 更新 ---")
        try:
            command = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            result = subprocess.check_output(command, stderr=subprocess.STDOUT,
                                             text=True, encoding='utf-8')
            for line in result.strip().split('\n'):
                self._put_log(line)
            self._put_log("--- yt-dlp 更新檢查完成 ---")
        except FileNotFoundError:
            self._put_log("錯誤：找不到 Python/pip 命令。無法自動更新 yt-dlp。")
            self._put_log("--- yt-dlp 更新檢查失敗 ---")
        except subprocess.CalledProcessError as e:
            self._put_log(f"yt-dlp 更新失敗，錯誤碼 {e.returncode}:")
            for line in e.output.strip().split('\n'):
                self._put_log(line)
            self._put_log("--- yt-dlp 更新檢查失敗 ---")
        except Exception as e:
            self._put_log(f"檢查 yt-dlp 更新時發生未知錯誤: {e}")
            self._put_log("--- yt-dlp 更新檢查失敗 ---")

    # ─── 網址分析 ──────────────────────────────────────────

    def analyze_url(self, url: str) -> dict:
        """
        分析網址，自動判別單一影片 / 頻道 / 播放清單。
        回傳 dict 包含 type, title, thumbnail_url, formats, subtitles, videos 等。
        """
        logger = YtdlpLogger(self.queue)
        result = {"type": "unknown"}

        try:
            is_channel_url = bool(re.search(r'/(channel|user|c/|@)', url, re.IGNORECASE))
            is_playlist_url = 'list=' in url
            is_playlist_like = False
            url_to_fetch = url

            # ── 頻道網址 → 轉換為上傳列表 ──
            if is_channel_url and not is_playlist_url:
                self._put_log("偵測到頻道網址，正在嘗試轉換為穩定的上傳列表...")
                try:
                    with yt_dlp.YoutubeDL({**self._base_ydl_opts, 'quiet': True, 'logger': logger}) as ydl:
                        info = ydl.extract_info(url, download=False, process=False)
                        channel_id = info.get('channel_id') or info.get('id')
                    if not channel_id or not channel_id.startswith('UC'):
                        raise yt_dlp.utils.DownloadError("無法從網址解析有效的頻道 ID (UC...)")
                    uploads_playlist_id = 'UU' + channel_id[2:]
                    url_to_fetch = f'https://www.youtube.com/playlist?list={uploads_playlist_id}'
                    self._put_log(f"成功轉換！正在掃描上傳列表：{url_to_fetch}")
                    is_playlist_like = True
                except Exception as e:
                    self._put_log(f"警告：無法自動轉換為上傳列表 ({e})。")
                    self._put_log("將回退至直接掃描影片分頁，此方法可能不穩定。")
                    url_to_fetch = url.rstrip('/') + '/videos'
                    is_playlist_like = True

            elif is_playlist_url:
                self._put_log("偵測到播放列表網址，正在掃描...")
                is_playlist_like = True

            # ── 播放清單／頻道 ──
            if is_playlist_like:
                ydl_opts = {**self._base_ydl_opts, 'extract_flat': True, 'noplaylist': False, 'logger': logger}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_to_fetch, download=False)

                result["type"] = "playlist"
                result["title"] = info.get('title', '未知標題')
                result["thumbnail_url"] = info.get('thumbnail')
                result["videos"] = self._extract_videos(info)
                result["video_count"] = len(result["videos"])

            # ── 單一影片 ──
            else:
                ydl_opts = {
                    **self._base_ydl_opts,
                    'ffmpeg_location': self.ffmpeg_path,
                    'noplaylist': True,
                    'logger': logger,
                }
                self._put_log("偵測到單一影片網址，正在獲取詳細資訊...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url_to_fetch, download=False)

                result["type"] = "single"
                result["title"] = info.get('title', '未知標題')
                result["thumbnail_url"] = info.get('thumbnail')
                result["formats"] = self._extract_formats(info)[:30]
                result["subtitles"] = self._extract_subtitles(url, info=info)

            return result

        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"分析錯誤: {e.msg}") from e
        except Exception as e:
            raise RuntimeError(f"發生未預期錯誤: {e}") from e

    def fetch_video_details(self, url: str) -> dict:
        """取得單一影片的詳細資訊（用於頻道列表中的個別影片）。"""
        ydl_opts = {
            **self._base_ydl_opts,
            'quiet': True,
            'ffmpeg_location': self.ffmpeg_path,
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return {
            "title": info.get('title', ''),
            "thumbnail_url": info.get('thumbnail'),
            "subtitles": self._extract_subtitles(url, info=info),
        }

    # ─── 下載 ──────────────────────────────────────────────

    def download_video(self, url: str, format_id: str, has_audio: bool,
                       output_dir: str, subtitle_lang: str = None,
                       height: int = 0) -> str:
        """
        下載單一影片。
        回傳下載完成的檔案路徑。
        """
        format_str = format_id
        if not has_audio:
            format_str += "+bestaudio[ext=m4a]/bestaudio"
            self._put_log("偵測到分離的影像與音訊，將使用 FFmpeg 合併...")

        output_template = os.path.join(
            output_dir,
            f"{height}p - %(title)s.%(ext)s" if height > 0 else "%(title)s.%(ext)s"
        )

        ydl_opts = {
            **self._base_ydl_opts,
            'format': format_str,
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'ffmpeg_location': self.ffmpeg_path,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [self._progress_hook],
            'sleep_subtitles': 2,
            'sleep_interval_requests': 1,
        }
        if subtitle_lang:
            ydl_opts.update({
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': [subtitle_lang],
                'subtitlesformat': 'vtt',
            })

        last_exception = None
        for attempt in range(self.retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                if attempt > 0:
                    self._put_log("重試成功。")
                # 嘗試取得實際檔案路徑
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                return ydl.prepare_filename(info)
            except Exception as e:
                last_exception = e
                error_str = str(e).lower()
                # 若為字幕相關錯誤，嘗試不下載字幕完成影片下載
                if subtitle_lang and (
                    'subtitle' in error_str
                    or 'unable to download' in error_str
                    or '429' in error_str
                ):
                    self._put_log("字幕下載失敗，改為不下載字幕重試...")
                    try:
                        ydl_opts_no_subs = {
                            k: v for k, v in ydl_opts.items()
                            if k not in (
                                'writesubtitles', 'writeautomaticsub',
                                'subtitleslangs', 'subtitlesformat',
                            )
                        }
                        with yt_dlp.YoutubeDL(ydl_opts_no_subs) as ydl:
                            ydl.download([url])
                        self._put_log("影片下載成功，但字幕已略過。")
                        with yt_dlp.YoutubeDL(ydl_opts_no_subs) as ydl:
                            info = ydl.extract_info(url, download=False)
                        return ydl.prepare_filename(info)
                    except Exception as e2:
                        last_exception = e2

                if attempt < self.retries:
                    self._put_log(
                        f"影片下載嘗試失敗。將在 {self.retry_delay} 秒後進行"
                        f"第 {attempt + 1}/{self.retries} 次重試..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    self._put_log("所有重試均告失敗。")

        if last_exception:
            raise last_exception
        return ""

    def download_audio(self, url: str, output_dir: str,
                       subtitle_lang: str = None) -> str:
        """
        下載音訊並轉為 MP3。
        回傳下載完成的檔案路徑。
        """
        self._put_log("正在使用 FFmpeg 將音訊轉換為 MP3...")
        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

        ydl_opts = {
            **self._base_ydl_opts,
            'format': 'bestaudio[ext=m4a]/bestaudio',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'ffmpeg_location': self.ffmpeg_path,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [self._progress_hook],
            'sleep_subtitles': 2,
            'sleep_interval_requests': 1,
        }
        if subtitle_lang:
            ydl_opts.update({
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': [subtitle_lang],
                'subtitlesformat': 'vtt',
            })

        last_exception = None
        for attempt in range(self.retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                self._put_log("MP3 轉檔完成。")
                if attempt > 0:
                    self._put_log("重試成功。")
                return ""  # MP3 檔名難以預測，略過路徑記錄
            except Exception as e:
                last_exception = e
                error_str = str(e).lower()
                # 若為字幕相關錯誤，嘗試不下載字幕完成音訊下載
                if subtitle_lang and (
                    'subtitle' in error_str
                    or 'unable to download' in error_str
                    or '429' in error_str
                ):
                    self._put_log("字幕下載失敗，改為不下載字幕重試...")
                    try:
                        ydl_opts_no_subs = {
                            k: v for k, v in ydl_opts.items()
                            if k not in (
                                'writesubtitles', 'writeautomaticsub',
                                'subtitleslangs', 'subtitlesformat',
                            )
                        }
                        with yt_dlp.YoutubeDL(ydl_opts_no_subs) as ydl:
                            ydl.download([url])
                        self._put_log("MP3 下載成功，但字幕已略過。")
                        return ""
                    except Exception as e2:
                        last_exception = e2

                if attempt < self.retries:
                    self._put_log(
                        f"音訊下載嘗試失敗。將在 {self.retry_delay} 秒後進行"
                        f"第 {attempt + 1}/{self.retries} 次重試..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    self._put_log("所有重試均告失敗。")

        if last_exception:
            raise last_exception
        return ""

    def download_playlist_parallel(self, videos: list, output_dir: str,
                                   subtitle_lang: str = None) -> dict:
        """
        使用 ThreadPoolExecutor 並行下載播放清單中的多個影片。
        回傳 {"success": int, "failed": int, "results": list}
        """
        total = len(videos)
        playlist_format_str = (
            'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/'
            'best[ext=mp4]/best'
        )
        playlist_height = 1080

        self._put_log(f"準備並行下載 {total} 個影片（同時進行 {self.parallel_downloads} 個）...")

        results = []
        completed = [0]  # 用 list 包裝以在閉包中修改

        def download_one(index: int, title: str, video_url: str) -> dict:
            """下載單一影片的工作函數。"""
            try:
                file_path = self.download_video(
                    video_url, playlist_format_str, True,
                    output_dir, subtitle_lang, playlist_height
                )
                self._put_log(f"--- ✔ 下載成功: {title} ---")
                return {
                    "index": index, "title": title, "url": video_url,
                    "status": "success", "file_path": file_path, "error": None,
                }
            except Exception as e:
                self._put_log(f"--- ❌ 下載失敗: {title} | 錯誤: {e} ---")
                return {
                    "index": index, "title": title, "url": video_url,
                    "status": "failed", "file_path": "", "error": str(e),
                }

        with ThreadPoolExecutor(max_workers=self.parallel_downloads) as executor:
            futures = {
                executor.submit(download_one, i, title, url): i
                for i, (title, url) in enumerate(videos)
            }

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                completed[0] += 1
                self._put_progress("total", (completed[0] / total) * 100)
                status_text = f"正在下載 {completed[0]}/{total}"
                if result["status"] == "success":
                    status_text += f": {result['title'][:20]}..."
                self._put_status(status_text)

        results.sort(key=lambda r: r["index"])
        success_count = sum(1 for r in results if r["status"] == "success")
        fail_count = total - success_count

        self._put_log("====================")
        self._put_log("所有下載任務已完成。")
        self._put_log(f"成功: {success_count} 個, 失敗: {fail_count} 個")
        self._put_log("====================")

        return {"success": success_count, "failed": fail_count, "results": results}

    # ─── 內部輔助方法 ──────────────────────────────────────

    def _progress_hook(self, d: dict):
        """yt-dlp 下載進度回呼。"""
        if d['status'] == 'downloading':
            try:
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if total_bytes > 0:
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    percent = (downloaded_bytes / total_bytes) * 100
                    self._put_progress("file", percent)
            except (ValueError, TypeError):
                pass
        elif d['status'] == 'finished':
            self._put_progress("file", 100)
            if d.get('postprocessor') == 'FFmpegMerger':
                self._put_log("FFmpeg 合併完成。")

    def _extract_formats(self, info: dict) -> list:
        """從 yt-dlp 資訊中提取 MP4 格式列表。"""
        formats = info.get('formats', [])
        mp4_formats = [f for f in formats
                       if f.get('ext') == 'mp4' and f.get('vcodec') != 'none']
        resolutions = []
        for fmt in mp4_formats:
            resolution = fmt.get('resolution',
                                 f"{fmt.get('width', '未知')}x{fmt.get('height', '未知')}")
            vcodec = simplify_codec(fmt.get('vcodec', '未知'))
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
                resolutions.append(
                    (resolution, vcodec, tbr, has_audio, height, filesize_str, format_id)
                )
        resolutions.sort(key=itemgetter(4), reverse=True)
        return resolutions

    def _extract_subtitles(self, url: str, info: dict = None) -> dict:
        """提取可用字幕列表（英文、中文、粵語，包含手動與自動字幕）。"""
        if info is None:
            ydl_opts = {
                **self._base_ydl_opts,
                'listsubtitles': True,
                'ffmpeg_location': self.ffmpeg_path,
                'quiet': True,
                'noplaylist': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                except Exception:
                    return {'無': 'none'}

        # 手動字幕位於 'subtitles'，自動字幕位於 'automatic_captions'
        manual_subs = info.get('subtitles', {})
        auto_subs = info.get('automatic_captions', {})

        available = {'無': 'none'}

        # 收集手動字幕
        for lang, sub_info in manual_subs.items():
            if lang.startswith('en') or lang.startswith('zh') or lang.startswith('yue'):
                lang_display = self._lang_to_display(lang)
                available[f"{lang_display} (手動)"] = lang

        # 收集自動字幕（避免與手動字幕重複）
        for lang, sub_info in auto_subs.items():
            if lang.startswith('en') or lang.startswith('zh') or lang.startswith('yue'):
                lang_display = self._lang_to_display(lang)
                # 如果手動字幕已有同語言，標記為「手動+自動」
                manual_key = f"{lang_display} (手動)"
                if lang in manual_subs:
                    available[f"{lang_display} (手動+自動)"] = lang
                else:
                    available[f"{lang_display} (自動)"] = lang

        return available

    @staticmethod
    def _lang_to_display(lang: str) -> str:
        """將 ISO 語言代碼轉換為中文顯示名稱。"""
        if lang == "en" or lang.startswith("en-"):
            return "英文"
        if lang in ("zh-Hant", "zh-TW", "zh-HK"):
            return "繁體中文"
        if lang in ("zh-Hans", "zh-CN", "zh-SG"):
            return "簡體中文"
        if lang.startswith("zh"):
            return "中文"
        if lang.startswith("yue"):
            return "粵語"
        return lang

    def _extract_videos(self, info: dict) -> list:
        """從頻道/播放清單資訊中提取影片列表。"""
        if 'entries' not in info or not info['entries']:
            return []
        entries = filter(None, info['entries'])
        return [
            (entry.get('title', '無標題'),
             entry.get('webpage_url', entry.get('url')))
            for entry in entries
            if entry.get('webpage_url') or entry.get('url')
        ]

    # ─── 佇列通訊輔助 ─────────────────────────────────────

    def _put_log(self, text: str):
        if self.queue:
            self.queue.put({"type": "log", "text": text})

    def _put_status(self, text: str):
        if self.queue:
            self.queue.put({"type": "status", "text": text})

    def _put_progress(self, key: str, value: float):
        if self.queue:
            self.queue.put({"type": f"{key}_progress", "value": value})
