"""Series attribution is metadata, and must not redo or bless transcript work."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.cli import main
from podcast_scribe.model import load_episode, new_episode, save_episode
from podcast_scribe.share import make_submission
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "series.json"
    path.write_text(json.dumps({"schema_version": 1, "series": [{
        "id": "sample-show", "title": "示例访谈", "description": "节目说明",
        "aliases": ["示例对话"],
        "sources": [{"title": "官方节目页", "url": "https://example.com/show"}],
    }]}, ensure_ascii=False))
    return path


@pytest.fixture
def episode(tmp_path):
    segments, speakers = normalize_segments([{
        "start": 0, "end": 10, "speaker": "A", "text": "完整的对话。",
    }])
    ep = new_episode({"id": "sample", "title": "节目单集", "source": {
        "platform": "web", "url": "https://example.com/episode", "author": "上传者",
    }}, segments, speakers, series_id="inbox", series_title="待归类")
    for segment in ep["segments"]:
        segment["review_status"] = "reviewed"
    ep["review"] = {"speakers_confirmed": True, "content_checked": True,
                    "mode": "auto", "basis": "user_accepted"}
    ep["summary"] = ["摘要"]
    ep["chapters"] = [{"id": "c1", "title": "开场", "start": 0,
                       "segment_id": ep["segments"][0]["id"]}]
    ep["references"] = [{"title": "原节目", "url": "https://example.com/episode"}]
    ep["artifacts"] = {"markdown": "old.md"}
    path = tmp_path / "episode.json"
    save_episode(path, ep)
    return path


def test_classify_reuses_alias_keeps_review_and_history_and_can_share(episode, catalog, capsys):
    before = load_episode(episode)
    command = ["classify", str(episode), "--series-title", "示例对话", "--catalog", str(catalog),
               "--evidence-url", "https://example.com/episode"]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    after = load_episode(episode)
    assert result["changed"] is True and result["series"]["id"] == "sample-show"
    assert after["series"] == {"id": "sample-show", "title": "示例访谈", "description": "节目说明"}
    for key in ("segments", "speakers", "review", "status", "summary", "chapters", "source"):
        assert after[key] == before[key]
    assert after["artifacts"] == {} and after["revision"] == before["revision"] + 1
    history = episode.parent / "history" / f"sample-r{before['revision']}.json"
    assert json.loads(history.read_text()) == before
    assert after["references"][0] == before["references"][0]
    assert make_submission(after, attribution="测试用户")["episode"]["series"] == after["series"]
    saved = episode.read_bytes()
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False
    assert episode.read_bytes() == saved


@pytest.mark.parametrize("options", [[], ["--series-id", "sample-show"],
    ["--series-title", "示例访谈", "--evidence-url", "file:///private/file"],
    ["--unclassified", "--series-title", "示例访谈"],
    ["--series-id", "different", "--series-title", "示例访谈", "--evidence-url", "https://example.com"]])
def test_invalid_classification_never_modifies_episode(episode, catalog, options):
    before = episode.read_bytes()
    assert main(["classify", str(episode), "--catalog", str(catalog), *options]) == 2
    assert episode.read_bytes() == before
    assert not (episode.parent / "history").exists()


def test_unclassified_removes_only_previous_classification_reference(episode, catalog, capsys):
    assert main(["classify", str(episode), "--series-id", "sample-show", "--catalog", str(catalog),
                 "--evidence-url", "https://example.com/episode"]) == 0
    classified = load_episode(episode)
    assert main(["classify", str(episode), "--unclassified"]) == 0
    after = load_episode(episode)
    assert after["series"] == {"id": "inbox", "title": "未分类", "description": ""}
    assert after["references"] == [{"title": "原节目", "url": "https://example.com/episode"}]
    assert after["review"] == classified["review"]


def test_unfinished_draft_does_not_become_reviewed(episode, catalog):
    ep = load_episode(episode)
    ep["review"] = {"speakers_confirmed": False, "content_checked": False, "mode": "auto"}
    ep["segments"][0]["review_status"] = "unreviewed"
    save_episode(episode, ep)
    assert main(["classify", str(episode), "--series-title", "示例对话", "--catalog", str(catalog),
                 "--evidence-url", "https://example.com/episode"]) == 0
    after = load_episode(episode)
    assert after["review"] == ep["review"] and after["segments"] == ep["segments"]


def test_import_resolves_name_and_default_does_not_guess_from_title(tmp_path, monkeypatch, catalog, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "示例访谈.json"
    source.write_text(json.dumps([{"start": 0, "end": 3, "text": "这是完整文字。"}]))
    assert main(["import", str(source), "--output", "plain.json"]) == 0
    assert load_episode(tmp_path / "plain.json")["series"] == {
        "id": "inbox", "title": "未分类", "description": ""}
    assert main(["import", str(source), "--output", "named.json", "--series-title", "示例对话",
                 "--series-catalog", str(catalog)]) == 0
    assert load_episode(tmp_path / "named.json")["series"]["id"] == "sample-show"


def test_invalid_series_fails_before_paid_transcription(tmp_path, monkeypatch):
    from podcast_scribe import transcribe
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"mock audio")
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid classification must not call paid transcription")
    monkeypatch.setattr(transcribe, "transcribe_audio", unexpected)
    assert main(["transcribe", str(source), "--series-id", "unknown-without-title",
                 "--output", str(tmp_path / "result.json")]) == 2
    assert not (tmp_path / "result.json").exists()


def test_read_only_catalog_and_status(episode, catalog, capsys):
    before = episode.read_bytes()
    assert main(["series-list", "--catalog", str(catalog)]) == 0
    assert json.loads(capsys.readouterr().out)["series"][0]["aliases"] == ["示例对话"]
    assert main(["status", str(episode)]) == 0
    assert json.loads(capsys.readouterr().out)["series"]["title"] == "未分类"
    assert episode.read_bytes() == before


def test_legacy_display_in_markdown_and_offline_site_does_not_edit_record(episode, tmp_path):
    from podcast_scribe.exporters import render_markdown
    from podcast_scribe.site import build_site
    original = load_episode(episode)
    before = deepcopy(original)
    markdown = render_markdown(original)
    html = build_site([original], tmp_path / "site", include_drafts=True).read_text()
    assert "未分类" in markdown and "待归类" not in markdown
    assert "未分类" in html and "待归类" not in html
    assert original == before
