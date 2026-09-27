"""Issue intake freezes only validated public data and cannot overwrite content."""
from copy import deepcopy
import base64
import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from podcast_scribe.model import new_episode
from podcast_scribe.share import canonical_bytes, make_submission, submission_digest
from podcast_scribe.transcripts import normalize_segments

spec = importlib.util.spec_from_file_location("community_submit", Path(__file__).parents[1] / "scripts/community_submit.py")
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)

ATTACHMENT = "https://github.com/user-attachments/files/12345/submission.json"


@pytest.fixture
def submission():
    segments, speakers = normalize_segments([{"start": 0, "end": 10, "speaker": "A", "text": "测试公开文稿。"}])
    episode = new_episode({"id": "community-test", "title": "测试文稿", "url": "https://example.com/episode"},
                          segments, speakers, series_id="test", series_title="测试节目")
    episode["source"]["url"] = "https://example.com/episode"
    episode["review"] = {"content_checked": True, "speakers_confirmed": True}
    episode["segments"][0]["review_status"] = "reviewed"
    episode["summary"] = ["测试摘要。"]
    episode["chapters"] = [{"id": "chapter-1", "title": "正文", "start": 0, "segment_id": segments[0]["id"]}]
    return make_submission(episode, attribution="测试投稿者")


def issue_event(submission):
    fields = dict(zip(intake.FIELDS, [submission["episode"]["source"]["url"], submission["attribution"],
                                     submission_digest(submission), f"[submission.json]({ATTACHMENT})",
                                     f"- [X] {intake.CONSENT}"]))
    return {"action": "opened", "repository": {"full_name": intake.REPOSITORY}, "issue": {
        "number": 12, "html_url": "https://github.com/aweng126/podcast-scribe/issues/12",
        "user": {"login": "contributor"}, "title": "[分享] 测试", "state": "open",
        "body": "\n\n".join(f"### {key}\n\n{value}" for key, value in fields.items()),
    }}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.merged = False
        self.pulls = []
        self.record = None
        self.other_path = False
        self.large_blob = False
        self.branch = False
        self.ref_created = False

    def call(self, method, path, data=None, **kwargs):
        self.calls.append((method, path, deepcopy(data)))
        if method == "GET":
            if path.endswith(("?ref=main", "?ref=mainsha")):
                return {"type": "file"} if self.merged else None
            if path.startswith("/pulls?"):
                return self.pulls
            if path == "/git/ref/heads/community/issue-12":
                return {"object": {"sha": "branchsha"}} if self.branch else None
            if path == "/git/ref/heads/main":
                return {"object": {"sha": "mainsha"}}
            if path == "/git/commits/mainsha":
                return {"tree": {"sha": "maintree"}}
            if path.startswith("/compare/"):
                return {"files": [{"filename": "README.md" if self.other_path else "content/episodes/issue-12.json", "status": "added"}]}
            if path.startswith("/contents/"):
                if self.large_blob:
                    return {"type": "file", "encoding": "none", "content": "", "sha": "a" * 40, "size": len(intake.record_bytes(self.record))}
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(intake.record_bytes(self.record)).decode()}
            if path == "/git/blobs/" + "a" * 40:
                return {"encoding": "base64", "content": base64.b64encode(intake.record_bytes(self.record)).decode()}
        if method == "POST":
            if path == "/git/blobs":
                self.record = json.loads(base64.b64decode(data["content"]))
                return {"sha": "blobsha"}
            if path == "/git/trees":
                return {"sha": "treesha"}
            if path == "/git/commits":
                return {"sha": "commitsha"}
            if path == "/git/refs":
                self.branch = self.ref_created = True
                return {}
            if path == "/pulls":
                self.pulls = [{**data, "state": "open", "html_url": "https://github.com/aweng126/podcast-scribe/pull/13"}]
                return self.pulls[0]
        raise AssertionError((method, path, data))


