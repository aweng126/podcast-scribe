"""Editing receipts only skip explicitly handled, unchanged batch targets."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.editing import compact_json, editing_status, read_batch
from podcast_scribe.editing_progress import (load_progress, progress_path, progress_summary,
                                           read_notes, record_progress, valid_skip_ids,
                                           validate_note)
from podcast_scribe.model import ContentError, apply_edits, new_episode, save_episode
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def episode():
    segments, speakers = normalize_segments([
        {"start": i * 10, "end": (i + 1) * 10, "speaker": str(i % 2),
         "text": f"原文{i}。" + "保留原意与所有数字。" * 8}
        for i in range(8)
    ])
    return new_episode({"id": "progress-test", "title": "虚构整理进度测试"},
                       segments, speakers, series_id="tests", series_title="测试",
                       review_mode="auto")


def handle(path, episode, patches, *, note=None, batch=None):
    batch = batch or read_batch(episode, max_chars=10000)
    edits = {"segments": patches}
    after = apply_edits(episode, edits)
    save_episode(path, after)
    progress = record_progress(path, episode, after, edits, batch, note=note)
    return after, progress


def test_partial_batch_never_skips_omitted_or_implicitly_edited_segments(episode, tmp_path):
    path = tmp_path / "episode.json"
    first, second, third, fourth = [segment["id"] for segment in episode["segments"][:4]]
    after, progress = handle(path, episode, [
        {"id": first, "text": "已整理第一段。", "review_status": "edited"},
        {"id": second, "text": "仅改正文，未显式登记本段处理完成。"},
        {"id": third, "review_status": "needs_review"},
    ], note="第一段事实已整理；第三段数字仍需确认。")
    assert valid_skip_ids(after, progress) == {first}
    resumed = read_batch(after, skip_ids=valid_skip_ids(after, progress))
    assert resumed["targets"][0]["id"] == second
    assert {third, fourth}.issubset({target["id"] for target in resumed["targets"]})
    assert progress_summary(after, progress)["remaining_to_edit"] == 7
    assert progress["notes"][0]["segment_ids"] == [s["id"] for s in episode["segments"]]
    assert load_progress(path, after) == progress


def test_legacy_edited_without_receipt_is_reread(episode, tmp_path):
    for segment in episode["segments"]:
        segment["review_status"] = "edited"
    progress = load_progress(tmp_path / "episode.json", episode)
    assert not valid_skip_ids(episode, progress)
    batch = read_batch(episode, skip_ids=valid_skip_ids(episode, progress))
    assert batch["targets"][0]["id"] == episode["segments"][0]["id"]
    assert progress_summary(episode, progress)["remaining_to_edit"] == 8


@pytest.mark.parametrize("field,value", [
    ("text", "外部改稿"), ("raw_text", "外部原文变化"), ("start", 0.5),
    ("end", 9.5), ("speaker_id", "spk-002"), ("review_status", "needs_review"),
])
def test_changed_segment_fingerprint_requires_rereading(episode, tmp_path, field, value):
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    changed = deepcopy(after)
    changed["segments"][0][field] = value
    assert valid_skip_ids(changed, progress) == set()
    assert progress_summary(changed, progress)["invalidated"] == 1


def test_only_related_speaker_definition_invalidates_receipt(episode, tmp_path):
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    unrelated = deepcopy(after)
    unrelated["speakers"][1]["name"] = "另一位人物"
    assert valid_skip_ids(unrelated, progress) == {ident}
    related = deepcopy(after)
    related["speakers"][0]["role"] = "确认后的采访者"
    assert not valid_skip_ids(related, progress)


def test_nonsemantic_bookkeeping_does_not_invalidate_receipt(episode, tmp_path):
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    after["revision"] += 3
    after["artifacts"] = {"markdown": "out.md"}
    after["updated_at"] = "a new bookkeeping value"
    after["summary"] = ["后来新增摘要。"]
    assert valid_skip_ids(after, progress) == {ident}


def test_mode_switch_and_return_do_not_resurrect_auto_receipts(episode, tmp_path):
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}], note="这一段已整理。")
    precise = apply_edits(after, {"review": {"mode": "precise"}})
    assert not valid_skip_ids(precise, progress)
    save_episode(path, precise)
    switched = record_progress(path, after, precise, {"review": {"mode": "precise"}})
    assert switched["entries"] == {}
    assert read_notes(precise, switched)["notes"][0]["stale"]
    auto = apply_edits(precise, {"review": {"mode": "auto"}})
    save_episode(path, auto)
    resumed = record_progress(path, precise, auto, {"review": {"mode": "auto"}})
    assert not valid_skip_ids(auto, resumed)
    assert len(resumed["notes"]) == 1


def test_precise_edited_receipt_never_skips_source_review(episode, tmp_path):
    episode["review"]["mode"] = "precise"
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    assert progress_summary(after, progress)["registered"] == 1
    assert not valid_skip_ids(after, progress)
    assert read_batch(after, skip_ids=valid_skip_ids(after, progress))["targets"][0]["id"] == ident


def test_ordinary_edit_invalidates_entries_without_registering_new_ones(episode, tmp_path):
    path = tmp_path / "episode.json"
    first, second = [segment["id"] for segment in episode["segments"][:2]]
    after, progress = handle(path, episode, [{"id": first, "review_status": "edited"}])
    edits = {"segments": [{"id": first, "text": "后来修改。", "review_status": "edited"},
                           {"id": second, "review_status": "edited"}]}
    changed = apply_edits(after, edits)
    save_episode(path, changed)
    progress = record_progress(path, after, changed, edits)
    assert not valid_skip_ids(changed, progress)
    # Restoring text later must not silently resurrect the removed receipt.
    reverted = deepcopy(changed)
    reverted["segments"][0]["text"] = after["segments"][0]["text"]
    assert not valid_skip_ids(reverted, progress)


def test_context_or_stale_batch_cannot_register_progress(episode, tmp_path):
    path = tmp_path / "episode.json"
    batch = read_batch(episode, max_chars=900)
    context_id = batch["context"]["after"][0]["id"]
    edits = {"segments": [{"id": context_id, "review_status": "edited"}]}
    after = apply_edits(episode, edits)
    with pytest.raises(ContentError, match="context 是只读"):
        record_progress(path, episode, after, edits, batch)
    changed_before = deepcopy(episode)
    changed_before["segments"][0]["text"] += "稍后变化"
    with pytest.raises(ContentError, match="批次已过期"):
        record_progress(path, changed_before, after, {"segments": []}, batch)
    assert not progress_path(path).exists()


def test_note_tracks_uncertain_and_unmodified_targets_without_skipping_them(episode, tmp_path):
    path = tmp_path / "episode.json"
    first, second = [s["id"] for s in episode["segments"][:2]]
    after, progress = handle(path, episode, [{"id": first, "review_status": "needs_review"}],
                             note="第一段数字待核对；第二段仍需接着检查。")
    assert not valid_skip_ids(after, progress)
    assert {first, second} <= set(progress["notes"][0]["segment_ids"])
    for index in (0, 1):
        changed = deepcopy(after)
        changed["segments"][index]["text"] += "后来更正了数字。"
        assert read_notes(changed, progress)["notes"][0]["stale"]


def test_ordinary_edit_cannot_restore_a_receipt_already_stale_before_the_edit(episode, tmp_path):
    path = tmp_path / "episode.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    external = deepcopy(after)
    external["segments"][0]["text"] = "未通过本工具登记的外部修改。"
    assert not valid_skip_ids(external, progress)
    edits = {"segments": [{"id": ident, "text": after["segments"][0]["text"], "review_status": "edited"}]}
    restored = apply_edits(external, edits)
    save_episode(path, restored)
    progress = record_progress(path, external, restored, edits)
    assert not valid_skip_ids(restored, progress)
    assert read_batch(restored, skip_ids=valid_skip_ids(restored, progress))["targets"][0]["id"] == ident


def test_all_registered_is_batch_done_without_changing_review_or_basis(episode, tmp_path):
    path = tmp_path / "episode.json"
    patches = [{"id": segment["id"], "review_status": "edited"} for segment in episode["segments"]]
    after, progress = handle(path, episode, patches)
    saved = deepcopy(after)
    assert read_batch(after, skip_ids=valid_skip_ids(after, progress))["done"]
    assert progress_summary(after, progress)["remaining_to_edit"] == 0
    assert editing_status(after)["remaining"] == 8
    assert editing_status(after)["completion_status"] == "incomplete"
    assert after == saved and not after["review"]["content_checked"]
    # An explicit reread works by omitting skip_ids; the CLI selects that behavior.
    assert read_batch(after, raw=True, include_reviewed=True)["targets"]


def test_sidecar_is_bound_to_path_and_source_not_just_episode_id(episode, tmp_path):
    path = tmp_path / "one.json"
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}])
    other = tmp_path / "two.json"
    assert progress_path(path).name == "one.json.editing-progress.json"
    assert not load_progress(other, after)["entries"]
    progress_path(other).write_bytes(progress_path(path).read_bytes())
    with pytest.raises(ContentError, match="其他文稿路径"):
        load_progress(other, after)
    foreign = deepcopy(after)
    foreign["source"]["url"] = "https://example.org/a-different-input"
    with pytest.raises(ContentError, match="不属于当前文稿"):
        load_progress(path, foreign)
    foreign["id"] = "another-episode"
    with pytest.raises(ContentError, match="不属于当前文稿"):
        valid_skip_ids(foreign, progress)


@pytest.mark.parametrize("broken", ["{broken", '{"schema_version": 1}', '[]'])
def test_corrupt_progress_is_preserved_and_never_silently_replaced(episode, tmp_path, broken):
    path = tmp_path / "episode.json"
    progress_path(path).write_text(broken, encoding="utf-8")
    with pytest.raises(ContentError):
        load_progress(path, episode)
    with pytest.raises(ContentError):
        record_progress(path, episode, episode, {})
    assert progress_path(path).read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("note", ["字" * 1201, "   ", 123])
def test_invalid_note_is_rejected_without_sidecar_writes(episode, tmp_path, note):
    path = tmp_path / "episode.json"
    with pytest.raises(ContentError):
        validate_note(note)
    with pytest.raises(ContentError):
        record_progress(path, episode, episode, {}, read_batch(episode), note=note)
    assert not progress_path(path).exists()


def test_notes_require_batch_and_allow_exact_limit(episode, tmp_path):
    path = tmp_path / "episode.json"
    with pytest.raises(ContentError, match="需要 --batch"):
        record_progress(path, episode, episode, {}, note="不可无批次登记")
    ident = episode["segments"][0]["id"]
    after, progress = handle(path, episode, [{"id": ident, "review_status": "edited"}], note="字" * 1200)
    assert read_notes(after, progress)["notes"][0]["text"] == "字" * 1200


def test_notes_are_bounded_paginated_and_retain_stale_evidence(episode, tmp_path):
    path = tmp_path / "episode.json"
    current = episode
    for index in range(3):
        current, progress = handle(path, current, [
            {"id": current["segments"][index]["id"], "review_status": "edited"},
        ], note=f"批次{index}事实和疑点。" + "简短笔记。" * 40)
    visited, cursor = [], 0
    while True:
        page = read_notes(current, progress, after=cursor, max_chars=600)
        assert len(compact_json(page)) <= 600
        assert "fingerprints" not in compact_json(page)
        visited.extend(note["id"] for note in page["notes"])
        cursor = page["next_after"]
        if cursor is None:
            break
    assert visited == [1, 2, 3]
    assert read_notes(current, progress, after=3)["done"]
    current["segments"][0]["text"] += "后来修正事实。"
    notes = read_notes(current, progress)["notes"]
    # Each note was attached to a batch containing all targets, not just its
    # explicit patch; changing the first target invalidates all three notes.
    assert all(note["stale"] for note in notes)
    with pytest.raises(ContentError, match="完整放入预算"):
        read_notes(current, progress, max_chars=256)
    with pytest.raises(ContentError, match="续读位置"):
        read_notes(current, progress, after=4)
    with pytest.raises(ContentError, match="至少 256"):
        read_notes(current, progress, max_chars=True)


def test_no_note_or_receipt_means_no_unnecessary_sidecar(episode, tmp_path):
    path = tmp_path / "episode.json"
    after = apply_edits(episode, {"description": "仅调整说明。"})
    progress = record_progress(path, episode, after, {"description": "仅调整说明。"})
    assert not progress["entries"] and not progress_path(path).exists()


def test_missing_note_text_is_detected_as_corruption(episode, tmp_path):
    path = tmp_path / "episode.json"
    after, progress = handle(path, episode, [], note="仅记录尚未处理的疑点。")
    del progress["notes"][0]["text"]
    progress_path(path).write_text(json.dumps(progress), encoding="utf-8")
    with pytest.raises(ContentError, match="损坏"):
        load_progress(path, after)
