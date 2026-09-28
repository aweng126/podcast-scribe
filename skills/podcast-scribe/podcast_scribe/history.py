"""Lossless retention of local revision snapshots.

Callers hold ``destination_lock(episode_path)`` while backing up or compacting.
Only canonical filenames whose JSON identity agrees are managed here. Compressed
snapshots remain ordinary gzip files and retain the original JSON bytes exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zlib

from .model import ContentError


_READ_ERRORS = (OSError, ValueError, EOFError, zlib.error, RecursionError)


def _identity(data: dict) -> tuple[str, int]:
    if not isinstance(data, dict):
        raise ContentError("历史内容必须是单集 JSON 对象")
    episode_id, revision = data.get("id"), data.get("revision", 1)
    if not isinstance(episode_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", episode_id):
        raise ContentError("历史单集 ID 无效")
    if type(revision) is not int or revision < 1:
        raise ContentError("历史 revision 必须是正整数")
    return episode_id, revision


def _regular_bytes(path: Path) -> tuple[bytes, os.stat_result]:
    # O_NOFOLLOW also protects against a symlink substituted after directory scan.
    if path.is_symlink():
        raise ContentError("跳过符号链接")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ContentError("跳过非普通文件")
        return stream.read(), info


def _history_directory(episode_path: Path, *, create: bool = False) -> Path:
    directory = episode_path.parent / "history"
    if directory.is_symlink():
        raise ContentError("history 目录不能是符号链接")
    if create:
        directory.mkdir(exist_ok=True)
    if directory.exists() and not directory.is_dir():
        raise ContentError("history 路径不是目录")
    return directory


def _atomic_create(path: Path, raw: bytes) -> None:
    """Publish a complete file atomically, without replacing an existing path."""
    descriptor, temporary = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_revision_backup(episode_path: Path, before: dict) -> Path:
    """Create a backup once, recognizing compressed copies from earlier runs.

    An occupied canonical path is preserved. Its conflicting pre-edit snapshot is
    saved separately as a content-addressed gzip file, so legacy user backups do
    not prevent editing and the actual previous content still remains traceable.
    No existing file is replaced.
    """
    episode_path = Path(episode_path)
    episode_id, revision = _identity(before)
    directory = _history_directory(episode_path, create=True)
    backup = directory / f"{episode_id}-r{revision}.json"
    found = []
    occupied = False
    for candidate in (backup, backup.with_suffix(".json.gz")):
        if not candidate.exists() and not candidate.is_symlink():
            continue
        occupied = True
        try:
            raw, _ = _regular_bytes(candidate)
            data = json.loads(gzip.decompress(raw) if candidate.suffix == ".gz" else raw)
            if _identity(data) != (episode_id, revision) or data != before:
                raise ContentError("已有历史版本与当前原稿不同")
        except _READ_ERRORS:
            continue
        found.append(candidate)
    if found:
        return found[0]
    raw = (json.dumps(before, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if occupied:
        digest = hashlib.sha256(raw).hexdigest()
        backup = directory / f"{episode_id}-r{revision}-conflict-{digest}.json.gz"
        try:
            if backup.exists() or backup.is_symlink():
                stored, _ = _regular_bytes(backup)
                if gzip.decompress(stored) != raw:
                    raise ContentError("冲突备份内容不匹配")
                return backup
            packed = gzip.compress(raw, compresslevel=6, mtime=0)
            if gzip.decompress(packed) != raw:
                raise ContentError("冲突备份压缩校验失败")
            _atomic_create(backup, packed)
            stored, _ = _regular_bytes(backup)
            if gzip.decompress(stored) != raw:
                raise ContentError("冲突备份写入后校验失败")
            return backup
        except _READ_ERRORS as exc:
            raise ContentError(f"已保留历史文件，无法另存原稿：{backup}（{exc}）") from exc
    try:
        _atomic_create(backup, raw)
    except OSError as exc:
        raise ContentError(f"历史备份写入失败，原稿未覆盖：{backup}（{exc}）") from exc
    return backup


@dataclass(frozen=True)
class _Snapshot:
    path: Path
    revision: int
    compressed: bool
    key: bool
    size: int
    digest: bytes
    inode: tuple[int, int]


def _snapshot(path: Path, episode_id: str, revision: int) -> _Snapshot:
    stored, info = _regular_bytes(path)
    compressed = path.suffix == ".gz"
    raw = gzip.decompress(stored) if compressed else stored
    data = json.loads(raw)
    if _identity(data) != (episode_id, revision):
        raise ContentError("文件名与 JSON 的 id/revision 不匹配")
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("status") not in ("draft", "published")
            or not isinstance(data.get("segments"), list)):
        raise ContentError("未知的单集历史内容或 schema_version")
    review = data.get("review", {})
    key = data.get("status") == "published" or (
        isinstance(review, dict) and review.get("speakers_confirmed") is True
        and review.get("content_checked") is True
    )
    return _Snapshot(path, revision, compressed, key, len(stored), hashlib.sha256(raw).digest(),
                     (info.st_dev, info.st_ino))


def _unchanged_bytes(snapshot: _Snapshot) -> bytes:
    stored, info = _regular_bytes(snapshot.path)
    raw = gzip.decompress(stored) if snapshot.compressed else stored
    if ((info.st_dev, info.st_ino) != snapshot.inode
            or hashlib.sha256(raw).digest() != snapshot.digest):
        raise ContentError("历史文件已变更，保留原件")
    return raw


def compact_history(episode_path: Path, *, keep_recent: int = 10, dry_run: bool = False) -> dict:
    """Keep first, latest N and completed/published revisions as JSON.

    All other recognized JSON snapshots become lossless gzip files. Existing gzip
    files are reused only after byte-for-byte verification. Dry runs return the
    proposed counts and byte sizes without creating directories or modifying files.
    Byte totals cover recognized snapshots; skipped user files are never changed.
    """
    if type(keep_recent) is not int or keep_recent < 0:
        raise ContentError("keep_recent 必须是非负整数")
    episode_path = Path(episode_path)
    current, _ = _regular_bytes(episode_path)
    episode_id, _ = _identity(json.loads(current))
    report = {
        "episode_id": episode_id, "keep_recent": keep_recent, "dry_run": dry_run,
        "counts": {"scanned": 0, "compressed": 0, "restored": 0, "kept_json": 0,
                   "already_compressed": 0, "skipped": 0},
        "bytes": {"before": 0, "after": 0, "saved": 0}, "skipped": [],
    }
    counts, sizes = report["counts"], report["bytes"]

    def skip(path: Path, reason: str) -> None:
        report["skipped"].append({"path": path.name, "reason": reason})
        counts["skipped"] += 1

    try:
        directory = _history_directory(episode_path)
    except _READ_ERRORS as exc:
        skip(episode_path.parent / "history", str(exc))
        return report
    if not directory.exists():
        return report
    pattern = re.compile(rf"{re.escape(episode_id)}-r([1-9][0-9]*)\.json(\.gz)?")
    revisions: dict[int, dict[bool, _Snapshot]] = {}
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        skip(directory, str(exc))
        return report
    for path in entries:
        counts["scanned"] += 1
        match = pattern.fullmatch(path.name)
        if not match:
            skip(path, "非当前单集的规范历史文件名")
            continue
        try:
            snapshot = _snapshot(path, episode_id, int(match[1]))
        except _READ_ERRORS as exc:
            skip(path, str(exc))
            continue
        revisions.setdefault(snapshot.revision, {})[snapshot.compressed] = snapshot
        sizes["before"] += snapshot.size
    sizes["after"] = sizes["before"]
    managed_paths = {snapshot.path for copies in revisions.values() for snapshot in copies.values()}
    ordered = sorted(revisions)
    keep = set(ordered[-keep_recent:]) if keep_recent else set()
    if ordered:
        keep.add(ordered[0])
    keep.update(revision for revision, copies in revisions.items() if any(s.key for s in copies.values()))
    for revision, copies in sorted(revisions.items()):
        plain, archived = copies.get(False), copies.get(True)
        if plain and archived and plain.digest != archived.digest:
            skip(plain.path, "已有 gzip 与 JSON 内容不同，保留两者")
            continue
        if revision in keep:
            if plain:
                counts["kept_json"] += 1
                continue
            assert archived is not None
            target = archived.path.with_suffix("")
            try:
                raw = _unchanged_bytes(archived)
                if target.exists() or target.is_symlink():
                    raise ContentError("JSON 目标已存在且未通过校验，保留原件")
                if not dry_run:
                    managed_paths.add(target)
                    _atomic_create(target, raw)
                    restored, _ = _regular_bytes(target)
                    if restored != raw:
                        raise ContentError("恢复后的 JSON 校验失败，保留 gzip 原件")
                counts["restored"] += 1
                sizes["after"] += len(raw)
            except _READ_ERRORS as exc:
                skip(archived.path, str(exc))
            continue
        if plain is None:
            counts["already_compressed"] += 1
            continue
        target = plain.path.with_suffix(".json.gz")
        try:
            raw = _unchanged_bytes(plain)
            if archived:
                if _unchanged_bytes(archived) != raw:
                    raise ContentError("已有 gzip 与 JSON 内容不同，保留两者")
                packed_size = 0  # The existing archive is already in byte totals.
            else:
                if target.exists() or target.is_symlink():
                    raise ContentError("gzip 目标已存在且未通过校验，保留原件")
                packed = gzip.compress(raw, compresslevel=6, mtime=0)
                if gzip.decompress(packed) != raw:
                    raise ContentError("gzip 校验失败，保留 JSON 原件")
                packed_size = len(packed)
                if not dry_run:
                    managed_paths.add(target)
                    _atomic_create(target, packed)
            if not dry_run:
                stored, _ = _regular_bytes(target)
                if gzip.decompress(stored) != raw or _unchanged_bytes(plain) != raw:
                    raise ContentError("压缩后校验失败，保留 JSON 原件")
                plain.path.unlink()
            counts["compressed"] += 1
            sizes["after"] += packed_size - plain.size
        except _READ_ERRORS as exc:
            skip(plain.path, str(exc))
    if not dry_run:
        # Include files retained after a failed post-write verification as well.
        sizes["after"] = 0
        for path in managed_paths:
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    sizes["after"] += info.st_size
            except FileNotFoundError:
                pass
            except OSError as exc:
                skip(path, str(exc))
    sizes["saved"] = sizes["before"] - sizes["after"]
    return report
