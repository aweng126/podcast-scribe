"""Subtitle selection and bounded repairs are local structural decisions."""
from copy import deepcopy
import math

import pytest

from podcast_scribe.model import ContentError
from podcast_scribe.subtitle_ingest import assess_subtitles, normalize_subtitle_document, segments_from_subtitles


def document():
    return {"schema_version": 1, "status": "available", "source": {"kind": "bilibili", "video_id": "BVexample"},
            "selected_track": {"id": "track-001", "language": "ai-zh", "origin": "automatic", "format": "srt"},
            "content_sha256": "a" * 64,
            "cues": [{"id": f"cue-{i}", "start": i * 5.0, "end": i * 5.0 + 4.8,
                      "text": f"第{i}段介绍团队如何处理数据以及为什么作出这个决定。"} for i in range(20)]}


def codes(assessment):
    return {reason["code"] for reason in assessment["reasons"]}


def test_complete_automatic_track_needs_no_audio_and_retains_source_fingerprints():
    source = document()
    before = deepcopy(source)
    result = assess_subtitles(source, 100)
    assert result["usable"] and not result["repairable"]
    assert result["repair_ranges"] == [] and result["reasons"] == []
    assert result["metrics"]["coverage_fraction"] == pytest.approx(0.96)
    assert result["provenance"]["subtitle_content_sha256"] == "a" * 64
    assert len(result["provenance"]["subtitle_document_sha256"]) == 64
    assert result["provenance"]["subtitle_track"]["origin"] == "automatic"
    assert result["assessment_kind"] == "structural_only"
    assert source == before
    source["cues"][0]["text"] += "补充"
    assert assess_subtitles(source, 100)["provenance"]["subtitle_document_sha256"] != result["provenance"]["subtitle_document_sha256"]


def test_subtitle_segments_preserve_text_and_times_without_guessing_speakers():
    source = document()
    source["cues"][0]["speaker"] = "主持人"
    source["cues"][1]["speaker_id"] = "known-person"
    segments, people = segments_from_subtitles(source)
    assert people == []
    assert len(segments) == len(source["cues"])
    assert all(segment["speaker_id"] is None and segment["review_status"] == "unreviewed" for segment in segments)
    assert [(s["start"], s["end"], s["text"], s["raw_text"]) for s in segments] == [
        (c["start"], c["end"], c["text"], c["text"]) for c in source["cues"]]


@pytest.mark.parametrize("status", ["no_subtitles", "unavailable", "login_required"])
def test_unavailable_sources_never_offer_repair(status):
    source = document()
    source.update(status=status, selected_track=None, cues=[])
    result = assess_subtitles(source, 100)
    assert not result["usable"] and not result["repairable"]
    assert "subtitles_unavailable" in codes(result)


def test_empty_available_track_is_rejected():
    source = document()
    source["cues"] = []
    assert "empty_cues" in codes(assess_subtitles(source, 100))


@pytest.mark.parametrize("duration", [None, 0, -1, math.nan, math.inf, True, "100"])
def test_unknown_duration_cannot_prove_full_coverage(duration):
    result = assess_subtitles(document(), duration)
    assert not result["usable"] and not result["repairable"]
    assert result["metrics"]["duration_seconds"] is None
    assert "unknown_duration" in codes(result)


@pytest.mark.parametrize("language", ["en", "ai-en", "fr"])
def test_wrong_track_language_is_rejected(language):
    source = document()
    source["selected_track"]["language"] = language
    result = assess_subtitles(source, 100, "zh")
    assert "language_mismatch" in codes(result)
    assert not result["repairable"]


def test_missing_language_or_obviously_wrong_script_is_rejected():
    source = document()
    del source["selected_track"]["language"]
    assert "unknown_language" in codes(assess_subtitles(source, 100))
    source = document()
    for i, cue in enumerate(source["cues"]):
        cue["text"] = f"This English sentence describes the current subject number {i}."
    assert "text_language_mismatch" in codes(assess_subtitles(source, 100, "zh"))
    source["selected_track"]["language"] = "en-US"
    assert assess_subtitles(source, 100, "en")["usable"]


@pytest.mark.parametrize("change", [
    {"start": -1}, {"start": math.nan}, {"end": math.inf}, {"end": 0},
    {"end": -1}, {"text": " "}, {"text": None}, {"id": "cue-1"},
    {"low_confidence": "true"},
])
def test_bad_cues_are_not_silently_dropped_or_repaired(change):
    source = document()
    source["cues"][0].update(change)
    result = assess_subtitles(source, 100)
    assert not result["usable"] and not result["repairable"]
    assert codes(result) & {"invalid_structure", "invalid_timing"}


def test_unsorted_out_of_bounds_and_duplicate_cues_are_rejected():
    source = document()
    source["cues"][2]["start"] = 1
    assert "invalid_structure" in codes(assess_subtitles(source, 100))
    source = document()
    source["cues"][-1]["end"] = 105
    assert "timing_out_of_bounds" in codes(assess_subtitles(source, 100))
    source = document()
    duplicate = {**source["cues"][3], "id": "duplicate"}
    source["cues"].insert(4, duplicate)
    assert "duplicate_cues" in codes(assess_subtitles(source, 100))


