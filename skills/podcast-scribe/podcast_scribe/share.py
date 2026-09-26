"""Explicit, local-only preparation of a public community submission.

The public schema is a whitelist, independent of private episode storage. This
module never uploads files, opens a browser, or changes local publication state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from .model import ContentError, validate_episode


MAX_SUBMISSION_BYTES = 512 * 1024 * 1024
MAX_DURATION_SECONDS = 7 * 24 * 60 * 60
_EPISODE_FIELDS = {
    "id", "title", "description", "source", "series", "duration_seconds",
    "published_at", "speakers", "segments", "chapters", "summary", "references", "review",
}


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContentError(f"{label} 字段必须且只能为：{', '.join(sorted(fields))}")


def _text(value, label, *, maximum, empty=False, multiline=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ContentError(f"{label} 必须是{'可空' if empty else '非空'}文本，最多 {maximum} 字符")
    allowed = "\r\n\t" if multiline else ""
    if any((ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value):
        raise ContentError(f"{label} 包含不支持的控制字符")


def _url(value, label):
    _text(value, label, maximum=2048)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and "\\" not in value and not any(char.isspace() for char in value))
        parsed.port  # Reject malformed ports as well as invalid bracketed hosts.
    except ValueError as exc:
        raise ContentError(f"{label} 必须是有效的 http/https 链接") from exc
    if not valid:
        raise ContentError(f"{label} 必须是无账号密码的 http/https 链接")


def _list(value, label, maximum, *, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ContentError(f"{label} 必须是包含 {minimum}–{maximum} 项的列表")


def _time(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= MAX_DURATION_SECONDS):
        raise ContentError(f"{label} 必须是 0–{MAX_DURATION_SECONDS} 范围内的有限秒数")


def _encoded_chunks(data):
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        for chunk in encoder.iterencode(data):
            yield chunk.encode("utf-8")
        yield b"\n"
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ContentError("投稿必须是有效的 UTF-8 JSON 数据") from exc


def _encoded(data):
    content = bytearray()
    for chunk in _encoded_chunks(data):
        content.extend(chunk)
        if len(content) > MAX_SUBMISSION_BYTES:
            raise ContentError("公开投稿文件不能超过 512 MiB")
    return bytes(content)


def _check_encoded_size(data):
    total = 0
    for chunk in _encoded_chunks(data):
        total += len(chunk)
        if total > MAX_SUBMISSION_BYTES:
            raise ContentError("公开投稿文件不能超过 512 MiB")


def _render_episode(data):
    ep = deepcopy(data["episode"])
    ep.update(schema_version=1, status="published", revision=1, is_demo=False, artifacts={})
    for segment in ep["segments"]:
        segment["raw_text"] = segment["text"]
    return ep


def validate_submission(data: dict) -> dict:
    """Reject unknown fields and validate all public content without mutation."""
    _object(data, {"schema_version", "episode", "attribution"}, "submission")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ContentError("公开投稿需要 schema_version=1")
    _text(data["attribution"], "attribution", maximum=200)
    ep = data["episode"]
    _object(ep, _EPISODE_FIELDS, "episode")
    _text(ep["id"], "episode.id", maximum=100)
    _text(ep["title"], "episode.title", maximum=300)
    _text(ep["description"], "episode.description", maximum=20000, empty=True, multiline=True)
    _time(ep["duration_seconds"], "duration_seconds")
    _text(ep["published_at"], "published_at", maximum=64, empty=True)
    if ep["published_at"]:
        try:
            if len(ep["published_at"]) == 10:
                date.fromisoformat(ep["published_at"])
            else:
                datetime.fromisoformat(ep["published_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContentError("published_at 必须为空或 ISO 日期/时间") from exc
    _object(ep["source"], {"platform", "url", "author"}, "source")
    _text(ep["source"]["platform"], "source.platform", maximum=100, empty=True)
    _text(ep["source"]["author"], "source.author", maximum=300, empty=True)
    _url(ep["source"]["url"], "source.url")
    if ep["source"]["platform"].strip().lower() == "demo":
        raise ContentError("演示内容不能投稿到公共文稿库")
    _object(ep["series"], {"id", "title", "description"}, "series")
    _text(ep["series"]["id"], "series.id", maximum=100)
    _text(ep["series"]["title"], "series.title", maximum=300)
    _text(ep["series"]["description"], "series.description", maximum=20000, empty=True, multiline=True)
    _object(ep["review"], {"speakers_confirmed", "content_checked"}, "review")
    if any(value is not True for value in ep["review"].values()):
        raise ContentError("公开投稿需要完成人物与内容校对")
    _list(ep["speakers"], "speakers", 200, minimum=1)
    for speaker in ep["speakers"]:
        _object(speaker, {"id", "name", "role"}, "speaker")
        _text(speaker["id"], "speaker.id", maximum=100)
        _text(speaker["name"], "speaker.name", maximum=300)
        _text(speaker["role"], "speaker.role", maximum=1000, empty=True)
    _list(ep["segments"], "segments", 10000, minimum=1)
    for segment in ep["segments"]:
        _object(segment, {"id", "start", "end", "speaker_id", "text", "review_status"}, "segment")
        _text(segment["id"], "segment.id", maximum=100)
        _text(segment["speaker_id"], "segment.speaker_id", maximum=100)
        _text(segment["text"], "segment.text", maximum=50000, multiline=True)
        _time(segment["start"], "segment.start")
        _time(segment["end"], "segment.end")
        if segment["review_status"] != "reviewed":
            raise ContentError("公开投稿的每个段落都必须明确标记为 reviewed")
    _list(ep["chapters"], "chapters", 500, minimum=1)
    for chapter in ep["chapters"]:
        _object(chapter, {"id", "title", "start", "segment_id"}, "chapter")
        _text(chapter["id"], "chapter.id", maximum=100)
        _text(chapter["title"], "chapter.title", maximum=300)
        _text(chapter["segment_id"], "chapter.segment_id", maximum=100)
        _time(chapter["start"], "chapter.start")
    _list(ep["summary"], "summary", 100, minimum=1)
    for item in ep["summary"]:
        _text(item, "summary item", maximum=5000, multiline=True)
    _list(ep["references"], "references", 200)
    for reference in ep["references"]:
        _object(reference, {"title", "url"}, "reference")
        _text(reference["title"], "reference.title", maximum=300)
        _url(reference["url"], "reference.url")
    # Reuse cross-reference, unique ID, chronological and publication checks.
    validate_episode(_render_episode(data), for_publication=True)
    _check_encoded_size(data)
    return data


def make_submission(episode: dict, *, attribution: str) -> dict:
    """Project a reviewed local episode onto the public whitelist."""
    validate_episode(episode, for_publication=True)
    ep = {key: deepcopy(episode.get(key, "")) for key in
          ("id", "title", "description", "duration_seconds", "published_at")}
    ep["source"] = {key: episode.get("source", {}).get(key, "") for key in ("platform", "url", "author")}
    ep["series"] = {key: episode["series"].get(key, "") for key in ("id", "title", "description")}
    ep["speakers"] = [{key: person.get(key, "") for key in ("id", "name", "role")}
                      for person in episode["speakers"]]
    ep["segments"] = [{key: segment[key] for key in ("id", "start", "end", "speaker_id", "text", "review_status")}
                      for segment in episode["segments"]]
    ep["chapters"] = [{key: chapter[key] for key in ("id", "title", "start", "segment_id")}
                      for chapter in episode["chapters"]]
    ep["summary"] = deepcopy(episode["summary"])
    ep["references"] = [{key: reference[key] for key in ("title", "url")}
                        for reference in episode.get("references", [])]
    ep["review"] = {key: episode["review"][key] for key in ("speakers_confirmed", "content_checked")}
    return validate_submission({"schema_version": 1, "episode": ep, "attribution": attribution})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContentError(f"JSON 包含重复字段：{key}")
        result[key] = value
    return result


def loads_submission(payload: bytes) -> dict:
    """Parse bounded UTF-8 JSON, rejecting ambiguous duplicate properties."""
    if not isinstance(payload, bytes):
        raise ContentError("投稿 JSON 需要 UTF-8 字节")
    if len(payload) > MAX_SUBMISSION_BYTES:
        raise ContentError("公开投稿文件不能超过 512 MiB")
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContentError(f"无法读取公开投稿 JSON：{exc}") from exc
    return validate_submission(data)


def load_submission(path: Path) -> dict:
    with Path(path).open("rb") as stream:
        return loads_submission(stream.read(MAX_SUBMISSION_BYTES + 1))


def submission_episode(data: dict) -> dict:
    """Produce an isolated renderer record from already public text only."""
    validate_submission(data)
    return _render_episode(data)


def canonical_bytes(data: dict) -> bytes:
    validate_submission(data)
    return _encoded(data)


def submission_digest(data: dict) -> str:
    validate_submission(data)
    digest = hashlib.sha256()
    for chunk in _encoded_chunks(data):
        digest.update(chunk)
    return digest.hexdigest()


def issue_url(submission: dict, repository: str = "aweng126/podcast-scribe") -> str:
    """Return a short form URL. The user reviews and uploads JSON themselves."""
    validate_submission(submission)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository):
        raise ContentError("repository 必须为 owner/repository")
    base = f"https://github.com/{repository}/issues/new?"
    fields = {"template": "share.yml", "title": "[分享] " + submission["episode"]["title"][:80],
              "content_digest": submission_digest(submission)}
    # Long sources/attributions can be filled in the form; never truncate their
    # meaning or produce a URL that common clients cannot open.
    for key, value in (("source_url", submission["episode"]["source"]["url"]),
                       ("attribution", submission["attribution"])):
        candidate = {**fields, key: value}
        if len(base + urlencode(candidate)) <= 1800:
            fields = candidate
    return base + urlencode(fields)
