"""Subtitle evidence never mutates a transcript, even on failure or resume."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from podcast_scribe.cli import main
from podcast_scribe.model import ContentError, load_episode, new_episode, save_episode
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    segments, speakers = normalize_segments([
        {"start": 0, "end": 2, "speaker": "A", "text": "我不同意。"},
        {"start": 2, "end": 4, "speaker": "B", "text": "投入十二万元。"},
    ])
    ep = new_episode({"id": "fictional-subtitle-test", "title": "自制字幕测试",
                      "source": {"platform": "bilibili", "url": "https://www.bilibili.com/video/BV1GZbT6UE7o"}},
                     segments, speakers, series_id="tests", series_title="自制测试")
    path = tmp_path / "episode.json"
    save_episode(path, ep)
    subtitles = tmp_path / "captions.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\n我同意。\n\n2\n00:00:02,000 --> 00:00:04,000\n投入十二万元。\n", encoding="utf-8")
    return path, subtitles


def test_local_subtitle_comparison_is_offline_readonly_and_keeps_versions(workspace, monkeypatch, capsys):
    from podcast_scribe import sources
    episode, subtitles = workspace
    before = episode.read_bytes()
    monkeypatch.setattr(sources, "_ydl", lambda *args, **kwargs: pytest.fail("no network"))
    monkeypatch.setattr("podcast_scribe.transcribe.transcribe_audio", lambda *args, **kwargs: pytest.fail("no paid ASR"))
    assert main(["check-subtitles", str(episode), "--file", str(subtitles)]) == 0
    result = json.loads(capsys.readouterr().out)
    report_path = Path(result["report"])
    report = json.loads(report_path.read_text())
    assert result["episode_changed"] is result["review_status_changed"] is False
    assert any(row["status"] == "text_difference" for row in report["groups"])
    assert Path(result["checklist"]).exists() and episode.read_bytes() == before
    original_report = report_path.read_bytes()
    assert main(["check-subtitles", str(episode), "--file", str(subtitles)]) == 0
    assert json.loads(capsys.readouterr().out)["report"] == str(report_path)
    changed = load_episode(episode)
    changed["segments"][0]["text"] = "我同意。"
    save_episode(episode, changed)
    assert main(["check-subtitles", str(episode), "--file", str(subtitles)]) == 0
    assert json.loads(capsys.readouterr().out)["report"] != str(report_path)
    assert report_path.read_bytes() == original_report


def test_difference_batch_bounds_and_stale_manuscript(workspace, capsys):
    episode, subtitles = workspace
    assert main(["check-subtitles", str(episode), "--file", str(subtitles)]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    assert main(["subtitle-batch", str(episode), "--report", report]) == 0
    batch = json.loads(capsys.readouterr().out)
    assert batch["groups"] and all(row["status"] != "match" for row in batch["groups"])
    assert main(["subtitle-batch", str(episode), "--report", report, "--max-chars", "1"]) == 2
    assert "字符" in capsys.readouterr().err
    changed = load_episode(episode)
    changed["segments"][0]["text"] = "另一版本。"
    save_episode(episode, changed)
    assert main(["subtitle-batch", str(episode), "--report", report]) == 2
    assert "不一致" in capsys.readouterr().err


@pytest.mark.parametrize("status,code", [("no_subtitles", 0), ("login_required", 2), ("unavailable", 2)])
def test_source_outcomes_never_claim_comparison_or_alter_review(workspace, monkeypatch, capsys, status, code):
    from podcast_scribe import sources
    episode, _ = workspace
    before = episode.read_bytes()
    monkeypatch.setattr(sources, "fetch_subtitles", lambda *args, **kwargs: {
        "schema_version": 1, "status": status, "source": {"kind": "bilibili"}, "cues": []})
    monkeypatch.setattr(sources, "fetch_video", lambda *args, **kwargs: pytest.fail("no implicit video download"))
    assert main(["check-subtitles", str(episode)]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and "report" not in result
    assert episode.read_bytes() == before


def test_ocr_prerequisites_checked_before_video_download(workspace, monkeypatch, capsys):
    from podcast_scribe import sources, subtitle_ocr
    episode, _ = workspace
    def missing(**kwargs):
        raise ContentError("测试：缺少 OCR 语言包")
    monkeypatch.setattr(subtitle_ocr, "check_dependencies", missing)
    monkeypatch.setattr(sources, "fetch_video", lambda *args, **kwargs: pytest.fail("download before dependency check"))
    assert main(["check-subtitles", str(episode), "--ocr", "--end", "10"]) == 2
    assert "语言包" in capsys.readouterr().err


def test_invalid_ocr_range_does_not_trigger_download(workspace, monkeypatch, capsys):
    from podcast_scribe import sources
    monkeypatch.setattr(sources, "fetch_video", lambda *args, **kwargs: pytest.fail("invalid range downloads video"))
    assert main(["check-subtitles", str(workspace[0]), "--ocr", "--end", "-1"]) == 2
    assert "起止时间" in capsys.readouterr().err


def test_invalid_report_object_has_actionable_error(workspace, capsys):
    report = Path("bad-report.json")
    report.write_text("[]")
    assert main(["subtitle-batch", str(workspace[0]), "--report", str(report)]) == 2
    assert "不一致" in capsys.readouterr().err


def test_local_video_ocr_scope_and_options_are_passed_without_mutating_episode(workspace, monkeypatch, capsys):
    from podcast_scribe import subtitle_ocr
    episode, _ = workspace
    before = episode.read_bytes()
    calls = []
    def extract(video, work, **kwargs):
        calls.append((video, work, kwargs))
        return {"schema_version": 1, "status": "available", "source": {"kind": "ocr", "scope": {"start": 0, "end": 2}},
                "cues": [{"id": "c1", "start": 0, "end": 2, "text": "我不同意。", "confidence": 90}]}
    monkeypatch.setattr(subtitle_ocr, "extract_subtitles", extract)
    assert main(["check-subtitles", str(episode), "--video", "local.mp4", "--end", "2", "--interval", "0.25"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "available" and Path(result["report"]).exists()
    assert calls[0][2]["end"] == 2 and calls[0][2]["interval"] == 0.25
    assert episode.read_bytes() == before


def test_difference_batch_paginates_every_conflict_once(workspace):
    from podcast_scribe.subtitle_review import read_difference_batch
    from podcast_scribe.subtitles import episode_digest
    episode = load_episode(workspace[0])
    groups = [{"status": "text_difference", "segment_ids": [f"s{i}"], "cue_ids": [f"c{i}"],
               "start": i, "end": i + 1, "transcript_text": "甲" * 50, "subtitle_text": "乙" * 50} for i in range(12)]
    report = {"kind": "subtitle_comparison", "episode_id": episode["id"], "episode_digest": episode_digest(episode), "groups": groups}
    original = deepcopy(report)
    after, visited = 0, []
    while True:
        result = read_difference_batch(episode, report, after=after, max_chars=950)
        assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n") <= 950
        visited.extend(row["index"] for row in result["groups"])
        if result["next_after"] is None:
            break
        after = result["next_after"]
    assert visited == list(range(1, 13)) and report == original
