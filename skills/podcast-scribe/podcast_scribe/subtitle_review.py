"""Read-only subtitle evidence beside an existing transcript, never in its place."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .model import ContentError, load_episode


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _store(directory: Path, prefix: str, document: dict) -> Path:
    payload = _canonical(document)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    path = directory / f"{prefix}-{digest[:20]}.json"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != payload:
            raise ContentError("已有字幕核验文件与内容摘要不符；请保留文件后检查。") from None
    return path


def _report_markdown(report: dict) -> str:
    from .exporters import _md, _timestamp

    lines = ["# 字幕对照报告", "", "字幕对照只提供文字核验线索，不代表已逐句听音或确认说话人；不会修改稿件校对状态。", "",
             f"文稿：{_md(report.get('episode_id'))}", "",
             f"文稿摘要：`{report['episode_digest']}`", "",
             "正文修改后请重新生成对照报告。", ""]
    labels = {"segments_total": "文稿总段数", "matched_segments": "文字对照一致", "different_segments": "需要检查的段落",
              "no_subtitle_segments": "没有对应字幕的段落", "covered_segments": "有字幕时间重叠的段落",
              "coverage_ratio": "字幕时间覆盖段落比例", "subtitle_cues_total": "原字幕条数",
              "deduplicated_cues": "去重后字幕条数", "unaligned_cues": "未对齐字幕条数", "scope": "核验范围"}
    for key, value in report.get("summary", {}).items():
        if value == "provided_subtitles":
            value = "仅本次提供的字幕；覆盖比例不等于识别准确率"
        lines.append(f"- {_md(labels.get(key, key))}：{_md(value)}")
    if report.get("source_scope"):
        lines.append("- 字幕采集范围：" + _md(json.dumps(report["source_scope"], ensure_ascii=False)))
    lines.append(f"- 字幕时间偏移：{report.get('offset_seconds', 0)} 秒")
    lines.append("")
    for warning in report.get("warnings", []):
        lines.extend([f"> {_md(warning)}", ""])
    labels = {"text_difference": "文字有差异", "no_subtitle": "没有对应字幕", "unaligned_subtitle": "字幕未对齐到正文"}
    for index, group in enumerate(report.get("groups", []), 1):
        if group.get("status") == "match":
            continue
        title = labels.get(group.get("status"), group.get("status", "待检查"))
        lines.extend([f"## {index}. {_timestamp(group.get('start'))}–{_timestamp(group.get('end'))} · {_md(title)}", "",
                      "文稿段落：" + ", ".join(_md(x) for x in group.get("segment_ids", [])), "",
                      "字幕片段：" + ", ".join(_md(x) for x in group.get("cue_ids", [])), ""])
        for key, label in (("transcript_text", "文稿"), ("subtitle_text", "字幕"), ("reason", "说明")):
            if group.get(key):
                lines.extend([f"**{label}**：{_md(group[key])}", ""])
        if group.get("reasons"):
            reasons = {"text_not_identical": "文字不同", "subtitle_addition": "字幕有额外文字",
                       "no_temporal_overlap": "没有时间重叠", "no_comparable_characters": "没有可比较的有效文字",
                       "alignment_window_exceeded": "超出本地对齐窗口，需检查字幕切分",
                       "low_confidence_ocr": "OCR 识别置信度较低"}
            lines.extend(["说明：" + "；".join(_md(reasons.get(reason, reason)) for reason in group["reasons"]), ""])
        if group.get("detail_truncated"):
            lines.extend(["以上为长文本摘录；请按段落 ID 和字幕 ID 回读完整证据。", ""])
    if all(group.get("status") == "match" for group in report.get("groups", [])):
        lines.extend(["本次对齐范围内未发现文字差异；请另行检查字幕缺失、字幕编辑及说话人归属。", ""])
    return "\n".join(lines)


def check_episode_subtitles(episode_path: Path, *, subtitle_file: Path | None = None,
                            video: Path | None = None, ocr: bool = False,
                            cache: Path = Path("data/cache"), output_dir: Path | None = None,
                            auth=None, refresh: bool = False, offset: float = 0,
                            start: float = 0, end: float | None = None, interval: float = 0.5,
                            region=(0.05, 0.65, 0.9, 0.3), language: str = "chi_sim+eng") -> dict:
    from .subtitles import load_subtitle_file

    if sum((subtitle_file is not None, video is not None, bool(ocr))) > 1:
        raise ContentError("字幕文件、本地视频与来源视频 OCR 只能选择一种输入。")
    if isinstance(offset, bool) or not math.isfinite(offset):
        raise ContentError("字幕偏移必须是有限秒数。")
    episode = load_episode(episode_path)
    destination = output_dir or Path("output") / episode["id"] / "subtitles"
    work = Path(cache) / episode["id"]
    if subtitle_file is not None:
        document = load_subtitle_file(subtitle_file)
    elif video is not None or ocr:
        from .subtitle_ocr import _configuration, extract_subtitles
        _configuration(start, end, interval, region, language)
        if video is None:
            from .sources import fetch_video
            source = episode.get("source") or {}
            if source.get("platform") != "bilibili" or not source.get("url"):
                raise ContentError("本地来源请提供 --video；来源视频 OCR 需要 B站单集链接。")
            # OCR dependency checks must precede a potentially large download.
            from .subtitle_ocr import check_dependencies
            check_dependencies(language=language)
            video = fetch_video(source["url"], work, auth=auth)
        document = extract_subtitles(video, work / "subtitle-ocr", start=start, end=end,
                                     interval=interval, region=tuple(region), language=language, refresh=refresh)
    else:
        from .sources import fetch_subtitles
        source = episode.get("source") or {}
        if source.get("platform") != "bilibili" or not source.get("url"):
            raise ContentError("此文稿没有 B站来源；请提供 --file 字幕或 --video 本地视频。")
        document = fetch_subtitles(source["url"], work, auth=auth, refresh=refresh)
    return write_subtitle_report(episode, document, output_dir=destination, offset=offset)


def write_subtitle_report(episode: dict, document: dict, *, output_dir: Path | None = None,
                          offset: float = 0) -> dict:
    from .subtitles import compare_subtitles

    destination = output_dir or Path("output") / episode["id"] / "subtitles"
    document_path = _store(Path(destination), "source", document)
    result = {"status": document.get("status"), "subtitle_source": str(document_path.resolve()),
              "episode_changed": False, "review_status_changed": False,
              "warnings": document.get("warnings", [])}
    if document.get("status") != "available" or not document.get("cues"):
        result["message"] = document.get("reason") or "没有取得可比较的字幕。"
        result["next_step"] = ("检查视频画面是否有字幕；有画面字幕时使用 --ocr，或提供 --video 本地视频。"
                               if document.get("status") == "no_subtitles" else "检查来源状态或提供本地字幕文件后重试。")
        return result
    report = compare_subtitles(episode, document, offset=offset)
    report_path = _store(Path(destination), "comparison", report)
    markdown_path = report_path.with_suffix(".md")
    markdown = _report_markdown(report)
    try:
        with markdown_path.open("x", encoding="utf-8") as stream:
            stream.write(markdown)
    except FileExistsError:
        if markdown_path.read_text(encoding="utf-8") != markdown:
            raise ContentError("已有对照清单内容不同；请使用新的 --output-dir 保留两个版本。") from None
    result.update(report=str(report_path.resolve()), checklist=str(markdown_path.resolve()),
                  summary=report["summary"], warnings=report.get("warnings", []),
                  message="字幕对照已完成；差异仅作为核验线索，正文及校对状态保持不变。")
    return result


def read_difference_batch(episode: dict, report: dict, *, after: int = 0, max_chars: int = 6000) -> dict:
    from .subtitles import episode_digest

    if (not isinstance(report, dict) or report.get("kind") != "subtitle_comparison" or report.get("episode_id") != episode["id"]
            or report.get("episode_digest") != episode_digest(episode)):
        raise ContentError("字幕报告与当前文稿不一致，请重新运行 check-subtitles；不使用陈旧核验结果。")
    if type(after) is not int or after < 0 or type(max_chars) is not int or max_chars < 1:
        raise ContentError("游标须为非负整数，字符预算须为正整数。")
    groups = report.get("groups")
    if not isinstance(groups, list) or any(not isinstance(row, dict) for row in groups):
        raise ContentError("字幕报告缺少有效的差异组。")
    if after > len(groups):
        raise ContentError("字幕报告游标超出范围。")
    candidates = [(index, group) for index, group in enumerate(groups, 1)
                  if index > after and group.get("status") != "match"]
    result = {"episode_id": episode["id"], "episode_digest": report["episode_digest"],
              "groups": [], "next_after": None, "done": not candidates}
    for index, group in candidates:
        entry = {"index": index, **group}
        trial = {**result, "groups": result["groups"] + [entry],
                 "next_after": index if index != candidates[-1][0] else None, "done": False}
        if len(json.dumps(trial, ensure_ascii=False, separators=(",", ":")) + "\n") > max_chars:
            if not result["groups"]:
                required = len(json.dumps(trial, ensure_ascii=False, separators=(",", ":")) + "\n")
                raise ContentError(f"单个完整差异组需要至少 {required} 字符，请增加 --max-chars。")
            break
        result = trial
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n") > max_chars:
        raise ContentError("字符预算不足以容纳报告元信息。")
    return result
