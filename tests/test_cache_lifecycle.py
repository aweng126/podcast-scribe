"""Exercise ownership, failed delivery and reuse against real filesystem state."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from podcast_scribe import cache_lifecycle as cache
from podcast_scribe.cli import main
from podcast_scribe.exporters import render_markdown
from podcast_scribe.model import (ContentError, apply_edits, complete_episode,
                                  new_episode, save_episode)
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def manuscript(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    segments, people = normalize_segments([{"start": 0, "end": 5, "speaker": "A", "text": "测试完整正文。"}])
    episode = new_episode({"id": "sample", "title": "测试"}, segments, people,
                          series_id="tests", series_title="测试")
    episode = apply_edits(episode, {"summary": ["测试摘要"],
        "chapters": [{"id": "c1", "title": "测试章节", "start": 0, "segment_id": "seg-00001"}],
        "segments": [{"id": "seg-00001", "review_status": "edited"}]})
    episode = complete_episode(episode, basis="automated")
    path = tmp_path / "data" / "episode.json"
    path.parent.mkdir()
    save_episode(path, episode)
    return path, episode


def owned(cache_root, episode_path=None, name="episode/source.m4a"):
    with cache.media_session(cache_root):
        media = cache_root / name
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"owned generated audio")
        cache.register_media(media)
        if episode_path:
            cache.bind_episode(episode_path)
    return media


def deliver(path, episode):
    output = path.parent / "exports"
    output.mkdir(exist_ok=True)
    paths = {"markdown": output / "sample.md", "pdf": output / "sample.pdf"}
    paths["markdown"].write_text(render_markdown(episode))
    # No rendering test here: the exporter has independent real PDF checks.
    paths["pdf"].write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n")
    assert cache.record_delivery(path, episode, paths)
    return paths


def age(cache_root):
    path = cache_root / cache.INDEX
    index = json.loads(path.read_text())
    for entry in index["files"].values():
        entry["last_used"] = 1
    path.write_text(json.dumps(index))


def test_cleanup_is_preview_then_owned_media_only_and_idempotent(manuscript, tmp_path):
    path, ep = manuscript
    root = tmp_path / "custom-cache"
    media = owned(root, path)
    original = tmp_path / "original.wav"
    original.write_bytes(b"user source")
    unrelated = media.parent / "notes.mp3"
    unrelated.write_bytes(b"not registered")
    response = media.parent / "transcription.json"
    response.write_text('{"paid":"result"}')
    exports = deliver(path, ep)
    before = path.read_bytes()
    preview = cache.cleanup_episode(path)
    assert preview["files"] == [str(media)] and media.exists()
    result = cache.cleanup_episode(path, apply=True)
    assert result["bytes"] == len(b"owned generated audio") and not media.exists()
    assert original.read_bytes() == b"user source" and unrelated.exists() and response.exists()
    assert path.read_bytes() == before and all(p.exists() for p in exports.values())
    assert not cache.cleanup_episode(path, apply=True)["files"]


def test_delivery_must_be_current_and_both_formats(manuscript, tmp_path):
    path, ep = manuscript
    media = owned(tmp_path / "cache", path)
    with pytest.raises(ContentError, match="交付"):
        cache.cleanup_episode(path, apply=True)
    exports = deliver(path, ep)
    assert not cache.record_delivery(path, ep, {"markdown": exports["markdown"]})
    ep["summary"] = ["a later edit"]
    save_episode(path, ep)
    with pytest.raises(ContentError, match="交付"):
        cache.cleanup_episode(path, apply=True)
    assert media.exists()


@pytest.mark.parametrize("format_name", ["pdf", "markdown"])
def test_changed_export_does_not_allow_cleanup(manuscript, tmp_path, format_name):
    path, ep = manuscript
    media = owned(tmp_path / "cache", path)
    exports = deliver(path, ep)
    exports[format_name].write_bytes(b"later external change")
    with pytest.raises(ContentError):
        cache.cleanup_episode(path, apply=True)
    assert media.exists()


def test_corrupt_changed_symlink_hardlink_and_escaping_entries_stay(manuscript, tmp_path):
    path, ep = manuscript
    root = tmp_path / "cache"
    changed = owned(root, path, "one/source.mp3")
    symlink = owned(root, path, "two/source.mp3")
    hardlink = owned(root, path, "three/source.mp3")
    directory_link = owned(root, path, "four/source.mp3")
    changed.write_bytes(b"user changed it")
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")
    symlink.unlink();symlink.symlink_to(outside)
    os.link(hardlink, tmp_path / "linked-original.mp3")
    directory_link.parent.rename(tmp_path / "moved")
    directory_link.parent.symlink_to(tmp_path / "moved", target_is_directory=True)
    registry = root / cache.INDEX
    index = json.loads(registry.read_text())
    index["files"]["../outside.mp3"] = deepcopy(index["files"]["one/source.mp3"])
    registry.write_text(json.dumps(index))
    saved = cache._sidecar(path)
    data = json.loads(saved.read_text());data["roots"][str(root)].append("../outside.mp3")
    saved.write_text(json.dumps(data))
    deliver(path, ep)
    report = cache.cleanup_episode(path, apply=True)
    assert not report["files"] and len(report["skipped"]) == 5
    assert changed.exists() and symlink.is_symlink() and hardlink.exists() and directory_link.exists()
    assert outside.read_bytes() == b"outside"


def test_active_media_session_blocks_collection(manuscript, tmp_path):
    path, ep = manuscript
    root = tmp_path / "cache";media = owned(root, path);deliver(path, ep)
    with cache.media_session(root):
        result = cache.cleanup_episode(path, apply=True)
        assert not result["files"] and result["skipped"]
        assert media.exists()
    assert cache.cleanup_episode(path, apply=True)["files"]


def test_keep_media_persists_then_explicit_apply_releases_pin(manuscript, tmp_path):
    path, ep = manuscript
    root = tmp_path / "cache";media = owned(root, path);deliver(path, ep)
    cache.cleanup_episode(path, keep_media=True)
    age(root)
    assert not cache.prune_expired(root, apply=True)["files"] and media.exists()
    assert cache.cleanup_episode(path)["files"] and media.exists()
    index = json.loads((root / cache.INDEX).read_text())
    assert index["files"]["episode/source.m4a"]["owners"][str(path)] is True
    assert cache.cleanup_episode(path, apply=True)["files"]
    assert json.loads((root / cache.INDEX).read_text())["files"]["episode/source.m4a"]["owners"][str(path)] is False


def test_keep_request_does_not_need_completed_delivery(manuscript, tmp_path):
    path, _ = manuscript
    root = tmp_path / "cache";media = owned(root, path)
    cache.cleanup_episode(path, keep_media=True)
    age(root)
    assert not cache.prune_expired(root, apply=True)["files"] and media.exists()


def test_shared_incomplete_owner_and_other_owner_pin_are_protected(manuscript, tmp_path):
    first, ep = manuscript
    second = first.with_name("second.json");save_episode(second, ep)
    root = tmp_path / "cache";media = owned(root, first)
    with cache.media_session(root):
        cache.touch_media_tree(media.parent);cache.bind_episode(second)
    deliver(first, ep)
    cache.cleanup_episode(first, keep_media=True)
    assert not cache.cleanup_episode(first, apply=True)["files"]
    # The failed attempt still cancels only the first owner's pin.
    deliver(second, ep)
    assert cache.cleanup_episode(second, apply=True)["files"] and not media.exists()


def test_ttl_only_removes_unused_managed_media_and_protects_explicit_input(tmp_path):
    root = tmp_path / "cache"
    old = owned(root, name="old/source.mp3")
    caller_input = owned(root, name="input/source.mp3")
    unknown = root / "unknown.mp3";unknown.write_bytes(b"not owned")
    age(root)
    with cache.media_session(root, protected=[caller_input]):
        assert not old.exists() and caller_input.exists() and unknown.exists()
    age(root)
    assert not cache.prune_expired(root, apply=True)["files"] and caller_input.exists()


def test_cache_touch_refreshes_expiry(tmp_path):
    root = tmp_path / "cache";media = owned(root)
    # Expiry is based on use, not old file mtime.
    os.utime(media, (1, 1))
    assert not cache.prune_expired(root, apply=True)["files"]
    with cache.media_session(root):
        cache.touch_media_tree(media.parent)
    assert not cache.prune_expired(root, apply=True)["files"]


@pytest.fixture
def fake_pdf(monkeypatch):
    monkeypatch.setattr("podcast_scribe.exporters._pdf_font", lambda: "unused")
    monkeypatch.setattr("podcast_scribe.exporters._render_pdf",
                        lambda ep, path, font: path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n"))


def test_cli_default_export_cleans_custom_cache_and_keeps_sources(manuscript, tmp_path, fake_pdf, capsys):
    path, ep = manuscript
    media = owned(tmp_path / "custom-cache", path)
    assert main(["export", str(path)]) == 0
    report = capsys.readouterr()
    assert not media.exists() and "media_cleanup" in report.err
    assert set(json.loads(report.out)) == {"markdown", "pdf"}
    assert main(["cleanup", str(path)]) == 0


def test_cli_failure_single_format_and_unfinished_keep_media(manuscript, tmp_path, monkeypatch, fake_pdf):
    path, ep = manuscript
    media = owned(tmp_path / "cache", path)
    assert main(["export", str(path), "--formats", "markdown"]) == 0 and media.exists()
    monkeypatch.setattr("podcast_scribe.exporters._render_pdf", lambda *a: (_ for _ in ()).throw(RuntimeError("fail")))
    assert main(["export", str(path)]) == 2 and media.exists()
    ep["review"].update(content_checked=False, speakers_confirmed=False)
    save_episode(path, ep)
    monkeypatch.setattr("podcast_scribe.exporters._render_pdf", lambda ep, path, font: path.write_bytes(b"%PDF-1.4\n%%EOF\n"))
    assert main(["export", str(path)]) == 0 and media.exists()


def test_cli_keep_media_even_for_single_format(manuscript, tmp_path, fake_pdf):
    path, ep = manuscript
    root = tmp_path / "cache";media = owned(root, path)
    assert main(["export", str(path), "--formats", "markdown", "--keep-media"]) == 0
    age(root)
    assert not cache.prune_expired(root, apply=True)["files"] and media.exists()
    assert main(["export", str(path)]) == 0 and not media.exists()


def test_cli_expired_preview_and_apply(tmp_path, capsys):
    root = tmp_path / "cache";media = owned(root);age(root)
    args = ["cleanup", "--expired", "--cache", str(root)]
    assert main(args) == 0 and media.exists()
    assert json.loads(capsys.readouterr().out)["files"] == [str(media)]
    assert main([*args, "--apply"]) == 0 and not media.exists()
    assert main(["cleanup"]) == 2
    assert main([*args, "--older-than-days", "0"]) == 2


def test_legacy_unregistered_media_is_not_claimed(tmp_path):
    root = tmp_path / "cache";root.mkdir()
    media = root / "source.mp3";media.write_bytes(b"old cache or user file")
    with cache.media_session(root):
        cache.touch_media_tree(root / "legacy")
    assert not cache.prune_expired(root, apply=True)["files"] and media.exists()


def test_explicit_input_is_protected_before_collection_lock_gap(tmp_path, monkeypatch):
    root = tmp_path / "cache";media = owned(root);age(root)
    original = cache.prune_expired

    def competing_collection(*args, **kwargs):
        first = original(*args, **kwargs)
        # A second process gets the exclusive lock after expiry collection,
        # before the new session acquires its long-lived shared lock.
        assert not original(root, apply=True)["files"]
        assert media.exists()
        return first

    monkeypatch.setattr(cache, "prune_expired", competing_collection)
    with cache.media_session(root, protected=[media]):
        assert media.exists()


def test_mov_video_is_managed_like_other_supported_video(tmp_path):
    root = tmp_path / "cache";media = owned(root, name="episode/source-video.mov")
    age(root)
    assert cache.prune_expired(root, apply=True)["files"] == [str(media)]


def test_failed_cli_task_binds_shared_media_before_releasing_session(manuscript, tmp_path, monkeypatch):
    path, ep = manuscript
    root = tmp_path / "cache";media = owned(root, path);deliver(path, ep)
    original = tmp_path / "original.wav";original.write_bytes(b"user audio")
    failed_path = tmp_path / "failed" / "episode.json"

    def fail(*args, **kwargs):
        cache.touch_media_tree(media.parent)
        raise ContentError("mock API interruption")

    monkeypatch.setattr("podcast_scribe.transcribe.transcribe_audio", fail)
    assert main(["transcribe", str(original), "--output", str(failed_path), "--cache", str(root)]) == 2
    assert not failed_path.exists() and cache._sidecar(failed_path).exists()
    assert not cache.cleanup_episode(path, apply=True)["files"] and media.exists()
    assert original.read_bytes() == b"user audio"
