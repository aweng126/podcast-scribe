"""Public sharing is explicit, bounded and independent of private storage."""
from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from podcast_scribe.cli import main
from podcast_scribe.model import ContentError, new_episode, save_episode, validate_episode
from podcast_scribe.share import (
    MAX_SUBMISSION_BYTES, canonical_bytes, issue_url, load_submission,
    loads_submission, make_submission, submission_digest, submission_episode,
    validate_submission,
)
from podcast_scribe.transcripts import normalize_segments


@pytest.fixture
def reviewed():
    segments, speakers = normalize_segments([
        {"start": 0, "end": 4, "speaker": "A", "text": "PRIVATE_RAW_TEXT"},
        {"start": 4, "end": 8, "speaker": "B", "text": "原始第二段"},
    ])
    ep = new_episode({"id": "public-test", "title": "校对测试",
                      "source": {"platform": "web", "url": "https://example.org/episode", "author": "原作者"}},
                     segments, speakers, series_id="interviews", series_title="访谈")
    for segment in ep["segments"]:
        segment.update(text="整理后的公开正文。", review_status="reviewed")
    ep["summary"] = ["公开摘要。"]
    ep["chapters"] = [{"id": "c1", "title": "开场", "start": 0, "segment_id": segments[0]["id"]}]
    ep["review"] = {"speakers_confirmed": True, "content_checked": True}
    ep["artifacts"] = {"markdown": "/private/home/sensitive.md"}
    ep["history"] = "/private/history"
    ep["source"]["token"] = "PRIVATE_SOURCE_TOKEN"
    ep["speakers"][0]["private_note"] = "PRIVATE_SPEAKER_NOTE"
    return ep


@pytest.fixture
def submission(reviewed):
    return make_submission(reviewed, attribution="测试投稿者")


def test_share_whitelist_preserves_public_content_without_mutating_local_record(reviewed):
    before = deepcopy(reviewed)
    public = make_submission(reviewed, attribution="测试投稿者")
    payload = canonical_bytes(public).decode()
    for private in ("PRIVATE_RAW_TEXT", "PRIVATE_SOURCE_TOKEN", "PRIVATE_SPEAKER_NOTE", "/private/",
                    '"artifacts"', '"history"', '"raw_text"', '"status"', '"is_demo"'):
        assert private not in payload
    assert public["episode"]["segments"][0]["text"] == reviewed["segments"][0]["text"]
    assert public["episode"]["source"]["url"] == reviewed["source"]["url"]
    assert reviewed == before and reviewed["status"] == "draft"
    public["episode"]["summary"].append("独立修改")
    assert reviewed == before


@pytest.mark.parametrize("change", [
    lambda ep: ep.update(is_demo=True),
    lambda ep: ep["source"].update(platform="demo"),
    lambda ep: ep["source"].update(url=""),
    lambda ep: ep["review"].update(content_checked=False),
    lambda ep: ep["review"].update(speakers_confirmed=False),
    lambda ep: ep["segments"][0].update(review_status="edited"),
    lambda ep: ep["segments"][0].update(speaker_id=None),
    lambda ep: ep.update(chapters=[]),
    lambda ep: ep.update(summary=[]),
])
def test_share_requires_reviewed_real_content_with_source(reviewed, change):
    change(reviewed)
    with pytest.raises(ContentError):
        make_submission(reviewed, attribution="投稿者")


@pytest.mark.parametrize("attribution", [None, "", "  ", True, ["A"], "A" * 201, "A\nB"])
def test_share_requires_valid_attribution(reviewed, attribution):
    with pytest.raises(ContentError):
        make_submission(reviewed, attribution=attribution)


@pytest.mark.parametrize("path", [
    (), ("episode",), ("episode", "source"), ("episode", "series"), ("episode", "review"),
    ("episode", "speakers", 0), ("episode", "segments", 0), ("episode", "chapters", 0),
    ("episode", "references", 0),
])
def test_unknown_nested_fields_are_rejected(submission, path):
    submission["episode"]["references"] = [{"title": "核验资料", "url": "https://example.org/verify"}]
    target = submission
    for key in path:
        target = target[key]
    target["artifacts"] = {"markdown": "/etc/passwd"}
    with pytest.raises(ContentError, match="字段必须且只能"):
        validate_submission(submission)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True),
    (("episode", "title"), []),
    (("episode", "duration_seconds"), True),
    (("episode", "duration_seconds"), float("nan")),
    (("episode", "duration_seconds"), 10 ** 400),
    (("episode", "duration_seconds"), -1),
    (("episode", "duration_seconds"), 2),
    (("episode", "id"), "../../escape"),
    (("episode", "published_at"), "yesterday"),
    (("episode", "review", "content_checked"), 1),
    (("episode", "speakers", 0, "name"), {"text": "name"}),
    (("episode", "segments", 0, "text"), None),
    (("episode", "segments", 0, "start"), -1),
    (("episode", "segments", 0, "end"), -1),
    (("episode", "segments", 0, "speaker_id"), "unknown"),
    (("episode", "segments", 0, "review_status"), "unreviewed"),
    (("episode", "segments", 1, "start"), 9),
    (("episode", "segments", 1, "id"), "seg-00001"),
    (("episode", "chapters", 0, "segment_id"), "unknown"),
    (("episode", "chapters", 0, "start"), 2),
    (("episode", "summary"), [None]),
    (("episode", "references"), [{"title": "bad", "url": "javascript:alert(1)"}]),
])
def test_public_schema_rejects_malformed_fields_and_cross_references(submission, path, value):
    target = submission
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ContentError):
        validate_submission(submission)


