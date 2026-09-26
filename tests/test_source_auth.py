"""Explicit login-state handling using only synthetic cookies and mock HTTP."""
import http.cookiejar
import json
from pathlib import Path
import sys
import time

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.cookies import CookieLoadError, YoutubeDLCookieJar
from yt_dlp.utils import DownloadError

from podcast_scribe import sources
from podcast_scribe.cli import main, parser
from podcast_scribe.model import ContentError
from podcast_scribe.source_auth import CookieAccessError, SourceAuth
from podcast_scribe.transcripts import normalize_segments


BV = "BV1GZbT6UE7o"
URL = f"https://www.bilibili.com/video/{BV}"


def cookie(domain=".bilibili.com", *, expires=None, name="test-session", value="synthetic-cookie-secret"):
    return http.cookiejar.Cookie(0, name, value, None, False, domain, True, domain.startswith("."),
                                 "/", True, False, expires, expires is None, None, None, {})


def jar(*cookies):
    result = YoutubeDLCookieJar()
    for item in cookies:
        result.set_cookie(item)
    return result


def info():
    return {"id": BV, "title": "模拟元信息", "duration": 10, "url": "https://media.example.test/audio",
            "vcodec": "none", "ext": "m4a"}


def test_default_inspect_does_not_load_cookies(monkeypatch):
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: pytest.fail("no explicit authorization"))
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *args, **kwargs: info())
    assert sources.inspect_source(URL)["id"] == BV
    with sources._ydl() as ydl:
        assert ydl.params["cookiefile"] is None and ydl.params["cookiesfrombrowser"] is None
        assert ydl.params["usenetrc"] is False


def test_browser_loading_is_lazy_once_and_domain_filtered(monkeypatch):
    calls = []
    loaded = jar(cookie(), cookie("api.bilibili.com", name="api"), cookie("example.test"),
                 cookie("bilibili.com.evil.test"), cookie("evilbilibili.com"),
                 cookie(name="expired", expires=int(time.time()) - 1))
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: calls.append(args[:2]) or loaded)
    auth = SourceAuth(browser="chrome:Profile 1")
    assert calls == []
    with sources._ydl() as first, sources._ydl() as second:
        auth.attach(first)
        auth.attach(second)
        assert first.cookiejar is second.cookiejar
        assert {item.domain for item in first.cookiejar} == {".bilibili.com", "api.bilibili.com"}
        assert first.cookiejar.filename is None
        assert first.params["cookiefile"] is None and first.params["cookiesfrombrowser"] is None
    assert calls == [(None, ("chrome", "Profile 1", None, None))]
    assert len(loaded) == 6  # no mutation of the original all-site jar


@pytest.mark.parametrize("loaded", [jar(), jar(cookie("example.test")), jar(cookie(expires=1))])
def test_empty_non_bilibili_or_expired_cookie_source_fails_before_request(monkeypatch, loaded):
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: loaded)
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *args, **kwargs: pytest.fail("must not anonymously continue"))
    with pytest.raises(CookieAccessError, match="没有可用的未过期"):
        sources.inspect_source(URL, auth=SourceAuth(browser="firefox"))


def test_loader_failure_does_not_echo_secrets_paths_or_retry_anonymously(monkeypatch, capsys):
    calls = []
    def failing(*args):
        calls.append(True)
        print("synthetic-cookie-secret /private/profile-secret", file=sys.stderr)
        print("synthetic-cookie-secret /private/profile-secret")
        raise CookieLoadError("synthetic-cookie-secret /private/profile-secret")
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", failing)
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *args, **kwargs: pytest.fail("must not make request"))
    auth = SourceAuth(browser="chrome:/private/profile-secret")
    for _ in range(2):
        with pytest.raises(CookieAccessError, match="读取或解密") as error:
            sources.inspect_source(URL, auth=auth)
        assert "secret" not in str(error.value)
    assert len(calls) == 1
    assert capsys.readouterr() == ("", "")


def test_partial_decryption_warning_does_not_silently_use_guest_cookie(monkeypatch, capsys):
    def partial(_file, _browser, ydl):
        ydl.report_warning("cannot decrypt cookie synthetic-cookie-secret")
        return jar(cookie(name="buvid3"))
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", partial)
    with pytest.raises(CookieAccessError, match="读取或解密"):
        sources.inspect_source(URL, auth=SourceAuth(browser="chrome"))
    assert capsys.readouterr() == ("", "")


def test_explicit_netscape_file_is_not_written_back(tmp_path):
    path = tmp_path / "synthetic-cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t2147483647\ttest-session\tsynthetic-cookie-secret\n"
                    ".example.test\tTRUE\t/\tFALSE\t2147483647\tother\tother-site-secret\n", encoding="utf-8")
    before, timestamp = path.read_bytes(), path.stat().st_mtime_ns
    auth = SourceAuth(cookies=path)
    with sources._ydl() as ydl:
        auth.attach(ydl)
        assert len(ydl.cookiejar) == 1 and ydl.cookiejar.filename is None
        ydl.cookiejar.set_cookie(cookie(name="server-response", value="new-memory-only-cookie"))
    assert path.read_bytes() == before and path.stat().st_mtime_ns == timestamp
    assert list(tmp_path.iterdir()) == [path]


