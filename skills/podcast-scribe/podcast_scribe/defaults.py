"""Workspace defaults, source identities, and non-overwriting draft creation."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from .model import ContentError, load_episode, safe_id, validate_episode


class SourceConflict(ContentError):
    def __init__(self, path: Path, message: str):
        super().__init__(message)
        self.report = {"status": "error", "code": "source_conflict", "path": str(path.resolve()),
                       "message": message, "next_steps": ["检查原稿来源", "指定新的 --output"]}


def _page(url: str) -> int:
    values = parse_qs(urlparse(url).query, keep_blank_values=True).get("p", ["1"])
    if len(set(values)) != 1 or not values[0].isdigit() or int(values[0]) < 1:
        raise ContentError("分 P 参数必须是单一的正整数")
    return int(values[0])


def video_target(value: str) -> dict | None:
    """Resolve a BV address without a network call; short/av links need metadata."""
    from .sources import normalize_url
    url = normalize_url(value)
    page = _page(url)
    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10})/?", urlparse(url).path)
    if match is None:
        return None
    return _video_target(match[1], page)


def _video_target(bvid: str, page: int) -> dict:
    return {"id": bvid if page == 1 else f"{bvid}-p{page}",
            "url": f"https://www.bilibili.com/video/{bvid}?p={page}",
            "identity": {"kind": "bilibili", "video_id": bvid, "page": page}}


def resolved_video_target(value: str, metadata: dict, *, require_metadata_id: bool = True) -> dict:
    """Canonicalize both yt-dlp's BV_pN and the public API fallback's BV-pN."""
    direct = video_target(value)
    source = metadata.get("source", {})
    detected = []
    for index, ident in enumerate((source.get("video_id"), metadata.get("id"))):
        match = re.fullmatch(r"(BV[A-Za-z0-9]{10})((?:[_-]p\d+)*)", str(ident or ""))
        if match:
            detected.append((match[1], [int(p) for p in re.findall(r"[_-]p(\d+)", match[2])]))
        elif ident not in (None, "") and (index == 0 or require_metadata_id):
            raise ContentError("来源返回了无法核验的非 BV 视频 ID，可能发生跨类型跳转；未下载或转写")
    if require_metadata_id and not detected:
        raise ContentError("来源未返回可核验的 BV 视频 ID；未下载或转写")
    source_target = None
    if source.get("url"):
        try:
            source_target = video_target(source["url"])
        except ContentError:
            pass
    if direct:
        target = direct
    elif source_target:
        target = source_target
    elif detected:
        page = _page(value)
        # A short URL may resolve to a non-first page. The extractor's suffix is
        # evidence; the short address alone cannot establish the resolved page.
        if urlparse(value).hostname == "b23.tv" and "p" not in parse_qs(urlparse(value).query):
            pages = {p for _, suffixes in detected for p in suffixes}
            if len(pages) != 1:
                raise ContentError("短链接未提供可核验的分 P 信息，请使用原始 BV 视频链接")
            page = pages.pop()
        target = _video_target(detected[0][0], page)
    else:
        raise ContentError("未能解析稳定的 BV 来源，请使用原始 BV 视频链接")
    expected = target["identity"]
    if (source_target and source_target["identity"] != expected) or any(
        bvid != expected["video_id"] or any(page != expected["page"] for page in pages)
        for bvid, pages in detected
    ):
        raise ContentError("来源元数据与请求的 BV 或分 P 不一致，未下载或转写")
    return target


def local_target(path: Path, command: str, *, ident: str | None = None, source_url: str = "") -> dict:
    if not path.is_file():
        raise ContentError(f"本地素材不存在：{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    digest = digest.hexdigest()
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ContentError("来源链接仅支持 http/https")
        if parsed.hostname in ("www.bilibili.com", "bilibili.com", "m.bilibili.com"):
            target = video_target(source_url)
            if target:
                source_url = target["url"]
    return {"id": safe_id(ident) if ident else f"{safe_id(path.stem)[:80]}-{digest[:12]}",
            "title": path.stem, "source_url": source_url,
            "identity": {"kind": command, "sha256": digest, "source_url": source_url}}


def existing_report(path: Path, identity: dict, args) -> dict | None:
    if not path.exists() and not path.is_symlink():
        return None
    ep = load_episode(path, for_edit=True)
    stored = ep.get("input_identity")
    if identity["kind"] == "bilibili":
        # Existing edited BV records predate input_identity. Only a definite BV
        # source address can establish equivalence without changing the record.
        try:
            old = video_target(ep.get("source", {}).get("url", ""))
        except ContentError:
            old = None
        if old is None or old["identity"] != identity:
            raise SourceConflict(path, "默认路径已有稿件，但已保存的来源链接不一致或无法核验；已保留原文件，未下载或转写。")
        try:
            resolved_video_target(old["url"], ep, require_metadata_id=False)
        except ContentError as exc:
            raise SourceConflict(path, "原稿的来源链接与视频 ID 或分 P 冲突；已保留原文件，请先检查来源。") from exc
        if stored is None:
            stored = old["identity"]
    elif ep.get("source", {}).get("url", "") != identity.get("source_url", ""):
        raise SourceConflict(path, "默认路径已有稿件，但已保存的来源链接不一致；已保留原文件，未重新处理。")
    if stored != identity:
        raise SourceConflict(path, "默认路径已有稿件，但来源身份不一致或无法核验；已保留原文件，未下载或转写。")
    options = {name: getattr(args, name) for name in ("id", "title", "series_id", "series_title", "language", "review_mode")
               if getattr(args, name, None) is not None}
    return {"status": "existing", "episode": ep["id"], "path": str(path.resolve()),
            "review_mode": ep.get("review", {}).get("mode", "auto"),
            "message": "已找到同一来源的稿件，未覆盖或重新转写。生成参数不会修改已有稿件；请继续 status/batch/edit/export，确需重建则指定新的 --output。",
            "requested_options": options, "next_steps": ["status", "batch", "edit", "export"]}


@contextmanager
def destination_lock(path: Path):
    """Prevent concurrent CLI workers from paying for and replacing one draft."""
    import fcntl  # Supported runtime: macOS, Linux and WSL.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(f".{path.name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContentError(f"稿件正在处理：{path}；已停止重复任务，请等待后重跑原命令。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def save_new_episode(path: Path, episode: dict):
    """Publish a complete new JSON atomically, refusing even a racing new file."""
    validate_episode(episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(episode, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ContentError(f"文件已存在，已保留人工修改：{path}。请使用 edit/export 或指定新的输出路径。") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
