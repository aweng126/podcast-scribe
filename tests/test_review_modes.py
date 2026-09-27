"""Completion records its evidence without disguising automation as source review."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.cli import main
from podcast_scribe.editing import editing_status
from podcast_scribe.model import (
    ContentError, apply_edits, complete_episode, load_episode, new_episode,
    save_episode, validate_episode,
)
from podcast_scribe.transcripts import normalize_segments


def make_episode(mode="auto"):
    segments, speakers = normalize_segments([
        {"start": 0, "end": 1, "speaker": "A", "text": "完整保留否定词。"},
        {"start": 1, "end": 2, "speaker": "B", "text": "保持人物归属。"},
        {"start": 2, "end": 3, "speaker": "A", "text": "不修改原始时间。"},
    ])
    episode = new_episode(
        {"id": "review-modes-test", "title": "虚构模式测试"}, segments, speakers,
        series_id="tests", series_title="测试", review_mode=mode,
    )
    episode["summary"] = ["只使用合成内容检查完成状态。"]
    episode["chapters"] = [
        {"id": "c1", "title": "开场", "start": 0, "segment_id": segments[0]["id"]},
    ]
    return episode


@pytest.fixture
def edited_episode():
    episode = make_episode()
    for segment, state in zip(episode["segments"], ("edited", "needs_review", "reviewed")):
        segment["review_status"] = state
    return episode


def assert_flags(episode, value):
    assert episode["review"]["content_checked"] is value
    assert episode["review"]["speakers_confirmed"] is value


def assert_content_preserved(before, after):
    for key in ("title", "summary", "chapters", "speakers", "source", "duration_seconds"):
        assert after[key] == before[key]
    assert len(after["segments"]) == len(before["segments"])
    for old, new in zip(before["segments"], after["segments"]):
        assert {k: v for k, v in new.items() if k != "review_status"} == {
            k: v for k, v in old.items() if k != "review_status"
        }


@pytest.mark.parametrize("mode", ["auto", "precise"])
def test_new_episode_persists_explicit_mode(mode):
    episode = make_episode(mode)
    assert episode["review"]["mode"] == mode
    assert "basis" not in episode["review"]
    assert_flags(episode, False)


def test_legacy_episode_remains_unchanged_when_reading_effective_auto_status():
    episode = make_episode(None)
    assert "mode" not in episode["review"]
    original = deepcopy(episode)
    validate_episode(episode)
    report = editing_status(episode)
    assert report["review_mode"] == "auto"
    assert report["completion_basis"] is None
    assert report["completion_status"] == "incomplete"
    assert "mode" not in report["review"]
    assert episode == original


@pytest.mark.parametrize("field,value", [
    ("mode", "fast"), ("mode", None), ("mode", True), ("mode", []),
    ("basis", "checked"), ("basis", None), ("basis", True), ("basis", {}),
])
def test_unknown_mode_or_basis_is_rejected_for_load_and_edit(field, value, edited_episode, tmp_path):
    with pytest.raises(ContentError):
        apply_edits(edited_episode, {"review": {field: value}})
    edited_episode["review"][field] = value
    path = tmp_path / "episode.json"
    path.write_text(json.dumps(edited_episode), encoding="utf-8")
    for for_edit in (False, True):
        with pytest.raises(ContentError):
            load_episode(path, for_edit=for_edit)


def test_automated_completion_is_pure_and_preserves_the_full_transcript(edited_episode):
    edited_episode["artifacts"] = {"markdown": "outdated.md"}
    original = deepcopy(edited_episode)
    completed = complete_episode(edited_episode)
    assert edited_episode == original
    assert completed is not edited_episode
    assert completed["review"]["mode"] == "auto"
    assert completed["review"]["basis"] == "automated"
    assert_flags(completed, True)
    assert all(segment["review_status"] == "reviewed" for segment in completed["segments"])
    assert completed["status"] == "draft"
    assert completed["revision"] == original["revision"] + 1
    assert completed["artifacts"] == {}
    assert_content_preserved(original, completed)
    validate_episode(completed, for_publication=True)


@pytest.mark.parametrize("state", [None, "unreviewed", "pending", "uncertain"])
def test_automated_completion_requires_every_segment_to_have_been_edited(edited_episode, state):
    segment = edited_episode["segments"][1]
    if state is None:
        segment.pop("review_status")
    else:
        segment["review_status"] = state
    original = deepcopy(edited_episode)
    with pytest.raises(ContentError):
        complete_episode(edited_episode, basis="automated")
    assert edited_episode == original


@pytest.mark.parametrize("basis", ["automated", "user_accepted", "source_checked"])
@pytest.mark.parametrize("missing", ["summary", "chapters", "speaker"])
def test_completion_requires_readable_structure_and_assigned_speakers(edited_episode, basis, missing):
    for segment in edited_episode["segments"]:
        segment["review_status"] = "reviewed"
    if missing == "speaker":
        edited_episode["segments"][0]["speaker_id"] = None
    else:
        edited_episode[missing] = []
    original = deepcopy(edited_episode)
    with pytest.raises(ContentError):
        complete_episode(edited_episode, basis=basis)
    assert edited_episode == original


@pytest.mark.parametrize("mode", ["auto", "precise"])
def test_user_acceptance_can_complete_unedited_text_without_claiming_source_checks(mode):
    episode = make_episode(mode)
    original = deepcopy(episode)
    accepted = complete_episode(episode, basis="user_accepted")
    assert accepted["review"]["basis"] == "user_accepted"
    assert_flags(accepted, True)
    assert all(segment["review_status"] == "reviewed" for segment in accepted["segments"])
    assert_content_preserved(original, accepted)
    assert episode == original


@pytest.mark.parametrize("basis", ["automated", "user_accepted"])
def test_completion_cannot_relabel_automatic_or_accepted_evidence_as_source_checked(edited_episode, basis):
    completed = complete_episode(edited_episode, basis=basis)
    original = deepcopy(completed)
    with pytest.raises(ContentError):
        complete_episode(completed, basis="source_checked")
    assert completed == original


@pytest.mark.parametrize("mode", ["auto", "precise"])
def test_source_checked_completion_requires_existing_segment_review(mode):
    episode = make_episode(mode)
    with pytest.raises(ContentError):
        complete_episode(episode, basis="source_checked")
    for segment in episode["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(episode, basis="source_checked")
    assert completed["review"]["mode"] == mode
    assert completed["review"]["basis"] == "source_checked"
    assert_flags(completed, True)


def test_precise_default_completion_uses_source_checked():
    episode = make_episode("precise")
    for segment in episode["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(episode)
    assert completed["review"]["basis"] == "source_checked"
    assert_flags(completed, True)


def test_reopening_precise_after_user_acceptance_requires_segment_checks():
    accepted = complete_episode(make_episode("precise"), basis="user_accepted")
    reopened = apply_edits(accepted, {"review": {"mode": "precise"}})
    assert reopened["review"]["mode"] == "precise"
    assert "basis" not in reopened["review"]
    assert_flags(reopened, False)
    assert all(segment["review_status"] == "edited" for segment in reopened["segments"])
    assert_content_preserved(accepted, reopened)
    with pytest.raises(ContentError):
        complete_episode(reopened)
    for segment in reopened["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(reopened)
    assert completed["review"]["basis"] == "source_checked"
    assert_flags(completed, True)


def test_completing_a_legacy_publication_returns_a_draft_with_explicit_provenance():
    episode = make_episode(None)
    for segment in episode["segments"]:
        segment["review_status"] = "reviewed"
    episode["review"].update(content_checked=True, speakers_confirmed=True)
    episode["status"] = "published"
    original = deepcopy(episode)
    completed = complete_episode(episode)
    assert completed["status"] == "draft"
    assert completed["review"]["mode"] == "auto"
    assert completed["review"]["basis"] == "automated"
    assert episode == original


def test_precise_mode_rejects_automated_evidence_everywhere(edited_episode):
    precise = apply_edits(edited_episode, {"review": {"mode": "precise"}})
    with pytest.raises(ContentError):
        complete_episode(precise, basis="automated")
    with pytest.raises(ContentError):
        apply_edits(precise, {"review": {"basis": "automated"}})
    precise["review"]["basis"] = "automated"
    with pytest.raises(ContentError):
        validate_episode(precise)


@pytest.mark.parametrize("basis", ["automated", "user_accepted", "source_checked"])
def test_regular_edit_retains_mode_and_last_completion_basis(edited_episode, basis):
    for segment in edited_episode["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(edited_episode, basis=basis)
    edited = apply_edits(completed, {"title": "更清楚的节目标题"})
    assert edited["review"]["mode"] == "auto"
    assert edited["review"]["basis"] == basis
    assert_flags(edited, False)
    assert all(segment["review_status"] == "reviewed" for segment in edited["segments"])
    assert_flags(completed, True)
    report = editing_status(edited)
    assert report["review_mode"] == "auto"
    assert report["completion_basis"] is None
    assert report["completion_status"] == "incomplete"


@pytest.mark.parametrize("basis", ["automated", "user_accepted", "source_checked"])
def test_status_distinguishes_completion_bases(edited_episode, basis):
    for segment in edited_episode["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(edited_episode, basis=basis)
    original = deepcopy(completed)
    report = editing_status(completed)
    assert report["completion_basis"] == basis
    assert report["completion_status"] == basis
    assert completed == original


@pytest.mark.parametrize("basis", ["automated", "user_accepted"])
def test_switching_to_precise_cannot_reuse_automated_or_accepted_review(edited_episode, basis):
    completed = complete_episode(edited_episode, basis=basis)
    # An ordinary edit must not erase the provenance needed by a later switch.
    edited = apply_edits(completed, {"segments": [
        {"id": completed["segments"][0]["id"], "text": "修改正文后仍需要核对来源。"},
    ]})
    assert edited["review"]["basis"] == basis
    precise = apply_edits(edited, {"review": {"mode": "precise"}})
    assert precise["review"]["mode"] == "precise"
    assert "basis" not in precise["review"]
    assert_flags(precise, False)
    assert all(segment["review_status"] == "edited" for segment in precise["segments"])
    with pytest.raises(ContentError):
        complete_episode(precise, basis="source_checked")
    with pytest.raises(ContentError):
        apply_edits(precise, {"review": {"content_checked": True}})
    assert_content_preserved(edited, precise)
    rechecked = apply_edits(precise, {"segments": [
        {"id": segment["id"], "review_status": "reviewed"}
        for segment in precise["segments"]
    ]})
    source_checked = complete_episode(rechecked, basis="source_checked")
    assert source_checked["review"]["basis"] == "source_checked"
    assert_flags(source_checked, True)


def test_switching_to_precise_preserves_actual_source_checks(edited_episode):
    for segment in edited_episode["segments"]:
        segment["review_status"] = "reviewed"
    completed = complete_episode(edited_episode, basis="source_checked")
    precise = apply_edits(completed, {"review": {"mode": "precise"}})
    assert precise["review"]["basis"] == "source_checked"
    assert all(segment["review_status"] == "reviewed" for segment in precise["segments"])
    assert_flags(precise, False)
    assert_flags(complete_episode(precise, basis="source_checked"), True)


def test_legacy_explicit_review_flags_still_work():
    episode = make_episode(None)
    result = apply_edits(episode, {
        "segments": [{"id": segment["id"], "review_status": "reviewed"}
                     for segment in episode["segments"]],
        "review": {"content_checked": True, "speakers_confirmed": True},
    })
    assert result["review"] == {"content_checked": True, "speakers_confirmed": True}
    validate_episode(result, for_publication=True)


@pytest.mark.parametrize("basis", [None, "automated", "user_accepted", "source_checked"])
def test_cli_completion_saves_original_history_and_an_unpublished_revision(edited_episode, basis, tmp_path):
    if basis == "source_checked":
        for segment in edited_episode["segments"]:
            segment["review_status"] = "reviewed"
    path = tmp_path / "episode.json"
    save_episode(path, edited_episode)
    argv = ["complete", str(path)]
    if basis is not None:
        argv.extend(["--basis", basis])
    assert main(argv) == 0
    completed = load_episode(path)
    assert completed["review"]["basis"] == (basis or "automated")
    assert completed["status"] == "draft"
    assert completed["revision"] == edited_episode["revision"] + 1
    assert_flags(completed, True)
    backup = tmp_path / "history" / "review-modes-test-r1.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == edited_episode


def test_cli_completion_does_not_overwrite_existing_revision_history(edited_episode, tmp_path):
    path = tmp_path / "episode.json"
    save_episode(path, edited_episode)
    backup = tmp_path / "history" / "review-modes-test-r1.json"
    backup.parent.mkdir()
    backup.write_text('{"preserved": "earlier backup"}\n', encoding="utf-8")
    original = backup.read_bytes()
    assert main(["complete", str(path)]) == 0
    assert backup.read_bytes() == original
    assert_flags(load_episode(path), True)


@pytest.mark.parametrize("basis", [None, "source_checked"])
def test_cli_failed_completion_does_not_write_episode_or_history(basis, tmp_path, capsys):
    path = tmp_path / "episode.json"
    save_episode(path, make_episode())
    original = path.read_bytes()
    argv = ["complete", str(path)]
    if basis is not None:
        argv.extend(["--basis", basis])
    assert main(argv) == 2
    assert capsys.readouterr().err
    assert path.read_bytes() == original
    assert not (tmp_path / "history").exists()


@pytest.mark.parametrize("mode", [None, "auto", "precise"])
def test_cli_import_persists_default_or_selected_mode(mode, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "synthetic.json"
    source.write_text(json.dumps({"segments": [
        {"start": 0, "end": 1, "speaker": "A", "text": "仅供离线测试。"},
    ]}, ensure_ascii=False), encoding="utf-8")
    destination = tmp_path / "episode.json"
    argv = ["import", str(source), "--output", str(destination)]
    if mode is not None:
        argv.extend(["--review-mode", mode])
    assert main(argv) == 0
    capsys.readouterr()
    episode = load_episode(destination)
    assert episode["review"]["mode"] == (mode or "auto")
    assert "basis" not in episode["review"]
    assert_flags(episode, False)


def test_reimport_reports_requested_mode_without_changing_existing_draft(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "synthetic.json"
    source.write_text(json.dumps({"segments": [
        {"start": 0, "end": 1, "speaker": "A", "text": "复用草稿不重置整理模式。"},
    ]}, ensure_ascii=False), encoding="utf-8")
    assert main(["import", str(source), "--id", "reuse-review-mode"]) == 0
    capsys.readouterr()
    destination = tmp_path / "data" / "reuse-review-mode" / "episode.json"
    original = destination.read_bytes()
    assert main(["import", str(source), "--id", "reuse-review-mode", "--review-mode", "precise"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "existing"
    assert report["review_mode"] == "auto"
    assert report["requested_options"]["review_mode"] == "precise"
    assert destination.read_bytes() == original


@pytest.mark.parametrize("legacy", [False, True])
def test_cli_status_reports_review_mode_without_mutating_source(edited_episode, legacy, tmp_path, capsys):
    episode = complete_episode(edited_episode)
    if legacy:
        episode["review"].pop("mode")
        episode["review"].pop("basis")
    path = tmp_path / "episode.json"
    save_episode(path, episode)
    original = path.read_bytes()
    assert main(["status", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["review_mode"] == "auto"
    assert report["completion_status"] == ("legacy_checked" if legacy else "automated")
    assert report["completion_basis"] == (None if legacy else "automated")
    assert report["review"] == episode["review"]
    assert report["remaining"] == 0
    assert path.read_bytes() == original
    assert not (tmp_path / "history").exists()
