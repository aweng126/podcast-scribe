"""Check local prerequisites without downloading files or contacting services."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


CAPABILITIES = ("import", "markdown", "site", "pdf", "transcribe", "ingest", "inspect", "subtitle-source", "ocr")
FFMPEG_TIMEOUT_SECONDS = 5


def _issue(code: str, message: str, remedy: str) -> dict:
    return {"code": code, "message": message, "remedy": remedy}


def _dependency(name: str, package: str) -> tuple[object | None, list[dict]]:
    try:
        return importlib.import_module(name), []
    except Exception:
        # Dependency imports may fail despite find_spec succeeding. Never echo
        # exception text: configuration values can occur in third-party errors.
        return None, [_issue(
            f"{name}_unavailable", f"无法导入 Python 依赖 {name}。",
            f"在运行本命令的 Python 环境中执行 python -m pip install --upgrade {package}。",
        )]


def _probe_ffmpeg(binary: str) -> list[dict]:
    remedy = "安装可执行的系统 ffmpeg，或重新安装 imageio-ffmpeg；检查程序权限与系统架构。"
    try:
        result = subprocess.run(
            [binary, "-version"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return [_issue("ffmpeg_timeout", "ffmpeg -version 检查超时。", remedy)]
    except (OSError, ValueError):
        return [_issue("ffmpeg_not_executable", "ffmpeg 无法执行。", remedy)]
    if result.returncode:
        return [_issue("ffmpeg_failed", "ffmpeg -version 返回失败状态。", remedy)]
    return []


def _ffmpeg() -> list[dict]:
    binary = shutil.which("ffmpeg")
    if binary:
        # Match the transcription adapter, which also prefers the system binary.
        return _probe_ffmpeg(binary)
    module, _ = _dependency("imageio_ffmpeg", "imageio-ffmpeg")
    if module is not None:
        explicit = os.environ.get("IMAGEIO_FFMPEG_EXE")
        if explicit:
            return _probe_ffmpeg(explicit)
        # Do not call get_ffmpeg_exe(): its automatic binary probes have no
        # timeout. Inspect its bundled binaries locally, then run bounded probes.
        package_file = getattr(module, "__file__", None)
        candidates = sorted((Path(package_file).parent / "binaries").glob("ffmpeg*")) if package_file else []
        candidates.append(Path(sys.prefix) / ("Library/bin/ffmpeg.exe" if sys.platform == "win32" else "bin/ffmpeg"))
        failures = []
        for candidate in candidates:
            if candidate.is_file():
                issues = _probe_ffmpeg(str(candidate))
                if not issues:
                    return []
                failures.extend(issues)
        if failures:
            return failures
    return [_issue(
        "ffmpeg_missing", "未找到系统 ffmpeg 或 imageio-ffmpeg 提供的可执行文件。",
        "安装系统 ffmpeg，或在当前 Python 环境执行 python -m pip install --upgrade imageio-ffmpeg。",
    )]


def _pdf() -> list[dict]:
    _, issues = _dependency("reportlab", "reportlab")
    if issues:
        return issues
    try:
        from .exporters import _pdf_font
        _pdf_font()
    except Exception:
        return [_issue(
            "chinese_font_unavailable", "未找到能够嵌入 PDF 且包含中文字符的有效字体。",
            "将 PODCAST_SCRIBE_FONT 设为可读取的中文 TrueType .ttf 或 TrueType .ttc 文件路径，再运行本检查。",
        )]
    return []


def _ocr() -> list[dict]:
    from .model import ContentError
    from .subtitle_ocr import check_tesseract

    issues = _ffmpeg()
    try:
        check_tesseract("chi_sim+eng")
    except ContentError as exc:
        issues.append(_issue("ocr_unavailable", str(exc),
                             "安装 Tesseract 及 chi_sim、eng 语言包；本地字幕文件对照不需要 OCR。"))
    except Exception:
        issues.append(_issue("ocr_unavailable", "无法检查本地 OCR 依赖。",
                             "检查 Tesseract 安装及语言包配置后重试。"))
    return issues


def check_environment(required=None) -> dict:
    """Report only requested capabilities; cloud readiness is local-only."""
    required = list(dict.fromkeys(CAPABILITIES if required is None else required))
    unknown = set(required) - set(CAPABILITIES)
    if unknown:
        raise ValueError("未知的环境检查能力")
    capabilities = {}
    transcribe_issues = None
    inspect_issues = None
    for capability in required:
        issues = []
        if capability == "pdf":
            issues = _pdf()
        if capability == "ocr":
            issues = _ocr()
        if capability in {"transcribe", "ingest"}:
            if transcribe_issues is None:
                _, transcribe_issues = _dependency("openai", "openai")
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    transcribe_issues.append(_issue(
                        "openai_api_key_missing", "未配置 OPENAI_API_KEY。",
                        "在当前运行环境设置 OPENAI_API_KEY；已有转写可使用 import，无需密钥。",
                    ))
                transcribe_issues.extend(_ffmpeg())
            issues = list(transcribe_issues)
        if capability in {"inspect", "ingest", "subtitle-source"}:
            if inspect_issues is None:
                _, inspect_issues = _dependency("yt_dlp", "yt-dlp")
            issues = issues + inspect_issues
        capabilities[capability] = {"ready": not issues, "issues": issues}
    return {
        "ready": all(item["ready"] for item in capabilities.values()),
        "required": required,
        "capabilities": capabilities,
        "network": "not tested",
        "cloud_transcription": "仅检查本地前置条件；未验证服务端、密钥有效性或模型权限，未调用 API。",
    }
