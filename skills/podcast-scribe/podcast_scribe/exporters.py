"""Render a shared episode record to readable Markdown and Chinese PDF.

PDF support is optional. Install ``reportlab`` and provide an embeddable Chinese
TrueType font via ``PODCAST_SCRIBE_FONT`` if none is installed system-wide.
"""

from __future__ import annotations

import hashlib
import html
import math
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .reading import reading_turns, segment_text_parts


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _safe_id(value: object) -> str:
    original = _text(value).strip() or "episode"
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", original).strip("-")[:96] or "episode"
    if name != original:
        name += "-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    return name


def _seconds(value: object) -> int:
    try:
        seconds = float(value or 0)
        return max(0, int(seconds)) if math.isfinite(seconds) else 0
    except (TypeError, ValueError):
        return 0


def _timestamp(value: object) -> str:
    seconds = _seconds(value)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def _safe_url(value: object) -> str:
    candidate = _text(value).strip()
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
    except ValueError:
        return ""
    return quote(candidate, safe=":/?#[]@!$&'*+,;=%-._~")


def _video_url(episode: dict, start: object | None = None) -> str:
    source = episode.get("source") or {}
    url = _safe_url(source.get("url"))
    if not url or start is None:
        return url
    parsed = urlsplit(url)
    if parsed.hostname and (
        parsed.hostname == "bilibili.com" or parsed.hostname.endswith(".bilibili.com")
    ):
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "t"]
        query.append(("t", str(_seconds(start))))
        return urlunsplit(parsed._replace(query=urlencode(query)))
    return url


def _md(value: object) -> str:
    value = html.escape(_text(value), quote=False)
    return re.sub(r"([\\`*_\[\]#])", r"\\\1", value)


def _one_line(value: object) -> str:
    return " ".join(_text(value).split())


def _speaker_map(episode: dict) -> dict[str, str]:
    return {
        speaker["id"]: _one_line(speaker.get("name")) or "说话人待确认"
        for speaker in episode.get("speakers", [])
        if speaker.get("id")
    }


def _speaker_label(segment: dict, speakers: dict[str, str]) -> str:
    return speakers.get(segment.get("speaker_id"), "说话人待确认")


def _segment_text(segment: dict) -> str:
    return _text(segment.get("text") or segment.get("raw_text"))


def _draft_note(episode: dict) -> str:
    review = episode.get("review") or {}
    if review.get("speakers_confirmed") and review.get("content_checked"):
        return "人物与内容已校对；此版本尚未公开发布。"
    return "段落归属与文字仍需校对，请结合来源核对。"


def _provenance_note(episode: dict) -> str:
    if episode.get("is_demo"):
        return "本文为自制功能演示，没有对应的原始音视频；时间戳仅为展示值。"
    return "本文依据提供的转写资料整理。摘要与章节属于辅助阅读内容；请结合来源核对文字与时间戳。"


def _chapter_targets(episode: dict) -> list[tuple[dict, int | None]]:
    segments = episode.get("segments", [])
    by_id = {segment.get("id"): index for index, segment in enumerate(segments)}
    targets = []
    for chapter in episode.get("chapters", []):
        target = by_id.get(chapter.get("segment_id")) if chapter.get("segment_id") else None
        if target is None and segments:
            start = _seconds(chapter.get("start"))
            # An in-segment chapter belongs to the segment already in progress.
            candidates = [i for i, s in enumerate(segments) if _seconds(s.get("start")) <= start]
            target = candidates[-1] if candidates else 0
        targets.append((chapter, target))
    return targets


def _metadata(episode: dict) -> list[tuple[str, str]]:
    source = episode.get("source") or {}
    series = episode.get("series") or {}
    result = []
    for label, value in (
        ("系列", series.get("title")),
        ("作者", source.get("author")),
        ("发布日期", _text(episode.get("published_at"))[:10]),
        ("时长", _timestamp(episode.get("duration_seconds"))),
    ):
        if value:
            result.append((label, _one_line(value)))
    return result


