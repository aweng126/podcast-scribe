"""Build a self-contained, static Chinese podcast transcript library.

The generated site can be opened from disk or served by any static web server.
It intentionally requires no build tools, CDN, analytics, or remote assets.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from .reading import reading_turns, segment_text_parts


_ASSETS = Path(__file__).with_name("assets")


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number) if math.isfinite(number) else 0


def _web_url(value: object) -> str:
    """Only allow ordinary web links; never publish file/javascript URLs."""
    url = _text(value).strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme in {"https", "http"} and parts.netloc else ""


def _copy_downloads(episode: dict, out_dir: Path, identifier: str) -> dict:
    copied = {}
    artifacts = episode.get("artifacts") or {}
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", identifier).strip("-")[:60] or "episode"
    suffix = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:8]
    for kind, extension in (("markdown", "md"), ("pdf", "pdf")):
        value = artifacts.get(kind)
        if not isinstance(value, (str, Path)) or not str(value):
            continue
        source = Path(value).expanduser()
        if not source.is_file():
            continue
        relative = Path("downloads") / f"{safe_id}-{suffix}.{extension}"
        destination = out_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        copied[kind] = relative.as_posix()
    return copied


def _public_episode(episode: dict, out_dir: Path, identifier: str) -> dict:
    """Select public reading data, excluding original paths and raw ASR text."""
    source = episode.get("source") or {}
    series = episode.get("series") or {}
    series_id = _text(series.get("id")) or "uncategorized"
    review = episode.get("review") or {}
    public = {
        "id": identifier,
        "title": _text(episode.get("title")) or "未命名单集",
        "description": _text(episode.get("description")),
        "source": {
            "url": _web_url(source.get("url")),
            "platform": _text(source.get("platform")),
            "author": _text(source.get("author")),
        },
        "series": {
            "id": series_id,
            "title": _text(series.get("title")) or "未分类节目",
            "description": _text(series.get("description")),
        },
        "duration_seconds": _number(episode.get("duration_seconds")),
        "published_at": _text(episode.get("published_at")),
        "speakers": [
            {"id": _text(speaker.get("id")), "name": _text(speaker.get("name")) or "未命名说话人", "role": _text(speaker.get("role"))}
            for speaker in episode.get("speakers", []) if isinstance(speaker, dict)
        ],
        "segments": [
            {
                "id": _text(segment.get("id")) or f"segment-{index}",
                "start": _number(segment.get("start")),
                "end": _number(segment.get("end")),
                "speaker_id": None if segment.get("speaker_id") is None else _text(segment.get("speaker_id")),
                "text": _text(segment.get("text") or segment.get("raw_text")),
                "review_status": _text(segment.get("review_status")),
            }
            for index, segment in enumerate(episode.get("segments", [])) if isinstance(segment, dict)
        ],
        "chapters": [
            {"id": _text(chapter.get("id")), "title": _text(chapter.get("title")), "start": _number(chapter.get("start")), "segment_id": _text(chapter.get("segment_id"))}
            for chapter in episode.get("chapters", []) if isinstance(chapter, dict)
        ],
        "summary": [_text(item) for item in episode.get("summary", []) if item],
        "references": [
            {
                "title": _text(reference.get("title")) or "核验来源",
                "url": _web_url(reference.get("url")),
                "note": _text(reference.get("note")),
            }
            for reference in (episode.get("references") or [])
            if isinstance(reference, dict) and _web_url(reference.get("url"))
        ],
        "status": "published" if episode.get("status") == "published" else "draft",
        "is_demo": bool(episode.get("is_demo", False)),
        "review": {"speakers_confirmed": bool(review.get("speakers_confirmed")), "content_checked": bool(review.get("content_checked"))},
        "downloads": _copy_downloads(episode, out_dir, identifier),
    }
    public["review"].update({key: review[key] for key in ("mode", "basis") if key in review})
    # Build reading turns only after stripping private ASR fields. Keep original
    # segment positions for chapter navigation without breaking the dialogue.
    public["turns"] = [
        {
            "speaker_id": turn["speaker_id"],
            "start": turn["start"],
            "end": turn["end"],
            "segment_indices": [segment["index"] for segment in turn["segments"]],
            "text": turn["text"],
            "text_parts": segment_text_parts(turn["segments"]),
            "needs_review": turn["needs_review"],
        }
        for turn in reading_turns(public)
    ]
    return public


def build_site(episodes: list[dict], out_dir: Path, include_drafts: bool = False) -> Path:
    """Write an offline-ready site and return its ``index.html`` path.

    Only non-demo ``status='published'`` episodes are included by default. Draft preview
    is explicit and clearly labelled. Artifact paths are resolved relative to
    the process working directory; callers may use absolute paths instead.
    Missing artifacts simply do not receive a download link. A manifest tracks
    generated downloads so rebuilding removes artifacts from removed drafts
    or publications, without touching unrelated files in the output directory.
    """
    out_dir = Path(out_dir)
    selected = [episode for episode in episodes if include_drafts or (episode.get("status") == "published" and not episode.get("is_demo"))]
    identifiers = [_text(episode.get("id")) or f"episode-{index + 1}" for index, episode in enumerate(selected)]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Episode IDs must be unique within a site.")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / ".site-manifest.json"
    try:
        old_downloads = json.loads(manifest_path.read_text(encoding="utf-8")).get("downloads", [])
    except (OSError, ValueError, AttributeError):
        old_downloads = []
    data = {
        "schema_version": 1,
        "preview": bool(include_drafts),
        "episodes": [_public_episode(episode, out_dir, identifier) for episode, identifier in zip(selected, identifiers)],
    }
    # A literal closing script tag must never be able to escape the JSON block.
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (_ASSETS / "index.html").read_text(encoding="utf-8")
    page = template.replace("<!-- TRANSCRIPT_DATA -->", serialized)
    target = out_dir / "index.html"
    target.write_text(page, encoding="utf-8")
    for name in ("app.css", "app.js"):
        destination = out_dir / name
        source = _ASSETS / name
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
    new_downloads = sorted(path for episode in data["episodes"] for path in episode["downloads"].values())
    # Only our exact generated filename pattern can be removed. Do not trust
    # paths from a hand-edited manifest to point inside this output directory.
    for stale in old_downloads if isinstance(old_downloads, list) else []:
        if isinstance(stale, str) and stale not in new_downloads and re.fullmatch(r"downloads/[a-zA-Z0-9_-]+-[0-9a-f]{8}\.(?:md|pdf)", stale):
            candidate = out_dir / stale
            if candidate.is_file() and not candidate.is_symlink():
                candidate.unlink()
    manifest_path.write_text(json.dumps({"downloads": new_downloads}, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
