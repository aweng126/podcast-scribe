"""Owned media lifetimes; transcripts and caller-provided inputs are durable.

Registration is opt-in at the point a media file is produced. Cleanup never
discovers ownership from a filename/glob. A shared lock spans the entire media
command; collection needs the exclusive lock and therefore skips active work.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

from .model import ContentError, load_episode

INDEX = ".media-index.json"
MEDIA_SUFFIXES = {".mp3", ".m4a", ".mp4", ".wav", ".mkv", ".webm", ".mov", ".flac", ".ogg"}
_current = ContextVar("podcast_scribe_media_session", default=None)


class CacheBusy(ContentError):
    pass


def _absolute(path):
    return Path(os.path.abspath(path))


def _no_symlinks(path):
    path = _absolute(path)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ContentError(f"缓存路径含符号链接，已保留：{path}")
    return path


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path, value):
    _no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".lifecycle-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read(path, default):
    _no_symlinks(path)
    if not path.exists():
        return default
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContentError(f"生命周期记录损坏，未清理文件：{path}") from exc
    if not isinstance(result, dict) or result.get("version") != 1:
        raise ContentError(f"生命周期记录版本无效：{path}")
    return result


@contextmanager
def _lock(root, name, *, shared=False, blocking=False):
    import fcntl
    root = _no_symlinks(root)
    root.mkdir(parents=True, exist_ok=True)
    path = _no_symlinks(root / name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(stream.fileno(), flags | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise CacheBusy(f"缓存正在使用，已跳过清理：{root}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _index(root):
    value = _read(root / INDEX, {"version": 1, "files": {}})
    if not isinstance(value.get("files"), dict):
        raise ContentError("媒体登记表无效，未清理文件")
    for relative, entry in value["files"].items():
        if (not isinstance(relative, str) or not isinstance(entry, dict)
                or not isinstance(entry.get("owners"), dict)
                or any(not isinstance(key, str) or not isinstance(val, bool)
                       for key, val in entry["owners"].items())
                or not isinstance(entry.get("sha256"), str)
                or len(entry["sha256"]) != 64
                or not isinstance(entry.get("size"), int) or entry["size"] < 0):
            raise ContentError("媒体登记项无效，未清理文件")
    return value


def _relative(root, path):
    try:
        return _absolute(path).relative_to(root).as_posix()
    except ValueError as exc:
        raise ContentError("生成媒体不在当前缓存目录内，未登记清理") from exc


def _candidate(root, relative):
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or Path(relative).is_absolute() or any(p in {".", ".."} for p in relative.split("/"))):
        raise ContentError("媒体登记路径无效")
    path = _no_symlinks(root / relative)
    if path.suffix.lower() not in MEDIA_SUFFIXES:
        raise ContentError("仅允许回收已登记的媒体文件")
    return path


@dataclass
class _Session:
    root: Path
    protected: set[Path]
    files: set[str] = field(default_factory=set)


@contextmanager
def media_session(cache, *, protected=()):
    root = _no_symlinks(cache)
    inputs = {_absolute(p).resolve() for p in protected if p is not None}
    # Persist input protection under the media lock *before* any collector can
    # run between expiry collection and this task acquiring its long-lived lock.
    with _lock(root, ".media-use.lock", shared=True), _lock(root, ".media-index.lock", blocking=True):
        index = _index(root)
        for relative, entry in index["files"].items():
            if (root / relative).resolve() in inputs:
                entry["external_input"] = True
        _write(root / INDEX, index)
    # Collection failures must never prevent a transcription or hide its error.
    try:
        prune_expired(root, apply=True, protected=inputs)
    except (OSError, ContentError):
        pass
    with _lock(root, ".media-use.lock", shared=True):
        session = _Session(root, inputs)
        token = _current.set(session)
        try:
            yield
        finally:
            _current.reset(token)


def register_media(path):
    session = _current.get()
    if session is None:
        return
    relative = _relative(session.root, path)
    path = _candidate(session.root, relative)
    if path.resolve() in session.protected:
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ContentError("仅登记工具生成的独立普通媒体文件")
    digest = _hash(path)
    with _lock(session.root, ".media-index.lock", blocking=True):
        index = _index(session.root)
        previous = index["files"].get(relative, {})
        if previous.get("external_input"):
            return
        index["files"][relative] = {"sha256": digest, "size": info.st_size,
            "last_used": time.time(), "removed": False, "owners": previous.get("owners", {})}
        _write(session.root / INDEX, index)
    session.files.add(relative)


def touch_media_tree(work):
    session = _current.get()
    if session is None:
        return
    prefix = _relative(session.root, work).rstrip("/") + "/"
    with _lock(session.root, ".media-index.lock", blocking=True):
        index = _index(session.root)
        for relative, entry in index["files"].items():
            if relative.startswith(prefix) and isinstance(entry, dict):
                entry["last_used"] = time.time()
                session.files.add(relative)
        _write(session.root / INDEX, index)


def was_removed(path, expected_sha256):
    session = _current.get()
    if session is None:
        return False
    relative = _relative(session.root, path)
    _candidate(session.root, relative)
    with _lock(session.root, ".media-index.lock", blocking=True):
        entry = _index(session.root)["files"].get(relative, {})
        return (entry.get("removed") is True and not entry.get("external_input")
                and entry.get("sha256") == expected_sha256)


def _sidecar(episode_path):
    return _absolute(episode_path).with_name(Path(episode_path).name + ".resources.json")


def bind_episode(episode_path):
    session = _current.get()
    if session is None or not session.files:
        return
    owner = str(_absolute(episode_path))
    sidecar = _sidecar(episode_path)
    from .defaults import destination_lock
    with destination_lock(sidecar):
        saved = _read(sidecar, {"version": 1, "episode": owner, "roots": {}})
        if saved.get("episode") != owner or not isinstance(saved.get("roots"), dict):
            raise ContentError("媒体归属记录与文稿不符")
        saved["roots"][str(session.root)] = sorted(set(saved["roots"].get(str(session.root), [])) | session.files)
        with _lock(session.root, ".media-index.lock", blocking=True):
            index = _index(session.root)
            for relative in session.files:
                index["files"][relative].setdefault("owners", {}).setdefault(owner, False)
            _write(session.root / INDEX, index)
        _write(sidecar, saved)


def _fingerprint(episode):
    value = {key: val for key, val in episode.items() if key != "artifacts"}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _receipt(path):
    return _absolute(path).with_name(Path(path).name + ".delivery.json")


def record_delivery(episode_path, episode, paths):
    """Record only complete, simultaneous MD/PDF exports after basic validation."""
    review = episode.get("review", {})
    if (not review.get("content_checked") or not review.get("speakers_confirmed")
            or set(paths) != {"markdown", "pdf"}):
        return False
    from .exporters import render_markdown
    md, pdf = Path(paths["markdown"]), Path(paths["pdf"])
    if md.read_text(encoding="utf-8") != render_markdown(episode):
        raise ContentError("导出 Markdown 与当前稿不一致，已保留媒体")
    with pdf.open("rb") as stream:
        header = stream.read(5)
        stream.seek(max(0, pdf.stat().st_size - 1024))
        ending = stream.read().rstrip()
    if header != b"%PDF-" or not ending.endswith(b"%%EOF"):
        raise ContentError("PDF 导出基础校验失败，已保留媒体")
    _write(_receipt(episode_path), {"version": 1, "episode_sha256": _fingerprint(episode),
        "artifacts": {key: {"path": str(_absolute(path)), "sha256": _hash(path)} for key, path in paths.items()}})
    return True


def _delivered(path):
    try:
        ep = load_episode(_no_symlinks(path))
        receipt = _read(_receipt(path), {})
        if (receipt.get("episode_sha256") != _fingerprint(ep)
                or set(receipt.get("artifacts", {})) != {"markdown", "pdf"}):
            return False
        return all(_hash(_no_symlinks(item["path"])) == item["sha256"] for item in receipt["artifacts"].values())
    except (OSError, ContentError, ValueError, KeyError, TypeError):
        return False


def _report():
    return {"files": [], "bytes": 0, "skipped": []}


def _collect(root, *, apply, selected=None, owner=None, cutoff=None, protected=()):
    report = _report()
    if not root.exists():
        return report
    try:
        with _lock(root, ".media-use.lock"), _lock(root, ".media-index.lock", blocking=True):
            index = _index(root)
            for relative, entry in index["files"].items():
                if selected is not None and relative not in selected:
                    continue
                try:
                    path = _candidate(root, relative)
                    if not isinstance(entry, dict) or not isinstance(entry.get("owners"), dict):
                        raise ContentError("无效登记项")
                    if entry.get("external_input") or path.resolve() in protected:
                        raise ContentError("用户原文件")
                    owners = entry["owners"]
                    if owner is not None and owner not in owners:
                        raise ContentError("不属于此文稿")
                    if any(pinned for key, pinned in owners.items() if key != owner):
                        raise ContentError("已选择保留媒体")
                    if owner is not None and any(not _delivered(key) for key in owners if key != owner):
                        raise ContentError("其他文稿仍需复用")
                    if cutoff is not None:
                        used = entry.get("last_used")
                        if not isinstance(used, (int, float)) or not math.isfinite(used) or used > cutoff:
                            continue
                        if len(owners) > 1 and any(not _delivered(key) for key in owners):
                            raise ContentError("共享未交付任务")
                    if not path.exists():
                        continue
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ContentError("非独立普通文件")
                    if info.st_size != entry.get("size") or _hash(path) != entry.get("sha256"):
                        raise ContentError("文件已改变，保留排查")
                    if apply:
                        # Journal before unlink: interruption after deletion must
                        # still allow a partial ASR task to rebuild this media.
                        entry["removed"] = True
                        _write(root / INDEX, index)
                        _no_symlinks(path)
                        current = path.lstat()
                        if any(getattr(current, key) != getattr(info, key) for key in
                               ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
                            raise ContentError("清理期间文件改变")
                        path.unlink()
                    report["files"].append(str(path))
                    report["bytes"] += info.st_size
                except (OSError, ContentError, ValueError, KeyError, TypeError) as exc:
                    report["skipped"].append({"file": relative, "reason": str(exc)})
    except CacheBusy:
        report["skipped"].append({"file": str(root), "reason": "缓存正在使用"})
    return report


def prune_expired(cache, *, older_than_days=7, apply=False, protected=()):
    if (isinstance(older_than_days, bool) or not isinstance(older_than_days, (int, float))
            or not math.isfinite(older_than_days) or older_than_days <= 0):
        raise ContentError("过期天数必须是正数")
    root = _no_symlinks(cache)
    return _collect(root, apply=apply, cutoff=time.time() - older_than_days * 86400,
                    protected={_absolute(p).resolve() for p in protected})


def cleanup_episode(episode_path, *, apply=False, keep_media=False):
    owner = str(_absolute(episode_path))
    if not keep_media and not _delivered(episode_path):
        raise ContentError("尚无当前版本完整且有效的 Markdown/PDF 交付记录，已保留媒体；请完成后重新 export")
    saved = _read(_sidecar(episode_path), {"version": 1, "episode": owner, "roots": {}})
    if saved.get("episode") != owner or not isinstance(saved.get("roots"), dict):
        raise ContentError("媒体归属记录与文稿不符")
    report = _report()
    for root_name, files in saved["roots"].items():
        root = _no_symlinks(root_name)
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise ContentError("媒体归属文件列表无效")
        if keep_media or apply:
            with _lock(root, ".media-use.lock", shared=True), _lock(root, ".media-index.lock", blocking=True):
                index = _index(root)
                for relative in files:
                    entry = index["files"].get(relative, {})
                    if owner in entry.get("owners", {}):
                        entry["owners"][owner] = keep_media
                _write(root / INDEX, index)
        if keep_media:
            continue
        result = _collect(root, apply=apply, selected=set(files), owner=owner)
        report["files"].extend(result["files"])
        report["bytes"] += result["bytes"]
        report["skipped"].extend(result["skipped"])
    report.update(applied=apply and not keep_media, keep_media=keep_media)
    return report
