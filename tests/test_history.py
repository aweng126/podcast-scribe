"""History stays recoverable while routine edits keep bounded plain snapshots."""
from copy import deepcopy
import gzip
import json
import pytest

from podcast_scribe import history
from podcast_scribe.model import ContentError


def _episode(revision, *, episode_id="sample", **changes):
    return {"schema_version": 1, "id": episode_id, "revision": revision, "status": "draft",
            "review": {"speakers_confirmed": False, "content_checked": False},
            "segments": [{"text": "保留未经修改的中文、空格和历史内容。" * 100}], **changes}


def _raw(episode):
    return (json.dumps(episode, ensure_ascii=False, indent=2) + "\n").encode()


def _prepare(tmp_path, revisions=range(1, 21)):
    path = tmp_path / "episode.json"
    path.write_bytes(_raw(_episode(21)))
    directory = tmp_path / "history"
    directory.mkdir()
    originals = {}
    for revision in revisions:
        originals[revision] = _raw(_episode(revision))
        (directory / f"sample-r{revision}.json").write_bytes(originals[revision])
    return path, directory, originals


def _directory_bytes(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_retains_initial_latest_ten_and_losslessly_compresses_others(tmp_path):
    path, directory, originals = _prepare(tmp_path)
    report = history.compact_history(path)
    assert report["counts"]["compressed"] == 9
    assert report["counts"]["kept_json"] == 11
    assert report["bytes"]["saved"] > 0
    assert report["bytes"]["after"] == sum(p.stat().st_size for p in directory.iterdir())
    json.dumps(report)
    for revision, raw in originals.items():
        plain = directory / f"sample-r{revision}.json"
        if revision == 1 or revision >= 11:
            assert plain.read_bytes() == raw
        else:
            assert not plain.exists()
            assert gzip.decompress(plain.with_suffix(".json.gz").read_bytes()) == raw
    assert path.read_bytes() == _raw(_episode(21))
    before = _directory_bytes(directory)
    again = history.compact_history(path)
    assert again["counts"]["compressed"] == again["counts"]["restored"] == 0
    assert again["counts"]["already_compressed"] == 9
    assert again["bytes"]["saved"] == 0
    assert _directory_bytes(directory) == before


def test_first_available_revision_and_completed_published_versions_remain_json(tmp_path):
    path, directory, _ = _prepare(tmp_path, range(4, 21))
    completed = _episode(6, review={"speakers_confirmed": True, "content_checked": True,
                                  "basis": "automated"})
    published = _episode(8, status="published")
    (directory / "sample-r6.json").write_bytes(_raw(completed))
    (directory / "sample-r8.json").write_bytes(_raw(published))
    report = history.compact_history(path)
    assert {p.name for p in directory.glob("*.json")} == {
        f"sample-r{revision}.json" for revision in (4, 6, 8, *range(11, 21))
    }
    assert report["counts"]["compressed"] == 4


def test_dry_run_is_a_read_only_preview(tmp_path):
    path, directory, _ = _prepare(tmp_path)
    before = _directory_bytes(directory)
    preview = history.compact_history(path, dry_run=True)
    assert _directory_bytes(directory) == before
    applied = history.compact_history(path)
    assert preview["counts"] == applied["counts"]
    assert preview["bytes"] == applied["bytes"]
    assert preview["dry_run"] is True


def test_unknown_files_other_episodes_mismatches_symlinks_stay_untouched(tmp_path):
    path, directory, _ = _prepare(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(_raw(_episode(2)))
    plain = directory / "sample-r2.json"
    plain.unlink()
    plain.symlink_to(outside)
    unknown = {
        "other-r2.json": _raw(_episode(2, episode_id="other")),
        "notes.txt": b"user notes", "sample-r02.json": _raw(_episode(2)),
        "sample-r3.json": _raw(_episode(300)), "sample-r4.json": b"not json",
        "sample-r5.json": _raw(_episode(True)),
        "sample-r6.json": _raw(_episode(6, episode_id="other")),
        "sample-r7.json.gz": b"not gzip",
        "sample-r8.json": _raw(_episode(8, schema_version=999)),
        "sample-r9.json": _raw(_episode(9, status=["unknown"])),
    }
    for name, raw in unknown.items():
        (directory / name).write_bytes(raw)
    report = history.compact_history(path)
    assert plain.is_symlink()
    assert outside.read_bytes() == _raw(_episode(2))
    assert (directory / "sample-r7.json").is_file()
    assert report["counts"]["skipped"] >= len(unknown) + 1
    for name, raw in unknown.items():
        assert (directory / name).read_bytes() == raw


def test_history_directory_symlink_is_not_followed(tmp_path):
    path = tmp_path / "episode.json"
    path.write_bytes(_raw(_episode(30)))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sample-r2.json").write_bytes(_raw(_episode(2)))
    (tmp_path / "history").symlink_to(outside, target_is_directory=True)
    before = _directory_bytes(outside)
    assert history.compact_history(path)["counts"]["skipped"] == 1
    with pytest.raises(ContentError, match="符号链接"):
        history.save_revision_backup(path, _episode(3))
    assert _directory_bytes(outside) == before


def test_conflicting_gzip_preserves_both_but_matching_gzip_recovers_interrupted_run(tmp_path):
    path, directory, originals = _prepare(tmp_path)
    conflicting = gzip.compress(_raw(_episode(2, title="不同内容")))
    (directory / "sample-r2.json.gz").write_bytes(conflicting)
    (directory / "sample-r3.json.gz").write_bytes(gzip.compress(originals[3]))
    report = history.compact_history(path)
    assert (directory / "sample-r2.json").read_bytes() == originals[2]
    assert (directory / "sample-r2.json.gz").read_bytes() == conflicting
    assert not (directory / "sample-r3.json").exists()
    assert gzip.decompress((directory / "sample-r3.json.gz").read_bytes()) == originals[3]
    assert any("内容不同" in entry["reason"] for entry in report["skipped"])
    assert report["bytes"]["after"] == sum(p.stat().st_size for p in directory.iterdir())


def test_retained_gzip_is_restored_when_policy_changes(tmp_path):
    path, directory, originals = _prepare(tmp_path)
    history.compact_history(path, keep_recent=1)
    preview = history.compact_history(path, dry_run=True)
    assert preview["counts"]["restored"] == 9
    report = history.compact_history(path)
    assert report["counts"]["restored"] == 9
    for revision in range(11, 20):
        assert (directory / f"sample-r{revision}.json").read_bytes() == originals[revision]
        assert gzip.decompress((directory / f"sample-r{revision}.json.gz").read_bytes()) == originals[revision]
    assert history.compact_history(path)["counts"]["restored"] == 0


def test_gzip_symlink_target_does_not_replace_or_remove_json(tmp_path):
    path, directory, originals = _prepare(tmp_path)
    outside = tmp_path / "outside.gz"
    outside.write_bytes(gzip.compress(originals[2]))
    (directory / "sample-r2.json.gz").symlink_to(outside)
    history.compact_history(path)
    assert (directory / "sample-r2.json").read_bytes() == originals[2]
    assert (directory / "sample-r2.json.gz").is_symlink()


def test_atomic_write_failure_preserves_all_originals_and_cleans_temporary_files(tmp_path, monkeypatch):
    path, directory, _ = _prepare(tmp_path)
    before = _directory_bytes(directory)

    def fail_publish(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(history.os, "link", fail_publish)
    report = history.compact_history(path)
    assert report["counts"]["compressed"] == 0
    assert report["counts"]["skipped"] == 9
    assert _directory_bytes(directory) == before
    assert not list(directory.glob(".history-*"))


def test_compression_verification_failure_keeps_original_json(tmp_path, monkeypatch):
    path, directory, _ = _prepare(tmp_path)
    before = _directory_bytes(directory)
    monkeypatch.setattr(history.gzip, "compress", lambda *args, **kwargs: b"broken gzip")
    report = history.compact_history(path)
    assert report["counts"]["compressed"] == 0
    assert _directory_bytes(directory) == before


def test_written_archive_is_verified_before_unlinking_source(tmp_path, monkeypatch):
    path, directory, originals = _prepare(tmp_path)
    real_create = history._atomic_create

    def corrupt_create(target, raw):
        real_create(target, gzip.compress(b"wrong contents"))

    monkeypatch.setattr(history, "_atomic_create", corrupt_create)
    report = history.compact_history(path)
    assert report["counts"]["compressed"] == 0
    for revision, raw in originals.items():
        assert (directory / f"sample-r{revision}.json").read_bytes() == raw
    assert report["bytes"]["after"] == sum(p.stat().st_size for p in directory.iterdir())


def test_changed_source_during_compression_is_not_removed(tmp_path, monkeypatch):
    path, directory, _ = _prepare(tmp_path)
    real_create = history._atomic_create
    modified = _raw(_episode(2, title="并行人工修改"))

    def change_source(target, raw):
        real_create(target, raw)
        if target.name == "sample-r2.json.gz":
            (directory / "sample-r2.json").write_bytes(modified)

    monkeypatch.setattr(history, "_atomic_create", change_source)
    report = history.compact_history(path)
    assert (directory / "sample-r2.json").read_bytes() == modified
    assert any("已变更" in entry["reason"] for entry in report["skipped"])


def test_initial_and_completed_gzip_snapshots_are_restored(tmp_path):
    path, directory, originals = _prepare(tmp_path, range(4, 21))
    completed = _raw(_episode(6, review={"speakers_confirmed": True, "content_checked": True}))
    for revision, raw in ((4, originals[4]), (6, completed)):
        (directory / f"sample-r{revision}.json").unlink()
        (directory / f"sample-r{revision}.json.gz").write_bytes(gzip.compress(raw))
    report = history.compact_history(path)
    assert report["counts"]["restored"] == 2
    assert (directory / "sample-r4.json").read_bytes() == originals[4]
    assert (directory / "sample-r6.json").read_bytes() == completed


def test_existing_gzip_backup_is_reused_and_conflicts_are_not_overwritten(tmp_path):
    path, directory, originals = _prepare(tmp_path)
    history.compact_history(path)
    before = _directory_bytes(directory)
    archived = history.save_revision_backup(path, _episode(2))
    assert archived == directory / "sample-r2.json.gz"
    assert _directory_bytes(directory) == before
    conflicting = _episode(2, title="冲突稿")
    recovery = history.save_revision_backup(path, conflicting)
    assert recovery.name.startswith("sample-r2-conflict-")
    assert recovery.suffix == ".gz"
    assert json.loads(gzip.decompress(recovery.read_bytes())) == conflicting
    assert history.save_revision_backup(path, conflicting) == recovery
    assert {name: raw for name, raw in _directory_bytes(directory).items()
            if name != recovery.name} == before
    current = _episode(21)
    backup = history.save_revision_backup(path, current)
    assert json.loads(backup.read_bytes()) == current
    assert history.save_revision_backup(path, deepcopy(current)) == backup


def test_backup_preserves_symlink_or_malformed_copies_and_saves_original_elsewhere(tmp_path):
    path, directory, _ = _prepare(tmp_path, [])
    backup = directory / "sample-r21.json"
    backup.symlink_to(tmp_path / "missing")
    recovered = history.save_revision_backup(path, _episode(21))
    assert backup.is_symlink()
    assert json.loads(gzip.decompress(recovered.read_bytes())) == _episode(21)
    backup.unlink()
    archive = directory / "sample-r21.json.gz"
    archive.write_bytes(b"user data")
    assert history.save_revision_backup(path, _episode(21)) == recovered
    assert archive.read_bytes() == b"user data"
    assert not backup.exists()


def test_conflict_recovery_failure_does_not_overwrite_any_existing_file(tmp_path):
    path, directory, _ = _prepare(tmp_path, [])
    backup = directory / "sample-r21.json"
    backup.write_bytes(b'{"preserved": "user backup"}\n')
    recovered = history.save_revision_backup(path, _episode(21))
    recovered.write_bytes(b"user changed the recovery file")
    before = _directory_bytes(directory)
    with pytest.raises(ContentError, match="无法另存原稿"):
        history.save_revision_backup(path, _episode(21))
    assert _directory_bytes(directory) == before


def test_no_history_directory_is_created_and_invalid_retention_is_rejected(tmp_path):
    path = tmp_path / "episode.json"
    path.write_bytes(_raw(_episode(1)))
    assert history.compact_history(path)["counts"]["scanned"] == 0
    assert not (tmp_path / "history").exists()
    for invalid in (-1, True, 1.5):
        with pytest.raises(ContentError):
            history.compact_history(path, keep_recent=invalid)
