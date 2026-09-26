#!/usr/bin/env bash
# Keep the caller's working directory: all content belongs to the task workspace.
set -euo pipefail

ps_skill_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ps_probe='import importlib.metadata, importlib.util, re, shutil, sys
if sys.version_info < (3, 10):
    raise SystemExit(1)
def available(module, package, minimum):
    try:
        version = importlib.metadata.version(package)
        release = re.match(r"\d+(?:\.\d+)*", version)
        parts = tuple(int(part) for part in release.group().split(".")) if release else ()
        return importlib.util.find_spec(module) is not None and parts + (0,) * 4 >= minimum
    except (ImportError, ValueError):
        return False
print(sum(available(*item) for item in (("openai", "openai", (2, 15)), ("yt_dlp", "yt-dlp", (2026, 8, 19)), ("reportlab", "reportlab", (4, 4)))) + int(bool(shutil.which("ffmpeg") or available("imageio_ffmpeg", "imageio-ffmpeg", (0, 6)))))'

ps_python=""
ps_score=-1
ps_candidates=()
if [[ -n "${PODCAST_SCRIBE_PYTHON:-}" ]]; then
    # An explicit override is one executable, never a shell command to evaluate.
    ps_override="$(command -v -- "$PODCAST_SCRIBE_PYTHON" || true)"
    if [[ -z "$ps_override" ]] || ! ps_score="$("$ps_override" -c "$ps_probe" 2>/dev/null)"; then
        printf '%s\n' 'PODCAST_SCRIBE_PYTHON 必须指向可用的 Python 3.10+ 解释器。' >&2
        exit 2
    fi
    ps_python="$ps_override"
else
    ps_candidates+=("$ps_skill_root/.venv/bin/python")
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        ps_candidates+=("$VIRTUAL_ENV/bin/python")
    fi
    ps_candidates+=("$PWD/.venv/bin/python")
    # Source checkouts keep their development environment at the repository root.
    if [[ -f "$ps_skill_root/../../scripts/build_public_site.py" ]]; then
        ps_candidates+=("$ps_skill_root/../../.venv/bin/python")
    fi
    for ps_name in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
        ps_found="$(command -v -- "$ps_name" || true)"
        if [[ -n "$ps_found" ]]; then ps_candidates+=("$ps_found"); fi
    done
    for ps_candidate in "${ps_candidates[@]}"; do
        [[ -x "$ps_candidate" ]] || continue
        if ! ps_candidate_score="$("$ps_candidate" -c "$ps_probe" 2>/dev/null)"; then continue; fi
        [[ "$ps_candidate_score" =~ ^[0-4]$ ]] || continue
        if (( ps_candidate_score > ps_score )); then
            ps_python="$ps_candidate"
            ps_score="$ps_candidate_score"
        fi
        if (( ps_score == 4 )); then break; fi
    done
fi
if [[ -z "$ps_python" ]]; then
    printf '%s\n' '未找到 Python 3.10+；请先安装 Python 后重试。' >&2
    exit 2
fi

if [[ "${1:-}" == setup ]]; then
    if (( $# != 1 )); then
        printf '%s\n' '用法：bash scripts/run.sh setup' >&2
        exit 2
    fi
    if [[ "$ps_score" == 4 ]] && "$ps_python" -c \
        'import openai, yt_dlp, reportlab, shutil
if not shutil.which("ffmpeg"):
    import imageio_ffmpeg' >/dev/null 2>&1; then
        printf '已复用具备所需 Python 依赖的环境：%s\n' "$ps_python"
        exit 0
    fi
    if [[ -n "${PODCAST_SCRIBE_PYTHON:-}" ]]; then
        ps_setup_python="$ps_python"
    else
        ps_environment="$ps_skill_root/.venv"
        if [[ ! -e "$ps_environment" && ! -L "$ps_environment" ]]; then
            "$ps_python" -m venv "$ps_environment"
        fi
        ps_setup_python="$ps_environment/bin/python"
    fi
    if [[ ! -x "$ps_setup_python" ]] || ! "$ps_setup_python" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) and sys.prefix != sys.base_prefix else 1)'; then
        printf '%s\n' '目标不是可用的 Python 3.10+ 虚拟环境；请修复 Skill 的 .venv，或取消 PODCAST_SCRIBE_PYTHON 后重试。未向全局 Python 安装依赖。' >&2
        exit 2
    fi
    "$ps_setup_python" -m pip install -e "$ps_skill_root"
    printf 'Python 依赖已安装：%s\n' "$ps_setup_python"
    exit 0
fi

exec "$ps_python" "$ps_skill_root/scripts/podcast_scribe.py" "$@"
