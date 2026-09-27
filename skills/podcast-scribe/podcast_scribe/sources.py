"""Single-video Bilibili acquisition; no implicit cookies or playlist expansion."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
import json
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
    subtitle_login_required = False

    def debug(self, *_): pass
    def warning(self, message):
        # yt-dlp can return success after warning that only a paid preview is
        # available. Record just the condition, not its signed URL or message.
        if "only the preview will be extracted" in str(message).lower():
            self.preview_only = True
        if "subtitles are only available when logged in" in str(message).lower():
            self.subtitle_login_required = True
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
    elif "视频状态受限" in message:
        reason = "来源视频状态受限"
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


def _public_api_info(ydl, url: str, *, audio_only: bool = True) -> dict:
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
               if (not audio_only or fmt.get("vcodec") == "none") and fmt.get("url")]
    if not formats:
        raise ContentError("B站公开播放 API 返回成功，但没有可用纯音轨；未下载音频")
    # Keep only the provider's own alternatives for the same audio stream. These
    # stay in memory, never in the exported source metadata or diagnostic text.
    dash = play_info.get("dash") or {}
    audio_streams = list(dash.get("audio") or []) + list((dash.get("dolby") or {}).get("audio") or [])
    if not audio_only:
        audio_streams += list(dash.get("video") or [])
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


def _extract_single(ydl, url: str, *, audio_only: bool = True) -> dict:
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
        # Subtitle fetching is optional to metadata/audio acquisition. Some
        # extractor versions fail the whole page when one subtitle URL fails.
        if ydl.params.get("writesubtitles"):
            previous = ydl.params.get("writesubtitles")
            ydl.params["writesubtitles"] = False
            try:
                info = _single(ydl.extract_info(url, download=False))
                if getattr(ydl.params.get("logger"), "preview_only", False):
                    raise ContentError("来源仅提供试看；请使用你有权取得的完整音视频。")
                return info
            except DownloadError as retry_error:
                if _access_denied(str(retry_error)):
                    raise _source_error(retry_error) from retry_error
            finally:
                ydl.params["writesubtitles"] = previous
        try:
            return _public_api_info(ydl, url) if audio_only else _public_api_info(ydl, url, audio_only=False)
        except ContentError as api_error:
            raise ContentError(f"网页提取失败（{_source_reason(webpage_error)}）；{api_error}。{_access_hint()}") from api_error
        except Exception as api_error:
            raise ContentError(
                f"网页提取失败（{_source_reason(webpage_error)}）；公开 API 请求失败（{_source_reason(api_error)}）；未取得音频。{_access_hint()}"
            ) from api_error


def inspect_source(value: str, *, auth=None) -> dict:
    url = normalize_url(value)
    try:
        with _ydl({"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                   "subtitleslangs": ["all", "-danmaku"]}) as ydl:
            if auth is not None:
                auth.attach(ydl)
            info = _extract_single(ydl, url)
            subtitle_login_required = getattr(ydl.params.get("logger"), "subtitle_login_required", False)
    except ContentError:
        raise
    except Exception as exc:
        raise _source_error(exc) from exc
    stamp = info.get("timestamp")
    published = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else info.get("upload_date", "")
    if re.fullmatch(r"\d{8}", published):
        published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
    page = str(_requested_page(url))
    ident = re.sub(r"_p\d+$", "", str(info.get("id") or ""))
    if not ident:
        match = re.search(r"BV[A-Za-z0-9]{10}", url)
        if not match:
            raise ContentError("未能解析来源视频 ID")
        ident = match.group()
    video_id = ident
    if page != "1":
        ident += "-p" + page
    languages = sorted({language for key in ("subtitles", "automatic_captions")
                        for language, tracks in (info.get(key) or {}).items()
                        if language != "danmaku" and tracks})
    return {"id": ident, "title": info.get("title") or ident, "description": info.get("description") or "",
            "duration_seconds": info.get("duration") or 0, "published_at": published,
            "source": {"platform": "bilibili", "url": url, "video_id": video_id, "author": info.get("uploader") or ""},
            "subtitle_languages": languages,
            "subtitle_status": "available" if languages else "login_required" if subtitle_login_required else "not_checked"}


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


def _subtitle_identity(value: str) -> dict:
    url = normalize_url(value)
    parsed, page = urlparse(url), _requested_page(url)
    video_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    canonical = (f"https://b23.tv/{video_id}" if parsed.hostname == "b23.tv"
                 else f"https://www.bilibili.com/video/{video_id}")
    if page != 1:
        canonical += f"?p={page}"
    return {"kind": "bilibili", "platform": "bilibili", "url": canonical,
            "video_id": video_id if parsed.hostname != "b23.tv" else "", "page": page}


def _subtitle_hash(result: dict) -> str:
    payload = {key: value for key, value in result.items() if key != "content_sha256"}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _cue_rows(rows) -> list[dict]:
    if not isinstance(rows, list) or not rows or len(rows) > 500000:
        raise ContentError("字幕没有有效的时间轴正文")
    cues, previous = [], -1.0
    for row in rows:
        if not isinstance(row, dict):
            raise ContentError("字幕段落不是有效对象")
        start, end, text = row.get("start", row.get("from")), row.get("end", row.get("to")), row.get("text", row.get("content"))
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or start < previous or not isinstance(text, str)):
            raise ContentError("字幕时间轴或文本无效")
        previous = start
        text = text.strip()
        if text:
            cues.append({"id": f"cue-{len(cues) + 1:05d}", "start": float(start), "end": float(end), "text": text})
    if not cues:
        raise ContentError("字幕没有有效的时间轴正文")
    return cues


def _parse_subtitle(text: str, fmt: str) -> list[dict]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 32 * 1024 * 1024:
        raise ContentError("字幕正文无效或超过 32 MB")
    if fmt == "json":
        obj = json.loads(text)
        return _cue_rows(obj if isinstance(obj, list) else obj.get("body", obj.get("segments")))
    if fmt not in ("srt", "vtt"):
        raise ContentError("字幕格式暂不支持")
    from .transcripts import _seconds
    rows = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        if block.strip().startswith(("NOTE", "STYLE", "REGION")):
            continue
        lines = block.strip().splitlines()
        for index, line in enumerate(lines):
            match = re.fullmatch(r"([\d:.,]+)\s+-->\s+([\d:.,]+)(?:\s+.*)?", line)
            if match:
                body = unescape(re.sub(r"<[^>]+>", "", " ".join(lines[index + 1:])))
                rows.append({"start": _seconds(match[1]), "end": _seconds(match[2]), "text": body})
                break
    return _cue_rows(rows)


def _subtitle_origin(language: str, track: dict, *, automatic=False) -> str:
    # Bilibili's yt-dlp extractor places AI captions in `subtitles` as well.
    # A dictionary name alone must never label a track as human-authored.
    if (automatic or language.lower().startswith("ai-") or track.get("is_auto") is True or track.get("automatic") is True
            or (type(track.get("ai_type")) is int and track["ai_type"] > 0)):
        return "automatic"
    if track.get("is_auto") is False or track.get("automatic") is False:
        return "manual"
    return "unknown"


def _web_subtitle_tracks(info: dict) -> list[dict]:
    tracks = []
    for key in ("subtitles", "automatic_captions"):
        for language, alternatives in (info.get(key) or {}).items():
            if not isinstance(language, str) or language.lower() == "danmaku" or not isinstance(alternatives, list):
                continue
            for track in alternatives:
                if not isinstance(track, dict) or track.get("ext") not in ("json", "srt", "vtt"):
                    continue
                if track.get("data") is None and not track.get("url"):
                    continue
                tracks.append({"language": language, "origin": _subtitle_origin(language, track, automatic=key == "automatic_captions"),
                               "format": track["ext"], "data": track.get("data"), "url": track.get("url")})
    return tracks


def _subtitle_api_tracks(ydl, url: str) -> tuple[dict, list[dict], str]:
    from yt_dlp.extractor.bilibili import BiliBiliIE
    identity = _subtitle_identity(url)
    video_id = identity["video_id"]
    if not video_id:
        raise ContentError("字幕接口无法解析短链接，请使用原始 BV 视频链接")
    extractor = BiliBiliIE(ydl)
    headers = {"Referer": identity["url"]}
    query = {"bvid": video_id} if video_id.startswith("BV") else {"aid": video_id[2:]}
    view = extractor._download_json("https://api.bilibili.com/x/web-interface/view", video_id,
                                   query=query, headers=headers, note="Checking subtitle source")
    if not isinstance(view, dict) or type(view.get("code")) is not int or view["code"] != 0:
        if isinstance(view, dict) and view.get("code") == -101:
            return identity, [], "login_required"
        raise ContentError("字幕来源元数据接口未明确返回成功")
    data = view.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ContentError("字幕来源缺少分 P 信息")
    if data.get("state") not in (None, 0):
        raise ContentError("字幕来源视频状态受限；未请求字幕")
    rights = data.get("rights") or {}
    if (any(_flag(data.get(key)) for key in ("is_upower_exclusive", "is_preview", "need_login", "is_area_limit"))
            or any(_flag(rights.get(key)) for key in ("pay", "ugc_pay", "ugc_pay_preview"))):
        return identity, [], "login_required"
    bvid = data.get("bvid")
    if not isinstance(bvid, str) or not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid) or (video_id.startswith("BV") and bvid != video_id):
        raise ContentError("字幕来源视频 ID 与请求不符")
    page = next((item for index, item in enumerate(data["pages"], 1)
                 if isinstance(item, dict) and item.get("page", index) == identity["page"]), None)
    if page is None or not page.get("cid"):
        raise ContentError("字幕来源没有请求的分 P")
    identity = {**identity, "video_id": bvid, "cid": page["cid"]}
    player = extractor._download_json("https://api.bilibili.com/x/player/wbi/v2", bvid,
                                     query={"bvid": bvid, "cid": page["cid"]}, headers=headers,
                                     note="Checking subtitle tracks")
    if not isinstance(player, dict) or type(player.get("code")) is not int or player["code"] != 0:
        if isinstance(player, dict) and player.get("code") == -101:
            return identity, [], "login_required"
        raise ContentError("字幕接口未明确返回成功")
    details = player.get("data")
    if not isinstance(details, dict):
        raise ContentError("字幕接口未返回有效数据")
    if _flag(details.get("need_login_subtitle")):
        return identity, [], "login_required"
    subtitle = details.get("subtitle")
    if not isinstance(subtitle, dict) or not isinstance(subtitle.get("subtitles"), list):
        raise ContentError("字幕接口没有明确返回字幕列表")
    tracks = []
    for track in subtitle["subtitles"]:
        if not isinstance(track, dict) or not isinstance(track.get("lan"), str) or not track.get("subtitle_url"):
            raise ContentError("字幕接口返回了无效字幕轨")
        if track["lan"].lower() == "danmaku":
            continue
        tracks.append({"language": track["lan"], "origin": _subtitle_origin(track["lan"], track),
                       "format": "json", "data": None, "url": track["subtitle_url"]})
    return identity, tracks, "available" if tracks else "no_subtitles"


def _read_subtitle_track(ydl, track: dict, source: dict) -> list[dict]:
    text = track.get("data")
    if text is None:
        from yt_dlp.extractor.bilibili import BiliBiliIE
        url = track.get("url")
        if not isinstance(url, str):
            raise ContentError("字幕轨缺少正文")
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or not any(host == domain or host.endswith("." + domain) for domain in ("bilibili.com", "hdslb.com"))):
            raise ContentError("字幕轨地址不属于来源平台")
        text = BiliBiliIE(ydl)._download_webpage(url, source["video_id"],
                                               headers={"Referer": source["url"]}, note="Reading subtitle text")
    return _parse_subtitle(text, track["format"])


def _subtitle_cache(path: Path, requested: dict, *, authorized=False) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("schema_version") != 1
                or value.get("requested_source") != requested
                or value.get("status") not in ("available", "no_subtitles")
                or (value.get("status") == "no_subtitles" and value.get("access_mode") != ("authorized" if authorized else "anonymous"))
                or value.get("content_sha256") != _subtitle_hash(value)):
            return None
        source = value.get("source")
        if (not isinstance(source, dict) or source.get("kind") != "bilibili"
                or (requested["video_id"] and source.get("page") != requested["page"])
                or (requested["video_id"].startswith("BV") and source.get("video_id") != requested["video_id"])):
            return None
        tracks = value.get("tracks")
        if not isinstance(tracks, list) or any(set(track) != {"id", "language", "origin", "format"}
                or track["origin"] not in ("manual", "automatic", "unknown") for track in tracks):
            return None
        if value["status"] == "available":
            if value.get("selected_track") not in tracks or _cue_rows(value.get("cues")) != value["cues"]:
                return None
        elif tracks or value.get("selected_track") is not None or value.get("cues") != []:
            return None
        return value
    except (OSError, ValueError, TypeError, KeyError, ContentError):
        return None


def fetch_subtitles(value: str, work_dir: Path, *, auth=None, refresh=False) -> dict:
    """Fetch timestamped text, never video or implicit credentials.

    Failures are explicit sidecar states and never mean there are no subtitles.
    Successful caches are source-bound and hashed; credentials and temporary
    subtitle URLs are never persisted. `refresh` retries a previously empty list.
    """
    requested = _subtitle_identity(value)
    work_dir = Path(work_dir)
    path = work_dir / "subtitles.json"
    if not refresh and (cached := _subtitle_cache(path, requested, authorized=auth is not None)) is not None:
        return cached
    result = {"schema_version": 1, "requested_source": requested, "source": requested.copy(),
              "access_mode": "authorized" if auth is not None else "anonymous",
              "checked_at": datetime.now(timezone.utc).isoformat(), "status": "unavailable", "reason": "",
              "tracks": [], "selected_track": None, "cues": []}
    try:
        with _ydl({"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                   "subtitleslangs": ["all", "-danmaku"]}) as ydl:
            if auth is not None:
                auth.attach(ydl)
            try:
                info = _single(ydl.extract_info(requested["url"], download=False))
                if getattr(ydl.params.get("logger"), "preview_only", False):
                    raise ContentError("来源仅提供试看或要求访问权限")
                page_match = re.search(r"_p(\d+)$", str(info.get("id") or ""))
                if requested["video_id"] and page_match and int(page_match[1]) != requested["page"]:
                    raise ContentError("字幕来源分 P 与请求不符")
                ident = re.sub(r"_p\d+$", "", str(info.get("id") or ""))
                if not re.fullmatch(r"BV[A-Za-z0-9]{10}", ident) or (requested["video_id"].startswith("BV") and ident != requested["video_id"]):
                    raise ContentError("字幕来源视频 ID 与请求不符")
                resolved = info.get("webpage_url") or requested["url"]
                resolved_identity = _subtitle_identity(resolved)
                source = {**requested, "video_id": ident}
                if not requested["video_id"] and resolved_identity["video_id"]:
                    source = {**resolved_identity, "video_id": ident}
                    if page_match:
                        source["page"] = int(page_match[1])
                tracks = _web_subtitle_tracks(info)
            except Exception as exc:
                # Access denial does not cause another access path to be tried.
                if _access_denied(str(exc)):
                    result.update(status="login_required", reason="字幕来源要求登录或访问权限")
                    tracks, source, resolved = [], requested, None
                else:
                    tracks, source, resolved = [], requested, requested["url"]
            if resolved is not None and not tracks:
                source, tracks, result["status"] = _subtitle_api_tracks(ydl, resolved)
            result["source"] = source
            public_tracks = [{"id": f"track-{index:03d}", **{key: track[key] for key in ("language", "origin", "format")}}
                             for index, track in enumerate(tracks, 1)]
            result["tracks"] = public_tracks
            # Original-language Chinese first, explicit human track before AI;
            # remaining ties follow the provider's stable ordering.
            order = sorted(range(len(tracks)), key=lambda index: (
                0 if re.match(r"^(?:ai-)?(?:zh|cmn)(?:-|$)", tracks[index]["language"].lower()) else 1,
                {"manual": 0, "unknown": 1, "automatic": 2}[tracks[index]["origin"]]))
            for index in order[:4]:
                try:
                    cues = _read_subtitle_track(ydl, tracks[index], source)
                except Exception:
                    continue
                result.update(status="available", selected_track=public_tracks[index], cues=cues)
                break
            if tracks and result["selected_track"] is None:
                result.update(status="unavailable", reason="字幕轨存在，但正文读取失败或时间轴无效")
    except Exception as exc:
        from .source_auth import CookieAccessError
        result["status"] = "login_required" if isinstance(exc, CookieAccessError) or _access_denied(str(exc)) else "unavailable"
        result["reason"] = "授权登录态不可用" if isinstance(exc, CookieAccessError) else "字幕查询未完成：" + _source_reason(exc)
    result["reason"] = result["reason"] or {"available": "已取得独立字幕轨", "no_subtitles": "接口成功返回空字幕列表；画面字幕尚未检查",
                                            "login_required": "字幕接口要求登录", "unavailable": "字幕查询未完成"}[result["status"]]
    result["content_sha256"] = _subtitle_hash(result)
    work_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return result


def fetch_video(value: str, work_dir: Path, *, auth=None) -> Path:
    """Fetch only the requested video for an explicitly requested OCR check."""
    url, work_dir = normalize_url(value), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    identity, manifest = _subtitle_identity(url), work_dir / "source-video-manifest.json"
    try:
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        filename = saved["filename"]
        path = work_dir / filename
        if (saved.get("schema_version") == 1 and saved.get("source") == identity
                and isinstance(filename, str) and re.fullmatch(r"source-video\.(?:mp4|webm|mkv|mov)", filename)
                and _complete_video(path) and saved.get("sha256") == _file_hash(path)):
            return path
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        with _ydl({"format": "bestvideo[height<=480]/best[height<=480]/worstvideo/worst",
                   "outtmpl": str(work_dir / "source-video.%(ext)s"),
                   "max_filesize": 1024 * 1024 * 1024, "overwrites": True}) as ydl:
            if auth is not None:
                auth.attach(ydl)
            info = _extract_single(ydl, url, audio_only=False)
            formats = info.get("formats") or [info]
            formats = [fmt for fmt in formats if fmt.get("vcodec") != "none"]
            if not formats:
                raise ContentError("来源没有可用视频轨；无法检查画面字幕")
            info = {**info, "formats": formats}
            info = _download_audio(ydl, info)
            path = Path(ydl.prepare_filename(info))
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                path = Path(requested[0]["filepath"])
        if not _complete_video(path):
            raise ContentError("视频下载未完成；无法检查画面字幕")
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "source": identity, "filename": path.name,
                                         "sha256": _file_hash(path)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest)
        return path
    except ContentError as exc:
        raise ContentError(str(exc).replace("音频", "视频")) from exc
    except Exception as exc:
        raise ContentError(_source_reason(exc) + "；未取得视频。" + _access_hint()) from exc


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _complete_video(path: Path) -> bool:
    return (path.suffix in (".mp4", ".webm", ".mkv", ".mov") and path.is_file()
            and path.stat().st_size > 0 and not Path(str(path) + ".part").exists())