def render_markdown(episode: dict) -> str:
    """Return an escaped, UTF-8-ready complete episode document."""
    title = _one_line(episode.get("title")) or "未命名单集"
    lines = [f"# {_md(title)}", ""]
    if episode.get("is_demo"):
        lines.extend(["> **自制功能演示 · 非真实视频转写**", "> 人物、对话与时间为演示内容，没有对应的原始音视频。", ""])
    if episode.get("status") != "published":
        lines.extend(["> **草稿 · 尚未发布**", "> " + _draft_note(episode), ""])
    lines.extend(f"- **{label}**：{_md(value)}" for label, value in _metadata(episode))
    source_url = _video_url(episode)
    if source_url:
        lines.append(f"- **来源**：[原视频]({source_url})")
    review = episode.get("review") or {}
    lines.extend([
        f"- **说话人归属校对**：{'已确认' if review.get('speakers_confirmed') else '待复核'}",
        f"- **内容校对**：{'已完成' if review.get('content_checked') else '待校对'}",
        "",
    ])
    if episode.get("description"):
        lines.extend([_md(episode["description"]), ""])
    speakers = _speaker_map(episode)
    lines.extend(["## 本期人物", ""])
    if episode.get("speakers"):
        for speaker in episode["speakers"]:
            label = speakers.get(speaker.get("id"), "说话人待确认")
            role = _one_line(speaker.get("role"))
            lines.append(f"- {_md(label)}" + (f"（{_md(role)}）" if role else ""))
    else:
        lines.append("人物待确认。")
    lines.extend(["", "## 本期摘要", ""])
    summary = episode.get("summary") or []
    lines.extend(f"- {_md(item)}" for item in summary)
    if not summary:
        lines.append("暂无摘要。")
    lines.extend(["", "## 章节目录", ""])
    targets = _chapter_targets(episode)
    for chapter, target in targets:
        label = f"{_timestamp(chapter.get('start'))} {_md(_one_line(chapter.get('title')))}"
        lines.append(f"- [{label}](#segment-{target + 1})" if target is not None else f"- {label}")
    if not targets:
        lines.append("暂无章节。")
    lines.extend(["", "## 完整对话", ""])
    for turn in reading_turns(episode):
        index = turn["segments"][0]["index"]
        lines.extend([f'<a id="segment-{index + 1}"></a>', ""])
        for chapter, target in targets:
            if target == index:
                lines.extend([f"### {_md(_one_line(chapter.get('title')))}", ""])
        time = _timestamp(turn["start"])
        url = _video_url(episode, turn["start"])
        timestamp = f"[{time}]({url})" if url else time
        # Keep internal chapter targets inline, without turning each alignment
        # slice into a paragraph or repeating the speaker's name.
        content = "".join(
            (f'<a id="segment-{segment["index"] + 1}"></a>' if offset else "") + _md(part)
            for offset, (segment, part) in enumerate(zip(turn["segments"], segment_text_parts(turn["segments"])))
        )
        pending = " · 待核对" if turn["needs_review"] else ""
        lines.extend([
            f"**{timestamp} · {_md(_speaker_label(turn, speakers))}{pending}**",
            "",
            content if turn["text"] else "[此段暂无文字]",
            "",
        ])
    if not episode.get("segments"):
        lines.extend(["尚无转写内容。", ""])
    if episode.get("references"):
        lines.extend(["## 人物与节目来源核验", ""])
        for ref in episode["references"]:
            url = _safe_url(ref.get("url"))
            title = _md(ref.get("title"))
            label = f"[{title}]({url})" if url else title
            lines.append(f"- {label}" + (f"：{_md(ref['note'])}" if ref.get("note") else ""))
        lines.append("")
    lines.extend(["---", "", _provenance_note(episode), ""])
    return "\n".join(lines)


