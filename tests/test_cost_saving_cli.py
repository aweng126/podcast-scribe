"""Offline integration: source choice and restart must not repeat paid/agent work."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from podcast_scribe.cli import main
from podcast_scribe.editing_progress import progress_path
from podcast_scribe.model import ContentError, load_episode, new_episode, save_episode, write_json
from podcast_scribe.transcripts import normalize_segments


URL = "https://www.bilibili.com/video/BV1GZbT6UE7o"


@pytest.fixture
def source(tmp_path, monkeypatch):
    from podcast_scribe import sources, transcribe, subtitle_repair
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    document = {"schema_version": 1, "status": "available", "source": {"kind": "bilibili"},
                "selected_track": {"language": "ai-zh"}, "cues": [
                    {"id": f"c{i}", "start": i * 5, "end": (i + 1) * 5,
                     "text": f"第{i}段节目讨论具体内容，保留完整的表达。"} for i in range(24)]}
    state = {"document": document, "audio": 0, "asr": 0, "repairs": 0}
    metadata = {"id": "BV1GZbT6UE7o", "title": "测试来源", "duration_seconds": 120,
                "source": {"url": URL, "platform": "bilibili", "video_id": "BV1GZbT6UE7o"}}
    monkeypatch.setattr(sources, "inspect_source", lambda *a, **k: deepcopy(metadata))
    monkeypatch.setattr(sources, "fetch_subtitles", lambda *a, **k: deepcopy(state["document"]))

    def download(*args, **kwargs):
        state["audio"] += 1
        return tmp_path / "mock.mp3"

    def asr(*args, **kwargs):
        state["asr"] += 1
        kwargs["metadata"]["duration_seconds"] = 120
        return normalize_segments([{"start": 0, "end": 120, "text": "来自完整音频的正文。", "speaker": "A"}])

    def repair(doc, assessment, *args, **kwargs):
        state["repairs"] += 1
        state["ranges"] = assessment["repair_ranges"]
        segments, speakers = normalize_segments([{"start": 0, "end": 120, "text": "字幕及局部转录正文。", "speaker": None}])
        return segments, speakers, assessment["repair_ranges"]

    monkeypatch.setattr(sources, "fetch_audio", download)
    monkeypatch.setattr(transcribe, "transcribe_audio", asr)
    monkeypatch.setattr(subtitle_repair, "repair_subtitles", repair)
    return state


def test_subtitles_need_no_key_audio_or_self_comparison(source, capsys, monkeypatch):
    monkeypatch.setattr("podcast_scribe.subtitle_review.write_subtitle_report",
                        lambda *a, **k: pytest.fail("same-source comparison is not independent evidence"))
    assert main(["ingest", URL]) == 0
    captured = capsys.readouterr()
    episode = load_episode(Path(captured.out.strip()))
    assert source["audio"] == source["asr"] == 0
    assert episode["transcription"]["source"] == "subtitles"
    assert len(episode["segments"]) == 24
    assert all(row["speaker_id"] is None and row["review_status"] == "unreviewed" for row in episode["segments"])
    assert episode["review"]["content_checked"] is False
    assert '"audio_transcription": "skipped"' in captured.err
    assert main(["ingest", URL, "--transcript-source", "audio"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "existing" and resumed["transcript_source"] == "subtitles"
    assert resumed["requested_options"]["transcript_source"] == "audio"
    assert source["audio"] == source["asr"] == 0


@pytest.mark.parametrize("options", [["--transcript-source", "audio"], ["--review-mode", "precise"]])
def test_explicit_audio_and_precise_auto_keep_independent_transcription(source, capsys, options):
    assert main(["ingest", URL, *options]) == 0
    episode = load_episode(Path(capsys.readouterr().out.strip()))
    assert source["audio"] == source["asr"] == 1
    assert episode["transcription"]["source"] == "audio"


@pytest.mark.parametrize("change", ["missing", "gap", "wrong_language"])
def test_subtitles_only_never_silently_calls_audio_api(source, capsys, change):
    if change == "missing":
        source["document"].update(status="no_subtitles", cues=[])
    elif change == "gap":
        del source["document"]["cues"][10:13]
    else:
        source["document"]["selected_track"]["language"] = "en"
    assert main(["ingest", URL, "--transcript-source", "subtitles"]) == 2
    assert "未请求音频 API" in capsys.readouterr().err
    assert source["audio"] == source["asr"] == source["repairs"] == 0
    assert not Path("data/BV1GZbT6UE7o/episode.json").exists()


def test_auto_routes_a_small_gap_to_partial_asr_only(source, capsys):
    del source["document"]["cues"][10:13]
    assert main(["ingest", URL]) == 0
    episode = load_episode(Path(capsys.readouterr().out.strip()))
    assert source["audio"] == source["repairs"] == 1 and source["asr"] == 0
    assert [(r["start"], r["end"]) for r in source["ranges"]] == [(50, 65)]
    assert episode["transcription"]["source"] == "subtitles+audio"


def test_auto_missing_subtitles_falls_back_without_dropping_audio(source, capsys):
    source["document"].update(status="no_subtitles", cues=[])
    assert main(["ingest", URL]) == 0
    episode = load_episode(Path(capsys.readouterr().out.strip()))
    assert source["audio"] == source["asr"] == 1 and source["repairs"] == 0
    assert episode["transcription"]["source"] == "audio"


@pytest.fixture
def manuscript(tmp_path):
    segments, speakers = normalize_segments([
        {"start": i * 10, "end": (i + 1) * 10, "speaker": "A", "text": f"第{i}段需要整理的具体内容。"}
        for i in range(5)])
    episode = new_episode({"id": "resume", "title": "续接测试"}, segments, speakers,
                          series_id="tests", series_title="测试")
    path = tmp_path / "episode.json"
    save_episode(path, episode)
    return path


def batch_patch(path, capsys):
    batch = path.parent / "batch.json"
    assert main(["batch", str(path), "--output", str(batch)]) == 0
    view = json.loads(capsys.readouterr().out)
    target = view["targets"][0]["id"]
    patch = path.parent / "edits.json"
    write_json(patch, {"segments": [{"id": target, "review_status": "edited"}]})
    return batch, patch, target


def test_cli_resumes_only_actual_targets_and_preserves_review_semantics(manuscript, capsys):
    batch, patch, target = batch_patch(manuscript, capsys)
    assert main(["edit", str(manuscript), "--edits", str(patch), "--batch", str(batch),
                 "--note", "已处理首段；后面继续讨论投资。 "]) == 0
    capsys.readouterr()
    assert main(["status", str(manuscript)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["remaining"] == 5 and report["completion_status"] == "incomplete"
    assert report["editing_progress"]["remaining_to_edit"] == 4
    assert main(["batch", str(manuscript)]) == 0
    assert json.loads(capsys.readouterr().out)["targets"][0]["id"] != target
    for options in (["--include-reviewed"], ["--raw"]):
        assert main(["batch", str(manuscript), *options]) == 0
        assert json.loads(capsys.readouterr().out)["targets"][0]["id"] == target
    assert main(["editing-notes", str(manuscript)]) == 0
    notes = json.loads(capsys.readouterr().out)["notes"]
    assert target in notes[0]["segment_ids"] and not notes[0]["stale"]
    # Ordinary edits invalidate saved work, including when switching modes.
    write_json(patch, {"segments": [{"id": target, "text": "手动修订后的新内容。"}]})
    assert main(["edit", str(manuscript), "--edits", str(patch)]) == 0
    capsys.readouterr()
    assert main(["batch", str(manuscript)]) == 0
    assert json.loads(capsys.readouterr().out)["targets"][0]["id"] == target
    assert main(["editing-notes", str(manuscript)]) == 0
    assert json.loads(capsys.readouterr().out)["notes"][0]["stale"]


@pytest.mark.parametrize("failure", ["note_without_batch", "long_note", "corrupt_progress", "unknown_target", "duplicate_target"])
def test_invalid_progress_input_does_not_partially_save_the_manuscript(manuscript, capsys, failure):
    batch, patch, _ = batch_patch(manuscript, capsys)
    before = manuscript.read_bytes()
    options = ["--batch", str(batch)]
    if failure == "note_without_batch":
        options = ["--note", "不应落盘"]
    elif failure == "long_note":
        options += ["--note", "字" * 1201]
    elif failure == "corrupt_progress":
        progress_path(manuscript).write_text("broken")
    else:
        data = json.loads(batch.read_text())
        data["targets"].append({"id": "missing"} if failure == "unknown_target" else data["targets"][0])
        write_json(batch, data)
        options += ["--note", "不应部分保存"]
    assert main(["edit", str(manuscript), "--edits", str(patch), *options]) == 2
    assert capsys.readouterr().err and manuscript.read_bytes() == before
    assert not (manuscript.parent / "history").exists()
