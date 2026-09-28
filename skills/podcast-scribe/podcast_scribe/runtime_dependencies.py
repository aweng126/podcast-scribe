"""Shared, standard-library-only runtime checks, usable before installation."""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
import shutil


# Keep installation requirements in pyproject.toml aligned with this table.
DEPENDENCIES = {
    "openai": ("openai", "2.15"),
    "yt_dlp": ("yt-dlp", "2026.8.19"),
    "reportlab": ("reportlab", "4.4"),
    "imageio_ffmpeg": ("imageio-ffmpeg", "0.6"),
}

_VERSION = re.compile(
    r"v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
    r"(?P<pre>[-_.]?(?:alpha|beta|preview|pre|rc|a|b|c)[-_.]?\d*)?"
    r"(?P<post>(?:-\d+|[-_.]?(?:post|rev|r)[-_.]?\d*))?"
    r"(?P<dev>[-_.]?dev[-_.]?\d*)?"
    r"(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?",
    re.IGNORECASE,
)


def meets_minimum(version: str, minimum: str) -> bool:
    """Compare a PEP 440 version against our stable minimum without packaging."""
    match = _VERSION.fullmatch(version) if isinstance(version, str) and len(version) <= 128 else None
    if match is None:
        raise ValueError("invalid package version")
    if int(match["epoch"] or 0):
        return True
    release = tuple(int(part) for part in match["release"].split("."))
    floor = tuple(int(part) for part in minimum.split("."))
    length = max(len(release), len(floor))
    release += (0,) * (length - len(release))
    floor += (0,) * (length - len(floor))
    if release != floor:
        return release > floor
    # Pre-releases and development releases preceding the minimum are too old.
    return not match["pre"] and not (match["dev"] and not match["post"])


def check_dependency(name: str, *, load: bool = True) -> tuple[object | None, dict]:
    package, minimum = DEPENDENCIES[name]
    details = {"package": package, "minimum": minimum, "installed": None, "status": "unavailable"}
    try:
        module = importlib.import_module(name) if load else importlib.util.find_spec(name)
        if module is None:
            return None, details
    except Exception:
        # Third-party errors may contain configuration or credentials.
        return None, details
    try:
        version = importlib.metadata.version(package)
    except Exception:
        details["status"] = "version_missing"
        return None, details
    try:
        compatible = meets_minimum(version, minimum)
    except ValueError:
        details["status"] = "version_invalid"
        return None, details
    details.update(installed=version, status="ready" if compatible else "too_old")
    return (module if compatible else None), details


def environment_score(*, load: bool = False) -> int:
    """Score available dependencies cheaply; setup also checks actual imports."""
    score = sum(check_dependency(name, load=load)[1]["status"] == "ready"
                for name in ("openai", "yt_dlp", "reportlab"))
    return score + int(bool(shutil.which("ffmpeg")) or check_dependency("imageio_ffmpeg", load=load)[1]["status"] == "ready")
