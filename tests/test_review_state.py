"""Review assertions must agree with per-segment evidence before publishing."""

from copy import deepcopy
import json

import pytest

from podcast_scribe.cli import main
from podcast_scribe.model import ContentError, apply_edits, load_episode, new_episode, save_episode, validate_episode
from podcast_scribe.reading import reading_turns
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def draft():
    segments, speakers = normalize_segments([
        {"start": 0, "end": 1, "speaker": "A", "text": "保留否定词。"},
        {"start": 1, "end": 2, "speaker": "B", "text": "检查说话人归属。"},
    ])
    ep = new_episode({"id": "review-test", "title": "虚构校对测试"}, segments, speakers,
                     series_id="tests", series_title="测试")
    ep["summary"] = ["测试校对流程。"]
    ep["chapters"] = [{"id": "c1", "title": "开场", "start": 0, "segment_id": segments[0]["id"]}]
    return ep


def review_patch(ep):
    return {
        "segments": [{"id": s["id"], "review_status": "reviewed"} for s in ep["segments"]],
        "review": {"content_checked": True, "speakers_confirmed": True},
    }


@pytest.mark.parametrize("status", [None, "unreviewed", "edited", "needs_review", "pending", "uncertain"])
def test_global_confirmation_cannot_bypass_segment_review(draft, status):
    for segment in draft["segments"]:
        if status is None:
            segment.pop("review_status")
        else:
            segment["review_status"] = status
    original = deepcopy(draft)
    validate_episode(draft)  # Legacy drafts remain readable, without normalizing evidence.
    assert draft == original
    with pytest.raises(ContentError, match="所有段落明确标记为 reviewed"):
        apply_edits(draft, {"review": {"content_checked": True}})
    draft["review"]["content_checked"] = True
    with pytest.raises(ContentError, match="所有段落明确标记为 reviewed"):
        validate_episode(draft)


@pytest.mark.parametrize("status", [None, True, 1, [], {}, "", "done", "REVIEWED"])
def test_unknown_or_non_string_segment_review_is_rejected(draft, status):
    with pytest.raises(ContentError, match="review_status 无效"):
        apply_edits(draft, {"segments": [{"id": draft["segments"][0]["id"], "review_status": status}]})
    draft["segments"][0]["review_status"] = status
    with pytest.raises(ContentError, match="review_status 无效"):
        validate_episode(draft)


@pytest.mark.parametrize("field", ["text", "speaker_id"])
def test_reviewed_content_changes_require_new_segment_review(draft, field):
    reviewed = apply_edits(draft, review_patch(draft))
    reviewed["status"] = "published"
    reviewed["artifacts"] = {"markdown": "previous.md"}
    change = {"text": "不能丢失否定词。", "speaker_id": reviewed["speakers"][1]["id"]}[field]
    patch = {"segments": [{"id": reviewed["segments"][0]["id"], field: change}]}
    edited = apply_edits(reviewed, patch)
    assert edited["segments"][0]["review_status"] == "edited"
    assert edited["segments"][1]["review_status"] == "reviewed"
    assert edited["review"] == {"content_checked": False, "speakers_confirmed": False}
    assert edited["status"] == "draft" and edited["artifacts"] == {}
    assert reviewed["segments"][0]["review_status"] == "reviewed"
    assert edited["segments"][0]["raw_text"] == draft["segments"][0]["raw_text"]
    with pytest.raises(ContentError, match="所有段落明确标记为 reviewed"):
        apply_edits(edited, {"review": {"content_checked": True}})
    patch["segments"][0]["review_status"] = "reviewed"
    patch["review"] = {"content_checked": True, "speakers_confirmed": True}
    explicitly_rechecked = apply_edits(reviewed, patch)
    validate_episode(explicitly_rechecked, for_publication=True)


def test_unchanged_text_does_not_reset_segment_review(draft):
    reviewed = apply_edits(draft, review_patch(draft))
    first = reviewed["segments"][0]
    result = apply_edits(reviewed, {"segments": [{"id": first["id"], "text": first["text"]}]})
    assert result["segments"][0]["review_status"] == "reviewed"
    assert not any(result["review"].values())


