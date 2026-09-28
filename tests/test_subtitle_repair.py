"""Bounded subtitle repair must preserve evidence and avoid redundant extraction."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import math
from threading import Event
from unittest.mock import Mock

import pytest

from podcast_scribe.model import ContentError
from podcast_scribe.subtitle_ingest import assess_subtitles
from podcast_scribe import subtitle_repair as repair


def document(cues):
    return {
        "schema_version": 1, "status": "available",
        "source": {"kind": "bilibili", "video_id": "offline-test"},
        "selected_track": {"language": "ai-zh", "origin": "automatic"},
        "cues": [
            {"id": name, "start": start, "end": end, "text": text}
            for name, start, end, text in cues
        ],
    }


def assessment(*ranges):
    return {"repairable": True, "repair_ranges": [
        {"start": start, "end": end} for start, end in ranges
    ]}


def response(start=1.0, end=9.0, text="补齐后的原话", speaker="speaker-1"):
    return {"start": start, "end": end, "raw_text": text, "speaker_id": speaker}


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """Fake byte fixtures only; every media/API boundary is replaced."""
    audio = tmp_path / "source.fake"
    audio.write_bytes(b"complete source identity")
    work, cache = tmp_path / "work", tmp_path / "asr-cache"
    analyze = Mock(return_value={"duration": 100.0})

    def extract(source, target, start, end):
        assert source == audio
        target.write_text(json.dumps({"start": start, "end": end}), encoding="utf-8")

    extract_mock = Mock(side_effect=extract)

    def transcribe(clip, asr_cache, *, language, metadata, allow_empty):
        assert asr_cache == cache
        assert language == "zh" and allow_empty is True
        bounds = json.loads(clip.read_text(encoding="utf-8"))
        metadata.update(duration_seconds=bounds["end"] - bounds["start"],
                        transcription_cache=f"offline/{clip.stem}")
        return [response(end=bounds["end"] - bounds["start"] - 1)], []

    transcribe_mock = Mock(side_effect=transcribe)
    monkeypatch.setattr(repair, "analyze_audio", analyze)
    monkeypatch.setattr(repair, "extract_audio", extract_mock)
    monkeypatch.setattr(repair, "transcribe_audio", transcribe_mock)

    class Harness:
        def run(self, source, selected, **kwargs):
            return repair.repair_subtitles(source, selected, audio, work, cache,
                                           duration_seconds=100.0, **kwargs)

    harness = Harness()
    harness.audio, harness.work, harness.cache = audio, work, cache
    harness.analyze, harness.extract, harness.transcribe = analyze, extract_mock, transcribe_mock
    harness.default_transcribe = transcribe
    return harness


def test_only_requested_ranges_are_extracted_and_global_cues_are_preserved(offline):
    source = document([
        ("before", 0, 10, "修补前的字幕"),
        ("uncertain", 20, 30, "需要替换的字幕"),
        ("after", 30, 100, "修补后的字幕"),
    ])
    selected = assessment((10, 20), (20, 30))
    originals = deepcopy((source, selected))
    segments, speakers, repairs = offline.run(source, selected)

    assert [(call.args[2], call.args[3]) for call in offline.extract.call_args_list] == [(10, 20), (20, 30)]
    assert [(s["start"], s["end"], s["text"]) for s in segments] == [
        (0, 10, "修补前的字幕"), (11, 19, "补齐后的原话"),
        (21, 29, "补齐后的原话"), (30, 100, "修补后的字幕"),
    ]
    assert segments[0]["speaker_id"] is None and segments[-1]["speaker_id"] is None
    assert all(s["review_status"] == "unreviewed" for s in segments)
    assert repairs[0]["replaced_cue_ids"] == []
    assert repairs[1]["replaced_cue_ids"] == ["uncertain"]
    assert all(r["transcription_cache"].startswith("offline/") for r in repairs)
    assert (source, selected) == originals


def test_anonymous_speaker_reuse_is_local_to_each_independent_clip(offline):
    source = document([("left", 0, 10, "原字幕甲"), ("right", 30, 100, "原字幕乙")])

    def two_turns(*args, **kwargs):
        offline.default_transcribe(*args, **kwargs)
        return [response(0, 3, "同一片段第一句"), response(4, 8, "同一片段第二句")], []

    offline.transcribe.side_effect = two_turns
    segments, speakers, _ = offline.run(source, assessment((10, 20), (20, 30)))
    restored = [s for s in segments if s["speaker_id"] is not None]
    assert restored[0]["speaker_id"] == restored[1]["speaker_id"]
    assert restored[2]["speaker_id"] == restored[3]["speaker_id"]
    assert restored[0]["speaker_id"] != restored[2]["speaker_id"]
    assert len(speakers) == 2


@pytest.mark.parametrize("replace_existing", [False, True])
def test_empty_asr_is_valid_for_silence_but_cannot_erase_existing_cues(offline, replace_existing):
    rows = [("left", 0, 10, "前文"), ("right", 20, 100, "后文")]
    if replace_existing:
        rows.insert(1, ("existing", 10, 20, "必须保留的证据"))
    source = document(rows)
    original = deepcopy(source)

    def silence(*args, **kwargs):
        offline.default_transcribe(*args, **kwargs)
        return [], []

    offline.transcribe.side_effect = silence
    if replace_existing:
        with pytest.raises(ContentError, match="没有有效文字"):
            offline.run(source, assessment((10, 20)))
    else:
        segments, people, repairs = offline.run(source, assessment((10, 20)))
        assert [(s["start"], s["end"], s["text"]) for s in segments] == [(0, 10, "前文"), (20, 100, "后文")]
        assert people == [] and repairs[0]["segments"] == 0
    assert source == original
    assert offline.transcribe.call_args.kwargs["allow_empty"] is True


@pytest.mark.parametrize("failure", ["truncated", "decode_failure"])
def test_complete_source_is_checked_before_extraction_or_any_asr(offline, failure):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])
    if failure == "truncated":
        offline.analyze.return_value = {"duration": 97.0}
    else:
        offline.analyze.side_effect = ContentError("源音频解码失败")
    with pytest.raises(ContentError):
        offline.run(source, assessment((10, 20)))
    offline.analyze.assert_called_once_with(offline.audio)
    offline.extract.assert_not_called()
    offline.transcribe.assert_not_called()


def test_assessment_expands_repair_to_whole_overlapping_cues(offline):
    source = document([(f"cue-{i}", i * 5, i * 5 + 4.8,
                        f"第{i}段介绍团队如何处理数据以及为什么作出这个决定。") for i in range(20)])
    source["cues"][5]["low_confidence"] = True
    source["cues"][6]["start"] = 29
    source["cues"][7]["start"] = 34
    selected = assess_subtitles(source, 100)
    assert selected["repairable"]
    segments, _, repairs = offline.run(source, selected)
    assert offline.extract.call_args.args[2:] == (25.0, 39.8)
    assert repairs[0]["replaced_cue_ids"] == ["cue-5", "cue-6", "cue-7"]
    remaining = {s["text"] for s in segments}
    assert not any(source["cues"][i]["text"] in remaining for i in (5, 6, 7))
    assert all(c["text"] in remaining for i, c in enumerate(source["cues"]) if i not in (5, 6, 7))
    assert len(segments) == 18


@pytest.mark.parametrize("bounds", [(10, 15), (8, 12), (9, 18)])
def test_handcrafted_partial_cue_repair_cannot_drop_its_untranscribed_edges(offline, bounds):
    source = document([("wide", 8, 18, "这一整句字幕的两端都不能被悄悄删除"),
                       ("after", 20, 100, "后文")])
    original = deepcopy(source)
    with pytest.raises(ContentError):
        offline.run(source, assessment(bounds))
    offline.extract.assert_not_called()
    offline.transcribe.assert_not_called()
    assert source == original


def test_repeated_repair_reuses_identical_clip_and_same_asr_cache_identity(offline):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])
    selected = assessment((10, 20))
    first = offline.run(source, selected)
    second = offline.run(source, selected)
    assert second == first
    offline.extract.assert_called_once()
    calls = offline.transcribe.call_args_list
    assert len(calls) == 2
    # transcribe_audio owns response reuse; repair must feed it the same bytes
    # and cache root rather than extracting a fresh, differently encoded clip.
    assert calls[0].args == calls[1].args
    assert calls[0].args[1] == offline.cache


def test_concurrent_repair_refuses_second_writer_before_shared_clip_or_asr(offline):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])
    selected = assessment((10, 20))
    entered, release = Event(), Event()
    original_extract = offline.extract.side_effect

    def blocked_extract(*args):
        entered.set()
        assert release.wait(timeout=10), "first extraction was not released"
        return original_extract(*args)

    offline.extract.side_effect = blocked_extract
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(offline.run, source, selected)
        try:
            assert entered.wait(timeout=5), "first task never reached extraction"
            second = pool.submit(offline.run, source, selected)
            with pytest.raises(ContentError, match="正在处理"):
                second.result(timeout=5)
            # Rejection precedes both a second temporary-file writer and API use.
            offline.extract.assert_called_once()
            offline.transcribe.assert_not_called()
        finally:
            release.set()
        segments, _, repairs = first.result(timeout=5)

    offline.extract.assert_called_once()
    offline.transcribe.assert_called_once()
    assert [(s["start"], s["end"], s["text"]) for s in segments] == [
        (0, 10, "前文"), (11, 19, "补齐后的原话"), (20, 100, "后文")]
    assert [(r["start"], r["end"]) for r in repairs] == [(10, 20)]
    clip = offline.transcribe.call_args.args[0]
    assert json.loads(clip.read_text(encoding="utf-8")) == {"start": 10, "end": 20}
    assert clip.with_suffix(".json").is_file()


@pytest.mark.parametrize("damage", ["clip_bytes", "missing_clip", "manifest_identity", "invalid_json"])
def test_clip_cache_damage_stops_before_another_asr_attempt(offline, damage):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])
    selected = assessment((10, 20))
    offline.run(source, selected)
    clip = offline.transcribe.call_args.args[0]
    manifest = clip.with_suffix(".json")
    if damage == "clip_bytes":
        clip.write_bytes(b"corrupted cached clip")
    elif damage == "missing_clip":
        clip.unlink()
    elif damage == "manifest_identity":
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        saved["identity"]["start"] = 0
        manifest.write_text(json.dumps(saved), encoding="utf-8")
    else:
        manifest.write_text("{broken", encoding="utf-8")
    offline.transcribe.reset_mock()
    offline.extract.reset_mock()
    with pytest.raises(ContentError, match="缓存"):
        offline.run(source, selected)
    offline.transcribe.assert_not_called()
    offline.extract.assert_not_called()


@pytest.mark.parametrize("start,end", [(-0.01, 2), (2, 10.26), (6, 5), (math.nan, 5), (0, math.inf)])
def test_out_of_range_asr_cannot_escape_the_repair_window(offline, start, end):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])

    def outside(*args, **kwargs):
        offline.default_transcribe(*args, **kwargs)
        return [response(start, end)], []

    offline.transcribe.side_effect = outside
    with pytest.raises(ContentError, match="超出"):
        offline.run(source, assessment((10, 20)))


def test_small_codec_overrun_is_clamped_to_global_repair_end(offline):
    source = document([("left", 0, 10, "前文"), ("right", 20, 100, "后文")])

    def rounded(*args, **kwargs):
        offline.default_transcribe(*args, **kwargs)
        return [response(0, 10.2)], []

    offline.transcribe.side_effect = rounded
    segments, _, _ = offline.run(source, assessment((10, 20)))
    restored = next(s for s in segments if s["text"] == "补齐后的原话")
    assert (restored["start"], restored["end"]) == (10, 20)


@pytest.mark.parametrize("reported", [None, 9.0])
def test_missing_or_wrong_asr_duration_cannot_replace_subtitles(offline, reported):
    source = document([("existing", 10, 20, "待校准原文"), ("after", 20, 100, "后文")])
    original = deepcopy(source)

    def wrong_duration(*args, **kwargs):
        rows = offline.default_transcribe(*args, **kwargs)
        if reported is None:
            kwargs["metadata"].pop("duration_seconds")
        else:
            kwargs["metadata"]["duration_seconds"] = reported
        return rows

    offline.transcribe.side_effect = wrong_duration
    with pytest.raises(ContentError, match="时长"):
        offline.run(source, assessment((10, 20)))
    assert source == original
