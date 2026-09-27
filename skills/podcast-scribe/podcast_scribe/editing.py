"""Compact, resumable views for host-agent editing; no text-model API calls."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

from .model import ContentError


def compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def episode_digest(episode: dict) -> str:
    # Export paths and bookkeeping timestamps do not change editorial input.
    content = {key: value for key, value in episode.items()
               if key not in {"artifacts", "created_at", "updated_at"}}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def editing_status(episode: dict) -> dict:
    segments = episode["segments"]
    pending = [s for s in segments if s.get("review_status") != "reviewed"]
    review = episode.get("review", {})
    completed = all(review.get(key) is True for key in ("speakers_confirmed", "content_checked"))
    completion_basis = review.get("basis") if completed else None
    completion_status = (completion_basis or "legacy_checked") if completed else "incomplete"
    return {
        "episode_id": episode["id"], "revision": episode.get("revision", 1),
        "segments": len(segments), "reviewed": len(segments) - len(pending),
        "remaining": len(pending),
        "states": dict(sorted(Counter(s.get("review_status", "unreviewed") for s in segments).items())),
        "unknown_speakers": sum(s.get("speaker_id") is None for s in segments),
        "remaining_text_chars": sum(len(s["text"]) for s in pending),
        "next_segment_id": pending[0]["id"] if pending else None,
        "review": review,
        "review_mode": review.get("mode", "auto"),
        "completion_basis": completion_basis,
        "completion_status": completion_status,
    }


def read_batch(episode: dict, *, max_chars: int = 6000, after: str | None = None,
               include_reviewed: bool = False, context_chars: int = 300,
               raw: bool = False) -> dict:
    """Fit complete target segments and optional neighbor excerpts in one budget.

    The budget includes the entire compact JSON and its trailing newline. A
    target is never truncated. Context may be shortened and is never a target.
    """
    if type(max_chars) is not int or max_chars < 256:
        raise ContentError("--max-chars 必须是至少 256 的整数（包括完整 JSON 元信息）")
    if type(context_chars) is not int or context_chars < 0:
        raise ContentError("--context-chars 必须是非负整数")
    segments = episode["segments"]
    indexes = {s["id"]: i for i, s in enumerate(segments)}
    if after is not None and after not in indexes:
        raise ContentError(f"找不到续读段落：{after}")
    offset = indexes[after] + 1 if after is not None else 0
    eligible = [i for i in range(offset, len(segments))
                if include_reviewed or segments[i].get("review_status") != "reviewed"]
    field = "raw_text" if raw else "text"
    digest = episode_digest(episode)

    def payload(target_indexes, excerpt_chars):
        targets = [{key: segments[i].get(key, "unreviewed" if key == "review_status" else None)
                    for key in ("id", "start", "end", "speaker_id", field, "review_status")}
                   for i in target_indexes]
        context = {"before": [], "after": []}
        omitted = []
        if target_indexes and context_chars and not excerpt_chars:
            omitted = [label for label, i in (("before", target_indexes[0] - 1), ("after", target_indexes[-1] + 1))
                       if 0 <= i < len(segments)]
        if target_indexes and excerpt_chars:
            for label, i in (("before", target_indexes[0] - 1), ("after", target_indexes[-1] + 1)):
                if 0 <= i < len(segments):
                    segment = segments[i]
                    text = segment[field]
                    excerpt = text[-excerpt_chars:] if label == "before" else text[:excerpt_chars]
                    context[label].append({"id": segment["id"], "speaker_id": segment.get("speaker_id"),
                                           field: excerpt, "truncated": len(excerpt) < len(text)})
        speaker_ids = {s["speaker_id"] for s in targets + context["before"] + context["after"]}
        remaining = sum(i > target_indexes[-1] for i in eligible) if target_indexes else 0
        return {
            "batch_version": 1, "episode_id": episode["id"],
            "base_revision": episode.get("revision", 1), "base_sha256": digest,
            "view": field, "targets": targets, "context": context, "context_omitted": omitted,
            "speakers": [{"id": p["id"], "name": p["name"]}
                         for p in episode["speakers"] if p["id"] in speaker_ids],
            "cursor_after": after,
            "next_after": segments[target_indexes[-1]]["id"] if remaining else None,
            "remaining_after_batch": remaining, "done": not targets,
        }

    if not eligible:
        result = payload([], 0)
        if len(compact_json(result)) > max_chars:
            raise ContentError("--max-chars 太小，无法容纳批次元信息")
        return result
    selected = [eligible[0]]
    result = payload(selected, context_chars)
    if len(compact_json(result)) > max_chars:
        # A long first target gets priority over optional surrounding context.
        minimal = payload(selected, 0)
        minimum = len(compact_json(minimal))
        if minimum > max_chars:
            raise ContentError(f"段落 {segments[selected[0]]['id']} 无法完整放入批次；"
                               f"请将 --max-chars 增至至少 {minimum}（不截断正文）")
        low, high = 0, context_chars
        while low < high:
            middle = (low + high + 1) // 2
            if len(compact_json(payload(selected, middle))) <= max_chars:
                low = middle
            else:
                high = middle - 1
        return payload(selected, low)
    for index in eligible[1:]:
        # Do not silently omit an already-reviewed passage inside a reading run.
        # The next batch resumes after it, with its tail available as context.
        if index != selected[-1] + 1:
            break
        candidate = payload(selected + [index], context_chars)
        if len(compact_json(candidate)) > max_chars:
            break
        selected.append(index)
        result = candidate
    return result


def validate_batch_edits(episode: dict, edits: dict, batch: dict) -> None:
    """Reject stale work and patches to read-only context before any write."""
    if not isinstance(batch, dict) or batch.get("batch_version") != 1:
        raise ContentError("--batch 需要 batch 命令生成的批次 JSON")
    if (batch.get("episode_id") != episode["id"]
            or batch.get("base_revision") != episode.get("revision", 1)
            or batch.get("base_sha256") != episode_digest(episode)):
        raise ContentError("批次已过期或不属于此文稿；请重新运行 batch，基于当前内容生成补丁")
    targets = batch.get("targets")
    if (not isinstance(targets, list)
            or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in targets)):
        raise ContentError("批次 targets 无效")
    target_ids = {s["id"] for s in targets}
    if not isinstance(edits, dict) or not isinstance(edits.get("segments", []), list):
        raise ContentError("编辑需要对象，segments 必须是列表")
    for patch in edits.get("segments", []):
        if not isinstance(patch, dict) or patch.get("id") not in target_ids:
            raise ContentError("补丁只能修改本批 targets 中的段落；context 是只读上下文")
