"""Bounded reads must preserve coverage, review evidence, and revision safety."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.cli import main
from podcast_scribe.editing import compact_json, editing_status, read_batch, validate_batch_edits
from podcast_scribe.model import ContentError, apply_edits, load_episode, new_episode, save_episode
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def episode():
    segments, speakers = normalize_segments([
        {"start": i * 10, "end": (i + 1) * 10, "speaker": str(i % 3),
         "text": f"原始标记{i:03d}。" + "这是假设的长文稿，数字与否定需要保留。" * 5}
        for i in range(36)
    ])
    ep = new_episode({"id": "editing-test", "title": "虚构批次测试"}, segments, speakers,
                     series_id="tests", series_title="测试")
    for i, segment in enumerate(ep["segments"]):
        segment["text"] = f"当前标记{i:03d}。" + "这是当前的整理稿，需要逐段核对。" * 5
    return ep


def test_budget_includes_json_and_chinese_without_repeating_raw(episode):
    batch = read_batch(episode, max_chars=1500)
    output = compact_json(batch)
    assert len(output) <= 1500 < len(output.encode("utf-8"))
    assert "raw_text" not in output and "原始标记" not in output
    assert batch["view"] == "text"
    assert batch["targets"]
    by_id = {s["id"]: s for s in episode["segments"]}
    for segment in batch["targets"]:
        assert segment["text"] == by_id[segment["id"]]["text"]
    visible_speakers = {s["speaker_id"] for s in batch["targets"] + batch["context"]["before"] + batch["context"]["after"]}
    assert {s["id"] for s in batch["speakers"]} == visible_speakers
    assert all(set(s) == {"id", "name"} for s in batch["speakers"])


def test_every_target_is_visited_once_and_context_is_not_a_target(episode):
    visited, after = [], None
    while True:
        batch = read_batch(episode, max_chars=1600, after=after, context_chars=30)
        assert len(compact_json(batch)) <= 1600
        ids = [s["id"] for s in batch["targets"]]
        context_ids = {s["id"] for s in batch["context"]["before"] + batch["context"]["after"]}
        assert not context_ids.intersection(ids)
        assert all(s["truncated"] for s in batch["context"]["before"] + batch["context"]["after"])
        visited.extend(ids)
        after = batch["next_after"]
        if after is None:
            break
    assert visited == [s["id"] for s in episode["segments"]]


def test_resume_skips_only_reviewed_and_retains_legacy_missing_states(episode):
    states = ["reviewed", "edited", "needs_review", "pending", "uncertain", None]
    for i, segment in enumerate(episode["segments"]):
        state = states[i % len(states)]
        if state is None:
            segment.pop("review_status")
        else:
            segment["review_status"] = state
    expected = [s["id"] for s in episode["segments"] if s.get("review_status") != "reviewed"]
    visited, after = [], None
    while True:
        batch = read_batch(episode, max_chars=1300, after=after, context_chars=10)
        visited.extend(s["id"] for s in batch["targets"])
        after = batch["next_after"]
        if after is None:
            break
    assert visited == expected
    report = editing_status(episode)
    assert report["reviewed"] == 6 and report["remaining"] == 30
    assert report["states"]["unreviewed"] == 6
    assert report["next_segment_id"] == expected[0]
    assert "text" not in report and "raw_text" not in report


def test_restart_after_reviewed_batch_finds_first_unfinished_segment(episode):
    batch = read_batch(episode, max_chars=1500)
    patch = {"segments": [{"id": s["id"], "review_status": "reviewed"} for s in batch["targets"]]}
    validate_batch_edits(episode, patch, batch)
    edited = apply_edits(episode, patch)
    resumed = read_batch(edited, max_chars=1500)
    assert resumed["targets"][0]["id"] == episode["segments"][len(batch["targets"])]["id"]
    assert edited["review"]["content_checked"] is False


def test_all_reviewed_returns_done_but_include_reviewed_can_reread(episode):
    for segment in episode["segments"]:
        segment["review_status"] = "reviewed"
    batch = read_batch(episode)
    assert batch["done"] and batch["targets"] == [] and batch["next_after"] is None
    assert editing_status(episode)["remaining"] == 0
    assert not editing_status(episode)["review"]["content_checked"]
    assert read_batch(episode, include_reviewed=True)["targets"]


def test_end_cursor_is_done_but_does_not_claim_earlier_segments_reviewed(episode):
    batch = read_batch(episode, after=episode["segments"][-1]["id"])
    assert batch["done"] and batch["targets"] == []
    assert editing_status(episode)["remaining"] == len(episode["segments"])


def test_raw_view_replaces_current_text_and_clips_context_explicitly(episode):
    batch = read_batch(episode, raw=True, after=episode["segments"][0]["id"],
                       context_chars=12, max_chars=1400)
    output = compact_json(batch)
    assert len(output) <= 1400 and "当前标记" not in output and '"text":' not in output
    assert batch["view"] == "raw_text"
    assert all("raw_text" in s and "text" not in s for s in batch["targets"])
    before = batch["context"]["before"][0]
    assert before["raw_text"] == episode["segments"][0]["raw_text"][-12:]
    assert before["truncated"] is True


def test_oversized_target_is_rejected_without_truncation(episode):
    episode["segments"][0]["text"] = "完整保留" * 2000
    with pytest.raises(ContentError, match="无法完整放入批次.*不截断正文"):
        read_batch(episode, max_chars=1200)
    batch = read_batch(episode, max_chars=9500)
    assert batch["targets"][0]["text"] == episode["segments"][0]["text"]


@pytest.mark.parametrize("options", [{"max_chars": 255}, {"max_chars": True}, {"context_chars": -1}, {"after": "missing"}])
def test_invalid_budget_or_cursor_is_rejected(episode, options):
    with pytest.raises(ContentError):
        read_batch(episode, **options)


def test_context_is_reduced_when_a_complete_target_needs_the_budget(episode):
    zero = read_batch(episode, max_chars=1500, context_chars=0)
    first_only = deepcopy(episode)
    first_only["segments"][0]["text"] += "额外文字" * 100
    minimum = len(compact_json(read_batch(first_only, max_chars=100000, context_chars=0)))
    batch = read_batch(first_only, max_chars=1200, context_chars=1000)
    assert batch["targets"] and len(compact_json(batch)) <= 1200
    assert len(batch["targets"]) < len(zero["targets"])
    assert minimum > 1200  # Whole-episode text did not fit in this batch.


def test_budget_driven_context_omission_is_explicit(episode):
    # Pick a budget fitting the first complete target, but not an excerpt object.
    batch = read_batch(episode, max_chars=620, context_chars=300)
    assert len(compact_json(batch)) <= 620
    assert batch["context"] == {"before": [], "after": []}
    assert batch["context_omitted"] == ["after"]


def test_batch_guards_reject_other_episode_revision_and_same_revision_mutation(episode):
    batch = read_batch(episode)
    patch = {"segments": [{"id": batch["targets"][0]["id"], "text": "仅改当前段落。"}]}
    validate_batch_edits(episode, patch, batch)
    for change in (
        lambda ep: ep.update(id="another-episode"),
        lambda ep: ep.update(revision=ep["revision"] + 1),
        lambda ep: ep["segments"][-1].update(text="外部修改，修订号未增加。"),
        lambda ep: ep["speakers"][0].update(name="后来确认的人物"),
    ):
        altered = deepcopy(episode)
        change(altered)
        with pytest.raises(ContentError, match="批次已过期"):
            validate_batch_edits(altered, patch, batch)
    exported = deepcopy(episode)
    exported["artifacts"] = {"markdown": "new.md"}
    validate_batch_edits(exported, patch, batch)


def test_batch_rejects_context_edits_and_cannot_assert_unseen_review(episode):
    batch = read_batch(episode, max_chars=1300)
    context_id = batch["context"]["after"][0]["id"]
    with pytest.raises(ContentError, match="context 是只读"):
        validate_batch_edits(episode, {"segments": [{"id": context_id, "text": "误改上下文。"}]}, batch)
    patch = {"segments": [{"id": s["id"], "review_status": "reviewed"} for s in batch["targets"]],
             "review": {"content_checked": True}}
    validate_batch_edits(episode, patch, batch)
    with pytest.raises(ContentError, match="所有段落明确标记为 reviewed"):
        apply_edits(episode, patch)


def test_cli_batch_edit_preserves_unmodified_text_and_rejects_stale_reuse(episode, tmp_path, capsys):
    episode_path, batch_path, patch_path = (tmp_path / name for name in ("episode.json", "batch.json", "edits.json"))
    save_episode(episode_path, episode)
    assert main(["status", str(episode_path)]) == 0
    assert json.loads(capsys.readouterr().out)["remaining"] == 36
    assert main(["batch", str(episode_path), "--max-chars", "1400", "--output", str(batch_path)]) == 0
    output = capsys.readouterr().out
    assert len(output) <= 1400 and batch_path.read_text(encoding="utf-8") == output
    batch = json.loads(output)
    first = batch["targets"][0]
    patch_path.write_text(json.dumps({"segments": [{"id": first["id"], "text": "整理后的新正文。"}]}), encoding="utf-8")
    args = ["edit", str(episode_path), "--edits", str(patch_path), "--batch", str(batch_path)]
    assert main(args) == 0
    updated = load_episode(episode_path)
    assert updated["segments"][0]["text"] == "整理后的新正文。"
    assert updated["segments"][0]["raw_text"] == episode["segments"][0]["raw_text"]
    assert updated["segments"][0]["review_status"] == "edited"
    assert updated["segments"][1:] == episode["segments"][1:]
    assert (tmp_path / "history" / "editing-test-r1.json").is_file()
    saved = episode_path.read_bytes()
    assert main(args) == 2
    assert "批次已过期" in capsys.readouterr().err
    assert episode_path.read_bytes() == saved


def test_existing_batch_output_is_not_overwritten(episode, tmp_path, capsys):
    path, batch_path = tmp_path / "episode.json", tmp_path / "batch.json"
    save_episode(path, episode)
    batch_path.write_text("previous batch", encoding="utf-8")
    assert main(["batch", str(path), "--output", str(batch_path)]) == 2
    assert batch_path.read_text(encoding="utf-8") == "previous batch"
