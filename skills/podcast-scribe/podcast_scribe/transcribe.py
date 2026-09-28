"""Resumable diarization with anonymous voice references between audio chunks."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

from .audio_chunks import (MAX_CHUNK_SECONDS, MAX_UPLOAD_BYTES, analyze_audio,
                           extract_audio, ffmpeg_binary, plan_chunks, prepare_audio)
from .cache_lifecycle import register_media, touch_media_tree, was_removed
from .model import ContentError, write_json
from .transcripts import normalize_segments

MODEL = "gpt-4o-transcribe-diarize"
CACHE_VERSION = 2
MAX_ATTEMPTS = 3
MAX_REFERENCES = 4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContentError(f"转写缓存损坏：{path}；请保留缓存并检查文件") from exc
    if not isinstance(data, dict):
        raise ContentError(f"转写缓存不是对象：{path}")
    return data


def _ensure_media(path: Path, expected_sha256: str, rebuild, message: str):
    """Only a recorded cache eviction permits reconstructing missing media."""
    if not path.exists() and was_removed(path, expected_sha256):
        rebuild()
        if not path.is_file() or _sha256(path) != expected_sha256:
            raise ContentError(message)
        register_media(path)
    elif not path.is_file() or _sha256(path) != expected_sha256:
        raise ContentError(message)


def _rows(payload: dict, duration: float | None = None) -> list[dict]:
    """Reject malformed/truncated timing before recording a successful request."""
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ContentError("转写响应缺少 segments 数组")
    if duration is not None and "duration" in payload:
        observed = payload["duration"]
        if (isinstance(observed, bool) or not isinstance(observed, (int, float))
                or not math.isfinite(observed) or abs(observed - duration) > 2):
            raise ContentError("转写响应时长与当前分片不一致，未计为成功")
    result, previous_start = [], -1.0
    for row in payload["segments"]:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ContentError("转写响应包含无效文本段落")
        start, end = row.get("start"), row.get("end")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0 for value in (start, end)):
            raise ContentError("转写时间戳必须是非负有限数值")
        if end < start or start < previous_start or (duration is not None and end > duration + 0.25):
            raise ContentError("转写时间戳倒置或超出当前分片，未计为成功")
        previous_start = start
        speaker = row.get("speaker", row.get("speaker_id"))
        if speaker is not None and not isinstance(speaker, str):
            raise ContentError("转写说话人标签必须是字符串或 null")
        if row["text"].strip():
            result.append({"start": min(start, duration) if duration is not None else start,
                           "end": min(end, duration) if duration is not None else end,
                           "speaker": speaker, "text": row["text"].strip()})
    if not result and isinstance(payload.get("text"), str) and payload["text"].strip():
        raise ContentError("转写响应有正文却没有有效带时间戳段落，未计为成功")
    return result


def _configuration(language: str) -> dict:
    return {"version": CACHE_VERSION, "model": MODEL, "language": language,
            "endpoint_sha256": hashlib.sha256(_endpoint().encode()).hexdigest(),
            "audio": "mono16k32kbps", "max_chunk_seconds": MAX_CHUNK_SECONDS,
            "max_upload_bytes": MAX_UPLOAD_BYTES, "silence": "-35dB:0.4s:30s-window",
            "references": "up-to-4:isolated-2-to-6s:v1"}


def _endpoint() -> str:
    return (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")


def _legacy_cache(source: Path, cache_dir: Path, language: str):
    # Old caches predate endpoint fingerprints and are safe only for the default.
    if _endpoint() != "https://api.openai.com/v1":
        return None
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(f"{MODEL}:{language}:mono16k32kbps:v1".encode())
    path = cache_dir / digest.hexdigest() / "transcription.json"
    if not path.is_file():
        return None
    touch_media_tree(path.parent)
    payload = _read(path)
    return normalize_segments(_rows(payload)), payload.get("duration"), path


def _load_manifest(work: Path, source_hash: str, config: dict) -> dict | None:
    path = work / "manifest.json"
    if not path.is_file():
        return None
    manifest = _read(path)
    if manifest.get("source_sha256") != source_hash or manifest.get("config") != config:
        raise ContentError("转写缓存与当前源文件或配置不符，已停止复用")
    duration, chunks = manifest.get("duration"), manifest.get("chunks")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration)
            or duration <= 0 or not isinstance(chunks, list) or not chunks):
        raise ContentError("转写缓存的分片规划无效")
    previous = 0.0
    for index, chunk in enumerate(chunks, 1):
        if (not isinstance(chunk, dict) or chunk.get("id") != f"chunk-{index:04d}"
                or chunk.get("start") != previous or not isinstance(chunk.get("end"), (int, float))
                or not math.isfinite(chunk["end"]) or chunk["end"] <= previous
                or chunk["end"] - previous > MAX_CHUNK_SECONDS + 0.001
                or chunk.get("boundary") not in ("silence", "forced", "end")):
            raise ContentError("转写缓存的时间轴不连续或分片无效")
        previous = chunk["end"]
    if abs(previous - duration) > 0.000001:
        raise ContentError("转写缓存没有覆盖完整音频时长")
    return manifest


class _IncompleteStream(ContentError):
    pass


def _stream_payload(stream, duration: float | None) -> dict:
    """Only a complete, internally consistent diarized stream is cacheable."""
    segments, identifiers = [], set()
    for event in stream:
        data = event if isinstance(event, dict) else event.model_dump()
        if not isinstance(data, dict):
            raise ContentError("转写流包含无效事件，未计为成功")
        kind = data.get("type")
        if kind == "transcript.text.segment":
            ident = data.get("id")
            if not isinstance(ident, str) or not ident or ident in identifiers:
                raise ContentError("转写流段落 ID 缺失或重复，未计为成功")
            if not all(key in data for key in ("start", "end", "speaker", "text")):
                raise ContentError("转写流段落字段不完整，未计为成功")
            row = {key: data[key] for key in ("id", "start", "end", "speaker", "text")}
            # Empty text is still a segment event. Validate its timing and
            # speaker, retain it for ID/order and terminal-text checks, then
            # let _rows omit it from the normalized reading transcript.
            _rows({"segments": [row]}, duration)
            if segments and row["start"] < segments[-1]["start"]:
                raise ContentError("转写流段落未按时间排序，未计为成功")
            identifiers.add(ident)
            segments.append(row)
        elif kind == "transcript.text.delta":
            if not isinstance(data.get("delta"), str):
                raise ContentError("转写流增量文本无效，未计为成功")
            # Deltas are provisional and may repeat completed segment text.
        elif kind == "transcript.text.done":
            text = data.get("text")
            if not isinstance(text, str):
                raise ContentError("转写完成事件缺少全文，未计为成功")
            joined = "".join(row["text"] for row in segments)
            if "".join(text.split()) != "".join(joined.split()):
                raise ContentError("转写完成全文与已完成段落不一致，未计为成功")
            payload = {"text": text, "segments": segments}
            for key in ("usage", "duration"):
                if data.get(key) is not None:
                    payload[key] = data[key]
            _rows(payload, duration)
            # done is the terminal success signal. Do not wait for transport EOF,
            # which may be delayed or reported as an error during normal close.
            return payload
        else:
            raise ContentError("转写流返回错误或未知事件，未计为成功")
    raise _IncompleteStream("转写流在完成事件之前中断，未计为成功")


def _request(client, audio_path: Path, language: str, references: list[dict], work: Path,
             duration: float | None = None) -> dict:
    extra = {}
    if references:
        extra = {"extra_body": {
            "known_speaker_names": [ref["name"] for ref in references],
            "known_speaker_references": ["data:audio/wav;base64," + base64.b64encode(
                (work / ref["file"]).read_bytes()).decode("ascii") for ref in references],
        }}
    for attempt in range(MAX_ATTEMPTS):
        try:
            with audio_path.open("rb") as audio:
                stream = client.audio.transcriptions.create(
                    file=audio, model=MODEL, response_format="diarized_json",
                    chunking_strategy="auto", language=language, stream=True, **extra,
                )
                try:
                    return _stream_payload(stream, duration)
                finally:
                    try:
                        stream.close()
                    except Exception:
                        # Closing a received, validated done must never cause a
                        # second billable request or hide the original failure.
                        pass
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            temporary = status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
            temporary = temporary or isinstance(exc, _IncompleteStream) or type(exc).__name__ in (
                "APIConnectionError", "APITimeoutError", "TimeoutError", "RemoteProtocolError",
                "ConnectError", "ReadError", "WriteError", "ProxyError", "ConnectTimeout",
                "ReadTimeout", "WriteTimeout", "PoolTimeout",
            )
            if not temporary or attempt + 1 == MAX_ATTEMPTS:
                if isinstance(exc, ContentError):
                    raise
                label = f"HTTP {status}" if status else type(exc).__name__
                raise ContentError(f"转写接口失败（{label}）；已保存成功分片，重跑原命令可继续。") from exc
            # A timed-out request may already have been billed; never retry forever.
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _reference_context(references: list[dict]) -> list[dict]:
    return [{"name": ref["name"], "source_label": ref["source_label"], "sha256": ref["sha256"]}
            for ref in references]


def _add_references(rows: list[dict], mapped: list[dict], references: list[dict],
                    audio_path: Path, work: Path, chunk: dict, previous: list[dict]):
    """Choose only 2–6 s contained in one diarized turn, excluding any overlap."""
    represented = {ref["source_label"] for ref in references}
    saved = {ref["name"]: ref for ref in previous}
    for index, row in enumerate(rows):
        label = mapped[index]["speaker"]
        if label is None or label in represented or len(references) >= MAX_REFERENCES:
            continue
        start, end = row["start"] + 0.2, min(row["end"] - 0.2, row["start"] + 6.2)
        if end - start < 2:
            continue
        if any(other_index != index and other["start"] < end and other["end"] > start
               for other_index, other in enumerate(rows)):
            continue
        name = f"voice-{len(references) + 1:04d}"
        path = work / "references" / f"{name}.wav"
        absolute_start, absolute_end = chunk["start"] + start, chunk["start"] + end
        old = saved.get(name)
        if old:
            if (old.get("source_label") != label or old.get("start") != absolute_start
                    or old.get("end") != absolute_end):
                raise ContentError("声源参考缓存校验失败，请保留成功响应并检查缓存")
            _ensure_media(path, old.get("sha256"),
                          lambda: extract_audio(audio_path, path, absolute_start, absolute_end, reference=True),
                          "声源参考缓存校验失败，请保留成功响应并检查缓存")
        else:
            extract_audio(audio_path, path, absolute_start, absolute_end, reference=True)
            register_media(path)
        references.append({"name": name, "source_label": label, "file": str(path.relative_to(work)),
                           "sha256": _sha256(path), "chunk_id": chunk["id"],
                           "start": absolute_start, "end": absolute_end,
                           "verification": "automatic_reference_requires_review"})
        represented.add(label)


def _normalized(rows: list[dict], *, multiple_chunks: bool,
                allow_empty: bool = False) -> tuple[list[dict], list[dict]]:
    if not rows and allow_empty:
        return [], []
    segments, people = normalize_segments(rows)
    for segment, row in zip(segments, rows):
        if row.get("boundary_review") or row.get("speaker_review"):
            segment["review_status"] = "needs_review"
    if multiple_chunks:
        for person in people:
            person["diarization_note"] = "跨片对应为自动声源参考或独立片级标签，需人工核对；不代表真实身份"
    return segments, people


def _metadata(metadata: dict | None, manifest: dict, work: Path, rows: list[dict]):
    boundaries = [c["end"] for c in manifest["chunks"] if c["boundary"] == "forced"]
    unmatched = len({row["speaker"] for row in rows if row.get("speaker_review")})
    if metadata is not None:
        metadata.update(duration_seconds=manifest["duration"], transcription_cache=str(work / "manifest.json"),
                        forced_boundary_seconds=boundaries, unmatched_speaker_labels=unmatched)
    print(f"转写缓存：{work / 'manifest.json'}；待核对强制边界 {len(boundaries)} 处、未匹配声源标签 {unmatched} 个。",
          file=sys.stderr)


@contextmanager
def _cache_lock(work: Path):
    """Serialize one content/configuration cache across all output destinations."""
    import fcntl  # Supported runtime: macOS, Linux and WSL.
    work.mkdir(parents=True, exist_ok=True)
    # Keep this inode after unlock: unlinking could split competing lock holders.
    with (work / ".transcribe.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContentError(f"相同音频与配置的转写正在运行：{work}；本次未发送转写请求，请等待完成后重跑原命令复用缓存。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def transcribe_audio(source: Path, cache_dir: Path, *, language: str = "zh",
                     metadata: dict | None = None,
                     allow_empty: bool = False) -> tuple[list[dict], list[dict]]:
    source, cache_dir = Path(source), Path(cache_dir)
    if not source.is_file():
        raise ContentError(f"音视频文件不存在：{source}")
    old = _legacy_cache(source, cache_dir, language)
    if old is not None:
        normalized, duration, path = old
        if metadata is not None:
            metadata.update(duration_seconds=duration or max(row["end"] for row in normalized[0]),
                            transcription_cache=str(path), legacy_cache=True,
                            forced_boundary_seconds=[], unmatched_speaker_labels=0)
        return normalized
    source_hash, config = _sha256(source), _configuration(language)
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    work = cache_dir / hashlib.sha256(f"{source_hash}:{config_hash}".encode()).hexdigest()
    with _cache_lock(work):
        return _transcribe_work(source, work, source_hash, config, config_hash, metadata,
                                allow_empty=allow_empty)


def _transcribe_work(source: Path, work: Path, source_hash: str, config: dict,
                     config_hash: str, metadata: dict | None, *,
                     allow_empty: bool = False) -> tuple[list[dict], list[dict]]:
    language = config["language"]
    touch_media_tree(work)
    manifest = _load_manifest(work, source_hash, config)
    final_path = work / "transcription.json"
    if manifest and manifest.get("status") == "complete" and final_path.is_file():
        if _sha256(final_path) != manifest.get("transcription_sha256"):
            raise ContentError("完整转写缓存校验失败，请保留缓存并检查文件")
        final = _read(final_path)
        _rows(final, manifest["duration"])
        _metadata(metadata, manifest, work, final["segments"])
        return _normalized(final["segments"], multiple_chunks=len(manifest["chunks"]) > 1,
                           allow_empty=allow_empty)
    if not os.environ.get("OPENAI_API_KEY"):
        raise ContentError("未配置 OPENAI_API_KEY。可在运行环境中配置，或用 import 命令导入已有转写；请勿将密钥写入文稿或代码。")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ContentError("缺少 openai Python SDK；请安装项目依赖") from exc
    audio_path = work / "audio.mp3"
    if manifest is None:
        audio_path = prepare_audio(source, audio_path)
        register_media(audio_path)
        analysis = analyze_audio(audio_path)
        manifest = {"schema_version": CACHE_VERSION, "source_sha256": source_hash, "config": config,
                    "duration": analysis["duration"], "audio_sha256": _sha256(audio_path),
                    "chunks": plan_chunks(analysis["duration"], analysis["silences"], max_seconds=MAX_CHUNK_SECONDS),
                    "status": "in_progress", "speaker_references": [],
                    "notes": ["声源参考匹配不代表真实身份；所有说话人和强制切分边界仍需核对。"]}
        write_json(work / "manifest.json", manifest)
    else:
        _ensure_media(audio_path, manifest.get("audio_sha256"),
                      lambda: prepare_audio(source, audio_path),
                      "转换音频缓存缺失或校验失败，请保留已完成响应并检查缓存")
    combined, references = [], []
    previous_references = manifest.get("speaker_references", [])
    with OpenAI(max_retries=0, timeout=600) as client:
        for index, chunk in enumerate(manifest["chunks"]):
            path = work / "chunks" / f"{chunk['id']}.mp3"
            response_path = path.with_suffix(".json")
            context = {"config_sha256": config_hash, "source_sha256": source_hash,
                       "chunk_id": chunk["id"], "start": chunk["start"], "end": chunk["end"],
                       "references": _reference_context(references)}
            cached = response_path.is_file()
            try:
                if cached:
                    stored = _read(response_path)
                    if stored.get("context") != context:
                        raise ContentError(f"{chunk['id']} 的缓存或声源参考配置不匹配")
                    if chunk.get("response_sha256") and _sha256(response_path) != chunk["response_sha256"]:
                        raise ContentError(f"{chunk['id']} 的转写缓存校验失败")
                    payload = stored["response"]
                else:
                    if len(manifest["chunks"]) == 1:
                        path = audio_path
                    elif not chunk.get("audio_sha256"):
                        extract_audio(audio_path, path, chunk["start"], chunk["end"])
                        register_media(path)
                    else:
                        _ensure_media(path, chunk["audio_sha256"],
                                      lambda: extract_audio(audio_path, path, chunk["start"], chunk["end"]),
                                      f"{chunk['id']} 的音频缓存校验失败")
                    if path.stat().st_size > MAX_UPLOAD_BYTES:
                        raise ContentError("单个音频分片超过 24 MB，已停止上传")
                    chunk["audio_sha256"] = _sha256(path)
                    write_json(work / "manifest.json", manifest)
                    payload = _request(client, path, language, references, work, chunk["end"] - chunk["start"])
                    _rows(payload, chunk["end"] - chunk["start"])
                    write_json(response_path, {"context": context, "response": payload})
                rows = _rows(payload, chunk["end"] - chunk["start"])
                known = {ref["name"]: ref["source_label"] for ref in references}
                mapped = []
                left_forced = index > 0 and manifest["chunks"][index - 1]["boundary"] == "forced"
                for row in rows:
                    label = row["speaker"]
                    unknown = label is None or not label.strip() or label.lower() in ("unknown", "none", "null")
                    source_label = None if unknown else known.get(label, f"{chunk['id']}:{label}")
                    boundary_review = ((left_forced and row["start"] < 2)
                                       or (chunk["boundary"] == "forced" and row["end"] > chunk["end"] - chunk["start"] - 2))
                    mapped.append({**row, "start": row["start"] + chunk["start"],
                                   "end": row["end"] + chunk["start"], "speaker": source_label,
                                   "speaker_review": unknown or (index > 0 and label not in known),
                                   "boundary_review": boundary_review})
                chunk.update(status="complete", response_sha256=_sha256(response_path),
                             segment_count=len(rows), reference_names=list(known))
                chunk.pop("error", None)
                # Save billable responses before local reference extraction.
                write_json(work / "manifest.json", manifest)
                if index + 1 < len(manifest["chunks"]) and len(references) < MAX_REFERENCES:
                    _add_references(rows, mapped, references, audio_path, work, chunk, previous_references)
                combined.extend(mapped)
                manifest["speaker_references"] = references.copy()
                write_json(work / "manifest.json", manifest)
                print(f"转写 {index + 1}/{len(manifest['chunks'])}：{'复用缓存' if cached else '已保存'}", file=sys.stderr)
            except (ContentError, OSError, ValueError, KeyError) as exc:
                chunk["status"] = "failed"
                chunk["error"] = type(exc).__name__
                write_json(work / "manifest.json", manifest)
                if isinstance(exc, ContentError):
                    raise
                raise ContentError(f"分片 {chunk['id']} 未完成；已保留成功响应，重跑原命令可继续") from exc
    combined.sort(key=lambda row: row["start"])
    write_json(final_path, {"duration": manifest["duration"], "segments": combined})
    manifest.update(status="complete", transcription_sha256=_sha256(final_path))
    write_json(work / "manifest.json", manifest)
    _metadata(metadata, manifest, work, combined)
    return _normalized(combined, multiple_chunks=len(manifest["chunks"]) > 1,
                       allow_empty=allow_empty)
