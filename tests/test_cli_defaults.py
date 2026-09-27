"""Usable workspace defaults without paid API calls or source downloads."""
import hashlib
import json
from pathlib import Path

import pytest

from podcast_scribe.cli import main, parser
from podcast_scribe.defaults import (destination_lock, local_target,
                                     resolved_video_target, save_new_episode, video_target)
from podcast_scribe.model import ContentError, load_episode, save_episode
from podcast_scribe.transcripts import normalize_segments


BV = "BV1GZbT6UE7o"
URL = f"https://www.bilibili.com/video/{BV}"
ROWS = [{"start": 0, "end": 5, "speaker": "A", "text": "完整保留这段对话。"}]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "录音.json"
    source.write_text(json.dumps({"segments": ROWS}, ensure_ascii=False), encoding="utf-8")
    return tmp_path, source


@pytest.fixture
def offline_video(monkeypatch, workspace):
    from podcast_scribe import sources, transcribe
    calls = {"inspect": [], "download": [], "transcribe": []}

    def inspect(url):
        calls["inspect"].append(url)
        target = video_target(url)
        page = target["identity"]["page"]
        return {"id": f"{BV}_p{page}" + (f"-p{page}" if page > 1 else ""),
                "title": f"测试节目 第{page}P", "duration_seconds": 10,
                "source": {"url": url, "platform": "bilibili", "video_id": f"{BV}_p{page}", "author": "作者"}}

    def fetch(url, directory):
        calls["download"].append(url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "source.mp3"
        path.write_bytes(b"mock audio, no download")
        return path

    def transcribe_audio(file, cache, *, language, metadata):
        calls["transcribe"].append((str(file), language))
        metadata["duration_seconds"] = 10
        return normalize_segments(ROWS)

    monkeypatch.setattr(sources, "inspect_source", inspect)
    monkeypatch.setattr(sources, "fetch_audio", fetch)
    monkeypatch.setattr(sources, "fetch_subtitles", lambda *args, **kwargs: {
        "schema_version": 1, "status": "no_subtitles", "source": {"kind": "bilibili"}, "cues": []})
    monkeypatch.setattr(transcribe, "transcribe_audio", transcribe_audio)
    return calls


def test_import_needs_only_a_filename_and_uses_content_identity(workspace, capsys):
    root, source = workspace
    assert main(["import", str(source)]) == 0
    path = Path(capsys.readouterr().out.strip())
    expected = local_target(source, "import")
    assert path == root / "data" / expected["id"] / "episode.json"
    ep = load_episode(path)
    assert ep["title"] == source.stem and ep["series"]["id"] == "inbox"
    assert ep["input_identity"] == expected["identity"]
    assert ep["segments"][0]["raw_text"] == ROWS[0]["text"]
    assert ep["review"] == {"speakers_confirmed": False, "content_checked": False}


def test_transcribe_needs_only_a_filename_and_keeps_default_and_override_language(workspace, offline_video, capsys):
    root, source = workspace
    assert main(["transcribe", str(source)]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path == root / "data" / local_target(source, "transcribe")["id"] / "episode.json"
    assert offline_video["transcribe"][0][1] == "zh"
    assert main(["transcribe", str(source), "--output", "custom/episode.json", "--language", "en"]) == 0
    assert offline_video["transcribe"][1][1] == "en"


def test_ingest_needs_only_url_and_forces_canonical_first_page(offline_video, workspace, capsys):
    root, _ = workspace
    assert main(["ingest", URL + "?spm_id_from=tracking"]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path == root / "data" / BV / "episode.json"
    ep = load_episode(path)
    assert ep["id"] == BV
    assert ep["source"]["url"] == URL + "?p=1"
    assert ep["source"]["video_id"] == BV
    assert ep["input_identity"] == {"kind": "bilibili", "video_id": BV, "page": 1}
    assert offline_video["inspect"] == offline_video["download"] == [URL + "?p=1"]


def test_equivalent_bv_urls_reuse_existing_draft_without_inspect_or_payment(offline_video, workspace, capsys):
    root, _ = workspace
    assert main(["ingest", BV]) == 0
    capsys.readouterr()
    path = root / "data" / BV / "episode.json"
    ep = load_episode(path)
    ep["segments"][0]["text"] = "人工修改必须保留。"
    ep["segments"][0]["review_status"] = "edited"
    save_episode(path, ep)
    original = path.read_bytes()
    for url in (URL, URL + "/?p=1&track=x", f"http://m.bilibili.com/video/{BV}?p=1"):
        assert main(["ingest", url, "--series-title", "不应覆盖旧系列"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "existing" and report["path"] == str(path)
        assert report["next_steps"] == ["status", "batch", "edit", "export"]
        assert "不会修改" in report["message"]
        assert report["requested_options"]["series_title"] == "不应覆盖旧系列"
        assert path.read_bytes() == original
    assert len(offline_video["inspect"]) == len(offline_video["download"]) == len(offline_video["transcribe"]) == 1


def test_second_page_has_distinct_stable_id_for_extractor_and_api_fallback(offline_video, workspace, capsys):
    root, _ = workspace
    assert main(["ingest", URL]) == 0
    capsys.readouterr()
    assert main(["ingest", URL + "?track=x&p=2"]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path == root / "data" / f"{BV}-p2" / "episode.json"
    assert load_episode(path)["id"] == f"{BV}-p2"
    assert offline_video["download"][-1] == URL + "?p=2"
    fallback = {"id": BV + "-p2", "source": {"url": URL + "?p=2", "video_id": BV}}
    assert resolved_video_target(URL + "?p=2", fallback)["id"] == f"{BV}-p2"


def test_legacy_bv_draft_without_identity_is_reused(offline_video, workspace, capsys):
    root, _ = workspace
    assert main(["ingest", URL]) == 0
    capsys.readouterr()
    path = root / "data" / BV / "episode.json"
    ep = load_episode(path)
    ep.pop("input_identity")
    ep["source"]["url"] = URL + "?track=old"
    save_episode(path, ep)
    original = path.read_bytes()
    assert main(["ingest", BV]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "existing"
    assert path.read_bytes() == original and len(offline_video["download"]) == 1


@pytest.mark.parametrize("field,value", [("url", URL + "?p=2"), ("video_id", BV + "_p2")])
def test_existing_bv_source_conflict_is_reported_without_network(offline_video, workspace, capsys, field, value):
    root, _ = workspace
    assert main(["ingest", BV]) == 0
    capsys.readouterr()
    path = root / "data" / BV / "episode.json"
    ep = load_episode(path)
    ep["source"][field] = value
    save_episode(path, ep)
    original = path.read_bytes()
    assert main(["ingest", BV]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["code"] == "source_conflict"
    assert path.read_bytes() == original
    assert len(offline_video["inspect"]) == len(offline_video["download"]) == 1


def test_legacy_local_draft_without_identity_and_explicit_id_collision_are_preserved(workspace, capsys):
    root, source = workspace
    assert main(["import", str(source), "--id", "one"]) == 0
    capsys.readouterr()
    path = root / "data/one/episode.json"
    ep = load_episode(path)
    ep.pop("input_identity")
    save_episode(path, ep)
    original = path.read_bytes()
    assert main(["import", str(source), "--id", "one"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error" and report["code"] == "source_conflict"
    assert path.read_bytes() == original
    ep["input_identity"] = {"kind": "import", "sha256": "other", "source_url": ""}
    save_episode(path, ep)
    assert main(["import", str(source), "--id", "one"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "source_conflict"


def test_explicit_output_refuses_overwrite_even_for_same_source(workspace, capsys, monkeypatch):
    _, source = workspace
    assert main(["import", str(source), "--output", "custom.json"]) == 0
    capsys.readouterr()
    monkeypatch.setattr("podcast_scribe.transcripts.read_transcript", lambda _: pytest.fail("must not reread transcript"))
    assert main(["import", str(source), "--output", "custom.json"]) == 2
    assert "已保留人工修改" in capsys.readouterr().err


def test_same_filename_different_content_isolated_and_id_length_bounded(workspace):
    root, source = workspace
    alternate = root / "another" / source.name
    alternate.parent.mkdir()
    alternate.write_text("different content")
    first, second = local_target(source, "import"), local_target(alternate, "import")
    assert first["id"] != second["id"]
    long_name = root / (("x" * 200) + ".json")
    long_name.write_bytes(source.read_bytes())
    target = local_target(long_name, "import")
    assert len(target["id"]) <= 100 and target["id"].endswith(hashlib.sha256(source.read_bytes()).hexdigest()[:12])


def test_export_defaults_to_episode_directory_and_keeps_pdf_default(workspace, capsys):
    root, source = workspace
    assert main(["import", str(source), "--id", "local"]) == 0
    episode = Path(capsys.readouterr().out.strip())
    assert main(["export", str(episode), "--formats", "markdown"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["markdown"] == str(root / "output/local/local.md")
    assert parser().parse_args(["export", str(episode)]).formats == ["markdown", "pdf"]


def test_metadata_bv_or_page_mismatch_rejected_before_download(offline_video, monkeypatch, capsys):
    def mismatch(url):
        return {"id": BV + "_p1-p2", "source": {"url": url, "video_id": BV + "_p1"}}
    monkeypatch.setattr("podcast_scribe.sources.inspect_source", mismatch)
    assert main(["ingest", URL + "?p=2"]) == 2
    assert "分 P 不一致" in capsys.readouterr().err
    assert not offline_video["download"] and not offline_video["transcribe"]


@pytest.mark.parametrize("metadata", [
    {"id": "288525", "source": {"url": URL, "video_id": "288525"}},
    {"id": BV, "source": {"url": URL, "video_id": "288525"}},
    {"source": {"url": URL}},
])
def test_metadata_cannot_hide_non_bv_redirect_or_absent_identity(offline_video, monkeypatch, capsys, metadata):
    monkeypatch.setattr("podcast_scribe.sources.inspect_source", lambda _: metadata)
    assert main(["ingest", URL]) == 2
    assert "BV 视频 ID" in capsys.readouterr().err
    assert not offline_video["download"] and not offline_video["transcribe"]


def test_short_link_preserves_extractor_page_or_fails_if_ambiguous():
    metadata = {"id": BV + "_p2", "source": {"url": "https://b23.tv/short", "video_id": BV + "_p2"}}
    target = resolved_video_target("https://b23.tv/short", metadata)
    assert target["id"] == BV + "-p2"
    metadata["id"] = metadata["source"]["video_id"] = BV
    with pytest.raises(ContentError, match="原始 BV"):
        resolved_video_target("https://b23.tv/short", metadata)


def test_conflicting_page_parameters_rejected():
    with pytest.raises(ContentError, match="单一的正整数"):
        video_target(URL + "?p=1&p=2")


def test_destination_lock_prevents_duplicate_transcribe(workspace, offline_video, capsys):
    root, source = workspace
    target = local_target(source, "transcribe")
    path = root / "data" / target["id"] / "episode.json"
    with destination_lock(path):
        assert main(["transcribe", str(source)]) == 2
    assert "稿件正在处理" in capsys.readouterr().err
    assert not offline_video["transcribe"]


def test_atomic_new_save_does_not_replace_file_created_after_check(workspace, capsys, monkeypatch):
    root, source = workspace
    assert main(["import", str(source), "--id", "base"]) == 0
    episode = load_episode(Path(capsys.readouterr().out.strip()))
    destination = root / "new.json"
    import podcast_scribe.defaults as defaults
    original_link = defaults.os.link
    def racing_link(src, dst):
        Path(dst).write_text("written by another editor", encoding="utf-8")
        return original_link(src, dst)
    monkeypatch.setattr(defaults.os, "link", racing_link)
    with pytest.raises(ContentError, match="已保留人工修改"):
        save_new_episode(destination, episode)
    assert destination.read_text() == "written by another editor"
    assert not list(root.glob(".new.json.*.tmp"))


def test_private_input_identity_never_enters_public_submission(workspace, capsys):
    from podcast_scribe.share import make_submission
    _, source = workspace
    assert main(["import", str(source), "--source-url", "https://example.test/source"]) == 0
    ep = load_episode(Path(capsys.readouterr().out.strip()))
    ep["review"] = {"content_checked": True, "speakers_confirmed": True}
    ep["segments"][0]["review_status"] = "reviewed"
    ep["chapters"] = [{"id": "c1", "title": "开场", "start": 0, "segment_id": "seg-00001"}]
    ep["summary"] = ["完整对话测试。"]
    public = make_submission(ep, attribution="署名")
    serialized = json.dumps(public)
    assert "input_identity" not in serialized and ep["input_identity"]["sha256"] not in serialized