def test_mixed_valid_and_bad_netscape_line_is_rejected_without_stderr_leak(tmp_path, capsys):
    path = tmp_path / "synthetic-cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t2147483647\ttest-session\tvalid-secret\n"
                    "invalid Netscape line including bad-line-secret\n", encoding="utf-8")
    before = path.read_bytes()
    with sources._ydl() as ydl, pytest.raises(CookieAccessError, match="无法完整读取") as error:
        SourceAuth(cookies=path).attach(ydl)
    assert "secret" not in str(error.value) and str(path) not in str(error.value)
    assert capsys.readouterr() == ("", "")
    assert path.read_bytes() == before


def test_missing_cookie_file_is_lazy_and_has_safe_error(tmp_path):
    path = tmp_path / "private-cookie-file.txt"
    auth = SourceAuth(cookies=path)
    with sources._ydl() as ydl, pytest.raises(CookieAccessError) as error:
        auth.attach(ydl)
    assert str(path) not in str(error.value)


def test_cached_audio_returns_without_cookie_access(tmp_path, monkeypatch):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"complete test fixture")
    monkeypatch.setattr(SourceAuth, "attach", lambda *args: pytest.fail("cached audio must not read cookies"))
    assert sources.fetch_audio(URL, tmp_path, auth=SourceAuth(browser="chrome")) == source


def test_same_authorized_memory_jar_reaches_api_fallback(monkeypatch):
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: jar(cookie()))
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("HTTP Error 412")))
    def fallback(ydl, url):
        assert [item.domain for item in ydl.cookiejar] == [".bilibili.com"]
        return info()
    monkeypatch.setattr(sources, "_public_api_info", fallback)
    assert sources.inspect_source(URL, auth=SourceAuth(browser="chrome"))["id"] == BV


@pytest.mark.parametrize("preview", [True, False])
def test_extractor_preview_warning_stops_but_missing_premium_quality_does_not(monkeypatch, capsys, preview):
    def extract(ydl, *_args, **_kwargs):
        message = ("This is a supporter-only video, only the preview will be extracted: signed-secret" if preview
                   else "Some high quality formats are missing; log in or become a premium member for those formats")
        ydl.report_warning(message)
        return info()
    monkeypatch.setattr(YoutubeDL, "extract_info", extract)
    monkeypatch.setattr(sources, "_public_api_info", lambda *args: pytest.fail("preview must not enter API fallback"))
    if preview:
        with pytest.raises(ContentError, match="试看") as error:
            sources.inspect_source(URL)
        assert "signed-secret" not in str(error.value)
    else:
        assert sources.inspect_source(URL)["id"] == BV
    assert capsys.readouterr() == ("", "")


def test_cli_auth_options_are_mutually_exclusive_and_browser_syntax_is_explicit():
    for command in ("inspect", "ingest"):
        with pytest.raises(SystemExit):
            parser().parse_args([command, URL, "--cookies", "file.txt", "--cookies-from-browser", "chrome"])
    for value in ("unknown", "chrome:", "chrome+KEYRING", "firefox::container"):
        with pytest.raises(CookieAccessError):
            SourceAuth(browser=value)


def test_empty_explicit_browser_option_never_turns_into_anonymous_request(monkeypatch, capsys):
    monkeypatch.setattr(sources, "inspect_source", lambda *args, **kwargs: pytest.fail("empty auth must be rejected"))
    assert main(["inspect", URL, "--cookies-from-browser", ""]) == 2
    assert "登录态来源" in capsys.readouterr().err


def test_cookie_expiry_between_inspect_and_download_does_not_continue_anonymously(monkeypatch):
    calls = []
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: calls.append(True) or jar(cookie(expires=int(time.time()) + 300)))
    auth = SourceAuth(browser="chrome")
    with sources._ydl() as first, sources._ydl() as second:
        auth.attach(first)
        for item in first.cookiejar:
            item.expires = 1
        with pytest.raises(CookieAccessError, match="已过期"):
            auth.attach(second)
    assert len(calls) == 1


def test_ingest_shares_one_lazy_auth_and_existing_draft_does_not_load_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from podcast_scribe import transcribe
    loads, auths = [], []
    monkeypatch.setattr("yt_dlp.cookies.load_cookies", lambda *args: loads.append(args[:2]) or jar(cookie()))
    def inspect(url, *, auth):
        auths.append(auth)
        with sources._ydl() as ydl:
            auth.attach(ydl)
        return {"id": BV, "title": "模拟单集", "source": {"url": url, "video_id": BV, "platform": "bilibili"}}
    def fetch(url, work, *, auth):
        auths.append(auth)
        with sources._ydl() as ydl:
            auth.attach(ydl)
        path = work / "source.mp3"
        path.write_bytes(b"mock audio")
        return path
    monkeypatch.setattr(sources, "inspect_source", inspect)
    monkeypatch.setattr(sources, "fetch_audio", fetch)
    monkeypatch.setattr(transcribe, "transcribe_audio", lambda *args, **kwargs: normalize_segments([
        {"start": 0, "end": 1, "text": "模拟正文", "speaker": "A"}]))
    assert main(["ingest", URL, "--cookies-from-browser", "chrome:Profile 1"]) == 0
    capsys.readouterr()
    assert auths[0] is auths[1] and len(loads) == 1
    content = Path("data", BV, "episode.json").read_text()
    assert "synthetic-cookie-secret" not in content and "Profile 1" not in content
    monkeypatch.setattr(SourceAuth, "attach", lambda *args: pytest.fail("existing draft must not read cookies"))
    assert main(["ingest", URL, "--cookies", "missing-private-path.txt"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "existing"
    assert len(loads) == 1
