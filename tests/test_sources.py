from copy import deepcopy
from pathlib import Path

import pytest
from yt_dlp.extractor.bilibili import BiliBiliIE
from yt_dlp.utils import DownloadError

from video_to_markdown.model import ContentError
from video_to_markdown import sources


URL = "https://www.bilibili.com/video/BV1GZbT6UE7o"
PRIMARY = "https://media.example.test/audio.m4s?signed=primary-secret"
BACKUP = "https://backup.example.test/audio.m4s?signed=backup-secret"


@pytest.fixture
def provider(monkeypatch):
    state = {
        "view": {"code": 0, "data": {
            "bvid": "BV1GZbT6UE7o", "title": "公开单集", "desc": "原始简介", "pubdate": 1700000000,
            "duration": 145, "state": 0, "rights": {"pay": 0, "ugc_pay": 0}, "owner": {"name": "作者"},
            "pages": [{"page": 1, "cid": 101, "duration": 145, "part": "第一段"}],
        }},
        "play": {"code": 0, "data": {"timelength": 144637, "is_preview": None, "dash": {"audio": [{
            "id": 30280, "baseUrl": PRIMARY, "backupUrl": [BACKUP],
            "mimeType": "audio/mp4", "codecs": "mp4a.40.2", "bandwidth": 128000,
        }], "video": [{"id": 80, "baseUrl": "https://media.example.test/video", "codecs": "avc1"}]}}},
        "api_calls": [], "download_urls": [], "options": [], "download_failures": {},
        "web_error": DownloadError("HTTP Error 412: Precondition Failed"),
    }

    def api(self, endpoint, video_id, **kwargs):
        state["api_calls"].append((endpoint, kwargs.get("query", {})))
        if state.get("network_error"):
            raise state["network_error"]
        if endpoint.endswith("/x/web-interface/view"):
            return deepcopy(state["view"])
        if endpoint.endswith("/x/player/wbi/playurl"):
            return deepcopy(state["play"])
        pytest.fail(f"unexpected API endpoint: {endpoint}")

    monkeypatch.setattr(BiliBiliIE, "_download_json", api)
    monkeypatch.setattr(BiliBiliIE, "is_logged_in", property(lambda self: False))
    monkeypatch.setattr(BiliBiliIE, "_dm_params", property(lambda self: {}))
    monkeypatch.setattr(BiliBiliIE, "_sign_wbi", lambda self, params, video_id: params)

    class Downloader:
        def __init__(self, extra):
            state["options"].append(extra)
            self.params = {**extra, "outtmpl": {"default": extra.get("outtmpl", "source.%(ext)s")}}

        def __enter__(self): return self
        def __exit__(self, *args): pass

        def extract_info(self, url, download):
            assert download is False
            if state["web_error"]:
                raise state["web_error"]
            return deepcopy(state["normal_info"])

        def process_ie_result(self, info, download):
            result = deepcopy(info)
            formats = result.get("formats") or [result]
            selected = max(formats, key=lambda item: item.get("tbr", 0))
            result.update(selected)
            if download:
                state["download_urls"].append(result["url"])
                failure = state["download_failures"].get(result["url"])
                if failure:
                    raise DownloadError(f"{failure}: {result['url']}")
                destination = Path(self.prepare_filename(result))
                destination.write_bytes(b"mock complete audio")
                Path(str(destination) + ".part").unlink(missing_ok=True)
                result["requested_downloads"] = [{"filepath": str(destination)}]
            return result

        def prepare_filename(self, info):
            # Match yt-dlp's normalized outtmpl structure, not a raw string.
            return self.params["outtmpl"]["default"] % {"ext": info.get("ext", "m4a")}

    monkeypatch.setattr(sources, "_ydl", lambda extra=None: Downloader(extra or {}))
    return state


def test_webpage_failure_falls_back_to_public_metadata_and_audio(provider, tmp_path):
    metadata = sources.inspect_source(URL)
    assert metadata["id"] == "BV1GZbT6UE7o"
    assert metadata["title"] == "公开单集"
    assert metadata["duration_seconds"] == 144.637
    assert metadata["source"]["author"] == "作者"
    assert metadata["subtitle_languages"] == []
    audio = sources.fetch_audio(URL, tmp_path)
    assert audio.stat().st_size > 0
    assert provider["download_urls"] == [PRIMARY]
    assert all("cookiefile" not in options and "cookiesfrombrowser" not in options for options in provider["options"])