@pytest.mark.parametrize("url", [
    "", "file:///private/audio.mp3", "javascript:alert(1)", "data:text/html,hello",
    "https://user:password@example.org/", "https://example.org\\@evil.org/",
    "https://example.org/a b", "https://example.org\n/", "https:///no-host",
    "https://example.org:bad/", "https://[bad/", "https://example.org/" + "a" * 2048,
])
def test_unsafe_or_invalid_source_links_are_rejected(submission, url):
    submission["episode"]["source"]["url"] = url
    with pytest.raises(ContentError):
        validate_submission(submission)


def test_bounded_loading_rejects_oversize_duplicate_and_invalid_encoding(submission, tmp_path, monkeypatch):
    monkeypatch.setattr("podcast_scribe.share.MAX_SUBMISSION_BYTES", 4096)
    path = tmp_path / "public.json"
    path.write_bytes(b" " * 4097)
    with pytest.raises(ContentError, match="512 MiB"):
        load_submission(path)
    with pytest.raises(ContentError, match="重复字段"):
        loads_submission(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(ContentError, match="JSON"):
        loads_submission(b"\xff")
    with pytest.raises(ContentError):
        loads_submission(b"[" * 2000)
    path.write_bytes(canonical_bytes(submission))
    assert load_submission(path) == submission


def test_count_and_total_size_limits(submission, monkeypatch):
    submission["episode"]["references"] = [{"title": "ref", "url": "https://example.org"}] * 201
    with pytest.raises(ContentError, match="references"):
        validate_submission(submission)
    submission["episode"]["references"] = []
    first = submission["episode"]["segments"][0]
    submission["episode"]["segments"] = [dict(first, id=f"s{i}", text="x" * 50000) for i in range(45)]
    submission["episode"]["chapters"][0]["segment_id"] = "s0"
    # Existing >2 MiB works under the new real production limit.
    validate_submission(submission)
    assert MAX_SUBMISSION_BYTES == 512 * 1024 * 1024
    monkeypatch.setattr("podcast_scribe.share.MAX_SUBMISSION_BYTES", 2 * 1024 * 1024)
    with pytest.raises(ContentError, match="512 MiB"):
        validate_submission(submission)


def test_canonical_digest_ignores_object_order_and_renderer_is_isolated(submission):
    reordered = json.loads(json.dumps(submission, sort_keys=True))
    assert submission_digest(submission) == submission_digest(reordered)
    before = deepcopy(submission)
    ep = submission_episode(submission)
    validate_episode(ep, for_publication=True)
    assert ep["status"] == "published" and ep["artifacts"] == {}
    assert ep["segments"][0]["raw_text"] == submission["episode"]["segments"][0]["text"]
    ep["segments"][0]["text"] = "renderer changed"
    assert submission == before
    assert "raw_text" not in submission["episode"]["segments"][0]
    reordered["episode"]["summary"][0] = "正文更改后校验值必须改变"
    assert submission_digest(reordered) != submission_digest(submission)


def test_issue_link_carries_metadata_but_never_transcript(submission):
    url = issue_url(submission)
    parts = urlsplit(url)
    fields = parse_qs(parts.query)
    assert parts.netloc == "github.com" and parts.path == "/aweng126/podcast-scribe/issues/new"
    assert fields["template"] == ["share.yml"]
    assert fields["source_url"] == [submission["episode"]["source"]["url"]]
    assert fields["attribution"] == [submission["attribution"]]
    assert fields["content_digest"] == [submission_digest(submission)]
    assert "正文" not in url and "attachment" not in fields and "body" not in fields
    submission["episode"]["title"] = "长" * 300
    submission["episode"]["source"]["url"] = "https://example.org/" + "长" * 600
    submission["attribution"] = "名" * 200
    assert len(issue_url(submission)) <= 1800
    with pytest.raises(ContentError, match="repository"):
        issue_url(submission, repository="https://evil.org/repo")


def test_cli_requires_explicit_public_confirmation_and_never_overwrites(reviewed, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    episode_path = tmp_path / "private" / "episode.json"
    save_episode(episode_path, reviewed)
    before = episode_path.read_bytes()
    args = ["share", str(episode_path), "--attribution", "投稿者"]
    assert main(args) == 2
    assert "--confirm-public" in capsys.readouterr().err
    output_path = tmp_path / "output/share/public-test.json"
    assert not output_path.exists()
    assert main([*args, "--confirm-public"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["file"] == str(output_path)
    assert report["sha256"] == submission_digest(load_submission(output_path))
    assert "尚未提交" in report["message"]
    first = output_path.read_bytes()
    assert main([*args, "--confirm-public"]) == 2
    assert "文件已存在" in capsys.readouterr().err
    assert output_path.read_bytes() == first
    assert episode_path.read_bytes() == before
    assert not (episode_path.parent / "history").exists()
    assert main([*args, "--confirm-public", "--output", str(episode_path)]) == 2
    assert episode_path.read_bytes() == before
