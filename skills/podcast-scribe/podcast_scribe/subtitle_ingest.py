"""Conservative, local checks for using subtitles as an unreviewed transcript.

These checks describe structure and coverage, not accuracy against the recording.
They never download media, call ASR, assign voices, or mutate the source document.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from statistics import median
import unicodedata

from .model import ContentError
from .subtitles import _validated_rows, subtitle_digest
from .transcripts import normalize_segments


MIN_COVERAGE = 0.70
MIN_CHARACTERS_PER_SECOND = 0.5
MAX_REPAIR_FRACTION = 0.20
MAX_REPAIR_RANGES = 5
MIN_CONFIDENCE = 0.80


def normalize_subtitle_document(document: dict) -> dict:
    """Validate every cue; never silently drop, reorder, or deduplicate evidence."""
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ContentError("字幕文档需要 schema_version=1")
    if not isinstance(document.get("source", {}), dict):
        raise ContentError("字幕 source 必须是对象")
    if document.get("selected_track") is not None and not isinstance(document["selected_track"], dict):
        raise ContentError("字幕 selected_track 必须是对象或 null")
    normalized = deepcopy(document)
    normalized["cues"] = _validated_rows(document.get("cues"))
    # This also rejects non-finite or unserializable ancillary metadata.
    subtitle_digest(normalized)
    return normalized


def _language(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.lower().replace("_", "-").removeprefix("ai-").split("-")[0]
    return {"cmn": "zh", "zho": "zh", "chi": "zh", "eng": "en"}.get(value, value)


def _provenance(document: dict) -> dict:
    return {"input_kind": "subtitles", "subtitle_source": deepcopy(document.get("source", {})),
            "subtitle_track": deepcopy(document.get("selected_track")),
            "subtitle_content_sha256": document.get("content_sha256"),
            "subtitle_document_sha256": subtitle_digest(document),
            "assessment_kind": "structural_only"}


def _repair_ranges(candidates: list[dict], cues: list[dict], duration: float) -> list[dict]:
    """Expand uncertain ranges to whole intersecting cues, then merge to a fixed point."""
    ranges = [{"start": max(0.0, row["start"]), "end": min(duration, row["end"]),
               "reasons": {row["reason"]}} for row in candidates
              if min(duration, row["end"]) > max(0.0, row["start"])]
    changed = True
    while changed:
        before = [(r["start"], r["end"], sorted(r["reasons"])) for r in ranges]
        for row in ranges:
            for cue in cues:
                if cue["start"] < row["end"] and cue["end"] > row["start"]:
                    row["start"] = min(row["start"], cue["start"])
                    row["end"] = min(duration, max(row["end"], cue["end"]))
        merged = []
        for row in sorted(ranges, key=lambda r: r["start"]):
            if merged and row["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], row["end"])
                merged[-1]["reasons"].update(row["reasons"])
            else:
                merged.append(row)
        ranges = merged
        changed = before != [(r["start"], r["end"], sorted(r["reasons"])) for r in ranges]
    return [{"start": row["start"], "end": row["end"],
             "reason": "low_confidence" if "low_confidence" in row["reasons"] else sorted(row["reasons"])[0],
             "reasons": sorted(row["reasons"]),
             "cue_ids": [cue["id"] for cue in cues if cue["start"] < row["end"] and cue["end"] > row["start"]]}
            for row in ranges]


def assess_subtitles(document: dict, duration_seconds: float | None, language: str = "zh") -> dict:
    """Return a zero-ASR decision or bounded repair candidates, with auditable reasons.

    ``usable`` means the whole track passes these structural checks. ``repairable``
    means only the returned ranges need another source; neither means reviewed or
    factually correct. Unknown recording duration cannot establish full coverage.
    """
    assessment = {"usable": False, "repairable": False, "reasons": [], "metrics": {},
                  "repair_ranges": [], "provenance": {}, "assessment_kind": "structural_only"}
    hard_reasons = set()

    def reject(code, message, *, hard=True):
        if not any(reason["code"] == code for reason in assessment["reasons"]):
            assessment["reasons"].append({"code": code, "message": message})
        if hard:
            hard_reasons.add(code)

    try:
        normalized = normalize_subtitle_document(document)
    except ContentError as exc:
        reject("invalid_structure", str(exc))
        return assessment
    assessment["provenance"] = _provenance(normalized)
    cues = normalized["cues"]
    metrics = assessment["metrics"]
    metrics.update(cue_count=len(cues), requested_language=_language(language),
                   track_language=_language((normalized.get("selected_track") or {}).get("language", normalized.get("language"))))
    if normalized.get("status") != "available":
        reject("subtitles_unavailable", "来源未提供可用字幕轨")
        return assessment
    if not cues:
        reject("empty_cues", "字幕轨没有正文")
        return assessment
    duration_valid = (not isinstance(duration_seconds, bool) and isinstance(duration_seconds, (int, float))
                      and math.isfinite(duration_seconds) and duration_seconds > 0)
    duration = float(duration_seconds) if duration_valid else None
    metrics["duration_seconds"] = duration
    if duration is None:
        reject("unknown_duration", "视频时长未知或无效，无法确认字幕完整覆盖")
    if metrics["requested_language"] is None or metrics["track_language"] is None:
        reject("unknown_language", "字幕轨或目标语言未明确标注")
    elif metrics["requested_language"] != metrics["track_language"]:
        reject("language_mismatch", "字幕轨语言与目标语言不符")

    texts = ["".join(c for c in unicodedata.normalize("NFKC", cue["text"]) if c.isalnum()) for cue in cues]
    characters = sum(map(len, texts))
    letters = [c for text in texts for c in text if c.isalpha()]
    han = sum("\u3400" <= c <= "\u9fff" or "\U00020000" <= c <= "\U0003134f" for c in letters)
    latin = sum("LATIN" in unicodedata.name(c, "") for c in letters)
    metrics.update(character_count=characters, han_letter_fraction=han / len(letters) if letters else 0.0,
                   latin_letter_fraction=latin / len(letters) if letters else 0.0)
    if ((metrics["requested_language"] == "zh" and metrics["han_letter_fraction"] < 0.20)
            or (metrics["requested_language"] == "en" and metrics["latin_letter_fraction"] < 0.60)):
        reject("text_language_mismatch", "字幕正文的文字体系与目标语言明显不符")
    if any(not text for text in texts):
        reject("empty_text_content", "字幕包含只有标点或空白的条目")
    if any(cue["end"] <= cue["start"] for cue in cues):
        reject("invalid_timing", "字幕必须具有正时长")
    if duration is not None and any(cue["end"] > duration + 1.0 for cue in cues):
        reject("timing_out_of_bounds", "字幕时间明显超出视频时长")

    seen, duplicate = {}, False
    for cue, text in zip(cues, texts):
        if text in seen and cue["start"] < seen[text]:
            duplicate = True
        seen[text] = max(seen.get(text, 0), cue["end"])
    repeated = Counter(text for text in texts if len(text) >= 12)
    repeated_characters = sum((count - 1) * len(text) for text, count in repeated.items())
    metrics["repeated_character_fraction"] = repeated_characters / max(1, characters)
    if duplicate or metrics["repeated_character_fraction"] > 0.20:
        reject("duplicate_cues", "字幕含重叠重复条目或大量重复长句")

    scale = normalized.get("source", {}).get("confidence_scale", "0-1")
    low_confidence = []
    for cue in cues:
        confidence = cue.get("confidence")
        if confidence is not None:
            maximum = 100 if scale == "0-100" else 1
            if scale not in ("0-1", "0-100") or confidence > maximum:
                reject("invalid_confidence", "字幕置信度范围不明或超出声明范围")
            confidence /= maximum
        if cue.get("low_confidence", False) or (confidence is not None and confidence < MIN_CONFIDENCE):
            low_confidence.append(cue)
    metrics["low_confidence_cue_count"] = len(low_confidence)
    if low_confidence:
        reject("low_confidence", "字幕包含低置信度条目，需要核对对应音频", hard=False)

    covered, current_end, internal_gaps, raw_duration = 0.0, cues[0]["start"], [], 0.0
    covered_intervals = []
    for cue in cues:
        if cue["start"] > current_end:
            internal_gaps.append((current_end, cue["start"]))
        covered += max(0.0, cue["end"] - max(current_end, cue["start"]))
        raw_duration += cue["end"] - cue["start"]
        current_end = max(current_end, cue["end"])
        if covered_intervals and cue["start"] <= covered_intervals[-1][1]:
            covered_intervals[-1][1] = max(covered_intervals[-1][1], cue["end"])
        else:
            covered_intervals.append([cue["start"], cue["end"]])
    metrics.update(first_start=cues[0]["start"], last_end=current_end,
                   covered_seconds=covered, largest_internal_gap_seconds=max((end - start for start, end in internal_gaps), default=0.0))
    if raw_duration > 0 and (raw_duration - covered) / raw_duration > 0.20:
        reject("excessive_overlap", "字幕大量重叠，无法保守确认时间轴")
    if any(cue["end"] - cue["start"] >= 20 and len(text) / (cue["end"] - cue["start"]) < MIN_CHARACTERS_PER_SECOND
           for cue, text in zip(cues, texts)):
        reject("sparse_long_cue", "长字幕条目的文字过少，不能代表该时段已有完整转写")
    if duration is None:
        return assessment

    edge_tolerance = max(8.0, min(30.0, duration * 0.02))
    # Use ordinary adjacent gaps, including zero, so one large hole cannot make
    # its own detection threshold larger. Short subtitle pauses are not repairs.
    ordinary_gaps = [max(0.0, right["start"] - left["end"]) for left, right in zip(cues, cues[1:])]
    internal_tolerance = max(12.0, min(30.0, median(ordinary_gaps) * 8)) if ordinary_gaps else 12.0
    density = characters / duration
    metrics.update(coverage_fraction=min(1.0, covered / duration), characters_per_second=density,
                   leading_gap_seconds=cues[0]["start"], trailing_gap_seconds=max(0.0, duration - current_end),
                   edge_gap_tolerance_seconds=edge_tolerance, internal_gap_tolerance_seconds=internal_tolerance)
    if density < MIN_CHARACTERS_PER_SECOND or characters < min(8, duration):
        reject("sparse_text", "字幕文字总量过少，不能保守代表完整节目")
    if duration >= 120 and len(cues) < math.ceil(duration / 60):
        reject("sparse_cues", "字幕时间点过少，无法确认长节目的局部覆盖")

    candidates = [{"start": cue["start"], "end": cue["end"], "reason": "low_confidence"} for cue in low_confidence]
    if cues[0]["start"] > edge_tolerance:
        reject("missing_start", "字幕明显缺失开头", hard=False)
        candidates.append({"start": 0.0, "end": cues[0]["start"], "reason": "missing_start"})
    if duration - current_end > edge_tolerance:
        reject("missing_end", "字幕明显缺失结尾", hard=False)
        candidates.append({"start": current_end, "end": duration, "reason": "missing_end"})
    for start, end in internal_gaps:
        if end - start >= internal_tolerance:
            reject("internal_gap", "字幕存在长时间内部缺口", hard=False)
            candidates.append({"start": start, "end": end, "reason": "internal_gap"})
    ranges = _repair_ranges(candidates, cues, duration)
    repair_seconds = sum(row["end"] - row["start"] for row in ranges)
    metrics.update(repair_seconds=repair_seconds, repair_fraction=repair_seconds / duration,
                   repair_range_count=len(ranges), max_repair_fraction=MAX_REPAIR_FRACTION,
                   minimum_coverage_fraction=MIN_COVERAGE)
    # Count only previously uncovered time when judging post-repair coverage.
    repaired_existing_seconds = sum(max(0.0, min(end, row["end"]) - max(start, row["start"]))
                                    for row in ranges for start, end in covered_intervals)
    projected_coverage = min(1.0, (covered + repair_seconds - repaired_existing_seconds) / duration)
    if metrics["coverage_fraction"] < MIN_COVERAGE and (not ranges or projected_coverage < MIN_COVERAGE):
        reject("insufficient_coverage", "字幕有效覆盖比例不足")
    if ranges and (repair_seconds / duration > MAX_REPAIR_FRACTION + 1e-9 or len(ranges) > MAX_REPAIR_RANGES):
        reject("repair_limit_exceeded", "字幕缺口或疑点过多，需要完整音频转写")
    assessment["usable"] = not assessment["reasons"]
    assessment["repairable"] = bool(ranges) and not hard_reasons
    if assessment["repairable"]:
        assessment["repair_ranges"] = ranges
    return assessment


def segments_from_subtitles(document: dict) -> tuple[list[dict], list[dict]]:
    """Keep every validated cue as an unreviewed segment with unknown speaker."""
    normalized = normalize_subtitle_document(document)
    if normalized.get("status") != "available":
        raise ContentError("来源未提供可用字幕轨")
    if any(cue["end"] <= cue["start"] for cue in normalized["cues"]):
        raise ContentError("字幕必须具有正时长")
    return normalize_segments([{"start": cue["start"], "end": cue["end"], "text": cue["text"], "speaker": None}
                               for cue in normalized["cues"]])
