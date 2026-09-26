from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from podcast_scribe.cli import main
from podcast_scribe.model import ContentError, apply_edits, load_episode, new_episode, validate_episode
from podcast_scribe.sources import normalize_url
from podcast_scribe.transcripts import normalize_segments, read_transcript


def episode():
    segments, speakers = normalize_segments([
        {"start": 0, "end": 4, "speaker": 0, "text": "这个结论不一定正确，样本只有十二个。"},
        {"start": 3.5, "end": 8, "speaker": 1, "text": "我们还需要保留反例。"},
    ])
    return new_episode({"id": "test", "title": "测试对话"}, segments, speakers, series_id="tests", series_title="测试")


def editorial():
    return {"summary": ["两人讨论结论的限制。"],
            "chapters": [{"id": "ch1", "title": "样本与反例", "start": 0, "segment_id": "seg-00001"}],
            "review": {"speakers_confirmed": True, "content_checked": True}}


def test_edits_preserve_evidence_and_reset_publication():
    original = episode()
    reviewed = apply_edits(original, editorial())
    validate_episode(reviewed, for_publication=True)
    reviewed["status"] = "published"
    reviewed["artifacts"] = {"pdf": "old.pdf"}
    revised = apply_edits(reviewed, {"speakers": [{"id": "speaker-1", "name": "说话人甲"}],
                                    "segments": [{"id": "seg-00001", "text": "结论不一定正确，样本只有十二个。"}]})
    assert [s["raw_text"] for s in revised["segments"]] == [s["raw_text"] for s in original["segments"]]
    assert revised["segments"][0]["end"] == 4
    assert revised["status"] == "draft" and revised["artifacts"] == {}
    assert not any(revised["review"].values())
    assert original["speakers"][0]["name"] == "说话人 1"
    with pytest.raises(ContentError):
        apply_edits(original, {"segments": [{"id": "seg-00001", "raw_text": "改变证据"}]})
    with pytest.raises(ContentError):
        apply_edits(original, {"segments": [{"id": "seg-00001", "text": ""}]})


def test_publication_requires_review_and_rejects_demo():
    original = episode()
    with pytest.raises(ContentError):
        validate_episode(original, for_publication=True)
    reviewed = apply_edits(original, editorial())
    reviewed["is_demo"] = True
    with pytest.raises(ContentError):
        validate_episode(reviewed, for_publication=True)


@pytest.mark.parametrize("mutate", [
    lambda ep: ep["segments"][0].update(start=float("nan")),
    lambda ep: ep["segments"][0].update(speaker_id="missing"),
    lambda ep: ep["segments"][0].update(end=100),
    lambda ep: ep.update(chapters=[{"id": "c1", "title": "错位", "start": 2, "segment_id": "seg-00001"}]),
])
def test_invalid_timeline_or_person_is_rejected(mutate):
    ep = episode()
    mutate(ep)
    with pytest.raises(ContentError):
        validate_episode(ep)


def test_srt_unknown_people_can_be_assigned_without_guessing(tmp_path):
    path = tmp_path / "input.srt"
    path.write_text("1\n00:00:01,500 --> 00:00:03,000\n我们不应该改变否定词。\n", encoding="utf-8")
    segments, speakers = read_transcript(path)
    assert segments[0]["start"] == 1.5 and segments[0]["speaker_id"] is None and speakers == []
    ep = new_episode({"id": "srt", "title": "字幕"}, segments, speakers, series_id="s", series_title="测试")
    changed = apply_edits(ep, {"speakers": [{"id": "person-a", "name": "匿名说话人 A"}],
                               "segments": [{"id": "seg-00001", "speaker_id": "person-a"}]})
    assert changed["segments"][0]["raw_text"] == segments[0]["raw_text"]
    assert changed["segments"][0]["speaker_id"] == "person-a"