def test_freezes_one_content_file_and_reuses_pr_without_refetch(submission):
    api = FakeAPI()
    event = issue_event(submission)
    message = intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: deepcopy(submission))
    assert "/pull/13" in message
    assert api.record["provenance"] == {"issue_url": event["issue"]["html_url"], "submitter": "contributor", "payload_sha256": submission_digest(submission)}
    tree = next(data for method, path, data in api.calls if path == "/git/trees")
    assert tree == {"base_tree": "maintree", "tree": [{"path": "content/episodes/issue-12.json", "mode": "100644", "type": "blob", "sha": "blobsha"}]}
    assert all(method != "PATCH" for method, _, _ in api.calls)
    api.calls.clear()
    event["action"] = "edited"
    event["issue"]["body"] = "completely edited after submission"
    message = intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: pytest.fail("Must not re-fetch frozen submission"))
    assert "/pull/13" in message
    assert all(method == "GET" for method, _, _ in api.calls)


def test_pr_explains_unknown_series_and_keeps_original_digest(submission, monkeypatch):
    monkeypatch.setattr(intake, "load_catalog", lambda: {"schema_version": 1, "series": []})
    submission["episode"]["series"] = {"id": "inbox", "title": "待归类", "description": ""}
    record = intake.make_record(issue_event(submission), intake.REPOSITORY, lambda _: submission)
    before = deepcopy(record)
    body = intake.pull_body(record)
    assert "投稿系列：待归类（ID：inbox）" in body
    assert "系列尚未确定，可先保留未分类" in body
    assert "docs/community.md#系列归属维护" in body
    assert "不改动固定投稿或摘要" in body
    assert record["provenance"]["payload_sha256"] in body
    assert record == before


def test_pr_distinguishes_new_and_known_series_without_rendering_submitted_markdown(submission, monkeypatch):
    submission["episode"]["series"]["title"] = "@someone <img src=x> [点击](https://example.com)"
    record = intake.make_record(issue_event(submission), intake.REPOSITORY, lambda _: submission)
    monkeypatch.setattr(intake, "load_catalog", lambda: {"schema_version": 1, "series": []})
    body = intake.pull_body(record)
    assert "系列尚未进入维护目录" in body
    assert "@someone" not in body and "<img" not in body and "[点击]" not in body
    monkeypatch.setattr(intake, "load_catalog", lambda: {"schema_version": 1, "series": [{"id": "test"}]})
    body = intake.pull_body(record)
    assert "请确认本期确实属于该系列" in body
    assert "系列尚未进入维护目录" not in body


@pytest.mark.parametrize("merged", [True, False])
def test_closed_or_accepted_submission_requires_new_issue(submission, merged):
    api = FakeAPI()
    api.merged = merged
    api.pulls = [{"state": "closed", "body": intake.PR_MARKER}]
    assert "新建" in intake.freeze_submission(api, issue_event(submission), intake.REPOSITORY,
                                           lambda url: pytest.fail("Closed submission must not download"))
    assert all(method == "GET" for method, _, _ in api.calls)


def test_recovers_pr_failure_using_exact_frozen_snapshot(submission):
    event = issue_event(submission)
    api = FakeAPI()
    api.record = intake.make_record(event, intake.REPOSITORY, lambda url: submission)
    api.branch = True
    event["issue"]["body"] = "changed after branch creation"
    intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: pytest.fail("Must recover frozen content"))
    assert api.pulls
    assert [(method, path) for method, path, _ in api.calls if method != "GET"] == [("POST", "/pulls")]


def test_recovers_file_over_one_mebibyte_via_git_blob(submission):
    ep = submission["episode"]
    ep["segments"] = [{**ep["segments"][0], "id": f"s-{i}", "start": i * 10, "end": (i + 1) * 10, "text": "a" * 30000} for i in range(40)]
    ep["duration_seconds"] = 400
    ep["chapters"][0]["segment_id"] = "s-0"
    event = issue_event(submission)
    api = FakeAPI()
    api.record = intake.make_record(event, intake.REPOSITORY, lambda url: submission)
    assert 1024 * 1024 < len(intake.record_bytes(api.record)) < 2 * 1024 * 1024
    api.branch = api.large_blob = True
    intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: pytest.fail("Must not re-download"))
    assert any(path == "/git/blobs/" + "a" * 40 for _, path, _ in api.calls)


def test_recovery_refuses_branch_with_unrelated_changes(submission):
    api = FakeAPI()
    api.branch = api.other_path = True
    with pytest.raises(intake.IntakeError, match="拒绝覆盖"):
        intake.freeze_submission(api, issue_event(submission), intake.REPOSITORY)
    assert all(method == "GET" for method, _, _ in api.calls)