@pytest.mark.parametrize("code", [-101, -403, -404, -400, None])
def test_metadata_api_rejection_never_downloads(provider, tmp_path, code):
    provider["view"]["code"] = code
    with pytest.raises(ContentError, match="元数据 API 拒绝访问"):
        sources.fetch_audio(URL, tmp_path)
    assert provider["download_urls"] == []
    assert len(provider["api_calls"]) == 1


@pytest.mark.parametrize("code", [-401, -403, -10403])
def test_playback_api_must_explicitly_return_success(provider, tmp_path, code):
    provider["play"]["code"] = code
    provider["play"]["message"] = "Access denied"
    with pytest.raises(ContentError, match="公开 API 请求失败"):
        sources.fetch_audio(URL, tmp_path)
    assert provider["download_urls"] == []


@pytest.mark.parametrize("page", [1, 2])
def test_requested_page_maps_to_its_own_cid_and_duration(provider, page):
    provider["view"]["data"]["pages"].append({"page": 2, "cid": 202, "duration": 45, "part": "第二段"})
    provider["view"]["data"]["duration"] = 190
    provider["play"]["data"]["timelength"] = 145000 if page == 1 else 45000
    metadata = sources.inspect_source(URL if page == 1 else URL + "?p=2")
    assert provider["api_calls"][-1][1]["cid"] == (101 if page == 1 else 202)
    assert metadata["duration_seconds"] == (145 if page == 1 else 45)
    assert metadata["id"] == "BV1GZbT6UE7o" + ("" if page == 1 else "-p2")
    assert len(provider["api_calls"]) == 2


def test_missing_page_does_not_fall_back_to_first_page(provider, tmp_path):
    with pytest.raises(ContentError, match="未返回第 2 P"):
        sources.fetch_audio(URL + "?p=2", tmp_path)
    assert len(provider["api_calls"]) == 1
    assert provider["download_urls"] == []


@pytest.mark.parametrize("message", ["Login required", "premium member", "supporter-only video", "geo-restricted"])
def test_explicit_webpage_access_denial_does_not_use_fallback(provider, message):
    provider["web_error"] = DownloadError(message)
    with pytest.raises(ContentError, match="访问权限"):
        sources.inspect_source(URL)
    assert provider["api_calls"] == []


@pytest.mark.parametrize("flag,value", [("is_preview", True), ("need_login", 1), ("is_area_limit", True)])
def test_preview_and_restricted_playback_rejected(provider, tmp_path, flag, value):
    provider["play"]["data"][flag] = value
    with pytest.raises(ContentError, match="预览或要求访问权限"):
        sources.fetch_audio(URL, tmp_path)
    assert provider["download_urls"] == []


def test_paywalled_metadata_rejected_before_playback(provider, tmp_path):
    provider["view"]["data"]["rights"]["ugc_pay"] = 1
    with pytest.raises(ContentError, match="付费"):
        sources.fetch_audio(URL, tmp_path)
    assert len(provider["api_calls"]) == 1


@pytest.mark.parametrize("milliseconds", [30000, 1000000, 0, None, float("nan")])
def test_audio_duration_must_match_complete_selected_page(provider, tmp_path, milliseconds):
    provider["play"]["data"]["timelength"] = milliseconds
    with pytest.raises(ContentError, match="时长与所选分 P 不符"):
        sources.fetch_audio(URL, tmp_path)
    assert provider["download_urls"] == []


def test_no_audio_and_api_network_failure_have_different_diagnostics(provider):
    provider["play"]["data"]["dash"]["audio"] = []
    with pytest.raises(ContentError, match="没有可用纯音轨") as no_audio:
        sources.inspect_source(URL)
    assert "风控" in str(no_audio.value)
    provider["network_error"] = DownloadError("Connection timed out https://example.test/?token=secret")
    with pytest.raises(ContentError, match="公开 API 请求失败（无法连接") as network:
        sources.inspect_source(URL)
    assert "secret" not in str(network.value)


