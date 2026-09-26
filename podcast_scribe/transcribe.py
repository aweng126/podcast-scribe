"""Optional OpenAI diarization adapter with content-addressed response caching."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .model import ContentError, write_json
from .transcripts import normalize_segments

MODEL = "gpt-4o-transcribe-diarize"
MAX_UPLOAD_BYTES = 24_000_000


def ffmpeg_binary() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise ContentError("需要 ffmpeg 或 imageio-ffmpeg 来提取音频") from exc


def prepare_audio(source: Path, destination: Path) -> Path:
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ContentError(f"音视频文件不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.stem + ".tmp.mp3")
    result = subprocess.run([ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                             "-i", str(source.resolve()), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                             "-c:a", "libmp3lame", "-b:a", "32k", str(temp.resolve())],
                            capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise ContentError("音频转换失败；请确认文件可播放并含音轨")
    if temp.stat().st_size > MAX_UPLOAD_BYTES:
        temp.unlink()
        raise ContentError("压缩后音频超过 24 MB。第一版不独立拆分说话人，请导入已有全片说话人转写 JSON，避免分块串人。")
    temp.replace(destination)
    return destination


def transcribe_audio(source: Path, cache_dir: Path, *, language: str = "zh") -> tuple[list[dict], list[dict]]:
    source = Path(source)
    if not source.is_file():
        raise ContentError(f"音视频文件不存在：{source}")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(f"{MODEL}:{language}:mono16k32kbps:v1".encode())
    work = Path(cache_dir) / digest.hexdigest()
    raw_path = work / "transcription.json"
    if raw_path.is_file():
        return normalize_segments(json.loads(raw_path.read_text(encoding="utf-8"))["segments"])
    if not os.environ.get("OPENAI_API_KEY"):
        raise ContentError("未配置 OPENAI_API_KEY。可在运行环境中配置，或用 import 命令导入已有转写；请勿将密钥写入文稿或代码。")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ContentError("缺少 openai Python SDK；请安装项目依赖") from exc
    audio_path = prepare_audio(source, work / "audio.mp3")
    try:
        # Keep one recording in one request; never concatenate unrelated speaker IDs.
        with OpenAI(max_retries=0, timeout=600) as client, audio_path.open("rb") as audio:
            response = client.audio.transcriptions.create(
                file=audio, model=MODEL, response_format="diarized_json",
                chunking_strategy="auto", language=language,
            )
        payload = response.model_dump()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        label = f"HTTP {status}" if status else type(exc).__name__
        raise ContentError(f"转写接口失败（{label}）；已保留转换音频。请检查网络、密钥与模型权限，再重试。") from exc
    normalized = normalize_segments(payload.get("segments", []))
    write_json(raw_path, payload)
    return normalized
