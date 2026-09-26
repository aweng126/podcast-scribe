"""Import timestamped transcripts without inventing speaker identities."""
from __future__ import annotations

from html import unescape
import json
from pathlib import Path
import re

from .model import ContentError


def _seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def normalize_segments(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    segments, people, mapping = [], [], {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ContentError("转写段落必须是对象")
        text = row.get("text", row.get("content", ""))
        if not isinstance(text, str):
            raise ContentError(f"第 {index} 段转写文本必须是字符串")
        text = text.strip()
        if not text:
            continue
        speaker = row.get("speaker_id", row.get("speaker"))
        if speaker is not None and str(speaker).strip() and str(speaker).lower() not in ("unknown", "none", "null"):
            label = str(speaker)
            if label not in mapping:
                ident = f"speaker-{len(mapping) + 1}"
                mapping[label] = ident
                people.append({"id": ident, "name": f"说话人 {len(mapping)}", "role": "", "source_label": label})
            speaker_id = mapping[label]
        else:
            speaker_id = None
        try:
            start, end = float(row.get("start", row.get("from"))), float(row.get("end", row.get("to")))
        except (TypeError, ValueError) as exc:
            raise ContentError("每个转写段落必须含 start/end（秒）") from exc
        segments.append({"id": f"seg-{len(segments)+1:05d}", "start": start, "end": end,
                         "speaker_id": speaker_id, "raw_text": text, "text": text,
                         "review_status": "unreviewed"})
    if not segments:
        raise ContentError("文件没有可用转写内容")
    return segments, people


def read_transcript(path: Path) -> tuple[list[dict], list[dict]]:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        obj = json.loads(text)
        if not isinstance(obj, (dict, list)):
            raise ContentError("JSON 需要对象或段落数组")
        rows = obj if isinstance(obj, list) else obj.get("segments", obj.get("body"))
        if not isinstance(rows, list):
            raise ContentError("JSON 需要 segments、B站 body 或段落数组")
        return normalize_segments(rows)
    if path.suffix.lower() not in (".srt", ".vtt"):
        raise ContentError("转写文件支持 JSON、SRT、VTT")
    rows = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        if block.startswith(("NOTE", "STYLE", "REGION")):
            continue
        lines = block.strip().splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
            if match:
                body = " ".join(lines[index + 1:])
                voice = re.search(r"<v(?:\.[^ >]+)?\s+([^>]+)>", body)
                rows.append({"start": _seconds(match[1]), "end": _seconds(match[2]),
                             "speaker": voice[1] if voice else None,
                             "text": unescape(re.sub(r"<[^>]+>", "", body))})
                break
    return normalize_segments(rows)
