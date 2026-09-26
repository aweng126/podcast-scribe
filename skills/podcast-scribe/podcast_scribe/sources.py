"""Single-video Bilibili acquisition; no implicit cookies or playlist expansion."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .model import ContentError


def normalize_url(value: str) -> str:
    if re.fullmatch(r"BV[A-Za-z0-9]{10}", value):
        return f"https://www.bilibili.com/video/{value}"
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in ("www.bilibili.com", "bilibili.com", "m.bilibili.com", "b23.tv") or parsed.username or parsed.password:
        raise ContentError("请输入 B站单个视频链接、b23.tv 短链接或 BV 号")
    if parsed.hostname != "b23.tv" and not re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av\d+)/?", parsed.path):
        raise ContentError("第一版仅接受单个 B站视频，不抓取整个频道或合集")
    return value


class QuietLogger:
    preview_only = False

    def debug(self, *_): pass
    def warning(self, message):
        # yt-dlp can return success after warning that only a paid preview is
        # available. Record just the condition, not its signed URL or message.
        if "only the preview will be extracted" in str(message).lower():
            self.preview_only = True
    def error(self, *_): pass


def _ydl(extra: dict | None = None):
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise ContentError("缺少 yt-dlp；请安装项目依赖") from exc
    options = {"noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
               "socket_timeout": 25, "retries": 1, "extractor_retries": 1,
               "cachedir": False, "ignoreerrors": False,
               "cookiefile": None, "cookiesfrombrowser": None, "usenetrc": False}
    options.update(extra or {})
    return YoutubeDL(options)


def _single(info: dict) -> dict:
    if not info or info.get("_type") in ("playlist", "multi_video") or "entries" in info:
        raise ContentError("获取结果包含多个视频，请指定单个分 P 链接")
    return info


def _source_reason(exc: Exception) -> str:
    # Signed media URLs and provider diagnostics need not leak into the content store.
    message = str(exc).lower()
    reason = "来源提取失败"
    playback_code = re.search(r"unable to download video info: (-?\d+)", message)
    if playback_code:
        reason = f"B站播放 API 拒绝访问（code={-int(playback_code.group(1))}）"
    elif any(term in message for term in ("514", "frequency capped")):
        reason = "音频 CDN 限制请求频率（HTTP 514）"
    elif any(term in message for term in ("412", "captcha", "风控", "验证码", "rate limit")):
        reason = "B站返回验证码或风控响应"
    elif "429" in message or "too many requests" in message:
        reason = "B站限制请求频率（HTTP 429）"
    elif "403" in message or "forbidden" in message or "access denied" in message:
        reason = "B站拒绝当前请求（HTTP 403 或访问被拒绝）"
    elif _access_denied(message):
        reason = "来源要求登录或相应访问权限"
    elif any(term in message for term in ("connection", "proxy", "timeout", "timed out", "resolve", "network")):
        reason = "无法连接视频来源"
    return reason


def _source_error(exc: Exception) -> ContentError:
    return ContentError(_source_reason(exc) + "；未取得音频。" + _access_hint())


def _access_hint() -> str:
    return ("请先在浏览器正常打开该视频，完成登录或验证码；需要复用登录态时，可明确授权 "
            "--cookies-from-browser 或 --cookies。授权 cookies 不保证登录有效或获得访问权限；"
            "仍失败时可使用本地 MP4/M4A/MP3，或导入 JSON/SRT/VTT 转写。不会自动读取浏览器 cookies。")


def _access_denied(message: str) -> bool:
    return any(term in message.lower() for term in (
        "login", "log in", "sign in", "登录", "premium", "会员", "paywall", "付费",
        "purchase", "supporter-only", "充电", "preview", "试看", "geo-restricted", "geo restricted", "地区限制", "地域限制",
    ))


def _requested_page(url: str) -> int:
    value = parse_qs(urlparse(url).query, keep_blank_values=True).get("p", ["1"])[-1]
    if not value.isdigit() or int(value) < 1:
        raise ContentError("分 P 参数必须是从 1 开始的正整数")
    return int(value)


def _flag(value) -> bool:
    return value not in (None, False, 0, "0", "false", "")


def _positive_duration(value) -> float:
    if isinstance(value, bool):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return number if math.isfinite(number) and number > 0 else 0


def _public_api_info(ydl, url: str) -> dict:
    """Use ordinary source APIs with the caller's explicit cookie jar, if any.

    The installed _download_playinfo returns data only for an API code of zero;
    it raises on all other codes. Its duration and access flags still need checks
    because a successful response may describe a paid video's free preview.
    """
    from yt_dlp.extractor.bilibili import BiliBiliIE

    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av\d+)/?", urlparse(url).path)
    if not match:
        raise ContentError("公开 API 无法解析此短链接，请使用原始 BV 视频链接")
    video_id = match.group(1)
    page_number = _requested_page(url)
    query = {"bvid": video_id} if video_id.startswith("BV") else {"aid": video_id[2:]}
    extractor = BiliBiliIE(ydl)
    headers = {"Referer": url}
    response = extractor._download_json(
        "https://api.bilibili.com/x/web-interface/view", video_id,
        query=query, headers=headers, note="Downloading public video metadata")
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0:
        code = response.get("code") if isinstance(response, dict) else None
        label = str(code) if isinstance(code, int) else "missing"
        raise ContentError(f"B站公开元数据 API 拒绝访问或未返回成功（code={label}）")
    data = response.get("data")
    if not isinstance(data, dict):
        raise ContentError("B站公开元数据 API 未返回有效视频信息")
    rights = data.get("rights") or {}
    if (any(_flag(data.get(key)) for key in ("is_upower_exclusive", "is_preview", "need_login", "is_area_limit"))
            or any(_flag(rights.get(key)) for key in ("pay", "ugc_pay", "ugc_pay_preview"))
            or data.get("state") not in (None, 0)):
        raise ContentError("B站公开 API 返回付费、预览或受限视频；未下载音频")
    pages = data.get("pages") or []
    page = next((item for index, item in enumerate(pages, 1)
                 if isinstance(item, dict) and item.get("page", index) == page_number), None)
    if page is None:
        raise ContentError(f"B站公开 API 未返回第 {page_number} P；未下载其他分 P")
    cid = page.get("cid")
    bvid = data.get("bvid")
    if video_id.startswith("BV") and bvid != video_id:
        raise ContentError("B站公开 API 返回的视频 ID 与请求不符；未下载音频")
    expected_duration = _positive_duration(page.get("duration"))
    if not cid or not bvid or not expected_duration:
        raise ContentError("B站公开 API 缺少所选分 P 的 ID 或完整时长，无法核验音频")
    play_info = extractor._download_playinfo(bvid, cid, headers=headers)
    if not isinstance(play_info, dict):
        raise ContentError("B站公开播放 API 未明确返回成功")
    if (any(_flag(play_info.get(key)) for key in (
            "is_preview", "need_login", "is_pay", "is_area_limit", "is_upower_exclusive"))
            or any("试看" in str(value) for value in (play_info.get("accept_description") or []))):
        raise ContentError("B站公开播放 API 只提供预览或要求访问权限；未下载音频")
    duration = _positive_duration(play_info.get("timelength")) / 1000
    if not duration or abs(duration - expected_duration) > max(2, expected_duration * 0.01):
        raise ContentError("B站公开播放 API 音频时长与所选分 P 不符，可能不完整；未下载音频")
    formats = [fmt for fmt in extractor.extract_formats(play_info)
               if fmt.get("vcodec") == "none" and fmt.get("url")]
    if not formats:
        raise ContentError("B站公开播放 API 返回成功，但没有可用纯音轨；未下载音频")
    # Keep only the provider's own alternatives for the same audio stream. These
    # stay in memory, never in the exported source metadata or diagnostic text.
    dash = play_info.get("dash") or {}
    audio_streams = list(dash.get("audio") or []) + list((dash.get("dolby") or {}).get("audio") or [])
    if (dash.get("flac") or {}).get("audio"):
        audio_streams.append(dash["flac"]["audio"])
    for fmt in formats:
        stream = next((item for item in audio_streams
                       if isinstance(item, dict) and (item.get("baseUrl") or item.get("base_url") or item.get("url")) == fmt["url"]), {})
        backups = stream.get("backupUrl") or stream.get("backup_url") or []
        if isinstance(backups, str):
            backups = [backups]
        fmt["_podcast_scribe_backup_urls"] = list(dict.fromkeys(
            item for item in backups if isinstance(item, str)
            and urlparse(item).scheme in ("http", "https") and item != fmt["url"]))[:2]
    title = data.get("title") or bvid
    if len(pages) > 1:
        title += f" p{page_number:02d} {page.get('part') or ''}"
    return {"id": bvid, "title": title, "description": data.get("desc") or "",
            "timestamp": data.get("pubdate"), "duration": duration,
            "uploader": (data.get("owner") or {}).get("name") or "",
            "formats": formats, "http_headers": headers, "webpage_url": url,
            "extractor": "BiliBili", "extractor_key": "BiliBili"}


def _extract_single(ydl, url: str) -> dict:
    from yt_dlp.utils import DownloadError

    _requested_page(url)
    try:
        info = _single(ydl.extract_info(url, download=False))
        if getattr(ydl.params.get("logger"), "preview_only", False):
            raise ContentError("来源仅提供充电或付费内容的试看，未下载音频；请使用你有权取得的完整音视频。")
        return info
    except DownloadError as webpage_error:
        if _access_denied(str(webpage_error)):
            raise _source_error(webpage_error) from webpage_error
        try:
            return _public_api_info(ydl, url)
        except ContentError as api_error:
            raise ContentError(f"网页提取失败（{_source_reason(webpage_error)}）；{api_error}。{_access_hint()}") from api_error
        except Exception as api_error:
            raise ContentError(
                f"网页提取失败（{_source_reason(webpage_error)}）；公开 API 请求失败（{_source_reason(api_error)}）；未取得音频。{_access_hint()}"
            ) from api_error


def inspect_source(value: str, *, auth=None) -> dict:
    url = normalize_url(value)
    try:
        with _ydl({"skip_download": True}) as ydl:
            if auth is not None:
                auth.attach(ydl)
            info = _extract_single(ydl, url)
    except ContentError:
        raise
    except Exception as exc:
        raise _source_error(exc) from exc
    stamp = info.get("timestamp")
    published = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else info.get("upload_date", "")
    if re.fullmatch(r"\d{8}", published):
        published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
    page = str(_requested_page(url))
    ident = info.get("id")
    if not ident:
        match = re.search(r"BV[A-Za-z0-9]{10}", url)
        if not match:
            raise ContentError("未能解析来源视频 ID")
        ident = match.group()
    if page != "1":
        ident += "-p" + page
    return {"id": ident, "title": info.get("title") or ident, "description": info.get("description") or "",
            "duration_seconds": info.get("duration") or 0, "published_at": published,
            "source": {"platform": "bilibili", "url": url, "video_id": info.get("id"), "author": info.get("uploader") or ""},
            "subtitle_languages": sorted(set(info.get("subtitles", {})) | set(info.get("automatic_captions", {})))}


def fetch_audio(value: str, work_dir: Path, *, auth=None) -> Path:
    url = normalize_url(value)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Only complete files with supported extensions count as cached audio.
    for path in sorted(work_dir.glob("source.*")):
        if _complete_audio(path):
            return path
    try:
        with _ydl({"format": "bestaudio/best", "outtmpl": str(work_dir / "source.%(ext)s"),
                   "max_filesize": 1024 * 1024 * 1024, "overwrites": True}) as ydl:
            if auth is not None:
                auth.attach(ydl)
            info = _extract_single(ydl, url)
            info = _download_audio(ydl, info)
            path = Path(ydl.prepare_filename(info))
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                path = Path(requested[0]["filepath"])
        if not _complete_audio(path):
            raise ContentError("音频下载未完成，未生成文稿")
        return path
    except ContentError:
        raise
    except Exception as exc:
        raise _source_error(exc) from exc


def _complete_audio(path: Path) -> bool:
    return (path.suffix in (".m4a", ".mp3", ".webm", ".mp4", ".ogg", ".flac", ".wav")
            and ".part" not in path.suffixes
            and path.is_file() and path.stat().st_size > 0
            and not Path(str(path) + ".part").exists())


def _download_audio(ydl, info: dict) -> dict:
    from yt_dlp.utils import DownloadError

    selected = _single(ydl.process_ie_result(info, download=False))
    try:
        return _single(ydl.process_ie_result(deepcopy(selected), download=True))
    except DownloadError as first_error:
        last_error = first_error
        for backup_url in selected.get("_podcast_scribe_backup_urls", [])[:2]:
            alternative = deepcopy(selected)
            alternative.pop("formats", None)
            alternative.pop("requested_downloads", None)
            alternative["url"] = backup_url
            try:
                return _single(ydl.process_ie_result(alternative, download=True))
            except DownloadError as backup_error:
                last_error = backup_error
        detail = (
            f"音频下载失败（{_source_reason(first_error)}）；来源提供的可用备用地址也未完成下载（{_source_reason(last_error)}）"
            if selected.get("_podcast_scribe_backup_urls") else f"音频下载失败（{_source_reason(first_error)}）；来源未提供可用备用地址"
        )
        raise ContentError(detail + "。" + _access_hint()) from last_error
