"""Consolidate diarization labels without dropping speech or review history."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.cli import main
from podcast_scribe.editing import read_batch
from podcast_scribe.model import ContentError, apply_edits, load_episode, new_episode, save_episode
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def episode():
    segments, speakers = normalize_segments([
        {"start": 0, "end": 2, "speaker": "host", "text": "问题是什么？"},
        {"start": 2, "end": 4, "speaker": "guest", "text": "答案不是这样。"},
        {"start": 3, "end": 3.2, "speaker": "extra", "text": "嗯。"},
    ])
    ep = new_episode({"id": "two-person", "title": "合成双人访谈"}, segments, speakers,
                     series_id="tests", series_title="测试")
    ep["segments"][2]["review_status"] = "reviewed"
    ep["speakers"][2]["source_label"] = "chunk-2:C"
    ep["artifacts"] = {"markdown": "old.md"}
    return ep


def test_reassign_then_remove_preserves_speech_and_history(episode, tmp_path, capsys):
    path, patch, batch = (tmp_path / n for n in ("episode.json", "edits.json", "batch.json"))
    save_episode(path, episode)
    batch.write_text(json.dumps(read_batch(episode, include_reviewed=True)))
    patch.write_text(json.dumps({
        "segments": [{"id": episode["segments"][2]["id"], "speaker_id": "speaker-1"}],
        "remove_speakers": ["speaker-3"],
    }))
    assert main(["edit", str(path), "--edits", str(patch), "--batch", str(batch)]) == 0
    result = load_episode(path)
    assert [p["id"] for p in result["speakers"]] == ["speaker-1", "speaker-2"]
    assert [s["speaker_id"] for s in result["segments"]] == ["speaker-1", "speaker-2", "speaker-1"]
    for before, after in zip(episode["segments"], result["segments"]):
        for field in ("id", "start", "end", "text", "raw_text"):
            assert before[field] == after[field]
    assert result["segments"][2]["review_status"] == "edited"
    assert result["artifacts"] == {} and result["revision"] == 2
    assert not any(result["review"].values())
    history = tmp_path / "history/two-person-r1.json"
    assert json.loads(history.read_text()) == episode
    saved = path.read_bytes()
    assert main(["edit", str(path), "--edits", str(patch), "--batch", str(batch)]) == 2
    assert path.read_bytes() == saved  # A removed label cannot be reused via a stale batch.


def test_in_use_speaker_removal_is_atomic(episode):
    original = deepcopy(episode)
    with pytest.raises(ContentError, match="仍被段落引用"):
        apply_edits(episode, {"remove_speakers": ["speaker-3"]})
    assert episode == original


@pytest.mark.parametrize("removed", [None, "speaker-3", {}, [None], [True], [["speaker-3"]],
                                     ["missing"], ["speaker-3", "speaker-3"]])
def test_invalid_removal_is_rejected(episode, removed):
    with pytest.raises(ContentError):
        apply_edits(episode, {"remove_speakers": removed})


def test_removal_does_not_bypass_batch_target_guard(episode, tmp_path, capsys):
    path, patch, batch = (tmp_path / n for n in ("episode.json", "edits.json", "batch.json"))
    save_episode(path, episode)
    view = read_batch(episode, include_reviewed=True)
    view["targets"] = view["targets"][:1]
    batch.write_text(json.dumps(view))
    patch.write_text(json.dumps({
        "segments": [{"id": episode["segments"][2]["id"], "speaker_id": "speaker-1"}],
        "remove_speakers": ["speaker-3"],
    }))
    original = path.read_bytes()
    assert main(["edit", str(path), "--edits", str(patch), "--batch", str(batch)]) == 2
    assert "targets" in capsys.readouterr().err
    assert path.read_bytes() == original
    assert not (tmp_path / "history").exists()