def test_never_overwrites_content_merged_while_downloading(submission):
    api = FakeAPI()
    def download(url):
        api.merged = True
        return submission
    message = intake.freeze_submission(api, issue_event(submission), intake.REPOSITORY, download)
    assert "已收录" in message
    assert all(method == "GET" for method, _, _ in api.calls)


@pytest.mark.parametrize("change", ["digest", "source", "attribution", "consent", "duplicate", "external", "extra"])
def test_invalid_intake_cannot_write_repository(submission, change):
    event = issue_event(submission)
    body = event["issue"]["body"]
    if change == "digest": body = body.replace(submission_digest(submission), "0" * 64)
    if change == "source": body = body.replace("https://example.com/episode", "https://example.com/other")
    if change == "attribution": body = body.replace("测试投稿者", "不同署名")
    if change == "consent": body = body.replace("- [X]", "- [ ]")
    if change == "duplicate": body += "\n\n### 来源链接\n\nhttps://example.com"
    if change == "external": body = body.replace(ATTACHMENT, "https://evil.example/submission.json")
    if change == "extra": body += "\n\n### artifacts\n\n/etc/passwd"
    event["issue"]["body"] = body
    api = FakeAPI()
    with pytest.raises(intake.IntakeError):
        intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: submission)
    assert all(method == "GET" for method, _, _ in api.calls)


@pytest.mark.parametrize("url", ["http://github.com/user-attachments/files/1/x.json", "https://github.com.evil/user-attachments/files/1/x.json",
                                 "https://github.com@evil.test/user-attachments/files/1/x.json", "https://github.com/user-attachments/files/1/x.json?url=http://localhost",
                                 "https://github.com/user-attachments/files/1/../../x.json", "https://github.com/user-attachments/files/1/x.zip"])
def test_rejects_non_attachment_urls(url):
    with pytest.raises(intake.IntakeError):
        intake.attachment_url(url)