def _pdf_font() -> str:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise RuntimeError("PDF 导出需要 reportlab，请安装项目的 PDF 依赖。") from exc
    explicit = os.environ.get("PODCAST_SCRIBE_FONT")
    candidates = [explicit] if explicit else [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    errors = []
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        name = "EpisodeCJK-" + hashlib.sha256(str(candidate).encode()).hexdigest()[:12]
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                font = TTFont(name, candidate, subfontIndex=0)
                # Catch an accidental Latin-only font override before emitting tofu.
                if any(ord(character) not in font.face.charToGlyph for character in "中文说话人"):
                    raise ValueError("字体不包含中文字符")
                pdfmetrics.registerFont(font)
                pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
            return name
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    detail = "; ".join(errors)
    raise RuntimeError(
        "未找到可嵌入的中文 TrueType 字体。请将 PODCAST_SCRIBE_FONT 设为中文 .ttf 或 TrueType .ttc 文件路径。"
        + (f" ({detail})" if detail else "")
    )


def _xml(value: object) -> str:
    # XML 1.0 paragraph markup cannot represent most ASCII control characters.
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", _text(value))
    return html.escape(clean, quote=True).replace("\n", "<br/>")


def _pdf_turn_paragraphs(turn: dict) -> list[tuple[str, bool]]:
    """Lay out flowing speech while retaining precise, invisible anchors."""
    parts = segment_text_parts(turn["segments"])
    content = "".join(parts)
    if not content:
        anchors = "".join(f'<a name="segment-{s["index"] + 1}"/>' for s in turn["segments"])
        return [(anchors + "[此段暂无文字]", False)]
    ranges = []
    start = 0
    for match in list(re.finditer(r"\n\s*\n", content)) + [None]:
        end = match.start() if match else len(content)
        if end > start:
            if end - start > 600:
                # Give the kept speaker label a short opening passage so a
                # lengthy turn can begin on the remaining space of the page.
                stop = re.search(r"[。！？.!?]", content[start + 160:start + 320])
                split = start + (161 + stop.start() if stop else 240)
                ranges.append((start, split, True))
                start = split
            ranges.append((start, end, False))
        if match:
            start = match.end()
    anchors = [[] for _ in ranges]
    position = 0
    for segment, part in zip(turn["segments"], parts):
        target = next((i for i, (_, end, _) in enumerate(ranges) if position < end), len(ranges) - 1)
        begin, end, _ = ranges[target]
        anchors[target].append((max(begin, min(position, end)), segment["index"]))
        position += len(part)
    result = []
    for (begin, end, continuation), locations in zip(ranges, anchors):
        markup = []
        cursor = begin
        for position, index in locations:
            markup.extend([_xml(content[cursor:position]), f'<a name="segment-{index + 1}"/>'])
            cursor = position
        markup.append(_xml(content[cursor:end]))
        result.append(("".join(markup), continuation))
    return result


def _render_pdf(episode: dict, path: Path, font: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    ink = colors.HexColor("#233A3D")
    teal = colors.HexColor("#276F72")
    muted = colors.HexColor("#647477")
    draft = episode.get("status") != "published"
    title = _one_line(episode.get("title")) or "未命名单集"
    common = dict(fontName=font, wordWrap="CJK", splitLongWords=True, alignment=TA_LEFT)
    styles = {
        "title": ParagraphStyle("EpisodeTitle", fontSize=22, leading=32, textColor=ink, spaceAfter=15, **common),
        "body": ParagraphStyle("EpisodeBody", fontSize=10.5, leading=18, textColor=ink, spaceAfter=10, **common),
        "continuation": ParagraphStyle("EpisodeContinuation", fontSize=10.5, leading=18, textColor=ink, spaceAfter=0, **common),
        "meta": ParagraphStyle("EpisodeMeta", fontSize=9, leading=15, textColor=muted, spaceAfter=4, **common),
        "reference_title": ParagraphStyle("EpisodeReferenceTitle", fontSize=10.5, leading=18, textColor=teal, spaceAfter=6, keepWithNext=True, **common),
        "h2": ParagraphStyle("EpisodeSection", fontSize=16, leading=24, textColor=teal, spaceBefore=19, spaceAfter=9, keepWithNext=True, **common),
        "h3": ParagraphStyle("EpisodeChapter", fontSize=12, leading=19, textColor=teal, spaceBefore=12, spaceAfter=8, keepWithNext=True, **common),
        "speaker": ParagraphStyle("EpisodeSpeaker", fontSize=9, leading=15, textColor=teal, spaceBefore=7, spaceAfter=5, keepWithNext=True, **common),
        "draft": ParagraphStyle("EpisodeDraft", fontSize=10, leading=17, textColor=colors.HexColor("#88581D"), backColor=colors.HexColor("#FFF1D9"), borderPadding=9, spaceBefore=4, spaceAfter=16, **common),
    }
    story = []

    def add(text: object, style: str = "body", markup: bool = False) -> None:
        if style == "body" and not markup and len(_text(text)) > 600:
            # A kept speaker heading followed by one enormous Paragraph would
            # otherwise push all of it to a fresh page before splitting. Give
            # the heading a short opening passage, then let the rest flow.
            content = _text(text)
            boundary = re.search(r"[。！？.!?]", content[160:320])
            split = 161 + boundary.start() if boundary else 240
            story.append(Paragraph(_xml(content[:split]), styles["continuation"]))
            story.append(Paragraph(_xml(content[split:]), styles["body"]))
            return
        story.append(Paragraph(_text(text) if markup else _xml(text), styles[style]))

    add(title, "title")
    if episode.get("is_demo"):
        add("自制功能演示 · 非真实视频转写\n人物、对话与时间为演示内容，没有对应的原始音视频。", "draft")
    if draft:
        add("草稿 · 尚未发布\n" + _draft_note(episode), "draft")
    for label, value in _metadata(episode):
        add(f"{label}：{value}", "meta")
    source_url = _video_url(episode)
    if source_url:
        add(f'来源：<link href="{_xml(source_url)}" color="#276F72">{_xml(source_url)}</link>', "meta", True)
    review = episode.get("review") or {}
    add(
        f"说话人归属校对：{'已确认' if review.get('speakers_confirmed') else '待复核'}　"
        f"内容校对：{'已完成' if review.get('content_checked') else '待校对'}",
        "meta",
    )
    if episode.get("description"):
        story.append(Spacer(1, 9))
        add(episode["description"])
    speakers = _speaker_map(episode)
    add("本期人物", "h2")
    for speaker in episode.get("speakers", []):
        label = speakers.get(speaker.get("id"), "说话人待确认")
        role = _one_line(speaker.get("role"))
        add(label + (f"（{role}）" if role else ""))
    if not episode.get("speakers"):
        add("人物待确认。")
    add("本期摘要", "h2")
    for index, item in enumerate(episode.get("summary") or []):
        add(f"{index + 1}. {_text(item)}")
    if not episode.get("summary"):
        add("暂无摘要。")
    add("章节目录", "h2")
    targets = _chapter_targets(episode)
    for chapter, target in targets:
        label = _xml(f"{_timestamp(chapter.get('start'))}  {_one_line(chapter.get('title'))}")
        if target is not None:
            add(f'<link href="#segment-{target + 1}" color="#276F72">{label}</link>', "body", True)
        else:
            add(label, "body", True)
    if not targets:
        add("暂无章节。")
    add("完整对话", "h2")
    for turn in reading_turns(episode):
        index = turn["segments"][0]["index"]
        for chapter, target in targets:
            if target == index:
                add(chapter.get("title"), "h3")
        pending = " · 待核对" if turn["needs_review"] else ""
        label = _xml(f"{_timestamp(turn['start'])} · {_speaker_label(turn, speakers)}{pending}")
        url = _video_url(episode, turn["start"])
        if url:
            label = f'<link href="{_xml(url)}" color="#276F72">{label}</link>'
        add(label, "speaker", True)
        # Never keep a whole speech together: long turns must flow across pages.
        for paragraph, continuation in _pdf_turn_paragraphs(turn):
            add(paragraph, "continuation" if continuation else "body", True)
    if not episode.get("segments"):
        add("尚无转写内容。")
    if episode.get("references"):
        add("人物与节目来源核验", "h2")
        for ref in episode["references"]:
            url = _safe_url(ref.get("url"))
            title = _xml(ref.get("title"))
            label = f'<link href="{_xml(url)}" color="#276F72">{title}</link>' if url else title
            add(label, "reference_title", True)
            if ref.get("note"):
                add(ref["note"], "meta")
    story.append(Spacer(1, 14))
    add(_provenance_note(episode), "meta")

    def footer(canvas, doc):
        width, _ = A4
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#DAE4E2"))
        canvas.line(48, 43, width - 48, 43)
        canvas.setFont(font, 8)
        canvas.setFillColor(muted)
        canvas.drawString(48, 29, "听稿" + (" · 草稿" if draft else ""))
        canvas.drawRightString(width - 48, 29, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=48, leftMargin=48,
        topMargin=46, bottomMargin=60, title=title,
        author=_text((episode.get("source") or {}).get("author")),
        allowSplitting=True,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def export_episode(episode: dict, out_dir: Path, formats: list[str]) -> dict[str, Path]:
    """Export requested formats, preserving existing files if rendering fails.

    ``markdown`` and ``pdf`` are supported. Filenames are based on the episode ID;
    unsafe IDs are normalized with a hash so exports cannot escape ``out_dir``.
    """
    requested = list(dict.fromkeys(formats))
    unsupported = set(requested) - {"markdown", "pdf"}
    if unsupported:
        raise ValueError("不支持的导出格式：" + ", ".join(sorted(unsupported)))
    if not requested:
        return {}
    font = _pdf_font() if "pdf" in requested else None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_id(episode.get("id"))
    pending = []
    exported = {}
    try:
        for format_name in requested:
            suffix = ".md" if format_name == "markdown" else ".pdf"
            target = out_dir / (name + suffix)
            with tempfile.NamedTemporaryFile(dir=out_dir, prefix=".export-", suffix=suffix, delete=False) as stream:
                temporary = Path(stream.name)
            pending.append((temporary, target))
            if format_name == "markdown":
                temporary.write_text(render_markdown(episode), encoding="utf-8")
            else:
                _render_pdf(episode, temporary, font)
            exported[format_name] = target
        for temporary, target in pending:
            temporary.replace(target)
        return exported
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
