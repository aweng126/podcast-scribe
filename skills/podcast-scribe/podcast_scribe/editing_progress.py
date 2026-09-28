"""Local evidence of explicitly handled batch targets, separate from review claims.

Call record_progress after saving every edit, including edits without a batch:
ordinary edits invalidate old entries but never register newly handled targets.
The caller holds the episode destination lock across both writes. A crash before
the sidecar write can require rereading a batch; it must not hide unhandled text.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .editing import compact_json, validate_batch_edits
from .model import ContentError, REVIEW_STATUSES, write_json

NOTE_MAX_CHARS = 1200
_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")


def progress_path(episode_path: Path) -> Path:
    path = Path(episode_path)
    return path.with_name(path.name + ".editing-progress.json")


def validate_note(note: str | None) -> str | None:
    """Validate before saving the manuscript, so a bad note cannot partly edit it."""
    if note is None:
        return None
    if not isinstance(note, str) or not note.strip():
        raise ContentError("批次笔记必须是非空文字")
    if len(note) > NOTE_MAX_CHARS:
        raise ContentError(f"每批笔记最多 {NOTE_MAX_CHARS} 字符，请只保留事实、疑点和段落 ID")
    return note.strip()


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mode(episode: dict) -> str:
    return episode.get("review", {}).get("mode", "auto")


def _identity(episode: dict) -> dict:
    return {"episode_id": episode["id"],
            "source_sha256": _digest({"source": episode.get("source", {}),
                                      "input_identity": episode.get("input_identity")})}


def _empty(episode_path: Path, episode: dict) -> dict:
    return {"schema_version": _SCHEMA_VERSION, **_identity(episode),
            "episode_path": str(Path(episode_path).resolve()),
            "mode": _mode(episode), "entries": {}, "notes": []}


def _fingerprints(episode: dict, *, content_only: bool = False, status=None,
                  override_status: bool = False) -> dict[str, str]:
    people = {person["id"]: person for person in episode["speakers"]}
    keys = ("id", "text", "raw_text", "start", "end", "speaker_id")
    if not content_only:
        keys += ("review_status",)
    return {segment["id"]: _digest({
        "mode": _mode(episode),
        "segment": {key: status if override_status and key == "review_status" else segment.get(key)
                    for key in keys},
        "speaker": people.get(segment.get("speaker_id")),
    }) for segment in episode["segments"]}


def _check_identity(progress: dict, episode: dict) -> None:
    if any(progress.get(key) != value for key, value in _identity(episode).items()):
        raise ContentError("整理进度不属于当前文稿或来源；已保留原进度与笔记，请检查侧文件")


def _check_structure(progress: dict) -> None:
    try:
        if (not isinstance(progress, dict) or progress.get("schema_version") != _SCHEMA_VERSION
                or progress.get("mode") not in {"auto", "precise"}
                or not isinstance(progress.get("episode_path"), str)
                or not isinstance(progress.get("entries"), dict)
                or not isinstance(progress.get("notes"), list)):
            raise ValueError("schema")
        for ident, fingerprint in progress["entries"].items():
            if not isinstance(ident, str) or not isinstance(fingerprint, str) or not _DIGEST.fullmatch(fingerprint):
                raise ValueError("entry")
        for number, note in enumerate(progress["notes"], 1):
            if (not isinstance(note, dict) or note.get("id") != number
                    or type(note.get("revision")) is not int or note["revision"] < 1
                    or note.get("mode") not in {"auto", "precise"}
                    or not isinstance(note.get("segment_ids"), list)
                    or not all(isinstance(ident, str) for ident in note["segment_ids"])
                    or len(set(note["segment_ids"])) != len(note["segment_ids"])
                    or not isinstance(note.get("fingerprints"), dict)
                    or set(note["fingerprints"]) != set(note["segment_ids"])):
                raise ValueError("note")
            validate_note(note.get("text"))
            if note.get("text") is None or any(
                not isinstance(value, str) or not _DIGEST.fullmatch(value)
                for value in note["fingerprints"].values()
            ):
                raise ValueError("note fingerprint")
            if "content_fingerprints" in note:
                values = note["content_fingerprints"]
                if (not isinstance(values, dict) or set(values) != set(note["segment_ids"])
                        or any(not isinstance(value, str) or not _DIGEST.fullmatch(value)
                               for value in values.values())):
                    raise ValueError("note content fingerprint")
    except (KeyError, TypeError, ValueError, ContentError) as exc:
        raise ContentError("整理进度文件损坏；已保留文件与笔记，请检查后重试") from exc


def load_progress(episode_path: Path, episode: dict) -> dict:
    """Missing sidecars are empty; corrupt or foreign sidecars are never replaced."""
    path = progress_path(episode_path)
    if not path.exists():
        return _empty(episode_path, episode)
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContentError(f"无法读取整理进度：{path}；已保留文件与笔记") from exc
    _check_structure(progress)
    _check_identity(progress, episode)
    if progress["episode_path"] != str(Path(episode_path).resolve()):
        raise ContentError("整理进度绑定了其他文稿路径；已保留原进度与笔记，请勿混用侧文件")
    return progress


def _valid_entries(episode: dict, progress: dict) -> dict[str, str]:
    _check_identity(progress, episode)
    if progress["mode"] != _mode(episode):
        return {}
    fingerprints = _fingerprints(episode)
    review = episode.get("review", {})
    # v1 sidecars written before completion used the pre-completion `edited`
    # state. Accept only this status-only transition on a completed manuscript;
    # all content/person/time/source fingerprints still have to match exactly.
    completed = (review.get("content_checked") is True and review.get("speakers_confirmed") is True
                 and review.get("basis") in {"automated", "user_accepted", "source_checked"})
    prior = _fingerprints(episode, status="edited", override_status=True) if completed else {}
    eligible = {segment["id"] for segment in episode["segments"]
                if segment.get("review_status") in {"edited", "reviewed"}}
    return {ident: digest for ident, digest in progress["entries"].items()
            if ident in eligible and digest in {fingerprints.get(ident), prior.get(ident)}}


def valid_skip_ids(episode: dict, progress: dict) -> set[str]:
    """Only automatic editing can skip explicitly registered, unchanged targets."""
    valid = _valid_entries(episode, progress)
    return set(valid) if _mode(episode) == "auto" else set()


def progress_summary(episode: dict, progress: dict) -> dict:
    valid = _valid_entries(episode, progress)
    skipped = set(valid) if _mode(episode) == "auto" else set()
    remaining = [segment for segment in episode["segments"]
                 if segment.get("review_status") != "reviewed" and segment["id"] not in skipped]
    return {"registered": len(valid), "invalidated": len(progress["entries"]) - len(valid),
            "remaining_to_edit": len(remaining),
            "next_segment_id": remaining[0]["id"] if remaining else None,
            "notes": len(progress["notes"]), "review_mode": _mode(episode)}


def completion_progress(episode_path: Path, before: dict, after: dict) -> dict:
    """Prepare completion receipts before saving; never promote stale entries.

    The caller saves the manuscript then this sidecar while holding its lock.
    Read compatibility above also covers interruption between those two writes.
    """
    progress = load_progress(episode_path, before)
    _check_identity(progress, after)
    previous, current = _fingerprints(before, content_only=True), _fingerprints(after, content_only=True)
    if previous != current:
        raise ContentError("完成操作不能改变正文、人物、时间或处理模式")
    result = deepcopy(progress)
    updated = _fingerprints(after)
    result["entries"] = {ident: updated[ident] for ident in _valid_entries(before, progress)}
    return result


def record_progress(episode_path: Path, before: dict, after: dict, edits: dict,
                    batch: dict | None = None, *, note: str | None = None) -> dict:
    """Record only explicit edited/reviewed patches from a validated batch.

    Invoke for ordinary edits too: a mode switch must invalidate entries even
    when the user later switches back, and changed entries must not resurrect.
    Notes survive invalidation and are reported as stale when their text changed.
    """
    note = validate_note(note)
    if note is not None and batch is None:
        raise ContentError("批次笔记需要 --batch，不能给未限定范围的编辑登记笔记")
    if batch is not None:
        validate_batch_edits(before, edits, batch)
    progress = load_progress(episode_path, before)
    _check_identity(progress, after)
    current = _fingerprints(after)
    if _mode(before) != _mode(after):
        retained = {}
    else:
        # A stale receipt must not revive when an ordinary edit happens to
        # restore an older value (including after an external/manual edit).
        previously_valid = _valid_entries(before, progress)
        retained = {ident: digest for ident, digest in _valid_entries(after, progress).items()
                    if ident in previously_valid}
    result = deepcopy(progress)
    result.update(mode=_mode(after), entries=retained)
    if batch is not None:
        statuses = {segment["id"]: segment.get("review_status") for segment in after["segments"]}
        for patch in edits.get("segments", []):
            ident = patch["id"]
            state = patch.get("review_status")
            if state in {"edited", "reviewed"} and statuses.get(ident) == state:
                result["entries"][ident] = current[ident]
    if note is not None:
        # Notes may discuss doubts in an unchanged or needs_review target.
        # Reading evidence is broader than the explicit completion receipts.
        note_ids = list(dict.fromkeys(target["id"] for target in batch["targets"]))
        content = _fingerprints(after, content_only=True)
        result["notes"].append({"id": len(result["notes"]) + 1,
                                "revision": after.get("revision", 1), "mode": _mode(after),
                                "segment_ids": note_ids, "text": note,
                                "fingerprints": {ident: current[ident] for ident in note_ids},
                                "content_fingerprints": {ident: content[ident] for ident in note_ids}})
    # No sidecar is needed merely to perform an ordinary edit on a legacy file.
    if result != progress or progress_path(episode_path).exists():
        write_json(progress_path(episode_path), result)
    return result


def read_notes(episode: dict, progress: dict, *, after: int = 0, max_chars: int = 6000) -> dict:
    """Page whole short notes within a compact-JSON character budget, never prose."""
    _check_identity(progress, episode)
    if type(after) is not int or not 0 <= after <= len(progress["notes"]):
        raise ContentError("笔记续读位置无效，请使用上一页的 next_after")
    if type(max_chars) is not int or max_chars < 256:
        raise ContentError("--max-chars 必须是至少 256 的整数（包括完整 JSON 元信息）")
    fingerprints = _fingerprints(episode, content_only=True)
    # Legacy notes did not store a content-only digest. Reconstruct the finite
    # status variants, so status-only changes can be distinguished from actual
    # text/person/time changes without rewriting or trusting stale notes.
    legacy = None
    selected = []

    def payload():
        cursor = selected[-1]["id"] if selected else after
        remaining = len(progress["notes"]) - cursor
        return {"episode_id": episode["id"], "notes": selected,
                "cursor_after": after, "next_after": cursor if remaining else None,
                "remaining": remaining, "done": not selected}

    for note in progress["notes"][after:]:
        visible = {key: note[key] for key in ("id", "revision", "mode", "segment_ids", "text")}
        if "content_fingerprints" in note:
            changed = any(fingerprints.get(ident) != digest
                          for ident, digest in note["content_fingerprints"].items())
        else:
            if legacy is None:
                legacy = [_fingerprints(episode, status=state, override_status=True)
                          for state in [None, *sorted(REVIEW_STATUSES)]]
            changed = any(not any(candidate.get(ident) == digest for candidate in legacy)
                          for ident, digest in note["fingerprints"].items())
        visible["stale"] = note["mode"] != _mode(episode) or changed
        selected.append(visible)
        if len(compact_json(payload())) > max_chars:
            if len(selected) == 1:
                minimum = len(compact_json(payload()))
                raise ContentError(f"笔记无法完整放入预算，请将 --max-chars 增至至少 {minimum}")
            selected.pop()
            break
    result = payload()
    if len(compact_json(result)) > max_chars:
        raise ContentError("--max-chars 太小，无法容纳笔记元信息")
    return result