def test_redirects_cannot_send_requests_to_arbitrary_hosts():
    handler = intake.AttachmentRedirects()
    request = Request(ATTACHMENT)
    with pytest.raises(intake.IntakeError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://127.0.0.1/private")
    assert intake.allowed_download("https://github-production-user-asset-6210df.s3.amazonaws.com/123/data?signature=abc")
    assert not intake.allowed_download("https://github-production-user-asset-6210df.s3.amazonaws.com.evil.test/x")
    assert not intake.allowed_download("http://objects.githubusercontent.com/github-production-repository-file-5c1aeb/x")


class Response:
    def __init__(self, data, headers=None): self.data, self.headers = data, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return self.data[:size]


def test_attachment_download_has_no_token_and_is_bounded(submission, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            assert not request.has_header("Authorization")
            assert timeout == 60
            return Response(canonical_bytes(submission))
    monkeypatch.setattr(intake, "build_opener", lambda *args: Opener())
    assert intake.download_submission(ATTACHMENT) == submission
    monkeypatch.setattr(intake, "MAX_SUBMISSION_BYTES", 4096)
    monkeypatch.setattr(Opener, "open", lambda *args, **kwargs: Response(b"x" * (intake.MAX_SUBMISSION_BYTES + 1)))
    with pytest.raises(intake.IntakeError, match="超过"):
        intake.download_submission(ATTACHMENT)


def test_api_pr_permission_error_is_actionable_and_not_token_bearing():
    api = intake.GitHub("private-test-token", intake.REPOSITORY)
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError("https://api.github.com", 403, "forbidden private-test-token", {}, None)
    api.opener = Opener()
    with pytest.raises(intake.IntakeError, match="Allow GitHub Actions") as error:
        api.call("POST", "/pulls", {})
    assert "private-test-token" not in str(error.value)


@pytest.mark.parametrize("mutation", ["repository", "url", "number", "login", "pr"])
def test_rejects_spoofed_event_identity(submission, mutation):
    event = issue_event(submission)
    if mutation == "repository": event["repository"]["full_name"] = "other/repo"
    if mutation == "url": event["issue"]["html_url"] = "https://evil.test/12"
    if mutation == "number": event["issue"]["number"] = True
    if mutation == "login": event["issue"]["user"]["login"] = "../content"
    if mutation == "pr": event["issue"]["pull_request"] = {"url": "example"}
    with pytest.raises(intake.IntakeError):
        intake.event_identity(event, intake.REPOSITORY)


def test_release_download_urls_and_redirects_are_strict(submission, monkeypatch):
    release = "https://github.com/contributor/manuscripts/releases/download/share-v1/episode.json"
    assert intake.attachment_url(f"[episode.json]({release})") == release
    assert intake.allowed_download(release)
    assert intake.allowed_download("https://release-assets.githubusercontent.com/github-production-release-asset/123/file?sig=abc")
    for url in [release.replace("/share-v1/", "/../"), release + "?token=secret",
                release.replace("github.com", "github.com.evil"), release.replace(".json", ".zip"),
                "https://release-assets.githubusercontent.com/untrusted/path"]:
        assert not intake.allowed_download(url)
    event = issue_event(submission)
    event["issue"]["body"] = event["issue"]["body"].replace(ATTACHMENT, release)
    assert intake.make_record(event, intake.REPOSITORY, lambda url: submission)["submission"] == submission


def test_chunked_submission_freezes_all_parts_and_recovers_without_download(submission, monkeypatch):
    import podcast_scribe.public_storage as storage
    monkeypatch.setattr(storage, "PART_BYTES", 1024)
    # Intake's read guard also reflects the deliberately small simulated limit.
    monkeypatch.setattr(intake, "PART_BYTES", 4096)
    submission["episode"]["segments"][0]["text"] = "公开内容" * 300
    original = intake.make_record(issue_event(submission), intake.REPOSITORY, lambda url: submission)

    class LargeAPI(FakeAPI):
        def __init__(self):
            super().__init__()
            self.blobs, self.paths = {}, {}
        def call(self, method, path, data=None, **kwargs):
            if method == "POST" and path == "/git/blobs":
                self.calls.append((method, path, deepcopy(data)))
                sha = f"{len(self.blobs) + 1:040x}"
                self.blobs[sha] = base64.b64decode(data["content"])
                return {"sha": sha}
            if method == "POST" and path == "/git/trees":
                self.paths = {entry["path"]: self.blobs[entry["sha"]] for entry in data["tree"]}
                return {"sha": "treesha"}
            if method == "GET" and path.startswith("/compare/"):
                return {"files": [{"filename": name, "status": "added"} for name in self.paths]}
            if method == "GET" and path.startswith("/contents/") and "?ref=community" in path:
                payload = self.paths[path.removeprefix("/contents/").split("?")[0]]
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(payload).decode(), "size": len(payload)}
            return super().call(method, path, data, **kwargs)
    api = LargeAPI()
    event = issue_event(submission)
    intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: submission)
    assert len(api.paths) > 2 and set(api.paths) == {"content/episodes/" + name for name, _ in storage.stored_files(original, 12)}
    assert all(len(payload) <= 4096 for payload in api.paths.values())
    api.pulls = []  # Simulate branch creation succeeding before the PR request failed.
    api.calls.clear()
    intake.freeze_submission(api, event, intake.REPOSITORY, lambda url: pytest.fail("Frozen content must not be downloaded again"))
    assert api.pulls and all(method != "PATCH" for method, _, _ in api.calls)
    api.pulls = []
    part = next(name for name in api.paths if name.endswith(".bin"))
    api.paths[part] = b"x" + api.paths[part][1:]
    with pytest.raises(ValueError, match="digest"):
        intake.freeze_submission(api, event, intake.REPOSITORY)


@pytest.mark.parametrize("code,path", [(429, "/git/blobs"), (403, "/pulls")])
def test_github_secondary_limit_retry_is_bounded_and_respects_header(monkeypatch, code, path):
    api = intake.GitHub("test-token", intake.REPOSITORY)
    attempts, sleeps = [], []
    class Opener:
        def open(self, request, timeout):
            attempts.append(request)
            if len(attempts) < 3:
                raise HTTPError(request.full_url, code, "secondary limit", {"Retry-After": "2"}, None)
            return Response(b'{"sha":"ok"}')
    api.opener = Opener()
    monkeypatch.setattr(intake.time, "sleep", sleeps.append)
    assert api.call("POST", path, {"content": "public"}) == {"sha": "ok"}
    assert len(attempts) == 3 and sleeps.count(2) == 2
