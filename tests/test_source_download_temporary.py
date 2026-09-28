"""Downloader leftovers belong to one fresh transfer, never to an old glob."""
import json
from pathlib import Path

import pytest

from podcast_scribe import cache_lifecycle as lifecycle
from podcast_scribe import sources
from podcast_scribe.model import ContentError

URL = "https://www.bilibili.com/video/BV1GZbT6UE7o"


@pytest.fixture(params=["audio", "video"])
def transfer(request, monkeypatch, tmp_path):
    kind = request.param
    work = tmp_path / "cache"
    work.mkdir()
    state = {"mode": "success", "downloads": [], "staging": []}
    stem, extension = ("source", "m4a") if kind == "audio" else ("source-video", "mp4")
    external = tmp_path / f"{stem}.{extension}"
    external.write_bytes(b"user's original file")

    class Downloader:
        def __init__(self, options): self.params = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def prepare_filename(self, info):
            return self.params["outtmpl"] % {"ext": extension}

    def download(ydl, info):
        path = Path(ydl.prepare_filename(info))
        state["downloads"].append(path)
        state["staging"].append(path.parent)
        assert path.parent.parent == work
        (path.parent / "fragment.part").write_bytes(b"new download fragment")
        if state["mode"] == "error":
            Path(str(path) + ".part").write_bytes(b"new incomplete media")
            raise RuntimeError("simulated download interruption")
        if state["mode"] == "incomplete":
            Path(str(path) + ".part").write_bytes(b"new incomplete media")
        elif state["mode"] == "outside":
            path = external
        elif state["mode"] == "symlink":
            path.symlink_to(external)
        else:
            path.write_bytes(b"completed new media")
            if state["mode"] == "collision":
                (work / path.name).write_bytes(b"another caller's file")
        return {"requested_downloads": [{"filepath": str(path)}]}

    monkeypatch.setattr(sources, "_ydl", Downloader)
    monkeypatch.setattr(sources, "_extract_single", lambda *args, **kwargs: {"formats": [{"vcodec": "avc1"}]})
    monkeypatch.setattr(sources, "_download_audio", download)
    state.update(work=work, external=external, stem=stem, extension=extension,
                 fetch=sources.fetch_audio if kind == "audio" else sources.fetch_video, kind=kind)
    return state


@pytest.mark.parametrize("mode", ["error", "incomplete", "outside", "symlink"])
def test_failed_download_removes_only_this_transfers_residuals(transfer, mode):
    state, work = transfer, transfer["work"]
    state["mode"] = mode
    old_partial = work / f"{state['stem']}.{state['extension']}.part"
    old_partial.write_bytes(b"old partial with unknown ownership")
    saved_response = work / "transcription.json"
    saved_response.write_text('{"segments": []}', encoding="utf-8")
    old_directory = work / ".media-download-prior"
    old_directory.mkdir()
    (old_directory / "source.m4a.part").write_bytes(b"unknown prior transfer")
    with pytest.raises(ContentError):
        state["fetch"](URL, work)
    assert state["staging"] and all(not path.exists() for path in state["staging"])
    assert old_partial.read_bytes() == b"old partial with unknown ownership"
    assert saved_response.read_bytes() == b'{"segments": []}'
    assert state["external"].read_bytes() == b"user's original file"
    assert (old_directory / "source.m4a.part").read_bytes() == b"unknown prior transfer"
    assert set(work.iterdir()) == {old_partial, saved_response, old_directory}


def test_success_promotes_and_registers_only_completed_media_then_reuses_it(transfer):
    state, work = transfer, transfer["work"]
    with lifecycle.media_session(work, protected=[state["external"]]):
        path = state["fetch"](URL, work)
        assert path == work / f"{state['stem']}.{state['extension']}"
        assert path.read_bytes() == b"completed new media"
        assert state["fetch"](URL, work) == path
    assert len(state["downloads"]) == 1
    assert all(not path.exists() for path in state["staging"])
    index = json.loads((work / lifecycle.INDEX).read_text())
    assert list(index["files"]) == [path.name]
    assert state["external"].read_bytes() == b"user's original file"


def test_old_partial_marker_and_old_media_survive_and_new_result_is_reusable(transfer):
    state, work = transfer, transfer["work"]
    old_media = work / f"{state['stem']}.{state['extension']}"
    old_media.write_bytes(b"old media with incomplete marker")
    old_partial = Path(str(old_media) + ".part")
    old_partial.write_bytes(b"unowned partial")
    path = state["fetch"](URL, work)
    assert path != old_media and path.name.startswith(state["stem"] + ".")
    assert path.read_bytes() == b"completed new media"
    assert old_media.read_bytes() == b"old media with incomplete marker"
    assert old_partial.read_bytes() == b"unowned partial"
    assert state["fetch"](URL, work) == path
    assert len(state["downloads"]) == 1
    assert all(not path.exists() for path in state["staging"])


def test_final_publish_never_overwrites_a_file_created_by_another_caller(transfer):
    state, work = transfer, transfer["work"]
    state["mode"] = "collision"
    with lifecycle.media_session(work):
        result = state["fetch"](URL, work)
    caller_file = work / f"{state['stem']}.{state['extension']}"
    assert caller_file.read_bytes() == b"another caller's file"
    assert result != caller_file and result.read_bytes() == b"completed new media"
    assert result.stat().st_nlink == 1
    index = json.loads((work / lifecycle.INDEX).read_text())
    assert list(index["files"]) == [result.name]
    assert all(not path.exists() for path in state["staging"])


@pytest.mark.parametrize("transfer", ["video"], indirect=True)
def test_failed_refresh_preserves_an_existing_complete_video_and_manifest(transfer):
    state, work = transfer, transfer["work"]
    path = state["fetch"](URL, work)
    manifest = work / "source-video-manifest.json"
    original_manifest = manifest.read_bytes()
    state["mode"] = "error"
    with pytest.raises(ContentError):
        state["fetch"](URL + "?p=2", work)
    assert path.read_bytes() == b"completed new media"
    assert manifest.read_bytes() == original_manifest
    assert all(not path.exists() for path in state["staging"])
