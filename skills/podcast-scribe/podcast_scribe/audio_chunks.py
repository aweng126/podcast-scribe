"""Local audio preparation and continuous, silence-aware upload planning."""
from __future__ import annotations

import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .model import ContentError

MAX_UPLOAD_BYTES = 24_000_000
MAX_CHUNK_SECONDS = 15 * 60
BITRATE = 32_000
SILENCE_WINDOW_SECONDS = 30


def ffmpeg_binary() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise ContentError("需要 ffmpeg 或 imageio-ffmpeg 来提取音频") from exc


def _run(arguments: list[str], *, timeout: int = 3600):
    try:
        # FFmpeg otherwise returns success after skipping corrupt AAC packets,
        # allowing a partial recording to become the upload source.
        result = subprocess.run([ffmpeg_binary(), "-nostdin", "-hide_banner", "-xerror", *arguments],
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContentError("音频处理未完成；请检查 ffmpeg 和本地磁盘后重试") from exc
    if result.returncode:
        raise ContentError("音频处理失败；请确认文件可播放并含音轨")
    return result


def prepare_audio(source: Path, destination: Path) -> Path:
    """Encode the entire local recording once; upload limits apply to its chunks."""
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ContentError(f"音视频文件不存在：{source}")
    if source.resolve() == destination.resolve():
        raise ContentError("转换音频不能覆盖调用者的源文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=destination.stem + ".tmp-", suffix=".mp3",
                                     dir=destination.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        _run(["-loglevel", "error", "-y", "-i", str(source.resolve()), "-map", "0:a:0",
              "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k",
              str(temp.resolve())])
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def analyze_audio(audio: Path) -> dict:
    """Measure decoded duration and quiet intervals without requiring ffprobe."""
    result = _run(["-i", str(Path(audio).resolve()), "-map", "0:a:0", "-vn", "-af",
                   "silencedetect=noise=-35dB:d=0.4", "-nostats", "-progress", "pipe:1",
                   "-f", "null", "-"])
    clocks = re.findall(r"^out_time_us=(\d+)$", result.stdout, re.MULTILINE)
    duration = max((int(value) / 1_000_000 for value in clocks), default=0)
    if not math.isfinite(duration) or duration <= 0:
        raise ContentError("音频没有可解码的有效时长")
    starts, silences = [], []
    for match in re.finditer(r"silence_(start|end):\s*([\d.eE+-]+)", result.stderr):
        value = max(0.0, min(duration, float(match[2])))
        if match[1] == "start":
            starts.append(value)
        elif starts:
            start = starts.pop()
            if value > start:
                silences.append([start, value])
    if starts and starts[-1] < duration:
        silences.append([starts[-1], duration])
    return {"duration": duration, "silences": silences}


def plan_chunks(duration: float, silences: list, *, max_seconds: float = MAX_CHUNK_SECONDS) -> list[dict]:
    """Partition every source second exactly once, preferring a quiet final 30 s."""
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ContentError("分片时长必须是正有限数值")
    limit = min(max_seconds, MAX_CHUNK_SECONDS, (MAX_UPLOAD_BYTES - 65536) * 8 / BITRATE)
    if limit <= 0:
        raise ContentError("音频上传限制不足以创建分片")
    chunks, start = [], 0.0
    while start < duration:
        ceiling = min(duration, start + limit)
        boundary = "end" if ceiling == duration else "forced"
        end = ceiling
        if ceiling < duration:
            floor = max(start + limit / 2, ceiling - SILENCE_WINDOW_SECONDS)
            choices = []
            for quiet_start, quiet_end in silences:
                candidate = max(floor, min(ceiling, (quiet_start + quiet_end) / 2))
                if floor <= candidate <= ceiling and quiet_start <= candidate <= quiet_end:
                    choices.append(candidate)
            if choices:
                end, boundary = max(choices), "silence"
        chunks.append({"id": f"chunk-{len(chunks) + 1:04d}", "start": start,
                       "end": end, "boundary": boundary, "status": "pending"})
        start = end
    return chunks


def extract_audio(audio: Path, destination: Path, start: float, end: float, *, reference: bool = False) -> Path:
    """Accurate decode-and-trim, without MP3 stream-copy seek offsets."""
    audio, destination = Path(audio), Path(destination)
    if audio.resolve() == destination.resolve():
        raise ContentError("音频分片不能覆盖调用者的源文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=destination.stem + ".tmp-", suffix=destination.suffix,
                                     dir=destination.parent, delete=False) as stream:
        temp = Path(stream.name)
    codec = ["-c:a", "pcm_s16le"] if reference else ["-c:a", "libmp3lame", "-b:a", "32k"]
    try:
        _run(["-loglevel", "error", "-y", "-ss", f"{start:.6f}", "-i", str(audio.resolve()),
              "-t", f"{end - start:.6f}", "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
              *codec, str(temp.resolve())], timeout=600)
        if temp.stat().st_size > MAX_UPLOAD_BYTES:
            raise ContentError("单个音频分片超过 24 MB，已停止上传；请检查音频编码配置")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination
