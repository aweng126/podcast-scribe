"""Local OCR evidence tests; synthetic fixtures never represent an actual episode."""
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from podcast_scribe import subtitle_ocr as module
from podcast_scribe.model import ContentError


TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def tsv(words):
    return TSV_HEADER + "".join(f"5\t1\t1\t1\t1\t{i}\t0\t0\t10\t10\t{score}\t{text}\n"
                                for i, (text, score) in enumerate(words, 1))


@pytest.fixture
def local_pipeline(monkeypatch, tmp_path):
    video = tmp_path / "synthetic-only.mp4"
    video.write_bytes(b"synthetic video fixture, no API or download")
    state = {"video": video, "work": tmp_path / "cache", "duration": 2.5,
             "extracts": [], "recognized": [], "results": ["你好", "你好", "", "数字 10", "数字 11"]}
    monkeypatch.setattr(module, "check_tesseract", lambda language: "tesseract-fixture")
    monkeypatch.setattr(module, "_engine_version", lambda binary: "tesseract synthetic-fixture")
    monkeypatch.setattr(module, "_video_info", lambda video: {"duration": state["duration"], "width": 640, "height": 360})

    def frames(video, directory, start, end, interval, region, count):
        state["extracts"].append((start, end, count))
        result = []
        for i in range(count):
            path = directory / f"frame-{i:06d}.png"
            path.write_bytes(b"synthetic frame")
            result.append(path)
        return result

    def recognize(frame, binary, language):
        state["recognized"].append(frame)
        value = state["results"].pop(0)
        if isinstance(value, Exception):
            raise value
        return {"text": value, "confidence": 90.0 if value else 0.0}

    monkeypatch.setattr(module, "_extract_frames", frames)
    monkeypatch.setattr(module, "_recognize", recognize)
    return state


def test_exact_dedupe_preserves_changes_and_blanks(local_pipeline):
    state = local_pipeline
    result = module.extract_subtitles(state["video"], state["work"])
    assert result["status"] == "available" and result["source"]["kind"] == "ocr"
    assert [(cue["start"], cue["end"], cue["text"]) for cue in result["cues"]] == [
        (0, 1, "你好"), (1.5, 2, "数字 10"), (2, 2.5, "数字 11")]
    assert result["cues"][0]["samples"] == 2
    assert all("speaker_id" not in cue and "review_status" not in cue for cue in result["cues"])
    assert not list(state["work"].rglob("*.png"))


def test_complete_cache_reuses_without_ocr_dependency(local_pipeline, monkeypatch):
    state = local_pipeline
    first = module.extract_subtitles(state["video"], state["work"])
    monkeypatch.setattr(module, "check_tesseract", lambda language: pytest.fail("completed cache needs no OCR"))
    second = module.extract_subtitles(state["video"], state["work"])
    assert first == second and len(state["extracts"]) == 1


def test_failure_resumes_only_completed_batches_and_cleans_frames(local_pipeline, monkeypatch):
    state = local_pipeline
    monkeypatch.setattr(module, "BATCH_FRAMES", 2)
    state["results"] = ["第一个", "第一个", ContentError("synthetic failure")]
    with pytest.raises(ContentError, match="synthetic failure"):
        module.extract_subtitles(state["video"], state["work"])
    manifest = json.loads(next(state["work"].rglob("manifest.json")).read_text())
    assert manifest["next_frame"] == 2 and manifest["status"] == "in_progress"
    assert not list(state["work"].rglob("*.png"))
    assert not list(state["work"].rglob("subtitles.json"))
    state["results"] = ["第二个", "第二个", "第三个"]
    result = module.extract_subtitles(state["video"], state["work"])
    assert [cue["text"] for cue in result["cues"]] == ["第一个", "第二个", "第三个"]
    assert [row[0] for row in state["extracts"]] == [0, 1, 1, 2]


def test_input_and_parameter_hashes_prevent_wrong_cache(local_pipeline):
    state = local_pipeline
    first = module.extract_subtitles(state["video"], state["work"])
    state["video"].write_bytes(b"different synthetic video")
    state["results"] = ["新字幕"] * 5
    second = module.extract_subtitles(state["video"], state["work"])
    assert first["source"]["cache_key"] != second["source"]["cache_key"]
    state["results"] = ["不同区域"] * 5
    third = module.extract_subtitles(state["video"], state["work"], region=(0, 0.8, 1, 0.2))
    assert second["source"]["cache_key"] != third["source"]["cache_key"]


def test_bounded_range_reports_source_time_and_respects_end(local_pipeline):
    state = local_pipeline
    state["results"] = ["范围字幕"] * 3
    result = module.extract_subtitles(state["video"], state["work"], start=1.1, end=2.3)
    assert result["cues"][0]["start"] == 1.1 and result["cues"][0]["end"] == 2.3
    assert state["extracts"] == [(1.1, 2.3, 3)]


def test_empty_success_is_explicit_and_static_overlays_not_called_subtitles(local_pipeline):
    state = local_pipeline
    state["results"] = [""] * 5
    result = module.extract_subtitles(state["video"], state["work"])
    assert result["status"] == "no_subtitles" and result["cues"] == []
    state["duration"] = 31
    state["results"] = ["Persistent watermark"] * 62
    result = module.extract_subtitles(state["video"], state["work"], refresh=True)
    assert result["status"] == "no_subtitles" and result["possible_static_text"][0]["end"] == 31


def test_modified_cache_never_silently_used(local_pipeline):
    state = local_pipeline
    module.extract_subtitles(state["video"], state["work"])
    cached = next(state["work"].rglob("batch-*.json"))
    cached.write_text('{"frames": []}')
    with pytest.raises(ContentError, match="缓存"):
        module.extract_subtitles(state["video"], state["work"])


def test_parallel_workers_reject_same_cache_without_overwriting(local_pipeline):
    state = local_pipeline
    module.extract_subtitles(state["video"], state["work"])
    manifest = next(state["work"].rglob("manifest.json"))
    before = manifest.read_bytes()
    with module.destination_lock(manifest):
        with pytest.raises(ContentError, match="正在处理"):
            module.extract_subtitles(state["video"], state["work"], refresh=True)
    assert before == manifest.read_bytes()


def test_static_line_is_separated_from_changing_subtitles():
    frames = [{"time": i * .5, "text": "WATERMARK\n" + ("first subtitle" if i < 30 else "second subtitle"),
               "confidence": 90, "low_confidence": i == 3} for i in range(60)]
    cues, static = module._aggregate(frames, 30, .5)
    assert [cue["text"] for cue in cues] == ["first subtitle", "second subtitle"]
    assert cues[0]["low_confidence"] and not cues[1]["low_confidence"]
    assert static[0]["text"] == "WATERMARK" and static[0]["end"] == 30


@pytest.mark.parametrize("kwargs", [{"interval": 0}, {"interval": float("nan")}, {"start": -1},
    {"end": 0}, {"end": float("inf")}, {"region": (0, 0, 2, 1)}, {"region": (0, 0, 0, 1)},
    {"region": (0, 0, 1)}, {"language": "eng;sh"}])
def test_invalid_parameters_fail_before_processing(local_pipeline, kwargs):
    with pytest.raises(ContentError):
        module.extract_subtitles(local_pipeline["video"], local_pipeline["work"], **kwargs)
    assert not local_pipeline["extracts"]


def test_language_preflight_does_not_silently_use_english(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/local/tesseract")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout='List of available languages (1):\neng\n', stderr=""))
    with pytest.raises(ContentError, match="chi_sim"):
        module.check_dependencies()
    assert module.check_tesseract("eng") == "/local/tesseract"


def test_tsv_retains_low_confidence_negation_and_flags_whole_cue():
    result = module._parse_tsv(tsv([("这", 95), ("不", 5), ("是", 95), ("10", 95)]))
    assert result["text"] == "这不是 10" and result["confidence"] < 95 and result["low_confidence"]
    poor = module._parse_tsv(tsv([("garbage", 2)]))
    assert poor["text"] == "garbage" and poor["low_confidence"]
    with pytest.raises(ContentError, match="TSV"):
        module._parse_tsv("error instead of TSV")


def test_per_frame_timeout_is_failure(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == module.OCR_TIMEOUT
        raise subprocess.TimeoutExpired("tesseract", 30)
    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(ContentError, match="超时"):
        module._recognize(tmp_path / "frame.png", "tesseract", "eng")


def test_recognize_selects_tsv_renderer_without_optional_config_file(monkeypatch, tmp_path):
    def recognize(command, **kwargs):
        assert "tsv" not in command
        assert "tessedit_create_tsv=1" in command and "tessedit_create_txt=0" in command
        return SimpleNamespace(returncode=0, stdout=tsv([("SYNTHETIC", 95)]), stderr="")
    monkeypatch.setattr(module.subprocess, "run", recognize)
    assert module._recognize(tmp_path / "synthetic.png", "tesseract", "eng")["text"] == "SYNTHETIC"


@pytest.mark.parametrize("models_only", [False, True])
def test_real_local_synthetic_english_smoke(tmp_path, monkeypatch, models_only):
    """Exercise installed tools with generated English captions, never user media."""
    if not shutil.which("tesseract"):
        pytest.skip("optional Tesseract not installed")
    try:
        dependencies = module.check_dependencies(language="eng")
    except ContentError as exc:
        pytest.skip(str(exc))
    if models_only:
        available = subprocess.run([dependencies["tesseract"], "--list-langs"],
                                   capture_output=True, text=True, timeout=15)
        directory = re.search(r'List of available languages in "([^"]+)"', available.stdout + available.stderr)
        if not directory or not (Path(directory[1]) / "eng.traineddata").is_file():
            pytest.skip("cannot locate installed English model for isolated tessdata fixture")
        isolated = tmp_path / "models-only-tessdata"
        isolated.mkdir()
        shutil.copyfile(Path(directory[1]) / "eng.traineddata", isolated / "eng.traineddata")
        assert not (isolated / "configs").exists()
        monkeypatch.setenv("TESSDATA_PREFIX", str(isolated))
    image = pytest.importorskip("PIL.Image")
    draw = pytest.importorskip("PIL.ImageDraw")
    font = pytest.importorskip("PIL.ImageFont")
    canvas = image.new("RGB", (640, 360), "black")
    draw.Draw(canvas).text((75, 275), "SYNTHETIC TEST CAPTION", font=font.load_default(size=28), fill="white")
    png = tmp_path / "synthetic-caption.png"
    canvas.save(png)
    video = tmp_path / "synthetic-only.mp4"
    result = subprocess.run([module.ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error", "-loop", "1",
        "-i", str(png), "-t", "2.2", "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = module.extract_subtitles(video, tmp_path / "ocr-cache", language="eng")
    assert report["status"] == "available"
    assert "SYNTHETIC TEST CAPTION" in " ".join(cue["text"] for cue in report["cues"])
    assert report["cues"][-1]["end"] == 2.2
    assert not list((tmp_path / "ocr-cache").rglob("*.png"))
