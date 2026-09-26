"""Validated, versioned episode records. Export formats never own the content."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse


class ContentError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")[:100]
    return slug or "item-" + hashlib.sha256(str(value).encode()).hexdigest()[:12]


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ContentError(f"{label} 必须是 1–100 位字母、数字、下划线或短横线")


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ContentError(f"{label} 必须是非负有限数值")


def validate_episode(ep: dict, *, for_publication: bool = False) -> dict:
    if not isinstance(ep, dict) or ep.get("schema_version") != 1:
        raise ContentError("需要 schema_version=1 的单集 JSON")
    for field in ("source", "series", "review", "artifacts"):
        if field in ep and not isinstance(ep[field], dict):
            raise ContentError(f"{field} 必须是对象")
    _identifier(ep.get("id"), "episode.id")
    if not isinstance(ep.get("title"), str) or not ep["title"].strip():
        raise ContentError("标题不能为空")
    if ep.get("status") not in ("draft", "published"):
        raise ContentError("status 必须是 draft 或 published")
    _identifier(ep.get("series", {}).get("id"), "series.id")
    if not ep.get("series", {}).get("title"):
        raise ContentError("需要系列名称")
    url = ep.get("source", {}).get("url", "")
    if url and (urlparse(url).scheme not in ("https", "http") or not urlparse(url).netloc):
        raise ContentError("来源链接仅支持 http/https")
    _number(ep.get("duration_seconds", 0), "duration_seconds")
    people = ep.get("speakers", [])
    if not isinstance(people, list):
        raise ContentError("speakers 必须是列表")
    speaker_ids = set()
    for p in people:
        if not isinstance(p, dict):
            raise ContentError("每个 speaker 必须是对象")
        _identifier(p.get("id"), "speaker.id")
        if p["id"] in speaker_ids:
            raise ContentError("说话人 ID 重复")
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise ContentError("说话人名称不能为空")
        speaker_ids.add(p["id"])
    segments = ep.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise ContentError("没有可用的转写段落")
    segment_ids, previous_start = set(), -1
    for s in segments:
        if not isinstance(s, dict):
            raise ContentError("每个 segment 必须是对象")
        _identifier(s.get("id"), "segment.id")
        if s["id"] in segment_ids:
            raise ContentError("段落 ID 重复")
        segment_ids.add(s["id"])
        _number(s.get("start"), "segment.start")
        _number(s.get("end"), "segment.end")
        if s["end"] < s["start"] or s["start"] < previous_start:
            raise ContentError("段落时间倒置或未按开始时间排序")
        if s["end"] > ep.get("duration_seconds", 0) + 1:
            raise ContentError("段落结束时间超出节目时长")
        previous_start = s["start"]
        if s.get("speaker_id") is not None and s["speaker_id"] not in speaker_ids:
            raise ContentError("段落引用了不存在的说话人")
        for key in ("raw_text", "text"):
            if not isinstance(s.get(key), str) or not s[key].strip():
                raise ContentError(f"段落 {key} 不能为空；完整对话不得删除段落")
    by_id = {s["id"]: s for s in segments}
    chapters = ep.get("chapters", [])
    if not isinstance(chapters, list):
        raise ContentError("chapters 必须是列表")
    chapter_ids, previous_start = set(), -1
    for c in chapters:
        if not isinstance(c, dict):
            raise ContentError("每个 chapter 必须是对象")
        _identifier(c.get("id"), "chapter.id")
        if c["id"] in chapter_ids or not isinstance(c.get("title"), str) or not c["title"].strip():
            raise ContentError("章节 ID 重复或标题为空")
        chapter_ids.add(c["id"])
        if c.get("segment_id") not in by_id:
            raise ContentError("章节必须引用真实段落")
        _number(c.get("start"), "chapter.start")
        if c["start"] < previous_start or abs(c["start"] - by_id[c["segment_id"]]["start"]) > 0.01:
            raise ContentError("章节必须有序，且时间与所引用段落一致")
        previous_start = c["start"]
    if not isinstance(ep.get("summary", []), list) or any(not isinstance(s, str) or not s.strip() for s in ep.get("summary", [])):
        raise ContentError("summary 必须是非空文本项列表")
    if not isinstance(ep.get("references", []), list):
        raise ContentError("references 必须是列表")
    for reference in ep.get("references", []):
        if not isinstance(reference, dict) or not isinstance(reference.get("title"), str) or not reference["title"].strip():
            raise ContentError("每条核验来源需要标题")
        link = urlparse(reference.get("url", ""))
        if link.scheme not in ("https", "http") or not link.netloc:
            raise ContentError("核验来源仅支持 http/https 链接")
    if for_publication or ep["status"] == "published":
        review = ep.get("review", {})
        if not all(review.get(k) is True for k in ("speakers_confirmed", "content_checked")):
            raise ContentError("发布前需要完成人物与内容校对")
        if not chapters or not ep.get("summary"):
            raise ContentError("发布前需要摘要和章节")
        if any(s.get("speaker_id") is None for s in segments):
            raise ContentError("仍有说话人待确认的段落")
        if ep.get("is_demo"):
            raise ContentError("演示内容仅允许本地预览，不能作为真实节目发布")
    return ep


def new_episode(metadata: dict, segments: list[dict], speakers: list[dict], *, series_id: str, series_title: str) -> dict:
    ep = {
        "schema_version": 1, "id": safe_id(metadata["id"]), "title": metadata["title"],
        "description": metadata.get("description", ""), "source": metadata.get("source", {}),
        "series": {"id": safe_id(series_id), "title": series_title, "description": ""},
        "duration_seconds": max(metadata.get("duration_seconds") or 0, max((s["end"] for s in segments), default=0)),
        "published_at": metadata.get("published_at", ""), "speakers": speakers, "segments": segments,
        "chapters": [], "summary": [], "status": "draft", "revision": 1,
        "review": {"speakers_confirmed": False, "content_checked": False},
        "created_at": utc_now(), "updated_at": utc_now(), "artifacts": {},
    }
    return validate_episode(ep)


def load_episode(path: Path) -> dict:
    return validate_episode(json.loads(Path(path).read_text(encoding="utf-8")))


def write_json(path: Path, data: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def save_episode(path: Path, ep: dict):
    validate_episode(ep)
    write_json(path, ep)


def apply_edits(ep: dict, edits: dict) -> dict:
    """Patch by stable IDs; never replace raw text, timestamps, or segment order."""
    allowed = {"title", "description", "series", "speakers", "segments", "summary", "chapters", "review", "references"}
    if not isinstance(edits, dict) or set(edits) - allowed:
        raise ContentError("编辑仅接受 title/description/series/speakers/segments/summary/chapters/review/references")
    result = deepcopy(ep)
    result["status"] = "draft"
    result["artifacts"] = {}
    result["review"] = {"speakers_confirmed": False, "content_checked": False}
    for key in ("title", "description", "series", "summary", "chapters", "references"):
        if key in edits:
            result[key] = deepcopy(edits[key])
    for group, allowed_fields in (("speakers", {"id", "name", "role"}), ("segments", {"id", "text", "speaker_id", "review_status"})):
        lookup = {row["id"]: row for row in result[group]}
        seen = set()
        if not isinstance(edits.get(group, []), list):
            raise ContentError(f"{group} 编辑必须是列表")
        for patch in edits.get(group, []):
            if not isinstance(patch, dict):
                raise ContentError(f"每个 {group} 编辑必须是对象")
            ident = patch.get("id")
            if ident in seen or set(patch) - allowed_fields:
                raise ContentError(f"无效的 {group} 编辑：{ident}")
            if ident not in lookup:
                if group != "speakers":
                    raise ContentError(f"不存在的段落：{ident}")
                _identifier(ident, "speaker.id")
                lookup[ident] = {"id": ident, "name": "", "role": ""}
                result[group].append(lookup[ident])
            seen.add(ident)
            lookup[ident].update(patch)
    if "review" in edits:
        if not isinstance(edits["review"], dict) or set(edits["review"]) - set(result["review"]) or any(type(v) is not bool for v in edits["review"].values()):
            raise ContentError("review 仅接受人物/内容校对布尔值")
        result["review"].update(edits["review"])
    result["revision"] = ep.get("revision", 1) + 1
    result["updated_at"] = utc_now()
    return validate_episode(result)
