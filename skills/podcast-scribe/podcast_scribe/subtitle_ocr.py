"""Resumable, local OCR evidence from a bounded video subtitle region.

OCR candidates are not verified captions, spoken-word truth, or speaker labels.
Only one small frame batch is kept on disk; completed text batches survive failure.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid

from .audio_chunks import ffmpeg_binary
from .defaults import destination_lock
from .model import ContentError

DEFAULT_REGION = (0.05, 0.65, 0.9, 0.3)  # x, y, width, height, relative to frame
BATCH_FRAMES = 60
OCR_TIMEOUT = 30
MIN_CONFIDENCE = 35.0
CACHE_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError) as exc:
        raise ContentError(f"OCR 缓存损坏：{path}；请保留文件检查，或明确使用 refresh 重新提取") from exc


def _number(value, *, positive=False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _configuration(start, end, interval, region, language):
    if not _number(start) or (end is not None and (not _number(end) or end <= start)):
        raise ContentError("OCR 起止时间必须是非负有限数值，且结束时间晚于开始时间")
    if not _number(interval, positive=True) or interval < 0.1:
        raise ContentError("OCR 采样间隔必须是至少 0.1 秒的有限数值")
    if (not isinstance(region, (tuple, list)) or len(region) != 4
            or any(not _number(v) for v in region)):
        raise ContentError("OCR region 需要 x,y,width,height 四个归一化数值")
    x, y, width, height = region
    if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ContentError("OCR region 必须位于画面内，且宽高大于零")
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ContentError("OCR 语言格式无效，例如 chi_sim+eng")
    return {"version": CACHE_VERSION, "start": float(start), "end": None if end is None else float(end),
            "interval": float(interval), "region": list(region), "language": language,
            "min_confidence": MIN_CONFIDENCE, "psm": 6}


def check_tesseract(language: str = "chi_sim+eng") -> str:
    """Return the local binary only after all explicitly requested packs exist."""
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ContentError("OCR 语言格式无效，例如 chi_sim+eng")
    binary = shutil.which("tesseract")
    if not binary:
        raise ContentError("画面字幕 OCR 需要本地 Tesseract；请安装 Tesseract 与 chi_sim、eng 语言包")
    try:
        response = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("无法检查 Tesseract 语言包；请确认本地安装可运行") from exc
    if response.returncode:
        raise ContentError("Tesseract 语言包检查失败；请检查本地安装")
    available = {line.strip() for line in (response.stdout + "\n" + response.stderr).splitlines()}
    missing = set(language.split("+")) - available
    if missing:
        raise ContentError("Tesseract 缺少 OCR 语言包：" + ", ".join(sorted(missing)) + "；请安装后重试")
    return binary


def check_dependencies(*, language: str = "chi_sim+eng") -> dict:
    """Check optional local tools before a caller downloads a large video."""
    return {"tesseract": check_tesseract(language), "ffmpeg": ffmpeg_binary(), "language": language}


def _engine_version(binary: str) -> str:
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("无法读取 Tesseract 版本；未开始字幕提取") from exc
    lines = (result.stdout + "\n" + result.stderr).strip().splitlines()
    if result.returncode or not lines or not lines[0].lower().startswith("tesseract "):
        raise ContentError("Tesseract 版本响应无效；未开始字幕提取")
    return lines[0]


def _video_info(video: Path) -> dict:
    try:
        result = subprocess.run([ffmpeg_binary(), "-nostdin", "-hide_banner", "-i", str(video.resolve())],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("无法读取视频信息；请确认 ffmpeg 与本地素材可用") from exc
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    stream = re.search(r"Stream[^\n]*Video:[^\n]*?\b(\d{2,6})x(\d{2,6})\b", result.stderr)
    if not duration or not stream:
        raise ContentError("OCR 需要含可解码画面和有效时长的本地视频；音频文件不能提取画面字幕")
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    if not _number(seconds, positive=True):
        raise ContentError("视频没有有效时长")
    return {"duration": seconds, "width": int(stream[1]), "height": int(stream[2])}


def _extract_frames(video: Path, directory: Path, start: float, end: float,
                    interval: float, region: list, count: int) -> list[Path]:
    x, y, width, height = region
    crop = f"crop=iw*{width}:ih*{height}:iw*{x}:ih*{y}"
    # Bounded output size matters for both long videos and very high resolution inputs.
    filters = (f"{crop},scale=w='min(iw,1600)':h=-2,tpad=stop_mode=clone:stop_duration={interval},"
               f"fps=fps=1/{interval}:start_time=0:round=near")
    args = [ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.6f}",
            "-t", f"{end - start:.6f}", "-i", str(video.resolve()), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", filters, "-frames:v", str(count), str(directory / "frame-%06d.png")]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=max(120, int((end - start) * 10)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("字幕抽帧超时或失败；已完成 OCR 批次已保留，可重试续跑") from exc
    frames = sorted(directory.glob("frame-*.png"))
    if result.returncode or len(frames) != count:
        raise ContentError("字幕抽帧不完整；未将当前批次记为成功，请检查视频范围或素材")
    return frames


def _text(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)


def _parse_tsv(tsv: str) -> dict:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    if not reader.fieldnames or not {"level", "block_num", "par_num", "line_num", "conf", "text"} <= set(reader.fieldnames):
        raise ContentError("Tesseract 未返回有效 TSV，当前批次未记为成功")
    lines, confidence = {}, []
    try:
        for row in reader:
            if any(row.get(key) is None for key in ("level", "block_num", "par_num", "line_num", "conf", "text")):
                raise ValueError("truncated row")
            if row["level"] != "5" or not row["text"] or not row["text"].strip():
                continue
            score = float(row["conf"])
            if not math.isfinite(score) or not -1 <= score <= 100:
                raise ValueError("invalid confidence")
            score = max(0, score)
            key = (row["block_num"], row["par_num"], row["line_num"])
            lines.setdefault(key, []).append(row["text"])
            confidence.append((score, len(row["text"])))
    except (KeyError, TypeError, ValueError, csv.Error) as exc:
        raise ContentError("Tesseract TSV 内容无效，当前批次未记为成功") from exc
    text = "\n".join(_text(" ".join(words)) for words in lines.values())
    score = sum(score * weight for score, weight in confidence) / sum(weight for _, weight in confidence) if confidence else 0
    # Never drop low-confidence words: they may be a negation or number.
    return {"text": text, "confidence": round(score, 3),
            "low_confidence": any(score < MIN_CONFIDENCE for score, _ in confidence)}


def _recognize(frame: Path, binary: str, language: str) -> dict:
    try:
        # Select the renderer directly: custom TESSDATA_PREFIX directories may
        # contain only traineddata files, without the optional configs/tsv file.
        result = subprocess.run([binary, str(frame), "stdout", "-l", language, "--psm", "6",
                                 "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"],
                                capture_output=True, text=True, timeout=OCR_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("单帧字幕 OCR 超时或失败；已完成批次已保留，可重试续跑") from exc
    if result.returncode:
        raise ContentError("Tesseract 字幕识别失败；当前批次未记为成功")
    return _parse_tsv(result.stdout)


def _separate_static_lines(frames: list[dict], end: float, interval: float) -> tuple[list, list]:
    """Separate persistent overlay lines even when other caption lines change."""
    active, suppress, static = {}, {}, []

    def finish(text, run):
        first, last, confidence_sum = run
        start, stop = frames[first]["time"], min(end, frames[last]["time"] + interval)
        if stop - start < 30 or last - first + 1 < 20:
            return
        static.append({"start": start, "end": stop, "text": text, "samples": last - first + 1,
                       "confidence": round(confidence_sum / (last - first + 1), 3),
                       "reason": "long_unchanging_overlay"})
        for index in range(first, last + 1):
            suppress.setdefault(index, set()).add(text)

    for index, frame in enumerate(frames):
        lines = set(frame["text"].splitlines()) - {""}
        for text in list(active):
            if text not in lines:
                finish(text, active.pop(text))
        for text in lines:
            if text in active:
                active[text][1] = index
                active[text][2] += frame["confidence"]
            else:
                active[text] = [index, index, frame["confidence"]]
    for text, run in active.items():
        finish(text, run)
    cleaned = [{**frame, "text": "\n".join(line for line in frame["text"].splitlines()
                                            if line not in suppress.get(index, set()))}
               for index, frame in enumerate(frames)]
    return cleaned, sorted(static, key=lambda row: (row["start"], row["text"]))


def _aggregate(frames: list[dict], end: float, interval: float) -> tuple[list, list]:
    frames, static = _separate_static_lines(frames, end, interval)
    spans = []
    for frame in frames:
        if not frame["text"]:
            continue
        stop = min(end, frame["time"] + interval)
        # Exact matching preserves numbers, negation, and genuine small subtitle changes.
        if spans and spans[-1]["text"] == frame["text"] and abs(spans[-1]["end"] - frame["time"]) < 0.00001:
            spans[-1]["end"] = stop
            spans[-1]["samples"] += 1
            spans[-1]["confidence_sum"] += frame["confidence"]
            spans[-1]["low_confidence"] |= frame.get("low_confidence", frame["confidence"] < MIN_CONFIDENCE)
        else:
            spans.append({"start": frame["time"], "end": stop, "text": frame["text"], "samples": 1,
                          "confidence_sum": frame["confidence"],
                          "low_confidence": frame.get("low_confidence", frame["confidence"] < MIN_CONFIDENCE)})
    cues = []
    for span in spans:
        span["confidence"] = round(span.pop("confidence_sum") / span["samples"], 3)
        span["id"] = f"ocr-{len(cues) + 1:06d}"
        cues.append(span)
    return cues, static


def _cached_frames(work: Path, manifest: dict, config: dict) -> list[dict]:
    frames = []
    try:
        batches = manifest["batches"]
        if not isinstance(batches, list):
            raise ValueError("batches")
        for batch in batches:
            if (not isinstance(batch, dict) or not re.fullmatch(r"batch-[0-9a-f]{32}\.json", batch["file"])
                    or batch["first"] != len(frames)):
                raise ValueError("batch identity")
            path = work / batch["file"]
            if _sha256(path) != batch["sha256"]:
                raise ValueError("batch hash")
            rows = _read(path)["frames"]
            if not isinstance(rows, list) or not 0 < len(rows) <= BATCH_FRAMES:
                raise ValueError("batch length")
            for row in rows:
                expected = config["start"] + len(frames) * config["interval"]
                if (not isinstance(row, dict) or row.get("time") != expected or not isinstance(row.get("text"), str)
                        or not _number(row.get("confidence")) or row["confidence"] > 100
                        or ("low_confidence" in row and type(row["low_confidence"]) is not bool)):
                    raise ValueError("frame")
                frames.append(row)
        if len(frames) != manifest["next_frame"] or len(frames) > manifest["total_frames"]:
            raise ValueError("progress")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ContentError("OCR 缓存批次或进度不一致；未继续处理，请检查缓存或使用 refresh") from exc
    return frames


def extract_subtitles(video: Path, work_dir: Path, *, start=0, end=None, interval=0.5,
                      region=DEFAULT_REGION, language="chi_sim+eng", refresh=False) -> dict:
    """Extract local candidate captions; never modify a manuscript or call an API.

    Region is normalized (x, y, width, height). A complete cached result does not
    need installed OCR tools; failed jobs resume at the first unfinished batch.
    """
    video, work_dir = Path(video), Path(work_dir)
    if not video.is_file():
        raise ContentError(f"OCR 视频文件不存在：{video}")
    config = _configuration(start, end, interval, region, language)
    source_hash = _sha256(video)
    key = hashlib.sha256((source_hash + json.dumps(config, sort_keys=True)).encode()).hexdigest()
    work = work_dir / "ocr" / key
    with destination_lock(work / "manifest.json"):
        return _extract_locked(video, work, config, source_hash, key, refresh)


def _extract_locked(video: Path, work: Path, config: dict, source_hash: str, key: str, refresh: bool) -> dict:
    start, end, interval, language = (config[name] for name in ("start", "end", "interval", "language"))
    manifest_path = work / "manifest.json"
    if manifest_path.is_file() and not refresh:
        manifest = _read(manifest_path)
        if manifest.get("source_sha256") != source_hash or manifest.get("config") != config:
            raise ContentError("OCR 缓存与素材或参数不一致，已停止复用")
    else:
        # Missing language packs fail before expensive video work or empty success.
        engine_version = _engine_version(check_tesseract(language))
        info = _video_info(video)
        stop = info["duration"] if end is None else float(end)
        if start >= info["duration"] or stop > info["duration"] + 0.01:
            raise ContentError("OCR 时间范围超出本地视频时长")
        stop = min(stop, info["duration"])
        manifest = {"schema_version": 1, "source_sha256": source_hash, "config": config,
                    "engine_version": engine_version,
                    "video": info, "end": stop, "total_frames": math.ceil((stop - start) / interval),
                    "next_frame": 0, "batches": [], "status": "in_progress"}
        _write(manifest_path, manifest)
    if (not _number(manifest.get("end"), positive=True) or manifest["end"] <= start
            or not isinstance(manifest.get("total_frames"), int) or isinstance(manifest["total_frames"], bool)
            or manifest["total_frames"] != math.ceil((manifest["end"] - start) / interval)
            or not isinstance(manifest.get("engine_version"), str)
            or not isinstance(manifest.get("video"), dict)
            or not _number(manifest["video"].get("duration"), positive=True)
            or manifest["end"] != min(end if end is not None else manifest["video"]["duration"], manifest["video"]["duration"])):
        raise ContentError("OCR 缓存时间范围或帧数无效")
    frames = _cached_frames(work, manifest, config)
    binary = check_tesseract(language) if len(frames) < manifest["total_frames"] else None
    while len(frames) < manifest["total_frames"]:
        first = len(frames)
        count = min(BATCH_FRAMES, manifest["total_frames"] - first)
        batch_start = start + first * interval
        batch_end = min(manifest["end"], batch_start + count * interval)
        rows = []
        with tempfile.TemporaryDirectory(prefix="frames-", dir=work) as temporary:
            images = _extract_frames(video, Path(temporary), batch_start, batch_end, interval, config["region"], count)
            for offset, frame in enumerate(images):
                rows.append({"time": start + (first + offset) * interval, **_recognize(frame, binary, language)})
        path = work / ("batch-" + uuid.uuid4().hex + ".json")
        _write(path, {"frames": rows})
        frames.extend(rows)
        manifest["batches"].append({"file": path.name, "first": first, "sha256": _sha256(path)})
        manifest["next_frame"] = len(frames)
        _write(manifest_path, manifest)
    cues, static = _aggregate(frames, manifest["end"], interval)
    result = {"schema_version": 1, "status": "available" if cues else "no_subtitles",
              "source": {"kind": "ocr", "file": str(video.resolve()), "sha256": source_hash,
                         "language": language, "start": start, "end": manifest["end"],
                         "interval": interval, "region": config["region"], "engine": "tesseract",
                         "engine_version": manifest["engine_version"],
                         "scope": {"start": start, "end": manifest["end"], "duration_seconds": manifest["video"]["duration"]},
                         "confidence_scale": "0-100", "cache_key": key},
              "cues": cues, "possible_static_text": static,
              "warnings": ["画面文字仅为候选字幕，可能含标题或水印；请先检查区域与短片结果。",
                           "OCR 置信度不代表原音或说话人已核对；采样时间误差最多约一个采样间隔，短于该间隔的字幕可能漏检。"]}
    if not cues:
        result["warnings"].append("指定范围和区域内未发现可用的变化字幕；这不证明视频没有字幕，可调整区域或语言后重试。")
    if static:
        result["warnings"].append("持续至少 30 秒不变的文字已单列为 possible_static_text，可能是标题、水印或长时间停留的字幕，需人工检查。")
    _write(work / "subtitles.json", result)
    manifest["status"] = "complete"
    _write(manifest_path, manifest)
    return result