@pytest.mark.parametrize("field", ["text", "content"])
@pytest.mark.parametrize("value", [None, 123, False, [], {}])
def test_cli_rejects_non_string_transcript_without_partial_output(tmp_path, capsys, field, value):
    source, output = tmp_path / "source.json", tmp_path / "episode.json"
    rows = [{"start": 0, "end": 1, field: "有效正文"},
            {"start": 1, "end": 2, field: value}]
    source.write_text(json.dumps({"body" if field == "content" else "segments": rows}), encoding="utf-8")
    assert main(["import", str(source), "--id", "invalid", "--title", "输入校验", "--output", str(output)]) == 2
    assert "第 2 段转写文本必须是字符串" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("field", ["text", "content"])
def test_transcript_import_preserves_strings_and_skips_blank_rows(tmp_path, field):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([
        {"start": 0, "end": 1},
        {"start": 1, "end": 2, field: " \n "},
        {"start": 2, "end": 3, field: " 007 "},
        {"start": 3, "end": 4, field: "None"},
    ]), encoding="utf-8")
    segments, speakers = read_transcript(source)
    assert [s["text"] for s in segments] == ["007", "None"]
    assert [s["raw_text"] for s in segments] == ["007", "None"]
    assert [s["id"] for s in segments] == ["seg-00001", "seg-00002"]
    assert speakers == []


def test_transcript_text_field_takes_precedence_over_content():
    with pytest.raises(ContentError, match="文本必须是字符串"):
        normalize_segments([{"start": 0, "end": 1, "text": None, "content": "备用文字"}])
    segments, _ = normalize_segments([{"start": 0, "end": 1, "text": "正文", "content": None}])
    assert segments[0]["text"] == "正文"


def test_cli_refuses_reimport_over_edits_and_stores_history(tmp_path, capsys):
    source, output, edits = tmp_path / "source.json", tmp_path / "episode.json", tmp_path / "edits.json"
    source.write_text(json.dumps({"segments": [{"start": 0, "end": 2, "text": "原始文字", "speaker": "A"}]}))
    argv = ["import", str(source), "--id", "single", "--title", "单集", "--output", str(output)]
    assert main(argv) == 0
    edits.write_text(json.dumps({"segments": [{"id": "seg-00001", "text": "人工修改"}]}))
    assert main(["edit", str(output), "--edits", str(edits)]) == 0
    assert main(argv) == 2
    assert load_episode(output)["segments"][0]["text"] == "人工修改"
    assert load_episode(tmp_path / "history/single-r1.json")["segments"][0]["text"] == "原始文字"


def test_single_video_input_only():
    assert normalize_url("BV1GZbT6UE7o").endswith("/BV1GZbT6UE7o")
    assert "p=2" in normalize_url("https://www.bilibili.com/video/BV1GZbT6UE7o?p=2")
    for url in ("https://example.org/video", "https://www.bilibili.com/", "file:///tmp/a"):
        with pytest.raises(ContentError):
            normalize_url(url)


def test_cloud_adapter_requests_speaker_segments_and_reuses_success_cache(tmp_path, monkeypatch):
    import openai
    import podcast_scribe.transcribe as module
    source = tmp_path / "test.mp3"
    source.write_bytes(b"mock audio bytes, not a recording")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda: {"segments": [{"start": 0, "end": 1, "speaker": "A", "text": "仅测试接口契约"}]})
    class Client:
        def __init__(self, **kwargs):
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(module, "prepare_audio", lambda source, destination: source)
    first = module.transcribe_audio(source, tmp_path / "cache")
    second = module.transcribe_audio(source, tmp_path / "cache")
    assert first == second and len(calls) == 1
    assert calls[0]["response_format"] == "diarized_json"
    assert calls[0]["chunking_strategy"] == "auto"
    assert first[0][0]["speaker_id"] == first[1][0]["id"]


def test_ffmpeg_can_prepare_local_audio(tmp_path):
    from podcast_scribe.transcribe import prepare_audio
    source = tmp_path / "silence.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 8000)
    output = prepare_audio(source, tmp_path / "output/audio.mp3")
    assert output.stat().st_size > 100