def test_complete_cached_audio_reused_without_network(provider, tmp_path):
    path = tmp_path / "source.m4a"
    path.write_bytes(b"complete audio")
    assert sources.fetch_audio(URL, tmp_path) == path
    assert provider["api_calls"] == []
    assert provider["options"] == []


@pytest.mark.parametrize("filename,content", [
    ("source.m4a", b""), ("source.m4a.part", b"partial"), ("source.part.m4a", b"partial"),
])
def test_empty_or_partial_cache_is_not_reused(provider, tmp_path, filename, content):
    (tmp_path / filename).write_bytes(content)
    path = sources.fetch_audio(URL, tmp_path)
    assert path.read_bytes() == b"mock complete audio"
    assert provider["download_urls"] == [PRIMARY]
    assert provider["options"][-1]["overwrites"] is True


def test_only_same_stream_provider_backup_is_tried(provider, tmp_path):
    provider["download_failures"][PRIMARY] = "HTTP Error 514: Frequency Capped"
    path = sources.fetch_audio(URL, tmp_path)
    assert path.stat().st_size > 0
    assert provider["download_urls"] == [PRIMARY, BACKUP]


def test_backup_attempts_are_bounded_and_signed_urls_are_not_in_diagnostics(provider, tmp_path):
    extra = [f"https://backup{n}.example.test/audio?signed=secret" for n in range(3)]
    provider["play"]["data"]["dash"]["audio"][0]["backupUrl"] = extra
    provider["download_failures"] = {url: "HTTP Error 514: Frequency Capped" for url in [PRIMARY, *extra]}
    with pytest.raises(ContentError, match="HTTP 514") as error:
        sources.fetch_audio(URL, tmp_path)
    assert provider["download_urls"] == [PRIMARY, *extra[:2]]
    assert "https://" not in str(error.value) and "secret" not in str(error.value)


def test_normal_extractor_success_does_not_request_fallback(provider, tmp_path):
    provider["web_error"] = None
    provider["normal_info"] = {"id": "BV1GZbT6UE7o", "title": "正常页面", "duration": 145,
                               "url": PRIMARY, "vcodec": "none", "ext": "m4a"}
    assert sources.fetch_audio(URL, tmp_path).stat().st_size > 0
    assert provider["api_calls"] == []


def test_downloader_disables_account_sources():
    # Constructing YoutubeDL and inspecting its options does not make a request.
    with sources._ydl() as ydl:
        assert ydl.params["cookiefile"] is None
        assert ydl.params["cookiesfrombrowser"] is None
        assert ydl.params["usenetrc"] is False


def test_real_ytdlp_format_processing_preserves_same_stream_backup(tmp_path, monkeypatch):
    attempted = []
    info = {"id": "example", "title": "format contract", "duration": 145,
            "extractor": "BiliBili", "extractor_key": "BiliBili", "formats": [
                {"format_id": "audio", "url": PRIMARY, "ext": "m4a", "vcodec": "none",
                 "acodec": "mp4a.40.2", "tbr": 128, "_vtm_backup_urls": [BACKUP]},
            ]}
    with sources._ydl({"format": "bestaudio", "outtmpl": str(tmp_path / "source.%(ext)s")}) as ydl:
        def transfer(selected):
            attempted.append(selected["url"])
            if selected["url"] == PRIMARY:
                raise DownloadError("HTTP Error 514: Frequency Capped")
            path = Path(ydl.prepare_filename(selected))
            path.write_bytes(b"mock transfer")
            selected["filepath"] = str(path)

        # Exercise actual format selection and normalized outtmpl, but no HTTP.
        monkeypatch.setattr(ydl, "process_info", transfer)
        result = sources._download_audio(ydl, info)
        assert result["url"] == BACKUP
        assert Path(result["requested_downloads"][0]["filepath"]).is_file()
        assert isinstance(ydl.params["outtmpl"], dict)
    assert attempted == [PRIMARY, BACKUP]
