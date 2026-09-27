"""Subtitle acquisition contracts, using synthetic text and mocked HTTP only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from yt_dlp.extractor.bilibili import BiliBiliIE
from yt_dlp.utils import DownloadError

from podcast_scribe import sources
from podcast_scribe.model import ContentError
from podcast_scribe.source_auth import CookieAccessError


BV = "BV1GZbT6UE7o"
URL = f"https://www.bilibili.com/video/{BV}"
SRT = "1\n00:00:00,200 --> 00:00:02,000\n独立字幕。\n"


@pytest.fixture
def provider(monkeypatch):
    state = {"options": [], "requests": [], "extracts": 0, "auth": 0,
             "info": {"id": BV, "title": "模拟视频", "duration": 20, "subtitles": {}},
             "view": {"code": 0, "data": {"bvid": BV, "pages": [
                 {"page": 1, "cid": 101}, {"page": 2, "cid": 202}]}},
             "player": {"code": 0, "data": {"subtitle": {"subtitles": []}}},
             "text": json.dumps({"body": [{"from": 0.2, "to": 2, "content": "模拟字幕。"}]}),
             "web_error": None}

    class Downloader:
        def __init__(self, options):
            state["options"].append(options)
            self.params = {**options, "logger": sources.QuietLogger()}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            assert download is False
            state["extracts"] += 1
            if state["web_error"]:
                raise state["web_error"]
            if state.get("preview"):
                self.params["logger"].warning("only the preview will be extracted")
            return deepcopy(state["info"])

    def api(_extractor, endpoint, video_id, **kwargs):
        state["requests"].append((endpoint, kwargs.get("query", {})))
        if state.get("api_error"):
            raise state["api_error"]
        return deepcopy(state["view"] if endpoint.endswith("/view") else state["player"])

    def webpage(_extractor, url, video_id, **kwargs):
        state["requests"].append((url, {}))
        if state.get("text_error"):
            raise state["text_error"]
        return state["text"]

    monkeypatch.setattr(sources, "_ydl", lambda extra=None: Downloader(extra or {}))
    monkeypatch.setattr(BiliBiliIE, "_download_json", api)
    monkeypatch.setattr(BiliBiliIE, "_download_webpage", webpage)
    return state


def track(language="zh-CN", **kwargs):
    return {"lan": language, "subtitle_url": "https://aisubtitle.hdslb.com/text.json?signed=private", **kwargs}


def test_webpage_subtitle_switches_chinese_preference_and_unknown_origin(provider, tmp_path):
    provider["info"]["subtitles"] = {
        "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/101.xml"}],
        "en": [{"ext": "srt", "data": SRT.replace("独立字幕", "English")}],
        "zh-CN": [{"ext": "srt", "data": SRT}],
        "ai-zh": [{"ext": "srt", "data": SRT}],
    }
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["schema_version"] == 1 and result["status"] == "available"
    assert result["selected_track"]["language"] == "zh-CN"
    assert result["selected_track"]["origin"] == "unknown"
    assert [item["origin"] for item in result["tracks"]] == ["unknown", "unknown", "automatic"]
    assert result["cues"] == [{"id": "cue-00001", "start": 0.2, "end": 2.0, "text": "独立字幕。"}]
    assert provider["requests"] == []
    assert provider["options"][0]["writesubtitles"] and provider["options"][0]["writeautomaticsub"]
    assert "-danmaku" in provider["options"][0]["subtitleslangs"]


def test_explicit_origins_and_automatic_captions(provider, tmp_path):
    provider["info"]["subtitles"] = {"zh": [{"ext": "srt", "data": SRT, "is_auto": False}]}
    provider["info"]["automatic_captions"] = {"en": [{"ext": "srt", "data": SRT}]}
    result = sources.fetch_subtitles(URL, tmp_path)
    assert [item["origin"] for item in result["tracks"]] == ["manual", "automatic"]


def test_automatic_flag_and_unknown_human_provenance(provider, tmp_path):
    provider["player"]["data"]["subtitle"]["subtitles"] = [track(ai_type=1), track("zh-TW", ai_type=0)]
    result = sources.fetch_subtitles(URL, tmp_path)
    assert [item["origin"] for item in result["tracks"]] == ["automatic", "unknown"]


def test_short_link_resolved_page_and_extractor_id_are_preserved(provider, tmp_path):
    provider["info"].update(id=BV + "_p2", webpage_url=URL + "?p=2", subtitles={"zh": [{"ext": "srt", "data": SRT}]})
    result = sources.fetch_subtitles("https://b23.tv/example", tmp_path)
    assert result["status"] == "available" and result["source"]["page"] == 2
    assert result["source"]["video_id"] == BV
    assert sources.fetch_subtitles("https://b23.tv/example", tmp_path) == result
    assert provider["extracts"] == 1


def test_failed_webpage_uses_subtitle_only_api_and_requested_page(provider, tmp_path):
    provider["web_error"] = DownloadError("HTTP Error 412")
    provider["player"]["data"]["subtitle"]["subtitles"] = [track("ai-zh")]
    result = sources.fetch_subtitles(URL + "?p=2&tracking=private", tmp_path)
    assert result["status"] == "available" and result["source"]["cid"] == 202
    assert provider["requests"][1][1] == {"bvid": BV, "cid": 202}
    saved = (tmp_path / "subtitles.json").read_text()
    assert "private" not in saved and "hdslb.com" not in saved and "tracking" not in saved
    assert not any("playurl" in endpoint for endpoint, _ in provider["requests"])


def test_only_explicit_successful_empty_list_is_no_subtitles(provider, tmp_path):
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "no_subtitles"
    assert len(provider["requests"]) == 2 and "画面字幕尚未检查" in result["reason"]


@pytest.mark.parametrize("state", [-4, -403, 1])
def test_restricted_metadata_state_stops_before_player_or_subtitle_request(provider, tmp_path, state):
    provider["view"]["data"]["state"] = state
    provider["player"]["data"]["subtitle"]["subtitles"] = [track()]
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "unavailable" and result["cues"] == []
    assert "视频状态受限" in result["reason"]
    assert len(provider["requests"]) == 1
    assert provider["requests"][0][0].endswith("/x/web-interface/view")


@pytest.mark.parametrize("player,status", [
    ({"code": -101}, "login_required"),
    ({"code": -403}, "unavailable"),
    ({"code": 0, "data": {"need_login_subtitle": True}}, "login_required"),
    ({"code": 0, "data": {}}, "unavailable"),
    ({"data": {"subtitle": {"subtitles": []}}}, "unavailable"),
])
def test_login_and_incomplete_query_are_not_empty_subtitles(provider, tmp_path, player, status):
    provider["player"] = player
    assert sources.fetch_subtitles(URL, tmp_path)["status"] == status


def test_existing_success_cache_reused_without_reading_login_state(provider, tmp_path):
    provider["player"]["data"]["subtitle"]["subtitles"] = [track()]
    result = sources.fetch_subtitles(URL, tmp_path)
    class Auth:
        def attach(self, _): pytest.fail("cache must not read cookies")
    assert sources.fetch_subtitles(URL, tmp_path, auth=Auth()) == result
    assert provider["extracts"] == 1


def test_refresh_failed_states_identity_and_hash_force_new_query(provider, tmp_path):
    provider["api_error"] = DownloadError("connection timed out signed=private")
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "unavailable" and "private" not in json.dumps(result)
    del provider["api_error"]
    assert sources.fetch_subtitles(URL, tmp_path)["status"] == "no_subtitles"
    assert provider["extracts"] == 2
    sources.fetch_subtitles(URL, tmp_path, refresh=True)
    assert provider["extracts"] == 3
    value = json.loads((tmp_path / "subtitles.json").read_text())
    value["reason"] = "modified without hash"
    (tmp_path / "subtitles.json").write_text(json.dumps(value))
    sources.fetch_subtitles(URL, tmp_path)
    assert provider["extracts"] == 4
    sources.fetch_subtitles(URL + "?p=2", tmp_path)
    assert provider["extracts"] == 5


def test_authorization_change_rechecks_previously_empty_anonymous_list(provider, tmp_path):
    assert sources.fetch_subtitles(URL, tmp_path)["status"] == "no_subtitles"
    class Auth:
        def attach(self, _): provider["auth"] += 1
    sources.fetch_subtitles(URL, tmp_path, auth=Auth())
    assert provider["auth"] == 1 and provider["extracts"] == 2


def test_invalid_cache_structure_requeries_even_with_valid_hash(provider, tmp_path):
    result = sources.fetch_subtitles(URL, tmp_path)
    result["tracks"] = [5]
    result["content_sha256"] = sources._subtitle_hash(result)
    (tmp_path / "subtitles.json").write_text(json.dumps(result))
    assert sources.fetch_subtitles(URL, tmp_path)["status"] == "no_subtitles"
    assert provider["extracts"] == 2


def test_explicit_auth_failure_is_safe_and_does_not_continue_anonymously(provider, tmp_path):
    class Auth:
        def attach(self, _): raise CookieAccessError("/private/location secret-cookie")
    result = sources.fetch_subtitles(URL, tmp_path, auth=Auth())
    assert result["status"] == "login_required"
    assert provider["extracts"] == 0 and provider["requests"] == []
    assert "private" not in json.dumps(result) and "secret-cookie" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["login", "preview", "paid"])
def test_access_restrictions_never_fetch_text(provider, tmp_path, kind):
    if kind == "login": provider["web_error"] = DownloadError("Login required")
    elif kind == "preview": provider["preview"] = True
    else: provider["view"]["data"]["rights"] = {"ugc_pay": 1}
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "login_required" and result["cues"] == []
    assert not any("hdslb.com" in endpoint for endpoint, _ in provider["requests"])


@pytest.mark.parametrize("text", [
    '{"body":[{"from":-1,"to":2,"content":"bad"}]}',
    '{"body":[{"from":3,"to":2,"content":"bad"}]}',
    '{"body":[{"from":0,"to":NaN,"content":"bad"}]}',
    '{"body":[{"from":0,"to":2,"content":""}]}',
    '{"body":[]}',
])
def test_invalid_cues_do_not_become_success_or_no_subtitles(provider, tmp_path, text):
    provider["player"]["data"]["subtitle"]["subtitles"] = [track()]
    provider["text"] = text
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "unavailable" and result["cues"] == []


def test_track_downloads_are_bounded_and_do_not_accept_unrelated_hosts(provider, tmp_path):
    provider["player"]["data"]["subtitle"]["subtitles"] = [track() for _ in range(12)]
    provider["text_error"] = DownloadError("HTTP 403 signed=private")
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "unavailable" and len(provider["requests"]) == 6
    provider["requests"] = []
    provider["player"]["data"]["subtitle"]["subtitles"] = [track(subtitle_url="https://unrelated.test/credentials")]
    result = sources.fetch_subtitles(URL, tmp_path)
    assert result["status"] == "unavailable" and len(provider["requests"]) == 2


def test_inspect_excludes_danmaku_and_does_not_claim_empty_means_absent(provider):
    provider["info"]["subtitles"] = {"danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/101.xml"}]}
    result = sources.inspect_source(URL)
    assert result["subtitle_languages"] == [] and result["subtitle_status"] == "not_checked"
    assert provider["options"][0]["writesubtitles"]


def test_inspect_normalizes_provider_page_id_once(provider):
    provider["info"]["id"] = BV + "_p2"
    result = sources.inspect_source(URL + "?p=2")
    assert result["id"] == BV + "-p2" and result["source"]["video_id"] == BV


def test_inspect_recovers_when_optional_subtitle_fetch_breaks_metadata(provider, monkeypatch):
    class Downloader:
        params = {"writesubtitles": True, "logger": sources.QuietLogger()}
        def extract_info(self, _url, download):
            if self.params["writesubtitles"]:
                raise DownloadError("subtitle connection timed out")
            return {"id": BV, "title": "metadata survives"}
    downloader = Downloader()
    assert sources._extract_single(downloader, URL)["title"] == "metadata survives"
    assert downloader.params["writesubtitles"] is True and provider["requests"] == []


def test_video_download_is_explicit_separate_low_resolution_and_cached(provider, tmp_path, monkeypatch):
    calls = []
    def extract(ydl, url, *, audio_only=True):
        assert audio_only is False
        assert "height<=480" in ydl.params["format"]
        assert "source-video" in ydl.params["outtmpl"]
        return {"id": BV, "formats": [{"vcodec": "none", "url": "audio"}, {"vcodec": "avc1", "url": "video"}]}
    def download(_ydl, info):
        calls.append(info)
        assert [item["url"] for item in info["formats"]] == ["video"]
        path = tmp_path / "source-video.mp4"
        path.write_bytes(b"synthetic complete video")
        return {"requested_downloads": [{"filepath": str(path)}]}
    monkeypatch.setattr(sources, "_extract_single", extract)
    monkeypatch.setattr(sources, "_download_audio", download)
    # prepare_filename is required even when requested_downloads has a path.
    original = sources._ydl
    def downloader(extra):
        instance = original(extra)
        instance.prepare_filename = lambda info: str(tmp_path / "source-video.mp4")
        return instance
    monkeypatch.setattr(sources, "_ydl", downloader)
    path = sources.fetch_video(URL, tmp_path)
    class Auth:
        def attach(self, _): pytest.fail("cached video must not read cookies")
    assert sources.fetch_video(URL, tmp_path, auth=Auth()) == path and len(calls) == 1
    assert not (tmp_path / "source.m4a").exists()
    path.write_bytes(b"modified")
    sources.fetch_video(URL, tmp_path)
    assert len(calls) == 2
    sources.fetch_video(URL + "?p=2", tmp_path)
    assert len(calls) == 3


def test_video_audio_only_result_is_rejected(provider, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_extract_single", lambda *args, **kwargs: {"formats": [{"vcodec": "none"}]})
    with pytest.raises(ContentError, match="视频轨"):
        sources.fetch_video(URL, tmp_path)


def test_public_video_fallback_preserves_video_formats_and_backups(provider, monkeypatch):
    provider["view"]["data"]["pages"][0]["duration"] = 20
    primary = "https://media.example.test/video?private=1"
    backup = "https://media.example.test/video?private=2"
    play = {"timelength": 20000, "dash": {"video": [{"baseUrl": primary, "backupUrl": [backup]}]}}
    monkeypatch.setattr(BiliBiliIE, "_download_playinfo", lambda *args, **kwargs: play)
    monkeypatch.setattr(BiliBiliIE, "extract_formats", lambda *args: [
        {"url": "audio", "vcodec": "none"}, {"url": primary, "vcodec": "avc1", "height": 360}])
    with sources._ydl() as ydl:
        result = sources._public_api_info(ydl, URL, audio_only=False)
    assert len(result["formats"]) == 2
    assert result["formats"][1]["_podcast_scribe_backup_urls"] == [backup]
