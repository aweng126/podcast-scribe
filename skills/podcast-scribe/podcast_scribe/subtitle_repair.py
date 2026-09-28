"""Transcribe only bounded subtitle gaps, retaining global times and cache identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .audio_chunks import analyze_audio, extract_audio
from .cache_lifecycle import register_media, touch_media_tree, was_removed
from .defaults import destination_lock
from .model import ContentError, write_json
from .transcribe import transcribe_audio
from .transcripts import normalize_segments


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repair_subtitles(document: dict, assessment: dict, audio: Path, work: Path,
                     asr_cache: Path, *, duration_seconds: float,
                     language: str = "zh") -> tuple[list[dict], list[dict], list[dict]]:
    from .subtitle_ingest import normalize_subtitle_document

    if not assessment.get("repairable") or not assessment.get("repair_ranges"):
        raise ContentError("字幕没有可安全局部补齐的范围")
    document = normalize_subtitle_document(document)
    ranges = assessment["repair_ranges"]
    previous_end = 0.0
    for item in ranges:
        if not (previous_end <= item["start"] < item["end"] <= duration_seconds):
            raise ContentError("字幕修补范围倒置、重叠或超出节目")
        for cue in document["cues"]:
            if cue["start"] < item["end"] and cue["end"] > item["start"]:
                if cue["start"] < item["start"] or cue["end"] > item["end"]:
                    raise ContentError("字幕修补范围截断原字幕，已停止以免丢失文字")
        previous_end = item["end"]
    # Decode the complete source first: a truncated download must never be
    # accepted as a valid gap and billed before the damage is discovered.
    analysis = analyze_audio(audio)
    if abs(analysis["duration"] - duration_seconds) > 2:
        raise ContentError("音频实际时长与字幕来源不一致，已停止局部转写，请检查来源文件")
    source_hash = _hash(audio)
    directory = Path(work) / "subtitle-repairs" / source_hash
    directory.mkdir(parents=True, exist_ok=True)
    touch_media_tree(directory)
    rows, repairs = [], []
    for cue in document["cues"]:
        if not any(cue["start"] < item["end"] and cue["end"] > item["start"]
                   for item in ranges):
            rows.append({"start": cue["start"], "end": cue["end"], "text": cue["text"],
                         "speaker": None})
    for index, item in enumerate(ranges, 1):
        start, end = item["start"], item["end"]
        identity = {"source_sha256": source_hash, "start": start, "end": end,
                    "encoding": "mono16k32kbps:v1"}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        clip, manifest_path = directory / f"{key}.mp3", directory / f"{key}.json"
        # Different manuscript destinations may share this clip. The ASR cache
        # lock starts later and cannot protect extraction's temporary files.
        with destination_lock(clip):
            if manifest_path.exists():
                try:
                    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ContentError("字幕补齐音频缓存损坏，请保留文件检查") from exc
                if not isinstance(saved, dict) or saved.get("identity") != identity:
                    raise ContentError("字幕补齐音频缓存校验失败，请保留文件检查")
                if not clip.exists() and was_removed(clip, saved.get("sha256")):
                    extract_audio(audio, clip, start, end)
                    if not clip.is_file() or _hash(clip) != saved.get("sha256"):
                        raise ContentError("字幕补齐音频缓存校验失败，请保留文件检查")
                    register_media(clip)
                elif not clip.is_file() or _hash(clip) != saved.get("sha256"):
                    raise ContentError("字幕补齐音频缓存校验失败，请保留文件检查")
            else:
                extract_audio(audio, clip, start, end)
                register_media(clip)
                write_json(manifest_path, {"identity": identity, "sha256": _hash(clip)})
        info = {}
        segments, _ = transcribe_audio(clip, asr_cache, language=language, metadata=info,
                                      allow_empty=True)
        if abs(info.get("duration_seconds", 0) - (end - start)) > 0.25:
            raise ContentError("字幕补齐响应时长与目标范围不一致，已保留缓存")
        replaced = [c for c in document["cues"] if c["start"] < end and c["end"] > start]
        if replaced and not segments:
            raise ContentError("疑点字幕的局部转写没有有效文字，未删除原字幕；请检查此范围")
        for segment in segments:
            if not 0 <= segment["start"] <= segment["end"] <= end - start + 0.25:
                raise ContentError("字幕补齐片段超出目标时间范围")
            # Anonymous labels from independent clips are not the same person.
            speaker = segment.get("speaker_id")
            rows.append({"start": start + min(segment["start"], end - start),
                         "end": start + min(segment["end"], end - start),
                         "text": segment["raw_text"],
                         "speaker": f"subtitle-repair-{index}:{speaker}" if speaker else None})
        repairs.append({"start": start, "end": end, "segments": len(segments),
                        "replaced_cue_ids": [c["id"] for c in replaced],
                        "transcription_cache": info.get("transcription_cache")})
    rows.sort(key=lambda row: (row["start"], row["end"]))
    segments, speakers = normalize_segments(rows)
    return segments, speakers, repairs