def test_repeated_long_boilerplate_is_not_full_programme_text():
    source = document()
    for cue in source["cues"]:
        cue["text"] = "欢迎关注节目并且订阅我们的频道谢谢大家。"
    assert "duplicate_cues" in codes(assess_subtitles(source, 100))


def test_few_words_in_a_long_cue_cannot_fake_full_coverage():
    source = document()
    source["cues"] = [{"id": "one", "start": 0, "end": 100, "text": "欢迎收听"}]
    result = assess_subtitles(source, 100)
    assert not result["usable"] and not result["repairable"]
    assert {"sparse_text", "sparse_long_cue"} <= codes(result)


def test_short_regular_pauses_are_not_asr_candidates():
    source = document()
    for cue in source["cues"]:
        cue["end"] = cue["start"] + 4
    result = assess_subtitles(source, 100)
    assert result["usable"] and result["repair_ranges"] == []


@pytest.mark.parametrize("side", ["start", "end"])
def test_small_missing_edge_can_be_repaired_but_large_partial_track_cannot(side):
    source = document()
    source["cues"] = source["cues"][3:] if side == "start" else source["cues"][:-3]
    result = assess_subtitles(source, 100)
    assert not result["usable"] and result["repairable"]
    assert f"missing_{side}" in codes(result)
    assert len(result["repair_ranges"]) == 1
    assert result["repair_ranges"][0]["cue_ids"] == []
    source["cues"] = source["cues"][4:] if side == "start" else source["cues"][:-4]
    result = assess_subtitles(source, 100)
    assert not result["repairable"] and result["repair_ranges"] == []
    assert "repair_limit_exceeded" in codes(result)


def test_local_internal_gap_provides_exact_safe_repair_range():
    source = document()
    del source["cues"][8:11]
    result = assess_subtitles(source, 100)
    assert not result["usable"] and result["repairable"]
    assert result["repair_ranges"] == [{"start": 39.8, "end": 55.0, "reason": "internal_gap",
                                         "reasons": ["internal_gap"], "cue_ids": []}]
    assert result["metrics"]["repair_fraction"] == pytest.approx(0.152)


def test_low_confidence_repair_expands_across_all_overlapping_cues():
    source = document()
    source["cues"][5]["low_confidence"] = True
    source["cues"][6]["start"] = 29
    source["cues"][7]["start"] = 34
    result = assess_subtitles(source, 100)
    assert not result["usable"] and result["repairable"]
    assert result["repair_ranges"] == [{"start": 25.0, "end": 39.8, "reason": "low_confidence",
                                         "reasons": ["low_confidence"], "cue_ids": ["cue-5", "cue-6", "cue-7"]}]


def test_adjacent_gap_and_uncertain_cue_merge_without_losing_replacement_ids():
    source = document()
    source["cues"][5]["low_confidence"] = True
    del source["cues"][6:9]
    result = assess_subtitles(source, 100)
    assert result["repairable"]
    assert result["repair_ranges"] == [{"start": 25.0, "end": 45.0, "reason": "low_confidence",
                                         "reasons": ["internal_gap", "low_confidence"], "cue_ids": ["cue-5"]}]
    assert result["metrics"]["repair_fraction"] == pytest.approx(0.20)


def test_many_small_local_holes_are_not_unbounded_asr_requests():
    source = document()
    source["cues"] = [{"id": f"cue-{i}", "start": i * 5.0, "end": i * 5.0 + 4.8,
                       "text": f"第{i}段介绍团队如何处理数据以及为什么作出这个决定。"}
                      for i in range(180) if i % 30 not in (15, 16, 17)]
    result = assess_subtitles(source, 900)
    assert result["metrics"]["repair_range_count"] == 6
    assert result["metrics"]["repair_fraction"] < 0.20
    assert "repair_limit_exceeded" in codes(result)
    assert not result["repairable"]


def test_low_confidence_majority_and_invalid_confidence_scale_need_full_fallback():
    source = document()
    for cue in source["cues"]:
        cue["confidence"] = 0.4
    result = assess_subtitles(source, 100)
    assert not result["usable"] and not result["repairable"]
    assert "repair_limit_exceeded" in codes(result)
    source = document()
    source["cues"][0]["confidence"] = 80
    assert "invalid_confidence" in codes(assess_subtitles(source, 100))
    source["source"]["confidence_scale"] = "0-100"
    assert assess_subtitles(source, 100)["usable"]


def test_scattered_sparse_cues_fail_coverage_even_without_a_long_gap():
    source = document()
    for cue in source["cues"]:
        cue["end"] = cue["start"] + 1
    result = assess_subtitles(source, 100)
    assert "insufficient_coverage" in codes(result)
    assert not result["repairable"]


def test_normalizer_keeps_source_unchanged_and_rejects_invalid_schema():
    source = document()
    normalized = normalize_subtitle_document(source)
    normalized["cues"][0]["text"] = "不同内容"
    assert source["cues"][0]["text"] != normalized["cues"][0]["text"]
    source["schema_version"] = True
    with pytest.raises(ContentError, match="schema_version"):
        normalize_subtitle_document(source)