def test_speakers_confirmation_rejects_unassigned_segments(draft):
    draft["segments"][0]["speaker_id"] = None
    with pytest.raises(ContentError, match="仍有说话人待确认"):
        apply_edits(draft, {"review": {"speakers_confirmed": True}})


@pytest.mark.parametrize("key", ["content_checked", "speakers_confirmed"])
@pytest.mark.parametrize("value", [1, "true", None])
def test_loaded_review_flags_are_booleans(draft, key, value, tmp_path):
    draft["review"][key] = value
    path = tmp_path / "episode.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    for for_edit in (False, True):
        with pytest.raises(ContentError, match="必须是布尔值"):
            load_episode(path, for_edit=for_edit)


@pytest.mark.parametrize("status", [None, "unreviewed", "edited", "needs_review", "pending", "uncertain", "reviewed"])
def test_reading_marks_every_unreviewed_state(draft, status):
    for segment in draft["segments"]:
        if status is None:
            segment.pop("review_status")
        else:
            segment["review_status"] = status
    assert all(turn["needs_review"] is (status != "reviewed") for turn in reading_turns(draft))


def test_cli_rejects_false_confirmation_without_writing(draft, tmp_path, capsys):
    episode_path, patch_path = tmp_path / "episode.json", tmp_path / "edits.json"
    save_episode(episode_path, draft)
    original_bytes = episode_path.read_bytes()
    patch_path.write_text(json.dumps({"review": {"content_checked": True, "speakers_confirmed": True}}))
    assert main(["edit", str(episode_path), "--edits", str(patch_path)]) == 2
    assert "所有段落明确标记为 reviewed" in capsys.readouterr().err
    assert episode_path.read_bytes() == original_bytes
    assert not (tmp_path / "history").exists()
    assert main(["publish", str(episode_path)]) == 2
    assert episode_path.read_bytes() == original_bytes


def test_cli_repairs_legacy_publication_and_keeps_original_history(draft, tmp_path, capsys):
    draft["status"] = "published"
    draft["review"] = {"content_checked": True, "speakers_confirmed": True}
    draft["segments"][0]["review_status"] = "pending"
    draft["segments"][1].pop("review_status")
    draft["artifacts"] = {"markdown": "legacy.md"}
    episode_path, patch_path = tmp_path / "episode.json", tmp_path / "edits.json"
    episode_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    original_bytes = episode_path.read_bytes()
    assert main(["publish", str(episode_path)]) == 2
    assert "所有段落明确标记为 reviewed" in capsys.readouterr().err
    assert episode_path.read_bytes() == original_bytes
    with pytest.raises(ContentError):
        load_episode(episode_path)
    assert load_episode(episode_path, for_edit=True) == draft

    patch_path.write_text("{}", encoding="utf-8")
    assert main(["edit", str(episode_path), "--edits", str(patch_path)]) == 0
    repaired = load_episode(episode_path)
    assert repaired["status"] == "draft"
    assert repaired["review"] == {"content_checked": False, "speakers_confirmed": False}
    assert repaired["artifacts"] == {}
    assert repaired["segments"] == draft["segments"]
    history = tmp_path / "history" / "review-test-r1.json"
    assert json.loads(history.read_text(encoding="utf-8")) == draft

    patch_path.write_text(json.dumps(review_patch(repaired)), encoding="utf-8")
    assert main(["edit", str(episode_path), "--edits", str(patch_path)]) == 0
    assert main(["publish", str(episode_path)]) == 0
    assert load_episode(episode_path)["status"] == "published"
    assert json.loads(history.read_text(encoding="utf-8")) == draft


@pytest.mark.parametrize("mutation", [
    lambda ep: ep.update(status="invalid"),
    lambda ep: ep["segments"][0].update(text=None),
    lambda ep: ep["segments"][0].update(review_status="unknown"),
    lambda ep: ep["segments"][0].update(end=9999),
])
def test_edit_recovery_still_requires_valid_structure(draft, mutation, tmp_path):
    mutation(draft)
    path = tmp_path / "episode.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ContentError):
        load_episode(path, for_edit=True)
